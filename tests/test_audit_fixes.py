"""Codebase audit fixes (planner side): minimax is the play, ltx-era rules off, as few clips as possible, split only
when needed."""
import unittest

from mcp_magichour import video_production as vp
from mcp_magichour.video_production_tool import plan_story

PEOPLE = [{"id": "maya", "role": "person", "words": "a young woman in a yellow raincoat"},
          {"id": "leo", "role": "person", "words": "a tall young man in a grey hoodie"}]


def bus_stop(clips, link="event"):
    beats = [
        {"seconds": 5, "text": "Maya looks at Leo.", "subjects": ["maya", "leo"], "spoken": ["Maya: You missed the bus again?"], "clip": clips[0]},
        {"seconds": 5, "text": "Leo shrugs.", "subjects": ["maya", "leo"], "spoken": ["Leo: The bus just didn't wait."], "clip": clips[1], "link": link},
        {"seconds": 5, "text": "Maya opens an umbrella.", "subjects": ["maya", "leo"], "spoken": ["Maya: Then we walk."], "clip": clips[2], "link": link},
    ]
    return {"title": "Bus stop", "aspect": "16:9", "setting": "A bus stop in light rain at dusk.", "audio": True,
            "subjects": PEOPLE, "beats": beats}


def ask(story):
    planned = plan_story(story)
    assert planned["ok"], planned.get("problems")
    return planned, {q["id"]: q for q in vp.questions(story, planned)}


class AuditFixTests(unittest.TestCase):
    def test_no_line_question_on_a_minimax_clip(self):
        planned, asked = ask(bus_stop([1, 1, 2]))
        self.assertEqual([c["steps"][-1]["model"] for c in planned["clips"]], ["minimax-h3", "minimax-h3"])
        self.assertFalse([q for q in asked if q.startswith("line-")])

    def test_same_people_in_the_next_clip_are_asked_to_join(self):
        _, asked = ask(bus_stop([1, 2, 3]))
        self.assertEqual(asked["together-2"]["recommend"], "join")
        self.assertIn("same place and person", asked["together-2"]["ask"])

    def test_a_process_with_no_person_is_not_joined(self):
        story = bus_stop([1, 2, 3])
        story["subjects"] = []
        for b in story["beats"]:
            b["subjects"], b["spoken"] = [], []
        _, asked = ask(story)
        self.assertFalse([q for q in asked if q.startswith("together-")])

    def test_join_stops_at_the_limit(self):
        story = bus_stop([1, 1, 2])
        story["beats"][2]["seconds"] = 6   # 10 s + 6 s > 15 s
        _, asked = ask(story)
        self.assertNotIn("together-3", asked)

    def test_a_scene_still_with_a_line_is_made_on_minimax(self):
        story = bus_stop([1, 2, 3])
        story["beats"][0].update(exact_words=["Line 7"], words_route="still")
        planned, _ = ask(story)
        self.assertEqual(planned["clips"][0]["reference"]["kind"], "scene_still")
        self.assertEqual(planned["clips"][0]["steps"][-1]["model"], "minimax-h3")

    def test_accept_words_are_drawn_by_the_scene_model(self):
        story = bus_stop([1, 2, 3])
        story["beats"][0].update(exact_words=["Night bus"], words_route="accept")
        planned, _ = ask(story)
        prompt = planned["clips"][0]["steps"][-1]["prompt"]
        self.assertIn('"Night bus"', prompt)
        self.assertNotIn("no captions", prompt)

    def test_a_chosen_picture_reference_keeps_every_pictured_subject(self):
        story = bus_stop([1, 2, 3])
        story["subjects"] = [dict(PEOPLE[0], picture="maya.png"), dict(PEOPLE[1], picture="leo.png")]
        story["beats"][0]["reference"] = {"kind": "picture", "subject": "leo"}
        planned, _ = ask(story)
        ref = planned["clips"][0]["reference"]
        self.assertEqual(ref["subject"], "leo")
        self.assertEqual([p["subject"] for p in ref["pictures"]], ["leo", "maya"])

    def test_no_still_from_an_image_model_for_a_product(self):
        story = bus_stop([1, 2, 3])
        story["subjects"].append({"id": "bottle", "role": "product", "words": "a grey bottle"})
        for b in story["beats"]:
            b["subjects"] = b["subjects"] + ["bottle"]
        planned, _ = ask(story)
        notes = [a["note"] for a in vp.advise(story, planned)]
        self.assertNotIn("product-no-picture", notes)
        self.assertNotIn("generate_image", " ".join(a["warning"] for a in vp.advise(story, planned)))


if __name__ == "__main__":
    unittest.main()
