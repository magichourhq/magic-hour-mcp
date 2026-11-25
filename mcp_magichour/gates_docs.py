"""The two documents the gates make the host read: simple against hard, and the notes for each tool and model.

The notes are video_production.NOTES: the film advice reads them too.
"""

from __future__ import annotations

from .video_production import NOTES

COMPARISON = """\
SIMPLE: every item below is true.
- One idea: one subject doing one thing in one place (a robot waters a sunflower).
- At most 15 seconds.
- No exact words that must appear on screen, and no spoken line that must be said word for word.
- At most one attached file, used as the look or the start of the video.
- Not a continuation or an edit of an attached video.

HARD: any item below is true.
- Two or more ideas: different places, different people at different times, shots listed in order, a card or end card.
- Longer than 15 seconds.
- Exact words on screen (a title, an end card, captions) or an exact spoken line or voice-over script.
- Two or more attached files, or a person and a product that must both stay the same.
- Continue, extend or edit an attached video, keeping part of it.
- Music or a voice-over across several shots.
"""


def notes_for(names: set[str]) -> dict[str, dict]:
    """The notes that apply to any of the tools or models named."""
    return {i: {"applies_to": [a for a in on if a in names], "finding": finding, "evidence": evidence}
            for i, (on, finding, evidence) in NOTES.items() if names & set(on)}
