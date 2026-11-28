"""Named looks for words, the caption choice and the double caption."""
import tempfile
import unittest
from pathlib import Path

from mcp_magichour import media_tools
from mcp_magichour import video_production as vp


class LookTests(unittest.TestCase):
    def test_every_look_has_its_shipped_font(self):
        for name, look in media_tools.LOOKS.items():
            self.assertTrue((media_tools.FONT_DIR / media_tools.FONT_FILES[look["font"]]).exists(), name)

    def test_a_look_sets_font_style_and_capitals(self):
        style = media_tools.word_style({"text": "x", "look": "bold"})
        self.assertEqual((style["font"], style["style"]), ("Anton", "outline"))
        self.assertNotIn("upper", style)   # the words are drawn as the host wrote them
        self.assertEqual(media_tools.word_style({"text": "x", "look": "bold", "font": "Pacifico"})["font"], "Pacifico")
        self.assertEqual(media_tools.word_style({"text": "x"})["style"], "band")   # add_words_to_media keeps its default

    def test_outline_draws_black_round_white_letters_with_no_band(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            png = media_tools._caption_png("Hello there", (640, 360), Path(folder) / "c.png", **media_tools.word_style({"text": "", "look": "caption"}))
            pixels = [p for p in Image.open(png).convert("RGBA").getdata() if p[3] > 200]
        self.assertTrue(any(p[:3] == (255, 255, 255) for p in pixels))
        self.assertTrue(any(p[:3] == (0, 0, 0) for p in pixels))
        self.assertLess(len(pixels), 640 * 360 * 0.2)   # letters only, no band behind them


class CaptionSourceTests(unittest.TestCase):
    def story(self, **extra):
        return {"aspect": "16:9", "setting": "A kitchen.", "captions": True, **extra,
                "beats": [{"seconds": 5, "text": "A cook stirs a pot.", "says": ["Good food takes time."]}]}

    def test_a_script_is_drawn_by_code(self):
        story = self.story()
        sound = vp.plan_sound(story, [{"n": 1, "seconds": 5, "parts": [dict(story["beats"][0], n=1)]}])
        self.assertTrue(sound["captions"])
        self.assertNotIn("subtitles", sound)

    def test_speech_goes_to_the_subtitle_tool_white_with_a_black_outline(self):
        story = self.story(caption_source="speech")
        story["beats"][0].pop("says")   # people talk, no script
        sound = vp.plan_sound(story, [{"n": 1, "seconds": 5, "parts": [dict(story["beats"][0], n=1)]}])
        self.assertFalse(sound["captions"])
        config = sound["subtitles"]["style"]["custom_config"]
        self.assertEqual((sound["subtitles"]["tool"], config["text_color"], config["stroke_color"]),
                         ("add_subtitles_to_video", "#FFFFFF", "#000000"))


if __name__ == "__main__":
    unittest.main()
