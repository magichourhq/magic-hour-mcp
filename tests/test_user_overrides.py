"""Best-practice rules apply when the user says nothing; the user's words override them."""

import unittest

from mcp_magichour.video_production_tool import plan_story


def story(beats: list[dict], **extra) -> dict:
    return {"title": "t", "aspect": "16:9", "setting": "A rainy bus stop at dusk.",
            "subjects": [{"id": "maya", "words": "Maya, a young woman in a yellow raincoat.", "role": "person"}],
            "beats": [{"subjects": ["maya"], "place": "stop", **b} for b in beats], **extra}


SCENE = {"clip": 1, "seconds": 4, "text": "Close shot: Maya looks at the sky."}
CARD = {"clip": 2, "seconds": 3, "kind": "card", "subjects": [], "text": "A plain dark blue card.", "exact_words": ["Rain Day"]}


class CardMotionTests(unittest.TestCase):
    def card(self, **extra):
        planned = plan_story(story([SCENE, dict(CARD, **extra)]))
        clip = next(c for c in planned["clips"] if c["kind"] == "card")
        line = next(l for l in planned["checklist"] if l["id"] == f"card-{clip['n']}")
        return clip["steps"][-1]["prompt"], line["want"]

    def test_a_card_holds_still_when_the_user_says_nothing(self):
        prompt, want = self.card()
        self.assertIn("camera does not move", prompt)
        self.assertTrue(want.startswith("Card held"))

    def test_a_card_moves_when_the_user_asks(self):
        prompt, want = self.card(card_motion="The title slides in from the left.")
        self.assertIn("The title slides in from the left.", prompt)
        self.assertNotIn("camera does not move", prompt)
        self.assertIn("Rain Day", want)
        self.assertIn("moving (The title slides in from the left)", want)


class MusicVocalsTests(unittest.TestCase):
    def music(self, **extra):
        return plan_story(story([SCENE], music="soft piano", **extra))["sound"]["music"]

    def test_the_music_is_instrumental_when_the_user_says_nothing(self):
        self.assertTrue(self.music()["instrumental"])

    def test_the_music_sings_when_the_user_asks(self):
        self.assertFalse(self.music(music_vocals=True)["instrumental"])


if __name__ == "__main__":
    unittest.main()
