"""Contract tests for plan_tool_call (tool management)."""
import asyncio
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from fastmcp import Client
from fastmcp.exceptions import ToolError

from mcp_magichour import tool_plan
from mcp_magichour.openapi_server import create_mcp

ROOT = Path(__file__).resolve().parents[1]


def tool_names(env: dict) -> set[str]:
    async def names():
        with patch.dict(os.environ, env):
            async with Client(create_mcp()) as client:
                return {t.name for t in await client.list_tools()}
    return asyncio.run(names())


class DataTests(unittest.TestCase):
    def test_every_sheet_tool_has_a_note(self):
        known = set(tool_plan.notes()["tools"]) | {"ai_voice_generator_create_audio"}
        for family, sheet in tool_plan.sheets()["families"].items():
            self.assertEqual([t for t in sheet["tools"] if t not in known], [], family)

    def test_every_row_has_options_a_default_and_evidence(self):
        for family, sheet in tool_plan.sheets()["families"].items():
            for row in sheet["rows"]:
                self.assertIn(row["kind"], ("deciding", "required"), row["id"])
                self.assertTrue(row["answers"] and row["default"] and row["why"], f"{family}.{row['id']}")

    def test_default_settings_use_free_models(self):
        free = {m.split(" ")[0] for ms in tool_plan.notes()["free_models"].values() if isinstance(ms, list) for m in ms}
        for family, sheet in tool_plan.sheets()["families"].items():
            for tool, s in sheet.get("settings", {}).items():
                if "model" in s:
                    self.assertIn(s["model"], free, f"{family}.{tool}")


class PlanTests(unittest.TestCase):
    def test_first_call_returns_the_sheet(self):
        out = tool_plan.plan("change_picture", "make this square", None)
        self.assertFalse(out["ready"])
        self.assertEqual(out["stage"], "fill_the_sheet")
        self.assertIn("keep_or_redraw", [r["id"] for r in out["rows"]])

    def test_unknown_family_and_row_are_errors(self):
        with self.assertRaises(ToolError):
            tool_plan.plan("films", "x", None)
        with self.assertRaises(ToolError):
            tool_plan.plan("change_picture", "x", {"nope": "keep"})

    def test_a_deciding_row_needs_one_of_its_options(self):
        with self.assertRaises(ToolError):
            tool_plan.plan("change_picture", "x", {"keep_or_redraw": "maybe"})

    def test_host_only_rows_are_never_questions(self):
        with self.assertRaises(ToolError):
            tool_plan.plan("faces", "put my face on this", {"photo_or_video": "ask"})

    def test_every_open_question_in_one_batch_no_limit(self):
        """No question limit; one batch."""
        out = tool_plan.plan("new_picture", "make me a picture", {"look": "ask", "shape": "ask", "exact_words": "ask",
                             "set": "ask", "transparent": "ask", "meme_kind": "n/a"})
        self.assertFalse(out["ready"])
        self.assertEqual(len(out["questions"]), 5)
        self.assertNotIn("defaulted", out)
        self.assertTrue(all(q["if_skipped"] for q in out["questions"]))

    def test_never_a_second_round(self):
        """After the user was asked once, a row still marked 'ask' takes its default."""
        out = tool_plan.plan("new_picture", "make me a picture", {"look": "matte black on marble", "shape": "ask"},
                             questions_asked=True)
        self.assertTrue(out["ready"])
        shape = next(d for d in out["decisions"] if d["id"] == "shape")
        self.assertNotIn("tell_the_user", shape)   # no telling of defaults
        self.assertEqual(shape["value"], "1:1.")

    def test_square_from_the_users_words_gives_reframe_not_edit_image(self):
        """'make this wide photo square' → keep, fill → reframe_media, with its notes."""
        out = tool_plan.plan("change_picture", "Make this wide photo square. Keep the whole sneaker.", {
            "keep_or_redraw": "keep", "shape_how": "blurred", "subject_place": "n/a", "words": "n/a",
            "on_object": "n/a", "bigger_or_fixed": "n/a", "colour_only": "n/a"})
        self.assertTrue(out["ready"])
        self.assertIn("reframe_media", out["tools"])
        self.assertIn("antitype", out["tools"]["reframe_media"])
        self.assertIn("reframe_media", out["decisions"][0]["instruction"])

    def test_your_call_uses_the_default(self):
        out = tool_plan.plan("sound", "a music bed", {"on_its_own": "on its own", "length": "your_call",
                                                     "mood": "your_call", "voices": "n/a"})
        length = next(d for d in out["decisions"] if d["id"] == "length")
        self.assertNotIn("tell_the_user", length)
        self.assertEqual(length["value"], "15 s.")
        self.assertNotIn("Tell the user every value", out["next"])

    def test_a_gap_is_reported(self):
        out = tool_plan.plan("change_video", "Spanish subtitles on my English clip", {
            "keep_or_remake": "keep", "shape_how": "n/a", "range": "n/a", "new_ending": "n/a", "words": "n/a",
            "subtitle_language": "translated", "has_speech": "n/a", "sound_on_video": "n/a", "logo_or_card": "n/a"})
        self.assertIn("subtitle_language", out["gaps"])

    def test_music_on_the_users_video_plans_add_sound_to_video(self):
        """No longer a gap."""
        out = tool_plan.plan("change_video", "add warm music to this clip, keep my voice", {
            "keep_or_remake": "keep", "sound_on_video": "keep"})
        self.assertNotIn("sound_on_video", out["gaps"])
        self.assertIn("add_sound_to_video", out["tools"])

    def test_faces_in_a_photo_plan_head_swap(self):
        out = tool_plan.plan("faces", "put my face on the astronaut", {
            "consent": "none", "photo_or_video": "photo", "which_person": "n/a", "which_part": "n/a", "headshot_look": "n/a"})
        self.assertIn("swap_head_in_photo", out["tools"])

    def test_plan_settings_are_free_tier(self):
        out = tool_plan.plan("new_picture", "a candle product shot", {
            "exact_words": "none", "shape": "1:1", "set": "one", "transparent": "no", "meme_kind": "n/a"})
        self.assertEqual(out["settings"]["generate_image"]["model"], "qwen-image-2.1")


class DryRunFixTests(unittest.TestCase):
    """Found in a dry run."""

    def test_the_words_themselves_answer_exact_words(self):
        out = tool_plan.plan("new_picture", "logo KwickFixx", {"exact_words": "KwickFixx", "shape": "1:1",
                             "set": "one", "transparent": "no", "meme_kind": "n/a"})
        d = next(d for d in out["decisions"] if d["id"] == "exact_words")
        self.assertEqual(d["value"], "KwickFixx")
        self.assertIn("generate_image with the exact words in the prompt", d["instruction"])   # generation first

    def test_consent_none_is_an_option(self):
        out = tool_plan.plan("faces", "my face", {"consent": "none", "photo_or_video": "photo", "which_person": "n/a",
                             "which_part": "n/a", "headshot_look": "n/a"})
        self.assertTrue(out["ready"])

    def test_side_by_side_plans_the_new_tool(self):
        """Side by side is no longer a gap."""
        out = tool_plan.plan("change_picture", "before and after side by side", {"keep_or_redraw": "keep", "several_in_one": "side by side"})
        self.assertNotIn("several_in_one", out["gaps"])
        self.assertIn("put_pictures_side_by_side", out["tools"])

    def test_an_outfit_picture_plans_the_clothes_tool(self):
        """The picture-change family lists the clothes tool."""
        out = tool_plan.plan("change_picture", "swap my T-shirt for the coat in her picture",
                             {"keep_or_redraw": "redraw", "outfit": "outfit picture"})
        self.assertIn("change_clothes_in_photo", out["tools"])
        out = tool_plan.plan("change_picture", "make my car red", {"keep_or_redraw": "redraw"})
        self.assertNotIn("change_clothes_in_photo", out["tools"])   # the row has `applies`: left out, it does not apply

class OnlyWhenNeededTests(unittest.TestCase):
    """The planner only when the answer would materially change the result."""

    def test_one_call_with_answers_gives_the_plan(self):
        out = tool_plan.plan("change_picture", "make this wide photo square", {"keep_or_redraw": "keep", "shape_how": "blurred"})
        self.assertTrue(out["ready"])
        self.assertIn("reframe_media", out["tools"])

    def test_rows_left_out_take_their_default(self):
        out = tool_plan.plan("sound", "a music bed", {"on_its_own": "on its own"})
        self.assertTrue(out["ready"])
        length = next(d for d in out["decisions"] if d["id"] == "length")
        self.assertEqual(length["value"], "15 s.")

    def test_only_rows_marked_ask_become_questions(self):
        out = tool_plan.plan("new_picture", "a poster", {"shape": "ask"})
        self.assertEqual([q["id"] for q in out["questions"]], ["shape"])

    def test_the_description_names_a_vague_request_with_an_example(self):
        """In a check run, 'make a product pic of a water bottle' went straight to generate_image (2, was 4)."""
        import inspect
        src = inspect.getsource(tool_plan.register_tool_plan)
        self.assertIn("names only the subject", src)
        self.assertIn("a product pic of a water bottle", src)

    def test_the_description_says_when_not_to_call_and_lists_the_rows(self):
        idx = tool_plan.sheet_index()
        self.assertIn("change_picture: keep_or_redraw(keep|redraw)", idx)
        self.assertLess(len(idx), 1500)


class TierTests(unittest.TestCase):
    def test_the_rules_say_free_models_only(self):
        """Free-tier models only."""
        rules = " ".join(tool_plan.sheets()["rules"])
        self.assertIn("Free-tier models only", rules)
        self.assertNotIn("paid model only when", rules)


class LookRowTests(unittest.TestCase):
    def test_a_vague_picture_asks_for_the_look(self):
        """'make a product pic of a water bottle' → the look is asked."""
        out = tool_plan.plan("new_picture", "make a product pic of a water bottle", {"look": "ask", "shape": "ask"})
        self.assertEqual([q["id"] for q in out["questions"]], ["look", "shape"])

    def test_a_given_look_goes_into_the_plan(self):
        out = tool_plan.plan("new_picture", "matte black bottle on marble", {"look": "matte black, on marble, soft light"})
        look = next(d for d in out["decisions"] if d["id"] == "look")
        self.assertEqual(look["value"], "matte black, on marble, soft light")

    def test_the_index_lists_the_look_row(self):
        self.assertIn("new_picture: look,", tool_plan.sheet_index())


class CompositionTests(unittest.TestCase):
    def test_several_sizes_decides_and_shape_does_not_contradict_it(self):
        """In a dry run, shape said one generation per shape, several_sizes said reframe one."""
        out = tool_plan.plan("new_picture", "a coffee ad in 1:1, 9:16 and 16:9", {
            "look": "steaming coffee cup, cream and dark green", "exact_words": "Daybreak Coffee", "shape": "1:1, 9:16, 16:9",
            "set": "set", "transparent": "no"})
        d = {x["id"]: x["instruction"] for x in out["decisions"]}
        self.assertIn("ONE master picture first", d["set"])
        self.assertIn("`set` decides", d["shape"])


class HandoffRuleTests(unittest.TestCase):
    def test_the_rules_say_api_tools_need_a_file_path_or_url(self):
        """In a test run, an image project id passed to edit_image and create_video_from_image was refused."""
        rules = " ".join(tool_plan.sheets()["rules"])
        self.assertIn("not a project id", rules)


class GenerationFirstTests(unittest.TestCase):
    def test_the_description_routes_words_and_shapes_through_the_planner(self):
        """In a dry run, four chats skipped the planner and drew words or reframed (generation first)."""
        import inspect
        src = inspect.getsource(tool_plan.register_tool_plan)
        self.assertIn("ANY picture with words in it", src)
        self.assertIn("Always call this before add_words_to_media or reframe_media", src)


class SetAndExactEditTests(unittest.TestCase):
    def test_a_set_is_made_from_one_master(self):
        """In a test run, separate generations broke the set."""
        out = tool_plan.plan("new_picture", "3 matching carousel slides", {"look": "cosy cafe", "exact_words": "Slow Sunday",
                             "shape": "1:1", "set": "set", "transparent": "no"})
        d = {x["id"]: x["instruction"] for x in out["decisions"]}
        self.assertIn("FROM the master with edit_image", d["set"])
        self.assertIn("create_video_from_image", d["set"])

    def test_an_exact_follow_up_edit_is_one_edit_and_the_user_is_told(self):
        """Two test runs: exact text resizing drifts; one edit_image, and say it is approximate."""
        answers = {r["id"]: "n/a" for r in tool_plan.sheets()["families"]["change_picture"]["rows"] if "applies" in r}
        answers.update({"keep_or_redraw": "redraw", "exact_change": "exact"})
        out = tool_plan.plan("change_picture", "bigger text, same font and centre", answers)
        d = {x["id"]: x["instruction"] for x in out["decisions"]}
        self.assertIn("edit_image", d["exact_change"])
        self.assertIn("not exact", d["exact_change"])
        self.assertNotIn("add_words_to_media", d["exact_change"])


class SwitchTests(unittest.TestCase):
    def test_off_by_default(self):
        env = {k: v for k, v in os.environ.items() if k != "MAGIC_HOUR_TOOL_PLAN"}
        with patch.dict(os.environ, env, clear=True):
            self.assertNotIn("plan_tool_call", tool_names({}))

    def test_on_registers_the_tool(self):
        self.assertIn("plan_tool_call", tool_names({"MAGIC_HOUR_TOOL_PLAN": "on"}))

    def test_the_server_offers_the_sheet_fields(self):
        async def schema():
            with patch.dict(os.environ, {"MAGIC_HOUR_TOOL_PLAN": "on"}):
                async with Client(create_mcp()) as client:
                    return next(t for t in await client.list_tools() if t.name == "plan_tool_call").inputSchema
        s = asyncio.run(schema())
        self.assertEqual(set(s["properties"]), {"family", "request", "answers", "questions_asked"})
        self.assertIn("change_video", json.dumps(s["properties"]["family"]))


class IsolationTests(unittest.TestCase):
    """Tool management changes no video composition file."""

    def test_tool_plan_imports_nothing_from_video_composition(self):
        src = (ROOT / "mcp_magichour" / "tool_plan.py").read_text()
        for mod in ("plan_request", "video_production", "gates", "media_tools"):
            self.assertNotIn(f"from .{mod}", src)
            self.assertNotIn(f"import {mod}", src)


if __name__ == "__main__":
    unittest.main()
