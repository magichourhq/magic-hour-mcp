"""Fixes for the jump at a last-frame joint, the music start, the card
spelling check. On real files made by ffmpeg (skipped without ffmpeg)."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from mcp_magichour import media_tools as m


def ff(*args):
    subprocess.check_call([m.ffmpeg_exe(), "-v", "error", "-y", *map(str, args)])


@unittest.skipUnless(m.have_ffmpeg(), "needs ffmpeg")
class JointMusicStartAndSpellingTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def clip(self, seconds):
        out = self.dir / "long.mp4"
        ff("-f", "lavfi", "-i", f"testsrc=s=320x240:d={seconds}:r=24", "-f", "lavfi", "-i", f"sine=f=440:d={seconds}",
           "-shortest", "-c:v", "libx264", "-c:a", "aac", out)
        return out

    def last_frame_bytes(self, file):
        return subprocess.run([m.ffmpeg_exe(), "-v", "error", "-sseof", "-0.05", "-i", str(file), "-frames:v", "1", "-f", "rawvideo",
                               "-pix_fmt", "gray", "-s", "32x24", "-"], capture_output=True).stdout

    def close(self, a, b):
        return sum(abs(x - y) for x, y in zip(a, b)) / max(1, len(a)) < 12

    def test_a_long_clip_that_starts_the_next_one_loses_its_head_not_its_last_frame(self):
        """The 6 s clip came back 6.6 s; cutting its end skipped 0.6 s of motion at the joint."""
        source = self.clip(6.6)
        out = m.fit_length(source, 6.0, "head", self.dir / "head.mp4")
        self.assertAlmostEqual(m.probe(out)["seconds"], 6.0, delta=0.1)
        self.assertTrue(self.close(self.last_frame_bytes(out), self.last_frame_bytes(source)))

    def test_a_clip_with_joints_at_both_ends_is_played_faster_and_keeps_both_ends(self):
        source = self.clip(6.6)
        out = m.fit_length(source, 6.0, "speed", self.dir / "speed.mp4")
        info = m.probe(out)
        self.assertAlmostEqual(info["seconds"], 6.0, delta=0.1)
        self.assertTrue(info["audio"])
        self.assertTrue(self.close(self.last_frame_bytes(out), self.last_frame_bytes(source)))

    def test_music_with_a_start_time_is_silent_before_it(self):
        """The music started at 0 s and covered the whoosh the user asked to hear first."""
        film = self.dir / "film.mp4"
        ff("-f", "lavfi", "-i", "testsrc=s=320x240:d=8", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", 8,
           "-c:v", "libx264", "-c:a", "aac", film)
        music = self.dir / "music.wav"
        ff("-f", "lavfi", "-i", "sine=f=440:d=8", music)
        m.add_sound(film, self.dir / "out.mp4", music=music, layers=self.dir / "l", music_at=4.0)
        level = lambda start: float(subprocess.run([m.ffmpeg_exe(), "-hide_banner", "-ss", str(start), "-t", "1", "-i", str(self.dir / "l" / "music.wav"),
                                                    "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
                                     .split("mean_volume:")[1].split("dB")[0])
        self.assertLess(level(1), -60)
        self.assertGreater(level(5), -30)

    def test_before_a_late_music_start_the_films_own_sound_is_not_lowered(self):
        """The whoosh came first but was mixed 10 dB down, at about -50 dB."""
        film = self.dir / "film.mp4"
        ff("-f", "lavfi", "-i", "testsrc=s=320x240:d=8", "-f", "lavfi", "-i", "sine=f=660:d=8", "-t", 8,
           "-c:v", "libx264", "-c:a", "aac", film)
        music = self.dir / "music.wav"
        ff("-f", "lavfi", "-i", "sine=f=440:d=8", music)
        m.add_sound(film, self.dir / "out.mp4", music=music, layers=self.dir / "l", music_at=4.0)
        level = lambda start: float(subprocess.run([m.ffmpeg_exe(), "-hide_banner", "-ss", str(start), "-t", "1", "-i", str(self.dir / "l" / "effects.wav"),
                                                    "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
                                     .split("mean_volume:")[1].split("dB")[0])
        self.assertGreater(level(1) - level(6), m.EFFECTS_UNDER_DB - 1.5)

    def test_a_quiet_cue_before_a_late_music_start_is_raised_to_the_music(self):
        """The whoosh peaked at -40 dB against music at -15 dB."""
        film = self.dir / "film.mp4"
        ff("-f", "lavfi", "-i", "testsrc=s=320x240:d=8", "-f", "lavfi", "-i", "sine=f=660:d=8,volume=0.2", "-t", 8,
           "-c:v", "libx264", "-c:a", "aac", film)
        music = self.dir / "music.wav"
        ff("-f", "lavfi", "-i", "sine=f=440:d=8", music)
        m.add_sound(film, self.dir / "out.mp4", music=music, layers=self.dir / "l", music_at=4.0)
        cue = m.loudness_between(self.dir / "l" / "effects.wav", [(0.5, 3.5)])
        self.assertAlmostEqual(cue, m.loudness(self.dir / "l" / "music.wav"), delta=3)

    def test_a_misspelled_word_is_not_found(self):
        """The card said "3 tips for better sleen"; tesseract read "better slze"."""
        self.assertEqual(m.words_found(["3 tips for", "better sleep"], "3 tips for\nbetter slze\n"), (4, 5))
        self.assertEqual(m.words_found(["3 tips for better sleep"], "3 TIPS FOR BETTER SLEEP"), (5, 5))

    @unittest.skipUnless(m.have_captions() and __import__("shutil").which("tesseract"), "needs tesseract")
    def test_an_unreadable_card_is_not_called_misspelled(self):
        """Thin letters over a glow read as nothing; 3 stills were made for a card that was right."""
        from PIL import Image
        blank = self.dir / "glow.png"
        Image.new("RGB", (480, 854), (40, 70, 140)).save(blank)
        self.assertEqual(m.check_words(blank, ["3 tips for better sleep"])["verdict"], "unread")

    @unittest.skipUnless(m.have_captions() and __import__("shutil").which("tesseract"), "needs tesseract")
    def test_a_card_with_one_wrong_word_is_misspelled(self):
        card = m.draw_card(["3 tips for better sleen"], "#1E2A4A", (720, 1280), self.dir / "card.png")
        self.assertEqual(m.check_words(card, ["3 tips for better sleep"])["verdict"], "misspelled")
        self.assertEqual(m.check_words(card, ["3 tips for better sleen"])["verdict"], "right")

    @unittest.skipUnless(m.have_captions() and __import__("shutil").which("tesseract"), "needs tesseract")
    def test_the_reader_reads_a_drawn_card(self):
        card = m.draw_card(["Better sleep"], "#1E6B52", (720, 1280), self.dir / "card.png")
        self.assertEqual(m.words_found(["Better sleep"], m.read_words(card) or ""), (2, 2))


if __name__ == "__main__":
    unittest.main()
