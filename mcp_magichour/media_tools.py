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


def read_words(image: Path) -> str | None:
    """The words in a picture, read by tesseract. None when the server has no tesseract (the words are then checked by eye)."""
    exe = shutil.which("tesseract")
    if not exe:
        return None
    done = subprocess.run([exe, str(image), "-", "--psm", "11"], capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else None


def check_words(image: Path, wanted: list[str]) -> dict | None:
    """Read a card's words, as drawn and on a 3x grey copy; the better read counts. None: no reader on this server.
    verdict: "right" (every word found), "misspelled" (most found, one or more not: "slze"), "unread" (the reader
    could not read the letters, as on thin words over a glow; the card is not made again, the eye checks it)."""
    reads = [read_words(image)]
    if reads[0] is None:
        return None
    bigger = image.with_name(image.stem + "-read.png")
    try:
        _ffmpeg(["-i", str(image), "-vf", "scale=iw*3:ih*3:flags=lanczos,format=gray,negate", str(bigger)])
        reads.append(read_words(bigger) or "")
    except Exception:
        pass
    best = max(reads, key=lambda r: words_found(wanted, r or "")[0]) or ""
    found, total = words_found(wanted, best)
    verdict = "right" if found == total else "misspelled" if found * 2 >= total else "unread"
    return {"read": " ".join(best.split()), "found": f"{found} of {total}", "verdict": verdict}


def words_found(wanted: list[str], read: str) -> tuple[int, int]:
    """How many of the wanted words are in the read text, letter for letter ("sleep" drawn as "sleen")."""
    tokens = set(re.findall(r"[a-z0-9]+", read.lower()))
    want = [t for w in wanted for t in re.findall(r"[a-z0-9]+", w.lower())]
    return sum(t in tokens for t in want), len(want)


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


def mix_audio(tracks: list[dict], out: Path, seconds: float | None = None, fade_out: float = 0.0) -> Path:
    """Sound files laid over each other into one sound file (a voice over music, no picture).
    tracks: [{file, at (seconds into the mix), volume (1 = as it is), fade_in (seconds)}]. seconds: the mix length
    (default: to the end of the longest track), at most MIX_MAX_SECONDS. fade_out: seconds of fade at the end of the mix."""
    if (seconds is not None and seconds <= 0) or fade_out < 0:
        raise ValueError("seconds must be more than 0, and fade_out 0 or more")
    inputs, chain = [], []
    for i, t in enumerate(tracks):
        inputs += ["-i", str(t["file"])]
        steps = [f"aresample=48000,aformat=channel_layouts=stereo,volume={float(t.get('volume', 1)):.3f}"]
        if t.get("fade_in"):
            steps.append(f"afade=t=in:d={float(t['fade_in']):.2f}")
        steps.append(f"adelay={int(float(t.get('at', 0)) * 1000)}:all=1")
        chain.append(f"[{i}:a]{','.join(steps)}[t{i}]")
    total = seconds or max(float(t.get("at", 0)) + probe(Path(t["file"]))["seconds"] for t in tracks)
    if total > MIX_MAX_SECONDS:
        raise ValueError(f"The mix would be {total:.0f} s long; the most is {MIX_MAX_SECONDS} s.")
    end =f",afade=t=out:st={max(0, total - fade_out):.2f}:d={fade_out:.2f}" if fade_out else ""
    # apad: a `seconds` longer than the tracks ends in silence (a line padded to the video's length)
    chain.append("".join(f"[t{i}]" for i in range(len(tracks))) + f"amix=inputs={len(tracks)}:duration=longest:normalize=0,apad,atrim=0:{total:.2f}{end}[a]")
    codec = {".wav": ["-c:a", "pcm_s16le"], ".mp3": ["-c:a", "libmp3lame", "-q:a", "2"]}.get(out.suffix.lower(), ["-c:a", "aac", "-b:a", "192k"])
    _ffmpeg([*inputs, "-filter_complex", ";".join(chain), "-map", "[a]", "-vn", *codec, "-t", f"{total:.2f}", str(out)])
    return out


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


# ---- sound: one narration for the whole film, a music track, drawn captions ------------------------------------------
def letters(text: str) -> int:
    return sum(ch.isalnum() for ch in text)


def speech_spans(audio: Path, noise_db: int = -35, min_gap: float = 0.12) -> list[tuple[float, float]]:
    """The stretches of an audio or video file where something is heard."""
    log = _ffmpeg(["-i", str(audio), "-af", f"silencedetect=noise={noise_db}dB:d={min_gap}", "-vn", "-f", "null", "-"], capture=True)
    total = probe_seconds(audio)
    starts = [float(x.split("silence_start:")[1].split()[0]) for x in log.splitlines() if "silence_start:" in x]
    ends = [float(x.split("silence_end:")[1].split()[0]) for x in log.splitlines() if "silence_end:" in x]
    spans, at = [], 0.0
    for i, start in enumerate(starts):
        if start - at > 0.05:
            spans.append((at, start))
        at = ends[i] if i < len(ends) else total
    if total - at > 0.05:
        spans.append((at, total))
    return spans


SAID_LEAD, SAID_TAIL = 0.6, 1.2   # how far before and after its slot a spoken line is looked for


def _heard_in(film: Path, start: float, end: float) -> list[tuple[float, float]]:
    """Where something is heard in the film's own sound between start and end (film seconds). The threshold is set
    from the loudest moment there, so a quiet background between the words reads as a pause."""
    with tempfile.TemporaryDirectory() as folder:
        part = Path(folder) / "part.wav"
        _ffmpeg(["-i", str(film), "-ss", f"{start:.3f}", "-t", f"{max(end - start, 0.1):.3f}", "-vn", "-ac", "1", "-ar", "16000", str(part)])
        report = _ffmpeg(["-i", str(part), "-af", "volumedetect", "-f", "null", "-"], capture=True)
        loudest = re.search(r"max_volume: (-?\d+\.?\d*) dB", report)
        if not loudest or float(loudest.group(1)) < -60:
            return []
        noise = int(max(-50.0, min(-30.0, float(loudest.group(1)) - 25)))
        return [(start + a, start + b) for a, b in speech_spans(part, noise_db=noise, min_gap=0.25)]


def time_spoken(film: Path, lines: list[dict]) -> list[dict]:
    """Caption times for the lines people on screen say (a voice-over and a presenter in one film).
    lines: [{"text", "start", "end", "clip": (start, end)}] in film seconds, in film order; start and end are the
    line's slot (the time the prompt gave it). The speech heard near the slot, in the film's own sound, sets the
    times; when nothing is heard there, the slot is used. A line with no slot (start None: the user's own clip) is
    placed with the other such lines of its clip in the speech heard there, in order, by length (align_parts).
    Each sentence gets a share of its line's time by its letters. Returns [{"text", "start", "end", "timed": "heard" | "slot"}]."""
    if not lines:
        return []
    sound = probe(film)["audio"]
    heard: dict[tuple, list[tuple[float, float]]] = {}
    for clip in {tuple(line["clip"]) for line in lines}:
        heard[clip] = _heard_in(film, *clip) if sound else []
    placed: dict[int, tuple[float, float, str]] = {}
    for clip in {tuple(line["clip"]) for line in lines if line["start"] is None}:
        free = [i for i, line in enumerate(lines) if line["start"] is None and tuple(line["clip"]) == clip]
        found = _share_spans(heard[clip], [lines[i]["text"] for i in free])
        if len(found) == len(free):
            placed.update({i: (a, b, "heard") for i, (a, b) in zip(free, found)})
        else:   # nothing heard: the clip's time, shared by letters
            total, at = sum(letters(lines[i]["text"]) for i in free) or 1, clip[0]
            for i in free:
                length = (clip[1] - clip[0]) * letters(lines[i]["text"]) / total
                placed[i] = (at, at + length, "slot")
                at += length
    out, previous = [], None
    for i, line in enumerate(lines):
        clip = tuple(line["clip"])
        if i in placed:
            start, end, how = placed[i]
            later = placed[i + 1][0] if i + 1 in placed and tuple(lines[i + 1]["clip"]) == clip else clip[1]
            end = max(end, min(start + 1.0, later))   # at least a second on screen
            previous = (clip, end)
            out += _sentences(line["text"], start, end, how)
            continue
        after = lines[i + 1] if i + 1 < len(lines) and tuple(lines[i + 1]["clip"]) == clip else None
        low = max(clip[0], line["start"] - SAID_LEAD, previous[1] if previous and previous[0] == clip else clip[0])
        high = min(clip[1], line["end"] + SAID_TAIL, after["start"] + 0.3 if after else clip[1])
        found = [(max(a, low), min(b, high)) for a, b in heard.get(clip, []) if b > low and a < high]
        found = [(a, b) for a, b in found if b - a >= 0.15]
        if found:
            start, end, how = found[0][0], found[-1][1], "heard"
        else:
            start, end, how = max(low, line["start"]), min(high, line["end"]), "slot"
        end = max(end, min(start + 1.0, high))   # at least a second on screen
        previous = (clip, end)
        out += _sentences(line["text"], start, end, how)
    return out


def _share_spans(spans: list[tuple[float, float]], texts: list[str]) -> list[tuple[float, float]]:
    """Lines said in order over heard spans, with no speech-to-text model: each line takes whole spans, and a line
    ends at the pause where the speech heard so far is closest to its share of the letters. With fewer spans than
    lines, align_parts cuts inside the spans."""
    if not spans or not texts:
        return []
    if len(spans) < len(texts):
        return align_parts(spans, texts)
    heard, total = [], 0.0
    for start, end in spans:
        total += end - start
        heard.append(total)
    weights = [letters(t) or 1 for t in texts]
    out, first, done = [], 0, 0
    for i, weight in enumerate(weights[:-1]):
        done += weight
        target = total * done / sum(weights)
        last = min(range(first, len(spans) - (len(texts) - 1 - i)), key=lambda k: abs(heard[k] - target))
        out.append((spans[first][0], spans[last][1]))
        first = last + 1
    out.append((spans[first][0], spans[-1][1]))
    return out


def _sentences(text: str, start: float, end: float, how: str) -> list[dict]:
    """One caption for each sentence of a line, each a share of the line's time by its letters."""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    total, at, out = sum(letters(s) for s in sentences) or 1, start, []
    for s in sentences:
        length = (end - start) * letters(s) / total
        out.append({"text": s, "start": round(at, 2), "end": round(at + length, 2), "timed": how})
        at += length
    return out


def probe_seconds(file: Path) -> float:
    return probe(file)["seconds"]


def align_parts(spans: list[tuple[float, float]], parts: list[str], fine: list[tuple[float, float]] | None = None) -> list[tuple[float, float]]:
    """Where each part of a script is in a narration that reads the parts in order, with no speech-to-text model.

    Each part gets a share of the spoken time equal to its share of the letters. The boundary between two parts is
    then moved to a pause near it, because a narrator pauses between sentences. `fine`: the same narration cut at
    shorter pauses (speech_spans with a small min_gap), used where no long pause is near.
    """
    if not spans or not parts:
        return []
    spoken = sum(end - start for start, end in spans)

    def said(text: str) -> int:   # about how long a text is when read: a digit is read as a word ("35": thirty-five)
        return letters(text) + 4 * sum(ch.isdigit() for ch in text)

    total_letters = sum(said(p) for p in parts) or 1

    def at_spoken(position: float) -> float:   # the clock time at which this much speech has been heard
        for start, end in spans:
            if position <= end - start:
                return start + position
            position -= end - start
        return spans[-1][1]

    def pauses(of: list[tuple[float, float]]) -> list[tuple[float, float]]:
        return [(of[i][1], of[i + 1][0]) for i in range(len(of) - 1)]

    def pick(candidates: list[tuple[float, float]], guess: float, reach: float, after: float) -> tuple[float, float] | None:
        near = [g for g in candidates if abs((g[0] + g[1]) / 2 - guess) <= reach and g[0] > after]
        return max(near, key=lambda g: (g[1] - g[0]) - 0.15 * abs((g[0] + g[1]) / 2 - guess), default=None)   # the longer pause wins

    cuts, used = [], 0
    for part in parts[:-1]:
        used += said(part)
        guess = at_spoken(spoken * used / total_letters)
        after = cuts[-1] if cuts else spans[0][0]
        # a sentence ends on a real pause; a breath inside a sentence is used only when no real pause is near
        # ("F-35A" is short in letters, long when read; the cut fell on a breath 1.4 s early)
        best = (pick(pauses(spans), guess, 0.6, after) or pick(pauses(spans), guess, 1.5, after)
                or pick(pauses(fine or []), guess, 0.9, after))
        cuts.append((best[0] + best[1]) / 2 if best else guess)
    edges = [spans[0][0], *cuts, spans[-1][1]]
    return [(edges[i], edges[i + 1]) for i in range(len(parts))]


# Sound layers. Priority: 1 speech (narration and spoken lines), 2 music, 3 effects (the clips' own
# sound). A layer is lowered only where a higher layer plays, and only there. With no speech, music is the top layer.
MUSIC_UNDER_SPEECH_DB = 9    # music under speech (18, then 12: more music under the voice)
SWELL_DB = 8                 # music_swell: how far the music rises between the lines
EFFECTS_UNDER_DB = 10        # effects under narration or music
CUE_MAX_RAISE_DB = 15        # a sound before a late music start is raised at most this much to the music's loudness
TARGET_LUFS = -16.0          # the whole film, social video
DUCK_RAMP = 0.3              # seconds to go down or up
DUCK_BRIDGE = 1.0            # a gap shorter than this between two speech spans stays lowered (no pumping)
LIMIT_TRUE_PEAK = 0.89       # about -1 dBTP


VOICE_LEAD = 0.5             # a narration part starts this far into its beat
VOICE_LEAD_MIN = 0.1         # ... or this far, when it does not fit
VOICE_GAP = 0.1              # the least pause between two parts
VOICE_TEMPO_MAX = 1.15       # the most a narration is sped up to fit (more is heard)
HOLD_TAIL = 0.3              # the picture holds this long after the last word


def fit_parts(parts: list[dict], seconds: float) -> dict:
    """Fit a narration's parts into a film of `seconds` without cutting a word (the last words ran past the
    end and were cut). parts: [{"start", "end"} in the narration, "at" in the film, with VOICE_LEAD]. In order:
    1 no part starts before the one before it ends; 2 the parts start VOICE_LEAD_MIN into their beat, not VOICE_LEAD;
    3 the narration is read up to VOICE_TEMPO_MAX faster; 4 the film holds its last frame. Returns
    {"parts" (each with "tempo"), "tempo", "moved", "hold"}: hold = the seconds the last frame is held (0: none)."""
    def place(lead_cut: float, tempo: float) -> list[dict]:
        out, after = [], 0.0
        for p in parts:
            at = max(p["at"] - lead_cut, after + (VOICE_GAP if out else 0.0))
            out.append(dict(p, at=round(at, 3), tempo=tempo))
            after = at + (p["end"] - p["start"]) / tempo
        return out

    def over(placed: list[dict]) -> float:
        last = placed[-1]
        return last["at"] + (last["end"] - last["start"]) / last["tempo"] + HOLD_TAIL - seconds

    if not parts:
        return {"parts": [], "tempo": 1.0, "moved": False, "hold": 0.0}
    placed = place(0.0, 1.0)
    if over(placed) <= 0:
        return {"parts": placed, "tempo": 1.0, "moved": placed != [dict(p, tempo=1.0) for p in parts], "hold": 0.0}
    placed = place(VOICE_LEAD - VOICE_LEAD_MIN, 1.0)
    if over(placed) > 0:
        low, high = 1.0, VOICE_TEMPO_MAX   # the least speed-up that fits (a part held at its beat start shrinks only by itself)
        if over(place(VOICE_LEAD - VOICE_LEAD_MIN, high)) <= 0:
            for _ in range(20):
                low, high = (low, (low + high) / 2) if over(place(VOICE_LEAD - VOICE_LEAD_MIN, (low + high) / 2)) <= 0 else ((low + high) / 2, high)
        placed = place(VOICE_LEAD - VOICE_LEAD_MIN, round(high + 0.0005, 3))
    return {"parts": placed, "tempo": placed[0]["tempo"], "moved": True, "hold": round(max(0.0, over(placed)), 2)}


def hold_end(film: Path, seconds: float, out: Path) -> Path:
    """The film with its last frame held `seconds` longer (its sound padded with silence)."""
    has_audio = probe(film)["audio"]
    _ffmpeg(["-i", str(film), "-vf", f"tpad=stop_mode=clone:stop_duration={seconds:.2f}",
             *(["-af", f"apad=pad_dur={seconds:.2f}"] if has_audio else []),
             "-c:v", "libx264", "-pix_fmt", "yuv420p", *(["-c:a", "aac"] if has_audio else []), str(out)])
    return out


def merge_spans(spans: list[tuple[float, float]], bridge: float = DUCK_BRIDGE) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for start, end in sorted(spans):
        if out and start - out[-1][1] < bridge:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return [(a, b) for a, b in out]


def _inside(spans: list[tuple[float, float]], ramp: float = DUCK_RAMP) -> str:
    """An ffmpeg expression of t: 1 inside the spans, 0 outside, with a linear ramp at each edge."""
    expr = "0"
    for start, end in spans:
        one = f"clip(min((t-{start - ramp:.3f})/{ramp},({end + ramp:.3f}-t)/{ramp}),0,1)"
        expr = one if expr == "0" else f"max({expr},{one})"
    return expr


def _gain(lowered: str, db: float, kept: str = "0") -> str:
    """volume expression: lowered by `db` where `lowered` is 1, never where `kept` is 1."""
    depth = 1 - 10 ** (-db / 20)
    return f"1-{depth:.4f}*({lowered})*(1-({kept}))"


def loudness(file: Path) -> float | None:
    """Integrated loudness in LUFS, or None when nothing is heard."""
    report = _ffmpeg(["-i", str(file), "-af", "ebur128", "-vn", "-f", "null", "-"], capture=True)
    found = re.findall(r"I:\s+(-?\d+\.?\d*) LUFS", report)
    value = float(found[-1]) if found else None
    return None if value is None or value <= -69 else value


def loudness_between(file: Path, spans: list[tuple[float, float]], gain_db: float = 0.0) -> float | None:
    """The loudness of a file inside the spans only (a spoken line inside the film's own sound). gain_db: measured this
    much louder, then taken off again (a quiet background is under the meter's -70 LUFS gate)."""
    pick = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in spans)
    raised = f"volume={gain_db:.1f}dB," if gain_db else ""
    report = _ffmpeg(["-i", str(file), "-vn", "-af", f"aselect='{pick}',{raised}ebur128", "-f", "null", "-"], capture=True)
    found = re.findall(r"I:\s+(-?\d+\.?\d*) LUFS", report)
    value = float(found[-1]) if found else None
    return None if value is None or value <= -69 else round(value - gain_db, 1)


# In testing, the street under a spoken line was 53 dB under the voice and heard only at
# full volume. The steady background of a clip's own sound is lifted, the voice is not touched. The background is what
# afftdn takes out as noise; the lowpass removes the speech traces left in its top.
BED_FILTER = "afftdn=nr=30:nf=-75:tn=1:om=n,lowpass=f=7000"
BED_UNDER_VOICE_DB = 23      # the background is lifted to this far under the spoken line (in testing: +30 dB, 23 LU)
BED_MAX_RAISE_DB = 30        # at most this much
BED_MIN_RAISE_DB = 3         # a smaller lift is not done
BED_MEASURE_GAIN_DB = 40     # a quiet background is measured this much louder (loudness_between)
BED_PAUSE_MARGIN = 0.2       # a pause starts this far after a heard sound and ends this far before the next


def lift_bed(film: Path, spoken: list[tuple[float, float]], clips: list[tuple[float, float]], out: Path) -> dict | None:
    """The film with the steady background of its own sound lifted inside `clips` (the clips with a spoken line), to
    BED_UNDER_VOICE_DB under the speech in `spoken`. The background is measured in the pauses, where nothing is heard
    (speech_spans). None when there is no pause (the background is heard already), no background, or it is loud enough."""
    if not spoken or not clips or not probe(film)["audio"]:
        return None
    voice = loudness_between(film, spoken)
    heard, pauses = speech_spans(film), []
    for start, end in clips:
        at = start
        for a, b in heard:
            if b <= start or a >= end:
                continue
            if a - BED_PAUSE_MARGIN - at >= 0.3:
                pauses.append((at, a - BED_PAUSE_MARGIN))
            at = max(at, b + BED_PAUSE_MARGIN)
        if end - at >= 0.3:
            pauses.append((at, end))
    if voice is None or sum(b - a for a, b in pauses) < 0.5:
        return None
    target = voice - BED_UNDER_VOICE_DB
    street = loudness_between(film, pauses, BED_MEASURE_GAIN_DB)
    if street is not None and street > target - BED_MIN_RAISE_DB:
        return None
    with tempfile.TemporaryDirectory() as folder:   # the background track is a working file, never beside the film
        bed_file = Path(folder) / "bed.wav"
        _ffmpeg(["-i", str(film), "-vn", "-af", BED_FILTER, str(bed_file)])
        bed = loudness_between(bed_file, pauses, BED_MEASURE_GAIN_DB)
        if bed is None:
            return None
        raise_db = min(BED_MAX_RAISE_DB, target - bed)
        if raise_db < BED_MIN_RAISE_DB:
            return None
        level = f"{10 ** (raise_db / 20):.4f}*({_inside(clips)})"
        _ffmpeg(["-i", str(film), "-i", str(bed_file), "-filter_complex",
                 f"[1:a]volume='{level}':eval=frame[bed];[0:a][bed]amix=inputs=2:normalize=0:duration=first[a]",
                 "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(out)])
    return {"raised_db": round(raise_db, 1), "voice_lufs": voice, "background_lufs_before": street,
            "background_lufs": loudness_between(out, pauses, BED_MEASURE_GAIN_DB)}


def add_sound(film: Path, out: Path, narration: Path | None = None, parts: list[dict] | None = None, music: Path | None = None,
              spoken: list[tuple[float, float]] | None = None, music_first: bool = False, layers: Path | None = None,
              music_swell: bool = False, music_at: float = 0.0) -> dict:
    """Mix the film's sound from three layers and normalize it to TARGET_LUFS.
    parts: [{"start", "end"} in the narration, "at" in the film]. spoken: where a person on screen speaks (inside the
    film's own sound). music_first: the music is never lowered (a music video). layers: a folder that keeps each layer
    as its own file, so a later mix can use them again. music_at: the second the music starts (the whoosh first,
    then the music). Returns what was done and measured."""
    seconds = probe_seconds(film)
    folder = layers or Path(tempfile.mkdtemp())
    folder.mkdir(parents=True, exist_ok=True)
    parts = parts if narration and parts else []
    narrated = merge_spans([(p["at"], p["at"] + (p["end"] - p["start"]) / p.get("tempo", 1.0)) for p in parts])
    talk = merge_spans(list(spoken or []))
    speech = merge_spans(narrated + talk)
    silence = ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={seconds:.3f}"]
    # pad to the film's length and stop there (an open-ended apad made ffmpeg wait forever on a multi-part narration)
    fit = f"aresample=48000,aformat=channel_layouts=stereo,apad=whole_dur={seconds:.3f},atrim=0:{seconds:.3f}"
    end = ["-t", f"{seconds:.3f}"]
    files: dict[str, Path] = {}

    # 1 speech: the narration, each part at its place
    files["speech"] = folder / "speech.wav"
    if parts:
        steps = [f"[0:a]atrim={p['start']:.2f}:{p['end']:.2f},asetpts=PTS-STARTPTS,"
                 + (f"atempo={p['tempo']:.3f}," if p.get("tempo", 1.0) != 1.0 else "")
                 + f"afade=t=in:d=0.03,afade=t=out:st={max(0, (p['end'] - p['start']) / p.get('tempo', 1.0) - 0.03):.2f}:d=0.03,"
                 f"adelay={int(p['at'] * 1000)}:all=1[n{i}]"
                 for i, p in enumerate(parts)]
        steps.append("".join(f"[n{i}]" for i in range(len(parts))) + f"amix=inputs={len(parts)}:normalize=0,{fit}[s]")
        _ffmpeg(["-i", str(narration), "-filter_complex", ";".join(steps), "-map", "[s]", *end, str(files["speech"])])
    else:
        _ffmpeg([*silence, str(files["speech"])])

    # 2 music. A film with speech: one steady level, 12 dB under the voice, the whole film (a bed). music_swell: full
    # level between the lines, down only while someone speaks. music_first, or no speech: never lowered.
    if music:
        files["music"] = folder / "music.wav"
        level = "1"
        if speech and not music_first:
            voice = loudness(files["speech"]) if parts else None
            if voice is None and talk and probe(film)["audio"]:
                voice = loudness_between(film, talk)
            track = loudness(music)
            depth = MUSIC_UNDER_SPEECH_DB if voice is None or track is None else track - (voice - MUSIC_UNDER_SPEECH_DB)
            bed = 10 ** (-depth / 20)
            if music_swell:   # between the lines the music rises SWELL_DB over the bed (not to full: that blasted)
                high = min(1.0, bed * 10 ** (SWELL_DB / 20)) if depth > 0 else bed
                level = f"{high:.4f}-{high - bed:.4f}*({_inside(speech)})"
            else:
                level = f"{bed:.4f}"
        late = f"adelay={int(music_at * 1000)}:all=1," if music_at > 0 else ""
        rise = 0.3 if music_at > 0 else 1   # a late start is a cue: it comes in quickly
        _ffmpeg(["-i", str(music), "-af", f"{late}{fit},volume='{level}':eval=frame,afade=t=in:st={music_at:.2f}:d={rise},"
                 f"afade=t=out:st={max(0, seconds - 2):.2f}:d=2", *end, str(files["music"])])

    # 3 effects: the film's own sound, lowered under narration and music; a spoken line in it is never lowered
    files["effects"] = folder / "effects.wav"
    if probe(film)["audio"]:
        # before a late music start the film's own sound (the whoosh) is not lowered
        heard = _inside([(music_at, seconds)]) if music and music_at > 0 else "1"
        above = heard if music else _inside(narrated)
        if music and narrated and music_at > 0:
            above = f"max({heard},{_inside(narrated)})"
        level = _gain(above, EFFECTS_UNDER_DB, _inside(talk)) if (music or narrated) else "1"
        if music and music_at > 0 and not narrated:
            # a cue before the music (the whoosh) came out 25 dB under it; it is raised to the music's
            # loudness, at most CUE_MAX_RAISE_DB
            cue, track = loudness_between(film, [(0, music_at)]), loudness(files["music"])
            raise_db = 0.0 if cue is None or track is None else max(0.0, min(CUE_MAX_RAISE_DB, track - cue))
            if raise_db:
                level = f"({level})*(1+{10 ** (raise_db / 20) - 1:.4f}*(1-({heard})))"
        _ffmpeg(["-i", str(film), "-vn", "-af", f"{fit},volume='{level}':eval=frame", *end, str(files["effects"])])
    else:
        _ffmpeg([*silence, str(files["effects"])])

    order = [k for k in ("speech", "music", "effects") if k in files]
    mix = folder / "mix.wav"
    _ffmpeg([*sum((["-i", str(files[k])] for k in order), []), "-filter_complex",
             "".join(f"[{i}:a]" for i in range(len(order))) + f"amix=inputs={len(order)}:normalize=0[a]", "-map", "[a]", str(mix)])
    before = loudness(mix)
    gain = 0.0 if before is None else TARGET_LUFS - before
    _ffmpeg(["-i", str(film), "-i", str(mix), "-map", "0:v", "-map", "1:a", "-c:v", "copy",
             "-af", f"volume={gain:.2f}dB,alimiter=limit={LIMIT_TRUE_PEAK}:level=disabled", "-c:a", "aac", "-b:a", "192k",
             "-t", f"{seconds:.2f}", str(out)])
    if layers:
        (layers / "layers.json").write_text(json.dumps({
            "priority": ["speech", "music", "effects"], "music_first": music_first, "music_swell": music_swell, "speech": speech, "spoken": talk,
            "files": {k: files[k].name for k in order}, "gain_db": round(gain, 2), "target_lufs": TARGET_LUFS}, indent=1))
    else:
        shutil.rmtree(folder, ignore_errors=True)
    return {"lufs_before": before, "lufs": loudness(out), "speech": speech, "layers": order}


def have_captions() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


FONTS = ("/System/Library/Fonts/Supplemental/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf")
# Fonts shipped with the MCP (Google Fonts, open licences in fonts/)
FONT_DIR = Path(__file__).with_name("fonts")
FONT_FILES = {
    "Anton": "Anton-Regular.ttf", "Bebas Neue": "BebasNeue-Regular.ttf", "Archivo Black": "ArchivoBlack-Regular.ttf",
    "Abril Fatface": "AbrilFatface-Regular.ttf", "Permanent Marker": "PermanentMarker-Regular.ttf",
    "Bungee": "Bungee-Regular.ttf", "Lilita One": "LilitaOne-Regular.ttf", "Pacifico": "Pacifico-Regular.ttf",
}
# Named looks for words in a film or picture. A look never changes the letters: the words are drawn as the host
# wrote them, capitals included. scale: tall narrow fonts are drawn larger, so a title stays larger than a caption. caption: the default for captions (white letters,
# black outline, as in memes). The others are for titles and on-screen words, so they never look like a caption.
LOOKS = {
    "caption": {"font": "Archivo Black", "style": "outline"},
    "bold": {"font": "Anton", "style": "outline", "scale": 1.4},          # a heavy tall title (default for titles)
    "clean": {"font": "Bebas Neue", "style": "shadow", "scale": 1.4},     # modern, thin and tall
    "classic": {"font": "Abril Fatface", "style": "shadow"},  # an elegant serif: luxury, film, food
    "marker": {"font": "Permanent Marker", "style": "outline"},  # hand written: vlog, fun, kids
    "block": {"font": "Bungee", "style": "outline"},        # loud blocks: games, sport, sale
    "playful": {"font": "Lilita One", "style": "outline"},  # round and friendly
    "script": {"font": "Pacifico", "style": "shadow"},     # joined hand writing: a wedding, a café
}
TITLE_LOOK = "bold"


def load_font(points: int, name: str | None = None):
    """A shipped font by name (FONT_FILES), else the system sans."""
    from PIL import ImageFont

    if name and (FONT_DIR / FONT_FILES.get(name, "-")).exists():
        return ImageFont.truetype(str(FONT_DIR / FONT_FILES[name]), points)
    return next((ImageFont.truetype(f, points) for f in FONTS if Path(f).exists()), None) or ImageFont.load_default(points)


def draw_card(words: list[str], background: str, size: tuple[int, int], out: Path, font: str | None = None) -> Path:
    """A flat card: the background colour exactly, the words centred in white, one item on each line, the first larger.
    font: a FONT_FILES name; default the title look's font."""
    from PIL import Image, ImageDraw

    width, height = size
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)
    sizes = [round(min(width, height) * (0.09 if i == 0 else 0.055)) for i in range(len(words))]
    fonts = [load_font(s, font or LOOKS[TITLE_LOOK]["font"]) for s in sizes]
    for i, (line, face) in enumerate(zip(words, fonts)):   # shrink a line that is too wide
        while draw.textlength(line, font=face) > width * 0.86 and face.size > 10:
            face = face.font_variant(size=face.size - 2)
        fonts[i] = face
    gaps = [round(f.size * 1.35) for f in fonts]
    y = (height - sum(gaps)) / 2
    for line, face, gap in zip(words, fonts, gaps):
        draw.text(((width - draw.textlength(line, font=face)) / 2, y), line, font=face, fill="white")
        y += gap
    image.save(out)
    return out


def fit_picture(source: Path, background: str | None, size: tuple[int, int], out: Path, words: list[str] | None = None,
                font: str | None = None) -> Path:
    """The picture unchanged, never cropped. With `background` (a logo, a screenshot): 80% of the frame, centred on that
    colour. Without it (a photo): as large as fits, over a blurred copy of itself that fills the frame.
    words: drawn exactly over the lower part of the picture, one line each, in the title look."""
    from PIL import Image, ImageFilter

    picture = Image.open(source).convert("RGBA")
    room = 0.8 if background else 1.0
    scale = min(size[0] * room / picture.width, size[1] * room / picture.height)
    if background and picture.width <= size[0] and picture.height <= size[1]:
        scale = min(scale, 1.0)   # a small logo is not blown up
    fitted = picture.resize((max(1, round(picture.width * scale)), max(1, round(picture.height * scale))), Image.LANCZOS)
    if background:
        frame = Image.new("RGBA", size, background)
    else:
        cover = max(size[0] / picture.width, size[1] / picture.height)
        wide = picture.resize((round(picture.width * cover) + 1, round(picture.height * cover) + 1), Image.LANCZOS)
        left, top = (wide.width - size[0]) // 2, (wide.height - size[1]) // 2
        frame = wide.crop((left, top, left + size[0], top + size[1])).filter(ImageFilter.GaussianBlur(24))
        frame = Image.alpha_composite(frame, Image.new("RGBA", size, (0, 0, 0, 60)))
    frame.alpha_composite(fitted, ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2))
    if words:
        band = out.with_name(out.stem + "-words.png")
        _caption_png("\n".join(words), size, band, **dict(word_style({"text": "", "look": TITLE_LOOK, "size": "large", "font": font}), at=0.82))
        frame.alpha_composite(Image.open(band).convert("RGBA"))
        band.unlink()
    frame.convert("RGB").save(out)
    return out


def picture_copy(file: Path) -> Path | None:
    """A copy of `file` with its picture type's suffix when it is a still picture (png, jpg, webp), else None. A kept or
    downloaded clip is saved as .mp4 whatever it holds, and ffmpeg loops a still only by its image suffix."""
    with open(file, "rb") as f:
        head = f.read(12)
    suffix = (".png" if head.startswith(b"\x89PNG") else ".jpg" if head.startswith(b"\xff\xd8\xff")
              else ".webp" if head[:4] == b"RIFF" and head[8:12] == b"WEBP" else None)
    if not suffix:
        return None
    out = file.with_name(file.stem + "-picture" + suffix)
    shutil.copyfile(file, out)
    return out


def still_video(image: Path, seconds: float, out: Path) -> Path:
    """A picture held still for `seconds`, silent, at 24 frames a second."""
    _ffmpeg(["-loop", "1", "-i", str(image), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{seconds:.2f}",
             "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=24,format=yuv420p", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(out)])
    return out


def _caption_png(text: str, size: tuple[int, int], out: Path, at: float = 0.80, share: float = 0.052, align: str = "center",
                 style: str = "band", opacity: float = 1.0, font: str | None = None) -> Path:
    """Words on a clear picture of `size`, white on a dark band. at: where the band's middle is (share of the height).
    share: letter height as a share of the short side. A line break in `text` starts a new line.
    align: left, center or right, with a 5% margin (a lower third). style: band (a dark band), shadow (no band,
    a soft shadow under the letters; a watermark) or outline (white letters with a black outline, no band).
    opacity: 0 to 1 for the whole drawing. font: a FONT_FILES name. The words are drawn as given."""
    from PIL import Image, ImageDraw

    width, height = size
    points = round(min(width, height) * share)
    font = load_font(points, font)
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    lines = []
    for given in text.split("\n"):
        current = ""
        for word in given.split():
            if current and draw.textlength(f"{current} {word}", font=font) > width * 0.84:
                lines.append(current)
                current = word
            else:
                current = f"{current} {word}".strip()
        lines.append(current)
    line_height = round(points * 1.3)
    top = round(height * at) - line_height * len(lines) // 2
    top = max(round(points * 0.5), min(top, height - line_height * len(lines) - round(points * 0.5)))   # the band stays inside the frame
    box = max(draw.textlength(line, font=font) for line in lines) + points
    margin = round(width * 0.05)
    left = {"left": margin, "right": width - margin - box}.get(align, (width - box) / 2)
    if style == "band":
        draw.rounded_rectangle([left, top - points * 0.35, left + box, top + line_height * len(lines) + points * 0.2],
                               radius=points * 0.4, fill=(0, 0, 0, 150))
    else:
        from PIL import ImageFilter
        shadow = Image.new("RGBA", size, (0, 0, 0, 0))
        shade = ImageDraw.Draw(shadow)
    for i, line in enumerate(lines):
        x = {"left": left + points / 2, "right": left + box - points / 2 - draw.textlength(line, font=font)}.get(
            align, (width - draw.textlength(line, font=font)) / 2)
        if style == "shadow":
            offset = max(1, round(points * 0.06))
            shade.text((x + offset, top + i * line_height + offset), line, font=font, fill=(0, 0, 0, 200))
        stroke = max(2, round(points * 0.09)) if style == "outline" else 0
        draw.text((x, top + i * line_height), line, font=font, fill=(255, 255, 255, 255), stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
    if style != "band":
        image = Image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(max(1, points * 0.08))), image)
    if opacity < 1:
        alpha = image.getchannel("A").point(lambda a: round(a * max(0.0, opacity)))
        image.putalpha(alpha)
    image.save(out)
    return out


def draw_words(source: Path, words: list[dict], out: Path, picture: bool) -> Path:
    """Exact words on a picture or a video. words: [{text, place, start?, end?, large?}]."""
    words = stack_lines(words)
    if picture:
        from PIL import Image

        frame = Image.open(source).convert("RGBA")
        for i, line in enumerate(words):
            band = out.with_name(f"{out.stem}-w{i}.png")
            _caption_png(line["text"], frame.size, band, **word_style(line))
            frame.alpha_composite(Image.open(band).convert("RGBA"))
            band.unlink()
        frame.convert("RGB").save(out)
        return out
    total = probe(source)["seconds"]
    return draw_captions(source, [dict(w, start=w.get("start") or 0, end=w.get("end") or total + 1) for w in words], out)


WORD_PLACES = {"top": 0.15, "middle": 0.5, "bottom": 0.80}
CORNERS = {"top_left": (0.08, "left"), "top_right": (0.08, "right"), "bottom_left": (0.92, "left"), "bottom_right": (0.92, "right")}
SIZES = {"small": 0.032, "normal": 0.052, "large": 0.075}


def word_style(line: dict) -> dict:
    """_caption_png settings for one line: {text, place, align?, size?, large?, look?, font?, style?, opacity?}.
    look: a LOOKS name (font, style); font and style given with it win over the look's."""
    place = line.get("place") or "bottom"
    at, align = CORNERS.get(place, (WORD_PLACES.get(place, 0.80), line.get("align") or "center"))
    size = line.get("size") or ("large" if line.get("large") else "normal")
    look = LOOKS.get(line.get("look") or "", {})
    return {"at": at, "align": align, "share": SIZES.get(size, 0.052) * look.get("scale", 1.0), "style": line.get("style") or look.get("style") or "band",
            "opacity": float(line["opacity"]) if line.get("opacity") is not None else 1.0,
            "font": line.get("font") or look.get("font")}


def stack_lines(lines: list[dict]) -> list[dict]:
    """Lines at the same place that are on screen at the same time become one band, one line under the other
    (two bottom lines were drawn over each other)."""
    out: list[dict] = []
    for line in lines:
        start, end = line.get("start") or 0, line.get("end") if line.get("end") is not None else 1e9
        same = next((o for o in out if (o.get("place") or "bottom") == (line.get("place") or "bottom")
                     and (o.get("start") or 0) == start and (o.get("end") if o.get("end") is not None else 1e9) == end), None)
        if same:
            same["text"] += "\n" + line["text"]
        else:
            out.append(dict(line))
    return out


def draw_captions(film: Path, timed: list[dict], out: Path) -> Path:
    """Draw exact words on the picture. timed: [{"text", "start", "end", "place"?: top|middle|bottom, "large"?: bool}].
    Lines at the same place should not overlap in time. Needs Pillow."""
    info = probe(film)
    timed = stack_lines(timed)
    inputs, chain, last, pngs = ["-i", str(film)], [], "0:v", []
    for i, line in enumerate(timed):
        pngs.append(_caption_png(line["text"], (info["width"], info["height"]), out.with_name(f"{out.stem}-caption{i}.png"),
                                 **word_style(line)))
        # -loop 1 and shortest=1: a single-frame picture was dropped when the film's video started a moment after 0 s
        # (the join's file starts at 0.021 s; words_drawn 3, none on screen)
        inputs += ["-loop", "1", "-i", str(pngs[-1])]
        chain.append(f"[{last}][{i + 1}:v]overlay=shortest=1:enable='between(t,{line['start']:.2f},{line['end']:.2f})'[v{i}]")
        last = f"v{i}"
    _ffmpeg([*inputs, "-filter_complex", ";".join(chain), "-map", f"[{last}]", "-map", "0:a?",
                           "-c:v", "libx264", "-crf", "18", "-c:a", "copy", str(out)])
    for png in pngs:
        png.unlink()
    return out
