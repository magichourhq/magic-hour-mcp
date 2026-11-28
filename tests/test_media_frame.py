"""Frame shape, trims and picture cards, on real files made by ffmpeg (skipped without ffmpeg)."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from mcp_magichour import media_tools as m


@unittest.skipUnless(m.have_ffmpeg(), "needs ffmpeg")
class FrameTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def clip(self, name, size, seconds):
        out = self.dir / name
        subprocess.check_call([m.ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=s={size}:d={seconds}", str(out)])
        return out

    def test_a_small_shape_miss_is_cropped_and_a_large_one_is_filled(self):
        self.assertEqual(m.frame_filter(480, 720, 480, 854)[1], "crop")   # 16% lost
        self.assertEqual(m.frame_filter(854, 480, 480, 854)[1], "fill")   # 68% would be lost

    def test_the_join_cuts_each_clip_to_its_planned_length_and_fits_the_frame(self):
        files = [self.clip("a.mp4", "480x854", 3), self.clip("b.mp4", "480x720", 2.4), self.clip("c.mp4", "854x480", 2)]
        film = m.join(files, self.dir / "film.mp4", False, [2, 2, None], (480, 854))
        info = m.probe(film)
        self.assertAlmostEqual(info["seconds"], 6.0, delta=0.15)
        self.assertEqual((info["width"], info["height"]), (480, 854))

    def test_the_first_and_last_frames_are_different_pictures_of_the_clip(self):
        clip = self.clip("f.mp4", "320x240", 2)
        first, last = m.first_frame(clip, self.dir / "first.jpg"), m.last_frame(clip, self.dir / "last.jpg")
        self.assertTrue(first.stat().st_size and last.stat().st_size)
        self.assertNotEqual(first.read_bytes(), last.read_bytes())   # testsrc has a moving counter

    def test_reframe_returns_the_new_size(self):
        got = m.reframe(self.clip("w.mp4", "854x480", 1), "9:16", self.dir / "r.mp4")
        self.assertEqual((got["width"], got["height"], got["way"]), (480, 854, "fill"))
        self.assertEqual((m.probe(self.dir / "r.mp4")["width"], m.probe(self.dir / "r.mp4")["height"]), (480, 854))

    def test_reframe_works_on_a_picture(self):
        from PIL import Image

        Image.new("RGB", (640, 640), (40, 120, 200)).save(self.dir / "p.png")
        got = m.reframe(self.dir / "p.png", "9:16", self.dir / "r.png")
        self.assertEqual(got["way"], "fill")
        self.assertEqual(Image.open(self.dir / "r.png").size, (got["width"], got["height"]))


class FfmpegByPathTest(unittest.TestCase):
    def test_no_step_starts_ffmpeg_by_name_alone(self):
        # join() started "ffmpeg" from the PATH and failed on a server without it
        import inspect
        from mcp_magichour import media_tools
        source = inspect.getsource(media_tools)
        self.assertNotRegex(source, r'\[\s*"ffmpeg"|\(\s*\n?\s*"ffmpeg",')
