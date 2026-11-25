"""Release gates: a feature that waits for upstream approval cannot ship on before it is approved.

Run before every push:
    git fetch upstream && git rebase upstream/main
    .venv/bin/python -m pytest -q tests/test_release_gates.py

A red test here means: turn the feature off in what we ship, or wait for upstream.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_magichour import video_production as vp
from mcp_magichour.openapi_policies import apply_magic_hour_policies

ROOT = Path(__file__).resolve().parent.parent
SHIPPED_CONFIG = [p for p in (ROOT / "vercel.json", ROOT / "server.json", ROOT / "glama.json", ROOT / ".env.example") if p.exists()]


def voice_cloner_approved_upstream() -> bool:
    """Upstream approves the voice cloner by no longer removing it in its MCP policy (commit 2e6d659, #109)."""
    spec = json.loads((ROOT / "docs" / "openapi.json").read_text())
    return "/v1/ai-voice-cloner" in apply_magic_hour_policies(spec)["paths"]


class VoiceClonerGate(unittest.TestCase):
    def test_voice_cloner_ships_off_until_upstream_approves(self):
        if vp.VOICE_OVER_DEFAULT == "cloner":
            self.assertTrue(voice_cloner_approved_upstream(), (
                "The voice-over ships with the voice cloner on, but upstream still hides /v1/ai-voice-cloner "
                "(openapi_policies.py). Set VOICE_OVER_DEFAULT back to \"video\", or wait for upstream."))

    def test_no_shipped_config_turns_the_cloner_on_before_approval(self):
        if voice_cloner_approved_upstream():
            return
        for config in SHIPPED_CONFIG:
            text = config.read_text().replace(" ", "")
            self.assertNotIn("MAGIC_HOUR_VOICE_OVER=cloner", text, f"{config.name} turns the voice cloner on before upstream approves it")
            self.assertNotIn('"MAGIC_HOUR_VOICE_OVER":"cloner"', text, f"{config.name} turns the voice cloner on before upstream approves it")

    def test_upstream_status_is_reported(self):
        # Not a failure either way: says where approval stands, so a push can be planned.
        print(f"\nVoice cloner approved upstream: {voice_cloner_approved_upstream()}; shipped default: {vp.VOICE_OVER_DEFAULT}")


STORY = {
    "title": "Acme", "aspect": "16:9", "setting": "flat diagrams", "captions": True, "audio": False, "narrator": "a calm voice",
    "beats": [{"clip": 1, "seconds": 5, "text": "A heat pump unit.", "says": ["A heat pump moves heat."]},
              {"clip": 2, "seconds": 4, "text": "A house.", "says": ["It warms your home."]}],
}


class VoiceOverSwitch(unittest.TestCase):
    def plan(self, story):
        planned = vp.plan(story, strategy="manual", cuts=[1])
        return planned, vp.questions(story, planned)

    def test_the_cloner_is_on_by_default_and_the_video_model_stays_available(self):
        # The cloner is on by default; production waits for upstream approval.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MAGIC_HOUR_VOICE_OVER", None)
            self.assertEqual(vp.voice_over_mode(), "cloner")
            planned, asked = self.plan(STORY)
        self.assertEqual(planned["sound"]["narration"]["tool"], "voice_cloner")
        self.assertNotIn("voice", [q["id"] for q in asked])
        with patch.dict(os.environ, {"MAGIC_HOUR_VOICE_OVER": "video"}):
            planned, asked = self.plan(STORY)
        self.assertEqual(planned["sound"]["narration"]["tool"], "video")

    def test_without_a_sample_the_cloner_reads_in_a_default_voice(self):
        # Proof of concept: no question; a preset voice, a man's when the narrator is one.
        with patch.dict(os.environ, {"MAGIC_HOUR_VOICE_OVER": "cloner"}):
            planned, asked = self.plan(STORY)
            self.assertNotIn("voice", [q["id"] for q in asked])
            narration = planned["sound"]["narration"]
            self.assertEqual((narration["tool"], narration["sample"]), ("voice_cloner", vp.pick_voice(STORY)["url"]))
            planned, _ = self.plan({**STORY, "narrator": "a deep, calm man's voice"})
            self.assertIn(planned["sound"]["narration"]["sample"], {v[6] for v in vp.VOICES if v[2] == "Male"})
            story, problems = vp.apply_answers(STORY, {"voice": "video"})
            self.assertFalse(problems)
            planned, _ = self.plan(story)
        self.assertEqual(planned["sound"]["narration"]["tool"], "video")

    def test_on_with_a_sample_uses_the_cloner(self):
        story = {**STORY, "voice_sample": "https://example.com/my-voice.mp3"}
        with patch.dict(os.environ, {"MAGIC_HOUR_VOICE_OVER": "cloner"}):
            planned, asked = self.plan(story)
        narration = planned["sound"]["narration"]
        self.assertEqual(narration["tool"], "voice_cloner")
        self.assertEqual(narration["script"], "A heat pump moves heat. It warms your home.")
        self.assertEqual(narration["credits"], 5)   # 43 letters -> 0.1 a letter, rounded up
        self.assertEqual([p["beat"] for p in narration["parts"]], [1, 2])
        self.assertNotIn("voice", [q["id"] for q in asked])
        self.assertFalse(vp.check_plan(story, planned))

    def test_the_cloner_keeps_the_music(self):
        # Found in testing: a story with a sample lost its music track.
        story = {**STORY, "voice_sample": "https://example.com/my-voice.mp3", "music": "soft piano"}
        for mode in ("video", "cloner"):
            with patch.dict(os.environ, {"MAGIC_HOUR_VOICE_OVER": mode}):
                planned, _ = self.plan(story)
            self.assertEqual(planned["sound"]["music"]["prompt"], "soft piano", mode)

    def test_unknown_switch_value_falls_back_to_the_default(self):
        with patch.dict(os.environ, {"MAGIC_HOUR_VOICE_OVER": "elevenlabs"}):
            self.assertEqual(vp.voice_over_mode(), vp.VOICE_OVER_DEFAULT)


if __name__ == "__main__":
    unittest.main()
