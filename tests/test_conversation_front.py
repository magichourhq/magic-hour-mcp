"""plan_conversation: one batch, implied defaults, required files, the film sheet."""
import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastmcp.exceptions import ToolError

from mcp_magichour import conversation_front, media_helpers_tool, media_tools, video_production_tool
from mcp_magichour.conversation_front import front


class ConversationFrontTests(unittest.TestCase):
    """plan_conversation: rows, defaults, files, the film sheet."""

    def test_missing_file_is_asked_in_the_one_batch(self):
        out = front("faces", "put my face on this poster", {"consent": "none", "photo_or_video": "photo", "which_person": "ask"}, attachments=[{"name": "poster.jpg", "kind": "image"}])
        assert out["stage"] == "ask_the_user"
        ids = [q["id"] for q in out["questions"]]
        assert "which_person" in ids and "file" in ids

    def test_file_still_missing_after_the_batch_makes_nothing(self):
        out = front("change_picture", "make this warmer", {}, questions_asked=True, attachments=[])
        assert out["stage"] == "missing_file" and not out["ready"]

    def test_new_video_sound_default_is_on(self):
        out = front("new_video", "a 10 second clip: a ufo flies through a starfield", {"from_picture": "words", "length": "10", "shape": "16:9"})
        sound = next(d for d in out["decisions"] if d["id"] == "sound")
        assert sound["value"] == "Natural sound on." and "tell_the_user" not in sound   # no telling of defaults

    def test_film_asks_open_rows_once_then_plans(self):
        ask = front("film", "an ad for my bottle", {"kind": "ad", "exact_words": "ask", "product_picture": "ask"})
        assert ask["stage"] == "ask_the_user" and {q["id"] for q in ask["questions"]} == {"exact_words", "product_picture"}
        done = front("film", "an ad for my bottle", {"kind": "ad", "exact_words": "ask", "product_picture": "ask"}, questions_asked=True)
        assert done["ready"] and "story_rules" not in done   # the rules moved to the video tool
        words = {d["id"]: d["value"] for d in done["decisions"]}["exact_words"]
        assert "call-to-action" in words   # a skipped words question on an ad still gives a call to action
        assert not any("tell_the_user" in d for d in done["decisions"]) and "Tell the user" not in done["next"]

    def test_film_never_asks_about_sound(self):
        with self.assertRaises(ToolError):
            front("film", "a street interview", {"sound": "ask"})

    def test_no_talking_is_not_silence(self):
        # no rewrites; "nobody" stays nobody, and its text says the sound comes from music and sound effects
        out = front("film", "an ad with mellow music and no talking", {"kind": "ad", "sound": "on", "who_speaks": "nobody"})
        vals = {d["id"]: d for d in out["decisions"]}
        assert vals["sound"]["value"] == "on" and vals["who_speaks"]["value"] == "nobody" and "not sound in every clip" in out["next"]
        assert "music" in vals["who_speaks"]["instruction"] and "Silent" not in vals["who_speaks"]["instruction"]
        with self.assertRaises(ToolError):   # a value that is not an option is the host's to fix, not rewritten
            front("film", "an ad with mellow music and no talking", {"kind": "ad", "sound": "music only", "who_speaks": "nobody"})
        assert "audio` is true" not in out["next"]   # sound in the video, not the story's `audio`

    def test_silence_asked_stays_silent(self):
        out = front("film", "a silent film of a forest", {"kind": "story_mood", "sound": "silent", "who_speaks": "nobody"})
        assert {d["id"]: d["value"] for d in out["decisions"]}["sound"] == "silent"

    def test_video_with_no_length_checks_short_or_film_once(self):
        out = front("new_video", "a ufo over a city", {"from_picture": "words", "length": "your_call", "shape": "16:9"})
        assert out["stage"] == "ask_the_user" and [q["id"] for q in out["questions"]] == ["short_or_film"]
        again = front("new_video", "a ufo over a city", {"from_picture": "words", "length": "your_call", "shape": "16:9"}, questions_asked=True)
        assert again["stage"] == "plan"
        stated = front("new_video", "a 5s clip of a ufo over a city", {"from_picture": "words", "length": "5", "shape": "16:9"})
        assert stated["stage"] == "plan"
        song = front("new_video", "a video for this track, as long as the song", {"from_picture": "words", "length": "your_call", "shape": "16:9"})
        assert song["stage"] != "ask_the_user" or "short_or_film" not in [q["id"] for q in song["questions"]]
        answered = front("new_video", "a ufo over a city", {"from_picture": "words", "length": "your_call", "shape": "16:9", "short_or_film": "one short clip"}, questions_asked=True)
        assert answered["stage"] == "plan"
        for clear in ("make my dog pic move", "a logo animation with this"):
            out = front("new_video", clear, {"from_picture": "picture", "length": "your_call", "shape": "1:1"})
            assert out["stage"] != "ask_the_user" or "short_or_film" not in [q["id"] for q in out["questions"]]
        longer = front("new_video", "a ufo over a city", {"from_picture": "words", "short_or_film": "a longer film please"}, questions_asked=True)
        assert longer["stage"] == "switch_family" and "family film" in longer["next"]
        for short in ("short, not an ad", "one short clip, no story", "not a film, just short"):
            kept = front("new_video", "a ufo over a city", {"from_picture": "words", "short_or_film": short}, questions_asked=True)
            assert kept["stage"] != "switch_family", short
        ad = front("new_video", "a ufo over a city", {"from_picture": "words", "short_or_film": "an ad for my shop"}, questions_asked=True)
        assert ad["stage"] == "switch_family"

    def test_a_skipped_product_picture_is_a_real_photo_and_words_is_the_users_choice(self):
        # no user wants a worse version; the look-alike is only the user's choice
        skipped = front("film", "an ad for my bottle", {"kind": "ad", "product_picture": "ask"}, questions_asked=True)
        picture = {d["id"]: d["value"] for d in skipped["decisions"]}["product_picture"]
        assert picture.startswith("web")
        chose = front("film", "an ad for my bottle", {"kind": "ad", "product_picture": "words"})
        words = next(d for d in chose["decisions"] if d["id"] == "product_picture")
        assert "answer none" in words["instruction"]

    def test_the_conversation_holds_no_video_building_rules(self):
        # the conversation only asks and records answers; the video tool's rules of thumb build
        from mcp_magichour.video_production_tool import RULES_OF_THUMB
        text = str(conversation_front.FILM_ROWS)
        for rule in ("Hook in the first 3 s", "one spoken line per beat", "Use it for the hook", "no stretching", "One music track", "White letters"):
            assert rule not in text
        for rule in ("call-to-action", "one point per beat", "One spoken line per beat", "no stretching"):
            assert rule in RULES_OF_THUMB

    def test_media_facts_read_length_and_shape(self):
        clip = Path(tempfile.mkdtemp()) / "clip.mp4"
        subprocess.check_call([media_tools.ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=360x640:d=6:r=24", str(clip)])

        async def fake_fetch(media, folder, kinds=("image", "video")):
            return Path(media), False

        async def account_id():
            return "acct"
        self.enterContext(patch.object(media_helpers_tool, "_fetch_media", fake_fetch))
        self.enterContext(patch.object(video_production_tool, "_account_id", account_id))
        conversation_front._MEDIA_CACHE.clear()
        media = asyncio.run(conversation_front.media_facts([{"name": "clip.mp4", "kind": "video", "file_path": str(clip)},
                                                            {"name": "note.txt", "kind": "text", "file_path": "x"},
                                                            {"name": "nofile.jpg", "kind": "image"}]))
        assert media == [{"name": "clip.mp4", "kind": "video", "seconds": 6.0, "width": 360, "height": 640, "shape": "9:16", "has_sound": False}]
        out = front("change_video", "trim the whole clip to vertical", {}, attachments=[{"name": "clip.mp4", "kind": "video"}], media=media)
        assert out["media"] == media and "never guess a length" in out["next"]

    def test_media_facts_are_kept_per_account_errors_never_and_at_most_256(self):
        """By file_path only, another account got the facts of an upload it may not read, and an error stayed forever."""
        from PIL import Image
        photo = Path(tempfile.mkdtemp()) / "source.png"
        Image.new("RGB", (64, 64)).save(photo)
        account, refuse = ["acctA"], {"acctB"}

        async def fetch(media, folder, kinds=("image", "video")):
            if account[0] in refuse:   # an upload is read only for the account that made it
                raise RuntimeError("not yours")
            return photo, True

        async def account_id():
            return account[0]
        self.enterContext(patch.object(media_helpers_tool, "_fetch_media", fetch))
        self.enterContext(patch.object(video_production_tool, "_account_id", account_id))
        conversation_front._MEDIA_CACHE.clear()
        upload = [{"name": "me.png", "kind": "image", "file_path": "api-assets/acctA/me.png"}]
        assert asyncio.run(conversation_front.media_facts(upload))[0]["kind"] == "image"
        account[0] = "acctB"
        assert "error" in asyncio.run(conversation_front.media_facts(upload))[0]
        refuse.clear()   # the file can be read now: the error was not kept
        assert asyncio.run(conversation_front.media_facts(upload))[0]["kind"] == "image"
        urls = [{"name": f"{n}.png", "kind": "image", "file_path": f"https://example.com/{n}.png"} for n in range(300)]
        asyncio.run(conversation_front.media_facts(urls))
        assert len(conversation_front._MEDIA_CACHE) == 256

    def test_media_facts_never_fail_the_plan(self):
        async def broken(media, folder, kinds=("image", "video")):
            raise RuntimeError("down")
        self.enterContext(patch.object(media_helpers_tool, "_fetch_media", broken))
        conversation_front._MEDIA_CACHE.clear()
        media = asyncio.run(conversation_front.media_facts([{"name": "a.mp4", "kind": "video", "file_path": "api-assets/x/a.mp4"}]))
        assert media[0]["name"] == "a.mp4" and "error" in media[0]

    def test_tool_names_sent_as_rows_are_left_out(self):
        # In testing, hosts answered tool names as rows in 44 of 60 faces chats and the call was refused.
        out = front("faces", "put me into this beach photo", {"consent": "none", "scene": "scene picture",
                    "change_clothes_in_photo": "n/a", "place_person_in_scene": "n/a"},
                    attachments=[{"name": "me.jpg", "kind": "image"}, {"name": "beach.jpg", "kind": "image"}])
        assert out["stage"] == "plan" and "place_person_in_scene" in out["changed"][0]
        assert "place_person_in_scene" in {d["id"]: d["instruction"] for d in out["decisions"]}["scene"]

    def test_the_plan_does_not_tell_the_user_the_defaults(self):
        out = front("new_video", "a 10 second clip: a ufo flies through a starfield", {"from_picture": "words", "length": "10", "shape": "16:9"})
        assert out["stage"] == "plan" and "Tell the user" not in out["next"]

    def test_every_tool_that_had_no_sheet_now_has_a_row(self):
        from mcp_magichour import tool_plan
        families = tool_plan.sheets()["families"]
        for tool in ("change_clothes_in_photo", "place_person_in_scene", "replace_character_in_video", "translate_video",
                     "create_video_from_image", "create_video_from_audio", "create_lip_sync_video", "ai_voice_cloner_create_audio"):
            homes = [f for f, s in families.items() if tool in s["tools"] and any(tool in a for r in s["rows"] for a in r["answers"].values())]
            assert homes, tool
        assert conversation_front.EXTRA_TOOLS == {} and " also:" not in conversation_front.index()

    def test_a_video_for_a_song_asks_for_the_song(self):
        out = front("new_video", "make a music video for my song", {"from_picture": "words", "length": "your_call", "shape": "16:9", "song": "whole track"})
        assert out["stage"] == "ask_the_user" and any(q["id"] == "file" and "song" in q["ask"] for q in out["questions"])

    def test_silence_is_read_whatever_the_case(self):
        for request in ("Silent clip of rain on a window", "SILENT clip of rain on a window"):
            out = front("new_video", request, {"from_picture": "words", "length": "5 s", "shape": "16:9", "sound": "silent"})
            assert next(d for d in out["decisions"] if d["id"] == "sound")["value"] == "silent"
            assert "changed" not in out
        film = front("film", "A Silent film of a city at night", {"kind": "story_mood", "sound": "silent"})
        assert "audio` is false" in film["next"]

    def test_your_call_on_exact_words_means_write_words_that_fit(self):
        handed = front("new_picture", "a poster for my bakery", {"look": "warm", "shape": "1:1", "exact_words": "your_call",
                                                                 "set": "one", "transparent": "no", "meme_kind": "n/a"})
        words = next(d for d in handed["decisions"] if d["id"] == "exact_words")
        assert "written by you" in words["value"] and "Never invent a price" in words["value"]
        assert "exact words in the prompt" in words["instruction"]
        left_out = front("new_picture", "a product shot of a mug", {"look": "matte", "shape": "1:1", "set": "one",
                                                                    "transparent": "no", "meme_kind": "n/a"})
        assert next(d for d in left_out["decisions"] if d["id"] == "exact_words")["value"] == "No words."
        skipped = front("new_picture", "a poster", {"look": "warm", "shape": "1:1", "exact_words": "ask", "set": "one",
                                                    "transparent": "no", "meme_kind": "n/a"}, questions_asked=True)
        assert next(d for d in skipped["decisions"] if d["id"] == "exact_words")["value"] == "No words."   # skipped = the default
        film = front("film", "an ad for my bakery", {"kind": "ad", "exact_words": "your_call"})
        assert "written by you" not in str(film["decisions"]) and "Write short words that fit" in str(film["decisions"])

    def test_outfit_or_scene_in_words_needs_one_photo(self):
        one = [{"name": "me.jpg", "kind": "image"}]
        for request, row, value in (("change my outfit in this photo to a navy blazer", "outfit", "outfit in words"),
                                    ("put me on a beach", "scene", "scene in words")):
            out = front("faces", request, {"consent": "none", row: value}, attachments=one)
            assert out["stage"] == "plan" and not out["files"]["missing"], out.get("next")
        swap = front("faces", "put my face onto this poster", {"consent": "none", "photo_or_video": "photo"}, attachments=one)
        assert swap["stage"] == "ask_the_user" and swap["files"]["missing"]   # a swap still needs the target

    def test_film_n_a_on_a_row_that_always_applies_takes_its_default(self):
        out = front("film", "a reel about my cafe", {"kind": "ad", "shape": "n/a", "length": "n/a", "product_picture": "n/a"})
        ids = {d["id"]: d["value"] for d in out["decisions"]}
        assert ids["shape"].startswith("9:16") and ids["length"].startswith("15 s")
        assert "product_picture" not in ids   # a row with ask_when may not apply
        assert any("'n/a' became 'your_call'" in c for c in out["changed"])

    def test_media_tools_are_listed_with_the_conversation_front(self):
        from fastmcp import Client
        from mcp_magichour.openapi_server import create_mcp

        env = {k: v for k, v in os.environ.items() if k != "MAGIC_HOUR_TOOL_PLAN"}
        self.enterContext(patch.dict(os.environ, {**env, "MAGIC_HOUR_CONVERSATION": "on"}, clear=True))

        async def names():
            async with Client(create_mcp()) as client:
                return {t.name for t in await client.list_tools()}
        listed = asyncio.run(names())
        assert conversation_front.TOOL_MEDIA <= listed and "plan_tool_call" not in listed
        out = front("change_video", "add warm music to this clip, keep my voice", {"keep_or_remake": "keep", "sound_on_video": "keep"},
                    attachments=[{"name": "clip.mp4", "kind": "video"}])
        assert "add_sound_to_video" in out["tools"] and out["unavailable"] == []

    def test_which_person_is_never_guessed_and_never_asked_twice(self):
        one = [{"name": "me.jpg", "kind": "image"}, {"name": "group.jpg", "kind": "image"}]
        asked = front("faces", "put my face onto the group photo", {"consent": "none", "photo_or_video": "photo", "which_person": "your_call"}, attachments=one)
        assert asked["stage"] == "ask_the_user" and [q["id"] for q in asked["questions"]] == ["which_person"]
        stop = front("faces", "put my face onto the group photo", {"consent": "none", "photo_or_video": "photo", "which_person": "ask"},
                     questions_asked=True, attachments=one)
        assert stop["stage"] == "missing_answer" and not stop["ready"] and "Make nothing" in stop["next"]

    def test_a_row_the_host_answers_is_never_put_to_the_user(self):
        with self.assertRaises(ToolError):   # "(Read from the files.) Is the target a photo or a video?"
            front("faces", "swap my face", {"consent": "none", "photo_or_video": "ask"}, attachments=[{"name": "a.jpg", "kind": "image"}])
        with self.assertRaises(ToolError):   # film kind is "(Read from the words.)"
            front("film", "a film", {"kind": "ask"})
        out = front("new_video", "a 5 s clip of rain", {"from_picture": "words", "length": "5 s", "shape": "16:9", "sound": "ask"})
        assert out["stage"] == "plan" and next(d for d in out["decisions"] if d["id"] == "sound")["value"] == "Natural sound on."

    def test_the_rows_come_with_the_answer_rules(self):
        # the rules left the description (Claude Code keeps 2,048 bytes); the first call returns them with the rows
        from mcp_magichour.conversation_front import ANSWER_RULES, front
        for family in ("new_video", "film"):
            out = front(family, "a cat")
            assert out["stage"] == "fill_the_sheet" and out["next"].endswith(ANSWER_RULES)

    def test_a_narrator_and_people_on_screen_are_one_answer(self):
        """A narrator plus a presenter had no who_speaks option; in testing, both hosts were refused once."""
        out = front("film", "a narrator, and a beekeeper says a line to the camera", {"kind": "explainer_list", "who_speaks": "narrator + one_on_screen"})
        who = {d["id"]: d for d in out["decisions"]}["who_speaks"]
        assert who["value"] == "narrator + one_on_screen" and "voice-over" in who["instruction"] and "on camera" in who["instruction"]
        for bad in ("nobody + narrator", "narrator + crowd"):
            with self.assertRaises(ToolError):
                front("film", "a film", {"kind": "story_mood", "who_speaks": bad})
