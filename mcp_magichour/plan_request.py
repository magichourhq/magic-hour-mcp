"""plan_request: check the host's parameter sheet against the user's own words.

The host model fills a sheet (one row per parameter: value and source). This code keeps a value
only when the user gave it or it follows from the user's words, and returns a question from a
fixed set for each value that is still missing. No model, no network, no credits.
"""

from __future__ import annotations

import os
import re
from typing import Any, Literal

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from .video_production import ROUTING_RULE

Source = Literal["user", "derived", "default", "missing"]

# The universal question set. One text for each parameter, asked word for word.
QUESTIONS = {
    "aspect": "Where will this be shown or posted (Instagram, TikTok, YouTube, a website, print, somewhere else)?",
    "seconds": "How long should it be?",
    "exact_words": "Should any words appear on it? If so, which, exactly?",
    "sound": "Should it have sound: music, a voice, or silent?",
    "language": "Which language for the voice or captions?",
    "which_face": "Whose face goes where (which person in the picture)?",
    "look": "Any look in mind (realistic, illustrated, a brand colour, a mood)? 'Your call' is fine.",
    "subject": "What should it show?",
    "edit": "What should change in the picture?",
    "keep": "What must stay exactly as it is?",
    "motion": "What should move, and how (small natural motion, a slow push in, something happening)?",
    "mood": "What mood or genre for the music?",
    "script": "What should be said, word for word? Or should I write it from the gist?",
    "range": "Which part should be kept (from when to when, or 'the best 10 seconds')?",
}

# tool: (the parameters it uses, the parameters it cannot run without)
TOOLS: dict[str, tuple[list[str], list[str]]] = {
    "generate_image": (["subject", "aspect", "exact_words", "look"], ["subject", "aspect"]),
    "edit_image": (["edit", "keep", "aspect", "exact_words"], ["edit"]),
    "create_video_from_text": (["subject", "seconds", "aspect", "sound", "exact_words", "look"], ["subject", "seconds", "aspect", "sound"]),
    "create_video_from_image": (["motion", "seconds", "aspect", "sound", "keep"], ["motion", "seconds", "sound"]),
    "generate_music": (["mood", "seconds"], ["mood", "seconds"]),
    "add_subtitles_to_video": (["language"], []),
    "upscale_image": ([], []),
    "remove_image_background": ([], []),
    "swap_face_in_photo": (["which_face"], []),
    "swap_head_in_photo": (["which_face"], []),
    "create_talking_photo": (["script", "language"], ["script"]),
    # plan_request is in the video composition path only
    "plan_video_production": (["subject", "seconds", "aspect", "sound", "exact_words", "look"], ["subject", "seconds", "aspect", "sound"]),
    "trim_media": (["range"], ["range"]),  # cut by code (the tool was missing)
}
ToolName = Literal[
    "generate_image", "edit_image", "create_video_from_text", "create_video_from_image", "generate_music",
    "add_subtitles_to_video", "upscale_image", "remove_image_background", "swap_face_in_photo",
    "swap_head_in_photo", "create_talking_photo", "trim_media", "plan_video_production",
]

# A host often sends the create tool's own argument names. Each maps to the sheet's name.
ALIASES = {"end_seconds": "seconds", "duration": "seconds", "length": "seconds", "aspect_ratio": "aspect",
           "orientation": "aspect", "shape": "aspect", "audio": "sound", "words": "exact_words", "text": "exact_words"}
PROMPT_NAMES = {"prompt", "style.prompt", "description"}
PROMPT_PARAM = {"generate_image": "subject", "create_video_from_text": "subject", "plan_video_production": "subject", "edit_image": "edit",
                "create_video_from_image": "motion", "generate_music": "mood"}
DEFAULT_ALLOWED = {"look", "position", "transition", "voice", "language", "keep"}
VIDEO_FROM_PICTURE = {"create_video_from_image", "create_talking_photo"}
YOUR_CALL_VALUES = {"aspect": "16:9", "seconds": 10, "sound": "ambient"}  # sound on
MAX_QUESTIONS = 3


def question_rounds() -> int | None:
    """Trial: MAGIC_HOUR_QUESTION_ROUNDS = how many batches of questions the user is asked at most.
    Unset: the old behaviour (three questions a call, no limit on rounds)."""
    raw = os.getenv("MAGIC_HOUR_QUESTION_ROUNDS", "").strip()
    return int(raw) if raw.isdigit() and int(raw) >= 1 else None


def question_rounds_used(asked: list[str]) -> int:
    """The batches already put to the user: the assistant messages that asked any of the set's questions."""
    return sum(1 for text in asked if any(asked_about(name, text) for name in QUESTIONS))

_I = re.ASCII | re.IGNORECASE
# The plain ways a person hands the choice over. In testing, "you pick", "wherever you pick"
# and "idk just make it" were unrecognised, so the shape question was asked again and again (57 of 163 cases with a casual user).
YOUR_CALL = re.compile(r"your call|you (decide|pick|choose)|pick (one|something|anything|whatever)|whatever|wherever|whichever"
                       r"|up to you|don'?t care|doesn'?t matter|no preference|anything|surprise me|dealer'?s choice"
                       r"|i don'?t know|idk|dunno|just (make|do) it|you know best|i trust you", _I)
# The user ends the questions: "just make it". Every open choice with a default then takes it, whatever was asked last.
GIVE_UP = re.compile(r"just (make|do) (it|something|whatever|the cheapest)|\bmake it\W*$|stop asking|your call on anything else"
                     r"|whatever you think is best|go ahead and make it", _I)
# The user handed the wording over, in the brief or later: "you write them", "you pick the words", "the exact words are your call",
# "choose a headline yourself". The host may then write the words itself.
YOU_WRITE = re.compile(
    r"you write (them|it)|write (them|it) for me"
    r"|you (?:pick|choose|decide|come up with|write|invent)\b(?:\s+\w+){0,4}\s+(?:words?|headline|tagline|slogan|caption|text|wording)\b"
    r"|\b(?:words?|headline|tagline|slogan|caption|text|wording)\b(?:\s+(?:is|are|will be|to be))?\s*[-:,—]?\s*(?:your call|you pick|up to you|you choose|you decide)"
    r"|\b(?:choose|pick|write|come up with)\b[^.\n]{0,40}\b(?:headline|tagline|slogan|caption)\b[^.\n]{0,25}\byourself", _I)
# The user approves the words the assistant just proposed. A bare "yes" does not: it may only answer "should any words appear?".
AFFIRM = re.compile(r"\b(?:use (?:that|it|those|them)|go with (?:that|it|those)|sounds good|looks good|that works|that one|those are fine|perfect)\b", _I)
# "No text" in the user's words. "No idea what words" is not it.
NO_TEXT = re.compile(
    r"\b(?:no|without|zero)\s+(?:(?:added|extra|any|on-?screen|visible|overlaid|written|printed)\s+){0,3}(?:text|words?|captions?|lettering|writing)\b"
    r"|\b(?:don'?t|do not|never)\s+(?:want|need|include|add|put|use|have|show)\s+(?:any\s+)?(?:text|words?|captions?|lettering|writing)\b"
    r"|\btext[- ]free\b", _I)
NONE_VALUE = re.compile(r"\W*(?:none|nothing|n/?a)\W*", _I)
# What each question is about, so "your call" answers a question the host reworded (a host rarely repeats the set word for word).
ASKED_ABOUT = {
    "aspect": r"shown|posted|vertical|square|wide|shape|platform|instagram|tiktok|youtube",
    "seconds": r"how long|seconds|length|duration",
    "exact_words": r"\bwords?\b|\btext\b|captions?|headline|wording|lettering",
    "sound": r"\bsound\b|music|silent|voice|audio",
    "motion": r"\bmov(?:e|es|ing|ement)\b|motion|animat|camera",
}
DESIGNED_PIECE = re.compile(r"\b(post|poster|flyer|banner|card|ad|advert|announcement|invite|invitation)\b", _I)
DESTINATION = re.compile(r"\b(website|web ?site|web|social|socials|online|site|homepage|ad|advert)\b", _I)
PRINT_WORDS = re.compile(r"\b(poster|flyer|print|a4|letter)\b", _I)
# A file name is not the user's wording: "portrait-woman.jpg" does not ask for a tall picture.
ATTACHED = re.compile(r"\[Attached:[^\]]*\]|\S+\.(?:png|jpe?g|webp|gif|mp4|mov|webm|mp3|wav|m4a)\b", _I)
# An edit is stated when the user's words name a change.
CHANGE_VERB = re.compile(
    r"\b(remove|delete|erase|add|put|place|change|replace|swap|make|turn|fix|clean|brighten|darken|lighten|warm|cool|crop|"
    r"blur|sharpen|colou?r|retouch|convert|extend|fill|move|cut|take out|get rid)", _I)
OPTIONAL_QUIET = {"look", "keep", "language", "exact_words"}  # never asked just because the host left them open
PLATFORMS = [
    (r"tiktok|reels?\b|stories|story\b|\bshorts\b|vertical|portrait (?:video|format|mode|orientation|reel)|in portrait\b|phone", "9:16"),
    (r"youtube|landscape|widescreen|tv\b|wide\b|banner|presentation|desktop", "16:9"),
    (r"instagram post|square|feed|profile pic", "1:1"),
    (r"instagram", "1:1"),
    (r"linkedin|facebook|twitter|\bx\b", "16:9"),
    (r"print|poster|flyer|a4|letter", "9:16"),
]


def parse_aspect(text: str) -> str | None:
    """A shape from plain words ("Instagram story") or an exact ratio ("9:16")."""
    ratio = re.search(r"\b(16:9|9:16|1:1|4:3|3:4|4:5)\b", text, re.ASCII)
    if ratio:
        return {"4:5": "9:16", "3:4": "9:16", "4:3": "16:9"}.get(ratio[1], ratio[1])
    for pattern, value in PLATFORMS:
        if re.search(pattern, text, _I):
            return value
    return None


def parse_seconds(text: str) -> int | None:
    """A length from one message. A bare number counts only in a short answer ("10", "about 15"), never "20% off"."""
    stated = re.search(r"(\d+(?:\.\d+)?)[\s-]*(s\b|sec|seconds?)", text, _I)
    if stated:
        return max(1, int(float(stated[1]) + 0.5))
    bare = re.search(r"\b(\d{1,3})\b(?!\s*%)", text, re.ASCII)
    if bare and len(text.split()) <= 3:
        return max(1, int(bare[1]))
    if re.search(r"half a minute", text, _I):
        return 30
    if re.search(r"minute", text, _I):
        return 60
    if re.search(r"short|quick|brief", text, _I):
        return 8
    if re.search(r"reel|tiktok|story", text, _I):
        return 15
    return None


def parse_sound(text: str) -> str | None:
    """A sound choice from one message. "No music" is not a request for music."""
    if re.search(r"silent|no sound|mute|none|quiet", text, _I):
        return "none"
    text = re.sub(r"\b(no|without)\s+(music|voice-?over|voice|narrat\w*|words?|dialog\w*)\b", " ", text, flags=_I)
    if re.search(r"dialog", text, _I):
        return "dialogue"
    if re.search(r"voice|narrat|speak|talk|say", text, _I):
        return "narration"
    if re.search(r"music|song|beat|track", text, _I):
        return "music"
    if re.search(r"ambient|natural sound|sfx", text, _I):
        return "ambient"
    return None


def _first(parser, texts: list[str]):
    return next((value for value in map(parser, texts) if value is not None), None)


def as_word_list(value: Any) -> list[str] | None:
    """exact_words arrives as a list, as one string, or as "No text". Always a list of lines; [] means the user wants no words."""
    if value is None:
        return None
    items = [value] if isinstance(value, str) else list(value.values()) if isinstance(value, dict) else list(value) if isinstance(value, (list, tuple)) else [value]   # a dict is text slots: {"top": ..., "bottom": ...}
    words: list[str] = []
    for item in items:
        for line in str(item).splitlines():
            line = line.strip()
            if line and not ((NO_TEXT.search(line) and len(line.split()) <= 10) or NONE_VALUE.fullmatch(line)):
                words.append(line)
    return words


def _plain(text: Any) -> str:
    """Words compared without case, quotes, punctuation or a list number in front: "1 Clear your desk" is "Clear your desk"."""
    text = re.sub(r"^\s*(?:\d+[.)]?|[-•*])\s+", "", str(text))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def asked_about(name: str, message: str) -> bool:
    """The message put this parameter's question: word for word, or in other words (a question sentence about the same thing)."""
    questions = " ".join(re.findall(r"[^.!?\n]*\?", message))   # a suggestion that mentions "vertical" is not a question about shape
    return QUESTIONS[name] in message or bool(re.search(ASKED_ABOUT.get(name, r"(?!)"), questions, _I))


def handed_over(messages: list[dict]) -> set[str]:
    """The parameters the user answered with "your call", at any point in the conversation. Said once, it holds to the end:
    asking again after "you pick" is the loop found in testing. A "your call" that follows no question hands over nothing."""
    names: set[str] = set()
    last_asked = ""
    for message in messages:
        if message["role"] == "assistant":
            last_asked = message["text"]
        elif last_asked and YOUR_CALL.search(ATTACHED.sub(" ", message["text"])):
            names |= {name for name in QUESTIONS if asked_about(name, last_asked)}
    return names


def plan_sheet(messages: list[dict], tool: str, params: list[dict], attachments: list[dict] | None = None) -> dict:
    """Check the sheet. Returns {tool, ready, params, questions}."""
    if tool not in TOOLS:
        raise ValueError(f"Unknown tool: {tool}. Choose one of: {', '.join(TOOLS)}")
    attachments = attachments or []
    uses, required = TOOLS[tool]
    users = [ATTACHED.sub(" ", m["text"]) for m in messages if m["role"] == "user"]
    asked = [m["text"] for m in messages if m["role"] == "assistant"]
    newest, all_user = (users[-1] if users else ""), "\n".join(users)
    your_call = bool(YOUR_CALL.search(newest))

    last_asked = asked[-1] if asked else ""
    handed = handed_over(messages)

    def our_call(name: str) -> bool:
        """The user said "your call" in answer to this parameter's question, now or earlier."""
        return name in handed

    sheet: dict[str, dict] = {}
    for p in params:
        name = p["name"]
        name = PROMPT_PARAM.get(tool, name) if name in PROMPT_NAMES else ALIASES.get(name, name)
        value = p.get("value")
        if name == "sound" and isinstance(value, bool):
            value = "ambient" if value else "none"
        if name == "exact_words":
            value = as_word_list(value)
        if name in uses and name not in sheet:
            sheet[name] = {"name": name, "value": value, "source": p["source"], "from": p.get("from") or ""}
    must_ask: set[str] = set()

    def missing(name: str, ask: bool = False) -> None:
        sheet[name] = {"name": name, "value": None, "source": "missing", "from": ""}
        if ask:
            must_ask.add(name)

    def fill(name: str, value: Any, source: str, why: str) -> None:
        sheet[name] = {"name": name, "value": value, "source": source, "from": why}

    # 1. A row the host left open is asked, except the few that have a safe default. Every required parameter has
    #    a row. A tool that makes a picture or a video always has a shape row (asked only when rule 6 says so).
    must_ask |= {name for name, row in sheet.items() if row["source"] == "missing" and name not in OPTIONAL_QUIET}
    for name in [*required, *(["aspect"] if "aspect" in uses else [])]:
        if name not in sheet:
            missing(name)

    # 1b. The user said "no text": that is the answer for the words. Nothing to ask, nothing to draw.
    if "exact_words" in uses and NO_TEXT.search(all_user) and (
            "exact_words" not in sheet or sheet["exact_words"]["source"] in ("missing", "default") or not sheet["exact_words"]["value"]):
        fill("exact_words", [], "user", "the user asked for no text")

    # 2. A default is permitted only for a few parameters, or when the user said it is our call.
    delegated = bool(YOU_WRITE.search(all_user))   # the user handed the wording over, in the brief or later
    for name, row in list(sheet.items()):
        if row["source"] == "default" and name not in DEFAULT_ALLOWED:
            permitted = our_call(name) or (name == "exact_words" and delegated)
            if not permitted:
                missing(name, ask=True)

    # 3. Exact words are never invented: they are in the user's messages (compared without case, punctuation or a list number),
    #    they are the words the assistant proposed and the user approved, or the user let us write them.
    words = sheet.get("exact_words")
    if words and words["source"] != "missing" and words["value"]:
        said = _plain(all_user)
        if last_asked and (AFFIRM.search(newest) or your_call):
            said += " " + _plain(last_asked)
        not_said = words["source"] == "user" and not all(_plain(w) in said for w in words["value"])
        if (words["source"] == "derived" or not_said) and not delegated:
            missing("exact_words", ask=True)
    designed = tool == "generate_image" and DESIGNED_PIECE.search(all_user)
    already_asked = any(re.search(r"\bwords?\b", text, _I) for text in asked)
    if designed and not already_asked and not re.search("[\"“”']", all_user) and not NO_TEXT.search(all_user) and not delegated:
        missing("exact_words", ask=True)

    # 4. An edit that the user did not state is asked, not assumed. The host's paraphrase of a stated change is kept.
    if tool == "edit_image":
        stated = sheet["edit"]["source"] in ("user", "derived") and CHANGE_VERB.search(all_user)
        if not stated:
            missing("edit", ask=True)

    # 5. Length and sound: kept only when the user's words speak of them; else read from the user's messages, newest first.
    for name, parser in (("seconds", parse_seconds), ("sound", parse_sound)):
        if name not in sheet:
            continue
        if sheet[name]["source"] in ("user", "derived") and _first(parser, users) is None:
            missing(name, ask=True)
        if sheet[name]["source"] == "missing":
            value = _first(parser, users[::-1])
            if value is not None:
                fill(name, value, "derived", "parsed from the user's words")

    # 6. Shape: the user's own words win. Else the picture's own shape, when the output is that picture.
    #    Ask when the picture's shape is not a safe answer.
    if "aspect" in sheet:
        shape_text = PRINT_WORDS.sub("", all_user) if attachments else all_user
        said = parse_aspect(shape_text)
        row = sheet["aspect"]
        if said:
            if not (row["source"] == "user" and row["value"] == said):
                fill("aspect", said, "user", "the user's words")
        elif row["source"] != "default":
            missing("aspect")
            picture = (next((a for a in attachments if a["kind"] == "image" and a.get("aspect")), None)
                       or next((a for a in attachments if a["kind"] == "video" and a.get("aspect")), None))
            if picture and tool not in VIDEO_FROM_PICTURE:
                fill("aspect", picture["aspect"], "derived", f"the shape of {picture['name']}")
            shapes = {a["aspect"] for a in attachments if a.get("aspect")}
            pictures = [a for a in attachments if a["kind"] == "image"]
            unsafe = (tool in VIDEO_FROM_PICTURE or DESTINATION.search(all_user) or len(shapes) > 1
                      or (len(pictures) > 1 and "swap" not in tool))
            if unsafe:
                missing("aspect", ask=True)

    # 7. "Your call" answers a question that was asked. It is not a default for a question that was not. A question asked
    #    twice without a usable answer takes the default too: asking a third time is the loop found in testing.
    #    For a video made from one picture, the shape handed to us is the picture's own.
    picture_shape = next((a["aspect"] for a in attachments if a["kind"] in ("image", "video") and a.get("aspect")), None)
    gave_up = bool(GIVE_UP.search(newest))
    open_rows = [name for name in uses if name in sheet and sheet[name]["source"] == "missing"]
    for name in list(open_rows):
        if name not in YOUR_CALL_VALUES:
            continue
        asked_twice = sum(asked_about(name, text) for text in asked) >= 2
        if our_call(name) or asked_twice or gave_up:
            value = picture_shape if name == "aspect" and picture_shape else YOUR_CALL_VALUES[name]
            why = ("the user said it is our call" if our_call(name) else "the user said to just make it" if gave_up
                   else "asked twice without an answer")
            fill(name, value, "default", why)
            open_rows.remove(name)

    # 8. One question for each open row, required parameters first, three at most.
    to_ask = [name for name in open_rows if name in required or name in must_ask
              or name not in OPTIONAL_QUIET | {"aspect"}]
    to_ask.sort(key=lambda name: name not in required)
    rounds = question_rounds()
    if rounds is None:
        questions = [{"param": name, "question": QUESTIONS[name]} for name in to_ask][:MAX_QUESTIONS]
        return {"tool": tool, "ready": not questions, "params": [sheet[name] for name in uses if name in sheet], "questions": questions}

    # 9. Trial: one batch with every open question, each with what happens if the user skips it. The batch covers
    #    every gap, so "your call" after a batch hands over all that is still open. When the rounds are used up, nothing
    #    more is asked: the open rows are left to the host to infer.
    used = question_rounds_used(asked)
    after_batch = used > 0 and bool(last_asked) and any(asked_about(name, last_asked) for name in QUESTIONS)
    if to_ask and (used >= rounds or (after_batch and your_call) or gave_up):
        why = ("the user said it is our call" if after_batch and your_call else "the user said to just make it" if gave_up
               else f"{used} round(s) of questions asked, the limit is {rounds}: infer it")
        for name in to_ask:
            if name in YOUR_CALL_VALUES:
                fill(name, picture_shape if name == "aspect" and picture_shape else YOUR_CALL_VALUES[name], "default", why)
            else:
                fill(name, None, "default", why + "; choose a value that fits the request and tell the user what you chose")
        to_ask = []
    skip = {name: f"if you skip it: {YOUR_CALL_VALUES[name]}" for name in YOUR_CALL_VALUES}
    questions = [{"param": name, "question": QUESTIONS[name], "if_skipped": skip.get(name, "if you skip it: the assistant chooses")}
                 for name in to_ask]
    return {"tool": tool, "ready": not questions, "params": [sheet[name] for name in uses if name in sheet], "questions": questions,
            "rounds": {"used": used, "limit": rounds}}


class Message(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class SheetRow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str
    value: Any = None
    source: Source
    why: str | None = Field(default=None, alias="from")


class Attachment(BaseModel):
    name: str
    kind: Literal["image", "video", "audio"]
    aspect: Literal["16:9", "9:16", "1:1"] | None = None
    seconds: float | None = None
    role: Literal["person", "copy", "product", "environment", "other"] | None = None


def register_plan_request(mcp: FastMCP) -> None:
    @mcp.tool(
        name="plan_request",
        title="Check the request before creating",
        description=(
            "Call this before plan_video_production, for a video made of several clips (longer than 15 seconds, 3 or "
            "more separate ideas, or a card or exact words with a scene), or a video with a purpose, with tool "
            "plan_video_production. " + ROUTING_RULE + " Do not call it before a single create or edit call. Pass the whole conversation so far, in order: each user message "
            "word for word and each message you sent the user, your questions included. Pass the tool you plan to "
            "call, and one row for each parameter you know: value and source (user = the user said it, derived = it "
            "follows from the user's words, default = your choice, missing = unknown). Row names: subject, edit, keep, "
            "motion, seconds, aspect, sound, exact_words, look, mood, script, language, which_face, range. Returns the "
            "checked sheet and "
            + ("every open question at once. If ready is false, ask the user all the returned questions in ONE message, "
               "numbered, each with its `if_skipped` default, and say they can answer only some or say 'your call' for the "
               "rest. Rows that come back as default with no value are yours to choose: pick what fits and tell the user. "
               if question_rounds() else "up to 3 questions. ")
            + "If ready is false, ask the user the returned questions word for word and call again "
            "with your question and the user's answer added to messages. If ready is true, create with the returned values. A value the user "
            "did not give comes back missing: do not guess shape, length, sound or on-screen words. exact_words is a "
            "list of strings, one for each line of text to show, exactly as the user wrote it; send [] when the user "
            "wants no text. Read-only; spends no credits and creates nothing."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    )
    def plan_request(
        messages: list[Message],
        tool: ToolName,
        params: list[SheetRow],
        attachments: list[Attachment] | None = None,
    ) -> dict:
        return plan_sheet(
            [m.model_dump() for m in messages],
            tool,
            [{"name": p.name, "value": p.value, "source": p.source, "from": p.why} for p in params],
            [a.model_dump() for a in attachments or []],
        )
