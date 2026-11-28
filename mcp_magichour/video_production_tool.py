"""The multi-clip video tools: plan the clips, make each clip, join them.

plan_video_production    story in, ordered clips out. Read-only.
create_production_clip   makes one clip of the plan with its one reference. Spends credits.
create_production_sound  makes the plan's narration and music. Spends credits.
join_production_clips    joins the finished clips in story order and adds the sound and the captions.

The server keeps no state. The plan travels as `production`, a packed copy of the story that the host passes back.
"""

from __future__ import annotations

import asyncio
import re
import base64
import hashlib
import ipaddress
import json
import os
import shutil
import socket
import tempfile
import time
import uuid
import zlib
from pathlib import Path
from typing import Any, Literal

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from . import kept_files, media_tools
from .video_production import word_match, ROUTING_RULE, advise, apply_answers, held_merges, host_cuts, plan, questions, spoken_slots

FILMS_PATH = "/films"
FILMS_DIR = Path(os.getenv("MAGIC_HOUR_FILMS_DIR", Path(tempfile.gettempdir()) / "magic-hour-films"))
STILL_WAIT_SECONDS = 120
STILL_TRIES = 3   # a still whose words are misspelled is made again, at most this many stills in all

# The split rule the tool applies. Chosen by benchmark, not by the host.
# Two test films, one run each: cut at a new idea and at 15 s. A cut at every event did worse on one
# (more last-frame handoffs: the framing locks, the colour drifts).
# First keep a pictured person's shots in one call; then cut at key ideas.
SPLIT_STRATEGY = "person"
SPLIT_MAX_SECONDS = 15
SPEECH_MIN_SECONDS = 1.0   # less sound than this in a narration track: no voice was made
WORDS_MIN_MATCH = 0.8      # the share of the script's words a transcript must have, in order (in testing: nonsense was 0.04)
TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"


def _drop_local(film: Path) -> None:
    """With BLOB_READ_WRITE_TOKEN the film is served from Blob: the local copy and its sound layers only fill /tmp."""
    if os.getenv("BLOB_READ_WRITE_TOKEN"):
        film.unlink(missing_ok=True)
        shutil.rmtree(film.with_name(film.stem + ".layers"), ignore_errors=True)


async def _transcribe(audio: Path) -> str | None:
    """What a narration says, by Whisper, when the server has OPENAI_API_KEY and MAGIC_HOUR_TRANSCRIBE_CHECK=on.
    None when it cannot be checked. Off by default: the check sends the narration audio to OpenAI."""
    key = os.getenv("OPENAI_API_KEY")
    if not key or os.getenv("MAGIC_HOUR_TRANSCRIBE_CHECK", "").lower() not in ("1", "on", "true") or not media_tools.have_ffmpeg():
        return None
    wav = audio.with_name(audio.stem + "-16k.wav")
    try:
        await asyncio.to_thread(media_tools._ffmpeg, ["-i", str(audio), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            response = await client.post(TRANSCRIBE_URL, headers={"Authorization": f"Bearer {key}"},
                                         data={"model": "whisper-1", "language": "en"}, files={"file": (wav.name, wav.read_bytes(), "audio/wav")})
            response.raise_for_status()
            return response.json().get("text", "")
    except Exception:   # the check is extra: a failed check leaves the words to the ear, it does not fail the join
        return None


class Subject(BaseModel):
    id: str = Field(description="Short name, letters only. Used as @id when its picture is the reference.")
    words: str = Field(description=(
        "One person or thing and how it looks, in one sentence. These exact words go into every clip that shows it."))
    role: Literal["person", "copy", "product", "place"]
    picture: str | None = Field(default=None, description=(
        "A real photo of it (file_path from upload_media, or a URL): the video uses it as a likeness guide in every clip "
        "that shows it. Each pictured subject in a clip is its own guide. For a real, named product: the user's photo, or a "
        "free-licence photo from the web (for example Wikimedia Commons; tell the user where it came from and its licence). "
        "Never a picture made by an image model: it invents the product (in testing: a Steam Deck with no trackpads)."))


class Reference(BaseModel):
    kind: Literal["none", "picture", "last_frame", "file"] = Field(description=(
        "none; picture = a subject's picture (`subject`); last_frame = the last frame of an earlier clip (`of_clip`, "
        "default the clip before); file = a picture you made or were given (`file`: its file_path or URL)."))
    subject: str | None = None
    of_clip: int | None = None
    file: str | None = None


class Beat(BaseModel):
    seconds: int = Field(ge=1, description=(
        "Length of this beat. An action the user gives a length (it flies for 12 seconds) is a beat of that length; its "
        "start and its end (the take-off, the landing) are beats of their own (in testing: all three in one 12 s beat gave "
        "about 4 s of flight)."))
    text: str = Field(description=(
        "What is seen in this beat: one action, and the framing if it matters (wide, close). Write the action as its "
        "start, its move and its end: where each person and thing is at the start and at the end (a closed umbrella in her "
        "hand, then open over both heads; Wilbur lets go of the wing and stays on the sand). Do not quote a spoken "
        "line here. Describe no talking (talks, speaks, answers, says, explains, as she makes her "
        "point): a line a person says goes in `spoken`, and the tool adds the talking there. For a card: how the card looks. A change of state that the model must show "
        "(a paper folded into a shape, a pour that fills a glass, a stage of a build) is one beat for each stage, "
        "each with its own clip; a real product or object that must look right gets a real photo as its subject's "
        "`picture` (see `picture` on the subject)."))
    kind: Literal["scene", "card"] = Field(default="scene", description=(
        "scene: made by the video model. card: a still with words, made by the picture model. Make a card only when "
        "the user asks for a card (in testing: unasked title cards)."))
    card_motion: str | None = Field(default=None, description=(
        "On a card, only when the user asks the card to move: the move in the user's words (\"the title slides in from "
        "the left\", \"a slow zoom in\"). Not set: the card holds still."))
    place: str | None = Field(default=None, description="Short name of the place. The same name means the same place.")
    subjects: list[str] = Field(default_factory=list, description="ids of the subjects seen in this beat.")
    link: Literal["continues", "event", "moves", "new"] | None = Field(
        default=None,
        description=(
            "How this beat relates to the beat before it. continues = the same shot goes on. event = same place and "
            "subjects, an important new event. moves = a subject carries over to a new place. new = nothing carries over."
        ),
    )
    exact_words: list[str] = Field(default_factory=list, description=(
        "Words shown on screen exactly: on a card, over a held `picture`, or over a scene (a title, a slogan, a "
        "caption). Over a scene, the plan asks how they are made; code draws them exactly (route overlay)."))
    in_scene_words: list[str] = Field(default_factory=list, description=(
        "Words that are part of the picture: a label on a product, a sign, a poster. The video makes them as well as it "
        "can; letters on a moving object may change."))
    background: str | None = Field(default=None, description=(
        "On a card: its background colour as a code, for example #3B1F6B, when the user gives one for the card. The card "
        "is generated on that colour, with its exact_words spelled by the picture model; every card is generated. "
        "A colour for the whole film (a logo reveal on dark navy #0B1D3A) goes in "
        "`setting`: it does not make a card."))
    words_look: Literal["bold", "clean", "classic", "marker", "block", "playful", "script"] | None = Field(default=None, description=(
        "How this beat's exact_words look when code draws them (a title over a scene). "
        "Words are drawn exactly as you write them: write capitals only if you want capitals. "
        "bold: a heavy tall font, black outline (default). clean: a thin tall font, modern. classic: an elegant serif "
        "(luxury, food, film). marker: hand written (vlog, fun, kids). block: loud blocks (games, sport, a sale). playful: "
        "round and friendly. script: joined hand writing (a wedding, a café). Pick one that fits the film's mood."))
    words_font: Literal["Anton", "Bebas Neue", "Archivo Black", "Abril Fatface", "Permanent Marker", "Bungee", "Lilita One", "Pacifico"] | None = Field(
        default=None, description="A font for this beat's drawn words, if the user names one or you want one other than the look's.")
    picture: str | None = Field(default=None, description=(
        "On a card the user asked for: a picture to show on it (its file_path from upload_media, or a URL). The card is "
        "generated from it by the image editor, with its exact_words; the picture may change a little. "
        "Make a card only when the user asks for a card. "
        "You choose how to use a picture of the user's (a screenshot, a photo, a logo), from the user's words: shown as "
        "it is, held still (a `source` beat; in testing: a screenshot \"full and unchanged\"); lightly edited first (edit_image, "
        "then its result as the `source` or a `reference.file`); on a card the user asked for (this field); or brought to "
        "life (a subject's `picture`: the video model moves it, and it may change a little). "
        "A logo reveal, or a logo that forms or moves, is a scene beat with the logo as its subject's `picture`: the video "
        "makes the motion, and the logo may change a little. Do not follow it with this card, also when the user says the "
        "logo stays exactly as in the file: the reference keeps it close, and the plan merges such a card into the reveal. "
        "A reel or slideshow of the user's photos looks static this way: make each photo a scene beat instead, with the "
        "photo as its reference (a subject's `picture`), so it moves, and put its words in exact_words (route overlay)."))
    model: Literal["ltx-2.5", "minimax-h3"] | None = Field(default=None, description=(
        "The video model for this beat's clip, if you want to choose it. Only minimax-h3 takes a picture reference."))
    words_route: Literal["overlay", "card", "model", "still", "accept"] | None = Field(default=None, description="Set by an answer to a words question.")
    says: list[str] = Field(default_factory=list, description=(
        "A voice-over: the narrator's sentences that are heard during this beat, word for word, one sentence in each "
        "item. Do not put the voice-over in `text`."))
    clip: int | None = Field(default=None, ge=1, description=(
        "The clip this beat is in: 1, 2, 3 ... in story order. You choose the clips: beats with the same number are made "
        "in one generation call. A card is a clip of its own. Set it on every beat (or on none, and the planner groups "
        "them). Use as few clips as the story allows, with one shot in each clip. Keep one motion that goes on in one "
        "clip (a logo forming, a jump and its landing). Start a new clip when: the framing or the camera angle changes "
        "(close to wide, front to behind); the clip would hold more than three actions; the clip would be too long (15 s; "
        "30 s with a person's picture); the next action does not depend on this one (a new step). A new shot inside "
        "one clip becomes an unclear move, and a third action is merged or lost (in testing: an umbrella came from nowhere in a "
        "close-to-wide clip; a man rose into the air with the plane in a clip of seven actions)."))
    spoken: list[str] = Field(default_factory=list, description=(
        "A line a person on screen says out loud in this beat, word for word. Not the narrator (that is `says`). The "
        "beat's `text` then gives only the look and the action (\"smiles at the camera\"), with no talking verb (talks, speaks, "
        "answers, explains): the tool adds the line. A talking verb there makes the model invent speech after the line. "
        "On a `source` beat: what is said in the user's clip, if you know it; it is not generated, only captioned."))
    sounds: list[str] = Field(default_factory=list, description=(
        "Sound effects the clip itself makes in this beat, said plainly: \"a short whoosh as the lines meet\", \"a door slams\". "
        "The clip is then made with sound. Never music: music is one track for the whole film (`music`)."))
    reference: Reference | None = Field(default=None, description=(
        "On the first beat of a clip: that clip's one reference, if you want to choose it. Without it the planner chooses."))
    source: str | None = Field(default=None, description=(
        "The user's own clip, used as it is: its file_path from upload_media, a URL, or a finished video project id. "
        "Nothing is generated for this beat (0 credits); it is a clip of its own. Use it to add a voice-over (`says`), music "
        "or captions to the user's clip, or to continue the user's clip (the next clip starts from its last frame). "
        "`seconds`: the clip's length. A picture (png, jpg, webp: a screenshot, a photo) is shown unchanged, held still "
        "for `seconds`."))
    source_sound: bool = Field(default=True, description="Keep the user's clip's own sound. False makes it silent before the voice-over and music are added.")
    source_speech: bool | None = Field(default=None, description=(
        "Does someone speak in the user's clip? True: their words stay on top, the music goes under them. False: its sound "
        "is only background (wind, a crowd, traffic) and goes under the voice-over and music. Not set: the MCP treats its "
        "sound as speech, so nothing said in it is lost."))
    source_start: float | None = Field(default=None, ge=0, description=(
        "Use the user's clip from this second (default 0). With `source_end`: keep only that part, cut by code (keep the "
        "first 5 s: source_end 5; a following clip then starts from the frame at 5 s)."))
    source_end: float | None = Field(default=None, gt=0, description="Use the user's clip up to this second (default: its end).")


class NarratorVoice(BaseModel):
    gender: Literal["Female", "Male"]
    age: Literal["Young", "Middle-aged", "Older"]
    style: Literal["Conversational", "Narration", "Social Media", "Advertising", "Professional", "Education", "Entertainment", "Wellness"]


class Story(BaseModel):
    title: str
    aspect: Literal["16:9", "9:16", "1:1"]
    audio: bool = False
    setting: str = Field(description=(
        "The place, light and look of the whole film, in one or two sentences. It is copied into every clip, so it "
        "must be true of every clip: no title cards, no on-screen words, no actions, no people, no model names."))
    subjects: list[Subject] = Field(default_factory=list)
    beats: list[Beat]
    narrator: str | None = Field(default=None, description=(
        "For a voice-over: how the narrator sounds in a few words (tone, pace, accent), for example 'warm, casual, quick, "
        "American'."))
    narrator_voice: NarratorVoice | None = Field(default=None, description=(
        "For a voice-over: who the narrator is. The plan picks the preset voice that fits it best, with `narrator`."))
    voice: str | None = Field(default=None, description=(
        "A preset voice id (cv-0001 ...) only when the user names one. Leave empty: the plan picks from `narrator_voice`."))
    music: str | None = Field(default=None, description=(
        "Music under the whole film: its mood and instruments. A reel, an ad or a social post gets music unless the user "
        "asks for silence. Leave empty for no music."))
    music_first: bool = Field(default=False, description=(
        "True only when the music is the point (a music video, a dance clip): the music is never lowered under speech. "
        "Otherwise speech comes first and the music is lowered while someone speaks."))
    music_swell: bool = Field(default=False, description=(
        "True only when the user asks for the music to rise when nobody speaks. Otherwise, in a film with a voice-over or a "
        "spoken line, the music stays at one quiet level under the voice for the whole film."))
    music_from_beat: int | None = Field(default=None, ge=1, description=(
        "The beat (1 = first) the music starts on, when the user asks for it to come in later: after a sound effect, as a "
        "logo lands, on the reveal. Not set: the music runs from the start of the film."))
    music_vocals: bool = Field(default=False, description=(
        "True only when the user asks for singing in the music: say the voice and what it sings in `music`. Otherwise "
        "the music is instrumental."))
    lift_background: bool = Field(default=True, description=(
        "False only when the user wants a clip's own background sound left as it was made under a spoken line (no place "
        "sound, a quiet room, a hushed scene). Otherwise the steady background (a street, a crowd) under a spoken line is raised, so "
        "the place is still heard while the person speaks."))
    captions: bool = Field(default=False, description=(
        "Words of what is said on the picture: white letters with a black outline. Words already on screen at that time "
        "are not captioned again."))
    caption_source: Literal["script", "speech"] | None = Field(default=None, description=(
        "script (default when the story has the words): the exact `says` sentences at the voice-over's pauses, and the "
        "exact `spoken` lines when the person says them; drawn by code, 0 credits, always spelled right. Both kinds in "
        "one film is fine. speech: Magic Hour's subtitle tool transcribes the finished film; use it only when people talk "
        "and the story has none of their words (the user's own clip of a talk). It costs credits and can miss a line or "
        "misspell a name. A film with words in the story and also a user's clip with unknown talking: keep script, and "
        "before the join call add_subtitles_to_video on that clip alone (start_seconds and end_seconds: the part you use; "
        "it returns only that part), then put the new video project id in the beat's `source`, with no source_start or "
        "source_end."))
    caption_font: Literal["Archivo Black", "Anton", "Bebas Neue", "Lilita One", "Permanent Marker"] | None = Field(default=None, description=(
        "The caption font, if the user wants one. Default Archivo Black."))
    voice_sample: str | None = Field(default=None, description=(
        "For a voice-over in the user's own voice: a short recording (10-30 s) of a voice the user owns or has rights to, "
        "as its file_path or a URL. Leave it empty otherwise: the voice cloner then reads the script in the preset voice "
        "that fits `narrator_voice`."))
    voice_route: str | None = Field(default=None, description="Set by the answer to the `voice` question. Leave empty.")


# The story rules the conversation front gave are on the video side now, as rules of thumb. Only the ones no field
# text says: an ablation over 78 test chats showed the other rules repeated field texts or changed nothing.
RULES_OF_THUMB = (
    "Rules of thumb, when the user's words do not say otherwise: no story from the user: write one, with a hook in the "
    "first 3 s, then the body, then the close. An ad: hook and close aim at its audience; end on call-to-action "
    "words (no made-up price, date or web address). An explainer or a list: one point per beat, its label in "
    "`exact_words`. One spoken line per beat. Fill the length: no stretching, no pauses. A card only when the user asks for a card; other "
    "words on screen go in a scene beat's `exact_words`. Keep every concrete detail the user gave, word for word "
    "(counts, colours, names, words).")


def register_video_production(mcp: FastMCP) -> None:
    @mcp.tool(
        name="plan_video_production",
        title="Plan a multi-clip video",
        description=(
            ROUTING_RULE + " Write the story as beats (one action each) and group them into "
            "clips yourself with `clip` on every beat: beats with the same number are made in one generation call. "
            "Pass the user's own words as `request`. " + RULES_OF_THUMB + " Returns the clips to make, in order (the prompt, the model, the "
            "seconds and the one reference of each), a `checklist` of what the result must have, and `advice`: "
            "warnings about your plan, each with the benchmark run that showed the problem. The advice changes "
            "nothing; you decide, and plan again if you change the story. Make each clip with create_production_clip. "
            "A voice-over, music and captions are added to the finished film: put the narrator's sentences in `says` "
            "and set `narrator`, `music`, `captions`. A line a person on screen says goes in `spoken`. Also use it for the "
            "user's own clip, whatever its length: a voice-over, music or captions on it, or a new clip that continues it. "
            "Put the clip in a beat of its own with `source` (its file_path, URL or project id); nothing is generated for "
            "it. Read-only; "
            "spends no credits."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    )
    def plan_video_production(story: Story, request: str | None = None, answers: dict[str, str] | None = None) -> dict:
        data = story.model_dump(exclude_none=True)
        answer_problems: list[str] = []
        # A held still of the picture the scene before forms is merged by code, first, whatever the host answers.
        merged = held_merges(data)
        answers = {**merged, **{k: v for k, v in (answers or {}).items() if k not in merged}} or None
        if answers:
            if host_cuts(data) is None:   # the answers keep the clips as they are: number them first
                first = plan_story(data)
                for clip in first.get("clips", []):
                    for n in clip["beats"]:
                        data["beats"][n - 1]["clip"] = clip["n"]
            data, answer_problems = apply_answers(data, answers)
        planned = plan_story(data)
        open_questions = questions(data, planned) if planned.get("clips") else []
        planned["advice"] = advise(data, planned, request) if planned.get("clips") else []
        for key in merged:
            n = int(key.split("-")[1])
            planned["advice"].append({
                "clip": None, "note": "held_merge",
                "warning": (f"Beat {n} held the picture still right after beat {n - 1} forms it, so the plan merged it into beat "
                            f"{n - 1}: one generated clip with the picture as its reference, the words drawn over it. A held still "
                            "after a reveal reads as a pasted picture. Only an end card the user asked for stays a card "
                            "(generated): write 'end card' in that beat's text."),
                "evidence": "One clip, logo as reference, approved; held card after it rejected."})
        if answer_problems:
            planned["problems"] = planned.get("problems", []) + answer_problems
            planned["ok"] = False
        if planned["ok"] and open_questions:
            planned["questions"] = open_questions
            planned["how"] = (
                "Answer every question before any clip is made: call plan_video_production again with the same story, "
                "the same request and `answers`: {question id: option}. Each question has a recommendation; choose "
                "what fits the request. The plan above shows the clips as they are now.")
            return planned
        if answers:
            planned["answered"] = answers
        if planned["ok"]:
            planned["production"] = pack(data)
            planned["how"] = (
                "Make the rounds in order. For each clip in a round call create_production_clip(production, clip, made); "
                "the clips of one round do not wait for each other. Then wait_for_video_project for each id. "
                "`made` maps each finished clip number to its video project id. After the last round call "
                "join_production_clips(production, made) and give the user its download_url. Before you deliver, go "
                "through `checklist`: the join returns `checked` for the lines code can measure; confirm the others from "
                "the clips. Tell the user plainly which lines are not met; do not present them as done."
            )
            if {"narration", "music"} & set(planned.get("sound") or {}):
                planned["how"] += (
                    " This plan has sound: call create_production_sound(production) once (at any time), wait for the ids "
                    "it returns, and pass them to join_production_clips as `sound`."
                )
        return planned

    @mcp.tool(
        name="create_production_sound",
        title="Make the voice-over and music of a planned video",
        description=(
            "Makes the sound of a plan from plan_video_production that has a `sound` part: one narration that reads all the "
            "narrator's sentences in one voice, and one music track. The voice cloner reads the narration: in the user's "
            "voice with `voice_sample`, else in the preset voice that fits `narrator_voice`. Returns `narration` and "
            "`music` (audio project ids: wait with wait_for_audio_project; the result's `next` says which waiter). "
            "Pass both to join_production_clips as `sound`. Spends credits: about 1 for every 10 letters of narration, "
            "1 for each second of music."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
    )
    async def create_production_sound(production: str) -> dict:
        return await make_sound(unpack(production))

    @mcp.tool(
        name="create_production_clip",
        title="Make one clip of a planned video",
        description=(
            "Makes one clip of a plan from plan_video_production: the server writes the prompt, picks the model and "
            "attaches the clip's one reference (a picture, its own still, or the last frame of an earlier clip). Pass "
            "`production` from the plan, the clip number, and `made`: the video project id of each finished clip, by "
            "clip number. A clip that starts from an earlier clip needs that clip finished first. Returns the video "
            "project id; then call wait_for_video_project. Spends credits: 24 for each second at 480p."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
    )
    async def create_production_clip(production: str, clip: int, made: dict[str, str] | None = None) -> dict:
        return await make_clip(unpack(production), clip, made or {})

    @mcp.tool(
        name="join_production_clips",
        title="Join the clips of a planned video",
        description=(
            "Joins the finished clips of a plan into one video file, in story order, with hard cuts. Pass `production` "
            "from the plan and `made`: the video project id of every clip, by clip number. Returns download_url for "
            "the finished film. If the plan has sound, pass `sound`: the ids from create_production_sound "
            "({narration, music}); the join puts each narrator sentence on its beat, mixes the music under it and draws "
            "the captions. Spends no credits."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
    )
    async def join_production_clips(production: str, made: dict[str, str], sound: dict[str, str] | None = None) -> dict:
        story = unpack(production)
        if not await _account_id():   # the join downloads and renders before any other call checks the key
            raise ToolError("Magic Hour did not accept this connection's key. Reconnect the Magic Hour account and try again.")
        return await join_clips(story, made, sound or {})


def pack(story: dict) -> str:
    return base64.urlsafe_b64encode(zlib.compress(json.dumps(story, separators=(",", ":")).encode())).decode()


PRODUCTION_MAX_BYTES = 2 * 1024 * 1024   # the unpacked plan; a zip bomb in `production` would fill the memory


def unpack(production: str) -> dict:
    try:
        inflate = zlib.decompressobj()
        raw = inflate.decompress(base64.urlsafe_b64decode(production.encode()), PRODUCTION_MAX_BYTES)
        if inflate.unconsumed_tail or not inflate.eof:
            raise ValueError("too large or cut short")
        story = json.loads(raw)
        if not isinstance(story, dict):
            raise ValueError("not a plan")
        return story
    except Exception:
        raise ToolError("`production` is not valid. Pass the value from plan_video_production unchanged.") from None


def plan_story(story: dict) -> dict:
    """The host's clips when it grouped the beats; else the split rule's."""
    cuts = host_cuts(story)
    if cuts is not None:
        return plan(story, "manual", SPLIT_MAX_SECONDS, cuts=cuts)
    return plan(story, SPLIT_STRATEGY, SPLIT_MAX_SECONDS)


def _planned(story: dict) -> dict:
    planned = plan_story(story)
    if not planned["ok"]:
        raise ToolError("The plan has problems: " + "; ".join(planned["problems"]))
    return planned


def _need_ffmpeg() -> None:
    if not media_tools.have_ffmpeg():
        raise ToolError("This server has no ffmpeg, so it cannot take a last frame or join clips.")


async def _api(method: str, path: str, body: dict | None = None) -> dict:
    from . import openapi_server as server   # at call time: the server module imports this one

    async with server.build_api_client() as client:
        response = await client.request(method, path, json=body, headers={"Authorization": server.current_authorization_header()})
    if response.status_code >= 400:
        raise ToolError(f"Magic Hour refused {path} (HTTP {response.status_code}): {response.text[:300]}")
    return response.json()


# Wikimedia refuses the default httpx User-Agent with 403 (in testing: two films got no real photo).
CUT_OVERLAP = 0.08   # seconds of sound overlap at each cut
USER_AGENT = "magic-hour-mcp/1.0 (https://github.com/magichourhq/magic-hour-mcp)"


DOWNLOAD_MAX_BYTES = 300 * 1024 * 1024
DOWNLOAD_SECONDS = 300


def _private_urls_allowed() -> bool:
    """A local test server (for example fixtures on 127.0.0.1) needs this switch. A hosted server leaves it off."""
    return os.getenv("MAGIC_HOUR_ALLOW_PRIVATE_URLS", "").strip().lower() in {"1", "on", "true", "yes"}


class _PublicHostsOnly(httpx.AsyncBaseTransport):
    """Connects only to public addresses. The host is resolved once and checked, and the request goes to that address,
    so a second DNS answer cannot point it inside the network. Each redirect comes back through here."""

    def __init__(self) -> None:
        self._inner = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.scheme not in ("http", "https"):
            raise ToolError(f"{url} is not an http or https link.")
        if not _private_urls_allowed():
            port = url.port or (443 if url.scheme == "https" else 80)
            try:
                found = await asyncio.get_running_loop().getaddrinfo(url.host, port, type=socket.SOCK_STREAM)
            except socket.gaierror:
                raise httpx.ConnectError(f"{url.host} has no address", request=request) from None
            addresses = []
            for *_, address in found:
                ip = ipaddress.ip_address(address[0].split("%")[0])
                ip = getattr(ip, "ipv4_mapped", None) or ip
                if not ip.is_global:
                    raise ToolError(f"{url.host} is a private or local address. Give a public link, or upload the file with upload_media.")
                addresses.append(ip)
            pinned = next((ip for ip in addresses if ip.version == 4), addresses[0])
            request.url = url.copy_with(host=str(pinned))
            request.extensions = {**request.extensions, "sni_hostname": url.host}   # the certificate is still checked for the name
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def _refuse_text(file: Path, source: str) -> None:
    """Refuse a playlist (HLS, ffconcat, DASH): ffmpeg follows its entries to files on this server, other accounts'
    kept copies too. Refuse a web page: it is an error page, not the media."""
    with file.open("rb") as f:
        head = f.read(1024).lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if head.startswith((b"#extm3u", b"#ext-x-", b"#extinf", b"ffconcat", b"[playlist]", b"<!doctype html", b"<html")) or b"<mpd" in head:
        raise ToolError(f"{source} is a playlist or a web page, not a picture, video or sound file.")


async def _download(url: str, out: Path, max_bytes: int = DOWNLOAD_MAX_BYTES) -> Path:
    """Fetch a media file from a public link, streamed to `out`, at most `max_bytes` and DOWNLOAD_SECONDS.
    A link to a film on this server is read from this server's folder."""
    base = os.getenv("MAGIC_HOUR_MCP_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/") + FILMS_PATH + "/"
    if url.startswith(base):
        film = film_file(url[len(base):].split("?")[0])
        if not film:
            raise ToolError(f"{url} is not on this server any more. Make it again, or pass the clips it came from.")
        await asyncio.to_thread(shutil.copyfile, film, out)
        return out

    async def fetch() -> None:
        async with httpx.AsyncClient(transport=_PublicHostsOnly(), timeout=httpx.Timeout(120.0), follow_redirects=True,
                                     max_redirects=5, headers={"User-Agent": USER_AGENT}) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                size = response.headers.get("content-length", "")
                if size.isdigit() and int(size) > max_bytes:
                    raise ToolError(f"{url} is {int(size) // 2**20} MB; this server takes at most {max_bytes // 2**20} MB.")
                got = 0
                with out.open("wb") as f:
                    async for chunk in response.aiter_bytes():
                        got += len(chunk)
                        if got > max_bytes:
                            raise ToolError(f"{url} is more than {max_bytes // 2**20} MB; this server takes at most that.")
                        f.write(chunk)

    try:
        await asyncio.wait_for(fetch(), DOWNLOAD_SECONDS)
    except asyncio.TimeoutError:
        raise ToolError(f"The download of {url} took more than {DOWNLOAD_SECONDS} s.") from None
    _refuse_text(out, url)
    return out


def _with_sources(planned: dict, made: dict[str, str]) -> dict[str, str]:
    """`made`, plus the user's own clips: the plan knows where they are, the host does not pass them."""
    return {**{str(c["n"]): c["steps"][0]["source"] for c in planned["clips"] if c.get("made_by") == "user"}, **made}


_ACCOUNTS: dict[str, tuple[str, float]] = {}   # sha256 of the caller's Authorization header -> (account id, when read)
ACCOUNT_SECONDS = 300


async def _account_id() -> str:
    """The id of the account that owns the caller's key (GET /v1/account). It is the <account> in that account's
    file_paths (api-assets/<account>/...)."""
    from . import openapi_server as server

    caller = hashlib.sha256(server.current_authorization_header().encode()).hexdigest()
    hit = _ACCOUNTS.get(caller)
    if hit and time.monotonic() - hit[1] < ACCOUNT_SECONDS:
        return hit[0]
    account = str((await _api("GET", "/v1/account")).get("id") or "")
    if len(_ACCOUNTS) > 10_000:
        _ACCOUNTS.clear()
    _ACCOUNTS[caller] = (account, time.monotonic())
    return account


async def _read_kept(file_path: str, out: Path) -> Path:
    """This server's copy of an upload, only for the account that uploaded it."""
    try:
        file = await asyncio.to_thread(kept_files.read, file_path, out, await _account_id())
        _refuse_text(file, file_path)
        return file
    except kept_files.NotYours:
        raise ToolError(kept_files.not_yours_message(file_path)) from None
    except kept_files.NotKept:
        raise ToolError(kept_files.not_kept_message(file_path)) from None


async def _finished_video(made: dict[str, str], n: int, folder: Path) -> Path:
    """The file of a finished clip, or of the user's own clip (a URL or a file_path this server kept).
    A clip that is not finished is an error for the host to act on."""
    if str(n) not in made:
        raise ToolError(f"clip {n} is not in `made`. Make it and wait for it first.")
    if made[str(n)].startswith(("http://", "https://")):
        return await _download(made[str(n)], folder / f"clip-{n}.mp4")
    if kept_files.is_file_path(made[str(n)]):
        return await _read_kept(made[str(n)], folder / f"clip-{n}.mp4")
    project = await _api("GET", f"/v1/video-projects/{made[str(n)]}")
    if project.get("status") != "complete" or not project.get("downloads"):
        raise ToolError(f"clip {n} (project {made[str(n)]}) is {project.get('status')}. Wait for it with wait_for_video_project.")
    return await _download(project["downloads"][0]["url"], folder / f"clip-{n}.mp4")


async def _clip_file(planned: dict, made: dict[str, str], n: int, folder: Path) -> Path:
    """A finished clip's file; the user's own clip cut to its source_start and source_end."""
    file = await _finished_video(made, n, folder)
    clip = next((c for c in planned["clips"] if c["n"] == n), {})
    step = clip.get("steps", [{}])[0] if clip.get("made_by") == "user" else {}
    if clip.get("made_by") == "user":   # the user's picture is shown unchanged, held for its seconds
        picture = await asyncio.to_thread(media_tools.picture_copy, file)
        if picture:
            return await asyncio.to_thread(media_tools.still_video, picture, float(clip["seconds"]), folder / f"clip-{n}-held.mp4")
    if step.get("start") or step.get("end"):
        part = folder / f"clip-{n}-part.mp4"
        try:
            return await asyncio.to_thread(media_tools.trim, file, part, float(step.get("start") or 0), step.get("end"))
        except ValueError as error:
            raise ToolError(f"clip {n}: {error}") from None
    return file


async def _upload(file: Path, kind: str = "image") -> str:
    """Upload to Magic Hour and keep a copy, so this server can read the file again."""
    data = await asyncio.to_thread(file.read_bytes)
    item = (await _api("POST", "/v1/files/upload-urls", {"items": [{"type": kind, "extension": file.suffix.lstrip(".").lower()}]}))["items"][0]
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        (await client.put(item["upload_url"], content=data)).raise_for_status()
    await asyncio.to_thread(kept_files.keep_if_possible, item["file_path"], data)
    return item["file_path"]


async def make_clip(story: dict, n: int, made: dict[str, str]) -> dict:
    planned = _planned(story)
    clip = next((c for c in planned["clips"] if c["n"] == n), None)
    if clip is None:
        raise ToolError(f"The plan has clips 1 to {len(planned['clips'])}; there is no clip {n}.")
    if clip.get("made_by") == "user":
        return {"clip": n, "id": clip["steps"][0]["source"], "credits_charged": 0, "made_by": "user",
                "next": "Nothing to make or wait for: the user's own clip is used as it is. The join takes it from the plan."}
    made = _with_sources(planned, made)
    reference, video, credits = clip["reference"], clip["steps"][-1], 0
    name = f"{story.get('title', 'Video')} · clip {n}"
    body: dict[str, Any] = {"name": name, "end_seconds": video["seconds"], "model": video["model"], "resolution": video["resolution"],
                            "audio": video["audio"], "style": {"prompt": video["prompt"]}}
    if reference["kind"] == "last_frame":
        _need_ffmpeg()
        with tempfile.TemporaryDirectory() as folder:
            source = await _clip_file(planned, made, reference["of_clip"], Path(folder))
            frame = await asyncio.to_thread(media_tools.last_frame, source, Path(folder) / "start.jpg")
            body["assets"] = {"image_file_path": await _upload(frame)}
    elif reference["kind"] in ("still", "scene_still"):
        still = clip["steps"][0]
        wanted = reference.get("exact_words") or []
        best, checks = None, []
        # In testing, a card read "3 tips for better sleen". The still's words are read before the video is made; a
        # misspelled still is made again, up to STILL_TRIES, and the best one is used.
        for _ in range(STILL_TRIES if wanted else 1):
            request = {"name": f"{name} · still", "image_count": 1, "model": still["model"], "aspect_ratio": story["aspect"],
                       "resolution": "640px", "style": {"prompt": still["prompt"]}}
            if still["tool"] == "edit_image":   # a card made from the user's picture
                request["assets"] = {"image_file_paths": [still["picture"]]}
            try:
                created = await _api("POST", "/v1/ai-image-editor" if still["tool"] == "edit_image" else "/v1/ai-image-generator", request)
                credits += int(created.get("credits_charged", 0))
                for _ in range(STILL_WAIT_SECONDS // 3):
                    project = await _api("GET", f"/v1/image-projects/{created['id']}")
                    if project.get("status") in ("complete", "error", "canceled"):
                        break
                    await asyncio.sleep(3)
                if project.get("status") != "complete" or not project.get("downloads"):
                    raise ToolError(f"The still for clip {n} did not finish (image project {created['id']}: {project.get('status')}).")
            except ToolError:
                if best is None:
                    raise
                break   # a new try failed: the paid still made before it is used
            url = project["downloads"][0]["url"]
            check = None
            if wanted and media_tools.have_ffmpeg():
                try:   # the check is extra: a failed read leaves the words to the eye, it does not fail the clip
                    with tempfile.TemporaryDirectory() as folder:
                        still_file = await _download(url, Path(folder) / "still")
                        check = await asyncio.to_thread(media_tools.check_words, still_file, wanted)
                except Exception:
                    check = None
            if check is None:   # no reader on this server: the words are checked by eye
                best = (0, url)
                break
            checks.append(check)
            found = int(check["found"].split()[0])
            if best is None or found > best[0]:
                best = (found, url)
            if check["verdict"] != "misspelled":   # right, or unreadable: making it again would not tell us more
                break
        body["assets"] = {"image_file_path": best[1]}
    else:
        body["aspect_ratio"] = story["aspect"]
        if reference["kind"] in ("picture", "file"):
            pictures = reference.get("pictures") or [{"subject": reference["subject"], "file": reference["file"]}]
            body["references"] = [{"name": p["subject"], "file_path": p["file"]} for p in pictures]
    try:
        created = await _api("POST", "/v1/image-to-video" if "assets" in body else "/v1/text-to-video", body)
    except ToolError as error:
        if not credits:
            raise
        raise ToolError(f"{error} The still for clip {n} is made and paid ({credits} credits): "
                        f"{body['assets']['image_file_path']}. Do not make it again. To make the clip from it, call "
                        f"create_video_from_image with that image_file_path, end_seconds {video['seconds']}, model "
                        f"{video['model']} and the clip's prompt, then pass the video project id in `made`.") from None
    spelling = {"still_words": checks} if reference["kind"] in ("still", "scene_still") and checks else {}
    return {"clip": n, "id": created["id"], "credits_charged": credits + int(created.get("credits_charged", 0)), **spelling,
            "seconds": video["seconds"], "model": video["model"], "reference": reference["kind"],
            "next": ("wait_for_video_project with this id. When every clip is made, call join_production_clips, also for a plan "
                     "of one clip: the join applies the plan's sound (it mutes a clip made without sound) and words. A clip's "
                     "own file is not the finished video; do not give it to the user.")}


async def make_sound(story: dict) -> dict:
    sound = _planned(story).get("sound")
    if not sound:
        raise ToolError("This plan has no sound. Put the narrator's sentences in `says`, or set `music`, and plan again.")
    out: dict[str, Any] = {"credits_charged": 0}
    if "narration" in sound and sound["narration"].get("tool") == "voice_cloner":
        n = sound["narration"]
        created = await _api("POST", "/v1/ai-voice-cloner", {
            "name": f"{story.get('title', 'Video')} · narration", "assets": {"audio_file_path": n["sample"]},
            "style": {"prompt": n["script"]}})
        out["narration"] = created["id"]
        out["credits_charged"] += int(created.get("credits_charged", 0))
    elif "narration" in sound:
        n = sound["narration"]
        created = await _api("POST", "/v1/text-to-video", {
            "name": f"{story.get('title', 'Video')} · narration", "end_seconds": n["seconds"], "aspect_ratio": story["aspect"],
            "model": n["model"], "resolution": n["resolution"], "audio": True, "style": {"prompt": n["prompt"]}})
        out["narration"] = created["id"]
        out["credits_charged"] += int(created.get("credits_charged", 0))
    cloned = sound.get("narration", {}).get("tool") == "voice_cloner"
    waits = f"wait_for_{'audio' if cloned else 'video'}_project with `narration`"
    if "music" in sound:
        m = sound["music"]
        try:
            created = await _api("POST", "/v1/ai-music-generator", {
                "name": f"{story.get('title', 'Video')} · music", "duration": m["seconds"], "style": {"prompt": m["prompt"], "instrumental": m.get("instrumental", True)}})
        except ToolError as error:
            if "narration" not in out:
                raise
            # The narration is paid for: return its id, so that a retry does not pay for it again.
            out["music_error"] = str(error)
            out["next"] = (f"The music call failed; the narration is made. {waits}. For the music, call generate_music "
                           f"({m['seconds']} s, prompt {m['prompt']!r}) and wait_for_audio_project. Then pass {{narration, music}} to "
                           "join_production_clips as `sound`. Do not call create_production_sound again: it makes the narration again.")
            return out
        out["music"] = created["id"]
        out["credits_charged"] += int(created.get("credits_charged", 0))
    waits = [waits] if "narration" in out else []
    waits += ["wait_for_audio_project with `music`"] if "music" in out else []
    ids = ", ".join(k for k in ("narration", "music") if k in out)
    out["next"] = f"{' and '.join(waits)}; then pass {{{ids}}} to join_production_clips as `sound`"
    return out


async def _finished(kind: str, project_id: str, out: Path) -> Path:
    project = await _api("GET", f"/v1/{kind}-projects/{project_id}")
    if project.get("status") != "complete" or not project.get("downloads"):
        raise ToolError(f"The {kind} project {project_id} is {project.get('status')}. Wait for it first.")
    return await _download(project["downloads"][0]["url"], out)


def _captions(timed: list[dict], overlays: list[dict] | None, font: str | None) -> tuple[list[dict], int]:
    """The caption lines to draw, one at a time, in the caption look. A line whose words are all on screen at that
    time already (an overlay or a card) is left out (the double caption). Returns the lines and how many were left out."""
    timed = sorted(({k: v for k, v in line.items() if k != "timed"} for line in timed), key=lambda line: line["start"])
    for line, after in zip(timed, timed[1:]):   # one caption at a time
        line["end"] = min(line["end"], after["start"])
    plain = lambda text: re.sub(r"[^a-z0-9]", "", text.lower())
    shown = [line for line in timed if not any(plain(line["text"]) and plain(line["text"]) in plain(o["text"]) and o["start"] < line["end"]
                                               and line["start"] < o["end"] for o in overlays or [])]
    return [dict(line, look="caption", **({"font": font} if font else {})) for line in shown], len(timed) - len(shown)


def _add_sound(sound_plan: dict, film: Path, narration: Path | None, music: Path | None, out: Path,
               clip_starts: dict[int, float] | None = None, spoken: list[tuple[float, float]] | None = None,
               music_first: bool = False, music_swell: bool = False, overlays: list[dict] | None = None, music_at: float = 0.0,
               said: list[dict] | None = None) -> dict:
    """The file steps of the sound, in one place (run in a thread). Returns what was done.
    clip_starts: where each clip really starts in the joined film (a clip is often a little longer than planned).
    overlays: the words already on screen; a caption whose words are all among them at that time is left out (the double caption).
    said: the captions of the lines people on screen say (time_spoken), drawn with the voice-over's."""
    parts, done = [], {"narration": False, "music": bool(music), "captions": False}
    if narration:
        wanted = sound_plan["narration"]["parts"]
        spans = media_tools.speech_spans(narration)
        fine = media_tools.speech_spans(narration, noise_db=-30, min_gap=0.04)
        found = media_tools.align_parts(spans, [" ".join(p["lines"]) for p in wanted], fine)
        parts = [{"start": start, "end": end, "lines": p["lines"],
                  "at": clip_starts[p["clip"]] + p["inside"] + 0.5 if clip_starts and p["clip"] in clip_starts else p["at"]}
                 for p, (start, end) in zip(wanted, found)]
        done["narration"] = bool(parts)
        fit = media_tools.fit_parts(parts, media_tools.probe_seconds(film))
        parts = fit["parts"]
        if fit["moved"]:   # the narration was longer than its beats; no word is cut now
            done["voice_fit"] = {"tempo": fit["tempo"], "held_end_seconds": fit["hold"]}
        if fit["hold"] > 0:
            film = media_tools.hold_end(film, fit["hold"], out.with_name(out.stem + "-held.mp4"))
    mixed = out.with_name(out.stem + "-mixed.mp4")
    layers = out.with_name(out.stem + ".layers")   # each layer kept beside the film, for a later mix
    mix = media_tools.add_sound(film, mixed, narration if parts else None, parts, music, spoken, music_first, layers, music_swell, music_at)
    if music and music_at > 0:
        done["music_starts_at"] = round(music_at, 2)
    done["loudness_lufs"] = mix["lufs"]
    if sound_plan.get("captions") and (parts or said) and media_tools.have_captions():
        timed = []
        for part in parts:   # each sentence gets a share of its part, by its length
            total, at = sum(media_tools.letters(line) for line in part["lines"]) or 1, part["at"]
            for line in part["lines"]:
                length = (part["end"] - part["start"]) / part.get("tempo", 1.0) * media_tools.letters(line) / total
                timed.append({"text": line, "start": at, "end": at + length})
                at += length
        timed, left_out = _captions(timed + list(said or []), overlays, sound_plan.get("caption_font"))
        if left_out:
            done["captions_left_out"] = left_out
        media_tools.draw_captions(mixed, timed, out) if timed else mixed.replace(out)
        mixed.unlink(missing_ok=True)
        done["captions"] = True
    else:
        mixed.replace(out)
    if (done.get("voice_fit") or {}).get("held_end_seconds"):
        film.unlink(missing_ok=True)   # the copy with the held frame; the mixed film has replaced it
    return done


async def join_clips(story: dict, made: dict[str, str], sound: dict[str, str] | None = None) -> dict:
    _need_ffmpeg()
    planned = _planned(story)
    subtitles = (planned.get("sound") or {}).get("subtitles")
    captions_on = bool((planned.get("sound") or {}).get("captions"))
    sound_plan = {k: v for k, v in (planned.get("sound") or {}).items() if k != "subtitles"}
    if not ("narration" in sound_plan or "music" in sound_plan):
        sound_plan = {}
    sound = sound or {}
    missing = [k for k in ("narration", "music") if k in sound_plan and k not in sound]
    if missing:
        raise ToolError(f"This plan has sound. Call create_production_sound, wait for its ids, and pass `sound` with: {', '.join(missing)}.")
    FILMS_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.mp4"
    result: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as folder:
        by_n = {c["n"]: c for c in planned["clips"]}
        made = _with_sources(planned, made)
        files = []
        for n in planned["join"]:   # every card is a generated clip: no card is drawn at the join
            files.append(await _clip_file(planned, made, n, Path(folder)))
        probes = [await asyncio.to_thread(media_tools.probe, file) for file in files]
        # A generated clip that ran long (in testing: a 28 s jump) and whose last frame starts a later clip keeps that
        # last frame. Its extra time is cut from the start, or, when its start is a joint too, it is played a little faster.
        starts_from = {c["reference"].get("of_clip") for c in planned["clips"] if c.get("reference", {}).get("kind") == "last_frame"}
        for i, n in enumerate(planned["join"]):
            clip = by_n[n]
            if clip.get("made_by") == "api" and n in starts_from and probes[i]["seconds"] > float(clip["seconds"]) + 0.05:
                how = "speed" if clip["reference"].get("kind") == "last_frame" else "head"
                files[i] = await asyncio.to_thread(media_tools.fit_length, files[i], float(clip["seconds"]), how, Path(folder) / f"fit-{n}.mp4")
                probes[i] = await asyncio.to_thread(media_tools.probe, files[i])
                result.setdefault("kept_last_frame", []).append({"clip": n, "how": how})
        # A generated clip is cut to its planned length (minimax-h3 returns at least about 2 s); the user's clip is kept
        # whole, or as cut by source_start and source_end above.
        cut = [None if by_n[n].get("made_by") == "user" else float(by_n[n]["seconds"]) for n in planned["join"]]
        lengths = {n: min(p["seconds"], c) if c else p["seconds"] for n, p, c in zip(planned["join"], probes, cut)}
        size = media_tools.size_for(story["aspect"], probes[0])
        # a line that runs to its clip's end stays on to the next cut (in testing: the words left a moment before the end)
        overlays = [dict(line, start=line["start"] + at, end=(lengths[n] + 0.1 if line["end"] >= lengths[n] - 0.05 else line["end"]) + at)
                    for n, at in zip(planned["join"], [sum(lengths[m] for m in planned["join"][:k]) for k in range(len(files))])
                    for line in by_n[n].get("overlay", [])]
        starts, at = {}, 0.0
        for n in planned["join"]:
            starts[n] = at
            at += lengths[n]
        spoken, talking = [], []   # where a person on screen speaks (the beats with a spoken line), and the clips that hold them
        for n in planned["join"]:
            inside = 0.0
            for b in by_n[n].get("beats", []):
                beat = story["beats"][b - 1]
                if beat.get("spoken"):
                    spoken.append((starts[n] + inside, starts[n] + min(inside + beat["seconds"], lengths[n])))
                    if by_n[n].get("made_by") != "user" and (starts[n], starts[n] + lengths[n]) not in talking:
                        talking.append((starts[n], starts[n] + lengths[n]))
                inside += beat["seconds"]
        if story.get("lift_background") is False:   # the user wants the background as it was made
            talking = []
        joined = Path(folder) / "joined.mp4" if (sound_plan or overlays or talking) else FILMS_DIR / name
        # a clip keeps its own sound when it was made with sound (the story's audio, or a spoken line in it)
        # the video step's `audio`, not the first step's: a clip that starts from a still has
        # generate_image first, so its spoken line was muted when the story's audio was off.
        keep = [by_n[n]["steps"][0]["sound"] if by_n[n].get("made_by") == "user"
                else bool(next((x["audio"] for x in reversed(by_n[n]["steps"]) if "audio" in x), story.get("audio")))
                for n in planned["join"]]
        # No sound jump at a cut. The sound overlaps the cut, and a background much louder
        # than the other clips' is turned down.
        await asyncio.to_thread(media_tools.join, files, joined, keep, cut, size, CUT_OVERLAP, True)
        said: list[dict] = []
        if captions_on:   # two caption sources: the spoken lines, timed from the clips' own sound
            lines = []
            for n in planned["join"]:
                window = (starts[n], starts[n] + lengths[n])
                beats = [dict(story["beats"][b - 1], n=b) for b in by_n[n].get("beats", [])]
                if by_n[n].get("made_by") == "user":   # the user's clip: no slot; its lines go where speech is heard in it
                    lines += [{"text": s["text"], "start": None, "end": None, "clip": window} for s in spoken_slots(beats)]
                    continue
                lines += [{"text": s["text"], "start": window[0] + min(s["start"], lengths[n]), "end": window[0] + min(s["end"], lengths[n]),
                           "clip": window} for s in spoken_slots(beats)]
            said = await asyncio.to_thread(media_tools.time_spoken, joined, lines)
            if said:   # "heard": timed from the speech near the line's slot; "slot": nothing was heard there, the slot's times
                result["spoken_captions"] = said
            # the user's clip with talking and no words in the story: code cannot caption it
            unknown = [n for n, k in zip(planned["join"], keep) if k and by_n[n].get("made_by") == "user"
                       and by_n[n]["steps"][0].get("speech") is not False
                       and not any(story["beats"][b - 1].get("spoken") for b in by_n[n].get("beats", []))]
            if unknown:
                result["captions_missing"] = (f"Clip {', '.join(map(str, unknown))}: the user's clip may have talking, and the story "
                                              "does not have its words, so it has no captions. Put the words in that beat's `spoken` "
                                              "and join again, or see `caption_source`.")
        if overlays:   # exact words over a scene, drawn by code (words_route overlay) in a title look, not a caption's
            worded = Path(folder) / "worded.mp4" if (sound_plan or talking) else FILMS_DIR / name
            await asyncio.to_thread(media_tools.draw_captions, joined, [dict(o, place=o.get("place") or "top", large=True,
                                                                            look=o.get("look") or media_tools.TITLE_LOOK) for o in overlays], worded)
            joined = worded
            result["words_drawn"] = len(overlays)
        if talking:   # the street under a spoken line is lifted
            lifted = Path(folder) / "lifted.mp4" if sound_plan else FILMS_DIR / name
            bed = await asyncio.to_thread(media_tools.lift_bed, joined, spoken, talking, lifted)
            if bed:
                joined = lifted
                result["background_lifted"] = bed
            elif not sound_plan:
                await asyncio.to_thread(shutil.copyfile, joined, FILMS_DIR / name)
        if said and not sound_plan and media_tools.have_captions():   # no voice-over and no music: the captions alone
            timed, left_out = _captions(said, overlays, (planned.get("sound") or {}).get("caption_font"))
            captioned = Path(folder) / "captioned.mp4"
            if timed:
                await asyncio.to_thread(media_tools.draw_captions, FILMS_DIR / name, timed, captioned)
                await asyncio.to_thread(shutil.move, captioned, FILMS_DIR / name)
            result["sound"] = {"captions": True, **({"captions_left_out": left_out} if left_out else {})}
        if sound_plan:
            cloned = (sound_plan.get("narration") or {}).get("tool") == "voice_cloner"
            narration = (await _finished("audio" if cloned else "video", sound["narration"], Path(folder) / ("narration" if cloned else "narration.mp4"))
                         if "narration" in sound_plan else None)
            if narration is not None:
                spans = await asyncio.to_thread(media_tools.speech_spans, narration)
                speech_seconds = round(sum(end - start for start, end in spans), 1)
                heard_words = await _transcribe(narration)
            music = await _finished("audio", sound["music"], Path(folder) / "music") if "music" in sound_plan else None
            # words on a card (drawn, held or generated): its caption is left out too (in testing: the end card's line twice)
            on_cards = [{"text": " ".join(w for b in by_n[n].get("beats", []) for w in story["beats"][b - 1].get("exact_words") or []),
                         "start": starts[n], "end": starts[n] + lengths[n]} for n in planned["join"] if by_n[n].get("kind") == "card"]
            # the user's clip with its sound kept: someone may speak in it. Unless the host says it is only background
            # (source_speech false), its sound is speech: never lowered, the music sits under it.
            music_at = 0.0
            if story.get("music_from_beat"):   # for example a whoosh first, then the music
                for n in planned["join"]:
                    inside = 0.0
                    for b in by_n[n].get("beats", []):
                        if b == story["music_from_beat"]:
                            music_at = starts[n] + min(inside, lengths[n])
                        inside += story["beats"][b - 1]["seconds"]
            kept_as_speech = [n for n, k in zip(planned["join"], keep) if k and by_n[n].get("made_by") == "user"
                              and by_n[n]["steps"][0].get("speech") is not False]
            spoken += [(starts[n], starts[n] + lengths[n]) for n in kept_as_speech]
            result["sound"] = await asyncio.to_thread(_add_sound, sound_plan, joined, narration, music, FILMS_DIR / name, starts,
                                                      spoken, bool(story.get("music_first")), bool(story.get("music_swell")),
                                                      overlays + on_cards, music_at, said)
            if kept_as_speech:
                result["sound"]["user_clip_as_speech"] = kept_as_speech
            if narration is not None:
                result["sound"]["speech_seconds"] = speech_seconds
                if heard_words is not None:
                    script = " ".join(line for part in sound_plan["narration"]["parts"] for line in part["lines"])
                    result["sound"]["heard"] = heard_words
                    result["sound"]["words_match"] = word_match(heard_words, script)
    film = FILMS_DIR / name
    measured = await asyncio.to_thread(media_tools.probe, film)
    loud = await asyncio.to_thread(media_tools.loudness_db, film)
    base = os.getenv("MAGIC_HOUR_MCP_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")
    url = await asyncio.to_thread(media_tools.store, film, name, FILMS_DIR, base)
    _drop_local(film)
    checked, unchecked = _check_film(planned, story, measured, loud, lengths, result.get("sound") or {})
    return {"download_url": url, "seconds": round(measured["seconds"], 1), "width": measured["width"], "height": measured["height"],
            "clips": len(files), **result, "checked": checked, "unchecked": unchecked,
            "not_met": [c["want"] for c in checked if c["result"] != "yes"],
            **({"next": f"Call add_subtitles_to_video on download_url with style {json.dumps(subtitles['style'])} (it transcribes "
                        "the speech and costs credits), then wait_for_video_project. Check that the first line is there."} if subtitles else {})}


def _check_film(planned: dict, story: dict, measured: dict, loud: float | None, lengths: dict[int, float], done: dict) -> tuple[list[dict], list[str]]:
    """The checklist lines code can measure, against the finished film. The others are returned for the host to confirm."""
    checked, unchecked = [], []
    ratio = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0}[story["aspect"]]
    for line in planned["checklist"]:
        want, kind = line["want"], line["id"].split("-")[0]
        if kind == "file":
            ok = abs(measured["seconds"] - planned["seconds"]) <= 1 and abs(measured["width"] / measured["height"] - ratio) < 0.05 * ratio
            checked.append({"want": want, "got": f"{measured['seconds']:.1f} s, {measured['width']}x{measured['height']}", "result": "yes" if ok else "no"})
        elif kind == "card":
            n = int(line["id"].split("-")[1])
            clip = next(c for c in planned["clips"] if c["n"] == n)
            ok = abs(lengths.get(n, 0) - clip["seconds"]) <= 0.5
            checked.append({"want": want, "got": f"the card clip is {lengths.get(n, 0):.1f} s; the words are checked by eye", "result": "yes" if ok else "no"})
        elif kind == "voice":
            heard = done.get("speech_seconds")
            if not done.get("narration"):
                checked.append({"want": want, "got": "no narration was placed", "result": "no"})
            elif heard is not None and heard < SPEECH_MIN_SECONDS:
                # In testing, a narration call came back silent and was counted as placed. Speech is measured now.
                checked.append({"want": want, "got": f"the narration track has {heard:.1f} s of sound in it: no voice was made; make the sound again", "result": "no"})
            elif done.get("words_match") is not None and done["words_match"] < WORDS_MIN_MATCH:
                # In testing, a narration said made-up words and passed the speech check. The words are compared now.
                checked.append({"want": want, "got": f"the narration says other words ({round(done['words_match'] * 100)}% of the script): "
                                f"\"{done['heard'][:200]}\"; make the sound again or tell the user", "result": "no"})
            elif done.get("words_match") is not None:
                checked.append({"want": want, "got": f"narration placed on its beats; a transcript has {round(done['words_match'] * 100)}% of the script's words, in order", "result": "yes"})
            else:
                checked.append({"want": want, "got": f"narration placed on its beats, {heard or 0:.1f} s of speech; the words are checked by ear", "result": "yes"})
            if fit := done.get("voice_fit"):   # the narration was longer than its beats
                checked[-1]["got"] += (f"; the voice-over was longer than its beats: moved earlier"
                                       + (f", read {round((fit['tempo'] - 1) * 100)}% faster" if fit["tempo"] > 1 else "")
                                       + (f", the last frame held {fit['held_end_seconds']} s longer; tell the user" if fit["held_end_seconds"] else ""))
        elif kind == "music":
            checked.append({"want": want, "got": "music mixed under the film" if done.get("music") else "no music", "result": "yes" if done.get("music") else "no"})
        elif kind == "captions":
            checked.append({"want": want, "got": "captions drawn" if done.get("captions") else "no captions (the server has no drawing library)", "result": "yes" if done.get("captions") else "no"})
        elif kind == "scene":
            ok = loud is not None and loud > -50
            checked.append({"want": want, "got": f"peak level {loud:.0f} dB" if loud is not None else "no sound track", "result": "yes" if ok else "no"})
        elif kind == "source":
            n = int(line["id"].split("-")[1])
            clip = next(c for c in planned["clips"] if c["n"] == n)
            ok = abs(lengths.get(n, 0) - clip["seconds"]) <= 1
            checked.append({"want": want, "got": f"the user's clip is {lengths.get(n, 0):.1f} s in the film", "result": "yes" if ok else "no"})
        elif kind == "silent":
            ok = loud is None or loud < -60
            checked.append({"want": want, "got": "silent" if ok else f"there is sound, peak level {loud:.0f} dB", "result": "yes" if ok else "no"})
        else:
            unchecked.append(want)
    return checked, unchecked


FILM_TYPES = {".mp4": "video/mp4", ".png": "image/png", ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav"}


def film_file(name: str) -> Path | None:
    """The stored film with this name, if the name is one this server made."""
    file = FILMS_DIR / name
    return file if name.endswith(tuple(FILM_TYPES)) and "/" not in name and ".." not in name and file.exists() else None
