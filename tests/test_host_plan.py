"""The host groups the beats into clips; the planner obeys, tracks the request and advises."""

import json
import re
import unittest
from copy import deepcopy
from pathlib import Path

from mcp_magichour import video_production as vp
from mcp_magichour.video_production_tool import plan_story


def risks(story: dict, planned: dict) -> list[dict]:
    """The questions about known risks: all but the host's own continuity check."""
    return [q for q in vp.questions(story, planned) if q["id"] != "continuity"]

STORIES = Path(__file__).parent / "fixtures" / "stories"
WALK_ANYWAY_REQUEST = ('Make a 30-second 16:9 short film called Walk Anyway. Start on a title card for 2 seconds: white text "WALK ANYWAY". '
              '3. Close on both of them. He says out loud: "You came back." She smiles.')


def walk_anyway() -> dict:
    """The story as a host wrote it in a test run: the line has its own beat, then the walk away."""
    story = json.loads((STORIES / "c4-walk-anyway-30.json").read_text())
    story.pop("variants", None)
    return story


def grouped(story: dict, clips: list[int]) -> dict:
    story = deepcopy(story)
    for beat, n in zip(story["beats"], clips):
        beat["clip"] = n
    return story


class HostGroupingTests(unittest.TestCase):
    def test_without_clip_numbers_the_split_rule_merges_and_says_so(self):
        story = walk_anyway()
        planned = plan_story(story)
        self.assertIn([4, 5], [c["beats"] for c in planned["clips"]])          # the line and the walk away in one call
        notes = [a["note"] for a in vp.advise(story, planned)]
        self.assertIn("grouping", notes)

    def test_the_hosts_clips_are_obeyed_exactly(self):
        story = grouped(walk_anyway(), [1, 2, 2, 3, 4, 5])                    # the line alone in clip 3
        planned = plan_story(story)
        self.assertTrue(planned["ok"], planned["problems"])
        self.assertEqual([c["beats"] for c in planned["clips"]], [[1], [2, 3], [4], [5], [6]])
        self.assertNotIn("grouping", [a["note"] for a in vp.advise(story, planned)])

    def test_bad_clip_numbers_are_problems(self):
        story = walk_anyway()
        self.assertTrue(vp.plan(grouped(story, [1, 2, 4, 5, 6, 7]), "manual")["problems"])   # skips 3
        self.assertTrue(vp.validate_story(grouped(story, [1, 1, 2, 3, 4, 5])))                # the card shares clip 1
        half = deepcopy(story)
        half["beats"][0]["clip"] = 1
        self.assertIn("set `clip` on every beat, or on none", vp.validate_story(half))


class SpokenLineTests(unittest.TestCase):
    def line_story(self, clips: list[int]) -> dict:
        story = grouped(walk_anyway(), clips)
        beat = story["beats"][3]
        beat["spoken"] = ["You came back."]
        return story

    def test_a_spoken_line_is_its_own_sentence_in_the_prompt_and_turns_the_sound_on(self):
        story = self.line_story([1, 2, 2, 3, 4, 5])
        story["audio"] = False
        planned = plan_story(story)
        clip = planned["clips"][2]
        self.assertIn('and says, word for word: "You came back."', clip["steps"][-1]["prompt"])
        self.assertNotIn("The speaker says", clip["steps"][-1]["prompt"])
        self.assertIn("2-6 s: the speaker lowers the hand, takes a slow breath and glances down", clip["steps"][-1]["prompt"])
        self.assertTrue(clip["steps"][-1]["audio"])
        self.assertIn('Said on screen once, word for word: "You came back."', [l["want"] for l in planned["checklist"]])

    def test_a_script_label_in_a_spoken_line_names_the_speaker_and_is_not_said(self):
        story = self.line_story([1, 2, 2, 3, 4, 5])
        story["beats"][3]["spoken"] = ["Maya: You came back."]
        prompt = plan_story(story)["clips"][2]["steps"][-1]["prompt"]
        self.assertIn('and Maya says, word for word: "You came back."', prompt)
        self.assertNotIn('"Maya:', prompt)

    def test_the_line_joins_the_beats_action_sentence(self):
        story = self.line_story([1, 2, 2, 3, 4, 5])
        story["beats"][3]["text"] = "Medium close-up: he smiles at the camera."
        prompt = plan_story(story)["clips"][2]["steps"][-1]["prompt"]
        self.assertIn('he smiles at the camera and says, word for word: "You came back."', prompt)
        self.assertNotIn("camera. ", prompt.split("word for word")[0][-12:])

    def test_lines_past_the_beats_time_follow_the_line_before_with_no_empty_slot(self):
        story = self.line_story([1, 2, 2, 3, 4, 5])
        story["beats"][3]["spoken"] = ["Maya: one two three four five six seven eight nine ten eleven twelve thirteen fourteen"] * 3
        prompt = plan_story(story)["clips"][2]["steps"][-1]["prompt"]
        self.assertIn('Then Maya says, word for word:', prompt)
        self.assertIsNone(re.search(r"\b(\d+)-\1 s:", prompt))   # no "4-4 s" slot

    def test_a_spoken_line_with_another_beat_is_a_question_and_split_gives_it_its_own_clip(self):
        story = self.line_story([1, 2, 2, 3, 3, 4])
        for beat in story["beats"][3:5]:   # the question is only for an ltx-2.5 clip
            beat["model"] = "ltx-2.5"
        asked = vp.questions(story, plan_story(story))
        line = next(q for q in asked if q["id"] == "line-4")
        self.assertIn("0 of 3", line["ask"])
        self.assertEqual(line["recommend"], "split")
        answered, problems = vp.apply_answers(story, {"line-4": "split"})
        self.assertEqual(problems, [])
        planned = plan_story(answered)
        self.assertIn([4], [c["beats"] for c in planned["clips"]])
        self.assertIn([5], [c["beats"] for c in planned["clips"]])
        self.assertEqual(risks(answered, planned), [])


class ChosenReferenceTests(unittest.TestCase):
    def test_a_file_the_host_made_is_the_clips_reference(self):
        story = grouped(walk_anyway(), [1, 2, 2, 3, 4, 5])
        story["beats"][4]["reference"] = {"kind": "file", "file": "api-assets/x/frame-with-words.png"}
        clip = plan_story(story)["clips"][3]
        self.assertEqual(clip["reference"], {"kind": "file", "subject": "reference", "file": "api-assets/x/frame-with-words.png", "chosen": True})
        self.assertEqual(clip["steps"][-1]["model"], "minimax-h3")
        self.assertIn("@reference", clip["steps"][-1]["prompt"])

    def test_a_picture_reference_needs_a_subject_with_a_picture(self):
        story = grouped(walk_anyway(), [1, 2, 2, 3, 4, 5])
        story["beats"][1]["reference"] = {"kind": "picture", "subject": "woman"}
        self.assertTrue(any("reference picture" in p for p in vp.validate_story(story)))


class QuestionTests(unittest.TestCase):
    def words_story(self) -> dict:
        story = grouped(walk_anyway(), [1, 2, 2, 3, 4, 5])
        story["beats"][4]["exact_words"] = ["walk anyway"]      # over the walk away
        return story

    def test_words_over_a_scene_are_a_question_with_five_options(self):
        story = self.words_story()
        q = next(q for q in vp.questions(story, plan_story(story)) if q["id"] == "words-5")
        self.assertEqual(set(q["options"]), {"overlay", "card", "model", "still", "accept"})
        self.assertEqual(q["recommend"], "overlay")

    def test_overlay_keeps_the_words_out_of_the_prompt_and_times_them_in_the_clip(self):
        answered, _ = vp.apply_answers(self.words_story(), {"words-5": "overlay"})
        planned = plan_story(answered)
        self.assertTrue(planned["ok"], planned["problems"])
        clip = next(c for c in planned["clips"] if 5 in c["beats"])
        self.assertNotIn("walk anyway", clip["steps"][-1]["prompt"])
        start = sum(answered["beats"][n - 1]["seconds"] for n in clip["beats"] if n < 5)
        self.assertEqual(clip["overlay"], [{"text": "walk anyway", "start": start, "end": start + answered["beats"][4]["seconds"]}])
        self.assertIn("exact: drawn by code", next(l["how"] for l in planned["checklist"] if l["id"] == "words-5"))

    def test_card_puts_the_words_on_a_generated_card_after_the_beat(self):
        # the card is generated, its words quoted to the picture model; not drawn by code
        answered, _ = vp.apply_answers(self.words_story(), {"words-5": "card"})
        planned = plan_story(answered)
        self.assertTrue(planned["ok"], planned["problems"])
        self.assertEqual(sum(c["seconds"] for c in planned["clips"]), 30)            # 2 s taken from the beat
        card = planned["clips"][4]
        still = card["steps"][0]
        self.assertEqual((card["kind"], card["made_by"], still["tool"], still["model"]), ("card", "api", "generate_image", vp.STILL_MODEL))
        self.assertIn('"walk anyway"', still["prompt"])
        self.assertNotIn("walk anyway", planned["clips"][3]["steps"][-1]["prompt"])  # not asked of the video model
        # the line (beat 4) and the walk away (beat 5) share a person, so the plan asks to join them
        self.assertEqual([q["id"] for q in risks(answered, planned)], ["together-5"])

    def test_model_routes_the_clip_to_minimax_and_asks_for_the_words(self):
        answered, _ = vp.apply_answers(self.words_story(), {"words-5": "model"})
        clip = plan_story(answered)["clips"][3]
        self.assertEqual(clip["steps"][-1]["model"], "minimax-h3")
        self.assertIn('Show the words "walk anyway"', clip["steps"][-1]["prompt"])

    def test_still_starts_the_clip_from_a_picture_with_the_words(self):
        answered, _ = vp.apply_answers(self.words_story(), {"words-5": "still"})
        clip = plan_story(answered)["clips"][3]
        self.assertEqual(clip["reference"]["kind"], "scene_still")
        self.assertEqual([s["tool"] for s in clip["steps"]], ["generate_image", "create_video_from_image"])
        self.assertIn('exactly "walk anyway"', clip["steps"][0]["prompt"])

    def test_colour_codes_become_colour_words(self):
        story = grouped(walk_anyway(), [1, 2, 2, 3, 4, 5])
        story["setting"] += " Brand colours #0E7C86 and #F28C28."
        q = next(q for q in vp.questions(story, plan_story(story)) if q["id"] == "colour-codes")
        self.assertIn("#0E7C86 as teal", q["options"]["words"])
        answered, _ = vp.apply_answers(story, {"colour-codes": "words"})
        self.assertNotRegex(answered["setting"], r"#[0-9A-Fa-f]{6}")
        self.assertIn("teal", answered["setting"])
        self.assertEqual([q["id"] for q in risks(answered, plan_story(answered))], ["together-5"])

    def test_in_scene_words_are_best_effort_with_no_question(self):
        story = grouped(walk_anyway(), [1, 2, 2, 3, 4, 5])
        story["beats"][1]["in_scene_words"] = ["BUS 12"]
        planned = plan_story(story)
        self.assertEqual([q["id"] for q in risks(story, planned)], ["together-5"])   # same person, one clip
        self.assertIn('The words "BUS 12" are part of the picture', planned["clips"][1]["steps"][-1]["prompt"])
        self.assertTrue(any("best effort" in l["want"] for l in planned["checklist"]))


class AdviceTests(unittest.TestCase):

    def test_what_the_request_asks_for_is_tracked(self):
        story = grouped(walk_anyway(), [1, 2, 2, 3, 4, 5])
        missing = [a["warning"] for a in vp.advise(story, plan_story(story), WALK_ANYWAY_REQUEST) if a["note"] == "not-carried"]
        self.assertTrue(any("You came back." in m for m in missing))                 # no beat carries the line yet
        story["beats"][3]["spoken"] = ["You came back."]
        missing = [a["warning"] for a in vp.advise(story, plan_story(story), WALK_ANYWAY_REQUEST) if a["note"] == "not-carried"]
        self.assertEqual(missing, [])

    def test_advice_never_changes_the_plan(self):
        story = grouped(walk_anyway(), [1, 2, 2, 3, 3, 4])
        planned = plan_story(story)
        before = json.dumps(planned, sort_keys=True)
        vp.advise(story, planned, WALK_ANYWAY_REQUEST)
        self.assertEqual(json.dumps(planned, sort_keys=True), before)


if __name__ == "__main__":
    unittest.main()


class DrawnCardTests(unittest.TestCase):
    def test_a_card_with_a_background_is_drawn_exactly(self):
        import shutil
        import tempfile
        from mcp_magichour import media_tools
        if not media_tools.have_captions() or not media_tools.have_ffmpeg():
            self.skipTest("needs Pillow and ffmpeg")
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            png = media_tools.draw_card(["MOCKUP FRAGRANCE", "Available 12 December"], "#3B1F6B", (480, 854), Path(folder) / "card.png")
            self.assertEqual(Image.open(png).getpixel((5, 5)), (0x3B, 0x1F, 0x6B))
            video = media_tools.still_video(png, 4, Path(folder) / "card.mp4")
            self.assertAlmostEqual(media_tools.probe(video)["seconds"], 4, delta=0.2)

    def test_a_card_with_a_colour_is_generated_on_that_colour(self):
        # every card is generated; a colour code no longer makes a code card
        story = grouped(walk_anyway(), [1, 2, 2, 3, 4, 5])
        story["beats"][0]["background"] = "#000000"
        clip = plan_story(story)["clips"][0]
        still = clip["steps"][0]
        self.assertEqual((clip["made_by"], still["tool"], still["model"]), ("api", "generate_image", "qwen-image-2.1"))
        self.assertIn("The background is flat black.", still["prompt"])
        self.assertNotIn("#000000", still["prompt"])   # a code in a prompt is drawn as text


class ToolFlowTests(unittest.TestCase):
    def test_an_open_question_holds_the_plan_and_the_answer_releases_it(self):
        import asyncio
        from fastmcp import Client, FastMCP
        from mcp_magichour.video_production_tool import register_video_production

        mcp = FastMCP("t")
        register_video_production(mcp)
        story = QuestionTests().words_story()
        for b in story["beats"]:
            b.pop("clip", None)          # the host did not group: the answer must keep the clips as they are

        async def go():
            async with Client(mcp) as client:
                first = (await client.call_tool("plan_video_production", {"story": story, "request": WALK_ANYWAY_REQUEST})).data
                second = (await client.call_tool("plan_video_production", {"story": story, "request": WALK_ANYWAY_REQUEST,
                                                                              "answers": {"words-5": "card", "continuity": "ok"}})).data
                return first, second
        first, second = asyncio.run(go())
        self.assertIn("words-5", [q["id"] for q in first["questions"]])
        self.assertNotIn("production", first)
        self.assertNotIn("questions", second)
        self.assertTrue(second["production"])
        self.assertIn("card", [c.get("kind") for c in second["clips"]])


class FixesFromTheDryRunTests(unittest.TestCase):
    """A test run of the question flow found these."""

    def test_the_answers_model_wins_over_another_beats_model(self):
        story = QuestionTests().words_story()
        story["beats"][4]["clip"] = 4          # the words beat shares clip 4 with the line beat
        story["beats"][5]["clip"] = 5
        story["beats"][3]["model"] = "ltx-2.5"  # a host had put ltx-2.5 on the first beat of the clip
        answered, _ = vp.apply_answers(story, {"words-5": "model"})
        clip = next(c for c in plan_story(answered)["clips"] if 5 in c["beats"])
        self.assertEqual(clip["steps"][-1]["model"], "minimax-h3")

    def test_a_model_that_cannot_take_the_picture_is_a_problem(self):
        story = json.loads((STORIES / "c5-fragrance-30.json").read_text())
        story.pop("variants", None)
        story["beats"][0]["model"] = "ltx-2.5"   # the bottle's picture on ltx-2.5
        problems = plan_story(story)["problems"]
        self.assertTrue(any("cannot take a picture reference" in p for p in problems), problems)

    def test_a_card_with_the_users_picture_is_generated_from_it(self):
        # a card with the user's logo is made by the image editor from it, not held by code
        story = grouped(walk_anyway(), [1, 2, 2, 3, 4, 5])
        story["beats"][5] = {"seconds": 2, "kind": "card", "clip": 5, "text": "The logo, unchanged.", "picture": "https://example.com/logo.png", "background": "#FFFFFF"}
        planned = plan_story(story)
        self.assertTrue(planned["ok"], planned["problems"])
        card = planned["clips"][-1]
        still = card["steps"][0]
        self.assertEqual((card["made_by"], still["tool"], still["picture"], still["credits"]), ("api", "edit_image", "https://example.com/logo.png", 10))
        self.assertIn("flat white", still["prompt"])
        self.assertEqual(card["steps"][-1]["tool"], "create_video_from_image")
        self.assertFalse(any(c.get("made_by") == "code" for c in planned["clips"]))

    def test_fit_picture_keeps_the_picture_unchanged_inside_the_frame(self):
        import tempfile
        from mcp_magichour import media_tools
        if not media_tools.have_captions():
            self.skipTest("needs Pillow")
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            logo = Path(folder) / "logo.png"
            Image.new("RGB", (400, 200), (14, 124, 134)).save(logo)
            out = media_tools.fit_picture(logo, "#FFFFFF", (854, 480), Path(folder) / "card.png")
            frame = Image.open(out)
            self.assertEqual(frame.size, (854, 480))
            self.assertEqual(frame.getpixel((427, 240)), (14, 124, 134))   # the logo's colour, unchanged, at the centre
            self.assertEqual(frame.getpixel((5, 5)), (255, 255, 255))


class HeldPictureUrlTests(unittest.TestCase):
    def test_a_held_picture_takes_a_url_or_a_file_path(self):
        story = grouped(walk_anyway(), [1, 2, 2, 3, 4, 5])
        story["beats"][5] = {"seconds": 2, "kind": "card", "clip": 5, "text": "The logo.", "picture": "api-assets/x/logo.png"}
        self.assertFalse(any("picture" in p for p in vp.validate_story(story)))
        story["beats"][5]["picture"] = "logo.png"
        self.assertTrue(any("must be the picture's URL or its file_path" in p for p in vp.validate_story(story)))

    def test_a_photo_card_keeps_its_words_and_has_no_colour_by_default(self):
        story = grouped(walk_anyway(), [1, 2, 2, 3, 4, 5])
        story["beats"][5] = {"seconds": 2, "kind": "card", "clip": 5, "text": "A photo.", "picture": "https://x.test/p.jpg",
                             "exact_words": ["Come on in"]}
        step = plan_story(story)["clips"][-1]["steps"][0]
        self.assertEqual(step["tool"], "edit_image")
        self.assertIn('The only added text is exactly "Come on in".', step["prompt"])
        self.assertNotIn("The background is", step["prompt"])


class ProductAndLabelTests(unittest.TestCase):
    """Found in testing: the photo's kitchen stayed, a product with no picture changed, a stage named by a label was
    drawn faintly."""

    def ad(self, picture=None):
        return {"title": "Ad", "aspect": "9:16", "setting": "A clean white studio, soft light.",
                "subjects": [{"id": "bottle", "words": "A green glass bottle with a cork.", "role": "product", **({"picture": picture} if picture else {})}],
                "beats": [{"seconds": 5, "text": "The bottle turns on a plinth.", "subjects": ["bottle"], "clip": 1},
                          {"seconds": 5, "text": "A close view of the bottle's label.", "subjects": ["bottle"], "clip": 2, "link": "new"}]}

    def test_a_product_with_no_picture_in_two_clips_is_a_picture_question(self):
        # no "make a still with generate_image" advice (never a picture made by an image model); the picture
        # question asks for a real photo
        story = self.ad()
        planned = plan_story(story)
        self.assertNotIn("product-no-picture", [a["note"] for a in vp.advise(story, planned)])
        self.assertIn("picture-bottle", [q["id"] for q in vp.questions(story, planned)])

    def test_the_users_product_photo_as_reference_warns_about_its_background(self):
        story = self.ad("api-assets/x/bottle.jpg")
        story["beats"][0]["reference"] = {"kind": "picture", "subject": "bottle"}
        planned = plan_story(story)
        self.assertTrue(planned["ok"], planned["problems"])
        self.assertIn("product-photo-scene", [a["note"] for a in vp.advise(story, planned)])

    def test_a_beat_with_a_label_asks_for_a_plain_action_from_its_start(self):
        story = {"title": "Water", "aspect": "16:9", "setting": "A lake under a hot sun.",
                 "beats": [{"seconds": 5, "text": "Thick white steam rises off the lake.", "exact_words": ["Evaporation"], "words_route": "overlay", "clip": 1}]}
        planned = plan_story(story)
        self.assertIn("plain to see from 0 s", planned["clips"][0]["steps"][-1]["prompt"])


class ContinuityTests(unittest.TestCase):
    """Keep what should stay continuous in one clip; split only when needed."""

    def logo_reveal(self, card_text: str = "The logo, exactly as in the file, held in the centre.") -> dict:
        return {"title": "Logo", "aspect": "16:9", "setting": "A dark navy background.",
                "subjects": [{"id": "logo", "words": "The attached logo.", "role": "product", "picture": "https://example.com/logo.png"}],
                "beats": [{"seconds": 4, "text": "Thin light lines draw in and form the logo.", "subjects": ["logo"], "clip": 1,
                           "model": "minimax-h3", "reference": {"kind": "picture", "subject": "logo"}},
                          {"seconds": 4, "kind": "card", "text": card_text, "picture": "https://example.com/logo.png",
                           "exact_words": ["Build better, together."], "clip": 2}]}

    def test_a_held_picture_after_its_own_reveal_is_a_question_and_merge_makes_one_clip(self):
        story = self.logo_reveal()
        q = next(q for q in vp.questions(story, plan_story(story)) if q["id"] == "held-2")
        self.assertEqual(q["recommend"], "merge")
        answered, problems = vp.apply_answers(story, {"held-2": "merge"})
        self.assertEqual(problems, [])
        self.assertEqual(len(answered["beats"]), 1)
        self.assertEqual((answered["beats"][0]["seconds"], answered["beats"][0]["exact_words"]), (8, ["Build better, together."]))
        self.assertEqual([c["kind"] for c in plan_story(answered)["clips"]], ["scene"])

    def tool_plan(self, story: dict, answers: dict | None = None) -> dict:
        import asyncio
        from fastmcp import Client, FastMCP
        from mcp_magichour.video_production_tool import register_video_production

        mcp = FastMCP("t")
        register_video_production(mcp)

        async def go():
            async with Client(mcp) as client:
                args = {"story": story, **({"answers": answers} if answers else {})}
                return (await client.call_tool("plan_video_production", args)).data
        return asyncio.run(go())

    def test_the_tool_merges_a_held_still_after_its_reveal_with_no_question(self):
        # one generated clip with the logo as reference, never a held card after the reveal
        p = self.tool_plan(self.logo_reveal(), {"continuity": "ok", "words-1": "overlay", "colour-codes": "words"})
        self.assertEqual([(c["kind"], c["made_by"]) for c in p["clips"]], [("scene", "api")])
        self.assertEqual(p["clips"][0]["reference"]["kind"], "picture")
        self.assertNotIn("held-2", [q["id"] for q in p.get("questions", [])])
        self.assertIn("held_merge", [a["note"] for a in p["advice"]])
        self.assertIn("production", p)

    def test_a_host_keep_does_not_hold_the_still_after_its_reveal(self):
        p = self.tool_plan(self.logo_reveal(), {"held-2": "keep", "continuity": "ok", "words-1": "overlay", "colour-codes": "words"})
        self.assertEqual([c["kind"] for c in p["clips"]], ["scene"])

    def test_the_tool_still_asks_about_an_end_card_the_user_asked_for(self):
        p = self.tool_plan(self.logo_reveal("End card: the logo on navy with the words."))
        self.assertIn("held-2", [q["id"] for q in p["questions"]])

    def test_an_end_card_the_user_asked_for_is_kept_by_default(self):
        story = self.logo_reveal("End card: the logo on navy with the words.")
        q = next(q for q in vp.questions(story, plan_story(story)) if q["id"] == "held-2")
        self.assertEqual(q["recommend"], "keep")
        answered, _ = vp.apply_answers(story, {"held-2": "keep"})
        self.assertNotIn("held-2", [q["id"] for q in vp.questions(answered, plan_story(answered))])

    def test_a_beat_that_continues_the_shot_in_another_clip_is_a_question_and_join_puts_it_back(self):
        story = {"title": "Jump", "aspect": "16:9", "setting": "A snowy field.",
                 "beats": [{"seconds": 5, "text": "A red fox leaps up.", "clip": 1},
                           {"seconds": 4, "text": "The fox lands head first in the snow.", "clip": 2, "link": "continues"},
                           {"seconds": 5, "text": "A wide view of the field.", "clip": 3, "link": "new"}]}
        self.assertIn("together-2", [q["id"] for q in vp.questions(story, plan_story(story))])
        answered, problems = vp.apply_answers(story, {"together-2": "join"})
        self.assertEqual(problems, [])
        self.assertEqual([c["beats"] for c in plan_story(answered)["clips"]], [[1, 2], [3]])

    def test_the_host_checks_every_plan_of_two_or_more_clips_once(self):
        story = walk_anyway()
        self.assertIn("continuity", [q["id"] for q in vp.questions(story, plan_story(story))])
        answered, problems = vp.apply_answers(story, {"continuity": "ok"})
        self.assertEqual(problems, [])
        self.assertNotIn("continuity", [q["id"] for q in vp.questions(answered, plan_story(answered))])
        _, problems = vp.apply_answers(story, {"continuity": "change"})
        self.assertTrue(problems)


class ProductPictureTests(unittest.TestCase):
    """A product with no photo is a question: a real photo, never an image-model picture."""

    def handhelds(self, picture: str | None = None) -> dict:
        deck = {"id": "deck", "words": "a Steam Deck OLED handheld PC.", "role": "product"}
        if picture:
            deck["picture"] = picture
        return {"title": "Handhelds", "aspect": "9:16", "setting": "A living room.", "subjects": [deck],
                "beats": [{"seconds": 6, "text": "Hands hold the deck, fully in frame.", "subjects": ["deck"], "clip": 1}]}

    def ids(self, story: dict) -> list[str]:
        return [q["id"] for q in vp.questions(story, plan_story(story))]

    def test_a_product_with_no_photo_is_asked_about_and_web_is_recommended(self):
        story = self.handhelds()
        q = next(q for q in vp.questions(story, plan_story(story)) if q["id"] == "picture-deck")
        self.assertEqual((q["recommend"], sorted(q["options"])), ("web", ["none", "user", "web"]))

    def test_web_without_a_photo_holds_the_plan_and_none_ends_the_question(self):
        story = self.handhelds()
        _, problems = vp.apply_answers(story, {"picture-deck": "web"})
        self.assertTrue(problems and "Never a picture made by an image model" in problems[0])
        answered, problems = vp.apply_answers(story, {"picture-deck": "none"})
        self.assertEqual(problems, [])
        self.assertNotIn("picture-deck", self.ids(answered))

    def test_a_product_with_a_photo_is_not_asked_about_and_the_clip_uses_it(self):
        story = self.handhelds("https://example.com/deck.jpg")
        self.assertNotIn("picture-deck", self.ids(story))
        clip = plan_story(story)["clips"][0]
        self.assertEqual((clip["reference"]["kind"], clip["steps"][-1]["model"]), ("picture", "minimax-h3"))
        self.assertIn("Use @deck as a likeness guide", clip["steps"][-1]["prompt"])


class SpokenFitTests(unittest.TestCase):
    """A spoken line is paced at 2.5 words a second, warned when it does not fit, and always gets a clear end."""

    def story(self, words: int, seconds: int) -> dict:
        story = grouped(walk_anyway(), [1, 2, 2, 3, 4, 5])
        beat = story["beats"][3]
        story["seconds"] += seconds - beat["seconds"]
        beat["seconds"] = seconds
        beat["spoken"] = [" ".join(["word"] * words)]
        return story

    def test_an_18_word_line_gets_8_seconds(self):
        clips = plan_story(self.story(18, 10))["clips"]
        prompt = next(c["steps"][-1]["prompt"] for c in clips if "word for word" in c["steps"][-1].get("prompt", ""))
        self.assertIn("8-10 s: the speaker stops talking", prompt)

    def test_a_line_that_does_not_fit_is_a_warning_and_still_has_a_clear_end(self):
        story = self.story(18, 6)
        planned = plan_story(story)
        warned = [a for a in vp.advise(story, planned) if a["note"] == "spoken-pace"]
        self.assertEqual(len(warned), 1)
        self.assertIn("needs about 9 s", warned[0]["warning"])
        self.assertIn("After the line, the speaker stops talking, mouth closed.", planned["clips"][2]["steps"][-1]["prompt"])

    def test_more_than_3_seconds_after_the_line_gets_the_breath_and_a_warning(self):
        story = self.story(18, 15)
        planned = plan_story(story)
        prompt = next(c["steps"][-1]["prompt"] for c in planned["clips"] if "word for word" in c["steps"][-1].get("prompt", ""))
        self.assertIn("8-15 s: the speaker lowers the hand, takes a slow breath and glances down, mouth closed", prompt)
        self.assertNotIn("smiles and nods", prompt)
        warned = [a for a in vp.advise(story, planned) if a["note"] == "spoken-idle"]
        self.assertEqual(len(warned), 1)
        self.assertIn("about 7 s after it", warned[0]["warning"])

    def test_up_to_3_seconds_after_the_line_keeps_the_short_end_and_no_idle_warning(self):
        story = self.story(18, 11)
        planned = plan_story(story)
        prompt = next(c["steps"][-1]["prompt"] for c in planned["clips"] if "word for word" in c["steps"][-1].get("prompt", ""))
        self.assertIn("8-11 s: the speaker stops talking, smiles and nods", prompt)
        self.assertEqual([a for a in vp.advise(story, planned) if a["note"] == "spoken-idle"], [])

    def test_a_line_that_fits_is_not_warned(self):
        story = self.story(9, 8)
        self.assertEqual([a for a in vp.advise(story, plan_story(story)) if a["note"] == "spoken-pace"], [])


class ShotAndActionAdviceTests(unittest.TestCase):
    """One shot and at most three actions in a clip (prompt guides)."""

    def story(self, beats: list[dict]) -> dict:
        return {"title": "t", "aspect": "16:9", "setting": "A rainy bus stop at dusk.",
                "subjects": [{"id": "maya", "words": "Maya, a young woman in a yellow raincoat.", "role": "person"},
                             {"id": "leo", "words": "Leo, a young man in a grey hoodie.", "role": "person"}],
                "beats": [{"subjects": ["maya", "leo"], "place": "stop", **b} for b in beats]}

    def notes(self, story: dict) -> list[dict]:
        return [a for a in vp.advise(story, plan_story(story)) if a["note"] in ("shot-change", "actions-per-clip")]

    def test_a_close_shot_then_a_wide_shot_in_one_clip_is_a_shot_change(self):
        story = self.story([
            {"clip": 1, "seconds": 3, "text": "Close two-shot: Maya smiles and lifts a closed black umbrella.", "spoken": ["Maya: Then we walk."]},
            {"clip": 1, "seconds": 3, "text": "Maya opens the black umbrella and Leo steps in close beside her."},
            {"clip": 1, "seconds": 4, "text": "Wide shot from behind: Maya and Leo walk away together under the umbrella."}])
        warned = self.notes(story)
        self.assertEqual([a["note"] for a in warned], ["shot-change"])
        self.assertIn("close two shot at 0 s, then from behind wide at 6 s", warned[0]["warning"])

    def test_the_same_shot_named_again_is_not_a_shot_change(self):
        story = self.story([
            {"clip": 1, "seconds": 4, "text": "Medium two-shot under the shelter: Maya turns to Leo."},
            {"clip": 1, "seconds": 5, "text": "Same two-shot: Leo shrugs and looks back at Maya."}])
        self.assertEqual(self.notes(story), [])

    def test_each_shot_in_its_own_clip_is_not_warned(self):
        story = self.story([
            {"clip": 1, "seconds": 6, "text": "Close two-shot: Maya opens the black umbrella and Leo steps in beside her."},
            {"clip": 2, "seconds": 4, "text": "Wide shot from behind: Maya and Leo walk away together under the umbrella."}])
        self.assertEqual(self.notes(story), [])

    def test_seven_actions_in_one_beat_are_too_many(self):
        story = self.story([{"clip": 1, "seconds": 12, "text": (
            "Wide shot: the biplane rolls along the rail as Wilbur runs beside the wing tip; it lifts off and flies low, "
            "then settles down and lands in the sand; Wilbur walks up to it and Orville climbs off the wing.")}])
        warned = self.notes(story)
        self.assertEqual([a["note"] for a in warned], ["actions-per-clip"])
        self.assertIn("about 4 actions in 12 s", warned[0]["warning"])

    def test_three_actions_hold(self):   # in testing: lift off, fly, land in 12 s; Wilbur stayed on the sand
        story = self.story([{"clip": 1, "seconds": 12, "text": (
            "Wide side view: the biplane lifts off the rail and flies low over the sand, Wilbur running alongside, then it "
            "lands softly in the sand.")}])
        self.assertEqual(self.notes(story), [])

    def test_one_person_talking_in_one_shot_is_not_warned(self):   # in testing: four beats, two lines, approved
        story = self.story([
            {"clip": 1, "seconds": 3, "text": "Medium close-up of Maya facing the camera; she thinks and smiles."},
            {"clip": 1, "seconds": 3, "text": "Maya looks into the camera, confident.", "spoken": ["I walk everywhere."]},
            {"clip": 1, "seconds": 7, "text": "Maya keeps facing the camera, a small nod.", "spoken": ["It is the best part of my day."]},
            {"clip": 1, "seconds": 2, "text": "Maya smiles at the camera and lowers the microphone."}])
        self.assertEqual(self.notes(story), [])
