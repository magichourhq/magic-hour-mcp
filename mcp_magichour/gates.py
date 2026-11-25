"""Broad gates: the host model does the reasoning; the server only makes it take each step.

    start_request    save the user's words; return the simple / hard comparison and the question sheet
    classify_request the host says simple or hard, with reasons; a code backstop refuses "simple" for an obvious hard request
    submit_plan      hard only: the question sheet (answered from the user's words), a checklist, the steps with their tools
    review_plan      the host answers every note for the tools and models it named; a conflict sends it back to submit_plan
    final_check      every checklist line, met or not, with what shows it

MAGIC_HOUR_GATES: off (no gate tools), warn (the create tools run without an approved token and say the gates were
skipped), refuse (a create tool needs the approved token). Each step returns a short token; what it stands for is kept
in this process's memory, so the gates need one server process.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from typing import Any, Literal

import mcp.types as mt
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent, ToolAnnotations
from pydantic import BaseModel, Field

from .gates_docs import COMPARISON, notes_for
from .plan_request import LENGTH, QUESTIONS, QUOTED, brief_items, parse_seconds

MODE = os.getenv("MAGIC_HOUR_GATES", "off")

# Tools that spend credits: a hard request needs an approved plan before any of them.
SPENDING = {
    "add_subtitles_to_video", "change_clothes_in_photo", "colorize_photo", "create_animation", "create_gif_or_short_video",
    "create_lip_sync_video", "create_meme", "create_spoken_ad_video", "create_talking_photo", "create_video_from_audio",
    "create_video_from_image", "create_video_from_text", "edit_face_in_photo", "edit_image", "edit_video", "generate_headshot",
    "generate_image", "generate_music", "place_person_in_scene", "remove_image_background", "replace_character_in_video",
    "restyle_video", "swap_face_in_photo", "swap_face_in_video", "swap_head_in_photo", "translate_video", "upscale_image",
    "upscale_video", "create_production_clip", "create_production_sound",
}
SHEET_REQUIRED = ("subject", "seconds", "aspect", "sound", "exact_words", "look")
SAID = "From the user's words: copy the words that answer it."


# ---- tokens ----------------------------------------------------------------------------------------------------------
# A token is a short id; what it stands for is kept on this server. (The first build put the whole state in the token:
# the host mis-copied the long tokens about 15 times in a dry run.) A deployed server with more than one
# process would keep this in shared storage.
_STORE: dict[str, dict] = {}


def sign(data: dict) -> str:
    token = f"{data['kind'][:3]}-{secrets.token_hex(5)}"
    _STORE[token] = json.loads(json.dumps(data))
    return token


def read(token: str, kind: str | tuple[str, ...]) -> dict:
    data = _STORE.get(token.strip())
    if data is None:
        raise ToolError("This token is not one this server made. Use the token the last gate step returned, unchanged.")
    kinds = (kind,) if isinstance(kind, str) else kind
    if data.get("kind") not in kinds:
        raise ToolError(f"This is a {data.get('kind')} token; this step needs a {' or '.join(kinds)} token.")
    return data


# ---- what the user's words show, by code -------------------------------------------------------------------------------
CONTINUE = re.compile(r"\b(continue|extend|carry on|keep going|add .* (more )?seconds)\b", re.I)
SHOTS = re.compile(r"(?m)^\s*(?:\d+[.)]|shot \d|scene \d)")


def hard_signals(original: str, attachments: list[str]) -> list[str]:
    out = []
    lengths = [float(x) for x in LENGTH.findall(original)]
    seconds = int(max(lengths) + 0.5) if lengths else parse_seconds(original)
    if seconds and seconds > 15:
        out.append(f"{seconds} seconds is longer than 15")
    if QUOTED.search(original):
        out.append("exact words in quotes")
    if len(SHOTS.findall(original)) >= 2:
        out.append("shots listed in order")
    if len(attachments) >= 2:
        out.append(f"{len(attachments)} attached files")
    if CONTINUE.search(original) and any(re.search(r"\.(mp4|mov|webm)\b", a, re.I) for a in attachments + [original]):
        out.append("continues an attached video")
    return out


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


# ---- the shapes the host fills ---------------------------------------------------------------------------------------
class Answer(BaseModel):
    answer: str = Field(description="The value to use.")
    source: Literal["user", "your call", "ask"] = Field(description=(
        "user: the user's words say it (put them in `quote`). your call: the user left it open or said it is your call. "
        "ask: it cannot be worked out; the user must be asked."))
    quote: str = Field(default="", description="With source user: the user's words that say it, copied exactly.")


class Line(BaseModel):
    id: str = Field(description="A short id, for example 'length' or 'end-card'.")
    want: str = Field(description="One thing the finished result must have, in the user's terms. Exact words in quotes.")
    check: str = Field(description="How it will be checked at the end (measured, by eye, by ear).")


class Step(BaseModel):
    n: int
    tool: str = Field(description="The tool this step calls, by its name on this server.")
    model: str = Field(default="", description="The model the step uses, when the tool takes one.")
    seconds: float | None = None
    does: str = Field(description="What this step makes or does.")
    covers: list[str] = Field(description="The checklist ids this step makes true.")


class Review(BaseModel):
    note: str = Field(description="The note id.")
    steps: list[int] = Field(default_factory=list, description="The steps it applies to.")
    conflict: bool = Field(description="True when a step as planned goes against the note.")
    change: str = Field(default="", description="With a conflict: how the plan changes. Without: why the step is fine.")


class Result(BaseModel):
    id: str
    met: Literal["yes", "partly", "no"]
    evidence: str = Field(description="What shows it: a measurement, what is seen or heard, or the tool's result.")


# ---- the tools ---------------------------------------------------------------------------------------------------------
def register_gates(mcp: FastMCP, mode: str = MODE) -> None:
    @mcp.tool(
        name="start_request",
        description=(
            "Step 1 for every request that makes or changes media, before any other tool. Pass the user's message word for "
            "word (and later replies added after it) and the names of attached files. Returns the simple / hard comparison "
            "and the question sheet. Then call classify_request. Spends no credits."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    )
    async def start_request(original: str, attachments: list[str] | None = None) -> dict:
        attachments = attachments or []
        return {"request": sign({"kind": "request", "original": original, "attachments": attachments}),
                "comparison": COMPARISON, "questions": QUESTIONS,
                "next": "Read the comparison. Call classify_request with `simple` or `hard` and the reasons, each naming the item it matches."}

    @mcp.tool(
        name="classify_request",
        description=(
            "Step 2: say whether the request is simple or hard, by the comparison start_request returned. Simple: returns "
            "the approved token for the create tools. Hard: the next step is submit_plan. Spends no credits."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    )
    async def classify_request(request: str, verdict: Literal["simple", "hard"], reasons: list[str]) -> dict:
        data = read(request, "request")
        signals = hard_signals(data["original"], data["attachments"])
        if verdict == "simple" and signals:
            raise ToolError("This request is hard by the comparison: " + "; ".join(signals) + ". Call classify_request again with `hard`.")
        if verdict == "simple":
            return {"approved": sign({"kind": "approved", "level": "simple", "original": data["original"]}),
                    "next": "Pass `approved` as `plan_token` to the create tools."}
        return {"next": "Call submit_plan with the request token, the question sheet, the checklist and the steps.",
                "sheet": {k: QUESTIONS[k] for k in SHEET_REQUIRED},
                "must_carry": brief_items(data["original"], data["attachments"])}

    @mcp.tool(
        name="submit_plan",
        description=(
            "Step 3, hard requests: the question sheet (each answer from the user's words, your call, or ask), a checklist "
            "of everything the result must have, and the steps, each with the tool it calls and the checklist ids it makes "
            "true. Problems come back to fix; when there are none, the notes for the tools and models named come back for "
            "review_plan. Spends no credits."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    )
    async def submit_plan(request: str, sheet: dict[str, Answer], checklist: list[Line], steps: list[Step]) -> dict:
        data = read(request, "request")
        original, problems, ask = data["original"], [], []
        said = _squash(original)
        for key in SHEET_REQUIRED:
            if key not in sheet:
                problems.append(f"sheet: '{key}' is not answered ({QUESTIONS[key]})")
        for key, a in sheet.items():
            if a.source == "user" and (not a.quote or _squash(a.quote) not in said):
                problems.append(f"sheet '{key}': the quote is not the user's words. Copy them exactly, or use 'your call' or 'ask'.")
            if a.source == "ask":
                ask.append(QUESTIONS.get(key, key))
        wants = " ".join(_squash(line.want) for line in checklist)
        for item in brief_items(original, data["attachments"]):
            value = _squash(item["value"])
            found = value in wants or (item["kind"] == "file" and re.search(r"\b(attached|photo|picture|image|clip|video|logo)\b", wants))
            if not found:
                problems.append(f"checklist: nothing carries the {item['kind']} the user named: \"{item['value']}\"")
        ids = {line.id for line in checklist}
        tools = {t.name for t in await mcp.list_tools()}
        for step in steps:
            if step.tool not in tools:
                problems.append(f"step {step.n}: there is no tool '{step.tool}' on this server")
            for c in step.covers:
                if c not in ids:
                    problems.append(f"step {step.n}: covers '{c}', which is not a checklist id")
        covered = {c for step in steps for c in step.covers}
        for line in checklist:
            if line.id not in covered:
                problems.append(f"checklist '{line.id}': no step makes it true")
        if not steps:
            problems.append("the plan has no steps")
        if problems:
            return {"ok": False, "problems": problems, "next": "Fix the plan and call submit_plan again."}
        if ask:
            return {"ok": False, "ask_user": ask, "next": "Ask the user these questions (no more than 3), then call start_request "
                    "again with their message and the reply added, and continue from there."}
        names = {s.tool for s in steps} | {s.model for s in steps if s.model}
        notes = notes_for(names)
        plan = {"kind": "plan", "original": original, "checklist": [l.model_dump() for l in checklist],
                "steps": [s.model_dump() for s in steps], "notes": sorted(notes)}
        return {"ok": True, "plan": sign(plan), "notes": notes,
                "next": "Read every note. Call review_plan with one review for each note id: the steps it applies to, whether "
                        "a step goes against it, and the change or the reason it is fine."}

    @mcp.tool(
        name="review_plan",
        description=(
            "Step 4, hard requests: one review for each note submit_plan returned. Any conflict sends the plan back to "
            "submit_plan with the change made. No conflicts: returns the approved token for the create tools. Spends no credits."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    )
    async def review_plan(plan: str, reviews: list[Review]) -> dict:
        data = read(plan, "plan")
        given = {r.note for r in reviews}
        missing = [n for n in data["notes"] if n not in given]
        if missing:
            return {"ok": False, "problems": [f"note '{n}' has no review" for n in missing], "next": "Review every note and call review_plan again."}
        conflicts = [r for r in reviews if r.conflict]
        if conflicts:
            return {"ok": False, "conflicts": [r.model_dump() for r in conflicts],
                    "next": "Change the plan as the reviews say and call submit_plan again with the same request token."}
        approved = {"kind": "approved", "level": "hard", "original": data["original"], "checklist": data["checklist"],
                    "tools": sorted({s["tool"] for s in data["steps"]})}
        return {"ok": True, "approved": sign(approved),
                "next": "Pass `approved` as `plan_token` to every create tool the plan names. When the result is made, call final_check."}

    @mcp.tool(
        name="final_check",
        description=(
            "Last step, hard requests: every checklist line with met yes / partly / no and what shows it. Returns the lines "
            "not met. Fix a line with another step when you can; else tell the user which lines are not met. Spends no credits."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    )
    async def final_check(approved: str, results: list[Result]) -> dict:
        data = read(approved, "approved")
        lines = {l["id"]: l["want"] for l in data.get("checklist", [])}
        given = {r.id: r for r in results}
        missing = [i for i in lines if i not in given]
        if missing:
            return {"ok": False, "problems": [f"'{i}' ({lines[i]}) has no result" for i in missing], "next": "Check every line."}
        not_met = [{"id": r.id, "want": lines.get(r.id, ""), "met": r.met, "evidence": r.evidence} for r in results if r.met != "yes"]
        return {"ok": not not_met, "not_met": not_met,
                "next": "Deliver." if not not_met else "Fix these with another step if you can; else tell the user plainly which are not met."}

    if mode in ("warn", "refuse"):
        mcp.add_middleware(GateMiddleware(mode))


class GateMiddleware(Middleware):
    """Adds `plan_token` to the tools that spend credits. refuse: required and checked. warn: optional; without it the
    tool runs and its result says the gates were skipped."""

    def __init__(self, mode: str):
        self.mode = mode

    async def on_list_tools(self, context: MiddlewareContext[mt.ListToolsRequest], call_next: CallNext) -> Any:
        tools = await call_next(context)
        out = []
        for tool in tools:
            if tool.name in SPENDING:
                params = json.loads(json.dumps(tool.parameters))
                params.setdefault("properties", {})["plan_token"] = {
                    "type": "string", "description": "The `approved` token from classify_request (simple) or review_plan (hard)."}
                if self.mode == "refuse":
                    params["required"] = sorted(set(params.get("required", [])) | {"plan_token"})
                tool = tool.model_copy(update={"parameters": params})
            out.append(tool)
        return out

    async def on_call_tool(self, context: MiddlewareContext[mt.CallToolRequestParams], call_next: CallNext) -> ToolResult:
        name, args = context.message.name, context.message.arguments or {}
        if name not in SPENDING:
            return await call_next(context)
        token = args.pop("plan_token", None)
        context.message.arguments = args
        problem = None
        if not token:
            problem = "no plan_token: start_request and classify_request were not called"
        else:
            try:
                data = read(token, "approved")
                if data.get("level") == "hard" and name not in data.get("tools", []):
                    problem = f"{name} is not a tool in the approved plan ({', '.join(data['tools'])})"
            except ToolError as error:
                problem = str(error)
        if problem and self.mode == "refuse":
            raise ToolError(f"Refused: {problem}. Every request starts with start_request; a hard one needs an approved plan.")
        result = await call_next(context)
        if problem:
            note = TextContent(type="text", text=f"Gates skipped: {problem}. If this request is hard by the comparison, the plan was not checked.")
            result = ToolResult(content=[note, *result.content], structured_content=result.structured_content, meta=result.meta)
        return result
