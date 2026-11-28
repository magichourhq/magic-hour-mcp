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


if __name__ == "__main__":
    unittest.main()
