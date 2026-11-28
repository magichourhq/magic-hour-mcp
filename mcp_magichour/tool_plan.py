"""plan_tool_call: the tool-management planner.

Tool management is kept separate from video composition: this
module imports nothing from plan_request, video_production, video_production_tool or gates.

The host model fills a question sheet (question_sheets.json) from the user's words; this code checks it and returns
either the questions still open (one batch, once) or the plan: what each answer means, the family's free-tier settings, and
the notes (tool_notes.json) for every tool the plan names. The server calls no model and spends no credits.
Switch: MAGIC_HOUR_TOOL_PLAN=on registers the tool; anything else (the default) leaves the server as before.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

HERE = Path(__file__).parent
ASK, YOUR_CALL, NOT_APPLICABLE = "ask", "your_call", "n/a"
Family = Literal["new_picture", "change_picture", "faces", "change_video", "new_video", "talking", "sound"]


def enabled() -> bool:
    return os.getenv("MAGIC_HOUR_TOOL_PLAN", "off").strip().lower() in ("on", "1", "true")


@lru_cache(maxsize=1)
def sheets() -> dict:
    return json.loads((HERE / "question_sheets.json").read_text())


@lru_cache(maxsize=1)
def notes() -> dict:
    return json.loads((HERE / "tool_notes.json").read_text())


def host_only(row: dict) -> bool:
    """A row the host answers itself (from the files, or a rule): never put to the user as a question."""
    return row["ask"].startswith("(")


def options(row: dict) -> list[str]:
    return list(row.get("answers", {}))


def _match(row: dict, value: str) -> str | None:
    """The answers key the value names, or None."""
    v = value.strip().lower()
    for key in options(row):
        if key.lower() == v:
            return key
    return None


def _words_option(row: dict) -> str | None:
    """The 'exact words' option of a deciding row: hosts send the words themselves (seen in a dry run)."""
    return next((k for k in options(row) if k.lower() == "exact words"), None)


def _tools_named(texts: list[str]) -> list[str]:
    known = notes()["tools"]
    found: list[str] = []
    for text in texts:
        for name in re.findall(r"[a-z][a-z0-9_]+", text):
            if name in known and name not in found:
                found.append(name)
    return found


def _sheet_view(family: str) -> dict:
    f = sheets()["families"][family]
    return {
        "family": family,
        "is": f["is"],
        "rows": [{"id": r["id"], "kind": r["kind"], "applies": r.get("applies", "always"), "ask": r["ask"],
                  "read_from": r["read_from"], "options": options(r), "default": r["default"]} for r in f["rows"]],
        "rules": sheets()["rules"],
    }


# In a test chat, the host told the user a length it made up, then the plan made another.
ONLY_THESE = ' Say only these questions and what happens if each is skipped. Do not tell the user a length, a shape or any other value that is not in these questions: those are decided later.'


def plan(family: str, request: str, answers: dict[str, str] | None, questions_asked: bool = False) -> dict:
    """Check the host's answers against the family's sheet; return the sheet, the open questions, or the plan."""
    all_sheets = sheets()["families"]
    if family not in all_sheets:
        raise ToolError(f"Unknown family {family!r}. Families: {', '.join(all_sheets)}.")
    if answers is None:
        return {"ready": False, "stage": "fill_the_sheet", **_sheet_view(family),
                "next": ("Fill `answers` for every row from the user's words and files: the value is one of the row's "
                         "options (or the user's exact words for a required row such as words, times or a line), "
                         f"'{YOUR_CALL}' when the user handed it over, '{NOT_APPLICABLE}' when the row's `applies` does "
                         f"not fit this request, or '{ASK}' when only the user can answer. Then call again.")}

    rows = {r["id"]: r for r in all_sheets[family]["rows"]}
    unknown = [k for k in answers if k not in rows]
    if unknown:
        raise ToolError(f"Unknown rows for {family}: {', '.join(unknown)}. Rows: {', '.join(rows)}.")

    # A host-only row with `applies` may be left out (it does not apply), but never put to the user (in an audit,
    # "(Read from the files.) Is the target a photo or a video?" went to the user word for word).
    missing_host = [i for i, r in rows.items() if host_only(r) and ("applies" not in r and answers.get(i, ASK) == ASK
                                                                     or "applies" in r and answers.get(i) == ASK)]
    if missing_host:
        raise ToolError(f"Answer these rows yourself from the files or the rules, not by asking the user: {', '.join(missing_host)}.")

    always = [i for i, v in answers.items() if v == NOT_APPLICABLE and "applies" not in rows[i]]
    if always:   # 7 refusals in 3 test chats, each retried as your_call
        answers = {i: (YOUR_CALL if i in always else v) for i, v in answers.items()}
    for i, value in answers.items():
        r = rows[i]
        if r["kind"] == "deciding" and value not in (ASK, YOUR_CALL, NOT_APPLICABLE) and _match(r, value) is None \
                and not _words_option(r):
            raise ToolError(f"Row {i} ({r['kind']}): {value!r} is not an option. Options: {', '.join(options(r))}.")

    # Only rows the host marks 'ask' go to the user (ask only when the answer would materially
    # change the result). A row left out takes its default or, if it has `applies`, does not apply.
    # One batch, no limit on its size, and only once: after the user was asked, rows still marked
    # 'ask' take their default instead of a second round.
    # A row with `no_default` (which person gets the new face) has no safe guess: 'your_call' is asked like 'ask', and
    # still open after the one batch, the plan stops (in an audit, its default "Ask; do not guess" asked again).
    answers = {i: (ASK if v == YOUR_CALL and rows[i].get("no_default") and not questions_asked else v) for i, v in answers.items()}
    open_rows = [r for i, r in rows.items() if answers.get(i) == ASK]
    if open_rows and not questions_asked:
        return {"ready": False, "stage": "ask_the_user", "family": family,
                "questions": [{"id": r["id"], "ask": r["ask"], "if_skipped": r["default"]} for r in open_rows],
                "next": ("Ask the user ALL these questions in ONE message, numbered, in plain words, each with what "
                         "happens if they skip it. This is the only time the user is asked. Then call again with their "
                         f"answers ('{YOUR_CALL}' for any they skip or hand over) and questions_asked=true." + ONLY_THESE)}
    if questions_asked:
        stuck = [i for i, v in answers.items() if v in (ASK, YOUR_CALL) and rows[i].get("no_default")]
        if stuck:
            return {"ready": False, "stage": "missing_answer", "family": family, "rows": stuck,
                    "next": ("Make nothing. Tell the user plainly what is still missing and why it matters: "
                             + " ".join(rows[i]["no_default"] for i in stuck) + " Do not ask again in this turn.")}
        handed = {i for i, v in answers.items() if v == YOUR_CALL}   # the user handed it over; skipped rows take the default
        answers = {i: (YOUR_CALL if v == ASK else v) for i, v in answers.items()}
    else:
        handed = {i for i, v in answers.items() if v == YOUR_CALL}

    decisions, texts = [], []
    for i, r in rows.items():
        value = answers.get(i, NOT_APPLICABLE if "applies" in r else YOUR_CALL)
        if value == NOT_APPLICABLE:
            continue
        # The user's own 'your call' on words means write words that fit, not "No words"
        own_words = value == YOUR_CALL and i in handed and "your_call" in r
        key = _match(r, value) if value != YOUR_CALL else (_words_option(r) if own_words else None)
        if key is None and value != YOUR_CALL and r["kind"] == "deciding":
            key = _words_option(r)   # the user's own words for an 'exact words' option
        if key is not None:
            instruction = r["answers"][key]
        else:   # 'your call', or a required row's own words: the default, and every option to apply it with
            instruction = " | ".join(f"{k}: {t}" for k, t in r["answers"].items())
        chosen = (r["your_call"] if own_words else r["default"]) if value == YOUR_CALL else value
        decisions.append({"id": i, "kind": r["kind"], "value": chosen, "instruction": instruction})
        texts.append(instruction)

    fam = all_sheets[family]
    settings = fam.get("settings", {})
    named = _tools_named(texts + list(settings))
    tool_notes = {t: {k: v for k, v in notes()["tools"][t].items() if k in
                      ("family", "type", "antitype", "limits", "best_settings", "preferred")} for t in named}
    gaps = [d["id"] for d in decisions if "GAP" in d["instruction"]
            or ("GAP" in rows[d["id"]]["why"] and d["instruction"] != "Stop.")]
    return {"ready": True, "stage": "plan", "family": family, "request": request, "decisions": decisions,
            "settings": settings, "free_models": notes().get("free_models", {}), "tools": tool_notes,
            "gaps": gaps, "rules": sheets()["rules"],
            "next": ("Call the tools in the order the decisions give, with `settings` (free-tier models only) and the "
                     "user's details word for word. Check each tool's `antitype` and `limits` before calling it. "
                     + ("For each row in `gaps`, tell the user plainly what cannot be done and offer the nearest option."
                        if gaps else "")).strip()}


def sheet_index() -> str:
    """One line per family: its row ids, deciding options in brackets, * for rows the host answers itself."""
    lines = []
    for family, sheet in sheets()["families"].items():
        rows = [r["id"] + ("*" if host_only(r) else "")   # consent too: it is required and the host answers it
                + (f"({'|'.join(options(r))})" if r["kind"] == "deciding" else "") for r in sheet["rows"]]
        lines.append(f"{family}: {', '.join(rows)}")
    return "\n".join(lines)


def register_tool_plan(mcp: FastMCP) -> None:
    if not enabled():
        return

    @mcp.tool(
        name="plan_tool_call",
        title="Plan a single tool call",
        description=(
            "Call this only when the answer would materially change the result of a single create or edit, that is: "
            "(1) something the result depends on is missing or ambiguous and only the user can say (which person, "
            "which part of the clip, the shape, the exact words), or the request is vague: it names only the subject, "
            "with no look, setting, colour, shape or words (for example 'make a product pic of a water bottle'): call "
            "this and mark the open rows 'ask', because the user may have details in mind; or (2) the request hits a known "
            "pitfall: a change to the user's own picture or video (new shape, words on it, a cut, sharpen or "
            "restore), a face or head swap, ANY picture with words in it (a headline, poster, flyer, logo, thumbnail, "
            "slides, an ad), music under 15 s or on a video, subtitles, a talking photo, pictures side by side, a logo "
            "on a video. Always call this before add_words_to_media or reframe_media: generation usually does those jobs "
            "better (the words in the generation prompt; a new shape by extending the picture). Otherwise (a clear new "
            "picture, clip or sound with no words) call the tool directly. Not for a film of several shots: use "
            "plan_conversation with family film when it is listed, else plan_request. "
            "Send the family and `answers` in ONE call. Rows (* = answer from the files yourself; options in "
            "brackets):\n" + sheet_index() + "\n"
            "Answer a row with an option, the user's own words, 'your_call' or 'n/a'; mark 'ask' ONLY for a row "
            "whose answer would change the result and that the user's words and files do not answer. Rows you leave "
            "out take their default. Mark as few rows 'ask' as you can. Returns every open question "
            "in one batch (ask the user once, all together), or the plan: which tools in "
            "order, free-tier settings (free models only), each tool's known faults, and gaps (what no tool can do). Call with the "
            "family only to see the full sheet. Read-only; spends no credits and creates nothing."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    )
    def plan_tool_call(family: Family, request: str, answers: dict[str, str] | None = None,
                       questions_asked: bool = False) -> dict:
        """questions_asked: true once the user has been asked this plan's questions; nothing is asked again."""
        return plan(family, request, answers, questions_asked)
