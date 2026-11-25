"""Tests for tool management's new media tools, on small media made with ffmpeg."""
import asyncio
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastmcp import Client
from fastmcp.exceptions import ToolError

from mcp_magichour import media_tools
from mcp_magichour.openapi_server import create_mcp
from mcp_magichour.tool_media import attach_card, card_clip, mix_onto_video, overlay_picture, recolour, side_by_side

ROOT = Path(__file__).resolve().parents[1]


def ff(*args):
    media_tools._ffmpeg(list(args))


@unittest.skipUnless(media_tools.have_ffmpeg(), "no ffmpeg")
class SoundOnVideoTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        # a 4 s clip whose own sound is a "talk" tone from 1 s to 3 s, silent around it
        self.film = self.dir / "clip.mp4"
        ff("-f", "lavfi", "-i", "testsrc=size=320x240:rate=24:duration=4", "-f", "lavfi",
           "-i", "sine=frequency=300:duration=4", "-af", "volume='between(t,1,3)':eval=frame",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(self.film))
        self.music = self.dir / "music.wav"
        ff("-f", "lavfi", "-i", "sine=frequency=600:duration=15", str(self.music))
        self.voice = self.dir / "voice.wav"
        ff("-f", "lavfi", "-i", "sine=frequency=900:duration=2", str(self.voice))
        self.long_voice = self.dir / "long-voice.wav"
        ff("-f", "lavfi", "-i", "sine=frequency=900:duration=6", str(self.long_voice))

    def test_music_under_the_videos_own_sound_keeps_the_picture_and_length(self):
        """Music under the user's clip."""
        out = self.dir / "out.mp4"
        done = mix_onto_video(self.film, out, music=self.music, original="keep")
        info = media_tools.probe(out)
        self.assertTrue(info["audio"])
        self.assertEqual((info["width"], info["height"]), (320, 240))
        self.assertAlmostEqual(info["seconds"], 4, delta=0.3)
        self.assertGreaterEqual(done["kept_spoken_spans"], 1)

    def test_replace_removes_the_videos_own_sound(self):
        """A narration instead of the conversation."""
        out = self.dir / "out.mp4"
        done = mix_onto_video(self.film, out, voice=self.voice, original="replace")
        self.assertEqual(done["original"], "replace")
        self.assertEqual(done["kept_spoken_spans"], 0)
        self.assertTrue(media_tools.probe(out)["audio"])

    def test_a_voice_longer_than_the_video_is_not_cut(self):
        """The last words ran past the end and were cut."""
        out = self.dir / "out.mp4"
        done = mix_onto_video(self.film, out, voice=self.long_voice, original="replace")
        self.assertTrue(done["voice_tempo"] > 1.0 or done["held_end_seconds"] > 0)
        self.assertGreaterEqual(media_tools.probe_seconds(out) * done["voice_tempo"], 6 - 0.3)

    def test_nothing_to_add_is_an_error(self):
        with self.assertRaises(ValueError):
            mix_onto_video(self.film, self.dir / "out.mp4")

    def test_no_working_files_are_left_behind(self):
        """Each call with original replace left silent.mp4 and held.mp4 in the temp folder."""
        scratch = self.dir / "temp"
        scratch.mkdir()
        with patch.object(tempfile, "tempdir", str(scratch)):
            mix_onto_video(self.film, self.dir / "out.mp4", voice=self.long_voice, original="replace")
        self.assertEqual(list(scratch.iterdir()), [])


def mean_rgb(path):
    from PIL import Image, ImageStat
    return ImageStat.Stat(Image.open(path).convert("RGB")).mean


@unittest.skipUnless(media_tools.have_ffmpeg(), "no ffmpeg")
class NewPictureToolTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.dir = Path(tempfile.mkdtemp())
        self.grey = self.dir / "grey.png"; Image.new("RGB", (400, 300), (128, 128, 128)).save(self.grey)
        self.red = self.dir / "red.png"; Image.new("RGB", (300, 300), (200, 40, 40)).save(self.red)
        self.wide = self.dir / "wide.png"; Image.new("RGB", (800, 200), (40, 40, 200)).save(self.wide)
        self.logo = self.dir / "logo.png"; Image.new("RGBA", (100, 100), (255, 255, 0, 255)).save(self.logo)
        self.film = self.dir / "clip.mp4"
        ff("-f", "lavfi", "-i", "color=c=gray:size=320x240:rate=24:duration=2", "-f", "lavfi", "-i", "sine=duration=2",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(self.film))

    def test_warmer_raises_red_and_lowers_blue_same_size(self):
        """Warmer, nothing redrawn."""
        out = recolour(self.grey, self.dir / "warm.png", warmth=0.5)
        r, g, b = mean_rgb(out)
        self.assertGreater(r, b + 40)   # clearly warmer (warmth 0.15 per unit was too weak)
        from PIL import Image
        self.assertEqual(Image.open(out).size, (400, 300))

    def test_saturation_zero_is_black_and_white(self):
        r, g, b = mean_rgb(recolour(self.red, self.dir / "bw.png", saturation=0))
        self.assertLess(max(r, g, b) - min(r, g, b), 3)

    def test_a_video_keeps_its_sound_and_length(self):
        out = recolour(self.film, self.dir / "warm.mp4", warmth=0.4)
        info = media_tools.probe(out)
        self.assertTrue(info["audio"])
        self.assertAlmostEqual(info["seconds"], 2, delta=0.3)

    def test_side_by_side_keeps_each_picture_whole(self):
        """Before and after, side by side, photos unchanged, no empty white space (in testing there were white bands)."""
        from PIL import Image
        out = side_by_side([self.red, self.wide], self.dir / "sbs.png", "row", "16:9", labels=["Before", "After"])
        sheet = Image.open(out).convert("RGB")
        self.assertEqual(sheet.size, (1280, 720))
        self.assertEqual(sheet.getpixel((320, 420)), (200, 40, 40))   # the red picture, whole, in the left cell
        self.assertEqual(sheet.getpixel((960, 400)), (40, 40, 200))   # the wide blue picture in the right cell
        above = sheet.getpixel((960, 200))                             # filled with a blurred copy, not white
        self.assertNotEqual(above, (255, 255, 255))
        self.assertLess(sum(sheet.getpixel((640 + 320, 10))), 100)      # the label band at the top of the cell

    def test_side_by_side_without_labels_or_with_a_colour(self):
        """Not limited to before and after: plain side by side, any background."""
        from PIL import Image
        out = side_by_side([self.red, self.wide, self.grey], self.dir / "three.png", "row", "16:9", background="white")
        self.assertEqual(Image.open(out).convert("RGB").getpixel((5, 5)), (255, 255, 255))

    def test_side_by_side_needs_two_to_four(self):
        with self.assertRaises(ValueError):
            side_by_side([self.red], self.dir / "x.png")

    def test_logo_in_the_top_right_corner_of_a_video(self):
        out = overlay_picture(self.film, self.logo, self.dir / "logo.mp4", "top_right", size=0.2)
        info = media_tools.probe(out)
        self.assertEqual((info["width"], info["height"]), (320, 240))
        self.assertTrue(info["audio"])
        frame = media_tools.last_frame(out, self.dir / "f.png")
        from PIL import Image
        im = Image.open(frame).convert("RGB")
        r, g, b = im.getpixel((320 - 10 - 20, 10 + 20))
        self.assertTrue(r > 200 and g > 200 and b < 80, (r, g, b))      # yellow logo in the top right
        r, g, b = im.getpixel((20, 220))
        self.assertTrue(abs(r - g) < 15 and abs(g - b) < 15, (r, g, b))  # grey elsewhere

    def test_logo_on_a_picture(self):
        out = overlay_picture(self.grey, self.logo, self.dir / "grey-with-logo.png", "bottom_left", size=0.25)
        from PIL import Image
        self.assertEqual(Image.open(out).size, (400, 300))

    def test_card_attached_after_the_clip_in_one_file(self):
        """In testing, join_videos failed (media_tools.join calls ffmpeg by name); the card tool attaches it itself."""
        card = card_clip(self.dir / "card.mp4", 2, ["Ready for takeoff?"], "black", (320, 240))
        out = attach_card(self.film, card, self.dir / "with-card.mp4", "after")
        info = media_tools.probe(out)
        self.assertEqual((info["width"], info["height"]), (320, 240))
        self.assertAlmostEqual(info["seconds"], 4, delta=0.4)
        self.assertTrue(info["audio"])
        from PIL import Image
        self.assertLess(sum(Image.open(media_tools.last_frame(out, self.dir / "end.png")).convert("RGB").getpixel((5, 5))), 30)

    def test_a_generated_card_fills_the_frame_with_background_none(self):
        """The card is generated first and given as a picture; with background none it fills the frame."""
        from PIL import Image
        made = self.dir / "made-card.png"
        Image.new("RGB", (640, 480), (11, 29, 58)).save(made)   # a generated card, the clip's shape
        out = card_clip(self.dir / "card.mp4", 2, None, "none", (320, 240), made)
        corner = Image.open(media_tools.last_frame(out, self.dir / "end.png")).convert("RGB").getpixel((3, 3))
        self.assertTrue(all(abs(a - b) < 12 for a, b in zip(corner, (11, 29, 58))), corner)   # no black border

    def test_end_card_matches_the_clip_and_can_be_joined(self):
        """A 2 s black card with exact words, the clip's size, with a sound track."""
        out = card_clip(self.dir / "card.mp4", 2, ["Ready for takeoff?", "@flightschool"], "black", (320, 240))
        info = media_tools.probe(out)
        self.assertEqual((info["width"], info["height"]), (320, 240))
        self.assertAlmostEqual(info["seconds"], 2, delta=0.3)
        self.assertTrue(info["audio"])


class SwitchTests(unittest.TestCase):
    def names(self, value, conversation="on"):
        async def run():
            with patch.dict(os.environ, {"MAGIC_HOUR_TOOL_PLAN": value, "MAGIC_HOUR_CONVERSATION": conversation}):
                server = create_mcp()
            async with Client(server) as client:
                return {t.name for t in await client.list_tools()}
        return asyncio.run(run())

    def test_on_with_the_conversation_front_or_the_tool_plan_switch(self):
        # The conversation front is on by default, so the 5 tools are too
        new = {"add_sound_to_video", "adjust_colours", "put_pictures_side_by_side", "put_picture_on_video", "make_card_clip"}
        self.assertFalse(new & self.names("off", conversation="off"))
        self.assertTrue(new <= self.names("on", conversation="off"))
        self.assertTrue(new <= self.names("off"))


class IsolationTests(unittest.TestCase):
    def test_no_direct_import_from_video_composition(self):
        src = (ROOT / "mcp_magichour" / "tool_media.py").read_text()
        for mod in ("plan_request", "video_production", "video_production_tool", "gates"):
            self.assertNotIn(f"from .{mod} ", src)
            self.assertNotIn(f"import {mod}", src)


if __name__ == "__main__":
    unittest.main()


class HeadSwapPhotoTests(unittest.TestCase):
    """swap_heads_in_photo helpers."""

    def test_cut_box_grows_the_face_and_snaps_to_near_photo_edges(self):
        from mcp_magichour.tool_media import cut_box
        self.assertEqual(cut_box((142, 25, 204, 285), (640, 384)), (91, 0, 397, 384))
        self.assertEqual(cut_box((310, 80, 180, 228), (640, 384)), (265, 0, 535, 384))
        self.assertEqual(cut_box((300, 300, 100, 100), (1000, 1000)), (275, 270, 425, 430))

    def test_margin_is_added_only_on_photo_edges_and_cut_off_again(self):
        from PIL import Image, ImageChops
        from mcp_magichour.tool_media import padded, unpadded
        photo = Image.effect_noise((640, 384), 60).convert("RGB")
        box = (91, 0, 397, 384)
        cut = photo.crop(box)
        pad, margins = padded(cut, box, photo.size)
        self.assertEqual(margins, (0, 16, 0, 16))
        self.assertEqual(pad.size, (306, 416))
        self.assertIsNone(ImageChops.difference(unpadded(pad, margins, cut.size), cut).getbbox())

    def test_a_face_crop_is_found_where_it_was_cut(self):
        from PIL import Image
        from mcp_magichour.tool_media import locate_face
        photo = Image.effect_noise((640, 384), 80).convert("RGB")
        self.assertEqual(locate_face(photo, photo.crop((310, 80, 490, 308))), (310, 80, 180, 228))

    def test_a_small_cut_out_is_pasted_back_without_an_error(self):
        """The faded edge was 12 px on each side: a cut-out under 25 px raised 'x1 must be greater than or equal to x0'."""
        from PIL import Image
        from mcp_magichour.tool_media import cut_box, put_head_back
        photo = Image.effect_noise((400, 300), 60).convert("RGB")
        face = (200, 150, 9, 11)
        box = cut_box(face, photo.size)
        new = Image.new("RGB", (box[2] - box[0], box[3] - box[1]), (0, 255, 0))
        pasted = put_head_back(photo, new, box, 0, [face])
        self.assertEqual(pasted.size, photo.size)

    def test_tool_is_listed_with_person_places(self):
        async def tools():
            async with Client(create_mcp()) as client:
                return {t.name: t for t in await client.list_tools()}
        with patch.dict(os.environ, {"MAGIC_HOUR_CONVERSATION": "on"}):
            listed = asyncio.run(tools())
        self.assertIn("swap_heads_in_photo", listed)
        self.assertIn("1 = the leftmost face", listed["swap_heads_in_photo"].description)


def group_photo(size, boxes):
    """A plain photo with a noise-textured 'face' in each box, so a face crop is found in one place only."""
    from PIL import Image
    photo = Image.new("RGB", size, (120, 140, 160))
    noise = Image.effect_noise(size, 70).convert("RGB")
    for x, y, w, h in boxes:
        photo.paste(noise.crop((x, y, x + w, y + h)), (x, y))
    return photo


def change_in(before, after, box):
    """The mean change of a box between two pictures, 0 to 765."""
    from PIL import ImageChops, ImageStat
    x, y, w, h = box
    return sum(ImageStat.Stat(ImageChops.difference(before.crop((x, y, x + w, y + h)), after.crop((x, y, x + w, y + h)))).mean)


class FakeMagicHour:
    """The Magic Hour calls of swap_heads_in_photo, with no network. Face detection returns crops cut at `boxes` from
    the uploaded photo; a head swap paints a green oval over the middle of its cut-out."""

    def __init__(self, boxes, failed_job=None, missing=()):
        self.boxes, self.failed_job, self.missing = boxes, failed_job, set(missing)
        self.dir = Path(tempfile.mkdtemp())
        self.uploads, self.jobs, self.charged = {}, {}, 0

    async def api(self, method, path, body=None):
        if method == "POST" and path == "/v1/face-detection":
            self.detected = self.uploads[body["assets"]["target_file_path"]]
            return {"id": "det1"}
        if path == "/v1/face-detection/det1":
            return {"status": "complete", "faces": [{"path": f"api-assets/acc/det1/{n}.png", "url": f"https://x/det1/{n}.png"}
                                                    for n in range(len(self.boxes))]}
        if method == "POST" and path == "/v1/head-swap":
            job = f"hs{len(self.jobs)}"
            self.jobs[job] = body
            self.charged += 10
            return {"id": job, "credits_charged": 10}
        if path.startswith("/v1/image-projects/"):
            project = path.rsplit("/", 1)[1]
            if project in self.missing:
                raise ToolError(f"Magic Hour refused {path} (HTTP 404): not found")
            if project == self.failed_job:
                return {"status": "error", "error": {"message": "no face"}}
            return {"status": "complete", "downloads": [{"url": f"https://x/{project}.png"}]}
        raise AssertionError(path)

    async def upload(self, file, kind="image"):
        ref = f"api-assets/acc/up{len(self.uploads)}.png"
        self.uploads[ref] = Path(shutil.copy(file, self.dir / f"up{len(self.uploads)}.png"))
        return ref

    async def download(self, url, out):
        from PIL import Image, ImageDraw
        name = url.rsplit("/", 1)[1][:-4]
        if "/det1/" in url:
            x, y, w, h = self.boxes[int(name)]
            Image.open(self.detected).convert("RGB").crop((x, y, x + w, y + h)).save(out)
        else:
            cut = Image.open(self.uploads[self.jobs[name]["assets"]["body_file_path"]]).convert("RGB")
            ImageDraw.Draw(cut).ellipse((cut.width * 0.25, cut.height * 0.2, cut.width * 0.75, cut.height * 0.8), fill=(0, 255, 0))
            cut.save(out)
        return out

    async def fetch_media(self, media, folder, kinds=("image", "video")):
        return Path(shutil.copy(media, folder / ("source" + Path(media).suffix))), True

    async def deliver(self, out, picture):
        return {"file": str(shutil.copy(out, self.dir / "result.png"))}

    def swap(self, picture, swaps):
        """Call swap_heads_in_photo; returns (is_error, the result's text)."""
        from mcp_magichour import tool_media

        async def run():
            with patch.dict(os.environ, {"MAGIC_HOUR_CONVERSATION": "on"}):
                server = create_mcp()
            with patch.object(tool_media, "_api", self.api), patch.object(tool_media, "_upload", self.upload), \
                    patch.object(tool_media, "_download", self.download), \
                    patch.object(tool_media, "_fetch_media", self.fetch_media), \
                    patch.object(tool_media, "_deliver", self.deliver), \
                    patch("mcp_magichour.openapi_server.current_authorization_header", return_value="Bearer synthetic-test-token"), \
                    patch("mcp_magichour.oauth_compat.current_authorization_header", return_value="Bearer synthetic-test-token"):
                async with Client(server) as client:
                    result = await client.call_tool("swap_heads_in_photo", {"picture": str(picture), "swaps": swaps},
                                                    raise_on_error=False)
            return result.is_error, result.content[0].text
        return asyncio.run(run())


class SwapHeadsToolTests(unittest.TestCase):
    """swap_heads_in_photo through the MCP client, with the Magic Hour API faked."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.boxes = [(60, 80, 90, 110), (260, 80, 90, 110), (460, 80, 90, 110)]
        self.photo = self.dir / "group.png"
        group_photo((640, 384), self.boxes).save(self.photo)

    def test_two_of_three_people_get_a_new_head(self):
        from PIL import Image
        api = FakeMagicHour(self.boxes)
        error, text = api.swap(self.photo, [{"person": 1, "head": "https://h/a.png"}, {"person": 2, "head": "https://h/b.png"}])
        self.assertFalse(error, text)
        done = json.loads(text)
        self.assertEqual(done["credits_charged"], 20)
        before, after = Image.open(self.photo).convert("RGB"), Image.open(done["file"]).convert("RGB")
        self.assertGreater(change_in(before, after, self.boxes[0]), 50)
        self.assertGreater(change_in(before, after, self.boxes[1]), 50)
        self.assertEqual(change_in(before, after, self.boxes[2]), 0)

    def test_every_head_is_checked_before_the_first_charge(self):
        """Person 2's head was a wrong project id: person 1 was charged, then the call failed with nothing to show."""
        api = FakeMagicHour(self.boxes, missing={"nosuchproject"})
        error, text = api.swap(self.photo, [{"person": 1, "head": "https://h/a.png"}, {"person": 2, "head": "nosuchproject"}])
        self.assertTrue(error, text)
        self.assertIn("Nothing was charged", text)
        self.assertEqual((api.charged, api.jobs), (0, {}))

    def test_a_failed_head_swap_keeps_the_heads_paid_for_before_it(self):
        """Person 2's head swap ended in error: person 1's paid head was lost with it."""
        from PIL import Image
        api = FakeMagicHour(self.boxes, failed_job="hs1")
        error, text = api.swap(self.photo, [{"person": 1, "head": "https://h/a.png"}, {"person": 2, "head": "https://h/b.png"},
                                            {"person": 3, "head": "https://h/c.png"}])
        self.assertFalse(error, text)
        done = json.loads(text)
        self.assertEqual(done["credits_charged"], 20)
        self.assertEqual([p["project_id"] for p in done["people"]], ["hs0"])
        self.assertEqual((done["failed"]["person"], done["failed"]["project_id"]), (2, "hs1"))
        self.assertIn("no face", done["failed"]["error"])
        self.assertEqual(done["not_tried"], ["person 3"])
        before, after = Image.open(self.photo).convert("RGB"), Image.open(done["file"]).convert("RGB")
        self.assertGreater(change_in(before, after, self.boxes[0]), 50)
        self.assertEqual(change_in(before, after, self.boxes[1]), 0)

    def test_a_person_given_twice_is_refused_before_any_charge(self):
        """The same person twice (or once by number, once by face path) was charged twice and one head was lost."""
        for second in ({"person": 1, "head": "https://h/b.png"}, {"face": "api-assets/acc/det1/0.png", "head": "https://h/b.png"}):
            api = FakeMagicHour(self.boxes)
            error, text = api.swap(self.photo, [{"person": 1, "head": "https://h/a.png"}, second])
            self.assertTrue(error, text)
            self.assertIn("Nothing was charged", text)
            self.assertEqual(api.charged, 0)

    def test_a_face_too_small_to_swap_is_refused_before_any_charge(self):
        """A 9x11 px face crashed after the person before it was charged."""
        boxes = [(60, 80, 90, 110), (300, 150, 9, 11)]
        group_photo((640, 384), boxes).save(self.dir / "small.png")
        api = FakeMagicHour(boxes)
        error, text = api.swap(self.dir / "small.png", [{"person": 1, "head": "https://h/a.png"}, {"person": 2, "head": "https://h/b.png"}])
        self.assertTrue(error)
        self.assertIn("Nothing was charged", text)
        self.assertEqual(api.charged, 0)
