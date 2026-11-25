"""Faults found in a live test run, each from a recorded chat. No network, no credits."""
import copy
import json
import unittest

from mcp_magichour import tool_plan
from mcp_magichour.conversation_front import front
from mcp_magichour.video_production import advise
from mcp_magichour.video_production_tool import Story, plan_story

# The bus stop story as the host wrote it: one person per beat, each clip from the last frame before it.
BUS_STOP = {"title": "Bus Stop in the Rain", "aspect": "16:9", "audio": True,
       "setting": "A city bus stop with a glass shelter at dusk in light rain.",
       "subjects": [{"id": "Maya", "words": "Maya, a young woman in a yellow raincoat", "role": "person"},
                    {"id": "Leo", "words": "Leo, a tall young man in a grey hoodie", "role": "person"}],
       "beats": [{"seconds": 5, "text": "Medium shot of Maya at the bus stop, teasing smile", "subjects": ["Maya"], "place": "bus stop",
                  "link": "new", "spoken": ["You missed the bus again?"], "clip": 1},
                 {"seconds": 6, "text": "Medium shot of Leo at the bus stop, sheepish half smile", "subjects": ["Leo"], "place": "bus stop",
                  "link": "event", "spoken": ["I was there. The bus just didn't wait."], "clip": 2},
                 {"seconds": 4, "text": "Medium shot of Maya at the bus stop, grinning", "subjects": ["Maya"], "place": "bus stop",
                  "link": "event", "spoken": ["Then we walk."], "clip": 3},
                 {"seconds": 5, "text": "Wide shot from behind: Maya and Leo walk off under one umbrella", "subjects": ["Maya", "Leo"],
                  "place": "bus stop", "link": "event", "clip": 4}]}


def planned(story):
    return plan_story(Story(**story).model_dump(exclude_none=True))


def last_frame_problems(story):
    return [p for p in planned(story)["problems"] if "starts from the last frame" in p]


# The first flight story as the host wrote it in a live run: "flies low for about 12 seconds" became 8 s + 4 s with
# the take-off and the landing inside; about 3 s of flight.
FIRST_FLIGHT = {"title": "First Flight", "aspect": "16:9", "audio": False, "setting": "Sand dunes at Kitty Hawk, 1903.",
       "subjects": [{"id": "flyer", "words": "A wooden biplane of 1903 on a launch rail.", "role": "product"}],
       "beats": [{"seconds": 3, "text": "A black card with the date.", "kind": "card", "exact_words": ["17 December 1903"], "clip": 1},
                 {"seconds": 5, "text": "Wide shot: the biplane rests on its launch rail.", "place": "dunes", "subjects": ["flyer"],
                  "link": "new", "clip": 2},
                 {"seconds": 8, "text": "Wide side shot: the biplane slides along the rail, lifts off and flies low.",
                  "place": "dunes", "subjects": ["flyer"], "link": "event", "clip": 3},
                 {"seconds": 4, "text": "Wide shot: the biplane flies low, sinks and lands in the sand.", "place": "dunes",
                  "subjects": ["flyer"], "link": "continues", "clip": 3},
                 {"seconds": 10, "text": "Wide shot: the biplane rests on the sand.", "place": "dunes", "subjects": ["flyer"],
                  "link": "event", "clip": 4}]}
FIRST_FLIGHT_REQUEST = ("A 30-second film, 16:9. It opens with a 3-second title card. Then the plane lifts off and flies low for "
               "about 12 seconds before landing in the sand.")


class LiveReviewFixTests(unittest.TestCase):
    """Faults found in a live test run."""

    def test_a_last_frame_clip_with_a_person_not_in_that_frame_is_a_problem(self):
        problems = last_frame_problems(BUS_STOP)
        assert [p.split(" starts")[0] for p in problems] == ["clip 2", "clip 3", "clip 4"]
        assert "Leo is not in clip 1" in problems[0]

    def test_both_people_in_every_beat_passes(self):   # both in every beat, the line given to one
        s = copy.deepcopy(BUS_STOP)
        for beat in s["beats"]:
            beat["subjects"] = ["Maya", "Leo"]
        assert last_frame_problems(s) == []

    def test_a_person_who_walks_in_or_a_fresh_clip_passes(self):
        s = copy.deepcopy(BUS_STOP)
        s["beats"][1]["text"] = "Leo walks up to the bus stop, sheepish half smile"
        s["beats"][2]["reference"] = s["beats"][3]["reference"] = {"kind": "none"}
        assert last_frame_problems(s) == []

    def test_words_route_overlay_is_in_the_schema(self):   # 3 refusals in test chats
        s = copy.deepcopy(BUS_STOP)
        s["beats"][3]["exact_words"], s["beats"][3]["words_route"] = ["See you tomorrow"], "overlay"
        assert Story(**s).beats[3].words_route == "overlay"

    def test_n_a_on_a_row_that_always_applies_takes_its_default(self):   # 7 refusals in 3 test chats
        out = front("talking", "Make the woman in the clip say this audio instead.",
                    {"presenter": "video with a presenter", "line": "n/a", "length": "your_call", "shape": "n/a", "own_sound": "video"},
                    attachments=[{"name": "talk.mp4", "kind": "video"}, {"name": "line.mp3", "kind": "audio"}])
        assert out["stage"] == "plan" and "line, shape" in out["changed"][-1]

    def test_the_ask_stage_tells_the_host_to_state_no_other_values(self):   # the host offered 5-10 s, the plan made 15 s
        out = front("film", "make a reel for my cafe", {"kind": "ad", "exact_words": "ask"})
        assert out["stage"] == "ask_the_user" and "Do not tell the user a length" in out["next"]
        out = front("new_picture", "make me a picture", {"look": "ask"})
        assert "Do not tell the user a length" in out["next"]

    def test_music_after_a_sound_gets_a_music_from_beat_hint(self):   # "then a soft music sting" ran from 0 s
        s = copy.deepcopy(BUS_STOP)
        s["music"] = "a soft piano sting"
        request = "Logo reveal: a whoosh, then a soft music sting."
        notes = [a["note"] for a in advise(s, planned(s), request)]
        assert "music-later" in notes
        s["music_from_beat"] = 2
        assert "music-later" not in [a["note"] for a in advise(s, planned(s), request)]

    def test_sheet_texts_from_the_review(self):
        notes, sheets = json.dumps(tool_plan.notes()), json.dumps(tool_plan.sheets())
        assert "same voice" not in notes and "same voice" not in sheets   # the API does not promise the voice
        assert "generation_mode lite. The line" not in sheets and "generation_mode standard" in sheets
        assert "model minimax-h3 on the free plan" in sheets   # free-tier model named

    def test_an_action_with_its_own_length_needs_a_beat_of_that_length(self):
        s = copy.deepcopy(FIRST_FLIGHT)
        timed = [a for a in advise(s, planned(s), FIRST_FLIGHT_REQUEST) if a["note"] == "timed-action"]
        assert len(timed) == 1 and "flies low for about 12 seconds" in timed[0]["warning"] and "12 s" in timed[0]["warning"]
        # take-off 2 s, flight 12 s, landing 2 s: no advice; the 3-second card and the 30-second film never count
        s["beats"][2:4] = [{**s["beats"][2], "seconds": 2, "text": "The biplane slides along the rail and lifts off."},
                           {**s["beats"][3], "seconds": 12, "text": "The biplane flies low and level over the sand."},
                           {**s["beats"][3], "seconds": 2, "text": "The biplane sinks and lands in the sand."}]
        s["beats"][-1]["seconds"] = 6
        assert "timed-action" not in [a["note"] for a in advise(s, planned(s), FIRST_FLIGHT_REQUEST)]
        s = copy.deepcopy(FIRST_FLIGHT)   # a number in words
        assert "timed-action" in [a["note"] for a in advise(s, planned(s), "The plane flies for twelve full seconds.")]
