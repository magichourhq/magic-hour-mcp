"""plan_conversation: the conversation front. One call for every request to make or change media.

Flow: guess the intent, pick the family, fill the family's sheet from the user's words and files,
apply the implied defaults, ask what is still open in ONE batch, then hand back the refined plan.

- The seven tool families come from question_sheets.json and tool_notes.json. The sheet check is
  tool_plan.plan(); plan_tool_call itself stays unregistered.
- The film family (several clips, a story, an ad, a reel) is this module's own sheet. A ready film plan goes to
  plan_video_production.
- Required files: each family names the files it needs. A missing file is asked in the same batch.
- Media facts: each attached file with a file_path is read by code (length, size, sound), so a trim or a "whole clip"
  uses exact seconds.

Switch: on by default: plan_conversation is registered and plan_request is left out.
MAGIC_HOUR_CONVERSATION=off brings back plan_request. Read-only, no credits.
"""
from __future__ import annotations

import asyncio
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from . import tool_plan
from .video_production import ROUTING_RULE

ASK, YOUR_CALL, NOT_APPLICABLE = tool_plan.ASK, tool_plan.YOUR_CALL, tool_plan.NOT_APPLICABLE
Family = Literal["new_picture", "change_picture", "faces", "change_video", "new_video", "talking", "sound", "film"]


def enabled() -> bool:
    # On by default: with no switch set the server shipped the old plan_request.
    return os.getenv("MAGIC_HOUR_CONVERSATION", "on").strip().lower() not in ("off", "0", "false")


# Rules every family follows.
IMPLIED = [
    "Every video has sound unless the user asks for silence: the plan picks where it comes from (a voice-over, spoken lines, "
    "the scene's own sound, music). This is sound in the video, not sound in every clip. Do not ask about it.",
    "Captions on a video, when there are captions: white letters with a black outline. Do not ask about the style.",
    "Derive what the words imply (TikTok, Reels, Shorts, a story = 9:16; YouTube, a website = 16:9; Instagram post = 1:1). "
    "Do not ask what the words already answer.",
    "Ask all open questions in ONE message, once. Anything still open after the answer takes its default.",   # no telling of defaults
]

# tool_media.py's tools: registered with MAGIC_HOUR_TOOL_PLAN or MAGIC_HOUR_CONVERSATION (the sheets route to
# them).
TOOL_MEDIA = {"add_sound_to_video", "adjust_colours", "put_pictures_side_by_side", "put_picture_on_video", "make_card_clip",
              "swap_heads_in_photo"}

# Tools no family sheet lists yet: they go here with the files they need. Empty (all moved into the sheets).
EXTRA_TOOLS: dict[str, dict[str, str]] = {}

# The files each family needs: (what, kinds, when). `when` names the case; the host decides if it applies.
FILES = {
    "change_picture": [("the picture to change", ("image",), "always")],
    "faces": [("the photo with the face, head or person to use", ("image",), "always"),
              ("the photo or video it goes onto (for a headshot: none)", ("image", "video"), "a swap, clothes, a scene, a character replace")],
    "change_video": [("the video to change", ("video",), "always")],
    "new_video": [("the picture to animate", ("image",), "the user asks to animate their picture"),
                  ("the song or sound file", ("audio",), "a video made for a song or a sound file")],
    "talking": [("the photo or video of the person who speaks", ("image", "video"), "always"),
                ("the sound file to mouth", ("audio",), "a song, or the user's own recording")],
    "sound": [("a sample of the user's voice", ("audio",), "the user's own voice")],
    "film": [("the product photo", ("image",), "a real product or brand is shown")],
}

FILM_ROWS = [
    {"id": "kind", "kind": "deciding", "ask": "(Read from the words.) What kind of film?",
     # The conversation only asks and records answers; how to build each kind is in the video
     # tool's RULES_OF_THUMB (hook, call to action, one point per beat, one spoken line per beat, fill the length).
     "answers": {"ad": "An ad or a promo for a product, a service or a brand.",
                 "someone_speaks": "An interview, a talking head, a demo, people talking.",
                 "explainer_list": "An explainer, a how-to or a list.",
                 "story_mood": "A story or a mood piece."},
     "default": "story_mood."},
    {"id": "purpose", "kind": "deciding", "ask": "Who is it for, and what should it make them do?",
     "answers": {"stated": "The audience and the goal are in the words.", "none": "A general audience."},
     "default": "Read from the words; else a general audience.",
     "ask_when": "an ad or a promo with no audience in the words"},
    {"id": "shape", "kind": "required", "ask": "Where will it be posted (TikTok, Instagram, YouTube, a website)?",
     "answers": {"9:16": "TikTok, Reels, Shorts, a story.", "16:9": "YouTube, a website, a presentation.", "1:1": "An Instagram post."},
     "default": "9:16 for social video, else 16:9."},
    {"id": "length", "kind": "required", "ask": "About how long, in seconds?",
     "answers": {"seconds": "The length in seconds."}, "default": "15 s (a reel); 30 s for an explainer."},
    {"id": "who_speaks", "kind": "deciding", "ask": "Should anyone talk: a voice-over, a person on camera, people talking to each other, or no one?",
     "answers": {"nobody": "No spoken lines. Sound stays on: music, sound effects or the scene's own sound.",
                 "scene_sound": "The scene's own sound (as the words ask for it), no spoken lines.",
                 "narrator": "A voice-over (in the user's own voice when they give a sample).",
                 "one_on_screen": "One person speaks on camera.",
                 "people_talking": "People talk to each other."},
     "default": "Read from the words; else nobody. Several speak: join them with ' + ' (narrator + one_on_screen).",
     "ask_when": "the words want speech but do not say who speaks"},
    {"id": "sound", "kind": "required", "ask": "(Implied.) The video has sound.",
     "answers": {"on": "The video has sound; who_speaks and music say where it comes from. Not every clip needs its own sound.",
                 "silent": "Only when the user asked for silence."}, "default": "on."},
    {"id": "music", "kind": "deciding", "ask": "(Implied.) Music under the film?",
     "answers": {"yes": "Music under the film.", "no": "No music."},
     "default": "Yes for an ad, a reel or a social post; else no."},
    {"id": "captions", "kind": "deciding", "ask": "(Implied.) Captions of the spoken words?",
     "answers": {"yes": "Captions of the spoken words.", "no": "No captions."},
     "default": "Yes when someone speaks in a social video (TikTok, Reels, Shorts, an ad); else no."},
    {"id": "exact_words", "kind": "deciding", "ask": "Any words that must appear on screen (a title, a price, a website), spelled exactly?",
     "answers": {"none": "No words on screen.", "exact words": "Exactly as the user wrote them."},
     # In testing, a skipped question gave "none", so the ad had no call to action. Now not "none" but not set, so
     # the video tool's rule of thumb (an ad ends on call-to-action words) decides.
     "default": "Not set: the video plan's rules of thumb decide (an ad ends on call-to-action words).",
     "ask_when": "an ad or a promo with no call-to-action words",
     # the user's 'your call' means write words that fit, not "none"
     "your_call": "Write short words that fit the request (a title or a call to action), exactly as they will show. Never invent a price, a date or a web address."},
    {"id": "product_picture", "kind": "deciding", "ask": "Do you have a photo of the product? A film made from words only looks like it.",
     "answers": {"photo": "The product looks like the user's photo.",
                 "web": "No photo from the user: a free-licence photo of the real product from the web.",
                 "words": "The user chose a look-alike made from words: answer none to the plan's picture question."},
     # no user wants a worse version; "words" only when the user chooses it or the product is made up
     "default": "web for a real, sold product; words for a made-up one.", "ask_when": "a real product or brand is shown and no photo is attached"},
]

# The story rules moved to the video tool (RULES_OF_THUMB in video_production_tool.py) or were dropped:
# 6 repeated the video tool's field texts, "no time skip" changed no clip, "avoid colour codes" contradicted the tool.


def _available(tool: str) -> bool:
    return tool_plan.enabled() or enabled() or tool not in TOOL_MEDIA


# A file whose `when` is not "always" is still required when the request's words name its case.
NEEDED_WHEN = {
    "a swap, clothes, a scene, a character replace": r"swap|onto|\bon (?:the|this|my)\b|put|replace|clothes|outfit|wear|scene|into",
    "the user asks to animate their picture": r"\b(?:this|my) (?:photo|pic|picture|image)|animate (?:this|my)",
    "a song, or the user's own recording": r"\bsong|sing|my (?:recording|audio|track)|lip.?sync",
    "the user's own voice": r"\bmy (?:own )?voice|in my voice|sounds? like me|clone",
    "a video made for a song or a sound file": r"\b(?:this|my|the) (?:song|track|audio|recording)\b|music video",
}


# These faces answers need only the person's photo ("outfit" and "beach" made the words ask for a second file).
WORDS_ONLY = {"outfit": "outfit in words", "scene": "scene in words"}
TARGET_ROWS = ("photo_or_video", "character")


def _words_only(answers: dict[str, str] | None, attachments: list[dict]) -> bool:
    answers = answers or {}
    if any(str(answers.get(r, "")).strip().lower() == w for r, w in WORDS_ONLY.items()):
        return True
    # 'your_call' with one file: the row's default is the words option; not when a swap or a character replace applies
    return (any(answers.get(r) == YOUR_CALL for r in WORDS_ONLY) and len(attachments) < 2
            and all(answers.get(r) in (None, NOT_APPLICABLE) for r in TARGET_ROWS))


def _files(family: str, attachments: list[dict], request: str = "", answers: dict[str, str] | None = None) -> dict:
    need = [{"what": w, "kinds": list(k), "when": ("always" if when == "always" or re.search(NEEDED_WHEN.get(when, r"(?!)"), request, re.I) else when)}
            for w, k, when in FILES.get(family, [])]
    if family == "faces" and (re.search(r"headshot", request, re.I) or _words_only(answers, attachments)):
        need = need[:1]
    given = [{"name": a["name"], "kind": a["kind"]} for a in attachments]
    kinds = [a["kind"] for a in attachments]
    missing = []
    for n in need:
        if n["when"] == "always" and not any(k in n["kinds"] for k in kinds):
            missing.append(n["what"])
        elif n["when"] == "always":
            kinds.remove(next(k for k in kinds if k in n["kinds"]))
    checks = ["Before a paid call, check each file: it is the kind the tool takes (photo, video, sound)."]
    if family in ("faces", "talking"):
        checks.append("Call detect_faces on each photo or video with a face (free) and check the face count; "
                      "with more than one face, ask which person (in the same batch).")
    return {"needed": need, "given": given, "missing": missing, "checks": checks}


# case-insensitive ("Silent clip of rain" stayed loud)
SILENCE = re.compile(r"\b(?:silent|silence|no sound|no audio|mute[d]?|without sound|sound off)\b", re.I)
OPTION_ROWS = ("kind", "who_speaks", "sound", "music", "captions", "product_picture")
# a narrator and people on screen in one film had no option; in testing, both hosts were refused once
SEVERAL = {"who_speaks"}


def _parts(row: str, value: str) -> list[str]:
    return [p.strip() for p in value.split("+")] if row in SEVERAL else [value]


ONLY_THESE = tool_plan.ONLY_THESE   # in testing: the host offered 5-10 s, the plan made 15 s


def _film(request: str, answers: dict[str, str] | None, questions_asked: bool, files: dict) -> dict:
    rows = {r["id"]: r for r in FILM_ROWS}
    view = [{"id": r["id"], "kind": r["kind"], "ask": r["ask"], "options": list(r["answers"]), "default": r["default"],
             **({"ask_when": r["ask_when"]} if "ask_when" in r else {})} for r in FILM_ROWS]
    if answers is None:
        return {"ready": False, "stage": "fill_the_sheet", "family": "film", "rows": view, "implied": IMPLIED,
                "files": files, "next": "Fill `answers` for every row, then call again."}
    unknown = [k for k in answers if k not in rows]
    if unknown:
        raise ToolError(f"Unknown film rows: {', '.join(unknown)}. Rows: {', '.join(rows)}.")
    implied = [i for i, r in rows.items() if r["ask"].startswith("(Implied") and answers.get(i) == ASK]
    if implied:
        raise ToolError(f"Do not ask about {', '.join(implied)}: implied. Use the default unless the user's words say otherwise.")
    host_rows = [i for i, r in rows.items() if r["ask"].startswith("(") and i not in implied and answers.get(i) == ASK]
    if host_rows:   # "(Read from the words.)" rows were put to the user word for word
        raise ToolError(f"Answer {', '.join(host_rows)} yourself from the user's words; do not ask the user.")
    open_rows = [r for i, r in rows.items() if answers.get(i) == ASK]
    file_qs = [{"id": "file", "ask": f"Please attach {m}.", "if_skipped": "I cannot make it without this file."} for m in files["missing"]]
    if (open_rows or file_qs) and not questions_asked:
        return {"ready": False, "stage": "ask_the_user", "family": "film",
                "questions": [{"id": r["id"], "ask": r["ask"], "if_skipped": r["default"]} for r in open_rows] + file_qs,
                "next": ("Ask the user ALL these questions in ONE message, numbered, in plain words, each with what happens if "
                         "they skip it. This is the only time the user is asked. Then call again with their answers and "
                         "questions_asked=true." + ONLY_THESE)}
    # Sound is on unless the user's own words ask for silence. "No talking" or "music only" is not silence.
    # The host's sound answer stands (no answer rewrites); the words decide only when it is open
    sound = answers.get("sound")
    silent = sound == "silent" or (sound not in ("on", "silent") and bool(re.search(SILENCE, request)))
    notes = []
    # No answer rewrites (an ablation over 78 test chats); the front guides the refined prompt only
    bad = [f"{i}={v!r}" for i, v in answers.items() if i in OPTION_ROWS and v not in (ASK, YOUR_CALL, NOT_APPLICABLE)
           and (any(p not in rows[i]["answers"] for p in _parts(i, v)) or (len(_parts(i, v)) > 1 and "nobody" in _parts(i, v)))]
    if bad:
        raise ToolError(f"Not an option: {', '.join(bad)}. " + "; ".join(f"{i}: {', '.join(rows[i]['answers'])}" for i in OPTION_ROWS)
                        + ". who_speaks takes several joined with ' + ' (narrator + people_talking).")
    # 'n/a' on a row that always applies (no ask_when) takes its default, as in the families;
    # before, the row was dropped and the plan had no shape or length.
    always_na = [i for i, r in rows.items() if answers.get(i) == NOT_APPLICABLE and "ask_when" not in r and i != "sound"]
    if always_na:
        notes.append(f"{', '.join(always_na)}: '{NOT_APPLICABLE}' became '{YOUR_CALL}' (the row always applies; its default is used).")
        answers = {**answers, **{i: YOUR_CALL for i in always_na}}
    decisions = []
    for i, r in rows.items():
        value = answers.get(i, YOUR_CALL)
        if value == YOUR_CALL and i in answers and "your_call" in r:   # the user handed it over (not a skipped question)
            decisions.append({"id": i, "value": r["your_call"]})
        elif value in (ASK, YOUR_CALL):
            decisions.append({"id": i, "value": r["default"]})
        elif value != NOT_APPLICABLE:
            decisions.append({"id": i, "value": value, "instruction": " ".join(r["answers"].get(p, "") for p in _parts(i, value))})
    return {"ready": True, "stage": "plan", "family": "film", "request": request, "decisions": decisions,
            "implied": IMPLIED, "files": files, **({"changed": notes} if notes else {}),
            "next": ("Write the story from the decisions, then call plan_video_production with it. `narrator`, "
                     "`narrator_voice`, `music` and `captions` are fields inside `story`, not arguments of their own. "
                     # "sound" is sound in the video, not the story's `audio` (the scenes' own sound in every clip)
                     + ("The video has sound, not sound in every clip: the who_speaks and music decisions say where it comes from."
                        if not silent else "The story's `audio` is false (the user asked for silence)."))}


# A video request that states no length and no single action may want a longer film: check once.
LENGTH_OR_ONE_ACTION = (r"\b\d+(?:[.,]\d+)?\s*(?:-\s*)?(?:s|sec|secs|seconds?|minutes?|mins?)\b|\bclip\b|\bgif\b|\bloop|\banimate|\bbring .* to life"
                        r"|\bas long as\b|\bsame length\b|\b(?:whole|full|entire) (?:song|track|clip|video)\b"
                        r"|\b(?:pic|photo|picture|image)s?\b.{0,20}\bmove|\banimation\b")
SHORT_OR_FILM = {"id": "short_or_film", "ask": "One short clip, or a longer film (a story, an ad, for a set audience)?",
                 "if_skipped": "One short clip."}
SHAPES = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0, "4:5": 4 / 5, "5:4": 5 / 4, "4:3": 4 / 3, "3:4": 3 / 4, "21:9": 21 / 9}


def _shape(w: int, h: int) -> str:
    if not w or not h:
        return ""
    name, ratio = min(SHAPES.items(), key=lambda kv: abs(kv[1] - w / h))
    return name if abs(ratio - w / h) / ratio < 0.04 else f"{w}:{h}"


# (account, file_path): an upload is read only for the account that made it. A URL needs no account ("").
_MEDIA_CACHE: dict[tuple[str, str], dict] = {}
MEDIA_CACHE_MAX = 256


async def media_facts(attachments: list[dict]) -> list[dict]:
    """Length, size, shape and sound of each attached file with a file_path, read by code (free). Never fails the plan."""
    from . import media_helpers_tool, media_tools, video_production_tool

    out = []
    for a in attachments:
        fp = a.get("file_path")
        if not fp or a.get("kind") == "text":
            continue
        try:
            key = ("" if fp.startswith(("http://", "https://")) else await video_production_tool._account_id(), fp)
            if key not in _MEDIA_CACHE:
                with tempfile.TemporaryDirectory() as tmp:
                    file, picture = await media_helpers_tool._fetch_media(fp, Path(tmp), ("image", "video", "audio"))
                    if picture:
                        from PIL import Image

                        w, h = Image.open(file).size
                        fact = {"kind": "image", "width": w, "height": h, "shape": _shape(w, h)}
                    else:
                        info = await asyncio.to_thread(media_tools.probe, file)
                        fact = {"kind": "video" if info["width"] else "audio", "seconds": round(info["seconds"], 2)}
                        if info["width"]:
                            fact |= {"width": info["width"], "height": info["height"], "shape": _shape(info["width"], info["height"]),
                                     "has_sound": info["audio"]}
                if len(_MEDIA_CACHE) >= MEDIA_CACHE_MAX:
                    _MEDIA_CACHE.pop(next(iter(_MEDIA_CACHE)))   # the oldest
                _MEDIA_CACHE[key] = fact
            fact = _MEDIA_CACHE[key]
        except Exception as e:  # a file the server cannot read: the plan goes on without its facts; not kept, so a retry reads it
            fact = {"error": f"could not read the file ({type(e).__name__})"}
        out.append({"name": a["name"], **fact})
    return out


MEDIA_NEXT = (" Media facts (`media`) are read from the files: use their exact seconds and shape. A whole clip is 0 to its "
              "seconds; never guess a length.")


def front(family: str, request: str, answers: dict[str, str] | None = None, questions_asked: bool = False,
          attachments: list[dict] | None = None, media: list[dict] | None = None) -> dict:
    attachments = attachments or []
    files = _files(family, attachments, request, answers)
    if family == "film":
        out = _film(request, answers, questions_asked, files)
        if out.get("stage") == "fill_the_sheet":
            out["next"] = out.get("next", "") + ANSWER_RULES
        if media:
            out["media"] = media
            out["next"] = out.get("next", "") + MEDIA_NEXT
        return out
    changed = []
    short_or_film = (answers or {}).get(SHORT_OR_FILM["id"])
    if short_or_film is not None:   # the user's answer to the "short or longer?" check, not a sheet row
        answers = {k: v for k, v in answers.items() if k != SHORT_OR_FILM["id"]}
        said = re.sub(r"\b(?:not|no|never|without|n't \w+)\s+(?:(?:a|an|the)\s+)?(?:long|longer|film|story|ad|several)\b", "",
                      str(short_or_film), flags=re.I)   # "short, not an ad" stays short
        if re.search(r"\b(?:long|longer|film|story|ad|several)\b", said, re.I):
            return {"ready": False, "stage": "switch_family", "family": family,
                    "next": "The user wants a longer film: call plan_conversation again with family film and the same request."}
    if family == "new_video" and answers and answers.get("sound") == ASK:   # sound is implied, never asked
        answers = {**answers, "sound": YOUR_CALL}
        changed.append("sound 'ask' became 'your_call': sound is implied (on unless the user asks for silence), not asked.")
    # No "silent" -> "sound" rewrite here either (no answer rewrites); the host's answer stands.
    tools = set(tool_plan.notes()["tools"]) | {t for f in tool_plan.sheets()["families"].values() for t in f["tools"]}
    named = sorted(k for k in (answers or {}) if k in tools)
    if named:   # hosts sent tool names as rows (in testing: 44 of 60 faces chats)
        answers = {k: v for k, v in answers.items() if k not in tools}
        changed.append(f"Left out {', '.join(named)}: tool names, not rows. Answer only the rows.")
    sheet_rows = {r["id"]: r for r in tool_plan.sheets()["families"].get(family, {}).get("rows", [])}
    always_na = sorted(k for k, v in (answers or {}).items() if v == tool_plan.NOT_APPLICABLE and k in sheet_rows and "applies" not in sheet_rows[k])
    if always_na:
        changed.append(f"{', '.join(always_na)}: '{tool_plan.NOT_APPLICABLE}' became '{tool_plan.YOUR_CALL}' (the row always applies; its default is used).")
    out = tool_plan.plan(family, request, answers, questions_asked)
    if changed:
        out["changed"] = changed
    extra = {t: d for t, d in EXTRA_TOOLS.get(family, {}).items() if _available(t)}
    out["implied"] = IMPLIED
    out["files"] = files
    if extra:
        out["more_tools"] = extra
    if "tools" in out:
        out["tools"] = {t: n for t, n in out["tools"].items() if _available(t)}
        out["unavailable"] = sorted(t for t in tool_plan.sheets()["families"][family]["tools"] if not _available(t))
    file_qs = [{"id": "file", "ask": f"Please attach {m}.", "if_skipped": "I cannot make it without this file."} for m in files["missing"]]
    if file_qs and not questions_asked:
        if out.get("stage") == "ask_the_user":
            out["questions"] += file_qs
        elif out.get("stage") == "plan":
            out = {"ready": False, "stage": "ask_the_user", "family": family, "questions": file_qs, "files": files,
                   "next": ("Ask the user for these files in ONE message. Then call again with the same answers, the "
                            "attachments and questions_asked=true.")}
    elif files["missing"] and questions_asked and out.get("stage") == "plan":
        out["ready"] = False
        out["stage"] = "missing_file"
        out["next"] = "A file the request needs is still missing: tell the user plainly and make nothing until they attach it."
    if family == "new_video" and not questions_asked and short_or_film is None and not re.search(LENGTH_OR_ONE_ACTION, request, re.I):
        if out.get("stage") == "ask_the_user":
            out["questions"].append(SHORT_OR_FILM)
        elif out.get("stage") == "plan":
            out = {"ready": False, "stage": "ask_the_user", "family": family, "questions": [SHORT_OR_FILM], "files": files,
                   "next": ("Ask the user this in ONE message, with what happens if they skip it. Then call again with the same "
                            "answers and questions_asked=true. A longer film: call again with family film.")}
    if out.get("stage") == "fill_the_sheet":
        out["next"] = out.get("next", "") + ANSWER_RULES
    if media:
        out["media"] = media
        out["next"] = out.get("next", "") + MEDIA_NEXT
    return out


# How to answer the rows. Sent with the rows (stage fill_the_sheet), not in the tool description:
# Claude Code keeps only the first 2,048 bytes of a description.
ANSWER_RULES = (
    " Answer a row with an option, the user's own words, 'your_call' (you pick the conventional value for this kind of "
    "request) or 'n/a'. Mark 'ask' ONLY when the answer would change the result and the user's words and files do not "
    "answer it. A vague request that names only the subject, with no look, setting, colour, shape or words (for example "
    "'make a product pic of a water bottle'): mark the open rows that would change it 'ask', because the user may have "
    "details in mind. Words the result shows or says (a caption, a spoken line, a slogan) that the user did not give: "
    "mark 'ask'; when the user answers 'your call', write words that fit the request. Never ask about sound or caption "
    "style (implied).")


def index() -> str:
    film = ", ".join(r["id"] + ("*" if r["ask"].startswith("(") else "")
                     + (f"({'|'.join(r['answers'])})" if r["kind"] == "deciding" else "") for r in FILM_ROWS)
    lines = [tool_plan.sheet_index(), f"film: {film}"]
    for fam, tools in EXTRA_TOOLS.items():
        lines.append(f"{fam} other tools (call them directly; they are not rows): {', '.join(tools)}")
    return "\n".join(lines)


class Attachment(BaseModel):
    name: str
    kind: Literal["image", "video", "audio", "text"]
    file_path: str | None = None   # the attachment's Magic Hour file_path or URL: the front reads its length, size and sound


def register_conversation_front(mcp: FastMCP) -> None:
    if not enabled():
        return
    families = "; ".join(f"{k}: {v['is']}" for k, v in tool_plan.sheets()["families"].items())

    @mcp.tool(
        name="plan_conversation",
        title="Plan the request (first call)",
        description=(
            "Call this FIRST for every request to make or change a picture, a video or a sound, before any create or edit "
            "call. Guess what the user wants and pick ONE family. Families: " + families + "; film: several clips, a story, "
            "an ad, a reel, an explainer, an interview, a video with a purpose. " + ROUTING_RULE + " "
            # The row index (about 2,000 bytes) left the description: Claude Code keeps only the first
            # 2,048 bytes of a tool description (upstream test_metadata_fits_claude_code_utf8_budget). The first call
            # returns the rows instead.
            "First call: the family, the user's request word for word and the attachments (name, kind and file_path), "
            "with no answers; the result lists the family's rows, their options and how to answer them. Then send `answers` "
            "for every row in ONE call. Returns every open question in one batch (ask the user once, all together, then call again "
            "with questions_asked=true), or the plan: tools, free-tier settings, known faults, needed files, and for a "
            "film the decisions for plan_video_production. Read-only; spends no credits and creates nothing."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    )
    async def plan_conversation(family: Family, request: str, answers: dict[str, str] | None = None,
                                questions_asked: bool = False, attachments: list[Attachment] | None = None) -> dict[str, Any]:
        """questions_asked: true once the user has been asked this plan's questions; nothing is asked again."""
        files = [a.model_dump() for a in attachments or []]
        return front(family, request, answers, questions_asked, files, await media_facts(files))
