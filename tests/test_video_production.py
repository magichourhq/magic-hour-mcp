"""The clip splitter: pure code, no network, no credits."""

import json
import os
import unittest
from unittest.mock import patch
from pathlib import Path

from mcp_magichour.video_production import (
    PROMPT_LIMIT,
    choose_reference,
    count_ideas,
    needs_production,
    split,
    validate_story,
)

STORIES = Path(__file__).parent / "fixtures" / "stories"


def story(prefix):
    return json.loads(next(STORIES.glob(f"{prefix}-*.json")).read_text())


def shape(clips):
    """[(beats, seconds)] for each clip."""
    return [(c["beats"], c["seconds"]) for c in clips]


# What each split rule must give for each story: {story: {(strategy, max_seconds): [(beats, seconds), ...]}}
EXPECTED = {
    "c1": {  # one scene, one continuous action, 30 s
        ("one", 15): [([1, 2, 3, 4, 5, 6], 30)],
        ("time", 15): [([1, 2, 3], 15), ([4, 5, 6], 15)],
        ("idea", 15): [([1, 2, 3], 15), ([4, 5, 6], 15)],
        ("idea", 30): [([1, 2, 3, 4, 5, 6], 30)],
        ("shot", 15): [([n], 5) for n in range(1, 7)],
    },
    "c2": {  # same place and people, a new event, 14 s
        ("idea", 15): [([1, 2], 14)],
        ("event", 15): [([1], 7), ([2], 7)],
    },
    "c3": {  # three separate ideas, 15 s
        ("time", 15): [([1, 2, 3], 15)],
        ("idea", 15): [([1], 5), ([2], 5), ([3], 5)],
    },
    "c4": {  # card, four shots of one place, card: 30 s
        ("idea", 30): [([1], 2), ([2, 3, 4, 5], 26), ([6], 2)],
        ("idea", 15): [([1], 2), ([2, 3], 13), ([4, 5], 13), ([6], 2)],
        ("event", 15): [([1], 2), ([2], 7), ([3], 6), ([4], 6), ([5], 7), ([6], 2)],
    },
    "c5": {  # bottle, presenter with bottle (two shots), card: 30 s
        ("idea", 30): [([1], 8), ([2, 3], 18), ([4], 4)],
        ("idea", 15): [([1], 8), ([2], 9), ([3], 9), ([4], 4)],
        ("event", 15): [([1], 8), ([2], 9), ([3], 9), ([4], 4)],
    },
}


class SplitTests(unittest.TestCase):
    def test_each_strategy_gives_the_expected_clips(self):
        for prefix, cases in EXPECTED.items():
            for (strategy, max_seconds), want in cases.items():
                with self.subTest(story=prefix, strategy=strategy, max_seconds=max_seconds):
                    self.assertEqual(shape(split(story(prefix), strategy, max_seconds)), want)

    def test_a_card_is_always_its_own_clip(self):
        for strategy in ("time", "idea", "event", "shot"):
            with self.subTest(strategy=strategy):
                clips = split(story("c4"), strategy, 30)
                self.assertEqual([c["kind"] for c in clips][0], "card")
                self.assertEqual([c["kind"] for c in clips][-1], "card")
                self.assertTrue(all(len(c["beats"]) == 1 for c in clips if c["kind"] == "card"))

    def test_a_beat_longer_than_the_limit_is_cut_into_parts_that_continue(self):
        s = story("c1")
        s["beats"] = [{**s["beats"][0], "seconds": 40}]
        s["seconds"] = 40
        clips = split(s, "idea", 15)
        self.assertEqual([c["seconds"] for c in clips], [14, 13, 13])
        self.assertEqual([c["link"] for c in clips], ["new", "continues", "continues"])

    def test_unknown_strategy_is_refused(self):
        with self.assertRaises(ValueError):
            split(story("c1"), "vibes", 15)


class PersonFirstTests(unittest.TestCase):
    """Priority 1: a pictured person's beats stay in one call. Priority 2: a new idea is a new clip."""

    def test_people_without_a_picture_follow_the_idea_rule(self):
        self.assertEqual(shape(split(story("c4"), "person", 15)), shape(split(story("c4"), "idea", 15)))

    def long_presenter_story(self):
        s = story("c5")
        s["beats"][1]["seconds"] = s["beats"][2]["seconds"] = 18   # 36 s of the presenter: more than one call holds
        s["seconds"] = 48
        return s


class TriggerTests(unittest.TestCase):
    def test_over_15_seconds_or_three_ideas_needs_production(self):
        self.assertTrue(needs_production(story("c1")))   # 30 s, one idea
        self.assertTrue(needs_production(story("c3")))   # 15 s, three ideas
        self.assertTrue(needs_production(story("c4")))
        self.assertTrue(needs_production(story("c5")))

    def test_one_short_scene_does_not(self):
        self.assertFalse(needs_production(story("c2")))  # 14 s, one idea
        self.assertEqual(count_ideas(story("c2")), 1)
        self.assertEqual(count_ideas(story("c3")), 3)


if __name__ == "__main__":
    unittest.main()
