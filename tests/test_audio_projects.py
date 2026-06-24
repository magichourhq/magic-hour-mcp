import unittest

from mcp_magichour.tools.audio_projects import _list_voice_names, _resolve_voice_name


class AudioVoicePresetTests(unittest.TestCase):
    def test_list_voice_names_returns_full_sdk_enum(self):
        voice_names = _list_voice_names()
        self.assertEqual(len(voice_names), 494)
        self.assertEqual(voice_names[0], "21 Savage")
        self.assertIn("Morgan Freeman", voice_names)

    def test_list_voice_names_filters_case_insensitively(self):
        voice_names = _list_voice_names(query="morgan")
        self.assertIn("Morgan Freeman", voice_names)
        self.assertTrue(all("morgan" in name.lower() for name in voice_names))

    def test_resolve_voice_name_accepts_case_insensitive_match(self):
        self.assertEqual(_resolve_voice_name("morgan freeman"), "Morgan Freeman")

    def test_resolve_voice_name_suggests_lookup_tool(self):
        with self.assertRaises(ValueError) as context:
            _resolve_voice_name("morgan freeeman")

        message = str(context.exception)
        self.assertIn("list_ai_voice_presets", message)
        self.assertIn("Closest presets:", message)


if __name__ == "__main__":
    unittest.main()
