"""Contract tests for `plan_request`.

In-memory API, no network, 0 credits.
"""

import json
import unittest
from unittest.mock import patch

import httpx
from fastmcp import Client

from mcp_magichour.openapi_server import create_mcp


TOOL = "plan_request"
ASYNC_CLIENT = httpx.AsyncClient

# --- Table 1: the question set. Word for word from the question set. --------------------
QUESTIONS = {
    "aspect": "Where will this be shown or posted (Instagram, TikTok, YouTube, a website, print, somewhere else)?",
    "seconds": "How long should it be?",
    "exact_words": "Should any words appear on it? If so, which, exactly?",
    "sound": "Should it have sound: music, a voice, or silent?",
    "language": "Which language for the voice or captions?",
    "which_face": "Whose face goes where (which person in the picture)?",
    "look": "Any look in mind (realistic, illustrated, a brand colour, a mood)? 'Your call' is fine.",
    "subject": "What should it show?",
    "edit": "What should change in the picture?",
    "keep": "What must stay exactly as it is?",
    "motion": "What should move, and how (small natural motion, a slow push in, something happening)?",
    "mood": "What mood or genre for the music?",
    "script": "What should be said, word for word? Or should I write it from the gist?",
    "range": "Which part should be kept (from when to when, or 'the best 10 seconds')?",
}

# --- Table 2: the attached files. Kind, shape and role of each file. ---------------------
FILES = {
    "perfume": {"name": "v4-perfume.jpg", "kind": "image", "aspect": "16:9", "role": "product"},
    "woman": {"name": "portrait-woman.jpg", "kind": "image", "aspect": "1:1", "role": "person"},
    "car": {"name": "rt3-bugatti.jpg", "kind": "image", "aspect": "16:9", "role": "product"},
    "bridge": {"name": "rt3-goldengate.jpg", "kind": "image", "aspect": "9:16", "role": "environment"},
    "poster": {"name": "v4-poster.jpg", "kind": "image", "aspect": "9:16", "role": "copy"},
    "evan": {"name": "web-evan.jpg", "kind": "image", "aspect": "9:16", "role": "person"},
    "teacher": {"name": "student-teacher.jpg", "kind": "image", "aspect": "9:16", "role": "person"},
    "clip": {"name": "v4-classroom-wipe.mp4", "kind": "video", "aspect": "16:9", "seconds": 10, "role": "other"},
}

# --- Table 3: the 13 items, vague brief and full brief. --------------------------------------
# Columns: item, brief kind, tool, brief, files, host sheet, expect missing, expect values.
# - host sheet: what the host model passes. {parameter: (value, source)}.
#   GUESS = the host did not get the value from the user. The test sends the value with each
#   source a host can put on a guess (see GUESS_SOURCES). The result must be the same each time.
# - expect missing: the parameters that come back with source `missing`. Each gives its question
#   from Table 1. No other question comes back. Empty = `ready` is true, 0 questions.
# - expect values: values that must be in the returned sheet.
GUESS = "guess"
INVENTED_WORDS = ["Live Music Tonight", "Doors 8pm", "Free Entry"]  # not in any user message
S03_WORDS = ["Open Mic Night", "Friday 7pm", "The Blue Door"]
S04_WORDS = ["20% OFF", "This weekend only"]

ITEMS = [
    ("S01", "vague", "edit_image", "I need a shot of my perfume for the website", ["perfume"],
     {"edit": ("a studio product shot", GUESS), "keep": ("the bottle", "default"), "aspect": ("16:9", GUESS)},
     ["edit", "aspect"], {}),
    ("S01", "full", "edit_image",
     "Put this perfume bottle (attached) on a white marble surface with soft studio light, a wide 16:9 banner for the website homepage; keep the bottle exactly as it is, no text.",
     ["perfume"],
     {"edit": ("put the bottle on a white marble surface with soft studio light", "user"),
      "keep": ("the bottle exactly as it is", "user"), "aspect": ("16:9", "user"), "exact_words": ([], "user")},
     [], {"aspect": "16:9"}),

    ("S02", "vague", "generate_image", "a product shot of a matte black water bottle", [],
     {"subject": ("a product shot of a matte black water bottle", "user"), "aspect": ("1:1", GUESS),
      "exact_words": ([], "derived"), "look": ("studio product photograph", "default")},
     ["aspect"], {}),
    ("S02", "full", "generate_image",
     "A product shot of a matte black insulated water bottle on a pale concrete surface, soft studio light, premium minimal look, square 1:1 for the Instagram feed, no text.",
     [],
     {"subject": ("a matte black insulated water bottle on a pale concrete surface", "user"),
      "aspect": ("1:1", "user"), "exact_words": ([], "user"), "look": ("soft studio light, premium minimal look", "user")},
     [], {"aspect": "1:1"}),

    ("S03", "vague", "generate_image", "make a poster for our open mic night", [],
     {"subject": ("a poster for an open mic night", "user"), "aspect": ("9:16", "derived"),
      "exact_words": (INVENTED_WORDS, GUESS), "look": ("warm stage colours", "default")},
     ["exact_words"], {"aspect": "9:16"}),
    ("S03", "full", "generate_image",
     "A poster for an open mic night, printed A4 (3:4), warm retro look; the words must read exactly: 'Open Mic Night', 'Friday 7pm', 'The Blue Door'.",
     [],
     {"subject": ("a poster for an open mic night", "user"), "aspect": ("9:16", "user"),
      "exact_words": (S03_WORDS, "user"), "look": ("warm retro look", "user")},
     [], {"aspect": "9:16", "exact_words": S03_WORDS}),

    ("S04", "vague", "generate_image", "a social post announcing 20% off", [],
     {"subject": ("a social post announcing 20% off", "user"), "aspect": ("1:1", GUESS),
      "exact_words": (["Big Summer Sale", "Shop now"], GUESS), "look": ("bold sale design", "default")},
     ["exact_words", "aspect"], {}),
    ("S04", "full", "generate_image",
     "A bold red-and-white square image for the Instagram feed announcing a sale; the words must read exactly '20% OFF' and 'This weekend only'.",
     [],
     {"subject": ("an image for the Instagram feed announcing a sale", "user"), "aspect": ("1:1", "derived"),
      "exact_words": (S04_WORDS, "user"), "look": ("bold red-and-white", "user")},
     [], {"aspect": "1:1", "exact_words": S04_WORDS}),

    ("S05", "vague", "edit_image", "make this photo warmer", ["woman"],
     {"edit": ("make the photo warmer", "user"), "keep": ("everything else", "default")},
     [], {"aspect": "1:1"}),
    ("S05", "full", "edit_image",
     "Make this photo warmer in tone (golden, like late afternoon light); change nothing else: same person, pose, framing and background.",
     ["woman"],
     {"edit": ("warmer in tone (golden, like late afternoon light)", "user"),
      "keep": ("same person, pose, framing and background", "user")},
     [], {"aspect": "1:1"}),

    ("S06", "vague", "edit_image", "remove the people from this", ["car"],
     {"edit": ("remove the people", "user"), "keep": ("everything else", "default")},
     [], {"aspect": "16:9"}),
    ("S06", "full", "edit_image",
     "Remove all the people from the background of this photo and fill the background in naturally; the car and everything else stay exactly as they are.",
     ["car"],
     {"edit": ("remove all the people from the background and fill the background in naturally", "user"),
      "keep": ("the car and everything else stay exactly as they are", "user")},
     [], {"aspect": "16:9"}),

    # trim_media cuts by code.
    ("S07", "vague", "trim_media", "cut this down", ["clip"],
     {"range": ("the first 5 seconds", GUESS)},
     ["range"], {}),
    ("S07", "full", "trim_media", "Trim this clip to the first 6 seconds (0 to 6).", ["clip"],
     {"range": ("0 to 6 seconds", "user")},
     [], {"range": "0 to 6 seconds"}),

    ("S08", "vague", "swap_face_in_photo", "put the one guy's face on the other", ["evan", "teacher"],
     {"which_face": ("the face from the first picture onto the second picture", GUESS)},
     ["which_face"], {}),
    ("S08", "full", "swap_face_in_photo",
     "Put the face of the man in the grey long-sleeve shirt onto the man with braids in the other photo; keep the braids photo as the scene.",
     ["evan", "teacher"],
     {"which_face": ("the face of the man in the grey long-sleeve shirt onto the man with braids", "user")},
     [], {}),

    # "short" gives 8 s. The answer sheet says 6 s.
    ("S09", "vague", "create_video_from_image", "make a short clip of her", ["woman"],
     {"motion": ("she smiles", GUESS), "seconds": (5, "default"), "aspect": ("1:1", GUESS),
      "sound": ("none", GUESS), "keep": ("her face", "default")},
     ["motion", "aspect", "sound"], {"seconds": 8}),
    # The full brief gives no shape. "this portrait" is the photo, not a shape (reading it as 9:16 made a video tall
    # when its clip is square), so the shape is asked, as for any animated picture.
    ("S09", "full", "create_video_from_image",
     "Animate this portrait for 6 seconds: she smiles and looks around a little, nothing dramatic; keep her face exactly like her; silent.",
     ["woman"],
     {"motion": ("she smiles and looks around a little, nothing dramatic", "user"), "seconds": (6, "user"),
      "sound": ("none", "user"), "keep": ("her face exactly like her", "user")},
     ["aspect"], {"seconds": 6, "sound": "none"}),

    # The item file says create_video_from_image; the expected tool is create_video_from_text.
    ("S10", "vague", "create_video_from_text", "a video of this car driving across this bridge", ["car", "bridge"],
     {"subject": ("this car driving across this bridge", "user"), "seconds": (5, GUESS), "aspect": ("16:9", GUESS),
      "sound": ("none", GUESS), "look": ("realistic", "default")},
     ["seconds", "aspect", "sound"], {}),
    ("S10", "full", "create_video_from_text",
     "A 10-second widescreen (16:9) silent clip of this car (attached) driving across this bridge (attached), seen from the roadside, morning light.",
     ["car", "bridge"],
     {"subject": ("this car driving across this bridge, seen from the roadside", "user"), "seconds": (10, "user"),
      "aspect": ("16:9", "user"), "sound": ("none", "user"), "exact_words": ([], "derived"), "look": ("morning light", "user")},
     [], {"seconds": 10, "aspect": "16:9", "sound": "none"}),

    ("S11", "vague", "edit_image", "put my poster on a billboard by this bridge", ["poster", "bridge"],
     {"edit": ("put the poster on a billboard by the bridge", "user"), "keep": ("the poster", "default"),
      "aspect": ("9:16", GUESS)},
     ["aspect"], {}),
    ("S11", "full", "edit_image",
     "Put this poster (attached) on a roadside billboard with this bridge (attached) behind it, landscape 16:9 for a LinkedIn post; the poster must stay exactly as designed, readable.",
     ["poster", "bridge"],
     {"edit": ("put this poster on a roadside billboard with this bridge behind it", "user"),
      "keep": ("the poster must stay exactly as designed, readable", "user"), "aspect": ("16:9", "user")},
     [], {"aspect": "16:9"}),

    # Guesses = the attempted call of the current MCP: 16:9, 5 s, audio on.
    ("V01", "vague", "create_video_from_text", "a clip of waves at sunset for my meditation app", [],
     {"subject": ("waves at sunset for a meditation app", "user"), "seconds": (5, GUESS), "aspect": ("16:9", GUESS),
      "sound": ("ambient", GUESS), "look": ("calm, natural", "default")},
     ["seconds", "aspect", "sound"], {}),
    ("V01", "full", "create_video_from_text",
     "A 10-second vertical (9:16) clip for a meditation app: slow waves at sunset on an empty beach, a gentle camera drift, with the natural sound of the waves, no music, no words.",
     [],
     {"subject": ("slow waves at sunset on an empty beach, a gentle camera drift", "user"), "seconds": (10, "user"),
      "aspect": ("9:16", "user"), "sound": ("ambient", "user"), "exact_words": ([], "user"),
      "look": ("calm, natural", "default")},
     [], {"seconds": 10, "aspect": "9:16", "sound": "ambient"}),

    ("V02", "vague", "create_video_from_image", "animate this", ["perfume"],
     {"motion": ("animate the picture as it is", "user"), "seconds": (5, GUESS), "aspect": ("16:9", GUESS),
      "sound": ("none", GUESS), "keep": ("the bottle", "default")},
     ["seconds", "aspect", "sound"], {}),
    ("V02", "full", "create_video_from_image",
     "Animate this perfume bottle (attached) for 6 seconds: a slow turn on the spot with light moving across the glass, the bottle exactly as in the picture, square, silent.",
     ["perfume"],
     {"motion": ("a slow turn on the spot with light moving across the glass", "user"), "seconds": (6, "user"),
      "aspect": ("1:1", "user"), "sound": ("none", "user"), "keep": ("the bottle exactly as in the picture", "user")},
     [], {"seconds": 6, "aspect": "1:1", "sound": "none"}),
]

# The sources a host can put on a value that the user did not give. The server must make each one `missing`.
# - aspect, seconds, sound: the server reads the user's words itself.
# - exact_words: never invented.
# - edit: any source other than `user` (intent.js, line 73).
# - other parameters: `default` is not permitted (only look, position, transition, voice, language, keep).
GUESS_SOURCES = {
    "aspect": ["default", "missing", "derived", "user"],
    "seconds": ["default", "missing", "derived", "user"],
    "sound": ["default", "missing", "derived", "user"],
    "exact_words": ["default", "missing", "derived", "user"],
    "edit": ["default", "missing", "derived"],
}
OTHER_GUESS_SOURCES = ["default", "missing"]
GUESS_ROUNDS = ["default", "missing", "derived", "user"]

# --- Table 4: scripts for the contract cases. --------------------------------------------------
S02_VAGUE = "a product shot of a matte black water bottle"
S03_VAGUE = "make a poster for our open mic night"
S03_STRANGE = ["the vibe is like jazz you know", "friday", "put the usual stuff on it"]
S04_VAGUE = "a social post announcing 20% off"
S04_TURN = [  # expect_tool = create_video_from_text
    "Instagram feed, the words are '20% OFF · This weekend only', bold red and white",
    "actually make it a 10 second video for Instagram reels instead, same words, no sound",
]
S04_TURN_WORDS = ["20% OFF · This weekend only"]
FIVE_MISSING = "make me a video ad"  # a brief with 5 missing parameters
YOUR_CALL_DEFAULTS = {"aspect": "16:9", "seconds": 10, "sound": "ambient"}  # intent.js, line 59; sound on


def data(result):
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(next(c.text for c in result.content if c.type == "text"))


def user(text):
    return {"role": "user", "text": text}


def assistant(text):
    return {"role": "assistant", "text": text}


def rows(sheet):
    """{parameter: (value, source)} -> the `params` argument."""
    return [{"name": name, "value": None if source == "missing" else value, "source": source}
            for name, (value, source) in sheet.items()]


class PlanRequestTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []

        def upstream(request):
            self.requests.append((request.method, str(request.url)))
            return httpx.Response(500, json={"message": "plan_request must not call the API"})

        self.transport = httpx.MockTransport(upstream)

        def client_factory(*args, **kwargs):
            kwargs["transport"] = self.transport
            return ASYNC_CLIENT(*args, **kwargs)

        self.patches = [
            patch("httpx.AsyncClient", side_effect=client_factory),
            patch("mcp_magichour.openapi_server.build_api_client", side_effect=lambda: ASYNC_CLIENT(base_url="https://api.example.test", transport=self.transport)),
            patch("mcp_magichour.openapi_server.current_authorization_header", return_value="Bearer synthetic-test-token"),
            patch("mcp_magichour.oauth_compat.current_authorization_header", return_value="Bearer synthetic-test-token"),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        with patch.dict("os.environ", {"MAGIC_HOUR_CONVERSATION": "off"}):   # plan_conversation is the default
            self.mcp = create_mcp()
        self.caller = await self.enterAsyncContext(Client(self.mcp))
        self.tools = {tool.name: tool for tool in await self.caller.list_tools()}
        if TOOL not in self.tools:
            self.fail(f"tool not found: `{TOOL}` is not registered on the MCP server")

    async def plan(self, messages, tool, sheet, files=()):
        result = data(await self.caller.call_tool(TOOL, {
            "messages": [user(m) if isinstance(m, str) else m for m in messages],
            "tool": tool,
            "params": rows(sheet),
            "attachments": [FILES[f] for f in files],
        }))
        self.assertEqual(result["tool"], tool)
        self.assertLessEqual(len(result["questions"]), 3)
        for question in result["questions"]:
            self.assertEqual(question["question"], QUESTIONS[question["param"]], "a question is not word for word from the set")
        self.assertEqual(result["ready"], result["questions"] == [])
        return result, {row["name"]: row for row in result["params"]}

    def check(self, result, sheet, missing, values):
        self.assertEqual({name for name, row in sheet.items() if row["source"] == "missing"}, set(missing))
        self.assertEqual({q["param"]: q["question"] for q in result["questions"]}, {name: QUESTIONS[name] for name in missing})
        self.assertEqual(result["ready"], not missing)
        for name, value in values.items():
            self.assertEqual(sheet[name]["value"], value, name)
            self.assertNotEqual(sheet[name]["source"], "missing", name)

    async def run_item(self, tool, brief, files, host_sheet, missing, values):
        guessed = [name for name, (_, source) in host_sheet.items() if source == GUESS]
        for round_source in (GUESS_ROUNDS if guessed else [None]):
            sent = {}
            for name, (value, source) in host_sheet.items():
                if source == GUESS:
                    allowed = GUESS_SOURCES.get(name, OTHER_GUESS_SOURCES)
                    source = round_source if round_source in allowed else allowed[0]
                sent[name] = (value, source)
            with self.subTest(host_sources={name: sent[name][1] for name in guessed}):
                result, sheet = await self.plan([brief], tool, sent, files)
                self.check(result, sheet, missing, values)

    # --- the tool itself ---

    async def test_tool_is_listed_read_only_with_the_contract_arguments(self):
        tool = self.tools[TOOL]
        self.assertTrue(tool.annotations.readOnlyHint)
        self.assertFalse(tool.annotations.destructiveHint)
        self.assertLessEqual({"messages", "tool", "params", "attachments"}, set(tool.inputSchema["properties"]))
        self.assertLessEqual({"messages", "tool", "params"}, set(tool.inputSchema["required"]))

    async def test_tool_makes_no_api_request(self):
        for item, kind, tool, brief, files, host_sheet, missing, values in ITEMS:
            if item == "V01":
                await self.run_item(tool, brief, files, host_sheet, missing, values)
        self.assertFalse([request for request in self.requests if request[0] == "POST"])
        self.assertEqual(self.requests, [])

    # --- the eight contract cases ---

    async def test_instagram_story_and_9_16_both_give_aspect_9_16(self):
        for answer in ["Instagram story", "9:16"]:
            with self.subTest(answer=answer):
                result, sheet = await self.plan(
                    [S02_VAGUE, assistant(QUESTIONS["aspect"]), answer], "generate_image",
                    {"subject": (S02_VAGUE, "user"), "aspect": (None, "missing"), "exact_words": ([], "derived")})
                self.assertEqual(sheet["aspect"]["value"], "9:16")
                self.assertIn(sheet["aspect"]["source"], ["user", "derived"])
                self.assertTrue(result["ready"])
                self.assertEqual(result["questions"], [])

    async def test_poster_gives_the_question_for_exact_words(self):
        item = next(i for i in ITEMS if i[:2] == ("S03", "vague"))
        await self.run_item(*item[2:])

    async def test_the_usual_stuff_is_not_permission_to_write_the_words(self):
        messages = [S03_VAGUE]
        for answer in S03_STRANGE:
            messages += [assistant(QUESTIONS["exact_words"]), answer]
        for source in ["default", "derived", "user"]:
            with self.subTest(host_source=source):
                result, sheet = await self.plan(messages, "generate_image", {
                    "subject": ("a poster for an open mic night", "user"), "aspect": ("9:16", "derived"),
                    "exact_words": (INVENTED_WORDS, source)})
                self.check(result, sheet, ["exact_words"], {"aspect": "9:16"})

    async def test_waves_clip_gives_questions_for_where_length_sound(self):
        item = next(i for i in ITEMS if i[:2] == ("V01", "vague"))
        await self.run_item(*item[2:])

    async def test_your_call_keeps_the_default_of_the_host_and_asks_no_second_question(self):
        result, sheet = await self.plan(
            [S02_VAGUE, assistant(QUESTIONS["aspect"]), "your call"], "generate_image",
            {"subject": (S02_VAGUE, "user"), "aspect": ("1:1", "default"), "exact_words": ([], "derived")})
        self.assertEqual((sheet["aspect"]["value"], sheet["aspect"]["source"]), ("1:1", "default"))
        self.assertTrue(result["ready"])
        self.assertEqual(result["questions"], [])

    async def test_your_call_fills_a_default_when_the_host_gives_no_value(self):
        result, sheet = await self.plan(
            [S02_VAGUE, assistant(QUESTIONS["aspect"]), "your call"], "generate_image",
            {"subject": (S02_VAGUE, "user"), "aspect": (None, "missing"), "exact_words": ([], "derived")})
        self.assertEqual((sheet["aspect"]["value"], sheet["aspect"]["source"]), (YOUR_CALL_DEFAULTS["aspect"], "default"))
        self.assertTrue(result["ready"])
        self.assertEqual(result["questions"], [])

    # --- the loop found in testing: plain delegation words, a give-up, a question asked twice ---

    async def test_you_pick_hands_the_shape_over_and_a_video_from_one_picture_takes_the_pictures_shape(self):
        for answer in ["wherever you pick", "idk", "you choose", "doesn't matter"]:
            with self.subTest(answer=answer):
                result, sheet = await self.plan(
                    ["make a 6 second video of the bottle in this pic turning slowly in place, no sound", assistant(QUESTIONS["aspect"]), answer], "create_video_from_image",
                    {"motion": ("the bottle turns slowly in place", "user"), "seconds": (6, "user"), "sound": ("none", "user"),
                     "aspect": (None, "missing")}, ["woman"])
                self.assertEqual((sheet["aspect"]["value"], sheet["aspect"]["source"]), ("1:1", "default"))
                self.assertTrue(result["ready"])
                self.assertEqual(result["questions"], [])

    async def test_just_make_it_ends_the_questions_with_the_defaults(self):
        for answer in ["idk just make it", "stop asking. make it.", "Your call on anything else. Go ahead and make it."]:
            with self.subTest(answer=answer):
                result, sheet = await self.plan(
                    ["make a video about a kind gesture at a park bench", assistant(QUESTIONS["seconds"] + " " + QUESTIONS["aspect"] + " " + QUESTIONS["sound"]), answer],
                    "create_video_from_text",
                    {"subject": ("a kind gesture at a park bench", "user"), "seconds": (None, "missing"), "aspect": (None, "missing"), "sound": (None, "missing")})
                self.check(result, sheet, [], YOUR_CALL_DEFAULTS)
                self.assertEqual({sheet[name]["source"] for name in YOUR_CALL_DEFAULTS}, {"default"})

    async def test_a_question_asked_twice_without_an_answer_takes_the_default(self):
        result, sheet = await self.plan(
            ["make a video about a kind gesture at a park bench", assistant(QUESTIONS["seconds"] + " " + QUESTIONS["aspect"]), "15 seconds",
             assistant(QUESTIONS["aspect"]), "silent"],
            "create_video_from_text",
            {"subject": ("a kind gesture at a park bench", "user"), "seconds": (15, "user"), "aspect": (None, "missing"), "sound": ("none", "user")})
        self.assertEqual((sheet["aspect"]["value"], sheet["aspect"]["source"]), (YOUR_CALL_DEFAULTS["aspect"], "default"))
        self.assertTrue(result["ready"])
        self.assertEqual(result["questions"], [])

    async def test_edit_of_one_attached_picture_gives_no_question_for_shape(self):
        item = next(i for i in ITEMS if i[:2] == ("S05", "vague"))
        result, sheet = await self.plan([item[3]], item[2], item[5], item[4])
        self.assertNotIn("aspect", [q["param"] for q in result["questions"]])
        self.assertEqual((sheet["aspect"]["value"], sheet["aspect"]["source"]), ("1:1", "derived"))
        self.assertTrue(result["ready"])

    async def test_five_missing_parameters_give_three_questions(self):
        five = ["subject", "seconds", "aspect", "sound", "exact_words"]
        result, sheet = await self.plan([FIVE_MISSING], "create_video_from_text", {name: (None, "missing") for name in five})
        self.assertEqual({name for name, row in sheet.items() if row["source"] == "missing"}, set(five))
        asked = [q["param"] for q in result["questions"]]
        self.assertEqual(len(asked), 3)
        self.assertEqual(len(set(asked)), 3)
        self.assertLessEqual(set(asked), set(five))
        self.assertFalse(result["ready"])

    async def test_direction_change_gives_the_new_tool_and_removes_the_old_shape(self):
        first = [S04_VAGUE, assistant(QUESTIONS["exact_words"] + " " + QUESTIONS["aspect"]), S04_TURN[0]]
        result, sheet = await self.plan(first, "generate_image", {
            "subject": (S04_VAGUE, "user"), "aspect": ("1:1", "derived"),
            "exact_words": (S04_TURN_WORDS, "user"), "look": ("bold red and white", "user")})
        self.check(result, sheet, [], {"aspect": "1:1", "exact_words": S04_TURN_WORDS})

        # The host changes the tool. It sends the old shape again (1:1, from "Instagram feed").
        second = first + [assistant("I will make a square picture for the Instagram feed."), S04_TURN[1]]
        result, sheet = await self.plan(second, "create_video_from_text", {
            "subject": ("a video announcing 20% off", "derived"), "seconds": (10, "user"), "aspect": ("1:1", "derived"),
            "sound": ("none", "user"), "exact_words": (S04_TURN_WORDS, "user"), "look": ("bold red and white", "user")})
        self.assertEqual(result["tool"], "create_video_from_text")
        self.check(result, sheet, [], {"aspect": "9:16", "seconds": 10, "sound": "none", "exact_words": S04_TURN_WORDS})

    # --- more contract behaviour ---

    async def test_you_write_them_is_permission_to_write_the_words(self):
        words = ["Open Mic Night", "Every voice is welcome"]
        result, sheet = await self.plan(
            [S03_VAGUE, assistant(QUESTIONS["exact_words"]), "you write them"], "generate_image",
            {"subject": ("a poster for an open mic night", "user"), "aspect": ("9:16", "derived"), "exact_words": (words, "default")})
        self.assertEqual((sheet["exact_words"]["value"], sheet["exact_words"]["source"]), (words, "default"))
        self.assertTrue(result["ready"])
        self.assertEqual(result["questions"], [])

    async def test_strange_answers_never_give_invented_words(self):
        messages = [S03_VAGUE]
        for answer in S03_STRANGE:
            messages += [assistant(QUESTIONS["exact_words"]), answer]
            for source in ["default", "derived", "user"]:
                with self.subTest(answer=answer, host_source=source):
                    result, sheet = await self.plan(list(messages), "generate_image", {
                        "subject": ("a poster for an open mic night", "user"), "aspect": ("9:16", "derived"),
                        "exact_words": (INVENTED_WORDS, source)})
                    self.check(result, sheet, ["exact_words"], {"aspect": "9:16"})

    async def test_no_question_for_a_parameter_that_the_tool_does_not_use(self):
        result, sheet = await self.plan(["remove the people from this"], "edit_image", {
            "edit": ("remove the people", "user"), "seconds": (None, "missing"), "sound": (None, "missing")}, ["car"])
        self.assertNotIn("seconds", sheet)
        self.assertNotIn("sound", sheet)
        self.assertTrue(result["ready"])
        self.assertEqual(result["questions"], [])


    # --- the words and "your call" bugs found in testing, recorded plan_request calls replayed ---
    # The host sent "No text" as a string. The code read it letter by letter, found an "N" the user never typed, and asked about words.

    async def test_no_text_as_a_string_is_no_words_not_a_missing_value(self):
        brief = "Put this bottle on a light stone kitchen counter, with a tidy modern kitchen behind it. Keep the bottle exactly as it is. Square photo, no text."
        for sent in ["No text", "no text", "No on-screen text", "none", [], ["no text"]]:
            with self.subTest(sent=sent):
                result, sheet = await self.plan([brief], "edit_image", {
                    "edit": ("put the bottle on a light stone kitchen counter", "user"),
                    "keep": ("the bottle exactly as it is", "user"), "exact_words": (sent, "user")}, ["perfume"])
                self.assertEqual(result["questions"], [])
                self.assertEqual((sheet["exact_words"]["value"], sheet["exact_words"]["source"]), ([], "user"))
                self.assertTrue(result["ready"])

    async def test_words_sent_as_text_slots_are_read_from_the_slots(self):   # {"top": ..., "bottom": ...}, seen in a meme case
        brief = "Turn this cat pic into a meme, square. Top text: WHEN YOU THINK IT'S FRIDAY. Bottom text: BUT IT'S ONLY MONDAY."
        result, sheet = await self.plan([brief], "edit_image", {
            "edit": ("make it a meme", "user"), "exact_words": ({"top": "WHEN YOU THINK IT'S FRIDAY", "bottom": "BUT IT'S ONLY MONDAY"}, "user")}, ["woman"])
        self.assertEqual(result["questions"], [])
        self.assertEqual(sheet["exact_words"]["value"], ["WHEN YOU THINK IT'S FRIDAY", "BUT IT'S ONLY MONDAY"])

    async def test_the_user_saying_no_text_is_the_answer_whatever_the_host_sends(self):
        brief = "A poster of a sunset over the sea, vertical, no text."
        for sent, source in [(None, "missing"), ([], "default"), ("No text", "default")]:
            with self.subTest(sent=sent, source=source):
                result, sheet = await self.plan([brief], "generate_image", {
                    "subject": ("a sunset over the sea", "user"), "aspect": ("9:16", "user"), "exact_words": (sent, source)})
                self.assertEqual(result["questions"], [])
                self.assertEqual((sheet["exact_words"]["value"], sheet["exact_words"]["source"]), ([], "user"))

    async def test_dont_include_any_words_is_no_text(self):
        result, sheet = await self.plan(
            ["Make a square product shot of a scented candle. Make it look premium.", assistant(QUESTIONS["exact_words"]),
             "No, please don't include any words on it."], "generate_image",
            {"subject": ("a scented candle, premium", "user"), "aspect": ("1:1", "user"), "exact_words": ("No words, no branding or label text", "user")})
        self.assertEqual(result["questions"], [])
        self.assertEqual(sheet["exact_words"]["value"], [])

    async def test_no_idea_what_words_is_not_no_text(self):
        result, sheet = await self.plan(
            ["Make a poster for my bake sale, vertical. I have no idea what words to put on it."], "generate_image",
            {"subject": ("a poster for a bake sale", "user"), "aspect": ("9:16", "user"), "exact_words": (None, "missing")})
        self.check(result, sheet, ["exact_words"], {"aspect": "9:16"})

    # The host numbers the lines or changes the quotes; the user's words are the same.
    async def test_words_match_the_users_without_case_quotes_or_list_numbers(self):
        brief = "A vertical poster with three tips: 'Clear your desk', 'Silence notifications', 'Pick one task'. Number them 1 to 3."
        for sent in [["1 Clear your desk", "2 Silence notifications", "3 Pick one task"],
                     "1. Clear your desk\n2. Silence notifications\n3. Pick one task", ["CLEAR YOUR DESK", "“Silence notifications”"]]:
            with self.subTest(sent=sent):
                result, sheet = await self.plan([brief], "generate_image", {
                    "subject": ("a poster with three tips", "user"), "aspect": ("9:16", "user"), "exact_words": (sent, "user")})
                self.assertEqual(result["questions"], [])
                self.assertTrue(result["ready"])

    async def test_words_the_user_did_not_say_are_still_asked_even_when_close_to_theirs(self):
        brief = "Make a matching vertical poster for Rooftop Jazz. Put these details on it: 17 October 2027 / 7 PM / Atlas Roof / Free entry."
        result, sheet = await self.plan([brief], "generate_image", {
            "subject": ("a poster for Rooftop Jazz", "user"), "aspect": ("9:16", "user"),
            "exact_words": (["Rooftop Jazz / 24 October 2027 / 7 PM / Atlas Roof / Free entry"], "user")})   # the host moved the date
        self.check(result, sheet, ["exact_words"], {"aspect": "9:16"})

    # "You pick the words" in the brief lets the host write them. Before, only "you write them" in the newest message did.
    async def test_you_pick_the_words_in_the_brief_lets_the_host_write_them(self):
        briefs = ["Make a square social graphic for a local beach clean-up, with a short catchy headline — you pick the words. No dates or signup details.",
                  "Make a vertical thumbnail for my video about sleeping better. Choose a punchy three-word headline yourself.",
                  "A square café post for this latte. The exact words are your call, short and warm."]
        for brief in briefs:
            for source in ["default", "derived", "user"]:
                with self.subTest(brief=brief[:40], source=source):
                    result, sheet = await self.plan([brief], "generate_image", {
                        "subject": ("a graphic", "user"), "aspect": ("1:1" if "square" in brief else "9:16", "user"),
                        "exact_words": (["Love Your Beach. Lend a Hand."], source)})
                    self.assertEqual(result["questions"], [])
                    self.assertEqual(sheet["exact_words"]["value"], ["Love Your Beach. Lend a Hand."])

    async def test_use_that_approves_the_words_the_assistant_proposed_but_a_bare_yes_does_not(self):
        messages = ["Add something catchy to this latte pic for a square café post, short and warm.",
                    assistant("I suggest “A little cup of calm”. " + QUESTIONS["exact_words"])]
        sheet_for = {"edit": ("add a catchy line to the latte picture", "user"), "exact_words": (["A little cup of calm"], "user")}
        result, sheet = await self.plan(messages + ["Yes, use that."], "edit_image", sheet_for, ["woman"])
        self.assertEqual(result["questions"], [])
        self.assertEqual(sheet["exact_words"]["value"], ["A little cup of calm"])
        result, sheet = await self.plan(messages + ["Yes."], "edit_image", sheet_for, ["woman"])
        self.check(result, sheet, ["exact_words"], {})

    # The host rarely repeats the question word for word. "Your call" answers the question that was asked, in whatever words.
    async def test_your_call_answers_a_reworded_question(self):
        # (what the brief already says, the reworded question, the answer, the parameter that was open, what "your call" gives)
        cases = [("make a silent widescreen video about a kind gesture at a park bench", "How long do you want it, in seconds?", "up to you", "seconds", 10),
                 ("make a 6 second silent video about a kind gesture at a park bench", "Do you want it vertical or wide?", "you pick", "aspect", "16:9"),
                 ("make a 6 second widescreen video about a kind gesture at a park bench", "Any sound? Music, a voice, or silence?", "whatever", "sound", "ambient")]
        for brief, question, answer, parameter, expected in cases:
            with self.subTest(parameter=parameter):
                sheet_in = {"subject": ("a kind gesture at a park bench", "user"), "seconds": (6, "user"), "aspect": ("16:9", "user"), "sound": ("none", "user")}
                sheet_in[parameter] = (None, "missing")
                result, sheet = await self.plan([brief, assistant(question), answer], "create_video_from_text", sheet_in)
                self.assertEqual((sheet[parameter]["value"], sheet[parameter]["source"]), (expected, "default"))
                self.assertEqual(result["questions"], [])
                self.assertTrue(result["ready"])

    async def test_a_suggestion_that_mentions_a_shape_is_not_a_question_about_shape(self):
        result, sheet = await self.plan(
            ["make a 6 second silent video about a kind gesture at a park bench", assistant("I would make it vertical. Anything else you want to add?"), "your call"],
            "create_video_from_text",
            {"subject": ("a kind gesture at a park bench", "user"), "seconds": (6, "user"), "aspect": (None, "missing"), "sound": ("none", "user")})
        self.check(result, sheet, ["aspect"], {})   # "your call" answered "anything else?", not the shape: the shape is still asked

    async def test_the_tool_description_says_what_exact_words_is(self):
        self.assertIn("exact_words is a list of strings", self.tools[TOOL].description)
        self.assertIn("send [] when the user wants no text", self.tools[TOOL].description)

    # --- a "your call" said to an earlier question still holds (in testing, a lost user was asked again
    #     in 13 calls of 8 conversations, e.g. "you pick" to length and sound, then to the shape, then length and sound again) ---

    async def test_your_call_said_to_an_earlier_question_still_holds_after_a_later_question(self):
        history = ["make a video about a kind gesture at a park bench", assistant(QUESTIONS["seconds"] + " " + QUESTIONS["sound"]), "you pick",
                   assistant(QUESTIONS["aspect"]), "you pick"]
        for host_sheet in ({"seconds": (None, "missing"), "sound": (None, "missing"), "aspect": (None, "missing")},   # the host left them open
                           {"seconds": (15, "default"), "sound": ("none", "default"), "aspect": ("16:9", "default")}):   # the host chose them
            with self.subTest(host_sheet=host_sheet):
                result, sheet = await self.plan(history, "create_video_from_text", {"subject": ("a kind gesture at a park bench", "user"), **host_sheet})
                self.assertEqual(result["questions"], [])
                self.assertTrue(result["ready"])
                self.assertEqual({sheet[name]["source"] for name in ("seconds", "sound", "aspect")}, {"default"})

    async def test_your_call_does_not_cover_a_question_asked_after_it(self):
        result, sheet = await self.plan(
            ["make a widescreen video about a kind gesture at a park bench", assistant(QUESTIONS["seconds"]), "you pick", assistant(QUESTIONS["sound"]), "hmm not sure"],
            "create_video_from_text",
            {"subject": ("a kind gesture at a park bench", "user"), "seconds": (None, "missing"), "sound": (None, "missing")})
        self.check(result, sheet, ["sound"], {"seconds": YOUR_CALL_DEFAULTS["seconds"], "aspect": "16:9"})

    async def test_a_value_the_user_gives_after_your_call_wins(self):
        result, sheet = await self.plan(
            ["make a 6 second silent video about a kind gesture at a park bench", assistant(QUESTIONS["aspect"]), "you pick",
             assistant("I would make it widescreen. Anything else?"), "no wait, make it vertical for TikTok"],
            "create_video_from_text",
            {"subject": ("a kind gesture at a park bench", "user"), "seconds": (6, "user"), "sound": ("none", "user"), "aspect": ("16:9", "default")})
        self.assertEqual((sheet["aspect"]["value"], sheet["aspect"]["source"]), ("9:16", "user"))
        self.assertTrue(result["ready"])

    async def test_your_call_after_no_question_hands_nothing_over(self):
        # the host left its own questions out of the list: the tool cannot tell what "you pick" answers, so it asks
        result, sheet = await self.plan(
            ["make a video about a kind gesture at a park bench", "you pick"], "create_video_from_text",
            {"subject": ("a kind gesture at a park bench", "user"), "seconds": (None, "missing"), "sound": (None, "missing"), "aspect": (None, "missing")})
        self.check(result, sheet, ["seconds", "aspect", "sound"], {})

    async def test_your_call_answers_a_reworded_question_about_movement(self):
        result, sheet = await self.plan(
            ["make a 6 second silent widescreen video from this pic", assistant("What kind of movement do you want in it?"), "you pick"],
            "create_video_from_image",
            {"motion": ("a slow push in", "default"), "seconds": (6, "user"), "sound": ("none", "user"), "aspect": ("16:9", "user")}, ["woman"])
        self.assertEqual((sheet["motion"]["value"], sheet["motion"]["source"]), ("a slow push in", "default"))
        self.assertTrue(result["ready"])

    async def test_the_tool_description_asks_for_the_whole_conversation(self):
        self.assertIn("each message you sent the user, your questions included", self.tools[TOOL].description)


def _item_test(tool, brief, files, host_sheet, missing, values):
    async def test(self):
        await self.run_item(tool, brief, files, host_sheet, missing, values)
    return test


for _item, _kind, *_rest in ITEMS:
    setattr(PlanRequestTests, f"test_item_{_item}_{_kind}_brief", _item_test(*_rest))


if __name__ == "__main__":
    unittest.main()


from mcp_magichour.plan_request import plan_sheet  # noqa: E402


class QuestionRoundsTrial(unittest.TestCase):
    """Trial: one batch of every open question; MAGIC_HOUR_QUESTION_ROUNDS limits the batches."""

    PARAMS = [{"name": "subject", "value": "a dog running on a beach", "source": "user"},
              {"name": "seconds", "value": None, "source": "missing"}, {"name": "aspect", "value": None, "source": "missing"},
              {"name": "sound", "value": None, "source": "missing"}, {"name": "exact_words", "value": None, "source": "missing"}]

    def sheet(self, messages, rounds):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"MAGIC_HOUR_QUESTION_ROUNDS": str(rounds)}):
            return plan_sheet(messages, "create_video_from_text", self.PARAMS)

    def batch_text(self, result):
        return " ".join(f"{i}. {q['question']}" for i, q in enumerate(result["questions"], 1))

    def test_the_first_batch_asks_every_open_question_with_its_default(self):
        first = self.sheet([{"role": "user", "text": "make a video of a dog running on a beach"}], 1)
        self.assertFalse(first["ready"])
        self.assertGreaterEqual({q["param"] for q in first["questions"]}, {"seconds", "sound"})
        self.assertTrue(all("if_skipped" in q for q in first["questions"]))
        self.assertEqual(first["rounds"], {"used": 0, "limit": 1})

    def test_one_round_then_the_rest_is_inferred(self):
        first = self.sheet([{"role": "user", "text": "make a video of a dog running on a beach"}], 1)
        chat = [{"role": "user", "text": "make a video of a dog running on a beach"}, {"role": "assistant", "text": self.batch_text(first)},
                {"role": "user", "text": "make it 10 seconds"}]
        second = self.sheet(chat, 1)
        self.assertTrue(second["ready"])
        rows = {r["name"]: r for r in second["params"]}
        self.assertEqual(rows["seconds"]["value"], 10)
        self.assertTrue(all(r["source"] != "missing" for r in second["params"] if r["name"] != "exact_words"))

    def test_two_rounds_ask_again_for_what_is_still_open(self):
        first = self.sheet([{"role": "user", "text": "make a video of a dog running on a beach"}], 2)
        chat = [{"role": "user", "text": "make a video of a dog running on a beach"}, {"role": "assistant", "text": self.batch_text(first)},
                {"role": "user", "text": "make it 10 seconds"}]
        second = self.sheet(chat, 2)
        if first["questions"] and len(first["questions"]) > 1:
            self.assertFalse(second["ready"])
            self.assertNotIn("seconds", [q["param"] for q in second["questions"]])
            self.assertEqual(second["rounds"], {"used": 1, "limit": 2})

    def test_your_call_after_a_batch_ends_the_questions(self):
        first = self.sheet([{"role": "user", "text": "make a video of a dog running on a beach"}], 2)
        chat = [{"role": "user", "text": "make a video of a dog running on a beach"}, {"role": "assistant", "text": self.batch_text(first)},
                {"role": "user", "text": "10 seconds, your call on the rest"}]
        self.assertTrue(self.sheet(chat, 2)["ready"])

    def test_unset_keeps_the_old_behaviour(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MAGIC_HOUR_QUESTION_ROUNDS", None)
            result = plan_sheet([{"role": "user", "text": "make a video of a dog"}], "create_video_from_text", self.PARAMS)
        self.assertNotIn("rounds", result)
        self.assertLessEqual(len(result["questions"]), 3)


class CompositionPathOnlyTests(unittest.TestCase):
    """plan_request is in the video composition path only; a single call is as before."""

    def test_the_sheet_for_a_production_asks_like_a_text_to_video_sheet(self):
        messages = [{"role": "user", "text": "Make a 30-second ad for my candle shop, three shots."}]
        rows = [{"name": "subject", "value": "a candle shop ad", "source": "derived"}, {"name": "seconds", "value": 30, "source": "user"}]
        made = plan_sheet(messages, "plan_video_production", rows)
        plain = plan_sheet(messages, "create_video_from_text", rows)
        self.assertEqual(made["ready"], plain["ready"])
        self.assertEqual([q.get("param") or q for q in made.get("questions", [])], [q.get("param") or q for q in plain.get("questions", [])])

    def test_the_description_keeps_single_calls_out(self):
        from mcp_magichour.openapi_policies import API_TOOL_METADATA

        async def tools():
            with patch.dict("os.environ", {"MAGIC_HOUR_CONVERSATION": "off"}):
                server = create_mcp()
            async with Client(server) as client:
                return {t.name: t.description for t in await client.list_tools()}

        import asyncio
        described = asyncio.run(tools())
        self.assertIn("Do not call it before a single create or edit call", described["plan_request"])
        # Upstream owns this wording (#188, #216); the check is only that no planning route was added to it.
        image_to_video = API_TOOL_METADATA[("POST", "/v1/image-to-video")][2]
        for planner in ("plan_request", "plan_video_production", "plan_conversation"):
            self.assertNotIn(planner, image_to_video)
