"""General helpers on any file: upload it so this server can read it again, take a first or last frame, join, trim,
reframe, add exact words, mix sound.
They decide nothing; the host chooses when to use them.

A video is named by a Magic Hour video project id (a finished one), a URL the server can download, or a file_path
from upload_media (this server keeps a copy; Magic Hour never hands an upload back).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from . import kept_files, media_tools
from .video_production_tool import FILMS_DIR, _api, _download, _drop_local, _need_ffmpeg, _read_kept, _upload

UPLOAD_MAX_BYTES = 100 * 1024 * 1024
KINDS = {
    "image": {"png", "jpg", "jpeg", "webp", "heic", "avif", "bmp", "tiff", "tif"},
    "video": {"mp4", "m4v", "mov", "webm"},
    "audio": {"mp3", "wav", "aac", "flac", "m4a", "ogg", "opus", "weba"},
}


def _kind(extension: str) -> str:
    for kind, extensions in KINDS.items():
        if extension in extensions:
            return kind
    raise ToolError(f"Unknown file type .{extension}. Give an image, video or audio file name with its extension.")


async def _fetch(video: str, out: Path) -> Path:
    if video.startswith(("http://", "https://")):
        return await _download(video, out)
    if kept_files.is_file_path(video):
        return await _read_kept(video, out)
    project = await _api("GET", f"/v1/video-projects/{video}")
    if project.get("status") != "complete" or not project.get("downloads"):
        raise ToolError(f"video project {video} is {project.get('status')}. Wait for it first, or pass a URL.")
    return await _download(project["downloads"][0]["url"], out)


class Words(BaseModel):
    text: str
    place: Literal["top", "middle", "bottom", "top_left", "top_right", "bottom_left", "bottom_right"] = "bottom"
    align: Literal["left", "center", "right"] = "center"
    size: Literal["small", "normal", "large"] | None = None
    style: Literal["band", "shadow", "outline"] | None = None
    look: Literal["caption", "bold", "clean", "classic", "marker", "block", "playful", "script"] | None = None
    font: Literal["Anton", "Bebas Neue", "Archivo Black", "Abril Fatface", "Permanent Marker", "Bungee", "Lilita One", "Pacifico"] | None = None
    opacity: float | None = Field(default=None, ge=0.05, le=1)
    start: float | None = None
    end: float | None = None


MIX_MAX_SECONDS = 600   # ten minutes: a film's sound bed fits, and one call's ffmpeg work stays bounded


class Track(BaseModel):
    media: str
    at: float = Field(default=0, ge=0, le=MIX_MAX_SECONDS)
    volume: float = Field(default=1, ge=0, le=4)
    fade_in: float = Field(default=0, ge=0, le=MIX_MAX_SECONDS)


AUDIO_SUFFIXES = tuple(f".{e}" for e in KINDS["audio"])
PICTURE_FORMATS = {"PNG": ".png", "JPEG": ".jpg", "MPO": ".jpg", "WEBP": ".webp", "BMP": ".png", "TIFF": ".png"}


def _by_content(file: Path, media: str, kinds: tuple[str, ...]) -> tuple[Path, bool]:
    """Decide what a fetched file is from its bytes, not from the link: a link can have no suffix, or a wrong one.
    A picture gets its own suffix (BMP and TIFF are saved as PNG), so the next step reads it as a picture."""
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(file) as image:
            found = image.format
            if found in ("BMP", "TIFF"):
                target = file.with_suffix(".png")
                image.convert("RGBA" if "A" in image.getbands() else "RGB").save(target)
                return target, True
    except Image.DecompressionBombError:
        raise ToolError(f"{media} is a picture too large to read.") from None
    except (UnidentifiedImageError, OSError):
        found = None
    if found in PICTURE_FORMATS:
        target = file.with_suffix(PICTURE_FORMATS[found])
        file.replace(target)
        return target, True
    try:
        facts = media_tools.probe(file)
    except Exception:
        raise ToolError(f"{media} is not a picture, video or sound file that this server can read.") from None
    if not facts["width"] and "audio" not in kinds:
        raise ToolError(f"{media} is a sound file with no picture; this tool takes a picture or a video.")
    return file, False


async def _fetch_media(media: str, folder: Path, kinds: tuple[str, ...] = ("image", "video")) -> tuple[Path, bool]:
    """A picture, a video (or a sound) by URL, file_path or finished project id. Returns (file, is_picture)."""
    suffix = Path(urlparse(media).path).suffix.lower()
    if media.startswith(("http://", "https://")) or kept_files.is_file_path(media):
        known = {f".{e}" for kind in KINDS.values() for e in kind}
        file = await _fetch(media, folder / f"source{suffix if suffix in known else '.mp4'}")
        return await asyncio.to_thread(_by_content, file, media, kinds)
    for kind in kinds:
        try:
            project = await _api("GET", f"/v1/{kind}-projects/{media}")
        except ToolError:
            continue
        if project.get("status") != "complete" or not project.get("downloads"):
            raise ToolError(f"{kind} project {media} is {project.get('status')}. Wait for it first, or pass a URL.")
        url = project["downloads"][0]["url"]
        suffix = Path(urlparse(url).path).suffix.lower() or {"image": ".png", "audio": ".wav"}.get(kind, ".mp4")
        return await _download(url, folder / f"source{suffix}"), kind == "image"
    raise ToolError(f"{media} is not a URL, a file_path from upload_media, or a finished project id.")


async def _deliver(file: Path, picture: bool) -> dict:
    """Store a made file for the user and, for a picture, upload it so other tools can take it as file_path."""
    FILMS_DIR.mkdir(parents=True, exist_ok=True)
    if file.suffix.lower() in AUDIO_SUFFIXES:
        name = f"{uuid.uuid4().hex}{file.suffix.lower()}"
        base = os.getenv("MAGIC_HOUR_MCP_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")
        url = await asyncio.to_thread(media_tools.store, file, name, FILMS_DIR, base)
        info = await asyncio.to_thread(media_tools.probe, file)
        return {"download_url": url, "seconds": round(info["seconds"], 1), "loudness_db": await asyncio.to_thread(media_tools.loudness_db, file),
                "audio_file_path": await _upload(file, "audio")}
    name = f"{uuid.uuid4().hex}{'.png' if picture else '.mp4'}"
    base = os.getenv("MAGIC_HOUR_MCP_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")
    url = await asyncio.to_thread(media_tools.store, file, name, FILMS_DIR, base)
    if picture:
        from PIL import Image

        width, height = Image.open(file).size
        return {"download_url": url, "width": width, "height": height, "file_path": await _upload(file)}
    info = await asyncio.to_thread(media_tools.probe, file)
    out = {"download_url": url, "width": info["width"], "height": info["height"]}
    out["seconds"] = round(info["seconds"], 1)
    out["video_file_path"] = await _upload(file, "video")
    return out


CODE_TOOL_MODULES = {f"mcp_magichour.{m}" for m in ("media_helpers_tool", "tool_media", "video_production_tool",
                                                     "conversation_front", "tool_plan", "plan_request", "gates")}


def _plain(error: BaseException, tool: str) -> str:
    if isinstance(error, subprocess.CalledProcessError):
        return ("ffmpeg could not process the file. Check that each input is the kind this tool takes (a picture, a "
                "video or a sound) and that the colours, times and sizes are valid.")
    if isinstance(error, subprocess.TimeoutExpired):
        return "ffmpeg took too long on this file. Use a shorter or smaller file."
    if isinstance(error, httpx.HTTPStatusError):
        return f"The link answered HTTP {error.response.status_code}. Give a link that opens the file itself, or upload it with upload_media."
    if isinstance(error, httpx.RequestError):
        return "The link's server could not be reached. Give another link, or upload the file with upload_media."
    return f"{tool} could not use these inputs ({type(error).__name__}). Check them against the tool's description."


class PlainCodeToolErrors(Middleware):
    """A code tool's unexpected failure reaches the host as a plain sentence. Python's own text names the ffmpeg command
    line, this server's folders and its user name; FastMCP writes it to the server log."""

    def __init__(self, mcp: FastMCP) -> None:
        self._mcp = mcp

    async def on_call_tool(self, context, call_next):
        try:
            return await call_next(context)
        except ToolError as error:
            cause = error.__cause__
            if cause is None or isinstance(cause, ToolError):
                raise
            tool = await self._mcp.get_tool(context.message.name)
            if getattr(getattr(tool, "fn", None), "__module__", None) not in CODE_TOOL_MODULES:
                raise
            raise ToolError(_plain(cause, context.message.name)) from None


def register_media_helpers(mcp: FastMCP) -> None:
    mcp.add_middleware(PlainCodeToolErrors(mcp))
    @mcp.tool(
        name="upload_media",
        description=(
            "Upload the user's image, video or audio to Magic Hour through this server. Give `url` (a link the server "
            "can download) or `data_base64` (the file's bytes) with `file_name` (for its type). Returns `file_path` for "
            "any Magic Hour tool. Use this, not get_upload_urls, for a file you may later join, trim or take a frame "
            "from: this server keeps a copy, and Magic Hour never hands an upload back. Up to 100 MB. Spends no credits."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
    )
    async def upload_media(file_name: str, url: str | None = None, data_base64: str | None = None) -> dict:
        if bool(url) == bool(data_base64):
            raise ToolError("Give exactly one of `url` or `data_base64`.")
        extension = Path(file_name or urlparse(url or "").path).suffix.lstrip(".").lower()
        kind = _kind(extension)
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / f"upload.{extension}"
            if url:
                if not url.startswith(("http://", "https://")):
                    raise ToolError("`url` must start with http:// or https://.")
                await _download(url, file)
            else:
                try:
                    file.write_bytes(base64.b64decode(data_base64, validate=True))
                except (binascii.Error, ValueError):
                    raise ToolError("`data_base64` is not valid base64.") from None
            size = file.stat().st_size
            if not size or size > UPLOAD_MAX_BYTES:
                raise ToolError(f"The file is {size} bytes; give one between 1 byte and 100 MB.")
            return {"file_path": await _upload(file, kind), "kind": kind, "bytes": size}

    @mcp.tool(
        name="get_last_frame",
        description=(
            "Take the last frame of a video as a picture, uploaded to Magic Hour. `video`: a finished video project id, "
            "a video URL, or a file_path from upload_media. Returns `file_path`, usable as the start image of "
            "create_video_from_image. Spends no credits."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True),
    )
    async def get_last_frame(video: str) -> dict:
        _need_ffmpeg()
        with tempfile.TemporaryDirectory() as folder:
            clip = await _fetch(video, Path(folder) / "video.mp4")
            frame = await asyncio.to_thread(media_tools.last_frame, clip, Path(folder) / "last-frame.jpg")
            return {"file_path": await _upload(frame)}

    @mcp.tool(
        name="get_first_frame",
        description=(
            "Take the first frame of a video as a picture, uploaded to Magic Hour. `video`: a finished video project id, "
            "a video URL, or a file_path from upload_media. Returns `file_path`, usable as `end_image_file_path` of "
            "create_video_from_image. To replace one clip between two others: start image = get_last_frame of the clip "
            "before, end image = get_first_frame of the clip after. Spends no credits."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True),
    )
    async def get_first_frame(video: str) -> dict:
        _need_ffmpeg()
        with tempfile.TemporaryDirectory() as folder:
            clip = await _fetch(video, Path(folder) / "video.mp4")
            frame = await asyncio.to_thread(media_tools.first_frame, clip, Path(folder) / "first-frame.jpg")
            return {"file_path": await _upload(frame)}

    @mcp.tool(
        name="join_videos",
        description=(
            "Join videos end to end with hard cuts, in the order given, into one file. `videos`: finished video project ids, "
            "video URLs, or file_paths from upload_media. Every video is fitted to the first one's size. `silent`: remove all sound. Returns the film's "
            "download URL, its length and its loudness. Sound files only (voice clips, .wav/.mp3): they are joined into one "
            "sound file (.m4a), for example a dialogue of two voices made one line at a time. Spends no credits."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True),
    )
    async def join_videos(videos: list[str], silent: bool = False) -> dict:
        _need_ffmpeg()
        if not videos:
            raise ToolError("Give at least one video.")
        FILMS_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as folder:
            files = []
            for i, v in enumerate(videos):
                (Path(folder) / f"p{i}").mkdir()
                files.append((await _fetch_media(v, Path(folder) / f"p{i}", ("video", "audio")))[0])
            infos = [await asyncio.to_thread(media_tools.probe, f) for f in files]
            if all(not i["width"] for i in infos):   # voice clips
                if silent:
                    raise ToolError("These are sound files; `silent` would leave nothing.")
                out = Path(folder) / "joined.m4a"
                await asyncio.to_thread(media_tools.join_audio, files, out)
                return await _deliver(out, False)
            if any(not i["width"] for i in infos):
                raise ToolError("Some of `videos` are sound files. Join the sound files on their own, or lay sound over a video with mix_audio.")
            name = f"{uuid.uuid4().hex}.mp4"
            await asyncio.to_thread(media_tools.join, files, FILMS_DIR / name, not silent)
        film = FILMS_DIR / name
        measured = await asyncio.to_thread(media_tools.probe, film)
        loud = await asyncio.to_thread(media_tools.loudness_db, film)
        base = os.getenv("MAGIC_HOUR_MCP_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")
        url = await asyncio.to_thread(media_tools.store, film, name, FILMS_DIR, base)
        _drop_local(film)
        return {"download_url": url, "seconds": round(measured["seconds"], 1), "width": measured["width"],
                "height": measured["height"], "loudness_db": loud}

    @mcp.tool(
        name="reframe_media",
        description=(
            "Change the shape (aspect ratio) of a picture or video by code, so it matches what the user asked: 16:9, 9:16 or "
            "1:1. Use it when a result came back in another shape (edit_image with flux-2-klein keeps the input's shape; "
            "edit_video, create_video_from_image and add_subtitles_to_video have no shape setting), or to reframe the user's "
            "own file. `how`: crop (cut the edges), fill (the whole picture over a blurred copy of itself), pad (the whole "
            "picture on bars of one `color`, for example #1B2A4A or black), or auto (crop when 20% or less is lost, else fill). "
            "`focus`: where a crop is centred, 0 = left (or top), 0.5 = middle, 1 = right (or bottom): look at a frame and "
            "keep the subject in. To follow a subject who moves (two speakers who take turns), trim_media each part, reframe "
            "each with its own focus, then join_videos. `media`: a URL, a file_path from upload_media, or a finished project id. "
            "Returns download_url, width and height (check them), and file_path for further tools. Spends no credits."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
    )
    async def reframe_media(media: str, aspect: Literal["16:9", "9:16", "1:1"], how: Literal["auto", "crop", "fill", "pad"] = "auto",
                            focus: float = 0.5, color: str = "black") -> dict:
        _need_ffmpeg()
        if not (0 <= focus <= 1):
            raise ToolError("`focus` is from 0 (left or top) to 1 (right or bottom).")
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}|[a-z]+", color):
            raise ToolError("`color` is a colour code like #1B2A4A, or a name like black or white.")
        with tempfile.TemporaryDirectory() as folder:
            source, picture = await _fetch_media(media, Path(folder))
            out = Path(folder) / ("framed.png" if picture else "framed.mp4")
            done = await asyncio.to_thread(media_tools.reframe, source, aspect, out, how, focus, color.replace("#", "0x"))
            return {**await _deliver(out, picture), "way": done["way"], "share_cut": done["lost"]}

    @mcp.tool(
        name="add_words_to_media",
        description=(
            "Draw exact words on a picture or video by code: a title, a slogan, a caption, a price, a name, a watermark. "
            "Always spelled right (a generation model often misspells). For words that must look printed on an object (a "
            "label, a sign), make the object without words first, then add them here, or tell the user the model may "
            "misspell them. `words`: [{text, place, align, size, style, look, font, opacity, start, end}]. place: top, middle, bottom, "
            "or a corner (top_left, top_right, bottom_left, bottom_right). align: left, center, right (a lower third: "
            "place bottom, align left). size: small, normal, large. style: band (white letters on a dark band), shadow "
            "(white letters with a soft shadow, no band) or outline (white letters, black outline, as in memes). look: a "
            "named look that sets font and style: caption (the caption default), bold (a heavy tall font, a title), clean, "
            "classic (serif), marker (hand written), block, playful, script. font: Anton, Bebas Neue, Archivo Black, "
            "Abril Fatface, Permanent Marker, Bungee, Lilita One, Pacifico. With no style or look: band. opacity: 0.05 to 1 (a watermark: a corner, size small, style "
            "shadow, opacity 0.6). Lines at the same place and time are stacked, one under the other. start and end in "
            "seconds, video only; leave them out for the whole video. `large`: size large for every line. Returns "
            "download_url, width, height, and file_path for further tools. Spends no credits."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
    )
    async def add_words_to_media(media: str, words: list[Words], large: bool = False) -> dict:
        _need_ffmpeg()
        if not words:
            raise ToolError("Give at least one line in `words`.")
        with tempfile.TemporaryDirectory() as folder:
            source, picture = await _fetch_media(media, Path(folder))
            out = Path(folder) / ("worded.png" if picture else "worded.mp4")
            await asyncio.to_thread(media_tools.draw_words, source, [dict(w.model_dump(), large=large) for w in words], out, picture)
            return await _deliver(out, picture)

    @mcp.tool(
        name="trim_media",
        description=(
            "Cut a video or sound file to the part from `start` to `end` seconds, by code: nothing is generated and the "
            "picture and sound are kept as they are. Use it to shorten the user's clip, make a highlight, keep the first "
            "N seconds, or cut a clip into parts. `media`: a URL, a file_path from upload_media, or a finished video or "
            "audio project id. `end`: leave it out for the end of the file. Returns download_url, seconds, and "
            "video_file_path (or audio_file_path) for further tools. Spends no credits."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
    )
    async def trim_media(media: str, start: float = 0.0, end: float | None = None) -> dict:
        _need_ffmpeg()
        with tempfile.TemporaryDirectory() as folder:
            source, picture = await _fetch_media(media, Path(folder), ("video", "audio"))
            if picture:
                raise ToolError("`media` is a picture; trim_media cuts a video or a sound file.")
            info = await asyncio.to_thread(media_tools.probe, source)
            out = Path(folder) / ("trimmed.mp4" if info["width"] else "trimmed.m4a")
            try:
                await asyncio.to_thread(media_tools.trim, source, out, start, end)
            except ValueError as error:
                raise ToolError(str(error)) from None
            return await _deliver(out, False)

    @mcp.tool(
        name="mix_audio",
        description=(
            "Lay sound files over each other into one sound file, with no picture: a voice over music, a podcast intro, "
            "a jingle. `tracks`: [{media, at, volume, fade_in}]: media is a URL, a file_path, or a finished audio or video "
            "project id; at = seconds into the mix where it starts (for example music at 0, the voice at 3 for a "
            "music-only opening); volume 1 = as it is (music under a voice: about 0.25); fade_in in seconds. `seconds`: "
            "the length of the mix, at most 600 (default: to the end of the longest track; longer than the tracks ends in silence). `fade_out`: seconds of fade at the end. "
            "`format`: m4a, mp3 or wav. Returns download_url, seconds, loudness_db and audio_file_path. Spends no credits."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
    )
    async def mix_audio(tracks: list[Track], seconds: Annotated[float, Field(gt=0, le=MIX_MAX_SECONDS)] | None = None,
                        fade_out: Annotated[float, Field(ge=0, le=MIX_MAX_SECONDS)] = 0.0,
                        format: Literal["m4a", "mp3", "wav"] = "m4a") -> dict:
        _need_ffmpeg()
        if not tracks:
            raise ToolError("Give at least one track.")
        with tempfile.TemporaryDirectory() as folder:
            laid = []
            for i, t in enumerate(tracks):
                (Path(folder) / f"t{i}").mkdir()
                file, picture = await _fetch_media(t.media, Path(folder) / f"t{i}", ("audio", "video"))
                if picture:
                    raise ToolError(f"track {i + 1} is a picture, not a sound.")
                laid.append({"file": file, "at": t.at, "volume": t.volume, "fade_in": t.fade_in})
            if seconds is None:
                lengths = [await asyncio.to_thread(media_tools.probe_seconds, t["file"]) for t in laid]
                longest = max(t["at"] + length for t, length in zip(laid, lengths))
                if longest > MIX_MAX_SECONDS:
                    raise ToolError(f"The mix would be {longest:.0f} s; mix_audio makes at most {MIX_MAX_SECONDS} s. "
                                    "Pass a shorter `seconds`, or trim the tracks with trim_media.")
            out = Path(folder) / f"mix.{format}"
            await asyncio.to_thread(media_tools.mix_audio, laid, out, seconds, fade_out)
            return await _deliver(out, False)
