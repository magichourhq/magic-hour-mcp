"""The clip splitter: pure code, no network, no credits."""

import json
import os
import unittest
from unittest.mock import patch
from pathlib import Path

from mcp_magichour.video_production import (
    PROMPT_LIMIT,
    check_outputs,
    choose_reference,
    count_ideas,
    needs_production,
    plan,
    split,
    validate_story,
)

STORIES = Path(__file__).parent / "fixtures" / "stories"


def story(prefix):
    return json.loads(next(STORIES.glob(f"{prefix}-*.json")).read_text())


def shape(clips):
    """[(beats, seconds)] for each clip."""
    return [(c["beats"], c["seconds"]) for c in clips]


# What each split rule must give for each story: {story: {(strategy, max_seconds): [(beats, seconds), ...]}}
EXPECTED = {
    "c1": {  # one scene, one continuous action, 30 s
        ("one", 15): [([1, 2, 3, 4, 5, 6], 30)],
        ("time", 15): [([1, 2, 3], 15), ([4, 5, 6], 15)],
        ("idea", 15): [([1, 2, 3], 15), ([4, 5, 6], 15)],
        ("idea", 30): [([1, 2, 3, 4, 5, 6], 30)],
        ("shot", 15): [([n], 5) for n in range(1, 7)],
    },
    "c2": {  # same place and people, a new event, 14 s
        ("idea", 15): [([1, 2], 14)],
        ("event", 15): [([1], 7), ([2], 7)],
    },
    "c3": {  # three separate ideas, 15 s
        ("time", 15): [([1, 2, 3], 15)],
        ("idea", 15): [([1], 5), ([2], 5), ([3], 5)],
    },
    "c4": {  # card, four shots of one place, card: 30 s
        ("idea", 30): [([1], 2), ([2, 3, 4, 5], 26), ([6], 2)],
        ("idea", 15): [([1], 2), ([2, 3], 13), ([4, 5], 13), ([6], 2)],
        ("event", 15): [([1], 2), ([2], 7), ([3], 6), ([4], 6), ([5], 7), ([6], 2)],
    },
    "c5": {  # bottle, presenter with bottle (two shots), card: 30 s
        ("idea", 30): [([1], 8), ([2, 3], 18), ([4], 4)],
        ("idea", 15): [([1], 8), ([2], 9), ([3], 9), ([4], 4)],
        ("event", 15): [([1], 8), ([2], 9), ([3], 9), ([4], 4)],
    },
}


class SplitTests(unittest.TestCase):
    def test_each_strategy_gives_the_expected_clips(self):
        for prefix, cases in EXPECTED.items():
            for (strategy, max_seconds), want in cases.items():
                with self.subTest(story=prefix, strategy=strategy, max_seconds=max_seconds):
                    self.assertEqual(shape(split(story(prefix), strategy, max_seconds)), want)

    def test_a_card_is_always_its_own_clip(self):
        for strategy in ("time", "idea", "event", "shot"):
            with self.subTest(strategy=strategy):
                clips = split(story("c4"), strategy, 30)
                self.assertEqual([c["kind"] for c in clips][0], "card")
                self.assertEqual([c["kind"] for c in clips][-1], "card")
                self.assertTrue(all(len(c["beats"]) == 1 for c in clips if c["kind"] == "card"))

    def test_a_beat_longer_than_the_limit_is_cut_into_parts_that_continue(self):
        s = story("c1")
        s["beats"] = [{**s["beats"][0], "seconds": 40}]
        s["seconds"] = 40
        clips = split(s, "idea", 15)
        self.assertEqual([c["seconds"] for c in clips], [14, 13, 13])
        self.assertEqual([c["link"] for c in clips], ["new", "continues", "continues"])

    def test_unknown_strategy_is_refused(self):
        with self.assertRaises(ValueError):
            split(story("c1"), "vibes", 15)


class PersonFirstTests(unittest.TestCase):
    """Priority 1: a pictured person's beats stay in one call. Priority 2: a new idea is a new clip."""

    def test_the_presenter_shots_stay_in_one_call_past_15_seconds(self):
        p = plan(story("c5"), "person", 15)
        self.assertEqual(shape(p["clips"]), [([1], 8), ([2, 3], 18), ([4], 4)])
        self.assertEqual([c["reference"]["kind"] for c in p["clips"]], ["picture", "picture", "still"])
        self.assertEqual(p["clips"][1]["reference"]["subject"], "presenter")
        self.assertEqual(p["problems"], [])

    def test_people_without_a_picture_follow_the_idea_rule(self):
        self.assertEqual(shape(split(story("c4"), "person", 15)), shape(split(story("c4"), "idea", 15)))

    def test_a_pictured_product_in_four_places_is_four_clips_each_with_its_picture(self):
        p = plan(story("c6"), "person", 15)
        self.assertEqual(shape(p["clips"]), [([1], 5), ([2], 5), ([3], 5), ([4], 5)])
        self.assertEqual([c["reference"] for c in p["clips"]], [{"kind": "picture", "subject": "bottle", "file": "bench-mockup-fragrance.png"}] * 4)
        self.assertEqual(p["problems"], [])

    def test_a_pictured_person_who_enters_a_scene_brings_their_picture_not_the_last_frame(self):
        s = story("c5")   # as a host wrote it: one place, the presenter enters as an "event" after the bottle shot
        for beat in s["beats"][:3]:
            beat["place"] = "studio"
        s["beats"][1]["link"] = "event"
        clips = plan(s, "person", 15)["clips"]
        self.assertEqual(shape(clips), [([1], 8), ([2, 3], 18), ([4], 4)])
        self.assertEqual(clips[1]["reference"]["subject"], "presenter")   # the person leads; the bottle is a second guide
        self.assertEqual(clips[1]["reference"]["pictures"], [{"subject": "presenter", "file": "bench-presenter.png"},
                                                             {"subject": "bottle", "file": "bench-mockup-fragrance.png"}])
        s["beats"][0]["seconds"], s["seconds"] = 4, 26   # a short bottle shot: the presenter still gets her own call
        clips = plan(s, "person", 15)["clips"]
        self.assertEqual(shape(clips), [([1], 4), ([2, 3], 18), ([4], 4)])
        self.assertEqual(clips[1]["reference"]["subject"], "presenter")

    def long_presenter_story(self):
        s = story("c5")
        s["beats"][1]["seconds"] = s["beats"][2]["seconds"] = 18   # 36 s of the presenter: more than one call holds
        s["seconds"] = 48
        return s

    def test_a_presenter_scene_longer_than_one_call_is_cut_and_the_first_part_ends_on_her(self):
        s = self.long_presenter_story()
        p = plan(s, "person", 15)
        self.assertEqual(shape(p["clips"]), [([1], 8), ([2], 18), ([3], 18), ([4], 4)])
        first, second = p["clips"][1], p["clips"][2]
        self.assertEqual(second["reference"], {"kind": "last_frame", "of_clip": 2})
        self.assertEqual(first["ends_on"], ["presenter"])
        self.assertIn("End with everyone from the last action still in frame", first["steps"][-1]["prompt"])
        self.assertEqual(first["steps"][-1]["prompt"].count(s["subjects"][0]["words"]), 1)   # the person is described once
        self.assertEqual(p["problems"], [])

    def test_a_clip_that_hands_nothing_on_has_no_ending_rule(self):
        p = plan(story("c5"), "person", 15)
        self.assertTrue(all(c["ends_on"] == [] for c in p["clips"]))
        self.assertTrue(all("End with everyone" not in c["steps"][-1]["prompt"] for c in p["clips"]))

    def test_a_clip_with_sound_never_makes_its_own_music(self):
        # music is one separate track, added at the join; a clip makes only the scene's own sound
        s = story("c5")
        silent = [c["steps"][-1]["prompt"] for c in plan(s, "person", 15)["clips"] if c["kind"] != "card"]
        self.assertTrue(silent and all("No music." not in x for x in silent))
        s["audio"] = True
        loud = [c["steps"][-1]["prompt"] for c in plan(s, "person", 15)["clips"] if c["kind"] != "card"]
        self.assertTrue(all(x.endswith("No music.") for x in loud))

    def test_a_sound_effect_is_made_by_its_clip(self):
        # the whoosh is a clip thing; the clip with it is made with sound, the others stay silent
        s = story("c5")
        first = next(b for b in s["beats"] if b.get("kind", "scene") == "scene")
        first["sounds"] = ["a short whoosh as the lines meet"]
        clips = [c for c in plan(s, "person", 15)["clips"] if c["kind"] != "card"]
        with_fx = [c for c in clips if s["beats"].index(first) + 1 in c["beats"]]
        self.assertTrue(with_fx[0]["steps"][-1]["audio"])
        self.assertIn("a short whoosh as the lines meet. No music.", with_fx[0]["steps"][-1]["prompt"])
        self.assertTrue(all(not c["steps"][-1]["audio"] for c in clips if c not in with_fx))

    def test_the_other_rules_give_the_same_plans_as_before(self):
        for clip in plan(story("c4"), "idea", 15)["clips"]:
            self.assertNotIn("End with everyone", clip["steps"][-1]["prompt"])


class ChainTests(unittest.TestCase):
    """An idea that is cut is chained: the clip ends on the person, the next clip starts from that frame."""

    def clips(self, prefix, name):
        s = story(prefix)
        v = next(v for v in s["variants"] if v["name"] == name)
        return plan(s, v["strategy"], v.get("max_seconds", 15), cuts=v.get("cuts"), chain=v.get("chain", True))["clips"]

    def test_the_same_person_in_two_places_is_one_idea_when_it_fits(self):
        clips = self.clips("c7", "rule")
        self.assertEqual(shape(clips), [([1], 5), ([2, 3], 10), ([4], 5)])

    def test_a_person_who_comes_back_starts_from_the_last_clip_that_showed_them(self):
        clips = self.clips("c7", "rule")
        self.assertEqual(clips[2]["reference"], {"kind": "last_frame", "of_clip": 1, "new_place": True})
        self.assertEqual(clips[2]["depends_on"], [1])
        self.assertEqual(clips[0]["ends_on"], ["mara"])
        self.assertIn("End close on the last action's person", clips[0]["steps"][-1]["prompt"])
        self.assertEqual(clips[0]["steps"][-1]["prompt"].count("Mara, a woman"), 1)
        self.assertIn("The place is new", clips[2]["steps"][-1]["prompt"])
        self.assertEqual(clips[1]["reference"], {"kind": "none"})   # Theo is new: nothing to start from
        self.assertEqual(clips[1]["ends_on"], [])

    def test_cut_at_every_season_each_person_is_chained_to_their_own_last_clip(self):
        clips = self.clips("c7", "one-per-season-chained")
        self.assertEqual(shape(clips), [([1], 5), ([2], 5), ([3], 5), ([4], 5)])
        self.assertEqual([c["reference"].get("of_clip") for c in clips], [None, None, 2, 1])
        self.assertEqual([c["ends_on"] for c in clips], [["mara"], ["theo"], [], []])

    def test_two_people_who_take_turns_are_two_chains_made_side_by_side(self):
        s = story("c7")
        chained = plan(s, "manual", 15, cuts=[2, 3, 4])
        self.assertEqual(chained["join"], [1, 2, 3, 4])          # the film keeps the story order
        self.assertEqual(chained["rounds"], [[1, 2], [3, 4]])    # Mara's chain and Theo's chain run together
        self.assertEqual(plan(s, "manual", 15, cuts=[2, 3, 4], chain=False)["rounds"], [[1, 2, 3, 4]])
        self.assertEqual(plan(story("c4"), "person", 15)["rounds"], [[1, 2, 4], [3]])

    def test_chain_off_gives_clips_that_share_words_only(self):
        clips = self.clips("c7", "one-per-season-words-only")
        self.assertEqual([c["reference"]["kind"] for c in clips], ["none"] * 4)
        self.assertTrue(all(c["ends_on"] == [] for c in clips))

    def test_the_cut_point_follows_the_variant(self):
        self.assertEqual(shape(self.clips("c8", "cut-before-the-door-opens")), [([1, 2], 6), ([3, 4], 6)])
        self.assertEqual(shape(self.clips("c8", "cut-in-the-open-doorway")), [([1, 2, 3], 8), ([4], 4)])
        self.assertEqual(shape(self.clips("c8", "no-cut")), [([1, 2, 3, 4], 12)])

    def test_a_shot_that_goes_on_through_a_cut_starts_from_the_last_frame_and_keeps_the_place(self):
        first, second = self.clips("c8", "cut-in-the-open-doorway")
        self.assertEqual(second["reference"], {"kind": "last_frame", "of_clip": 1})
        self.assertEqual(first["ends_on"], ["mara"])
        self.assertIn("End with everyone from the last action still in frame", first["steps"][-1]["prompt"])
        self.assertNotIn("End close on", first["steps"][-1]["prompt"])
        self.assertIn("Continue from it", second["steps"][-1]["prompt"])

    def test_the_hard_cut_has_no_reference_and_no_ending_rule(self):
        first, second = self.clips("c8", "hard-cut-no-chain")
        self.assertEqual(second["reference"], {"kind": "none"})
        self.assertEqual(first["ends_on"], [])

    def test_people_in_words_only_are_chained_under_the_tool_rule(self):
        clips = plan(story("c4"), "person", 15)["clips"]   # Walk Anyway: two halves
        self.assertEqual(shape(clips), [([1], 2), ([2, 3], 13), ([4, 5], 13), ([6], 2)])
        self.assertEqual(clips[1]["ends_on"], ["man", "woman"])
        self.assertEqual(clips[2]["reference"], {"kind": "last_frame", "of_clip": 2})


class SoundPlanTests(unittest.TestCase):
    """A voice-over is one track for the whole film. The plan says what to make and where each part goes."""

    def setUp(self):   # these tests cover the video-model narration; the cloner has its own tests
        self.enterContext(patch.dict(os.environ, {"MAGIC_HOUR_VOICE_OVER": "video"}))

    def story_with_voice(self):
        s = story("c5")
        lines = [["Some scents fade by noon.", "This one stays."], ["It starts soft, like morning light on a wall."],
                 ["One spray on the wrist is enough."], ["Mockup Fragrance.", "Available 12 December."]]
        for beat, says in zip(s["beats"], lines):
            beat["says"] = says
        s.update(narrator="a calm, warm woman's voice", music="soft piano", captions=True)
        return s

    def test_a_story_with_no_voice_and_no_music_has_no_sound_part(self):
        self.assertNotIn("sound", plan(story("c5"), "person", 15))

    def test_the_narration_is_one_call_that_reads_every_line_once_in_story_order(self):
        s = self.story_with_voice()
        p = plan(s, "person", 15)
        n = p["sound"]["narration"]
        self.assertEqual(p["problems"], [])
        self.assertEqual([part["beat"] for part in n["parts"]], [1, 2, 3, 4])
        script = "Some scents fade by noon. This one stays. It starts soft, like morning light on a wall. One spray on the wrist is enough. Mockup Fragrance. Available 12 December."
        self.assertEqual(n["prompt"].count(script), 1)
        self.assertIn("a calm, warm woman's voice", n["prompt"])
        self.assertEqual((n["model"], n["credits"]), ("ltx-2.5", n["seconds"] * 24))
        self.assertGreaterEqual(n["seconds"], len(script.split()) / 2.5)

    def test_each_part_knows_its_clip_and_where_its_beat_starts_inside_it(self):
        parts = plan(self.story_with_voice(), "person", 15)["sound"]["narration"]["parts"]   # clips: [1], [2, 3], [4]
        self.assertEqual([(part["clip"], part["inside"], part["at"]) for part in parts], [(1, 0, 0.5), (2, 0, 8.5), (2, 9, 17.5), (3, 0, 26.5)])

    def test_music_covers_the_film_and_the_sound_is_in_the_total(self):
        p = plan(self.story_with_voice(), "person", 15)
        self.assertEqual(p["sound"]["music"], {"seconds": 32, "prompt": "soft piano", "credits": 32, "instrumental": True})
        self.assertTrue(p["sound"]["captions"])
        clips = sum(step["credits"] for clip in p["clips"] for step in clip["steps"])
        self.assertEqual(p["credits"], clips + p["sound"]["narration"]["credits"] + 32)

    def test_music_alone_needs_no_narration_and_no_captions(self):
        s = story("c5")
        s.update(music="soft piano", captions=True)
        sound = plan(s, "person", 15)["sound"]
        self.assertNotIn("narration", sound)
        self.assertFalse(sound["captions"])

    def test_lines_too_long_for_their_beat_are_a_problem_before_any_call(self):
        s = self.story_with_voice()
        s["beats"][3]["says"] = ["word " * 40]
        self.assertTrue(any("too long for 4 s" in problem for problem in plan(s, "person", 15)["problems"]))


class TriggerTests(unittest.TestCase):
    def test_over_15_seconds_or_three_ideas_needs_production(self):
        self.assertTrue(needs_production(story("c1")))   # 30 s, one idea
        self.assertTrue(needs_production(story("c3")))   # 15 s, three ideas
        self.assertTrue(needs_production(story("c4")))
        self.assertTrue(needs_production(story("c5")))

    def test_one_short_scene_does_not(self):
        self.assertFalse(needs_production(story("c2")))  # 14 s, one idea
        self.assertEqual(count_ideas(story("c2")), 1)
        self.assertEqual(count_ideas(story("c3")), 3)


class ReferenceTests(unittest.TestCase):
    def kinds(self, prefix, strategy, max_seconds, **options):
        return [c["reference"]["kind"] for c in plan(story(prefix), strategy, max_seconds, **options)["clips"]]

    def test_every_clip_has_at_most_one_reference(self):
        for prefix, cases in EXPECTED.items():
            for strategy, max_seconds in cases:
                for clip in plan(story(prefix), strategy, max_seconds)["clips"]:
                    with self.subTest(story=prefix, strategy=strategy, clip=clip["n"]):
                        video = clip["steps"][-1]
                        self.assertIn(clip["reference"]["kind"], ("none", "last_frame", "picture", "still"))
                        self.assertEqual(video["tool"] == "create_video_from_image", clip["reference"]["kind"] in ("last_frame", "still"))
                        # minimax-h3 makes every scene clip up to 30 s; a still with words stays on ltx-2.5;
                        # a card goes on minimax-h3, which holds it still
                        still = clip["reference"]["kind"] == "still" and clip["kind"] != "card"
                        want = "ltx-2.5" if still or (clip["seconds"] > 30 and clip["reference"]["kind"] != "picture") else "minimax-h3"
                        self.assertEqual(video["model"], want)

    def test_a_clip_that_continues_takes_the_last_frame(self):
        self.assertEqual(self.kinds("c1", "time", 15), ["none", "last_frame"])
        self.assertEqual(self.kinds("c2", "event", 15), ["none", "last_frame"])

    def test_separate_ideas_take_no_reference(self):
        self.assertEqual(self.kinds("c3", "idea", 15), ["none", "none", "none"])

    def test_a_card_takes_its_own_still_and_the_clip_after_it_starts_fresh(self):
        self.assertEqual(self.kinds("c4", "event", 15), ["still", "none", "last_frame", "last_frame", "last_frame", "still"])

    def test_the_person_wins_the_one_picture_and_the_next_shot_takes_the_last_frame(self):
        clips = plan(story("c5"), "event", 15)["clips"]
        self.assertEqual([c["reference"]["kind"] for c in clips], ["picture", "picture", "last_frame", "still"])
        self.assertEqual(clips[0]["reference"]["subject"], "bottle")
        self.assertEqual(clips[1]["reference"]["subject"], "presenter")
        self.assertEqual(clips[2]["depends_on"], [2])

    def test_a_carried_subject_without_a_picture_follows_the_moves_option(self):
        s = story("c5")
        for subject in s["subjects"]:
            subject.pop("picture")
        clips = split(s, "event", 15)
        self.assertEqual(choose_reference(s, clips[1], clips[0], moves="last_frame")["kind"], "last_frame")
        self.assertEqual(choose_reference(s, clips[1], clips[0], moves="none")["kind"], "none")


class PlanTests(unittest.TestCase):
    def test_every_variant_in_every_story_passes_the_gates(self):
        for path in sorted(STORIES.glob("*.json")):
            s = json.loads(path.read_text())
            for variant in s["variants"]:
                with self.subTest(story=s["id"], variant=variant["name"]):
                    p = plan(s, variant["strategy"], variant.get("max_seconds", 15), cuts=variant.get("cuts"), chain=variant.get("chain", True), end_on=variant.get("end_on", True))
                    self.assertEqual(p["problems"], [])
                    self.assertEqual(p["seconds"], s["seconds"])
                    self.assertEqual(p["join"], list(range(1, len(p["clips"]) + 1)))
                    for clip in p["clips"]:
                        self.assertLessEqual(len(clip["steps"][-1]["prompt"]), PROMPT_LIMIT)

    def test_credits_are_24_a_second_plus_10_for_each_card_still(self):
        self.assertEqual(plan(story("c3"), "idea", 15)["credits"], 15 * 24)
        self.assertEqual(plan(story("c4"), "event", 15)["credits"], 30 * 24 + 2 * 10)

    def test_the_prompt_reuses_the_story_words_and_never_states_the_clip_count(self):
        s = story("c4")
        p = plan(s, "event", 15)
        for clip in p["clips"]:
            if clip["kind"] == "card":
                continue
            prompt = clip["steps"][-1]["prompt"]
            self.assertIn(s["setting"], prompt)
            self.assertIn(s["subjects"][0]["words"], prompt)
            self.assertNotRegex(prompt.lower(), r"clip \d+ of \d+")
        self.assertIn("You came back.", p["clips"][3]["steps"][-1]["prompt"])
        self.assertNotIn("You came back.", p["clips"][2]["steps"][-1]["prompt"])

    def test_a_story_that_does_not_add_up_is_refused_before_any_call(self):
        s = story("c2")
        s["seconds"] = 20
        p = plan(s, "idea", 15)
        self.assertFalse(p["ok"])
        self.assertEqual(p["clips"], [])
        self.assertIn("beats add up to 14 s, the story says 20 s", p["problems"])

    def test_bad_stories_are_named(self):
        s = story("c4")
        s["beats"][0].pop("exact_words")
        s["beats"][1]["subjects"] = ["nobody"]
        s["aspect"] = "4:3"
        problems = validate_story(s)
        self.assertIn("beat 1: a card needs exact_words, or `picture` (a picture to hold unchanged, such as a logo)", problems)
        self.assertIn("beat 2: unknown subject nobody", problems)
        self.assertTrue(any(p.startswith("aspect must be") for p in problems))

    def test_one_call_longer_than_the_model_allows_is_a_problem(self):
        s = story("c5")  # the picture forces minimax-h3, which stops at 30 s
        s["beats"][0]["seconds"] = 20
        s["seconds"] = 42
        self.assertTrue(any("more than minimax-h3 makes" in p for p in plan(s, "one")["problems"]))

    def test_a_last_frame_with_no_earlier_clip_is_a_problem_not_a_crash(self):
        def two_clips(reference, on_beat, clips=True):
            beats = [{"seconds": 5, "text": "A woman walks to the door."}, {"seconds": 5, "text": "She opens it.", "link": "new"}]
            if clips:
                beats[0]["clip"], beats[1]["clip"] = 1, 2
            beats[on_beat - 1]["reference"] = reference
            return {"title": "Door", "aspect": "16:9", "setting": "A quiet hall.", "beats": beats}

        cases = {
            "first clip, no of_clip": (two_clips({"kind": "last_frame"}, 1), "manual"),
            "its own clip": (two_clips({"kind": "last_frame", "of_clip": 2}, 2), "manual"),
            "a clip that is not there": (two_clips({"kind": "last_frame", "of_clip": 9}, 2), "manual"),
            "a negative clip": (two_clips({"kind": "last_frame", "of_clip": -1}, 2), "manual"),
            "no clip numbers, a later clip": (two_clips({"kind": "last_frame", "of_clip": 5}, 2, clips=False), "shot"),
        }
        for name, (s, strategy) in cases.items():
            with self.subTest(name):
                p = plan(s, strategy, cuts=[2])
                self.assertFalse(p["ok"])
                self.assertTrue(p["problems"])


class OutputCheckTests(unittest.TestCase):
    def test_a_short_clip_a_missing_clip_and_mixed_sizes_are_reported(self):
        p = plan(story("c3"), "idea", 15)
        good = [{"n": n, "seconds": 5.04, "width": 854, "height": 480} for n in (1, 2, 3)]
        self.assertEqual(check_outputs(p, good), [])
        bad = [{"n": 1, "seconds": 2.0, "width": 854, "height": 480}, {"n": 2, "seconds": 5.0, "width": 480, "height": 480}]
        problems = check_outputs(p, bad)
        self.assertIn("clip 1: 2.0 s, planned 5 s", problems)
        self.assertIn("clip 3: no file", problems)
        self.assertTrue(any(x.startswith("clips have different sizes") for x in problems))


if __name__ == "__main__":
    unittest.main()
