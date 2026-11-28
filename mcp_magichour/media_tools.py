"""The file work this server does itself with ffmpeg: frames, the join, trim and reframe, the sound mix, words on screen.

ffmpeg comes from the PATH, else from the imageio-ffmpeg package (a server with no ffmpeg installed).
"""

from __future__ import annotations

import array
import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


import re


def ffmpeg_exe() -> str | None:
    """ffmpeg on the PATH, else the one the `imageio-ffmpeg` package ships (for a server with no ffmpeg installed)."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def have_ffmpeg() -> bool:
    return ffmpeg_exe() is not None


FFMPEG_TIMEOUT = 600   # seconds; an ffmpeg run still going after this is stopped (one call cannot run for ever)


def _run(command: list, **options) -> subprocess.CompletedProcess:
    """subprocess.run with the time limit; a run past FFMPEG_TIMEOUT is stopped and raises RuntimeError."""
    try:
        return subprocess.run(command, timeout=FFMPEG_TIMEOUT, stdin=subprocess.DEVNULL, **options)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{Path(command[0]).name} ran longer than {FFMPEG_TIMEOUT} s and was stopped") from None


def _ffmpeg(args: list[str], capture: bool = False) -> str:
    exe = ffmpeg_exe()
    if exe is None:
        raise RuntimeError("ffmpeg is not available")
    if capture:
        return _run([exe, "-hide_banner", *args], capture_output=True, text=True).stderr
    _run([exe, "-v", "error", "-y", *args], check=True)
    return ""


def probe(file: Path) -> dict:
    """Length, size and whether there is a sound track. With ffprobe when it exists, else from ffmpeg's own report."""
    if shutil.which("ffprobe"):
        raw = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                    "-of", "json", str(file)], check=True, stdout=subprocess.PIPE).stdout
        data = json.loads(raw)
        video = next((s for s in data["streams"] if s["codec_type"] == "video"), {})   # a voice track has no picture
        return {"seconds": float(data["format"].get("duration") or 0), "width": video.get("width", 0), "height": video.get("height", 0),
                "audio": any(s["codec_type"] == "audio" for s in data["streams"])}
    report = _ffmpeg(["-i", str(file)], capture=True)
    duration = re.search(r"Duration: (\d+):(\d+):(\d+\.?\d*)", report)
    size = re.search(r"Video: .*? (\d{2,5})x(\d{2,5})", report)
    if not (size or "Audio:" in report):
        raise RuntimeError(f"ffmpeg could not read {file.name}")
    h, m, sec = duration.groups() if duration else (0, 0, 0)   # a picture has no duration ("Duration: N/A"), as with ffprobe
    return {"seconds": int(h) * 3600 + int(m) * 60 + float(sec), "width": int(size[1]) if size else 0,
            "height": int(size[2]) if size else 0, "audio": "Audio:" in report}


def loudness_db(file: Path) -> float | None:
    """The peak level of the sound track in dB (0 is full scale, about -91 is silence), or None with no sound track."""
    if not probe(file)["audio"]:
        return None
    report = _ffmpeg(["-i", str(file), "-af", "volumedetect", "-vn", "-f", "null", "-"], capture=True)
    found = re.search(r"max_volume: (-?\d+\.?\d*) dB", report)
    return float(found[1]) if found else None


def store(file: Path, name: str, local_dir: Path, public_base: str) -> str:
    """Keep a finished film where the user can fetch it, and return its address.

    With BLOB_READ_WRITE_TOKEN set (Vercel Blob), the file is uploaded there and its public address returned.
    Else it is kept in local_dir and served by this server at public_base/films/name.
    """
    token = os.getenv("BLOB_READ_WRITE_TOKEN")
    if token:
        import httpx
        response = httpx.put(f"https://blob.vercel-storage.com/films/{name}", content=file.read_bytes(), timeout=120,
                             headers={"authorization": f"Bearer {token}", "x-api-version": "7", "x-content-type": {".png": "image/png", ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav"}.get(Path(name).suffix, "video/mp4"),
                                      "x-add-random-suffix": "0"})
        response.raise_for_status()
        return response.json()["url"]
    local_dir.mkdir(parents=True, exist_ok=True)
    if file.resolve() != (local_dir / name).resolve():
        shutil.copy(file, local_dir / name)
    return f"{public_base.rstrip('/')}/films/{name}"


def last_frame(video: Path, out: Path) -> Path:
    _ffmpeg(["-sseof", "-0.15", "-i", str(video), "-frames:v", "1", "-update", "1",
                           "-q:v", "2", str(out)])
    return out


def first_frame(video: Path, out: Path) -> Path:
    """The end frame of a clip made to fit before this one."""
    _ffmpeg(["-i", str(video), "-frames:v", "1", "-q:v", "2", str(out)])
    return out


CROP_LIMIT = 0.2   # a picture of another shape is cropped to fill the frame when this share or less is lost; else filled with a blur


def crop_share(width: int, height: int, target_w: int, target_h: int) -> float:
    """The share of the picture lost when it is scaled to cover the target frame and cropped."""
    ratio, target = width / height, target_w / target_h
    return 1 - min(ratio / target, target / ratio)


def frame_filter(width: int, height: int, target_w: int, target_h: int, how: str = "auto", focus: float = 0.5,
                 color: str = "black") -> tuple[str, str]:
    """An ffmpeg -vf graph that puts a picture of one shape into a frame of another. Returns (graph, way).
    crop: scale to cover and crop the edges. fill: the whole picture over a blurred, cropped copy of itself.
    pad: the whole picture on bars of one colour. focus: where the crop is centred, 0 = left or top, 1 = right or
    bottom (keep the speaker in the frame)."""
    if how == "auto":
        how = "crop" if crop_share(width, height, target_w, target_h) <= CROP_LIMIT else "fill"
    focus = min(1.0, max(0.0, focus))
    cover = (f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
             f"crop={target_w}:{target_h}:(iw-{target_w})*{focus:.3f}:(ih-{target_h})*{focus:.3f}")
    if how == "crop" or (width * target_h == height * target_w):
        return cover, "crop"
    inside = f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease"
    if how == "pad":
        return f"{inside},pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color={color}", "pad"
    cover = f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,crop={target_w}:{target_h}"
    return f"split[a][b];[a]{cover},boxblur=20:2[bg];[b]{inside}[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2", "fill"


def size_for(aspect: str, like: dict | None = None) -> tuple[int, int]:
    """The frame size for an aspect: the clip's own size when it has that shape, else 480p of that shape."""
    ratio = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0}[aspect]
    if like and abs(like["width"] / like["height"] - ratio) < 0.01 * ratio:
        return like["width"] - like["width"] % 2, like["height"] - like["height"] % 2
    return {"16:9": (854, 480), "9:16": (480, 854), "1:1": (640, 640)}[aspect]


def join(files: list[Path], out: Path, audio: bool | list[bool] = True, seconds: list[float | None] | None = None,
         size: tuple[int, int] | None = None, overlap: float = 0.0, even: bool = False) -> Path:
    """Hard cuts, in the order given. Every clip is fitted to `size` (default: the first clip's size) and gets a sound track.
    A clip of another shape is cropped to fill the frame, or filled with a blur when a crop loses too much (frame_filter).
    seconds: one planned length per clip; a longer clip is cut to it (minimax-h3 returns at least about 2 s).
    audio=False: every clip is made silent (minimax-h3 adds a music bed even with audio off).
    A list: one flag per clip (the user's own clip keeps its sound while generated clips are made silent).
    overlap, even: see smooth_cuts."""
    keep = list(audio) if isinstance(audio, list) else [audio] * len(files)
    seconds = list(seconds) if seconds else [None] * len(files)
    infos = [probe(f) for f in files]
    if all(not i["width"] for i in infos):   # voice clips have no picture; join the sound only
        return join_audio(files, out)
    if any(not i["width"] for i in infos):
        raise ValueError("Some files have no picture. Join sound files apart from video files, or use mix_audio.")
    if size is None:
        first = probe(files[0])
        size = (first["width"] - first["width"] % 2, first["height"] - first["height"] % 2)
    width, height = size
    parts = []
    for i, file in enumerate(files):
        part = out.with_name(f"{out.stem}-part{i}.mp4")
        info = infos[i]
        silent = ["-i", str(file), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
        sound = ["-i", str(file)] if keep[i] and info["audio"] else silent
        cut = ["-t", f"{seconds[i]:.3f}"] if seconds[i] and info["seconds"] > seconds[i] + 0.05 else []
        graph, _ = frame_filter(info["width"], info["height"], width, height)
        _ffmpeg([*sound, *cut, "-vf", f"{graph},fps=24,format=yuv420p",
                 "-c:v", "libx264", "-crf", "18", "-c:a", "aac", "-ar", "48000", "-ac", "2", str(part)])
        parts.append(part)
    listing = out.with_suffix(".txt")
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    hard = out.with_name(f"{out.stem}-hard.mp4") if overlap else out
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(hard)])
    for p in [*parts, listing]:
        p.unlink()
    if overlap:
        lengths = [min(i["seconds"], s) if s else i["seconds"] for i, s in zip(infos, seconds)]
        smooth_cuts(hard, files, [k and bool(i["audio"]) for k, i in zip(keep, infos)], lengths, out, overlap, even)
        hard.unlink()
    return out


FIT_MAX_SPEED = 1.15   # a clip whose two ends are both joints is played at most this much faster to fit its slot


def fit_length(file: Path, seconds: float, how: str, out: Path) -> Path:
    """Make a clip that ran long exactly `seconds` long, keeping its last frame (the next clip starts
    from that frame, so cutting the end skipped 0.6 s of motion at the joint).
    how="head": cut the extra time from the start. how="speed": play it faster (both ends are joints), at most FIT_MAX_SPEED;
    a longer clip is cut at the head after that."""
    info = probe(file)
    extra = info["seconds"] - seconds
    if extra <= 0.05:
        shutil.copy(file, out)
        return out
    speed = min(FIT_MAX_SPEED, info["seconds"] / seconds) if how == "speed" else 1.0
    start = max(0.0, info["seconds"] - seconds * speed)
    video = f"setpts=(PTS-STARTPTS)/{speed:.5f}" if speed > 1 else "setpts=PTS-STARTPTS"
    sound = (["-af", f"asetpts=PTS-STARTPTS,atempo={speed:.5f}"] if speed > 1 else []) if info["audio"] else ["-an"]
    _ffmpeg(["-ss", f"{start:.3f}", "-i", str(file), "-vf", video, *sound, "-t", f"{seconds:.3f}",
             "-c:v", "libx264", "-crf", "18", "-c:a", "aac", "-ar", "48000", str(out)])
    return out


FLOOR_GAP_DB = 6.0   # a clip whose background is this much louder than the others is turned down
FLOOR_MAX_CUT_DB = 12.0


def floor_db(file: Path, seconds: float | None = None) -> float | None:
    """The quiet level of a clip's sound: the 10th percentile of its 0.25 s levels (the background between words)."""
    cut = ["-t", f"{seconds:.3f}"] if seconds else []
    raw = _run([ffmpeg_exe() or "ffmpeg", "-v", "error", "-i", str(file), *cut, "-vn", "-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
               capture_output=True).stdout
    samples = array.array("f", raw[: len(raw) - len(raw) % 4])
    if len(samples) < 4000:
        return None
    levels = sorted(10 * math.log10(max(1e-12, sum(x * x for x in samples[i:i + 2000]) / 2000))
                    for i in range(0, len(samples) - 1999, 2000))
    return levels[len(levels) // 10]


def smooth_cuts(film: Path, files: list[Path], keep: list[bool], lengths: list[float], out: Path,
                overlap: float = 0.08, even: bool = True) -> Path:
    """Rebuild the film's sound so no cut jumps (in testing, the rain jumped up at the last cut).
    Each clip's sound runs `overlap` seconds past its cut and fades out while the next fades in (a clip that ran long,
    like minimax-h3, has real sound there). even=True: a clip whose background is FLOOR_GAP_DB louder than the
    middle of the others is turned down to it, at most FLOOR_MAX_CUT_DB."""
    floors = [floor_db(f, s) if k else None for f, k, s in zip(files, keep, lengths)]
    known = sorted(f for f in floors if f is not None)
    middle = known[(len(known) - 1) // 2] if known else None
    gains = [0.0 if (f is None or middle is None or not even or f - middle < FLOOR_GAP_DB)
             else -min(FLOOR_MAX_CUT_DB, f - middle) for f in floors]
    inputs, chain, labels, at = ["-i", str(film)], [], [], 0.0
    for i, (file, k, length) in enumerate(zip(files, keep, lengths)):
        if k:
            n = len(inputs) // 2
            inputs += ["-i", str(file)]
            run = length + (overlap if i < len(files) - 1 else 0)
            fades = (f"afade=t=in:d={overlap:.3f}," if i else "") + (f"afade=t=out:st={run - overlap:.3f}:d={overlap:.3f}," if i < len(files) - 1 else "")
            chain.append(f"[{n}:a]atrim=0:{run:.3f},asetpts=PTS-STARTPTS,apad=whole_dur={run:.3f},"
                         f"aresample=48000,aformat=channel_layouts=stereo,{fades}volume={gains[i]:.1f}dB,"
                         f"adelay={int(at * 1000)}:all=1[c{i}]")
            labels.append(f"[c{i}]")
        at += length
    if not labels:
        shutil.copy(film, out)
        return out
    chain.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{at:.3f}[z]")
    chain.append("[z]" + "".join(labels) + f"amix=inputs={len(labels) + 1}:normalize=0:duration=first[a]")
    _ffmpeg([*inputs, "-filter_complex", ";".join(chain), "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
             "-ar", "48000", "-ac", "2", str(out)])
    return out


def join_audio(files: list[Path], out: Path, gap: float = 0.0) -> Path:
    """Sound files end to end into one file (.m4a, .mp3 or .wav by the suffix of `out`). gap: seconds of silence between."""
    inputs, chain = [], []
    for i, file in enumerate(files):
        inputs += ["-i", str(file)]
        pad = f",apad=pad_dur={gap:.2f}" if gap and i < len(files) - 1 else ""
        chain.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo{pad}[a{i}]")
    chain.append("".join(f"[a{i}]" for i in range(len(files))) + f"concat=n={len(files)}:v=0:a=1[a]")
    codec = {".wav": ["-c:a", "pcm_s16le"], ".mp3": ["-c:a", "libmp3lame", "-q:a", "2"]}.get(out.suffix.lower(), ["-c:a", "aac", "-b:a", "192k"])
    _ffmpeg([*inputs, "-filter_complex", ";".join(chain), "-map", "[a]", "-vn", *codec, str(out)])
    return out


MIX_MAX_SECONDS = 600   # the longest mix (a mix padded to seconds=1e6 wrote a 63 GB file)


def trim(source: Path, out: Path, start: float = 0.0, end: float | None = None) -> Path:
    """The part of a video or sound file from `start` to `end` seconds, cut frame-exact (re-encoded), sound kept."""
    length = probe(source)["seconds"]
    end = length if end is None or end > length else end
    if not 0 <= start < end:
        raise ValueError(f"start {start} and end {end} do not give a part of the {length:.1f} s file")
    if out.suffix.lower() in (".m4a", ".mp3", ".wav"):
        codec = {".wav": ["-c:a", "pcm_s16le"], ".mp3": ["-c:a", "libmp3lame", "-q:a", "2"]}.get(out.suffix.lower(), ["-c:a", "aac"])
        _ffmpeg(["-ss", f"{start:.3f}", "-i", str(source), "-t", f"{end - start:.3f}", "-vn", *codec, str(out)])
    else:
        _ffmpeg(["-ss", f"{start:.3f}", "-i", str(source), "-t", f"{end - start:.3f}", "-c:v", "libx264", "-crf", "18",
                 "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)])
    return out


def reframe(source: Path, aspect: str, out: Path, how: str = "auto", focus: float = 0.5, color: str = "black") -> dict:
    """A picture or video of another shape put into `aspect`: crop (edges cut) or fill (whole picture over a blur).
    auto: crop when it loses 20% or less, else fill. Keeps the sound. Returns the new size and the way used."""
    info = probe(source)
    ratio = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0}[aspect]
    long_side = max(info["width"], info["height"])
    if ratio >= 1:
        target = (long_side - long_side % 2, round(long_side / ratio / 2) * 2)
    else:
        target = (round(long_side * ratio / 2) * 2, long_side - long_side % 2)
    graph, way = frame_filter(info["width"], info["height"], *target, how, focus, color)
    if out.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
        _ffmpeg(["-i", str(source), "-vf", graph, "-frames:v", "1", str(out)])
    else:
        _ffmpeg(["-i", str(source), "-vf", f"{graph},format=yuv420p", "-c:v", "libx264", "-crf", "18", "-c:a", "aac", str(out)])
    return {"width": target[0], "height": target[1], "way": way, "lost": round(crop_share(info["width"], info["height"], *target), 2) if way == "crop" else 0}
