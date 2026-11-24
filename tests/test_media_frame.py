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


@unittest.skipUnless(m.have_ffmpeg(), "needs ffmpeg")
class TicketTests(unittest.TestCase):
    """Trim, sound joins and mixes, pad,
    focus, corner words, left-aligned words."""

    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def clip(self, name, size, seconds):
        out = self.dir / name
        subprocess.check_call([m.ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=s={size}:d={seconds}",
                               "-f", "lavfi", "-i", f"sine=f=440:d={seconds}", "-shortest", str(out)])
        return out

    def tone(self, name, seconds, freq=440):
        out = self.dir / name
        subprocess.check_call([m.ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f={freq}:d={seconds}", str(out)])
        return out

    def test_trim_keeps_the_asked_part_with_its_sound(self):
        out = m.trim(self.clip("v.mp4", "320x240", 8), self.dir / "t.mp4", 0, 5)
        info = m.probe(out)
        self.assertAlmostEqual(info["seconds"], 5.0, delta=0.1)
        self.assertTrue(info["audio"])
        with self.assertRaises(ValueError):
            m.trim(self.dir / "v.mp4", self.dir / "x.mp4", 6, 2)

    def test_join_of_sound_files_gives_one_sound_file(self):
        out = m.join([self.tone("a.wav", 1.5), self.tone("b.wav", 2, 660)], self.dir / "d.m4a")
        info = m.probe(out)
        self.assertAlmostEqual(info["seconds"], 3.5, delta=0.15)
        self.assertEqual(info["width"], 0)

    def test_mix_lays_a_voice_over_music_from_its_start(self):
        out = m.mix_audio([{"file": self.tone("music.wav", 20), "volume": 0.25}, {"file": self.tone("voice.wav", 10, 660), "at": 3}],
                          self.dir / "intro.m4a", 20, fade_out=2)
        self.assertAlmostEqual(m.probe(out)["seconds"], 20, delta=0.2)

    def test_a_mix_longer_than_its_track_ends_in_silence(self):
        """A 4.8 s line padded to the 8 s a talking photo asks, so the photo does not repeat the line."""
        out = m.mix_audio([{"file": self.tone("line.wav", 4.8, 660)}], self.dir / "padded.wav", 8)
        self.assertAlmostEqual(m.probe(out)["seconds"], 8, delta=0.1)
        self.assertLess(self.mean_db(out, 5.5, 2), -80)

    def mean_db(self, path, start, seconds):
        err = subprocess.run([m.ffmpeg_exe(), "-hide_banner", "-ss", str(start), "-t", str(seconds), "-i", str(path), "-af", "volumedetect",
                              "-f", "null", "-"], capture_output=True, text=True).stderr
        return float(err.split("mean_volume:")[1].split("dB")[0])

    def silent_film(self, seconds=8, sound="anullsrc=r=48000:cl=stereo"):
        film = self.dir / "film.mp4"
        subprocess.check_call([m.ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=s=320x240:d={seconds}", "-f", "lavfi",
                               "-i", sound, "-t", str(seconds), "-c:v", "libx264", "-c:a", "aac", str(film)])
        return film

    def test_music_alone_is_the_top_layer_and_the_film_is_at_the_target_loudness(self):
        """Music with no voice-over was mixed at 0.16, so the ad sounded silent (-32 LUFS)."""
        done = m.add_sound(self.silent_film(), self.dir / "out.mp4", music=self.tone("music.wav", 8), layers=self.dir / "l")
        self.assertAlmostEqual(done["lufs"], m.TARGET_LUFS, delta=1)
        music = self.dir / "l" / "music.wav"
        self.assertAlmostEqual(self.mean_db(music, 2, 1), self.mean_db(music, 5, 1), delta=0.5)   # never lowered

    def test_with_swell_the_music_is_lowered_only_where_someone_speaks(self):
        voice = self.tone("voice.wav", 2, 660)
        done = m.add_sound(self.silent_film(), self.dir / "out.mp4", voice, [{"start": 0, "end": 2, "at": 3}],
                           self.tone("music.wav", 8), layers=self.dir / "l", music_swell=True)
        music = self.dir / "l" / "music.wav"
        self.assertAlmostEqual(self.mean_db(music, 1.5, 1) - self.mean_db(music, 3.5, 1), m.SWELL_DB, delta=1.5)
        self.assertAlmostEqual(self.mean_db(music, 1.5, 1), self.mean_db(music, 5.4, 0.5), delta=0.5)   # back up after (before the end fade)
        self.assertEqual(done["speech"], [(3, 5)])

    def test_a_spoken_line_in_the_film_is_never_lowered_and_the_music_sits_under_it(self):
        film = self.silent_film(sound="sine=f=300:d=8")
        m.add_sound(film, self.dir / "out.mp4", music=self.tone("music.wav", 8), spoken=[(4, 6)], layers=self.dir / "l")
        effects, music = self.dir / "l" / "effects.wav", self.dir / "l" / "music.wav"
        self.assertAlmostEqual(self.mean_db(effects, 4.5, 1) - self.mean_db(effects, 1.5, 1), m.EFFECTS_UNDER_DB, delta=1.5)
        self.assertAlmostEqual(self.mean_db(music, 1.5, 1), self.mean_db(music, 4.5, 1), delta=0.5)   # one steady bed
        self.assertAlmostEqual(self.mean_db(effects, 4.5, 1) - self.mean_db(music, 4.5, 1), m.MUSIC_UNDER_SPEECH_DB, delta=1.5)

    def talking_film(self, street):
        """8 s: a voice (300 Hz) from 3 to 6 s over a steady street (pink noise of amplitude `street`)."""
        film = self.dir / "talk.mp4"
        subprocess.check_call([m.ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=320x240:d=8", "-f", "lavfi",
                               "-i", f"anoisesrc=c=pink:a={street}:d=8:r=48000", "-f", "lavfi", "-i", "sine=f=300:d=8:r=48000",
                               "-filter_complex", "[2:a]volume='0.5*between(t,3,6)':eval=frame[v];[1:a][v]amix=inputs=2:normalize=0[a]",
                               "-map", "0:v", "-map", "[a]", "-t", "8", "-c:v", "libx264", "-c:a", "aac", str(film)])
        return film

    def test_a_street_far_under_a_spoken_line_is_lifted_and_the_voice_is_not(self):
        """The street was 53 dB under her voice, heard only at full volume."""
        film = self.talking_film(0.002)
        done = m.lift_bed(film, [(3, 6)], [(0, 8)], self.dir / "lifted.mp4")
        self.assertGreater(done["raised_db"], 10)
        out = self.dir / "lifted.mp4"
        self.assertGreater(self.mean_db(out, 1, 1) - self.mean_db(film, 1, 1), 10)               # the street, before she speaks
        self.assertAlmostEqual(self.mean_db(out, 4, 1), self.mean_db(film, 4, 1), delta=1)        # her voice
        self.assertAlmostEqual(m.probe(out)["seconds"], 8, delta=0.1)
        self.assertEqual(list(self.dir.glob("*-bed.wav")), [])   # the working track stays out of the films folder

    def test_a_street_already_heard_under_the_voice_is_left_alone(self):
        self.assertIsNone(m.lift_bed(self.talking_film(0.1), [(3, 6)], [(0, 8)], self.dir / "lifted.mp4"))
        self.assertIsNone(m.lift_bed(self.silent_film(), [(3, 6)], [(0, 8)], self.dir / "lifted.mp4"))   # nothing to lift

    def test_music_first_is_never_lowered(self):
        m.add_sound(self.silent_film(), self.dir / "out.mp4", self.tone("voice.wav", 2, 660), [{"start": 0, "end": 2, "at": 3}],
                    self.tone("music.wav", 8), music_first=True, layers=self.dir / "l")
        music = self.dir / "l" / "music.wav"
        self.assertAlmostEqual(self.mean_db(music, 1.5, 1), self.mean_db(music, 3.5, 1), delta=0.5)

    def test_a_loud_music_track_is_lowered_more_to_stay_under_the_voice(self):
        voice = self.dir / "voice.wav"
        subprocess.check_call([m.ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=660:d=2", "-af", "volume=0.3", str(voice)])
        m.add_sound(self.silent_film(), self.dir / "out.mp4", voice, [{"start": 0, "end": 2, "at": 3}], self.tone("music.wav", 8),
                    layers=self.dir / "l")
        speech, music = self.dir / "l" / "speech.wav", self.dir / "l" / "music.wav"
        self.assertAlmostEqual(self.mean_db(speech, 3.5, 1) - self.mean_db(music, 3.5, 1), m.MUSIC_UNDER_SPEECH_DB, delta=1.5)

    def test_by_default_the_music_is_one_quiet_level_under_the_voice_for_the_whole_film(self):
        """The music went from gone (under the voice) to loud (between lines). A steady bed is wanted."""
        m.add_sound(self.silent_film(), self.dir / "out.mp4", self.tone("voice.wav", 2, 660), [{"start": 0, "end": 2, "at": 3}],
                    self.tone("music.wav", 8), layers=self.dir / "l")
        speech, music = self.dir / "l" / "speech.wav", self.dir / "l" / "music.wav"
        self.assertAlmostEqual(self.mean_db(music, 1.5, 1), self.mean_db(music, 3.5, 1), delta=0.5)
        self.assertAlmostEqual(self.mean_db(speech, 3.5, 1) - self.mean_db(music, 3.5, 1), m.MUSIC_UNDER_SPEECH_DB, delta=1.5)

    def test_short_gaps_between_sentences_stay_lowered(self):
        self.assertEqual(m.merge_spans([(1, 2), (2.5, 3), (5, 6)]), [(1, 3), (5, 6)])

    def test_a_voice_over_longer_than_its_beats_is_fitted_and_no_word_is_cut(self):
        """Part 2 (3.9 s) started at 4.5 s in an 8.1 s film; its last words were cut."""
        parts = [{"start": 0.0, "end": 3.3, "at": 0.5}, {"start": 3.5, "end": 7.4, "at": 4.5}]
        fit = m.fit_parts(parts, 8.3)   # moved 0.4 s earlier: fits, no speed-up, no hold
        self.assertEqual((fit["tempo"], fit["hold"]), (1.0, 0.0))
        self.assertEqual([p["at"] for p in fit["parts"]], [0.1, 4.1])
        self.assertFalse(m.fit_parts(parts, 12)["moved"])   # a film with room is left alone
        fit = m.fit_parts(parts, 8.0)   # read about 8% faster, still no hold
        self.assertTrue(1.0 < fit["tempo"] <= m.VOICE_TEMPO_MAX and fit["hold"] == 0.0, fit)
        fit = m.fit_parts(parts, 6.0)   # too long even at the top speed: the last frame is held
        last = fit["parts"][-1]
        self.assertEqual(fit["tempo"], m.VOICE_TEMPO_MAX)
        self.assertAlmostEqual(last["at"] + 3.9 / fit["tempo"] + m.HOLD_TAIL, 6.0 + fit["hold"], delta=0.02)
        self.assertGreaterEqual(fit["parts"][1]["at"], 0.1 + 3.3 / fit["tempo"] + m.VOICE_GAP - 0.001)   # no overlap

    def test_a_sentence_with_numbers_is_cut_at_its_real_pause_not_a_breath(self):
        """The cut fell on a breath at 2.8 s; the first sentence ends at the pause 4.18-4.78 s."""
        spans = [(0.0, 1.2), (1.54, 4.18), (4.78, 6.99), (7.4, 8.62)]
        fine = [(0.0, 1.2), (1.54, 2.75), (2.85, 4.18), (4.78, 6.99), (7.4, 8.62)]
        got = m.align_parts(spans, ["The F-35A Lightning II is powered by a single engine.",
                                    "It is the conventional takeoff and landing version, built for normal runways."], fine)
        self.assertAlmostEqual(got[0][1], 4.48, delta=0.01)

    def test_the_held_film_is_longer_by_the_hold_and_a_faster_voice_is_mixed_in(self):
        held = m.hold_end(self.silent_film(4), 1.5, self.dir / "held.mp4")
        self.assertAlmostEqual(m.probe_seconds(held), 5.5, delta=0.15)
        voice = self.tone("voice.wav", 4, freq=300)
        done = m.add_sound(held, self.dir / "out.mp4", voice, [{"start": 0, "end": 4, "at": 0.5, "tempo": 1.15}], layers=self.dir / "l")
        self.assertAlmostEqual(done["speech"][0][1], 0.5 + 4 / 1.15, delta=0.05)
        self.assertLess(self.mean_db(self.dir / "l" / "speech.wav", 4.3, 1), -60)   # the faster voice has ended by 4.0 s

    def test_pad_puts_the_whole_picture_on_coloured_bars(self):
        from PIL import Image

        got = m.reframe(self.clip("w.mp4", "854x480", 1), "9:16", self.dir / "p.mp4", "pad", color="0x1B2A4A")
        self.assertEqual(got["way"], "pad")
        subprocess.check_call([m.ffmpeg_exe(), "-v", "error", "-y", "-i", str(self.dir / "p.mp4"), "-frames:v", "1", str(self.dir / "f.png")])
        r, g, b = Image.open(self.dir / "f.png").convert("RGB").getpixel((240, 20))
        self.assertTrue(abs(r - 0x1B) < 12 and abs(g - 0x2A) < 12 and abs(b - 0x4A) < 12, (r, g, b))

    def test_focus_moves_the_crop(self):
        self.assertIn("(iw-480)*0.000", m.frame_filter(854, 480, 480, 854, "crop", 0.0)[0])
        self.assertIn("(iw-480)*1.000", m.frame_filter(854, 480, 480, 854, "crop", 1.0)[0])


class FfmpegByPathTest(unittest.TestCase):
    def test_no_step_starts_ffmpeg_by_name_alone(self):
        # join() started "ffmpeg" from the PATH and failed on a server without it
        import inspect
        from mcp_magichour import media_tools
        source = inspect.getsource(media_tools)
        self.assertNotRegex(source, r'\[\s*"ffmpeg"|\(\s*\n?\s*"ffmpeg",')
