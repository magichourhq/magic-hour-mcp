import asyncio
import json
import unittest

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from mcp_magichour import gates

WALK_ANYWAY = ('Make a 30-second 16:9 short film called Walk Anyway. Start on a title card for 2 seconds: white text "WALK ANYWAY".\n'
               '1. Night, rain. The woman waits.\n2. The man walks up.\n3. He says out loud: "You came back."\n'
               'Sound: rain, and his one line. No music.')
ROBOT = "Make a 6-second video of a tiny robot watering a sunflower. Everything else is your call."


def server(mode: str) -> FastMCP:
    mcp = FastMCP("test")

    @mcp.tool(name="generate_music")   # a tool that spends credits, faked
    async def generate_music(prompt: str) -> dict:
        return {"made": prompt}

    @mcp.tool(name="create_video_from_text")
    async def create_video_from_text(prompt: str) -> dict:
        return {"made": prompt}

    gates.register_gates(mcp, mode)
    return mcp


def call(mcp: FastMCP, name: str, args: dict):
    async def go():
        async with Client(mcp) as client:
            result = await client.call_tool(name, args, raise_on_error=False)
            text = " ".join(c.text for c in result.content if hasattr(c, "text"))
            return result.is_error, (result.data if not result.is_error else None), text
    return asyncio.run(go())


def good_plan(request: str) -> dict:
    you = {"answer": "x", "source": "your call"}
    return {
        "request": request,
        "sheet": {"subject": {"answer": "Walk Anyway", "source": "user", "quote": "short film called Walk Anyway"},
                  "seconds": {"answer": "30", "source": "user", "quote": "30-second"}, "aspect": {"answer": "16:9", "source": "user", "quote": "16:9"},
                  "sound": {"answer": "rain and the line", "source": "user", "quote": "rain, and his one line"},
                  "exact_words": {"answer": "WALK ANYWAY", "source": "user", "quote": 'white text "WALK ANYWAY"'}, "look": you},
        "checklist": [{"id": "len", "want": "one 30 s 16:9 file", "check": "measured"},
                      {"id": "card", "want": 'a title card "WALK ANYWAY"', "check": "by eye"},
                      {"id": "line", "want": 'he says "You came back." once; rain; no music; sound as asked', "check": "by ear"}],
        "steps": [{"n": 1, "tool": "create_video_from_text", "model": "ltx-2.5", "seconds": 13, "does": "the scenes", "covers": ["len", "line"]},
                  {"n": 2, "tool": "generate_music", "does": "nothing really", "covers": ["card"]}],
    }


class BackstopTests(unittest.TestCase):
    def test_hard_signals(self):
        signals = gates.hard_signals(WALK_ANYWAY, [])
        self.assertTrue(any("30 seconds" in s for s in signals))
        self.assertTrue(any("quotes" in s for s in signals))
        self.assertTrue(any("shots" in s for s in signals))
        self.assertEqual(gates.hard_signals(ROBOT, []), [])

    def test_continue_request_is_square_twelve_seconds_not_a_tall_six(self):
        request = ("The attached clip is 6 seconds long. It was made from the attached portrait. Continue the clip for 6 more seconds. "
                   "Give me one 12-second video file.")
        values = {(i["kind"], i["value"]) for i in gates.brief_items(request, [])}
        self.assertIn(("seconds", "12"), values)
        self.assertNotIn("aspect", {k for k, _ in values})

    def test_continue_a_clip_is_hard(self):
        request = "The attached clip is 6 seconds long. Continue the clip for 6 more seconds."
        self.assertIn("continues an attached video", gates.hard_signals(request, ["clip.mp4"]))

    def test_brief_items(self):
        kinds = {(i["kind"], i["value"]) for i in gates.brief_items(WALK_ANYWAY, ["a.png"])}
        self.assertIn(("words", "WALK ANYWAY"), kinds)
        self.assertIn(("words", "You came back."), kinds)
        self.assertIn(("seconds", "30"), kinds)
        self.assertIn(("aspect", "16:9"), kinds)
        self.assertIn(("file", "a.png"), kinds)
        self.assertIn(("sound", "no music"), kinds)   # "No music" asks for no music
        self.assertNotIn(("sound", "says"), kinds)


class TokenTests(unittest.TestCase):
    def test_round_trip_and_tamper(self):
        token = gates.sign({"kind": "plan", "x": 1})
        self.assertEqual(gates.read(token, "plan")["x"], 1)
        with self.assertRaises(ToolError):
            gates.read(token + "x", "plan")
        with self.assertRaises(ToolError):
            gates.read(token, "approved")


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.mcp = server("refuse")

    def request(self, text: str = WALK_ANYWAY) -> str:
        err, data, _ = call(self.mcp, "start_request", {"original": text})
        self.assertFalse(err)
        self.assertIn("SIMPLE", data["comparison"])
        return data["request"]

    def test_simple_verdict_on_hard_request_is_refused(self):
        err, _, text = call(self.mcp, "classify_request", {"request": self.request(), "verdict": "simple", "reasons": ["short"]})
        self.assertTrue(err)
        self.assertIn("hard by the comparison", text)

    def test_simple_request_gets_a_token_and_can_create(self):
        err, data, _ = call(self.mcp, "classify_request", {"request": self.request(ROBOT), "verdict": "simple", "reasons": ["one idea, 6 s"]})
        self.assertFalse(err)
        err, data, _ = call(self.mcp, "create_video_from_text", {"prompt": "robot", "plan_token": data["approved"]})
        self.assertFalse(err)
        self.assertEqual(data["made"], "robot")

    def test_hard_path_problems_then_approval(self):
        request = self.request()
        err, data, _ = call(self.mcp, "classify_request", {"request": request, "verdict": "hard", "reasons": ["30 s"]})
        self.assertIn({"kind": "words", "value": "You came back."}, data["must_carry"])
        plan = good_plan(request)
        bad = json.loads(json.dumps(plan))
        bad["checklist"][2]["want"] = "rain only"                                    # drops the spoken line
        bad["sheet"]["seconds"]["quote"] = "forty seconds"                            # not the user's words
        bad["steps"].append({"n": 3, "tool": "no_such_tool", "does": "?", "covers": ["zzz"]})
        err, data, _ = call(self.mcp, "submit_plan", bad)
        problems = " | ".join(data["problems"])
        self.assertIn("You came back.", problems)
        self.assertIn("not the user's words", problems)
        self.assertIn("no tool 'no_such_tool'", problems)
        self.assertIn("'zzz'", problems)
        err, data, _ = call(self.mcp, "submit_plan", plan)
        self.assertTrue(data["ok"], data)
        self.assertIn("ltx-words", data["notes"])
        notes = list(data["notes"])
        reviews = [{"note": n, "steps": [1], "conflict": False, "change": "fine"} for n in notes]
        err, out, _ = call(self.mcp, "review_plan", {"plan": data["plan"], "reviews": reviews[:-1]})
        self.assertFalse(out["ok"])
        reviews[0]["conflict"] = True
        err, out, _ = call(self.mcp, "review_plan", {"plan": data["plan"], "reviews": reviews})
        self.assertIn("conflicts", out)
        reviews[0]["conflict"] = False
        err, out, _ = call(self.mcp, "review_plan", {"plan": data["plan"], "reviews": reviews})
        approved = out["approved"]
        err, made, _ = call(self.mcp, "create_video_from_text", {"prompt": "scene", "plan_token": approved})
        self.assertFalse(err)
        err, data, _ = call(self.mcp, "final_check", {"approved": approved, "results": [{"id": "len", "met": "yes", "evidence": "30.1 s"}]})
        self.assertFalse(data["ok"])
        results = [{"id": i, "met": "yes", "evidence": "seen"} for i in ("len", "card")] + [{"id": "line", "met": "no", "evidence": "not heard"}]
        err, data, _ = call(self.mcp, "final_check", {"approved": approved, "results": results})
        self.assertEqual([x["id"] for x in data["not_met"]], ["line"])

    def test_ask_user(self):
        plan = good_plan(self.request())
        plan["sheet"]["look"] = {"answer": "?", "source": "ask"}
        err, data, _ = call(self.mcp, "submit_plan", plan)
        self.assertEqual(data["ask_user"], [gates.QUESTIONS["look"]])


class ArgumentCheckTests(unittest.TestCase):
    """The server's own argument check runs before the gate. It must let plan_token through (a paid run
    lost its whole refuse side to this: every create call was rejected as having an unexpected argument)."""

    def test_plan_token_passes_the_argument_check(self):
        from mcp_magichour.mcp_errors import install_structured_tool_errors
        old = gates.MODE
        gates.MODE = "refuse"
        try:
            mcp = server("refuse")
            install_structured_tool_errors(mcp)
            token = gates.sign({"kind": "approved", "level": "simple", "original": ""})
            err, data, text = call(mcp, "generate_music", {"prompt": "x", "plan_token": token})
            self.assertFalse(err, text)
            self.assertEqual(data["made"], "x")
            with self.assertRaises(Exception) as caught:   # any other unknown argument is still refused
                call(mcp, "generate_music", {"prompt": "x", "other": 1})
            self.assertIn("unexpected", str(caught.exception))
        finally:
            gates.MODE = old


class MiddlewareTests(unittest.TestCase):
    def test_refuse_without_token(self):
        err, _, text = call(server("refuse"), "generate_music", {"prompt": "x"})
        self.assertTrue(err)
        self.assertIn("Refused", text)

    def test_refuse_tool_not_in_plan(self):
        token = gates.sign({"kind": "approved", "level": "hard", "original": "", "checklist": [], "tools": ["create_video_from_text"]})
        err, _, text = call(server("refuse"), "generate_music", {"prompt": "x", "plan_token": token})
        self.assertTrue(err)
        self.assertIn("not a tool in the approved plan", text)

    def test_warn_runs_and_says_so(self):
        err, data, text = call(server("warn"), "generate_music", {"prompt": "x"})
        self.assertFalse(err)
        self.assertIn("Gates skipped", text)

    def test_listing_adds_plan_token(self):
        async def go(mode):
            async with Client(server(mode)) as client:
                return {t.name: t.inputSchema for t in await client.list_tools()}
        refuse, warn = asyncio.run(go("refuse")), asyncio.run(go("warn"))
        self.assertIn("plan_token", refuse["generate_music"]["required"])
        self.assertNotIn("plan_token", warn["generate_music"].get("required", []))
        self.assertIn("plan_token", warn["generate_music"]["properties"])
        self.assertNotIn("plan_token", refuse["start_request"]["properties"])


if __name__ == "__main__":
    unittest.main()
