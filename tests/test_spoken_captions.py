"""Captions of the lines people on screen say, beside the voice-over's (two caption sources)."""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from mcp_magichour import media_tools
from mcp_magichour import video_production as vp
from mcp_magichour.video_production_tool import plan_story


def story(beats: list[dict], **extra) -> dict:
    return {"title": "t", "aspect": "16:9", "setting": "A rainy bus stop at dusk.",
            "subjects": [{"id": "maya", "words": "Maya, a young woman in a yellow raincoat.", "role": "person"},
                         {"id": "leo", "words": "Leo, a young man in a grey hoodie.", "role": "person"}],
            "beats": [{"subjects": ["maya", "leo"], "place": "stop", **b} for b in beats], **extra}


DIALOGUE = [{"clip": 1, "seconds": 4, "text": "Close two-shot: Maya looks at the sky", "spoken": ["Maya: It is going to rain."]},
            {"clip": 1, "seconds": 3, "text": "Leo smiles", "spoken": ["Leo: I brought an umbrella."]},
            {"clip": 2, "seconds": 4, "text": "Close two-shot: Maya and Leo walk away under the umbrella."}]


class SoundPlanTests(unittest.TestCase):
    def test_spoken_lines_alone_get_captions_drawn_by_code(self):
        sound = plan_story(story(DIALOGUE, captions=True)).get("sound") or {}
        self.assertTrue(sound.get("captions"))
        self.assertNotIn("subtitles", sound)   # no paid subtitle call: the story has the words

    def test_a_voice_over_and_a_spoken_line_both_get_captions(self):
        beats = DIALOGUE + [{"clip": 3, "seconds": 4, "text": "Close two-shot: the bus arrives.", "says": ["And then the bus came."]}]
        sound = plan_story(story(beats, captions=True))["sound"]
        self.assertTrue(sound["captions"])
        self.assertIn("narration", sound)

    def test_no_captions_unless_asked(self):
        self.assertFalse((plan_story(story(DIALOGUE)).get("sound") or {}).get("captions"))

    def test_the_host_can_still_ask_for_the_subtitle_tool(self):
        sound = plan_story(story(DIALOGUE, captions=True, caption_source="speech"))["sound"]
        self.assertFalse(sound["captions"])
        self.assertIn("subtitles", sound)

    def test_a_plan_with_captions_only_does_not_ask_for_create_production_sound(self):
        planned = plan_story(story(DIALOGUE, captions=True))
        self.assertNotIn("create_production_sound", planned.get("how", ""))


class SlotTests(unittest.TestCase):
    def test_the_slots_are_the_ones_in_the_prompt(self):
        s = story(DIALOGUE)
        clip = plan_story(s)["clips"][0]
        prompt = clip["steps"][-1]["prompt"]
        slots = vp.spoken_slots([dict(s["beats"][b - 1], n=b) for b in clip["beats"]])   # as the join reads them
        self.assertEqual([s["text"] for s in slots], ["It is going to rain.", "I brought an umbrella."])   # no "Maya:" label
        for s in slots:
            self.assertIn(f'{s["start"]}-{s["end"]} s:', prompt)
            self.assertIn(f'"{s["text"]}"', prompt)


@unittest.skipUnless(media_tools.ffmpeg_exe(), "needs ffmpeg")
class TimingTests(unittest.TestCase):
    """A 6 s film: a quiet background (-50 dB noise) and two 'lines' (tones) at 1.0-2.5 s and 3.6-4.4 s."""

    @classmethod
    def setUpClass(cls):
        cls.folder = Path(tempfile.mkdtemp())
        cls.film = cls.folder / "film.mp4"
        tone = "sine=frequency=300:duration=6,volume=enable='between(t,1.0,2.5)+between(t,3.6,4.4)':volume=1,volume=enable='not(between(t,1.0,2.5)+between(t,3.6,4.4))':volume=0"
        subprocess.check_call([media_tools.ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x180:d=6",
                               "-f", "lavfi", "-i", tone, "-f", "lavfi", "-i", "anoisesrc=d=6:a=0.003",
                               "-filter_complex", "[1][2]amix=inputs=2:normalize=0[a]", "-map", "0:v", "-map", "[a]",
                               "-c:v", "libx264", "-c:a", "aac", "-shortest", str(cls.film)])
        cls.silent = cls.folder / "silent.mp4"
        subprocess.check_call([media_tools.ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x180:d=6",
                               "-c:v", "libx264", str(cls.silent)])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, ignore_errors=True)

    def lines(self):
        return [{"text": "It is going to rain.", "start": 0, "end": 3, "clip": (0, 6)},
                {"text": "I brought an umbrella.", "start": 3, "end": 5, "clip": (0, 6)}]

    def test_the_heard_speech_sets_the_times(self):
        first, second = media_tools.time_spoken(self.film, self.lines())
        self.assertEqual((first["timed"], second["timed"]), ("heard", "heard"))
        self.assertAlmostEqual(first["start"], 1.0, delta=0.15)
        self.assertAlmostEqual(first["end"], 2.5, delta=0.2)
        self.assertAlmostEqual(second["start"], 3.6, delta=0.15)
        self.assertGreaterEqual(second["end"], second["start"] + 1.0)   # at least a second on screen

    def test_with_no_sound_the_slot_is_used(self):
        timed = media_tools.time_spoken(self.silent, self.lines())
        self.assertEqual([(t["start"], t["end"], t["timed"]) for t in timed], [(0, 3, "slot"), (3, 5, "slot")])

    def test_each_sentence_is_its_own_caption(self):
        timed = media_tools.time_spoken(self.silent, [{"text": "Rain. Then we walk home together.", "start": 0, "end": 4, "clip": (0, 6)}])
        self.assertEqual([t["text"] for t in timed], ["Rain.", "Then we walk home together."])
        self.assertLess(timed[0]["end"] - timed[0]["start"], timed[1]["end"] - timed[1]["start"])


class UserClipTests(unittest.TestCase):
    def test_lines_take_whole_spans_and_end_at_pauses(self):   # a test clip's heard spans
        heard = [(1.41, 2.54), (4.46, 5.09), (5.97, 6.99), (12.02, 12.72)]
        lines = ["You missed the bus again?", "I was there. The bus just didn't wait.", "Then we walk."]
        self.assertEqual(media_tools._share_spans(heard, lines), [(1.41, 2.54), (4.46, 6.99), (12.02, 12.72)])

    def test_fewer_spans_than_lines_still_gives_every_line_a_time(self):
        self.assertEqual(len(media_tools._share_spans([(0.0, 6.0)], ["One line.", "Two line."])), 2)


if __name__ == "__main__":
    unittest.main()
