"""Plan a multi-clip video: story -> clips -> one reference per clip -> ordered calls.

Pure code. No network, no model, no credits. The split rule is a parameter so a
benchmark can compare candidate rules.

Story (a dict, JSON in and out):
    title, aspect ("16:9" | "9:16" | "1:1"), audio (bool), setting (words reused in every clip),
    subjects: [{id, words, role: person|copy|product|place, picture?}],
    beats:    [{seconds, text, kind: scene|card, place?, subjects?: [id], link?, exact_words?: [str], says?: [str],
                clip?: int, spoken?: [str], reference?: {kind: none|picture|last_frame|file, subject?, of_clip?, file?}}]
    narrator? (how the voice sounds), music? (what the music is), captions? (bool)

The host groups the beats into clips (the model decides, the code keeps track and advises):
    clip        the clip a beat belongs to, 1, 2, 3 ... in order; a card is a clip of its own. When every beat has one,
                the clips are exactly these. Without them the split rule groups the beats and `advice` says so.
    spoken      a line a person on screen says out loud, word for word (not the narrator: that is `says`)
    sounds      sound effects the clip itself makes in this beat (a whoosh, a door slam); never music. Turns on the clip's sound
    reference   on the first beat of a clip: the clip's one reference, chosen by the host (a subject's picture, the
                last frame of an earlier clip, a picture file the host made, or none). Without it the code chooses.
`advise` returns warnings with the evidence behind them; nothing in them changes the plan.

Questions (the model decides; the MCP asks at the known risks, in exact words, with a recommendation):
    `questions` lists a question for each risk with a known better way; a plan with an open question has no
    production token. The answers go back with the same story (`answers`: {id: option}); `apply_answers` changes
    the story the way the option says, and the plan is made again.
    in_scene_words  words that are part of the picture (a label, a sign): no question; the video does its best and
                    the checklist says the letters may change.
    background      on a card: its colour as a code (#3B1F6B). The generated card is on that colour (its plain name goes
                    in the picture prompt: a code in a prompt is drawn as text).
    picture         on a card: a picture to show on it (a logo, a screenshot). The card is generated from it by the image
                    editor (qwen-image-2.1), with its exact_words. Every card is generated; code draws no card in a film
                    (a drawn card only by hand, make_card_clip, when the user asks or generation failed).
    source          the user's own clip, used as it is: its file_path from upload_media, a URL, or a finished
                    project id. The beat is a clip of its own; nothing is generated for it (0 credits). `says`, `music`
                    and captions go on it like on any clip; a later clip can start from its last frame (continue a clip).
    source_sound    keep the user's clip's own sound (default true); false makes that clip silent before the sound is added.
    source_start, source_end  use only that part of the user's clip, cut by code (in testing, "keep the first 5 s
                    and change the rest" had no way to shorten the user's clip).

A voice-over is one track for the whole film, not a sound inside each clip: `says` holds the narrator's lines
for a beat. One call reads all the lines in one voice; the join puts each beat's lines on that beat.
    voice_sample  a URL of a short recording of a voice the user owns or has rights to. With the voice-over switch on
                  (MAGIC_HOUR_VOICE_OVER=cloner, the default), the voice cloner reads the script in that voice; without a
                  recording, in the preset voice that best fits `narrator_voice` (pick_voice). With the switch
                  set to video, a video model reads it over a black screen (2 right in 8).
    The switch is off in what we ship until upstream approves the voice cloner (tests/test_release_gates.py).

A beat is the smallest unit: one action. `link` says how a beat relates to the beat before it:
    continues   the same shot goes on (no cut wanted)
    event       same place and subjects, an important new event (a cut is acceptable)
    moves       a subject carries over, the place is new
    new         nothing carries over
"""

from __future__ import annotations

import math
import os
import re
from copy import deepcopy
from typing import Any

LINKS = ("continues", "event", "moves", "new")
WORD_ROUTES = ("overlay", "card", "model", "still", "accept")
STRATEGIES = ("one", "time", "idea", "event", "shot", "person", "manual")
ASPECTS = ("16:9", "9:16", "1:1")
ROLE_PRIORITY = ("person", "copy", "product", "place")
MODEL_MAX_SECONDS = {"ltx-2.5": 60, "minimax-h3": 30}
TEXT_MODEL = "ltx-2.5"          # a still with exact words, a clip longer than SCENE_MODEL makes, the narration clip
REFERENCE_MODEL = "minimax-h3"  # the free model that takes a named reference
# Every scene clip: in a 60-clip sound test minimax-h3 made the sound in every case
# (wind, a street under a voice, a second speaker, two lines in one clip); ltx-2.5 failed most of them.
SCENE_MODEL = "minimax-h3"
# In testing, one shot with a spoken line and words on screen went to create_video_from_text and lost
# its street sound, the line timing and the checklist. A video with a purpose goes through the production tool.
ROUTING_RULE = (
    "Use plan_video_production, not a single create call, when the video has a purpose beyond one action: a target "
    "audience, a platform (Instagram, TikTok, YouTube), a story, an ad or marketing purpose, a brand or product, a spoken "
    "line, a voice-over, music, or words on screen. This is true for one shot too. A single action goes to its own tool: "
    "animate this photo, a 5-second clip of a cat.")
STILL_MODEL = "qwen-image-2.1"  # exact words on a card
SUBTITLE_FONT = "Archivo"   # the subtitle tool's font closest to the caption look (Archivo Black is refused)
RESOLUTION = "480p"
CREDITS_PER_SECOND = 24
STILL_CREDITS = 10
PROMPT_LIMIT = 2000
TRIGGER_SECONDS = 15


def word_match(heard: str, script: str) -> float:
    """The share of the script's words that a transcript has, in order (longest common subsequence). 1.0 = all."""
    a = re.findall(r"[a-z0-9']+", script.lower().replace("\u2019", "'"))
    b = re.findall(r"[a-z0-9']+", heard.lower().replace("\u2019", "'"))
    row = [0] * (len(b) + 1)
    for x in a:
        prev = 0
        for j, y in enumerate(b, 1):
            prev, row[j] = row[j], (prev + 1 if x == y else max(row[j], row[j - 1]))
    return round(row[-1] / (len(a) or 1), 2)
TRIGGER_IDEAS = 3

# Where each strategy cuts between two scene beats. Every strategy also cuts when the
# next beat does not fit in max_seconds. A card is always its own clip.
CUT_ON = {
    "one": (),
    "time": (),
    "idea": ("moves", "new"),
    "event": ("event", "moves", "new"),
    "shot": LINKS,
    # Priority 1: the beats of a pictured person stay in one call (see split). Priority 2: a new idea is a new clip.
    "person": ("moves", "new"),
    "manual": (),   # cuts exactly where the caller says (the benchmark compares cut points with it)
}
CHAINED = ("person", "manual")   # an idea that is cut is chained: end on the person, start the next clip from that frame


def beat_link(beat: dict, previous: dict | None) -> str:
    """The link the story gives, else the link the place and subjects imply."""
    if previous is None or beat.get("kind") == "card" or previous.get("kind") == "card":
        return "new"
    if beat.get("link") in LINKS:
        return beat["link"]
    shared = set(beat.get("subjects", [])) & set(previous.get("subjects", []))
    if shared and beat.get("place") and beat.get("place") == previous.get("place"):
        return "continues"
    return "moves" if shared else "new"


def validate_story(story: dict) -> list[str]:
    problems: list[str] = []
    if story.get("aspect") not in ASPECTS:
        problems.append(f"aspect must be one of {', '.join(ASPECTS)}")
    beats = story.get("beats") or []
    if not beats:
        problems.append("the story has no beats")
    ids = [s.get("id") for s in story.get("subjects", [])]
    if len(ids) != len(set(ids)):
        problems.append("subject ids must be unique")
    for s in story.get("subjects", []):
        if s.get("role") not in ROLE_PRIORITY:
            problems.append(f"subject {s.get('id')}: role must be one of {', '.join(ROLE_PRIORITY)}")
    for i, beat in enumerate(beats, start=1):
        seconds = beat.get("seconds")
        if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 1:
            problems.append(f"beat {i}: seconds must be a whole number, 1 or more")
        if not str(beat.get("text", "")).strip():
            problems.append(f"beat {i}: text is empty")
        if beat.get("kind", "scene") not in ("scene", "card"):
            problems.append(f"beat {i}: kind must be scene or card")
        if beat.get("kind") == "card" and not beat.get("exact_words") and not beat.get("picture"):
            problems.append(f"beat {i}: a card needs exact_words, or `picture` (a picture to hold unchanged, such as a logo)")
        if beat.get("picture") and not str(beat["picture"]).startswith(("http://", "https://", "api-assets/")):
            problems.append(f"beat {i}: `picture` must be the picture's URL or its file_path from upload_media")
        if beat.get("words_route") == "still" and beat.get("reference"):
            problems.append(f"beat {i}: the words answer 'still' makes this clip's start picture; drop `reference`, or answer 'card' or 'model'")
        if "link" in beat and beat["link"] not in LINKS:
            problems.append(f"beat {i}: link must be one of {', '.join(LINKS)}")
        for sid in beat.get("subjects", []):
            if sid not in ids:
                problems.append(f"beat {i}: unknown subject {sid}")
        problems += _reference_problems(story, i, beat.get("reference"), beat.get("clip"))
        if beat.get("words_route") not in (None, *WORD_ROUTES):
            problems.append(f"beat {i}: words_route must be one of {', '.join(WORD_ROUTES)}")
        if beat.get("model") not in (None, *MODEL_MAX_SECONDS):
            problems.append(f"beat {i}: model must be one of {', '.join(MODEL_MAX_SECONDS)}")
        if beat.get("source"):
            # `spoken` is allowed: the words said in the user's clip, for its captions only
            clash = [f for f in ("reference", "sounds", "in_scene_words", "exact_words", "words_route", "model", "picture", "background") if beat.get(f)]
            if beat.get("kind", "scene") != "scene" or clash:
                problems.append(f"beat {i}: a `source` beat is the user's own clip, used as it is; drop {', '.join(clash) or 'kind card'}")
        if (beat.get("source_start") is not None or beat.get("source_end") is not None) and not beat.get("source"):
            problems.append(f"beat {i}: source_start and source_end go only on a `source` beat")
        if beat.get("source_end") is not None and beat["source_end"] <= (beat.get("source_start") or 0):
            problems.append(f"beat {i}: source_end must be after source_start")
        if beat.get("background") and not re.fullmatch(r"#[0-9A-Fa-f]{6}", beat["background"]):
            problems.append(f"beat {i}: background must be a colour code like #3B1F6B")
    problems += _clip_problems(beats)
    if "seconds" in story and not problems:
        total = sum(b["seconds"] for b in beats)
        if total != story["seconds"]:
            problems.append(f"beats add up to {total} s, the story says {story['seconds']} s")
    return problems


def _reference_problems(story: dict, i: int, reference: dict | None, clip: int | None = None) -> list[str]:
    if not reference:
        return []
    kind, pictured = reference.get("kind"), {s["id"]: s for s in story.get("subjects", []) if s.get("picture")}
    if kind not in ("none", "picture", "last_frame", "file"):
        return [f"beat {i}: reference kind must be none, picture, last_frame or file"]
    if kind == "last_frame":   # the last frame of a clip made before this one; beat 1 is always in clip 1
        own, of = (1 if i == 1 else clip), reference.get("of_clip")
        if own == 1:
            return [f"beat {i}: reference last_frame needs an earlier clip, and clip 1 has none; use none, picture or file"]
        if of is not None and (not isinstance(of, int) or isinstance(of, bool) or of < 1 or (own is not None and of >= own)):
            return [f"beat {i}: reference of_clip must be an earlier clip" + (f" (1 to {own - 1})" if own and own > 2 else " (clip 1)" if own else "")]
    if kind == "picture" and reference.get("subject") not in pictured:
        return [f"beat {i}: reference picture needs `subject`: a subject that has a picture ({', '.join(pictured) or 'none has one'})"]
    if kind == "file" and not str(reference.get("file", "")).strip():
        return [f"beat {i}: reference file needs `file`: the file_path or URL of the picture"]
    return []


def _clip_problems(beats: list[dict]) -> list[str]:
    """The host's grouping: all beats or none, 1, 2, 3 ... in order, a card alone in its clip."""
    marked = [b.get("clip") for b in beats]
    if not any(m is not None for m in marked):
        return []
    if any(m is None for m in marked):
        return ["set `clip` on every beat, or on none"]
    expected, problems = 1, []
    for i, (beat, m) in enumerate(zip(beats, marked), start=1):
        if m not in (expected - 1, expected) or (i == 1 and m != 1):
            problems.append(f"beat {i}: clip {m} is out of order; clips are numbered 1, 2, 3 ... in story order")
            break
        expected = m + 1
    for i, beat in enumerate(beats):
        if beat.get("kind") == "card" and sum(1 for b in beats if b.get("clip") == beat.get("clip")) > 1:
            problems.append(f"beat {i + 1}: a card is a clip of its own; give it its own clip number")
        if beat.get("source") and sum(1 for b in beats if b.get("clip") == beat.get("clip")) > 1:
            problems.append(f"beat {i + 1}: the user's clip (`source`) is a clip of its own; give it its own clip number")
    return problems


def host_cuts(story: dict) -> list[int] | None:
    """The beat numbers where the host's clips start, or None when the host did not group the beats."""
    beats = story["beats"]
    if not beats or any(b.get("clip") is None for b in beats):
        return None
    return [i + 1 for i in range(1, len(beats)) if beats[i]["clip"] != beats[i - 1]["clip"]]


def count_ideas(story: dict) -> int:
    """Scene groups that share nothing with the group before them, plus cards."""
    ideas, previous = 0, None
    for beat in story["beats"]:
        if beat_link(beat, previous) in ("moves", "new"):
            ideas += 1
        previous = beat
    return ideas


def needs_production(story: dict) -> bool:
    total = sum(b["seconds"] for b in story["beats"])
    return total > TRIGGER_SECONDS or count_ideas(story) >= TRIGGER_IDEAS


def _beats_that_fit(story: dict, max_seconds: int, person_seconds: int | None = None) -> list[dict]:
    """Number the beats; cut a beat longer than its limit into equal parts that continue.
    person_seconds: the limit for a beat that shows a pictured person (it may be longer than the others)."""
    out: list[dict] = []
    previous = None
    for n, beat in enumerate(story["beats"], start=1):
        link = beat_link(beat, previous)
        previous = beat
        limit = person_seconds if person_seconds and pictured_people(story, [beat]) else max_seconds
        if beat.get("source"):   # the user's clip is used whole, however long
            out.append({**deepcopy(beat), "n": n, "kind": "source", "link": link})
            continue
        parts = -(-beat["seconds"] // limit)
        base, extra = divmod(beat["seconds"], parts)
        for p in range(parts):
            out.append({
                **deepcopy(beat), "n": n, "kind": beat.get("kind", "scene"),
                "seconds": base + (1 if p < extra else 0),
                "link": link if p == 0 else "continues",
            })
    return out


def pictured_people(story: dict, parts: list[dict]) -> set[str]:
    """The people in these beats whose look comes from a picture."""
    by_id = {s["id"]: s for s in story.get("subjects", [])}
    return {sid for part in parts for sid in part.get("subjects", [])
            if by_id[sid]["role"] == "person" and by_id[sid].get("picture")}


def clip_limit(story: dict, parts: list[dict]) -> int:
    """The longest clip for these beats: 15 s; with a pictured person, the reference model's 30 s."""
    return MODEL_MAX_SECONDS[REFERENCE_MODEL] if pictured_people(story, parts) else TRIGGER_SECONDS


def people(story: dict, parts: list[dict]) -> set[str]:
    """The people in these beats, with or without a picture."""
    by_id = {s["id"]: s for s in story.get("subjects", [])}
    return {sid for part in parts for sid in part.get("subjects", []) if by_id[sid]["role"] == "person"}


def split(story: dict, strategy: str = "idea", max_seconds: int = TRIGGER_SECONDS, cuts: list[int] | None = None) -> list[dict]:
    """Group the beats into clips. Returns [{n, beats, seconds, kind, link, parts}].

    "person": ideas come first, and the same person is an idea. A beat that shows a person from the clip before
    it stays in that clip, also when the place changes, while the clip fits: max_seconds, or what the reference
    model makes in one call when the person's look comes from a picture (a last-frame start loses that face).
    "manual": a new clip starts before each beat number in `cuts`.
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"strategy must be one of {', '.join(STRATEGIES)}")
    if strategy == "one":
        beats = _beats_that_fit(story, 10**6)
        return [{"n": 1, "kind": "scene", "link": "new", "parts": beats,
                 "beats": [b["n"] for b in beats], "seconds": sum(b["seconds"] for b in beats)}]
    clips: list[dict] = []
    current: dict | None = None
    person_seconds = MODEL_MAX_SECONDS[REFERENCE_MODEL] if strategy == "person" else None
    manual = strategy == "manual"
    for beat in _beats_that_fit(story, 10**6 if manual else max_seconds, person_seconds):
        is_card = beat["kind"] in ("card", "source")   # each is a clip of its own
        in_scene = current is not None and not is_card and current["kind"] == "scene"
        same_person = strategy == "person" and in_scene and beat["link"] != "new" and bool(people(story, current["parts"]) & people(story, [beat]))
        from_picture = same_person and bool(pictured_people(story, current["parts"]) & pictured_people(story, [beat]))
        limit = MODEL_MAX_SECONDS[REFERENCE_MODEL] if from_picture else max_seconds
        # A pictured person who was not in the clip so far is a new idea: their picture needs its own call.
        enters = strategy == "person" and in_scene and bool(pictured_people(story, [beat]) - pictured_people(story, current["parts"]))
        if manual:
            cut = not in_scene or beat["n"] in (cuts or [])
        else:
            cut = (
                not in_scene or enters
                or (beat["link"] in CUT_ON[strategy] and not same_person)
                or current["seconds"] + beat["seconds"] > limit
            )
        if cut:
            current = {"n": len(clips) + 1, "kind": beat["kind"], "link": beat["link"], "parts": [], "beats": [], "seconds": 0}
            clips.append(current)
        current["parts"].append(beat)
        current["beats"].append(beat["n"])
        current["seconds"] += beat["seconds"]
    return clips


def _all_pictured(story: dict, clip: dict) -> list[dict]:
    """Every subject in this clip with a picture, in role order. In testing, two products, each with a real
    photo as its own likeness guide, both looked right in every shot."""
    by_id = {s["id"]: s for s in story.get("subjects", [])}
    seen = list({sid: by_id[sid] for part in clip["parts"] for sid in part.get("subjects", []) if by_id[sid].get("picture")}.values())
    return sorted(seen, key=lambda s: ROLE_PRIORITY.index(s["role"]))


def choose_reference(story: dict, clip: dict, previous: dict | None, moves: str = "last_frame",
                     earlier: list[dict] | None = None, chain: bool = True) -> dict:
    """At most one reference for a clip.

    card                    its own still
    continues / event       the last frame of the clip before it
    moves                   the carried subject's picture; without one, `moves` decides (last_frame | none)
    new                     a picture of a subject in the clip, else nothing
    earlier (chained rules) a person with no picture: the last frame of the last clip that showed them,
                            also when other clips are in between. new_place: that frame is in another place.
    chain=False             no last-frame reference at all (clips share words only)
    """
    if clip["kind"] == "card":
        return {"kind": "still", "exact_words": [w for part in clip["parts"] for w in part.get("exact_words", [])]}
    if clip["kind"] == "source":
        return {"kind": "source", "file": clip["parts"][0]["source"]}
    routed = [w for part in clip["parts"] if part.get("words_route") == "still" for w in part.get("exact_words", [])]
    if routed:   # the host chose: a picture of the scene with the words, and the clip starts from it
        return {"kind": "scene_still", "exact_words": routed}
    chosen = clip["parts"][0].get("reference")
    if chosen:   # the host chose the reference
        kind = chosen["kind"]
        if kind == "picture":
            subject = next(s for s in story.get("subjects", []) if s["id"] == chosen["subject"])
            out = {"kind": "picture", "subject": subject["id"], "file": subject["picture"], "chosen": True}
            # Each pictured subject in the clip is its own likeness guide,
            # also when the host picks the reference. The chosen one goes first.
            pictured = [subject] + [x for x in _all_pictured(story, clip) if x["id"] != subject["id"]]
            if len(pictured) > 1:
                out["pictures"] = [{"subject": x["id"], "file": x["picture"]} for x in pictured]
            return out
        if kind == "file":
            return {"kind": "file", "subject": "reference", "file": chosen["file"], "chosen": True}
        if kind == "last_frame":
            return {"kind": "last_frame", "of_clip": chosen.get("of_clip") or (previous["n"] if previous else 0), "chosen": True}
        return {"kind": "none", "chosen": True}
    can_continue = previous is not None and previous["kind"] != "card"
    link = clip["link"] if can_continue else "new"
    # A pictured person who was not in the clip before: the picture is the only thing that carries the face.
    new_face = pictured_people(story, clip["parts"]) - (pictured_people(story, previous["parts"]) if can_continue else set())
    if chain and link in ("continues", "event") and not new_face:
        return {"kind": "last_frame", "of_clip": previous["n"]}
    pictured = _all_pictured(story, clip)
    if pictured:
        out = {"kind": "picture", "subject": pictured[0]["id"], "file": pictured[0]["picture"]}
        if len(pictured) > 1:
            out["pictures"] = [{"subject": s["id"], "file": s["picture"]} for s in pictured]
        return out
    if not chain:
        return {"kind": "none"}
    for before in reversed(earlier or []):
        if before["kind"] == "scene" and people(story, before["parts"]) & people(story, clip["parts"]):
            reference = {"kind": "last_frame", "of_clip": before["n"]}
            if before["parts"][-1].get("place") != clip["parts"][0].get("place"):
                reference["new_place"] = True
            return reference
    if link == "moves" and moves == "last_frame":
        return {"kind": "last_frame", "of_clip": previous["n"]}
    return {"kind": "none"}
