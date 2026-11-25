"""New media tools for tool management: jobs no existing tool does.

Tool management is kept separate from video composition: this module
changes no shared file. It reuses, unchanged, the shared media code: media_tools (ffmpeg work) and media_helpers_tool
(fetch a user's file, deliver a made file). It imports nothing directly from plan_request, video_production,
video_production_tool or gates.
Registered with the planner's switch MAGIC_HOUR_TOOL_PLAN=on, or with the conversation front (on unless MAGIC_HOUR_CONVERSATION=off)
(its sheets route to these tools). With both off, the server is as before.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import media_tools
from .media_helpers_tool import _api, _deliver, _download, _fetch_media, _upload
from . import tool_plan

Original = Literal["keep", "under", "replace"]


# ---- sound on the user's video

def mix_onto_video(film: Path, out: Path, music: Path | None = None, voice: Path | None = None, voice_at: float = 0.0,
                   original: Original = "under", music_swell: bool = False) -> dict:
    """Lay music and/or a voice-over on a video, by code (no generation).

    original: keep = the video's own sound stays at full level where it is heard (speech, radio calls) and the music
    sits under it; under = the video's own sound is lowered under the new sound; replace = the video's own sound is
    removed. A voice-over longer than the video is read up to a little faster, then the last frame is held, so no word
    is cut (media_tools.fit_parts). Loudness is set to media_tools.TARGET_LUFS. Returns what was done."""
    if not (music or voice):
        raise ValueError("give music, a voice, or both")
    with tempfile.TemporaryDirectory() as folder:   # the working files go when the mix is done
        work = Path(folder)
        if original == "replace":
            silent = work / "silent.mp4"
            media_tools._ffmpeg(["-i", str(film), "-an", "-c:v", "copy", str(silent)])
            film = silent
        seconds = media_tools.probe_seconds(film)
        spoken = []
        if original == "keep" and media_tools.probe(film)["audio"]:
            spoken = media_tools.speech_spans(film)
        parts, fit = [], {"tempo": 1.0, "hold": 0.0, "moved": False}
        if voice:
            length = media_tools.probe_seconds(voice)
            fit = media_tools.fit_parts([{"start": 0.0, "end": length, "at": max(0.0, voice_at)}], seconds)
            parts = fit["parts"]
            if fit["hold"] > 0:
                film = media_tools.hold_end(film, fit["hold"], work / "held.mp4")
        mix = media_tools.add_sound(film, out, narration=voice, parts=parts or None, music=music, spoken=spoken,
                                    music_swell=music_swell)
        return {"seconds": round(media_tools.probe_seconds(out), 2), "lufs": mix["lufs"], "original": original,
                "kept_spoken_spans": len(spoken), "voice_tempo": round(fit["tempo"], 3), "held_end_seconds": round(fit["hold"], 2)}


# ---- colour change by code

def recolour(source: Path, out: Path, warmth: float = 0.0, saturation: float = 1.0, brightness: float = 0.0,
             contrast: float = 1.0) -> Path:
    """Change only the colours of a picture or video, nothing redrawn. warmth -1 (cooler) to 1 (warmer); saturation 0
    (black and white) to 2; brightness -0.5 to 0.5; contrast 0.5 to 2. A video keeps its sound."""
    w = max(-1.0, min(1.0, warmth))
    picture = source.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
    sat = max(0.0, min(2.0, saturation))
    steps = [f"colorchannelmixer=rr={1 + 0.4 * w:.3f}:gg={1 + 0.08 * w:.3f}:bb={1 - 0.4 * w:.3f}"]   # warmer: more red, less blue (0.15 was too weak)
    steps.append(f"eq=saturation={sat:.3f}:brightness={max(-0.5, min(0.5, brightness)):.3f}:contrast={max(0.5, min(2.0, contrast)):.3f}")
    if sat == 0:
        steps.append("format=gray")   # black and white exactly, no tint left by the colour conversion
    steps.append("format=rgb24" if picture else "format=yuv420p")
    args = ["-i", str(source), "-vf", ",".join(steps)]
    if not picture:
        args += ["-c:v", "libx264", "-c:a", "copy"]
    media_tools._ffmpeg([*args, str(out)])
    return out


# ---- pictures side by side

SHEET_SIZES = {"16:9": (1280, 720), "1:1": (1080, 1080), "9:16": (720, 1280), "4:5": (1080, 1350)}


def side_by_side(pictures: list[Path], out: Path, layout: str = "row", aspect: str = "16:9", gap: int = 0,
                 background: str = "blur", labels: list[str] | None = None) -> Path:
    """2 to 4 pictures in one, each whole: scaled to fit its cell, never cropped or stretched. layout: row, column or
    grid (2x2). background: blur (the default: each cell filled with a blurred, darkened copy of its own picture, so no
    empty space shows; in testing there were white bands) or a colour. labels: one per picture, exact, in a bold band
    across the top of its cell."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    if not 2 <= len(pictures) <= 4:
        raise ValueError("give 2 to 4 pictures")
    width, height = SHEET_SIZES[aspect]
    cols, rows = {"row": (len(pictures), 1), "column": (1, len(pictures)), "grid": (2, (len(pictures) + 1) // 2)}[layout]
    cell_w, cell_h = (width - gap * (cols - 1)) // cols, (height - gap * (rows - 1)) // rows
    sheet = Image.new("RGB", (width, height), "white" if background == "blur" else background)
    draw = ImageDraw.Draw(sheet)
    size = max(16, round(min(cell_w, cell_h) * 0.1))
    bold = ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    font = next((ImageFont.truetype(f, size) for f in (*bold, *media_tools.FONTS) if Path(f).exists()), None) or ImageFont.load_default(size)
    for i, path in enumerate(pictures):
        pic = Image.open(path).convert("RGB")
        x0, y0 = (i % cols) * (cell_w + gap), (i // cols) * (cell_h + gap)
        if background == "blur":
            cover = max(cell_w / pic.width, cell_h / pic.height)
            wide = pic.resize((round(pic.width * cover) + 1, round(pic.height * cover) + 1), Image.LANCZOS)
            left, top = (wide.width - cell_w) // 2, (wide.height - cell_h) // 2
            bg = wide.crop((left, top, left + cell_w, top + cell_h)).filter(ImageFilter.GaussianBlur(22))
            sheet.paste(Image.blend(bg, Image.new("RGB", bg.size, (0, 0, 0)), 0.25), (x0, y0))
        band = 0
        if labels and i < len(labels) and labels[i]:
            box = draw.textbbox((0, 0), labels[i], font=font)
            band = (box[3] - box[1]) + round(size * 0.9)
        scale = min(cell_w / pic.width, (cell_h - band) / pic.height)
        fitted = pic.resize((max(1, round(pic.width * scale)), max(1, round(pic.height * scale))), Image.LANCZOS)
        x, y = x0 + (cell_w - fitted.width) // 2, y0 + band + (cell_h - band - fitted.height) // 2
        sheet.paste(fitted, (x, y))
        if band:
            draw.rectangle((x0, y0, x0 + cell_w - 1, y0 + band - 1), fill=(20, 20, 20))
            draw.text((x0 + (cell_w - (box[2] - box[0])) // 2 - box[0], y0 + (band - (box[3] - box[1])) // 2 - box[1]),
                      labels[i], font=font, fill="white")
    sheet.save(out)
    return out


# ---- a picture on a video

PLACES = {"top_left": ("{m}", "{m}"), "top_right": ("W-w-{m}", "{m}"), "bottom_left": ("{m}", "H-h-{m}"),
          "bottom_right": ("W-w-{m}", "H-h-{m}"), "center": ("(W-w)/2", "(H-h)/2")}


def overlay_picture(base: Path, picture: Path, out: Path, place: str = "top_right", size: float = 0.15,
                    opacity: float = 1.0, margin: float = 0.03, start: float | None = None, end: float | None = None) -> Path:
    """A picture (a logo; a transparent PNG keeps its transparency) on a video or a picture. size: its width as a share
    of the base's width; margin: the gap to the edges, as a share of the base's width; start/end: seconds (video)."""
    if base.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
        from PIL import Image
        info = {"width": Image.open(base).width}
    else:
        info = media_tools.probe(base)
    m = max(0, round(info["width"] * margin))
    x, y = (p.format(m=m) for p in PLACES[place])
    logo_w = max(2, round(info["width"] * max(0.02, min(0.9, size))) // 2 * 2)
    when = f":enable='between(t,{start or 0},{end})'" if end is not None else (f":enable='gte(t,{start})'" if start else "")
    picture_base = base.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
    graph = (f"[1:v]format=rgba,scale={logo_w}:-1,colorchannelmixer=aa={max(0.05, min(1.0, opacity)):.2f}[l];"
             f"[0:v][l]overlay={x}:{y}{when}" + ("" if picture_base else ",format=yuv420p") + "[v]")
    args = ["-i", str(base), "-i", str(picture), "-filter_complex", graph, "-map", "[v]"]
    if not picture_base:
        args += ["-map", "0:a?", "-c:v", "libx264", "-c:a", "copy"]
    media_tools._ffmpeg([*args, str(out)])
    return out


# ---- a still card as a clip

def attach_card(video: Path, card: Path, out: Path, position: str = "after") -> Path:
    """The user's clip with the card before or after it, one file, the clip's size; the clip's sound kept, the card
    silent."""
    info = media_tools.probe(video)
    w, h = info["width"] - info["width"] % 2, info["height"] - info["height"] % 2
    sound = ["-f", "lavfi", "-t", f"{info['seconds']:.3f}", "-i", "anullsrc=r=48000:cl=stereo"] if not info["audio"] else []
    clip_a = "2:a" if sound else "0:a"
    graph = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p[v0];"
             f"[1:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p[c];"
             f"[{clip_a}]aresample=48000,aformat=channel_layouts=stereo[a0];[1:a]aresample=48000,aformat=channel_layouts=stereo[a1];"
             + (f"[c][a1][v0][a0]" if position == "before" else f"[v0][a0][c][a1]") + "concat=n=2:v=1:a=1[v][a]")
    media_tools._ffmpeg(["-i", str(video), "-i", str(card), *sound, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                         "-c:v", "libx264", "-c:a", "aac", str(out)])
    return out


def card_clip(out: Path, seconds: float, words: list[str] | None = None, background: str = "black",
              size: tuple[int, int] = (854, 480), picture: Path | None = None) -> Path:
    """A still card held for `seconds` (silent sound track, so join_videos can append it): words on a flat colour
    (media_tools.draw_card: exact, centred, the first line larger), or a picture fitted whole on that colour."""
    still = out.with_suffix(".png")
    if picture:
        media_tools.fit_picture(picture, None if background == "none" else background, size, still, words)   # none: fills the frame
    else:
        media_tools.draw_card(words or [""], background, size, still)
    return media_tools.still_video(still, max(0.5, seconds), out)


# ---- Head swap for several people in one photo
# The head swap API has no person choice: on a photo of two people it changes one, and a second call changes both.
# So each person is cut out, head swapped alone, and only the new head is put back.

HEAD_SWAP_WAIT_SECONDS = 180
EDGE = 12   # px over which a pasted head fades out at a cut-out's inner edge
MIN_FACE = 2 * EDGE + 1   # px: a smaller face has no room for that fade, so it is refused before any charge


def _diff_score(big, small, x: int, y: int) -> float:
    from PIL import ImageChops, ImageStat

    d = ImageChops.difference(big.crop((x, y, x + small.width, y + small.height)), small)
    return sum(ImageStat.Stat(d).mean)


def locate_face(photo, crop) -> tuple[int, int, int, int]:
    """Where a face crop from face detection sits in the photo: (x, y, width, height). The crops are cut from the
    photo at its own size; other sizes are tried when the match is poor."""
    from PIL import Image

    best = None
    for scale in (1.0, 0.9, 1.1, 0.8, 1.25, 0.67, 1.5, 0.5, 2.0):
        c = crop.resize((max(8, round(crop.width * scale)), max(8, round(crop.height * scale))))
        if c.width > photo.width or c.height > photo.height:
            continue
        k = max(1, round(max(photo.size) / 200))   # coarse search on a photo about 200 px wide
        small_photo = photo.resize((max(1, photo.width // k), max(1, photo.height // k)), Image.BILINEAR)
        small_crop = c.resize((max(1, c.width // k), max(1, c.height // k)), Image.BILINEAR)
        _, cx, cy = min((_diff_score(small_photo, small_crop, x, y), x, y)
                        for x in range(small_photo.width - small_crop.width + 1)
                        for y in range(small_photo.height - small_crop.height + 1))
        fine = min((_diff_score(photo, c, x, y), x, y)
                   for x in range(max(0, cx * k - k), min(photo.width - c.width, cx * k + k) + 1)
                   for y in range(max(0, cy * k - k), min(photo.height - c.height, cy * k + k) + 1))
        if best is None or fine[0] < best[0]:
            best = (fine[0], fine[1], fine[2], c.width, c.height)
        if best[0] < 3:   # a near-exact match: stop
            break
    return best[1:]


def cut_box(face: tuple[int, int, int, int], size: tuple[int, int]) -> tuple[int, int, int, int]:
    """The part of the photo sent to the head swap: the face crop with room for hair and shoulders."""
    x, y, w, h = face
    ex, ey = round(w * 0.25), round(h * 0.3)
    x0, y0, x1, y1 = max(0, x - ex), max(0, y - ey), min(size[0], x + w + ex), min(size[1], y + h + ey)
    # an edge near the photo's edge goes to it: a faded edge there kept old hair
    snap = 24
    return (0 if x0 < snap else x0, 0 if y0 < snap else y0,
            size[0] if size[0] - x1 < snap else x1, size[1] if size[1] - y1 < snap else y1)


PAD = 16


def padded(cut, box: tuple, size: tuple[int, int]):
    """The cut-out with a mirrored margin on each side that is the photo's edge: the head swap can draw stray
    colour along its border; the margin takes it and is cut off after. Returns (picture, margins)."""
    from PIL import Image, ImageOps

    x0, y0, x1, y1 = box
    left, top = (PAD if x0 == 0 else 0), (PAD if y0 == 0 else 0)
    right, bottom = (PAD if x1 == size[0] else 0), (PAD if y1 == size[1] else 0)
    out = Image.new("RGB", (cut.width + left + right, cut.height + top + bottom))
    out.paste(cut, (left, top))
    if left:
        out.paste(ImageOps.mirror(cut.crop((0, 0, left, cut.height))), (0, top))
    if right:
        out.paste(ImageOps.mirror(cut.crop((cut.width - right, 0, cut.width, cut.height))), (left + cut.width, top))
    if top:
        out.paste(ImageOps.flip(out.crop((0, top, out.width, 2 * top))), (0, 0))
    if bottom:
        b = top + cut.height
        out.paste(ImageOps.flip(out.crop((0, b - bottom, out.width, b))), (0, b))
    return out, (left, top, right, bottom)


def unpadded(new, margins: tuple, cut_size: tuple[int, int]):
    """The head swap's result without the margin, at the cut-out's size."""
    from PIL import Image

    left, top, right, bottom = margins
    w, h = cut_size[0] + left + right, cut_size[1] + top + bottom
    return new.convert("RGB").resize((w, h), Image.LANCZOS).crop((left, top, left + cut_size[0], top + cut_size[1]))


def _oval(face, size, grow: float = 1.1):
    """A filled oval over a face box, photo size."""
    from PIL import Image, ImageDraw

    x, y, w, h = face
    m = Image.new("L", size, 0)
    cx, cy, rx, ry = x + w / 2, y + h / 2, w / 2 * grow, h / 2 * grow
    ImageDraw.Draw(m).ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=255)
    return m


def _own(i: int, faces: list, box: tuple) -> "Image.Image":
    """255 where face i is the nearest face (distance in units of each face's size), soft at the border."""
    from PIL import Image, ImageFilter

    x0, y0, x1, y1 = box
    k = 4
    m = Image.new("L", (max(1, (x1 - x0) // k), max(1, (y1 - y0) // k)), 0)
    px = m.load()
    centres = [(fx + fw / 2, fy + fh / 2, fw, fh) for fx, fy, fw, fh in faces]
    for yy in range(m.height):
        for xx in range(m.width):
            X, Y = x0 + xx * k + k / 2, y0 + yy * k + k / 2
            d = [((X - cx) / w) ** 2 + ((Y - cy) / h) ** 2 for cx, cy, w, h in centres]
            if min(range(len(d)), key=d.__getitem__) == i:
                px[xx, yy] = 255
    return m.resize((x1 - x0, y1 - y0), Image.BILINEAR).filter(ImageFilter.GaussianBlur(5))


def put_head_back(base, new, box: tuple, i: int, faces: list, keep=None, rivals: list | None = None):
    """Paste only what the head swap changed (grown and softened), faded at the cut-out's edges unless the edge is
    the photo's edge, and never over `keep` (a photo-size mask: the heads pasted before, and the people not swapped).
    `base` is changed in place. Returns the pasted mask at photo size, to add to `keep`.
    In testing, a mask of 'pixels nearer this face' kept the old hair between two people in 2 of 5 runs."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    x0, y0, x1, y1 = box
    cut = base.crop(box)
    new = new.convert("RGB").resize(cut.size, Image.LANCZOS)
    changed = ImageChops.difference(new, cut).convert("L").point(lambda v: 255 if v > 28 else 0)
    changed = changed.filter(ImageFilter.MinFilter(5))
    # the face area is pasted whole: skin on skin changes little, and holes there let the old fringe show
    changed = ImageChops.lighter(changed, _oval(faces[i], base.size).crop(box))
    changed = changed.filter(ImageFilter.MaxFilter(15)).filter(ImageFilter.GaussianBlur(6))
    ex, ey = min(EDGE, (cut.width - 1) // 2), min(EDGE, (cut.height - 1) // 2)   # a small cut-out has a narrower fade
    edge = Image.new("L", cut.size, 0)
    ImageDraw.Draw(edge).rectangle((0 if x0 == 0 else ex, 0 if y0 == 0 else ey, cut.width - 1 - (0 if x1 == base.width else ex),
                                    cut.height - 1 - (0 if y1 == base.height else ey)), fill=255)
    mask = ImageChops.multiply(changed, edge.filter(ImageFilter.GaussianBlur(6)))
    if rivals:   # faces not pasted yet: each keeps the pixels nearer to it
        mask = ImageChops.multiply(mask, _own(0, [faces[i]] + rivals, box))
    if keep is not None:   # heads pasted before
        mask = ImageChops.multiply(mask, ImageChops.invert(keep.crop(box).filter(ImageFilter.GaussianBlur(4))))
    base.paste(new, (x0, y0), mask)
    full = Image.new("L", base.size, 0)
    full.paste(mask.point(lambda v: 255 if v > 127 else 0), (x0, y0))
    # later pastes keep off this face, not off the space beside it: that space can hold the next person's old hair
    return ImageChops.multiply(full, _oval(faces[i], base.size))


async def _image_url(project_id: str) -> str:
    """Wait for an image project; its download URL."""
    for _ in range(HEAD_SWAP_WAIT_SECONDS // 3):
        project = await _api("GET", f"/v1/image-projects/{project_id}")
        if project.get("status") == "complete" and project.get("downloads"):
            return project["downloads"][0]["url"]
        if project.get("status") in ("error", "canceled"):
            raise ToolError(f"The head swap {project_id} failed: {project.get('error')}")
        await asyncio.sleep(3)
    raise ToolError(f"The head swap {project_id} did not finish in {HEAD_SWAP_WAIT_SECONDS} s.")


async def _face_crop(face: str, out: Path) -> Path:
    """The face crop's file, from the face detection that made it (api-assets/<account>/<detection id>/<n>.png)."""
    parts = face.split("/")
    if len(parts) < 3:
        raise ToolError(f"`face` {face} is not a face path from get_face_detection_results.")
    detection = await _api("GET", f"/v1/face-detection/{parts[-2]}")
    match = next((f for f in detection.get("faces") or [] if f.get("path") == face), None)
    if not match or not match.get("url"):
        raise ToolError(f"`face` {face} is not in face detection {parts[-2]}. Use a path from get_face_detection_results.")
    return await _download(match["url"], out)


async def _detected_faces(target: str, folder: Path) -> list[Path]:
    """Run face detection on an uploaded picture and wait for it (it can stay queued for minutes)."""
    job = await _api("POST", "/v1/face-detection", {"assets": {"target_file_path": target}})
    for _ in range(HEAD_SWAP_WAIT_SECONDS // 3):
        result = await _api("GET", f"/v1/face-detection/{job['id']}")
        if result.get("status") == "complete":
            return [await _download(f["url"], folder / f"detected{n}.png") for n, f in enumerate(result.get("faces") or [])]
        if result.get("status") == "error":
            raise ToolError(f"Face detection {job['id']} failed: {result.get('error')}")
        await asyncio.sleep(3)
    raise ToolError(f"Face detection {job['id']} was still queued after {HEAD_SWAP_WAIT_SECONDS} s. Nothing was charged; try again later.")


def _who(swap: dict) -> str:
    """How an entry of `swaps` names its person, for a message."""
    return f"face {swap['face']}" if swap.get("face") else f"person {swap['person']}"


async def _head_ref(head: str) -> str:
    """A head picture as the head swap takes it: a file_path or URL as it is, an image project id as its URL."""
    if head.startswith(("http://", "https://", "api-assets/")):
        return head
    return await _image_url(head)


def _need() -> None:
    if not media_tools.have_ffmpeg():
        raise ToolError("This server has no ffmpeg, so it cannot do this.")


def enabled() -> bool:
    conversation = os.getenv("MAGIC_HOUR_CONVERSATION", "on").strip().lower() not in ("off", "0", "false")   # on by default
    return tool_plan.enabled() or conversation


def register_tool_media(mcp: FastMCP) -> None:
    if not enabled():
        return

    @mcp.tool(
        name="add_sound_to_video",
        title="Add music or a voice-over to a video",
        description=(
            "Lay music and/or a voice-over on the user's own video, by code: the picture is kept exactly, nothing is "
            "generated, 0 credits. Make the sound first (generate_music, or a voice), then call this. `video`: a "
            "file_path from upload_media, a URL, or a finished video project id. `music`, `voice`: a file_path, URL or "
            "finished audio project id. `original`: keep = the video's own speech or sound stays clear and the music "
            "sits under it (music under the user's talk or radio calls); under = the video's own sound is lowered; "
            "replace = the video's own sound is removed (a new soundtrack or a narration instead of the conversation). "
            "`voice_at`: seconds into the video where the voice starts. A voice longer than the video is read a little "
            "faster, then the last frame is held, so no word is cut. `music_swell`: the music rises between spoken "
            "lines. Returns download_url and video_file_path."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
    )
    async def add_sound_to_video(video: str, music: str | None = None, voice: str | None = None,
                                 original: Original = "under", voice_at: float = 0.0, music_swell: bool = False) -> dict:
        _need()
        if not (music or voice):
            raise ToolError("Give `music`, `voice`, or both.")
        with tempfile.TemporaryDirectory() as folder:
            f = Path(folder)
            (f / "v").mkdir(); (f / "m").mkdir(); (f / "n").mkdir()
            film, picture = await _fetch_media(video, f / "v", ("video",))
            if picture:
                raise ToolError("`video` is a picture; give a video.")
            music_file = (await _fetch_media(music, f / "m", ("audio", "video")))[0] if music else None
            voice_file = (await _fetch_media(voice, f / "n", ("audio", "video")))[0] if voice else None
            out = f / "with-sound.mp4"
            try:
                done = await asyncio.to_thread(mix_onto_video, film, out, music_file, voice_file, voice_at, original, music_swell)
            except ValueError as error:
                raise ToolError(str(error)) from None
            return {**await _deliver(out, False), "done": done}


    @mcp.tool(
        name="adjust_colours",
        title="Change only the colours",
        description=(
            "Change only the colours of the user's picture or video, by code: warmer or cooler, more or less colour, "
            "black and white, brighter, more contrast. Nothing else changes and nothing is redrawn (edit_image redraws). "
            "0 credits. `media`: a file_path, URL or finished project id. `warmth` -1 (cooler) to 1 (warmer), 0.3 is "
            "noticeably warmer; `saturation` 0 (black and white) to 2, 1 = as it is; `brightness` -0.5 to 0.5; "
            "`contrast` 0.5 to 2. A video keeps its sound. Returns download_url and file_path (video_file_path)."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
    )
    async def adjust_colours(media: str, warmth: float = 0.0, saturation: float = 1.0, brightness: float = 0.0,
                             contrast: float = 1.0) -> dict:
        _need()
        with tempfile.TemporaryDirectory() as folder:
            source, picture = await _fetch_media(media, Path(folder))
            out = Path(folder) / ("coloured.png" if picture else "coloured.mp4")
            await asyncio.to_thread(recolour, source, out, warmth, saturation, brightness, contrast)
            return await _deliver(out, picture)

    @mcp.tool(
        name="swap_heads_in_photo",
        title="Put a new head on each of several people in a photo",
        description=(
            "Put a new head (face, hair and head shape) on each of several people in one photo, each person with their "
            "own head picture. For one person, use swap_head_in_photo. Give one entry in `swaps` per person: `person` = "
            "the person's place from the left (1 = the leftmost face in the photo), `head` = the picture of the new head "
            "(file_path, URL or finished image project id). The tool finds the faces itself (no detect_faces needed); "
            "instead of `person` you may give `face`, a face path from get_face_detection_results. "
            "The tool cuts each person out, swaps the head on the cut-out, and puts back only the new head with a soft "
            "edge, one person after another, so the other people do not change. The head swap tool alone cannot do "
            "this: it has no person choice, and a second call changes everyone. `picture`: the photo (file_path, URL or "
            "finished image project id). 10 credits per person. Returns download_url, file_path and credits_charged. "
            "When a person's head swap fails, the photo comes back with the heads done before it, with `failed` (that "
            "person and the error) and `not_tried`."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
    )
    async def swap_heads_in_photo(picture: str, swaps: list[dict[str, str | int]], name: str | None = None) -> dict:
        from PIL import Image, ImageChops

        if not swaps or any(not ((s.get("face") or s.get("person")) and s.get("head")) for s in swaps):
            raise ToolError("Give `swaps` as a list of {person, head} (person 1 = leftmost face): one entry per person.")
        with tempfile.TemporaryDirectory() as folder:
            f = Path(folder)
            source, is_picture = await _fetch_media(picture, f, ("image",))
            if not is_picture:
                raise ToolError("`picture` is not a picture. For a video, see swap_face_in_video.")
            photo = await asyncio.to_thread(lambda: Image.open(source).convert("RGB"))   # a large photo took seconds
            heads = []   # every head is checked before the first charge: a wrong project id failed after others were paid for
            for s in swaps:
                try:
                    heads.append(await _head_ref(str(s["head"])))
                except ToolError as error:
                    raise ToolError(f"{_who(s)}: the head {s['head']} cannot be used ({error}). Nothing was charged.") from None
            found = []
            if any(not s.get("face") for s in swaps):
                photo_file = f / "photo.png"
                await asyncio.to_thread(photo.save, photo_file)
                crops = await _detected_faces(await _upload(photo_file), f)
                found = sorted([await asyncio.to_thread(locate_face, photo, Image.open(c).convert("RGB")) for c in crops])
            faces = []
            for i, s in enumerate(swaps):
                if s.get("face"):
                    crop = Image.open(await _face_crop(str(s["face"]), f / f"face{i}.png")).convert("RGB")
                    faces.append(await asyncio.to_thread(locate_face, photo, crop))
                    continue
                n = int(s["person"])
                if not 1 <= n <= len(found):
                    raise ToolError(f"person {n}: the photo has {len(found)} faces (1 = leftmost). Nothing was charged.")
                faces.append(found[n - 1])
            given = {}
            for s, face in zip(swaps, faces):
                if min(face[2:]) < MIN_FACE:
                    raise ToolError(f"{_who(s)}: the face is {face[2]}x{face[3]} px; a head swap here needs a face at "
                                    f"least {MIN_FACE} px wide and high. Nothing was charged.")
                if tuple(face) in given:   # the same person twice was charged twice, and one head was lost
                    raise ToolError(f"{given[tuple(face)]} and {_who(s)} are the same face in the photo. Give each person "
                                    "once. Nothing was charged.")
                given[tuple(face)] = _who(s)
            credits, done, failed = 0, [], None
            # people in the photo who get no new head keep their own area; each pasted head is kept from later pastes
            keep = Image.new("L", photo.size, 0)
            others = [x for x in found if tuple(x) not in {tuple(y) for y in faces}]   # people who get no new head
            for i, s in enumerate(swaps):
                box = cut_box(faces[i], photo.size)
                job = {}
                try:
                    cut_file = f / f"cut{i}.png"
                    cut, margins = await asyncio.to_thread(padded, photo.crop(box), box, photo.size)
                    await asyncio.to_thread(cut.save, cut_file)
                    body = await _upload(cut_file)
                    job = await _api("POST", "/v1/head-swap", {"name": f"{name or 'Head swap'} · person {i + 1}",
                                                               "assets": {"body_file_path": body, "head_file_path": heads[i]}})
                    credits += job.get("credits_charged") or 0
                    swapped = await _download(await _image_url(job["id"]), f / f"swapped{i}.png")
                    new = await asyncio.to_thread(lambda: unpadded(Image.open(swapped), margins, (box[2] - box[0], box[3] - box[1])))
                    pasted = await asyncio.to_thread(put_head_back, photo, new, box, i, faces, keep, faces[i + 1:] + others)
                except Exception as error:
                    if not done:
                        raise
                    # a failure here lost the heads paid for before it: the photo now comes back with them
                    failed = {"person": s.get("person"), "face": s.get("face"), "head": s["head"], "project_id": job.get("id"),
                              "error": str(error)}
                    break
                keep = ImageChops.lighter(keep, pasted)
                done.append({"person": s.get("person"), "face": s.get("face"), "head": s["head"], "project_id": job["id"], "cut_out": list(box)})
            out = f / "heads-swapped.png"
            await asyncio.to_thread(photo.save, out)
            result = {**await _deliver(out, True), "credits_charged": credits, "people": done}
            if failed:
                result |= {"failed": failed, "not_tried": [_who(s) for s in swaps[len(done) + 1:]]}
            return result

    @mcp.tool(
        name="put_pictures_side_by_side",
        title="Put pictures side by side",
        description=(
            "Put 2 to 4 of the user's pictures into one picture by code: in a row, a column or a 2x2 grid. Each picture "
            "stays whole and unchanged (scaled to fit, never cropped, stretched or redrawn); a before-and-after, a "
            "comparison, a collage. 0 credits. `pictures`: file_paths, URLs or finished image project ids, in order "
            "(left to right, top to bottom). `aspect`: 16:9, 1:1, 9:16 or 4:5. `labels`: one per picture, drawn exactly "
            "in a bold band across the top of its picture (for example ['Before', 'After'], or none for plain side by side). "
            "`gap` in pixels; `background`: blur (default: each cell filled with a blurred copy of its picture, no empty space) "
            "or a colour. Returns "
            "download_url and file_path."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
    )
    async def put_pictures_side_by_side(pictures: list[str], layout: Literal["row", "column", "grid"] = "row",
                                        aspect: Literal["16:9", "1:1", "9:16", "4:5"] = "16:9", labels: list[str] | None = None,
                                        gap: int = 0, background: str = "blur") -> dict:
        if not 2 <= len(pictures) <= 4:
            raise ToolError("Give 2 to 4 pictures.")
        with tempfile.TemporaryDirectory() as folder:
            files = []
            for i, p in enumerate(pictures):
                (Path(folder) / f"p{i}").mkdir()
                file, picture = await _fetch_media(p, Path(folder) / f"p{i}", ("image",))
                if not picture:
                    raise ToolError(f"picture {i + 1} is not a picture.")
                files.append(file)
            out = Path(folder) / "side-by-side.png"
            await asyncio.to_thread(side_by_side, files, out, layout, aspect, max(0, gap), background, labels)
            return await _deliver(out, True)

    @mcp.tool(
        name="put_picture_on_video",
        title="Put a logo or picture on a video",
        description=(
            "Put a picture (a logo; a transparent PNG keeps its transparency) on the user's video or picture, by code: "
            "the video is kept exactly, 0 credits. Remove a logo's background first with remove_image_background if "
            "needed. `media`: the video or picture underneath; `picture`: the logo; each a file_path, URL or finished "
            "project id. `place`: top_left, top_right, bottom_left, bottom_right, center. `size`: the logo's width as a "
            "share of the video's width (0.15 = small corner logo). `opacity` 0.05 to 1. `start`/`end`: seconds, video "
            "only; leave out for the whole clip. For words, use add_words_to_media. Returns download_url and file_path."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
    )
    async def put_picture_on_video(media: str, picture: str,
                                   place: Literal["top_left", "top_right", "bottom_left", "bottom_right", "center"] = "top_right",
                                   size: float = 0.15, opacity: float = 1.0, start: float | None = None,
                                   end: float | None = None) -> dict:
        _need()
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "b").mkdir(); (Path(folder) / "l").mkdir()
            base, base_is_picture = await _fetch_media(media, Path(folder) / "b")
            logo, logo_is_picture = await _fetch_media(picture, Path(folder) / "l", ("image",))
            if not logo_is_picture:
                raise ToolError("`picture` must be a picture.")
            out = Path(folder) / ("with-logo.png" if base_is_picture else "with-logo.mp4")
            await asyncio.to_thread(overlay_picture, base, logo, out, place, size, opacity, 0.03, start, end)
            return await _deliver(out, base_is_picture)

    @mcp.tool(
        name="make_card_clip",
        title="Make a still card clip",
        description=(
            "Make a still card as a short video clip, by code, 0 credits. A card is generated first: "
            "generate_image with qwen-image-2.1, the exact words quoted, the colour named, the user's clip's shape "
            "(edit_image with the user's logo when it goes on the card); check the spelling, make a misspelled card again "
            "(at most 3 in all) and keep the best one: a small misspelling beats words drawn by code. "
            "Then pass that picture here as `picture` with background none: it fills the frame. Words drawn on a flat "
            "colour (`words`, no picture) only when the user asks for a plain drawn card. "
            "It has a silent sound track, so join_videos "
            "can add it before or after the user's clip. `seconds`: how long. `words`: one line each, the first larger, "
            "spelled exactly. `background`: a colour such as black or #1B2A4A, or none (the picture fills the frame). `like_video`: the user's clip, to match "
            "its size (else `aspect`). `picture`: optional, a file_path, URL or finished image project id. `attach`: "
            "after (an end card) or before (a title card) returns the user's clip and the card as ONE file, the clip's "
            "sound kept; no = the card alone. Returns download_url and video_file_path. In a film from "
            "plan_video_production, a card is a beat of the plan: do not make it here."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
    )
    async def make_card_clip(seconds: float, words: list[str] | None = None, background: str = "black",
                             like_video: str | None = None, aspect: Literal["16:9", "9:16", "1:1"] = "16:9",
                             picture: str | None = None, attach: Literal["no", "before", "after"] = "no") -> dict:
        _need()
        if not words and not picture:
            raise ToolError("Give `words`, a `picture`, or both.")
        with tempfile.TemporaryDirectory() as folder:
            size = media_tools.size_for(aspect)
            if like_video:
                (Path(folder) / "v").mkdir()
                clip, is_picture = await _fetch_media(like_video, Path(folder) / "v", ("video",))
                info = media_tools.probe(clip)
                size = (info["width"] - info["width"] % 2, info["height"] - info["height"] % 2)
            pic = None
            if picture:
                (Path(folder) / "p").mkdir()
                pic = (await _fetch_media(picture, Path(folder) / "p", ("image",)))[0]
            out = Path(folder) / "card.mp4"
            await asyncio.to_thread(card_clip, out, seconds, words, background, size, pic)
            if attach != "no":
                if not like_video:
                    raise ToolError("To attach the card, give the user's clip as `like_video`.")
                joined = Path(folder) / "clip-with-card.mp4"
                await asyncio.to_thread(attach_card, clip, out, joined, attach)
                out = joined
            return await _deliver(out, False)
