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

from .voices import VOICES
from .media_tools import LOOKS

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
VOICE_OVER_DEFAULT = "cloner"   # on by default; ships to production once upstream approves
VOICE_OVER_MODES = ("video", "cloner")
CLONER_CREDITS_PER_10_LETTERS = 1   # 0.1 credits a character, rounded up


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


# With the cloner on and no recording from the user, the cloner reads the script in
# a preset voice (Magic Hour's own voice previews), so a voice-over needs no question. A user's own recording wins.
# The host gives the narrator's profile (`narrator_voice`: gender, age, style) and the code
# takes the best tagged preset. Before, every film got Ashley or Daniel. In testing, the host's own pick
# from a short list of 24 broke its own brief in 3 of 6 requests; the profile match kept it in 6 of 6.
MALE_WORDS = re.compile(r"\b(man|man's|male|men|he|his|him|guy|boy|gentleman|father|dad|deep)\b", re.I)
ACCENTS = ("American", "British", "Australian", "Irish", "Scottish", "Indian", "Nigerian", "African", "Southern", "Canadian")
_PLAIN = set("a an the and with of for to in on voice voices male female natural is it its that this like".split())


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if w not in _PLAIN}


def pick_voice(story: dict) -> dict:
    """The preset voice for the narrator: `voice` (an id) if set, else the best match for `narrator_voice` and `narrator`.

    Score: style 4, age 2, each accent named in both 2, another accent named -2 (in testing, "neutral American" got a West African voice), each other
    shared description word 1. Style outweighs an accent: at 3 and 3, two test briefs lost their style. Ties go to the lowest id. With no profile: a man's voice if `narrator` says so, else a
    woman's; middle-aged; Conversational.
    """
    chosen = story.get("voice")
    if chosen:
        hit = next((v for v in VOICES if v[0] == chosen), None)
        if hit:
            return {"id": hit[0], "name": hit[1], "url": hit[6], "tags": f"{hit[2]}, {hit[3]}, {hit[4]}"}
    said = story.get("narrator") or ""
    profile = story.get("narrator_voice") or {}
    gender = profile.get("gender") or ("Male" if MALE_WORDS.search(said) else "Female")
    age, style = profile.get("age") or "Middle-aged", profile.get("style") or "Conversational"
    accents, words = {a for a in ACCENTS if a.lower() in said.lower()}, _words(said)

    def score(v: tuple) -> tuple:
        theirs = {a for a in ACCENTS if a.lower() in v[5].lower()}
        named = 2 * len(accents & theirs) - (2 if accents and theirs - accents else 0)
        return (4 * (v[4] == style) + 2 * (v[3] == age) + named + len(words & _words(v[5])), -int(v[0][3:]))

    best = max((v for v in VOICES if v[2] == gender), key=score)
    return {"id": best[0], "name": best[1], "url": best[6], "tags": f"{best[2]}, {best[3]}, {best[4]}"}


def voice_over_mode() -> str:
    """How a voice-over is made: "video" (a video model over a black screen) or "cloner" (the voice cloner)."""
    mode = os.getenv("MAGIC_HOUR_VOICE_OVER", VOICE_OVER_DEFAULT).strip().lower()
    return mode if mode in VOICE_OVER_MODES else VOICE_OVER_DEFAULT
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


SPOKEN_LABEL = re.compile(r"\s*([A-Z][\w'-]*(?: [A-Z][\w'-]*){0,2}):\s+(.+)")


def spoken_slots(parts: list[dict]) -> list[dict]:
    """Each spoken line of a clip and its time slot in the clip (seconds), as compose_prompt gives it to the model:
    {"beat", "who", "text", "start", "end"}. The captions of spoken lines start from these slots."""
    out, at = [], 0
    for part in parts:
        end = at + part["seconds"]
        start = at
        for said in part.get("spoken") or []:
            label = SPOKEN_LABEL.match(said)
            who, said = (label.group(1), label.group(2).strip()) if label else ("", said.strip())
            talk = min(end - start, max(2, math.ceil(len(said.split()) / SPOKEN_WORDS_PER_SECOND)))
            out.append({"beat": part.get("n"), "who": who, "text": said, "start": start, "end": start + talk if talk > 0 else end})
            start += talk
        at = end
    return out


def compose_prompt(story: dict, clip: dict, reference: dict, ends_on: list[str] | None = None, ends_close: bool = False) -> str:
    """One prose prompt from the story's own words. The same words go into every clip that shows the thing."""
    by_id = {s["id"]: s for s in story.get("subjects", [])}
    if clip["kind"] == "card":
        # The card holds still. "A very slow, barely visible push in" came out as a 1.3-1.5x zoom on
        # ltx-2.5 and cut long words and corner decoration. This prompt still pushed
        # 1.13-1.27x on ltx-2.5, so a card goes on minimax-h3 (1.00x).
        motion = next((p["card_motion"].strip().rstrip(".") for p in clip["parts"] if p.get("card_motion")), None)
        if motion:   # the user asked the card to move (the user's words override the held card)
            return (f"The supplied image, with one move for the whole clip: {motion}. Every letter stays exactly as in the "
                    "image. Nothing else appears.")
        return ("A locked-off static shot of the supplied image for the whole clip: the camera does not move, the "
                "image fills the frame at the same size from the first frame to the last. Every letter stays exactly "
                "as in the image. Nothing else appears.")
    lines = [f"A {clip['seconds']}-second {story['aspect']} clip. {story['setting'].strip()}"]
    shown: list[str] = []
    for part in clip["parts"]:
        for sid in part.get("subjects", []):
            if sid not in shown:
                shown.append(sid)
    for sid in shown:
        subject = by_id[sid]
        tagged = {p["subject"] for p in reference.get("pictures", [])} or {reference.get("subject")}
        tag = f"@{sid} is the reference for " if reference["kind"] == "picture" and sid in tagged else ""
        lines.append(f"{tag}{subject['words'].strip().rstrip('.')}.")
    if reference["kind"] == "picture":
        names = [p["subject"] for p in reference.get("pictures", [])] or [reference["subject"]]
        guides = " and ".join(f"@{n}" for n in names)
        lines.append(f"Use {guides} as {'likeness guides' if len(names) > 1 else 'a likeness guide'}, not "
                     f"{'pasted photos' if len(names) > 1 else 'a pasted photo'}. Build the scene around {'them' if len(names) > 1 else 'it'}.")
    if reference["kind"] == "file":
        lines.append("Use @reference, the supplied picture, as a likeness guide for how everything looks. Build the scene around it.")
    if reference["kind"] == "last_frame" and reference.get("new_place"):
        lines.append("The supplied image is the first frame: a close view of the person. Keep this person exactly as shown. "
                     "The place is new: the camera pulls back from the close view to show it.")
    elif reference["kind"] == "last_frame":
        lines.append("The supplied image is the first frame. Continue from it: the same people, place and light.")
    at = 0
    for part in clip["parts"]:
        end = at + part["seconds"]
        spoken = part.get("spoken") or []
        if not spoken:
            lines.append(f"{at}-{end} s: {part['text'].strip()}")
        start, text = at, part["text"].strip()
        for said in spoken:
            # In testing, a script label inside the quote ("Maya: Then we walk.") was said out loud as a name
            label = SPOKEN_LABEL.match(said)
            who, said = (label.group(1), label.group(2).strip()) if label else ("", said.strip())
            # In testing, "talks to the camera" over 0-8 s with a 3 s line gave 4 s of made-up speech first.
            # The line in a time slot of its own, then a silent end, gave clean speech (a prompt test, 4 variants).
            # 2.5 words a second, measured: 18 words in 6.9-7.1 s, 9 words in 3.0-3.6 s. At 3 words a second
            # the line ran past its slot every time, and once the model then made up words.
            talk = min(end - start, max(2, math.ceil(len(said.split()) / SPOKEN_WORDS_PER_SECOND)))
            slot = f"{start}-{start + talk} s:" if talk > 0 else "Then"   # no time left: the line follows the one before
            # Prompt tests, 36 clips: a talking verb in its own sentence ("talks to the camera. The
            # speaker says, word for word: ...") gave made-up speech after the line in 7 of 10 clips. The line joined to
            # the action ("smiles at the camera and says, word for word: ...") gave 12 of 12 clean.
            if text:
                said_by = f"and {who} says" if who else "and says"
                lines.append(f'{slot} {text.rstrip(" .;:,")} {said_by}, word for word: "{said}"'.replace("  ", " "))
            else:
                lines.append(f'{slot} {who or "The speaker"} says, word for word: "{said}"'.replace("  ", " "))
            start, text = start + talk, ""
        # Tests direct to minimax, 3 runs each: a person idle on camera for 7 s or more after the
        # line talks again (0 of 3). Up to 3 s of "smiles and nods" stayed clean (6 of 6). For a longer stretch, "lowers
        # the hand, takes a slow breath and glances down" was the one neutral action clean with 7 s and 11 s (6 of 6).
        if spoken and 1 <= end - start <= SPOKEN_QUIET_SECONDS:
            lines.append(f"{start}-{end} s: {who or 'the speaker'} stops talking, smiles and nods, mouth closed. Nobody speaks.")
        elif spoken and end - start > SPOKEN_QUIET_SECONDS:
            lines.append(f"{start}-{end} s: {who or 'the speaker'} lowers the hand, takes a slow breath and glances down, mouth closed, still in frame.")
        elif spoken:   # no time left in the beat: the line still gets a clear end
            lines.append(f"After the line, {who or 'the speaker'} stops talking, mouth closed. Nobody speaks.")
        for words in part.get("in_scene_words") or []:
            lines.append(f'The words "{words.strip()}" are part of the picture, spelled exactly.')
        if part.get("exact_words") and part.get("kind", "scene") == "scene":
            # In testing, a label named a stage (Evaporation) and the model drew it faintly or late
            lines.append(f"The action of {at}-{end} s is large and plain to see from {at} s, not faint or late.")
        if part.get("words_route") in ("model", "accept"):   # accept: best effort by the scene model
            for words in part.get("exact_words") or []:
                lines.append(f'Show the words "{words.strip()}" on screen in large clean letters, spelled exactly.')
        at = end
    if ends_on:
        # A later clip starts from this clip's last frame, so that frame must show the people it carries. The line
        # does not describe them again: a second description made the model draw a second person.
        if ends_close:   # the next place is another one: carry the person, not the place
            lines.append("End close on the last action's person: head and shoulders fill the frame, the background soft, "
                         "held still for the last second.")
        else:
            lines.append("End with everyone from the last action still in frame, held still for the last second.")
    if any(p.get("words_route") in ("model", "accept") and p.get("exact_words") for p in clip["parts"]):
        lines.append("Each action happens once, in this order. No repeated shots, no other scenes.")
    else:
        lines.append("Each action happens once, in this order. No repeated shots, no title cards, no captions, no other scenes.")
    effects = [s for p in clip["parts"] for s in p.get("sounds", [])]
    if story.get("audio") or any(p.get("spoken") for p in clip["parts"]) or effects:
        # A clip never makes music; music is one separate track for the whole film, added at the join.
        # A sound effect is a clip thing (the whoosh as the logo forms), so the clip makes it.
        heard = f"only the voices and sounds of the scene itself, including {'; '.join(effects)}" if effects else "only the voices and sounds of the scene itself"
        lines.append(f"Sound: {heard}. No music.")
    return " ".join(lines)


def plan(story: dict, strategy: str = "idea", max_seconds: int = TRIGGER_SECONDS, moves: str = "last_frame",
         cuts: list[int] | None = None, chain: bool = True, end_on: bool = True) -> dict:
    """The ordered calls for a story, with the checks that must pass before anything is paid for."""
    problems = validate_story(story)
    if problems:
        return {"ok": False, "problems": problems, "clips": []}
    clips = split(story, strategy, max_seconds, cuts)
    chained = strategy in CHAINED and chain
    out: list[dict] = []
    late_problems: list[str] = []
    references, previous = [], None
    for i, clip in enumerate(clips):
        references.append(choose_reference(story, clip, previous, moves, clips[:i] if chained else None, chain))
        previous = clip
    for i, clip in enumerate(clips):
        reference = references[i]
        # Each later clip that starts from this clip's last frame: this clip ends on the people they share.
        takers = [j for j in range(i + 1, len(clips)) if chained and end_on and references[j].get("of_clip") == clip["n"]]
        ends_on = sorted({sid for j in takers for sid in people(story, clip["parts"]) & people(story, clips[j]["parts"])})
        ends_close = any(references[j].get("new_place") for j in takers)
        steps: list[dict] = []
        background = next((p.get("background") for p in clip["parts"] if p.get("background")), None)
        held = next((p.get("picture") for p in clip["parts"] if p.get("picture")), None)
        if clip["kind"] == "source":   # the user's own clip, used as it is: nothing to make
            part = clip["parts"][0]
            out.append({
                "n": clip["n"], "kind": "source", "beats": clip["beats"], "seconds": clip["seconds"], "link": clip["link"],
                "reference": reference, "ends_on": [], "depends_on": [], "made_by": "user",
                "steps": [{"tool": "use_source", "model": "user", "credits": 0, "seconds": clip["seconds"], "source": part["source"],
                           "sound": part.get("source_sound", True) is not False, "speech": part.get("source_speech"),
                           "start": part.get("source_start"), "end": part.get("source_end")}],
            })
            continue
        # Every card is generated, also with an exact colour (`background`) or the user's picture
        # (a logo). Before: both were drawn by code, and in testing hosts held the logo file on a code card (20 of 20).
        if reference["kind"] == "scene_still":
            words = " and ".join(f'"{w}"' for w in reference["exact_words"])
            by_id = {x["id"]: x for x in story.get("subjects", [])}
            shown = [by_id[sid]["words"].strip().rstrip(".") for p in clip["parts"] for sid in p.get("subjects", []) if sid in by_id]
            steps.append({
                "tool": "generate_image", "model": STILL_MODEL, "credits": STILL_CREDITS,
                "prompt": " ".join([story["setting"].strip(), *[f"{x}." for x in dict.fromkeys(shown)],
                                    clip["parts"][0]["text"].strip(), f"The words {words} are written large and clear in the picture, "
                                    f"spelled exactly. The only text in the image is exactly {words}."]),
            })
        if reference["kind"] == "still":
            words = " and ".join(f'"{w}"' for w in reference["exact_words"])
            text = " ".join(p["text"].strip() for p in clip["parts"])
            colour = f" The background is flat {colour_name(background)}." if background else ""
            if held:   # the user's picture on the card: the image editor makes the card from it
                steps.append({
                    "tool": "edit_image", "model": STILL_MODEL, "credits": STILL_CREDITS, "picture": held,
                    "prompt": (f"A designed card. {text}{colour} The supplied picture is shown whole and centred, recognisable: "
                               "its shapes, colours and letters kept. "
                               + (f"The only added text is exactly {words}." if words else "No added text.")),
                })
            else:
                steps.append({
                    "tool": "generate_image", "model": STILL_MODEL, "credits": STILL_CREDITS,
                    "prompt": f"{text}{colour} The only text in the image is exactly {words}.",
                })
        needs_sound = (bool(story.get("audio")) or any(p.get("spoken") or p.get("sounds") for p in clip["parts"])) and clip["kind"] != "card"
        # a clip longer than the scene model makes in one call falls back to ltx-2.5
        scene_model = SCENE_MODEL if clip["seconds"] <= MODEL_MAX_SECONDS[SCENE_MODEL] else TEXT_MODEL
        if reference["kind"] in ("picture", "file"):
            tool, model = "create_video_from_text", REFERENCE_MODEL
        elif reference["kind"] == "last_frame":
            # Back to minimax-h3 for every chained clip. The ltx-2.5 rule for silent chained clips
            # gave smoother cuts but lost the product (bottle drift, no picture reference) and made another case worse.
            tool, model = "create_video_from_image", scene_model
        elif reference["kind"] in ("still", "scene_still"):   # words in the still stay exact on ltx-2.5
            # A clip with speech or sound needs minimax-h3; ltx-2.5 dropped speech and sound
            # in the sound test. A silent word still stays on ltx-2.5.
            # A card goes on minimax-h3 (a card holds still). Card test, 9 clips, 792 credits: minimax-h3
            # from the card still held 1.00x in 3 of 3, letters kept; ltx-2.5 pushed in 1.13-1.27x.
            tool, model = "create_video_from_image", scene_model if (needs_sound or clip["kind"] == "card") else TEXT_MODEL
        else:
            tool, model = "create_video_from_text", scene_model
        answered = next((p.get("model") for p in clip["parts"] if p.get("words_route") == "model" and p.get("model")), None)
        asked = answered or next((p.get("model") for p in clip["parts"] if p.get("model")), None)
        if asked and asked in MODEL_MAX_SECONDS:   # the host chose the model; an answer's choice wins over a beat's
            if reference["kind"] in ("picture", "file") and asked != REFERENCE_MODEL:
                late_problems.append(f"clip {clip['n']}: {asked} cannot take a picture reference; use {REFERENCE_MODEL}, or drop the reference")
            else:
                model = asked
        overlay, inside = [], 0
        for part in clip["parts"]:
            if part.get("words_route") == "overlay" and part.get("exact_words"):
                overlay.append({"text": "\n".join(part["exact_words"]), "start": inside, "end": inside + part["seconds"],
                                **{k: part[f"words_{k}"] for k in ("look", "font") if part.get(f"words_{k}")}})
            inside += part["seconds"]
        out_overlay = {"overlay": overlay} if overlay else {}
        steps.append({
            "tool": tool, "model": model, "resolution": RESOLUTION, "seconds": clip["seconds"],
            "audio": needs_sound,
            "credits": clip["seconds"] * CREDITS_PER_SECOND,
            "prompt": compose_prompt(story, clip, reference, ends_on, ends_close),
        })
        out.append({
            "n": clip["n"], "kind": clip["kind"], "beats": clip["beats"], "seconds": clip["seconds"],
            "link": clip["link"], "reference": reference, "ends_on": ends_on,
            "depends_on": [reference["of_clip"]] if reference["kind"] == "last_frame" else [],
            "steps": steps, **out_overlay,
        })
    for c in out:
        c.setdefault("made_by", "api")
    result = {
        "ok": True, "strategy": strategy, "max_seconds": max_seconds,
        "seconds": sum(c["seconds"] for c in out),
        "credits": sum(s["credits"] for c in out for s in c["steps"]),
        "clips": out, "join": [c["n"] for c in out], "rounds": [],
    }
    try:
        result["rounds"] = rounds(out)
    except ValueError:   # a clip waits for a clip that is not made before it: check_plan names it
        pass
    sound = plan_sound(story, clips)
    if sound:
        result["sound"] = sound
        result["credits"] += sum(sound[k]["credits"] for k in ("narration", "music") if k in sound)
    result["checklist"] = checklist(story, clips, sound)
    result["problems"] = check_plan(story, result) + late_problems
    result["ok"] = not result["problems"]
    return result


NARRATION_WORDS_PER_SECOND = 2.5
SPOKEN_WORDS_PER_SECOND = 2.5   # a line a person on screen says (minimax-h3), measured
SPOKEN_QUIET_SECONDS = 3   # most idle time after a spoken line that stays clean (minimax-h3), measured
VOICE_WORDS_PER_SECOND = 2.7   # the cloner's pace, measured: 22 words in 7.3 s, 16 in 6.2 s
MUSIC_MIN_SECONDS, MUSIC_MAX_SECONDS, MUSIC_CREDITS_PER_SECOND = 15, 300, 1   # the music model makes 15 to 300 s


def plan_sound(story: dict, clips: list[dict]) -> dict | None:
    """The sound that is added after the join: one narration call, one music track, drawn captions."""
    parts, at = [], 0.0
    for clip in clips:
        inside = 0.0   # where the beat starts inside its clip: the join uses it with the real length of each clip
        for beat in clip["parts"]:
            lines = [line.strip() for line in beat.get("says") or [] if line.strip()]
            if lines:
                parts.append({"beat": beat["n"], "clip": clip["n"], "inside": inside, "at": round(at + 0.5, 2),
                              "seconds": beat["seconds"], "lines": lines})
            at += beat["seconds"]
            inside += beat["seconds"]
    film_seconds = sum(c["seconds"] for c in clips)
    # captions: script = the exact `says` words, drawn by code at the voice-over's pauses
    # (0 credits; the default when there is a script). speech = Magic Hour's subtitle tool transcribes the finished
    # film (for the user's clip with talking and no script; it costs credits and can miss a line).
    # Two caption sources in one film: script also covers the `spoken` lines, drawn by code when the
    # person says them (the speech found near the line's slot in the clip's own sound), e.g. a voice-over and a presenter.
    said = any(p.get("spoken") for clip in clips for p in clip["parts"])
    source = story.get("caption_source") or ("script" if parts or said else "speech")
    sound: dict = {"captions": bool(story.get("captions")) and bool(parts or said) and source == "script"}
    if story.get("captions") and source == "speech":
        # In a test, the subtitle tool refused all five caption fonts (Archivo Black, Anton, Bebas Neue, Lilita One,
        # Permanent Marker: invalid_font) and took "Archivo", so the subtitles are always in bold Archivo
        sound["subtitles"] = {"tool": "add_subtitles_to_video", "after": "join_production_clips", "style": {"custom_config": {
            "font": SUBTITLE_FONT, "font_style": "bold", "text_color": "#FFFFFF",
            "highlighted_text_color": "#FFFFFF", "stroke_color": "#000000", "stroke_width": 4, "vertical_position": "bottom",
            "horizontal_position": "center"}}}
    if story.get("caption_font") and sound["captions"]:
        sound["caption_font"] = story["caption_font"]
    if parts:
        script = " ".join(line for part in parts for line in part["lines"])
        seconds = min(MODEL_MAX_SECONDS[TEXT_MODEL], max(5, -(-len(script.split()) // NARRATION_WORDS_PER_SECOND) + 3))
        if voice_over_mode() == "cloner" and story.get("voice_route") != "video":
            preset = pick_voice(story)
            sample = story.get("voice_sample") or preset["url"]
            sound["narration"] = {
                "parts": parts, "tool": "voice_cloner", "sample": sample, "script": script,
                "voice": "the user's recording" if story.get("voice_sample") else f"preset voice {preset['name']} ({preset['id']}: {preset['tags']})",
                "credits": -(-len(script) // 10) * CLONER_CREDITS_PER_10_LETTERS,
            }
        else:
            voice = (story.get("narrator") or "a calm, warm narrator").strip().rstrip(".")
            sound["narration"] = {
                "tool": "video",
                "parts": parts, "seconds": int(seconds), "model": TEXT_MODEL, "resolution": RESOLUTION, "credits": int(seconds) * CREDITS_PER_SECOND,
                "prompt": (f"A plain black screen for the whole clip; nothing appears and nothing moves. Sound only: an off-camera narrator, {voice}. "
                           f"The narrator reads these words once, exactly, at a relaxed pace, with a clear pause after each sentence, and then stays silent: "
                           f"\"{script}\" No music, no other sound."),
            }
    if story.get("music"):
        seconds = min(MUSIC_MAX_SECONDS, max(MUSIC_MIN_SECONDS, film_seconds + 2))
        sound["music"] = {"seconds": seconds, "prompt": story["music"].strip(), "credits": seconds * MUSIC_CREDITS_PER_SECOND,
                          "instrumental": not story.get("music_vocals")}   # singing only when the user asks
    return sound if "narration" in sound or "music" in sound or "subtitles" in sound or sound["captions"] else None


def checklist(story: dict, clips: list[dict], sound: dict | None) -> list[dict]:
    """What the finished film must have, from the story. `how`: "measured" lines are checked by code at the join;
    "by eye" lines are for the host to confirm before it delivers."""
    seconds = sum(c["seconds"] for c in clips)
    by_id = {s["id"]: s for s in story.get("subjects", [])}
    lines = [{"id": "file", "want": f"One file, {seconds} s, {story['aspect']}", "how": "measured"},
             {"id": "order", "want": f"{len(story['beats'])} beats in story order, each once, no repeats", "how": "by eye"}]
    for clip in clips:
        if clip["kind"] == "source":
            lines.append({"id": f"source-{clip['n']}", "want": f"The user's clip used as it is, about {clip['seconds']} s", "how": "measured length"})
        if clip["kind"] == "card":
            words = ", ".join(f'"{w}"' for part in clip["parts"] for w in part.get("exact_words", []))
            motion = next((p["card_motion"].strip().rstrip(".") for p in clip["parts"] if p.get("card_motion")), None)
            held = f"Card {clip['seconds']} s, moving ({motion})," if motion else f"Card held {clip['seconds']} s"
            lines.append({"id": f"card-{clip['n']}", "want": f"{held} with exactly {words}", "how": "measured length, words by eye"})
    for sid in sorted({sid for beat in story["beats"] for sid in beat.get("subjects", [])}):
        lines.append({"id": f"same-{sid}", "want": f"{by_id[sid]['words'].split(',')[0].split(':')[0].strip()} looks the same in every clip", "how": "by eye"})
    for n, beat in enumerate(story["beats"], start=1):
        if beat.get("kind", "scene") == "scene" and beat.get("exact_words"):
            route = beat.get("words_route") or "not chosen"
            how = {"overlay": "exact: drawn by code over the scene", "model": "by eye: drawn by the video model, may be misspelled",
                   "still": "by eye: drawn by the picture model, may be misspelled",
                   "accept": "by eye: accepted as a risk, tell the user"}.get(route, "by eye")
            lines.append({"id": f"words-{n}", "want": f"On screen, spelled exactly: {', '.join(chr(34) + w + chr(34) for w in beat['exact_words'])}", "how": how})
        for words in beat.get("in_scene_words") or []:
            lines.append({"id": f"in-scene-{_letters(words)[:12]}", "want": f"\"{words}\" in the picture (best effort: letters on a moving object may change; tell the user)", "how": "by eye, best effort"})
    spoken = [said.strip() for beat in story["beats"] if not beat.get("source") for said in beat.get("spoken") or []]
    for k, said in enumerate(spoken, start=1):
        lines.append({"id": f"line-{k}", "want": f"Said on screen once, word for word: \"{said}\"", "how": "by ear"})
    if sound and "narration" in sound:
        script = " ".join(line for part in sound["narration"]["parts"] for line in part["lines"])
        lines.append({"id": "voice", "want": f"Voice-over says once, in order: \"{script}\"", "how": "measured: speech heard; words by ear"})
    if sound and "music" in sound:
        lines.append({"id": "music", "want": "Music under the whole film", "how": "measured"})
    if sound and sound.get("captions"):
        lines.append({"id": "captions", "want": "The voice-over's sentences drawn on the picture when they are said", "how": "measured"})
    if sound and "subtitles" in sound:
        lines.append({"id": "subtitles", "want": "Subtitles of what is said, white with a black outline (add_subtitles_to_video on the joined film)", "how": "by eye"})
    if story.get("audio"):
        lines.append({"id": "scene-sound", "want": "The scenes have their own sound (as the beats describe)", "how": "measured: a sound track that is not silent"})
    elif not sound and not any(c["kind"] == "source" and c["parts"][0].get("source_sound", True) is not False for c in clips):
        lines.append({"id": "silent", "want": "No sound", "how": "measured"})
    return lines


def rounds(clips: list[dict]) -> list[list[int]]:
    """Batches of clips to make together. A clip is in the first round where every clip it starts from is done.
    Two people who take turns are two chains: each chain is linear, and the chains run side by side."""
    done: set[int] = set()
    out: list[list[int]] = []
    left = list(clips)
    while left:
        ready = [c["n"] for c in left if set(c["depends_on"]) <= done]
        if not ready:
            raise ValueError("clips wait for each other in a circle")
        out.append(ready)
        done |= set(ready)
        left = [c for c in left if c["n"] not in done]
    return out


ENTERS = re.compile(r"\b(enter|enters|entering|walks? (?:in|up|over|into)|arrives?|comes? (?:in|over|into)|joins?|appears?|steps? in)\b", re.I)


def _last_frame_people(story: dict, planned: dict) -> list[str]:
    """In testing, one person per beat (Maya, Leo, Maya), each clip from the last frame before it. The
    clip for Maya's second line started on Leo, so Leo said her line in her voice, and the last shot added a stranger. A
    clip that starts from a last frame shows the people in that frame; a person not in it must walk in (said in the beat)."""
    by_id = {s["id"]: s for s in story.get("subjects", [])}
    by_n = {c["n"]: c for c in planned["clips"]}
    def people_in(clip: dict) -> set[str]:
        return {sid for b in clip["beats"] for sid in story["beats"][b - 1].get("subjects", []) if by_id.get(sid, {}).get("role") == "person"}
    problems = []
    for clip in planned["clips"]:
        reference = clip.get("reference") or {}
        before = by_n.get(reference.get("of_clip")) if reference.get("kind") == "last_frame" else None
        if not before or before.get("kind") != "scene":
            continue
        missing = sorted(people_in(clip) - people_in(before))
        if missing and not ENTERS.search(story["beats"][clip["beats"][0] - 1].get("text", "")):
            names = " and ".join(missing)
            problems.append(f"clip {clip['n']} starts from the last frame of clip {before['n']}, but {names} {'is' if len(missing) == 1 else 'are'} "
                            f"not in clip {before['n']}: the video model gives the action or line to whoever is in that frame. "
                            f"Put {names} in the subjects and words of clip {before['n']}'s beats too (in the shot, also when silent), "
                            f"or say in the beat that {names} walks in, or start clip {clip['n']} with no last frame (`reference` "
                            f"{{\"kind\": \"none\"}} on its first beat; the faces may then differ).")
    return problems


def check_plan(story: dict, planned: dict) -> list[str]:
    """Gates on a plan. A problem here stops the run before the first paid call."""
    problems: list[str] = []
    total = sum(b["seconds"] for b in story["beats"])
    if planned["seconds"] != total:
        problems.append(f"clips add up to {planned['seconds']} s, the story is {total} s")
    covered = [n for c in planned["clips"] for n in c["beats"]]
    if sorted(set(covered)) != list(range(1, len(story["beats"]) + 1)) or covered != sorted(covered):
        problems.append("every beat must be in a clip, in story order")
    narration = (planned.get("sound") or {}).get("narration")
    if narration and len(narration.get("prompt", "")) > PROMPT_LIMIT:
        problems.append(f"the narration is {len(narration['prompt'])} characters with its instructions, the limit is {PROMPT_LIMIT}")
    for part in (narration or {}).get("parts", []):
        if len(" ".join(part["lines"]).split()) > part["seconds"] * 3.2:
            problems.append(f"beat {part['beat']}: the narrator's lines are too long for {part['seconds']} s")
    problems += _last_frame_people(story, planned)
    made: set[int] = set()
    for clip in planned["clips"]:
        video = clip["steps"][-1]
        if clip.get("made_by") == "user":   # the user's clip: no model, no prompt
            made.add(clip["n"])
            continue
        if clip["seconds"] > MODEL_MAX_SECONDS[video["model"]]:
            problems.append(f"clip {clip['n']}: {clip['seconds']} s is more than {video['model']} makes in one call")
        if len(video["prompt"]) > PROMPT_LIMIT:
            problems.append(f"clip {clip['n']}: the prompt is {len(video['prompt'])} characters, the limit is {PROMPT_LIMIT}")
        for needed in clip["depends_on"]:
            if needed not in made:
                problems.append(f"clip {clip['n']}: needs clip {needed}, which is not made before it")
        made.add(clip["n"])
    return problems


HEX = re.compile(r"#[0-9A-Fa-f]{6}\b")
NAMED_HEX = re.compile(r"(\b[A-Za-z]+ )?(#[0-9A-Fa-f]{6})\b")   # "teal #0E7C86": the word before a code, if any


SOCIAL = re.compile(r"\b(reels?|ads?|advert\w*|promo\w*|commercial|tiktok|instagram|insta|social|shorts|story post)\b", re.I)
LATE_MUSIC = re.compile(r"\bthen\b[^.;]{0,40}\b(?:music|sting|jingle|tune)\b|\b(?:music|sting|jingle|tune)\b[^.;]{0,30}\b(?:after|once|as soon as)\b"
                        r"|\bmusic (?:comes|kicks|starts) in\b|\bsting\b", re.I)
SILENT = re.compile(r"\b(silent|no (music|sound|audio)|without (music|sound|audio)|mute[d]?)\b", re.I)
# An action the user gives its own length: "flies low for about 12 seconds".
NUMBER_WORDS = {w: n for n, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
    "eighteen nineteen twenty".split())}
NUMBER_WORDS["thirty"] = 30
TIMED_ACTION = re.compile(r"\b(?:for|lasting)\s+(?:(?:about|around|roughly|nearly|almost|some|a full|a good|at least|just over)\s+)?"
                          r"(\d+(?:\.\d+)?|" + "|".join(NUMBER_WORDS) + r")[- ](?:full )?(?:seconds?|secs?)\b", re.I)


FRAMING = re.compile(r"\b(extreme close[- ]up|close[- ]up|close|medium|wide|two[- ]shot|over[- ]the[- ]shoulder|"
                     r"from behind|aerial|overhead|top[- ]down)\b", re.I)




def beat_framing(text: str) -> frozenset[str] | None:
    """The framing a beat's text opens with ("Close two-shot: ...", "Wide shot from behind: ..."); empty if none.
    None when the text keeps the shot before ("Same two-shot: ...")."""
    head = text.split(":", 1)[0] if ":" in text[:50] else text[:40]
    if re.search(r"\bsame\b", head, re.I):
        return None
    words = {re.sub(r"[- ]up$|extreme ", "", m.lower()).replace("-", " ") for m in FRAMING.findall(head)}
    return frozenset(words)


def beat_actions(beat: dict) -> int:
    """About how many actions a beat holds: one, plus one for each ';' and each 'then' in its text. A beat with a spoken
    line adds only its ';' and 'then': the tool times the line itself (in testing, four beats of one woman talking held)."""
    text = beat.get("text", "")
    extra = text.count(";") + len(re.findall(r"\bthen\b", text, re.I))
    return extra if beat.get("spoken") else 1 + extra


# The notes on each tool and model. Findings only: each note is something a benchmark run measured. No advice that
# was not measured, and no picked defaults. Edit a note here when a new run changes it. The gates show them too.
# id: (tools or models it applies to, the finding, the evidence)
NOTES: dict[str, tuple[tuple[str, ...], str, str]] = {
    "ideas-per-call": (("create_video_from_text", "create_video_from_image", "ltx-2.5", "minimax-h3"),
                       "About two ideas hold in one call of about 15 s. More ideas in one call: the model plays the shots more than once and mixes them.",
                       "Old MCP: 4 shots in one 30 s call played 3 times. In testing, 4 seasons in one 20 s call played twice. "
                       "An 18 s call with 2 shots played them twice. Calls of 5 to 13 s with 1 or 2 beats held."),
    "ltx-words": (("ltx-2.5",),
                  "ltx-2.5 does not spell words it is asked to show in a scene.",
                  "0 of 4: \"walk anway\", \"13 awway\", \"walk anwwy\", \"Avalabile 12 Dembmer\" (end card)."),
    "minimax-words": (("minimax-h3",),
                      "minimax-h3 spelled the words it was asked to show.",
                      "2 of 2: \"walk anyway\" over a scene, an end card (old MCP). Two attempts only."),
    "still-words": (("generate_image", "qwen-image-2.1"),
                    "A still made with the exact words, then animated with image-to-video, keeps the words exact. The words stay on screen for the whole clip.",
                    "End cards from a still: exact in 3 runs. Another run: exact, on screen from the first frame to the last."),
    "minimax-sound": (("minimax-h3",),
                      "minimax-h3 adds music or sound even when audio is off.",
                      "In testing, guitar at -16 dB with audio off. Seasons runs: a music bed on silent requests. Joining with the sound removed made them silent (-91 dB)."),
    "ltx-sound": (("ltx-2.5",),
                  "ltx-2.5 is silent when audio is off.",
                  "In testing: -91 dB."),
    "regenerate-given-clip": (("create_video_from_text", "minimax-h3"),
                              "Giving an attached clip as a reference does not reproduce it: the result is a new video that resembles it.",
                              "In testing, the first 6 s did not match the original (similarity 0.43), and 12 s cost 288 credits."),
    "last-frame-continues": (("create_video_from_image", "ltx-2.5", "minimax-h3", "get_last_frame"),
                             "Image-to-video from the last frame of a clip continues it; joined after the clip, the cut is hard to see.",
                             "ltx-2.5: similarity across the join 0.98. minimax-h3: 0.88, a small jump. "
                             "minimax-h3 with the clip as a reference, no start frame: 0.41, a visible jump."),
    "end-frame-fits-between": (("create_video_from_image", "get_first_frame", "get_last_frame", "minimax-h3", "ltx-2.5"),
                               "A clip made to replace one between two others, with the clip before's last frame as the start image and "
                               "the clip after's first frame as `end_image_file_path`, joins both cuts smoothly. minimax-h3 kept the "
                               "planned action; ltx-2.5 made two jumps inside the clip and skipped the action.",
                               "A fox film, shot 2 replaced: cut after the new shot 0.99 on both models; before, from the start "
                               "image only, 0.52 (minimax-h3). Inside the new shot: minimax-h3 lowest 0.98 and the yawn shown; ltx-2.5 0.64 "
                               "and 0.65 (a jump to a close-up and back), no yawn. One run per model. Another run: the host used this path on its "
                               "own; cuts 1.00 and 0.94, where shot 3 itself changes 0.90 in its first 0.12 s (the fox moves fast)."),
    "last-frame-keeps-place": (("create_video_from_image", "get_last_frame"),
                               "A clip that starts from another clip's last frame keeps that clip's place, also when the prompt names a new place.",
                               "Two chained runs: winter stayed spring; the sea stayed behind the autumn bench."),
    "picture-resets-place": (("create_video_from_text", "minimax-h3"),
                             "With a picture reference in each call, two places in one 10 s call held, each in its own part.",
                             "A bottle, and a bottle with two people: 2 x 10 s, all four seasons in order."),
    "words-only-places-merge": (("ltx-2.5", "create_video_from_text"),
                                "Without a picture reference, two places described in one call merged into one place.",
                                "In testing (ltx-2.5, 2 x 10 s): cherry trees on a beach; autumn and winter never came."),
    "person-in-words": (("create_video_from_text", "create_video_from_image", "ltx-2.5", "minimax-h3"),
                        "A person described in words is the same inside one call and a look-alike across calls.",
                        "One call per season: coats and hair held, faces changed. Another run: Theo the same when his two seasons shared a call; Mara a look-alike in every split."),
    "small-letters": (("minimax-h3", "edit_image"),
                      "Letters on a product that is small in the frame change at 480p.",
                      "In testing, the bottle about 60 px wide in a 640 px edited picture; the letters were wrong in the hand shots and right in the bottle-alone shot."),
    "spoken-line": (("create_video_from_text", "ltx-2.5", "minimax-h3"),
                    "A spoken line in a clip with another beat was not said. The same line alone in a short clip was said word for word.",
                    "ltx-2.5, the line plus a walk-away in one 12 s clip: 0 of 3 runs. The line alone in a 4 s close-up: ltx-2.5 3 of 3, "
                    "minimax-h3 heard 2 of 2, word for word 1 of 2. Old MCP, one 30 s call: about 10 times."),
    "hex-codes": (("ltx-2.5", "minimax-h3", "create_video_from_text"),
                  "A colour code written in a prompt is drawn on screen as text.",
                  "In testing, \"#E78C28\" printed for 2 s; the codes were in the setting copied into every clip."),
    "grouping": ((), "The split rule keeps the same people in one clip up to 15 s.",
                 "Two dry runs: beats the host wrote as separate shots were merged; the spoken line was then lost."),
    "one-narration": (("create_production_sound", "generate_music", "join_production_clips"),
                      "One narration track and one music track across the joined film kept the script once and the music even.",
                      "In testing, with sound: 8 yes, 2 partly, 0 no; the old MCP's single call read the script 3 times and had no music."),
    "join-silent": (("join_production_clips", "join_videos"),
                    "Joining with the sound off removes any sound the clips have.",
                    "In testing: -91 dB after the join."),
}


def advise(story: dict, planned: dict, request: str | None = None) -> list[dict]:
    """Warnings on a plan, each with the run that showed it. They change nothing: the host decides. (Words in a scene,
    a spoken line with other beats and colour codes are `questions`, not advice.)

    The notes are in NOTES (findings only). `request`: the user's own words; a quoted phrase, the length or
    the shape that the story does not carry is reported as not carried."""
    out: list[dict] = []

    def warn(note: str, clip: int | None, said: str) -> None:
        out.append({"clip": clip, "note": note, "warning": said, "evidence": NOTES[note][2] if note in NOTES else ""})

    beats = story["beats"]
    if host_cuts(story) is None and len(planned["clips"]) > 0:
        warn("grouping", None, "You did not set `clip` on the beats, so the split rule grouped them (the same people stay in one "
                               "clip up to 15 s). Set `clip` on every beat to choose the clips yourself.")
    for clip in planned["clips"]:
        parts = [beats[n - 1] for n in clip["beats"]]
        model = clip["steps"][-1]["model"]
        if clip["kind"] == "card":
            continue
        places = {p.get("place") for p in parts if p.get("place")}
        ideas = sum(1 for k, p in enumerate(parts) if k == 0 or beat_link(p, parts[k - 1]) in ("moves", "new"))
        # The same limit as the split: 15 s, a pictured person 30 s
        if clip["seconds"] > clip_limit(story, parts) or ideas > 2:
            warn("ideas-per-call", clip["n"], f"Clip {clip['n']}: {clip['seconds']} s with {ideas} separate ideas in one call.")
        # Prompt guides: one shot and few actions per clip; separate subject moves, camera moves and cuts
        shots, at = [], 0
        for p in parts:
            framing = beat_framing(p.get("text", ""))
            if framing and (not shots or framing != shots[-1][0]):
                shots.append((framing, at))
            at += p["seconds"]
        if len(shots) > 1:
            seen = ", then ".join(f"{' '.join(sorted(f))} at {t} s" for f, t in shots)
            out.append({"clip": clip["n"], "note": "shot-change", "evidence": (
                "In testing, clip 2: close two-shot, umbrella, wide from behind in one 10 s call; a second open "
                "umbrella came in front of the camera."),
                "warning": f"Clip {clip['n']} changes the shot inside one call ({seen}). The model turns a new shot inside a "
                           "clip into an unclear move. Give each shot its own clip: the next clip starts from this clip's "
                           "last frame."})
        actions = sum(beat_actions(p) for p in parts)
        if actions > 3:
            out.append({"clip": clip["n"], "note": "actions-per-clip", "evidence": (
                "In testing, clip 3: seven actions in one 12 s beat; Wilbur rose into the air with the plane. "
                "Another run, three actions in the same 12 s: he stayed on the sand."),
                "warning": f"Clip {clip['n']} holds about {actions} actions in {clip['seconds']} s. After three, the model merges "
                           "or drops actions. Give each action its own beat, at most three in a clip, and say where each "
                           "person ends (lets go, stays on the sand)."})
        # That evidence was ltx-2.5 only; minimax-h3 is now the scene model
        if model == "ltx-2.5" and len(places) > 1 and clip["reference"]["kind"] not in ("picture", "file"):
            warn("words-only-places-merge", clip["n"], f"Clip {clip['n']} has {len(places)} places and no picture reference.")
        # new_place: the planner chose this chain and ends the clip before close on the person
        if clip["reference"]["kind"] == "last_frame" and clip["reference"].get("of_clip") and not clip["reference"].get("new_place"):
            before = next((c for c in planned["clips"] if c["n"] == clip["reference"]["of_clip"]), None)
            if before:
                old = beats[before["beats"][-1] - 1].get("place")
                if old and places and old not in places:
                    warn("last-frame-keeps-place", clip["n"], f"Clip {clip['n']} starts from clip {before['n']}'s last frame "
                                                              f"({old}) but takes place in {', '.join(sorted(places))}.")
    subjects = {s["id"]: s for s in story.get("subjects", [])}
    for sid, subject in subjects.items():
        if subject["role"] != "product":
            continue
        shown = [c for c in planned["clips"] if c["kind"] != "card" and any(sid in beats[n - 1].get("subjects", []) for n in c["beats"])]
        if subject.get("picture"):
            used = [c["n"] for c in shown if c["reference"]["kind"] == "picture" and c["reference"].get("subject") == sid]
            if used:
                out.append({"clip": used[0], "note": "product-photo-scene", "evidence": "In testing, the kitchen behind the user's bottle stayed in a studio shot.",
                            "warning": f"Clip(s) {', '.join(map(str, used))} start from the user's photo of {sid}; the model keeps the photo's "
                                       "background. If the scene is somewhere else, first make a still of the product in that scene "
                                       "(edit_image with the photo, or remove_image_background) and give it as `reference.file`."})
        # No "product-no-picture" advice. It said to make a still with generate_image, against
        # "never a picture made by an image model". The picture-<id> question covers it.
    if request and story.get("music") and not story.get("music_from_beat") and LATE_MUSIC.search(request):
        out.append({"clip": None, "note": "music-later", "evidence": "In testing, 'then a soft music sting' ran as a bed from 0 s.",
                    "warning": "The user's words may place the music after something (a whoosh, the reveal, a line): '"
                               + LATE_MUSIC.search(request).group(0) + "'. If so, set `music_from_beat` to the beat it starts on; "
                               "without it the music runs from the start of the film."})
    if request and not story.get("music") and SOCIAL.search(request) and not SILENT.search(request):
        out.append({"clip": None, "note": "music-default", "evidence": "In testing, a reel was made silent; the user expected sound.",
                    "warning": "This is a reel, an ad or a social post, and the story has no `music`. Such videos get music unless the "
                               "user asks for silence: set `music` (a mood and instruments), or tell the user it is silent."})
    if request:
        total = sum(b["seconds"] for b in beats)
        scenes = [b["seconds"] for b in beats if b.get("kind", "scene") != "card"]
        for m in TIMED_ACTION.finditer(request):
            word = m.group(1).lower()
            n = float(NUMBER_WORDS.get(word, word))
            if abs(n - total) <= 1 or any(abs(s - n) <= 1 for s in scenes):   # the film's own length, or a beat of it
                continue
            start = max(request.rfind(c, 0, m.start()) for c in ".;:!?\n") + 1
            said = request[start:m.end()].strip()
            out.append({"clip": None, "note": "timed-action", "evidence": (
                "In testing, 'flies low for about 12 seconds' was written as an 8 s and a 4 s beat with the take-off "
                "and the landing inside them; about 3 s of flight. A prompt with 0-2 s take-off, 2-14 s flight and 14-16 s "
                "landing gave about 12 s of flight (direct test)."),
                "warning": f"The user gives one action its own length: '{said}'. No beat is about {n:g} s long. Make that "
                           f"action one beat of {n:g} s. Its start and its end (a take-off, a landing) are beats of their own, "
                           "before and after it. When the start and the end are in the same beat, the model gives the action "
                           "only a part of the time."})
    for n, beat in enumerate(beats, start=1):
        words = len(" ".join(beat.get("says") or []).split())
        need = round(words / VOICE_WORDS_PER_SECOND + 0.1, 1)   # the join can start the words 0.1 s into the beat
        if words and need > beat["seconds"]:
            out.append({"clip": None, "note": "voice-over-pace", "evidence": "In testing, 12 words in a 4 s beat; the last words ran past the film's end.",
                        "warning": f"Beat {n}: the voice-over has {words} words for {beat['seconds']} s. The voice reads about "
                                   f"{VOICE_WORDS_PER_SECOND} words a second, so it needs about {need} s. Cut words or make the beat longer. "
                                   "If not, the join moves the words earlier, reads them up to 15% faster, and holds the last frame."})
        said = 0 if beat.get("source") else len(" ".join(beat.get("spoken") or []).split())   # the user's clip: only captioned
        need = math.ceil(said / SPOKEN_WORDS_PER_SECOND) + 1   # 1 s after the line for the clear end
        if said and need > beat["seconds"]:
            out.append({"clip": None, "note": "spoken-pace", "evidence": "In testing, 18 words in a 6 s slot; the line ran over and the model made up words after it.",
                        "warning": f"Beat {n}: the spoken line has {said} words for {beat['seconds']} s. People on screen say about "
                                   f"{SPOKEN_WORDS_PER_SECOND} words a second, plus 1 s to finish, so it needs about {need} s. Make the "
                                   "beat longer or cut words. If not, the line may run past the beat and the model may make up words."})
        idle = beat["seconds"] - sum(max(2, math.ceil(len(s.split()) / SPOKEN_WORDS_PER_SECOND)) for s in beat.get("spoken") or [])
        if said and idle > SPOKEN_QUIET_SECONDS:
            out.append({"clip": None, "note": "spoken-idle", "evidence": "In testing, a person idle on camera for 7 s or more after the line talked again in 3 of 3 clips.",
                        "warning": f"Beat {n}: the spoken line leaves about {idle} s after it with nothing to do. A person idle on camera "
                                   f"after a line often starts to talk again. Make the beat shorter (at most {SPOKEN_QUIET_SECONDS} s after the line), or put "
                                   "the next action in a new beat. If not, the tool fills the time with a slow breath and a glance down."})
        if beat.get("source") and beat.get("says") and beat.get("source_sound", True) is not False and beat.get("source_speech") is not False:
            out.append({"clip": None, "note": "voice-over-on-speech", "evidence": "",
                        "warning": f"Beat {n}: a voice-over on the user's clip, and the clip keeps its own sound. If someone speaks in "
                                   "the clip, both voices play at once. Set `source_speech` false if its sound is only background, "
                                   "or `source_sound` false to drop it."})
    if request:
        from .plan_request import brief_items

        story_words = _letters(" ".join(
            [w for b in beats for w in (b.get("exact_words") or []) + (b.get("says") or []) + (b.get("spoken") or [])]))
        total = sum(b["seconds"] for b in beats)
        for item in brief_items(request, []):
            missing = None
            if item["kind"] == "words" and _letters(item["value"]) not in story_words:
                missing = f"The request quotes \"{item['value']}\", and no beat carries it in exact_words, says or spoken."
            elif item["kind"] == "seconds" and abs(int(item["value"]) - total) > 1:
                missing = f"The request asks for {item['value']} s; the beats add up to {total} s."
            elif item["kind"] == "aspect" and item["value"] != story["aspect"]:
                missing = f"The request asks for {item['value']}; the story is {story['aspect']}."
            if missing:
                out.append({"clip": None, "note": "not-carried", "warning": missing, "evidence": ""})
        # A card only when the user asks for one, checked by code so a vague request gets it too.
        # A card that an answer to a words question made (words_route "card") was chosen, not guessed.
        if not re.search(r"\bcards?\b", request, re.I):
            for n, beat in enumerate(beats, start=1):
                if beat.get("kind") == "card" and beat.get("words_route") != "card":
                    out.append({"clip": None, "note": "card-not-asked", "evidence": (
                        "In an ablation test, with no card rule the host made an unasked title card in 3 of 4 runs."),
                        "warning": f"Beat {n} is a card, and the request does not ask for a card. Make it a scene beat with "
                                   "its words in `exact_words`, unless the user asked for a card later in the chat."})
    return out


# Plain names for colour codes: a code in a prompt is drawn on screen as text.
COLOUR_NAMES = {
    "black": (0, 0, 0), "white": (255, 255, 255), "grey": (128, 128, 128), "red": (200, 30, 30), "dark red": (120, 20, 20),
    "orange": (240, 140, 40), "yellow": (240, 210, 40), "green": (40, 160, 60), "dark green": (20, 90, 40), "teal": (14, 124, 134),
    "turquoise": (60, 200, 200), "blue": (40, 90, 200), "navy": (20, 30, 90), "light blue": (140, 190, 240), "purple": (110, 40, 160),
    "deep purple": (59, 31, 107), "pink": (240, 140, 180), "magenta": (200, 40, 160), "brown": (120, 80, 40), "beige": (220, 200, 160),
    "gold": (210, 170, 60), "silver": (190, 190, 200), "cream": (250, 240, 220),
}


def colour_name(code: str) -> str:
    r, g, b = int(code[1:3], 16), int(code[3:5], 16), int(code[5:7], 16)
    return min(COLOUR_NAMES, key=lambda k: sum((x - y) ** 2 for x, y in zip(COLOUR_NAMES[k], (r, g, b))))


def questions(story: dict, planned: dict) -> list[dict]:
    """The questions a plan must answer before it is made: risks with a known better way. Ids stay the same while the
    story is the same, so the answers can go back with it. Each has a recommendation; the host decides."""
    out: list[dict] = []
    beats = story["beats"]
    for n, beat in enumerate(beats, start=1):
        if beat.get("kind", "scene") == "scene" and beat.get("exact_words") and not beat.get("words_route"):
            words = " and ".join(f'"{w}"' for w in beat["exact_words"])
            out.append({
                "id": f"words-{n}", "beat": n,
                "ask": (f"Beat {n} asks for the words {words} on screen over the scene. A video model asked to spell words in a scene: "
                        "ltx-2.5 got them wrong 0 of 4 times; minimax-h3 right 2 of 2. How should they be made? (Words that are "
                        "part of the picture, such as a label or a sign, go in `in_scene_words` instead: no question, best effort.)"),
                "options": {
                    "overlay": "The scene is made without words, and the join draws the words over it by code for the beat's seconds. Always exact; the beat's `words_look` (bold by default).",
                    "card": "A card of its own right after this beat, 2 s taken from the beat. The words are not over the scene. Made in the film's style by the picture model, which may misspell them.",
                    "model": "minimax-h3 makes this beat's clip and draws the words in the scene. Looks best; 2 of 2 so far; check the frame after.",
                    "still": "A picture of the scene with the words is made first and the clip starts from it. The picture model may misspell them; the words stay on screen for the whole clip.",
                    "accept": "The scene model draws the words as well as it can. They may be misspelled.",
                },
                "recommend": "overlay",
            })
    for clip in planned.get("clips", []):
        # The 0 of 3 was ltx-2.5. minimax-h3 said two
        # lines in one clip 4 of 4 (sound test), so the question is only for an ltx-2.5 clip.
        model = (clip.get("steps") or [{}])[-1].get("model")
        if clip["kind"] == "card" or len(clip["beats"]) < 2 or model != "ltx-2.5":
            continue
        for n in clip["beats"]:
            if beats[n - 1].get("spoken") and not beats[n - 1].get("spoken_route"):
                out.append({
                    "id": f"line-{n}", "beat": n,
                    "ask": (f"Beat {n} has a spoken line and shares clip {clip['n']} with {len(clip['beats']) - 1} other beat(s). "
                            "With another beat in the clip the line was said 0 of 3 times; alone in a short clip, 3 of 3."),
                    "options": {"split": "Give beat {0} a clip of its own.".format(n), "keep": "Keep the clip. The line may not be said."},
                    "recommend": "split",
                })
    for n, beat in enumerate(beats, start=1):
        before = beats[n - 2] if n > 1 else None
        if _held_after_reveal(story, beat, before):
            out.append({
                "id": f"held-{n}", "beat": n,
                "ask": (f"Beat {n} holds the picture still right after beat {n - 1}, which already makes it move. A held still "
                        "after a reveal reads as a pasted picture, and it jumped in size at the cut."),
                "options": {"merge": f"Drop the held card: beat {n - 1} runs {beat['seconds']} s longer and ends on the picture; the card's words go over it.",
                            "keep": "Keep the card: generated from the picture, which may change a little. Choose it when the user asked for an end card."},
                # An end card the user asked for stays; a still after a reveal goes.
                "recommend": "keep" if "card" in beat.get("text", "").lower() else "merge",
            })
        # As few clips as possible, split only when needed: also an `event` beat that shares a
        # person with the beat before. The same person is one idea and stays in one clip while it fits.
        # A process with no person is not joined.
        link = beat.get("link")
        shared = bool(people(story, [beat]) & people(story, [before])) if before else False
        if (beat.get("clip") and before and before.get("clip") and beat["clip"] != before["clip"]
                and (link == "continues" or (link == "event" and shared))
                and beat.get("kind", "scene") == "scene" and before.get("kind", "scene") == "scene" and not before.get("source")
                and not beat.get("together_route")):   # the user's own clip is never generated, so it cannot be joined
            group = [b for b in beats if b.get("clip") == before["clip"]] + [beat]
            together = sum(b["seconds"] for b in group)
            # a line the host split off on an ltx-2.5 clip (line-<n>) stays split
            if together <= clip_limit(story, group) and not any(b.get("spoken_route") == "split" for b in group):
                why = (f"Beat {n} continues the same shot as beat {n - 1}, but they are in different clips. A cut in the "
                       "middle of one motion shows as a jump.") if link == "continues" else (
                       f"Beat {n} has the same place and person as beat {n - 1}, but they are in different clips. The same "
                       "person is one idea: one clip while it fits, which also keeps the face and the place the same.")
                out.append({
                    "id": f"together-{n}", "beat": n,
                    "ask": why,
                    "options": {"join": f"Put beat {n} in clip {before['clip']} ({together} s).", "keep": "Keep the cut."},
                    "recommend": "join",
                })
    # In testing, with no photo, the host made "reference" pictures with an image model and they invented the
    # products. Real photos as likeness guides made both devices right in every shot.
    answered = story.get("picture_answers") or {}
    for subject in story.get("subjects", []):
        if subject["role"] == "product" and not subject.get("picture") and subject["id"] not in answered:
            out.append({
                "id": f"picture-{subject['id']}", "beat": None,
                "ask": (f"\"{subject['id']}\" is a product with no photo. The video model draws a product it only knows by "
                        "name wrongly. A real photo as its `picture` keeps it right in every clip. When the user chose a "
                        "look-alike made from words, answer none: the user's choice wins."),
                "options": {"web": "A real, sold product: find a free-licence photo on the web (for example Wikimedia "
                                   "Commons), upload it, set it as the subject's `picture`, and tell the user its source and licence.",
                            "user": "Ask the user for a photo (their own product, or no free photo found), then set it as `picture`.",
                            "none": "No photo: the product is made up, its exact look does not matter, or the user chose a "
                                    "look-alike made from words (the conversation's product_picture answer 'words')."},
                "recommend": "web",
            })
    scene_clips = [c for c in planned.get("clips", []) if c["kind"] == "scene"]
    if len(scene_clips) >= 2 and not story.get("continuity_checked"):
        out.append({
            "id": "continuity", "beat": None,
            "ask": ("Check the plan before any clip is made. Does any of these happen? (1) A motion or a thing that should "
                    "stay continuous (a logo forming, one action that goes on, the same object) is cut into two clips, or "
                    "followed by a card that shows it still. Keep it in one clip; split only when it does not fit or the "
                    "next action does not depend on it. (2) Two separate actions share one clip "
                    "with no cut between them: the second one was never made. Give each its own clip, or write them "
                    "as two shots with a hard cut: \"CUT at 6 s\", a new camera angle, the first action's tool out of frame."),
            "options": {"ok": "None of these happens.", "change": "One happens: change the beats or `clip` numbers and plan again."},
            "recommend": "ok",
        })
    texts = [story.get("setting", "")] + [b.get("text", "") for b in beats if b.get("kind", "scene") == "scene"]
    codes = sorted({c for t in texts for c in HEX.findall(t)})
    if codes and not story.get("colour_codes"):
        out.append({
            "id": "colour-codes", "beat": None,
            "ask": (f"The setting or a scene names colours by code ({', '.join(codes)}). A code in a prompt was drawn on screen as text."),
            "options": {"words": "Write them as colour words: " + ", ".join(f"{c} as {colour_name(c)}" for c in codes) + ".",
                        "keep": "Keep the codes."},
            "recommend": "words",
        })
    return out


def _held_after_reveal(story: dict, beat: dict, before: dict | None) -> bool:
    """A card that holds the picture still right after the scene beat that is made from the same picture."""
    return bool(beat.get("kind") == "card" and beat.get("picture") and before and before.get("kind", "scene") == "scene"
                and beat["picture"] in _beat_pictures(story, before) and not beat.get("held_route"))


def held_merges(story: dict) -> dict[str, str]:
    """The held cards the plan merges into the scene before, with no question: {"held-N": "merge"}.

    The video makes the logo from its reference picture, in one clip. A held still after the
    reveal reads as a pasted picture. Hosts kept the card when they were asked ("keep", the user's "stays exactly
    as in the file") or went around the question. An end card the user asked for ("card" in the beat's text)
    stays a question."""
    beats, out = story["beats"], {}
    for n, beat in enumerate(beats, start=1):
        if _held_after_reveal(story, beat, beats[n - 2] if n > 1 else None) and "card" not in beat.get("text", "").lower():
            out[f"held-{n}"] = "merge"
    return out


def _beat_pictures(story: dict, beat: dict) -> set[str]:
    """The pictures a scene beat is made from: its subjects' pictures and its own file reference."""
    by_id = {s["id"]: s for s in story.get("subjects", [])}
    out = {by_id[s]["picture"] for s in beat.get("subjects", []) if s in by_id and by_id[s].get("picture")}
    if (beat.get("reference") or {}).get("file"):
        out.add(beat["reference"]["file"])
    return out


def apply_answers(story: dict, answers: dict[str, str]) -> tuple[dict, list[str]]:
    """The story changed the way the chosen options say. Returns (story, problems with the answers)."""
    story = deepcopy(story)
    beats, problems = story["beats"], []
    groups = [b.get("clip") for b in beats]   # the host's grouping, kept while beats are added or split off
    inserts: list[tuple[int, dict]] = []
    drops: list[int] = []   # beats removed by an answer (index), after the answers are read
    for key, choice in answers.items():
        kind, _, num = key.partition("-")
        if key == "colour-codes":
            if choice == "words":
                swap = lambda text: NAMED_HEX.sub(lambda m: m.group(1).rstrip() if m.group(1) and m.group(1).strip().lower() == colour_name(m.group(2)) else (m.group(1) or "") + colour_name(m.group(2)), text)
                story["setting"] = swap(story.get("setting", ""))
                for b in beats:
                    if b.get("kind", "scene") == "scene":
                        b["text"] = swap(b["text"])
            story["colour_codes"] = choice
            continue
        if key == "continuity":
            if choice == "ok":
                story["continuity_checked"] = True
            elif choice == "change":
                problems.append("continuity: change the beats or `clip` numbers, then plan again without this answer")
            else:
                problems.append("continuity: choose ok or change")
            continue
        if kind == "picture" and num:
            ids = {x["id"]: x for x in story.get("subjects", [])}
            if num not in ids:
                problems.append(f"no question {key}")
            elif choice == "none":
                story.setdefault("picture_answers", {})[num] = "none"
            elif choice in ("web", "user"):
                if not ids[num].get("picture"):
                    where = ("a free-licence photo from the web" if choice == "web" else "the user's photo")
                    problems.append(f"{key}: upload {where} and set it as `picture` on subject {num}, then plan again "
                                    f"(or answer none). Never a picture made by an image model.")
            else:
                problems.append(f"{key}: choose web, user or none")
            continue
        if key == "voice":
            if choice == "video":
                story["voice_route"] = "video"
            elif choice == "sample":
                pass   # with no recording, the preset voice from pick_voice reads it
            else:
                problems.append("voice: choose sample or video")
            continue
        if not num.isdigit() or not 1 <= int(num) <= len(beats):
            problems.append(f"no question {key}")
            continue
        beat = beats[int(num) - 1]
        if kind == "words":
            if choice not in WORD_ROUTES:
                problems.append(f"{key}: choose one of {', '.join(WORD_ROUTES)}")
                continue
            beat["words_route"] = choice
            if choice == "model":
                beat["model"] = REFERENCE_MODEL
            if choice == "card":
                take = min(2, beat["seconds"] - 1) if beat["seconds"] > 1 else 0
                words = beat.pop("exact_words")
                beat["seconds"] -= take
                # A card is generated (qwen-image-2.1 spells the quoted
                # words), not drawn by code, also with an exact colour or a picture.
                inserts.append((int(num), {"seconds": take or 1, "kind": "card", "exact_words": words, "words_route": "card",
                                           "text": "A designed title card in the film's style: bold, clean lettering reading exactly "
                                                   + " and ".join(f'"{w}"' for w in words) + ", large and centred.",
                                           "_own": True}))
        elif kind == "held":
            if choice not in ("merge", "keep"):
                problems.append(f"{key}: choose merge or keep")
                continue
            beat["held_route"] = choice
            if choice == "merge" and int(num) > 1:
                before = beats[int(num) - 2]
                before["seconds"] += beat["seconds"]
                before["exact_words"] = before.get("exact_words", []) + beat.get("exact_words", [])
                before["text"] = before["text"].rstrip() + " It ends on the finished picture, held centred and still."
                drops.append(int(num) - 1)
        elif kind == "together":
            if choice not in ("join", "keep"):
                problems.append(f"{key}: choose join or keep")
                continue
            beat["together_route"] = choice
            if choice == "join" and int(num) > 1:
                groups[int(num) - 1] = groups[int(num) - 2]
        elif kind == "line":
            if choice not in ("split", "keep"):
                problems.append(f"{key}: choose split or keep")
                continue
            beat["spoken_route"] = choice
            if choice == "split":
                beat["_own"] = True
        else:
            problems.append(f"no question {key}")
    for at in sorted(drops, reverse=True):
        del beats[at]
        del groups[at]
        inserts = [(i - 1 if i > at else i, c) for i, c in inserts]
    for at, card in sorted(inserts, reverse=True):
        beats.insert(at, card)
        groups.insert(at, None)
    if all(g is not None for g in groups if g is not None) and any(g is not None for g in groups):
        _renumber(beats, groups)
    else:
        for b in beats:
            b.pop("_own", None)
    if (inserts or drops) and "seconds" in story:
        story["seconds"] = sum(b["seconds"] for b in beats)
    return story, problems


def _renumber(beats: list[dict], groups: list) -> None:
    """Clip numbers 1, 2, 3 ... from the host's groups; a card and a beat split off get a clip of their own."""
    n, previous = 0, object()
    for beat, group in zip(beats, groups):
        own = beat.pop("_own", False) or beat.get("kind") == "card"
        if own or group != previous or previous == "own":
            n += 1
        beat["clip"] = n
        previous = "own" if own else group


def _letters(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def check_outputs(planned: dict, measured: list[dict], tolerance: float = 1.0) -> list[str]:
    """Gates on the files that came back. `measured` is one {n, seconds, width, height} per clip, from ffprobe."""
    problems: list[str] = []
    by_n = {m["n"]: m for m in measured}
    sizes = set()
    for clip in planned["clips"]:
        got = by_n.get(clip["n"])
        if got is None:
            problems.append(f"clip {clip['n']}: no file")
            continue
        if abs(got["seconds"] - clip["seconds"]) > tolerance:
            problems.append(f"clip {clip['n']}: {got['seconds']:.1f} s, planned {clip['seconds']} s")
        sizes.add((got["width"], got["height"]))
    if len(sizes) > 1:
        problems.append("clips have different sizes: " + ", ".join(f"{w}x{h}" for w, h in sorted(sizes)))
    return problems
