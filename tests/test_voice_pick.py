"""The narrator's preset voice comes from the host's profile, matched to Magic Hour's tags."""

import unittest

from mcp_magichour.video_production import pick_voice
from mcp_magichour.voices import AGES, GENDERS, STYLES, VOICES


def tags(story):
    return pick_voice(story)["tags"]


class VoicePickTest(unittest.TestCase):
    def test_the_list_holds_only_tagged_english_presets(self):
        self.assertGreater(len(VOICES), 300)
        for v in VOICES:
            self.assertEqual((v[2] in GENDERS, v[3] in AGES, v[4] in STYLES), (True, True, True), v[0])
            self.assertTrue(v[6].startswith("https://videos.magichour.ai/presets/voices/"), v[0])
        self.assertEqual(len({v[0] for v in VOICES}), len(VOICES))

    def test_the_profile_decides_gender_age_and_style(self):
        for gender in GENDERS:
            for style in ("Narration", "Social Media", "Wellness", "Advertising"):
                got = tags({"narrator_voice": {"gender": gender, "age": "Young", "style": style}})
                self.assertTrue(got.startswith(gender + ","), got)
                self.assertTrue(got.endswith(style), got)

    def test_a_named_accent_beats_another_accent(self):
        # In testing, "neutral American accent" got a West African voice
        story = {"narrator": "soft, warm and soothing; slow, gentle pace; neutral American accent",
                 "narrator_voice": {"gender": "Female", "age": "Middle-aged", "style": "Narration"}}
        self.assertNotEqual(pick_voice(story)["id"], "cv-0053")
        british = {"narrator": "calm, British", "narrator_voice": {"gender": "Male", "age": "Older", "style": "Narration"}}
        got = next(v for v in VOICES if v[0] == pick_voice(british)["id"])
        self.assertIn("British", got[5])

    def test_no_profile_falls_back_to_the_narrator_words(self):
        self.assertEqual(pick_voice({})["id"], "cv-0001")   # Ashley: woman, middle-aged, Conversational
        self.assertTrue(tags({"narrator": "a deep, calm man's voice"}).startswith("Male,"))

    def test_a_voice_the_user_names_wins(self):
        self.assertEqual(pick_voice({"voice": "cv-0118", "narrator_voice": {"gender": "Male", "age": "Older", "style": "Narration"}})["id"], "cv-0118")
        self.assertEqual(pick_voice({"voice": "not-a-voice"})["id"], "cv-0001")


if __name__ == "__main__":
    unittest.main()
