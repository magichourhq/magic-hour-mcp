"""plan_video_production through the MCP server, and the answer parsers of plan_request. No network, no credits."""

import asyncio
import json
import socket
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastmcp import Client
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import McpError

from mcp_magichour.openapi_server import create_mcp
from mcp_magichour.plan_request import QUESTIONS, parse_aspect, parse_seconds, parse_sound, plan_sheet
from mcp_magichour.video_production_tool import SPLIT_MAX_SECONDS, SPLIT_STRATEGY

STORIES = Path(__file__).parent / "fixtures" / "stories"
STORY_FIELDS = ("title", "aspect", "audio", "setting", "subjects", "beats")
ASYNC_CLIENT = httpx.AsyncClient


def story(prefix):
    full = json.loads(next(STORIES.glob(f"{prefix}-*.json")).read_text())
    return {key: full[key] for key in STORY_FIELDS}


def data(result):
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(next(c.text for c in result.content if c.type == "text"))


class VideoProductionToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []

        def upstream(request):
            self.requests.append((request.method, str(request.url)))
            return httpx.Response(500, json={"message": "plan_video_production must not call the API"})

        transport = httpx.MockTransport(upstream)
        self.patches = [
            patch("httpx.AsyncClient", side_effect=lambda *a, **k: ASYNC_CLIENT(*a, **{**k, "transport": transport})),
            patch("mcp_magichour.openapi_server.build_api_client", side_effect=lambda: ASYNC_CLIENT(base_url="https://api.example.test", transport=transport)),
            patch("mcp_magichour.openapi_server.current_authorization_header", return_value="Bearer synthetic-test-token"),
            patch("mcp_magichour.oauth_compat.current_authorization_header", return_value="Bearer synthetic-test-token"),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.caller = await self.enterAsyncContext(Client(create_mcp()))

    async def plan(self, prefix):
        return data(await self.caller.call_tool("plan_video_production", {"story": story(prefix)}))

    async def test_tool_is_listed_read_only_and_takes_a_story(self):
        tool = next(t for t in await self.caller.list_tools() if t.name == "plan_video_production")
        self.assertTrue(tool.annotations.readOnlyHint)
        self.assertEqual(tool.inputSchema["required"], ["story"])
        self.assertLessEqual(len(tool.description.encode()), 2048)

    async def test_walk_anyway_comes_back_as_ordered_clips_with_one_reference_each(self):
        p = await self.plan("c4")
        self.assertTrue(p["ok"])
        self.assertEqual((p["strategy"], p["max_seconds"]), (SPLIT_STRATEGY, SPLIT_MAX_SECONDS))
        self.assertEqual(p["seconds"], 30)
        self.assertEqual(p["join"], [c["n"] for c in p["clips"]])
        self.assertEqual(p["clips"][0]["kind"], "card")
        self.assertEqual(p["clips"][-1]["kind"], "card")
        for clip in p["clips"]:
            self.assertIn(clip["reference"]["kind"], ("none", "last_frame", "picture", "still"))
            self.assertLessEqual(clip["seconds"], SPLIT_MAX_SECONDS)
            self.assertTrue(all(n < clip["n"] for n in clip["depends_on"]))

    async def test_a_picture_goes_into_the_plan_as_the_one_named_reference(self):
        p = await self.plan("c5")
        first = p["clips"][0]
        self.assertEqual(first["reference"], {"kind": "picture", "subject": "bottle", "file": "bench-mockup-fragrance.png"})
        self.assertEqual(first["steps"][-1]["tool"], "create_video_from_text")
        self.assertIn("@bottle", first["steps"][-1]["prompt"])

    async def test_the_plan_does_not_judge_by_length_or_idea_count(self):
        # one routing rule (a purpose goes to production, also for one shot); the old 15 s / 3-ideas
        # flag told hosts a one-shot ad did not need production
        p = await self.plan("c2")
        self.assertNotIn("needs_production", p)
        tool = next(t for t in await self.caller.list_tools() if t.name == "plan_video_production")
        self.assertNotIn("longer than 15 seconds", tool.description)
        self.assertIn("This is true for one shot too", tool.description)

    async def test_a_bad_story_comes_back_with_named_problems_and_no_clips(self):
        bad = story("c4")
        bad["beats"][0]["exact_words"] = []
        bad["beats"][1]["subjects"] = ["nobody"]
        p = data(await self.caller.call_tool("plan_video_production", {"story": bad}))
        self.assertFalse(p["ok"])
        self.assertEqual(p["clips"], [])
        self.assertIn("beat 1: a card needs exact_words, or `picture` (a picture to hold unchanged, such as a logo)", p["problems"])
        self.assertIn("beat 2: unknown subject nobody", p["problems"])

    async def test_a_story_with_a_wrong_shape_is_refused_by_the_schema(self):
        bad = story("c1")
        bad["aspect"] = "4:3"
        with self.assertRaises((ToolError, McpError)):
            await self.caller.call_tool("plan_video_production", {"story": bad})

    async def test_the_tool_makes_no_api_request(self):
        await self.plan("c4")
        await self.plan("c5")
        self.assertEqual(self.requests, [])


class ProductionRunTests(unittest.IsolatedAsyncioTestCase):
    """Make and join the clips of a plan against an in-memory API. The file steps (ffmpeg) are replaced."""

    async def asyncSetUp(self):
        self.posts, self.uploads, self.joined = [], [], []
        self.video_status = "complete"
        self.refuse = set()   # POST paths the fake API refuses

        self.paths = []

        def upstream(request):
            path = request.url.path
            self.paths.append(path)
            if request.url.host == "storage.example.test":
                self.uploads.append(path)
                return httpx.Response(200)
            if request.url.host == "videos.magichour.ai":
                return httpx.Response(200, content=b"video-bytes")
            if path == "/v1/files/upload-urls":
                return httpx.Response(200, json={"items": [{"upload_url": "https://storage.example.test/frame.jpg", "file_path": "api-assets/test/frame.jpg"}]})
            if path == "/v1/account":   # whose key it is; kept copies are read only for that account
                if getattr(self, "refuse_account", False):
                    return httpx.Response(401, json={"message": "invalid key"})
                return httpx.Response(200, json={"id": "test"})
            if request.method == "POST" and path in self.refuse:
                return httpx.Response(422, json={"message": "refused"})
            if request.method == "POST":
                self.posts.append((path, json.loads(request.content)))
                return httpx.Response(200, json={"id": f"job-{len(self.posts)}", "credits_charged": 10 if "image-generator" in path else 144})
            if path.startswith("/v1/video-projects/"):
                done = self.video_status == "complete"
                return httpx.Response(200, json={"id": path.rsplit("/", 1)[1], "status": self.video_status,
                                                 "downloads": [{"url": f"https://videos.magichour.ai{path}/output.mp4"}] if done else []})
            if path.startswith("/v1/audio-projects/"):
                return httpx.Response(200, json={"id": "music", "status": "complete", "downloads": [{"url": "https://videos.magichour.ai/music/output.mp3"}]})
            if path.startswith("/v1/image-projects/"):
                return httpx.Response(200, json={"id": "still", "status": "complete", "downloads": [{"url": "https://images.example.test/still/output.png"}]})
            raise AssertionError(f"Unhandled mock request: {request.method} {path}")

        transport = httpx.MockTransport(upstream)
        self.films = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        self.kept = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {"MAGIC_HOUR_KEPT_DIR": str(self.kept)}))
        self.enterContext(patch.dict(os.environ, {"BLOB_READ_WRITE_TOKEN": ""}))
        self.enterContext(patch.dict(os.environ, {"MAGIC_HOUR_VOICE_OVER": "video"}))   # these tests cover the video-model narration; the cloner test sets its own

        def fake_join(files, out, audio=True, seconds=None, size=None, overlap=0.0, even=False):
            self.joined.append(([f.name for f in files], audio))
            out.write_bytes(b"film")
            return out

        self.fitted = []
        for target, value in [
            ("httpx.AsyncClient", lambda *a, **k: ASYNC_CLIENT(*a, **{**k, "transport": transport})),
            ("mcp_magichour.openapi_server.build_api_client", lambda: ASYNC_CLIENT(base_url="https://api.example.test", transport=transport)),
            ("mcp_magichour.openapi_server.current_authorization_header", lambda: "Bearer synthetic-test-token"),
            ("mcp_magichour.oauth_compat.current_authorization_header", lambda: "Bearer synthetic-test-token"),
            ("mcp_magichour.media_tools.have_ffmpeg", lambda: True),
            ("mcp_magichour.media_tools.last_frame", lambda video, out: (out.write_bytes(b"frame"), out)[1]),
            ("mcp_magichour.media_tools.join", fake_join),
            ("mcp_magichour.media_tools.fit_length", lambda file, seconds, how, out: (self.fitted.append((file.name, seconds, how)), file)[1]),
            ("mcp_magichour.media_tools.probe", lambda file: {"seconds": {"clip-1.mp4": 8.4, "clip-2.mp4": 18.4, "clip-3.mp4": 4.0}.get(file.name, 30.2), "width": self.film_size[0], "height": self.film_size[1], "audio": True}),
            ("mcp_magichour.media_tools.speech_spans", lambda audio, **k: [(0.2, 3.0), (4.0, 6.0), (7.0, 9.0), (10.0, 12.0)]),
            ("mcp_magichour.media_tools.add_sound", self.fake_add_sound),
            ("mcp_magichour.media_tools.lift_bed", self.fake_lift_bed),
            ("mcp_magichour.media_tools.draw_captions", self.fake_captions),
            ("mcp_magichour.media_tools.have_captions", lambda: True),
            ("mcp_magichour.media_tools.loudness_db", lambda file: -18.0),
            ("mcp_magichour.video_production_tool.FILMS_DIR", self.films),
            ("mcp_magichour.video_production_tool._transcribe", self.fake_transcribe),
        ]:
            p = patch(target, side_effect=value) if callable(value) else patch(target, value)
            p.start()
            self.addCleanup(p.stop)
        self.caller = await self.enterAsyncContext(Client(create_mcp()))

    film_size = (854, 480)   # the size the fake probe reports; a 9:16 story sets it
    heard = None   # what the fake transcriber hears; None = no transcriber on the server

    async def fake_transcribe(self, audio):
        return self.heard

    def fake_add_sound(self, film, out, narration=None, parts=None, music=None, spoken=None, music_first=False, layers=None, music_swell=False, music_at=0.0):
        self.sound = {"narration": narration.name if narration else None, "music": music.name if music else None, "parts": parts,
                      "spoken": spoken, "music_first": music_first}
        out.write_bytes(b"film with sound")
        return {"lufs_before": -30.0, "lufs": -16.0, "speech": [], "layers": ["speech", "effects"]}

    lifted_bed = None   # what the fake street lift returns; None = the street is loud enough

    def fake_lift_bed(self, film, spoken, clips, out):
        self.lift = {"spoken": spoken, "clips": clips}
        if self.lifted_bed:
            out.write_bytes(b"film with the street lifted")
        return self.lifted_bed

    def fake_captions(self, film, timed, out):
        self.captions = timed
        out.write_bytes(b"film with captions")
        return out

    async def call(self, name, **arguments):
        if name == "plan_video_production":   # the host has checked the plan (the continuity question)
            arguments["answers"] = {"continuity": "ok", **(arguments.get("answers") or {})}
        return data(await self.caller.call_tool(name, arguments))

    async def planned(self, prefix):
        return await self.call("plan_video_production", story=story(prefix))

    async def planned_with_voice(self):
        s = story("c5")
        lines = [["Some scents fade by noon.", "This one stays."], ["It starts soft."], ["One spray is enough."], ["Mockup Fragrance."]]
        for beat, says in zip(s["beats"], lines):
            beat["says"] = says
        s.update(narrator="a calm, warm woman's voice", music="soft piano", captions=True)
        self.film_size = (480, 858)
        return await self.call("plan_video_production", story=s)

    async def test_the_sound_tool_makes_one_narration_and_one_music_track(self):
        p = await self.planned_with_voice()
        self.assertIn("create_production_sound", p["how"])
        made = await self.call("create_production_sound", production=p["production"])
        (narration_path, narration), (music_path, music) = self.posts
        self.assertEqual((narration_path, narration["audio"], narration["model"]), ("/v1/text-to-video", True, "ltx-2.5"))
        self.assertEqual(narration["style"]["prompt"], p["sound"]["narration"]["prompt"])
        self.assertEqual((music_path, music["duration"], music["style"]), ("/v1/ai-music-generator", 32, {"prompt": "soft piano", "instrumental": True}))
        self.assertEqual((made["narration"], made["music"]), ("job-1", "job-2"))

    async def test_with_the_switch_on_and_a_sample_the_voice_cloner_reads_the_script(self):
        # In testing, the cloner read a test script exactly; the video model 2 in 8. Off in what we ship until upstream approves.
        with patch.dict(os.environ, {"MAGIC_HOUR_VOICE_OVER": "cloner"}):
            s = story("c5")
            for beat, says in zip(s["beats"], [["Some scents fade by noon."], ["It starts soft."], ["One spray is enough."], ["Mockup Fragrance."]]):
                beat["says"] = says
            s.update(narrator="a calm voice", captions=True, voice_sample="https://example.com/voice.mp3")
            self.film_size = (480, 858)
            p = await self.call("plan_video_production", story=s)
            self.assertEqual(p["sound"]["narration"]["tool"], "voice_cloner")
            made = await self.call("create_production_sound", production=p["production"])
            (path, body), = self.posts
            self.assertEqual(path, "/v1/ai-voice-cloner")
            self.assertEqual(body["assets"], {"audio_file_path": "https://example.com/voice.mp3"})
            self.assertEqual(body["style"]["prompt"], "Some scents fade by noon. It starts soft. One spray is enough. Mockup Fragrance.")
            self.assertIn("wait_for_audio_project", made["next"])
            film = await self.call("join_production_clips", production=p["production"], made={"1": "a", "2": "b", "3": "c"},
                                   sound={"narration": made["narration"]})
        self.assertEqual(self.sound["narration"], "narration")   # fetched as an audio project
        self.assertTrue(film["sound"]["narration"])

    async def test_a_narration_that_says_other_words_is_not_met(self):
        # In testing, the narration said made-up words and the speech check passed it.
        p = await self.planned_with_voice()
        self.heard = "What Van Holtz of TMO Experience called the Mewmaw"
        film = await self.call("join_production_clips", production=p["production"], made={"1": "a", "2": "b", "3": "c"},
                               sound={"narration": "n", "music": "m"})
        voice = next(c for c in film["checked"] if c["want"].startswith("Voice-over"))
        self.assertEqual(voice["result"], "no")
        self.assertIn("Van Holtz", voice["got"])
        self.assertIn(voice["want"], film["not_met"])

    async def test_a_narration_that_says_the_script_passes_the_word_check(self):
        p = await self.planned_with_voice()
        self.heard = "Some scents fade by noon. This one stays. It starts soft. One spray is enough. Mockup fragrance."
        film = await self.call("join_production_clips", production=p["production"], made={"1": "a", "2": "b", "3": "c"},
                               sound={"narration": "n", "music": "m"})
        voice = next(c for c in film["checked"] if c["want"].startswith("Voice-over"))
        self.assertEqual(voice["result"], "yes")
        self.assertIn("100%", voice["got"])
        self.assertEqual(film["sound"]["words_match"], 1.0)

    async def test_a_refused_music_call_still_returns_the_paid_narration(self):
        p = await self.planned_with_voice()
        self.refuse = {"/v1/ai-music-generator"}
        made = await self.call("create_production_sound", production=p["production"])
        self.assertEqual(made["narration"], "job-1")
        self.assertNotIn("music", made)
        self.assertIn("HTTP 422", made["music_error"])
        self.assertIn("Do not call create_production_sound again", made["next"])

    async def test_a_join_with_a_key_magic_hour_refuses_downloads_nothing(self):
        # Review: with a junk key, the join downloaded and rendered the clips before any call checked the key.
        p = await self.planned_with_voice()
        from mcp_magichour import video_production_tool as vpt
        vpt._ACCOUNTS.clear()
        self.addCleanup(vpt._ACCOUNTS.clear)
        self.refuse_account = True
        self.paths.clear()
        with self.assertRaisesRegex(ToolError, "/v1/account"):
            await self.call("join_production_clips", production=p["production"], made={"1": "https://cdn.test/a.mp4"})
        self.assertEqual(self.paths, ["/v1/account"])
        self.assertEqual(self.joined, [])

    async def test_a_production_that_unpacks_too_large_is_refused(self):
        import base64, zlib
        bomb = base64.urlsafe_b64encode(zlib.compress(b" " * (50 * 1024 * 1024))).decode()
        with self.assertRaisesRegex(ToolError, "`production` is not valid"):
            await self.call("join_production_clips", production=bomb, made={"1": "a"})

    async def test_a_film_longer_than_the_music_model_gets_300_seconds_of_music(self):
        from mcp_magichour.video_production import plan_sound
        clips = [{"seconds": 15, "parts": [{"seconds": 15, "text": "Rain on a window."}]} for _ in range(22)]   # 330 s
        self.assertEqual(plan_sound({"music": "soft piano", "beats": []}, clips)["music"]["seconds"], 300)

    async def test_a_plan_with_no_sound_has_no_sound_to_make(self):
        p = await self.planned("c4")
        with self.assertRaises(ToolError):
            await self.call("create_production_sound", production=p["production"])
        self.assertEqual(self.posts, [])

    async def test_the_join_puts_each_part_of_the_narration_on_its_clip_and_draws_the_captions(self):
        p = await self.planned_with_voice()   # clips: [1], [2, 3], [4]
        made = {"1": "a", "2": "b", "3": "c"}
        with self.assertRaises(ToolError):   # the plan has sound: the join asks for it
            await self.call("join_production_clips", production=p["production"], made=made)
        film = await self.call("join_production_clips", production=p["production"], made=made, sound={"narration": "n", "music": "m"})
        self.assertEqual(film["sound"], {"narration": True, "music": True, "captions": True, "loudness_lufs": -16.0, "speech_seconds": 8.8,
                                      "captions_left_out": 1})   # "Mockup Fragrance." is on the end card
        self.assertEqual((self.sound["narration"], self.sound["music"]), ("narration.mp4", "music"))
        self.assertEqual(len(self.sound["parts"]), 4)
        starts = [part["at"] for part in self.sound["parts"]]
        self.assertEqual(starts, sorted(starts))
        self.assertEqual(starts[0], 0.5)                       # 0.5 s after its clip starts
        self.assertAlmostEqual(starts[2] - starts[1], 9, 5)    # the second beat of clip 2 starts 9 s into that clip
        self.assertEqual([c["text"] for c in self.captions], ["Some scents fade by noon.", "This one stays.", "It starts soft.", "One spray is enough."])
        self.assertTrue(all(a["end"] <= b["start"] + 1e-6 for a, b in zip(self.captions, self.captions[1:])))
        self.assertFalse(any(self.joined[-1][1]))              # the clips themselves are silent
        self.assertEqual([c["result"] for c in film["checked"]], ["yes"] * len(film["checked"]))
        self.assertEqual(film["not_met"], [])
        self.assertTrue(any("looks the same" in line for line in film["unchecked"]))

    async def test_a_narration_with_no_speech_is_not_met(self):
        # In testing, the narration call came back silent and the join counted it as placed.
        p = await self.planned_with_voice()
        with patch("mcp_magichour.media_tools.speech_spans", lambda audio, **k: [(0.0, 0.3)]):
            film = await self.call("join_production_clips", production=p["production"], made={"1": "a", "2": "b", "3": "c"},
                                   sound={"narration": "n", "music": "m"})
        voice = next(c for c in film["checked"] if c["want"].startswith("Voice-over"))
        self.assertEqual(voice["result"], "no")
        self.assertIn("no voice was made", voice["got"])
        self.assertIn(voice["want"], film["not_met"])

    async def test_the_plan_carries_a_checklist_and_the_join_reports_what_is_not_met(self):
        p = await self.planned("c4")   # Walk Anyway: scene sound wanted, no narration
        self.assertIn("file", [line["id"] for line in p["checklist"]])
        self.assertIn("scene-sound", [line["id"] for line in p["checklist"]])
        with patch("mcp_magichour.media_tools.loudness_db", return_value=None):   # a film that came out silent
            film = await self.call("join_production_clips", production=p["production"], made={"1": "a", "2": "b", "3": "c", "4": "d"})
        self.assertIn("The scenes have their own sound (as the beats describe)", film["not_met"])
        self.assertEqual(next(c for c in film["checked"] if c["want"].startswith("One file"))["result"], "yes")

    async def test_the_plan_carries_the_production_and_the_rounds(self):
        p = await self.planned("c4")
        self.assertEqual(p["rounds"], [[1, 2, 4], [3]])
        self.assertTrue(p["production"])
        self.assertIn("join_production_clips", p["how"])
        self.assertEqual(self.posts, [])

    async def test_a_clip_with_no_reference_is_one_text_to_video_call_with_the_plan_prompt(self):
        p = await self.planned("c4")
        made = await self.call("create_production_clip", production=p["production"], clip=2)
        path, body = self.posts[0]
        self.assertEqual((path, len(self.posts)), ("/v1/text-to-video", 1))
        self.assertEqual((body["model"], body["resolution"], body["end_seconds"], body["aspect_ratio"], body["audio"]), ("minimax-h3", "480p", 13, "16:9", True))   # the scene model
        self.assertEqual(body["style"]["prompt"], p["clips"][1]["steps"][-1]["prompt"])
        self.assertEqual((made["id"], made["clip"], made["credits_charged"]), ("job-1", 2, 144))

    async def test_a_clip_that_continues_starts_from_the_last_frame_of_the_finished_clip(self):
        p = await self.planned("c4")
        await self.call("create_production_clip", production=p["production"], clip=3, made={"2": "job-a"})
        path, body = self.posts[0]
        self.assertEqual(path, "/v1/image-to-video")
        self.assertEqual(body["assets"], {"image_file_path": "api-assets/test/frame.jpg"})
        self.assertEqual(self.uploads, ["/frame.jpg"])
        self.assertNotIn("aspect_ratio", body)

    async def test_a_clip_whose_source_is_missing_or_not_finished_makes_no_paid_call(self):
        p = await self.planned("c4")
        with self.assertRaises(ToolError):
            await self.call("create_production_clip", production=p["production"], clip=3)
        self.video_status = "rendering"
        with self.assertRaises(ToolError):
            await self.call("create_production_clip", production=p["production"], clip=3, made={"2": "job-a"})
        self.assertEqual(self.posts, [])

    async def test_a_card_is_a_still_and_then_a_clip_from_that_still(self):
        p = await self.planned("c4")
        made = await self.call("create_production_clip", production=p["production"], clip=1)
        self.assertEqual([path for path, _ in self.posts], ["/v1/ai-image-generator", "/v1/image-to-video"])
        self.assertIn("WALK ANYWAY", self.posts[0][1]["style"]["prompt"])
        self.assertEqual(self.posts[1][1]["assets"], {"image_file_path": "https://images.example.test/still/output.png"})
        self.assertEqual(made["credits_charged"], 154)

    async def test_a_refused_video_call_returns_the_paid_still(self):
        # Review: the still was paid, the video call failed, and the error named no still: the next try paid again.
        p = await self.planned("c4")
        self.refuse = {"/v1/image-to-video"}
        with self.assertRaises(ToolError) as caught:
            await self.call("create_production_clip", production=p["production"], clip=1)
        message = str(caught.exception)
        self.assertIn("HTTP 422", message)
        self.assertIn("https://images.example.test/still/output.png", message)
        self.assertIn("Do not make it again", message)

    async def test_a_card_with_the_users_logo_is_made_by_the_image_editor_from_it(self):
        # every card is generated; the logo goes to the image editor as its input picture
        s = story("c4")
        s["beats"][0].update(picture="api-assets/x/logo.png", background="#0B1D3A")
        p = await self.call("plan_video_production", story=s)
        await self.call("create_production_clip", production=p["production"], clip=1)
        self.assertEqual([path for path, _ in self.posts], ["/v1/ai-image-editor", "/v1/image-to-video"])
        edit = self.posts[0][1]
        self.assertEqual((edit["model"], edit["assets"]), ("qwen-image-2.1", {"image_file_paths": ["api-assets/x/logo.png"]}))
        self.assertIn("flat navy", edit["style"]["prompt"])

    async def test_each_pictured_subject_is_a_named_reference(self):
        p = await self.planned("c5")
        await self.call("create_production_clip", production=p["production"], clip=2)
        path, body = self.posts[0]
        self.assertEqual((path, body["model"]), ("/v1/text-to-video", "minimax-h3"))
        self.assertEqual(body["references"], [{"name": "presenter", "file_path": "bench-presenter.png"},
                                              {"name": "bottle", "file_path": "bench-mockup-fragrance.png"}])   # each pictured subject

    async def test_the_street_under_a_spoken_line_is_lifted_with_no_music_or_narration(self):
        # In testing, the street under her line was heard only at full volume
        s = story("c4")
        s["beats"][1]["spoken"] = ["Pay yourself first."]
        p = await self.call("plan_video_production", story=s)
        made = {"1": "a", "2": "b", "3": "c", "4": "d"}
        film = await self.call("join_production_clips", production=p["production"], made=made)
        self.assertEqual(len(self.lift["spoken"]), 1)
        self.assertEqual(len(self.lift["clips"]), 1)
        self.assertTrue(self.lift["clips"][0][0] <= self.lift["spoken"][0][0] < self.lift["spoken"][0][1] <= self.lift["clips"][0][1])
        self.assertNotIn("background_lifted", film)
        self.assertEqual((self.films / film["download_url"].rsplit("/", 1)[1]).read_bytes(), b"film")   # the street was loud enough
        self.lifted_bed = {"raised_db": 30, "voice_lufs": -15.5, "background_lifted": -38.5}
        film = await self.call("join_production_clips", production=p["production"], made=made)
        self.assertEqual(film["background_lifted"]["raised_db"], 30)
        self.assertEqual((self.films / film["download_url"].rsplit("/", 1)[1]).read_bytes(), b"film with the street lifted")

    async def test_the_user_can_keep_the_background_as_it_was_made(self):
        # the lift is a best-practice rule; the user's words override it
        s = story("c4")
        s["beats"][1]["spoken"] = ["Pay yourself first."]
        s["lift_background"] = False
        p = await self.call("plan_video_production", story=s)
        self.lift, self.lifted_bed = None, {"raised_db": 30, "voice_lufs": -15.5, "background_lifted": -38.5}
        film = await self.call("join_production_clips", production=p["production"], made={"1": "a", "2": "b", "3": "c", "4": "d"})
        self.assertIsNone(self.lift)
        self.assertNotIn("background_lifted", film)
        self.assertEqual((self.films / film["download_url"].rsplit("/", 1)[1]).read_bytes(), b"film")

    async def test_the_music_sings_only_when_the_user_asks(self):
        s = story("c5")
        s.update(music="an upbeat pop song, a woman sings about the morning", music_vocals=True)
        p = await self.call("plan_video_production", story=s)
        self.posts.clear()
        await self.call("create_production_sound", production=p["production"])
        (path, music), = [(a, b) for a, b in self.posts if a == "/v1/ai-music-generator"]
        self.assertFalse(music["style"]["instrumental"])

    async def test_the_join_takes_every_finished_clip_in_story_order(self):
        p = await self.planned("c4")
        made = {"1": "a", "2": "b", "3": "c", "4": "d"}
        film = await self.call("join_production_clips", production=p["production"], made=made)
        # each clip keeps the sound its video step was made with; the two cards are made silent
        self.assertEqual(self.joined, [(["clip-1.mp4", "clip-2.mp4", "clip-3.mp4", "clip-4.mp4"], [False, True, True, False])])
        self.assertRegex(film["download_url"], r"/films/[0-9a-f]{32}\.mp4$")
        self.assertTrue((self.films / film["download_url"].rsplit("/", 1)[1]).exists())
        self.assertEqual(film["clips"], 4)
        # clip 2 ran long (18.4 s for 13 s) and clip 3 starts from its last frame: its head is cut, its end kept
        self.assertEqual(self.fitted, [("clip-2.mp4", 13.0, "head")])
        self.assertEqual(film["kept_last_frame"], [{"clip": 2, "how": "head"}])
        self.assertEqual(self.posts, [])
        with self.assertRaises(ToolError):
            await self.call("join_production_clips", production=p["production"], made={"1": "a", "2": "b"})

    async def test_an_upload_through_the_server_can_be_joined_and_framed(self):
        import base64
        up = await self.call("upload_media", file_name="clip.mp4", data_base64=base64.b64encode(b"user-clip").decode())
        self.assertEqual((up["file_path"], up["kind"], up["bytes"]), ("api-assets/test/frame.jpg", "video", 9))
        self.assertEqual(self.uploads, ["/frame.jpg"])
        self.paths.clear()
        film = await self.call("join_videos", videos=[up["file_path"], "proj-1"])
        self.assertEqual(self.joined, [(["source.jpg", "source.mp4"], True)])   # each file in its own folder, named by its type
        self.assertNotIn("/v1/video-projects/api-assets/test/frame.jpg", self.paths)
        self.assertRegex(film["download_url"], r"/films/[0-9a-f]{32}\.mp4$")
        frame = await self.call("get_last_frame", video=up["file_path"])
        self.assertEqual(frame["file_path"], "api-assets/test/frame.jpg")

    async def test_an_upload_by_url_is_downloaded_then_kept(self):
        up = await self.call("upload_media", file_name="jet.mp4", url="https://videos.magichour.ai/u/jet.mp4")
        self.assertEqual(up["kind"], "video")
        self.assertEqual(list(self.kept.iterdir())[0].read_bytes(), b"video-bytes")

    async def test_a_file_uploaded_around_the_server_fails_clearly_and_spends_nothing(self):
        for name, arguments in [("join_videos", {"videos": ["api-assets/test/never-seen.mp4"]}),
                                ("get_last_frame", {"video": "api-assets/test/never-seen.mp4"})]:
            with self.subTest(tool=name), self.assertRaisesRegex(ToolError, "uploaded straight to Magic Hour.*upload_media"):
                await self.call(name, **arguments)
        self.assertFalse([p for p in self.paths if "never-seen" in p])
        self.assertEqual(self.posts, [])

    async def test_the_production_join_takes_the_users_own_clip_by_file_path(self):
        import base64
        p = await self.planned("c4")
        up = await self.call("upload_media", file_name="mine.mov", data_base64=base64.b64encode(b"mine").decode())
        await self.call("join_production_clips", production=p["production"], made={"1": up["file_path"], "2": "b", "3": "c", "4": "d"})
        self.assertEqual(self.joined[0][0], ["clip-1.mp4", "clip-2.mp4", "clip-3.mp4", "clip-4.mp4"])
        self.assertNotIn("/v1/video-projects/api-assets/test/frame.jpg", self.paths)

    def users_clip_story(self, file_path, **extra):
        return {"title": "My clip", "aspect": "16:9", "setting": "A sunny kitchen, soft daylight.", "captions": True,
                "beats": [{"seconds": 7, "text": "The user's own clip.", "source": file_path, "clip": 1,
                           "says": ["Make mornings easier.", "Prepare the night before."]}], **extra}

    async def test_a_voice_over_goes_on_the_users_own_clip(self):
        # The user's clip is a beat of its own (`source`); nothing is generated for it.
        import base64
        up = await self.call("upload_media", file_name="mine.mp4", data_base64=base64.b64encode(b"user-clip").decode())
        p = await self.call("plan_video_production", story=self.users_clip_story(up["file_path"]))
        clip = p["clips"][0]
        self.assertEqual((clip["made_by"], clip["kind"], clip["steps"][0]["credits"]), ("user", "source", 0))
        self.assertIn("source-1", [line["id"] for line in p["checklist"]])
        made = await self.call("create_production_clip", production=p["production"], clip=1)
        self.assertEqual((made["made_by"], made["credits_charged"]), ("user", 0))
        sound = await self.call("create_production_sound", production=p["production"])
        self.assertEqual([path for path, _ in self.posts], ["/v1/text-to-video"])   # the narration only, no clip
        self.paths.clear()
        film = await self.call("join_production_clips", production=p["production"], made={}, sound={"narration": sound["narration"]})
        self.assertEqual(self.joined[-1], (["clip-1.mp4"], [True]))   # the user's clip keeps its own sound
        self.assertFalse([path for path in self.paths if "api-assets" in path])   # read from the kept copy
        self.assertTrue(film["sound"]["narration"])

    async def test_speech_in_the_users_clip_is_never_lowered_unless_the_host_says_it_is_background(self):
        # In testing, the user's clip's own sound was lowered 10 dB as "effects", even when someone speaks in it.
        import base64
        up = await self.call("upload_media", file_name="mine.mp4", data_base64=base64.b64encode(b"user-clip").decode())
        for speech, kept in [(None, [(0.0, 8.4)]), (True, [(0.0, 8.4)]), (False, [])]:
            story = self.users_clip_story(up["file_path"], music="Soft piano.")
            story["beats"][0].pop("says")
            if speech is not None:
                story["beats"][0]["source_speech"] = speech
            p = await self.call("plan_video_production", story=story)
            sound = await self.call("create_production_sound", production=p["production"])
            film = await self.call("join_production_clips", production=p["production"], made={}, sound={"music": sound["music"]})
            self.assertEqual(self.sound["spoken"], kept, speech)
            self.assertEqual(film["sound"].get("user_clip_as_speech"), [1] if kept else None)

    async def test_a_voice_over_on_the_users_clip_warns_when_the_clip_may_have_speech(self):
        import base64
        up = await self.call("upload_media", file_name="mine.mp4", data_base64=base64.b64encode(b"user-clip").decode())
        p = await self.call("plan_video_production", story=self.users_clip_story(up["file_path"]))
        self.assertIn("voice-over-on-speech", [a["note"] for a in p["advice"]])
        story = self.users_clip_story(up["file_path"])
        story["beats"][0]["source_speech"] = False
        p = await self.call("plan_video_production", story=story)
        self.assertNotIn("voice-over-on-speech", [a["note"] for a in p["advice"]])

    async def test_a_voice_over_with_too_many_words_for_its_beat_is_warned_at_the_plan(self):
        # In testing: 12 words in a 4 s beat; the last words ran past the end of the film.
        story = {"title": "Jet", "aspect": "16:9", "setting": "An airfield at dawn.", "beats": [
            {"seconds": 4, "text": "A jet on the runway.", "clip": 1, "says": ["The F-35A Lightning II is powered by a single engine."]},
            {"seconds": 4, "text": "The jet takes off.", "clip": 1,
             "says": ["It is the conventional takeoff and landing version, built for normal runways."]}]}
        p = await self.call("plan_video_production", story=story)
        paced = [a["warning"] for a in p["advice"] if a["note"] == "voice-over-pace"]
        self.assertEqual(len(paced), 1)
        self.assertIn("Beat 2: the voice-over has 12 words for 4 s", paced[0])

    async def test_a_new_clip_continues_from_the_users_clip(self):
        # The next clip starts from the last frame of the user's clip.
        import base64
        up = await self.call("upload_media", file_name="mine.mp4", data_base64=base64.b64encode(b"user-clip").decode())
        story = self.users_clip_story(up["file_path"])
        story["beats"][0].pop("says")
        story["beats"].append({"seconds": 5, "text": "She turns and walks out of frame.", "clip": 2, "link": "continues"})
        p = await self.call("plan_video_production", story=story)
        self.assertEqual(p["clips"][1]["reference"]["kind"], "last_frame")
        self.assertEqual(p["clips"][1]["depends_on"], [1])
        await self.call("create_production_clip", production=p["production"], clip=2, made={})
        (path, body), = self.posts
        self.assertEqual(path, "/v1/image-to-video")
        await self.call("join_production_clips", production=p["production"], made={"2": "job-1"})
        self.assertEqual(self.joined[-1], (["clip-1.mp4", "clip-2.mp4"], [True, False]))   # the new clip is silent

    async def test_the_users_clip_is_cut_to_source_start_and_end(self):
        # Keep the first 5 s of the user's 8 s clip and make a new ending from the frame at 5 s.
        import base64
        cuts = []

        def fake_trim(source, out, start=0.0, end=None):
            cuts.append((source.name, start, end))
            out.write_bytes(b"part")
            return out

        up = await self.call("upload_media", file_name="mine.mp4", data_base64=base64.b64encode(b"user-clip").decode())
        story = self.users_clip_story(up["file_path"])
        story["beats"][0].pop("says")
        story["beats"][0].update(seconds=5, source_end=5)
        story["beats"].append({"seconds": 3, "text": "The plane lands on the desk.", "clip": 2, "link": "continues"})
        p = await self.call("plan_video_production", story=story)
        self.assertEqual(p["clips"][0]["steps"][0]["end"], 5)
        with patch("mcp_magichour.media_tools.trim", side_effect=fake_trim):
            await self.call("create_production_clip", production=p["production"], clip=2, made={})
            await self.call("join_production_clips", production=p["production"], made={"2": "job-1"})
        self.assertEqual(cuts, [("clip-1.mp4", 0.0, 5), ("clip-1.mp4", 0.0, 5)])   # the last frame and the join both use the cut
        self.assertEqual(self.joined[-1][0], ["clip-1-part.mp4", "clip-2.mp4"])
        bad = self.users_clip_story(up["file_path"])
        bad["beats"][0].update(source_start=4, source_end=2)
        self.assertFalse((await self.call("plan_video_production", story=bad))["ok"])

    async def test_the_users_picture_is_held_unchanged_for_its_seconds(self):
        # A screenshot shown "full and unchanged" is a `source` beat, held still by code (0 credits).
        import base64
        held = []

        def fake_still(image, seconds, out):
            held.append((image.suffix, image.read_bytes()[:4], seconds))
            out.write_bytes(b"held")
            return out

        png = b"\x89PNG\r\n\x1a\n" + b"screen"
        up = await self.call("upload_media", file_name="app.png", data_base64=base64.b64encode(png).decode())
        story = self.users_clip_story(up["file_path"])
        story["beats"][0].update(seconds=8)
        story["beats"][0].pop("says"); story.pop("captions")
        p = await self.call("plan_video_production", story=story)
        self.assertEqual((p["clips"][0]["kind"], p["clips"][0]["steps"][0]["credits"]), ("source", 0))
        with patch("mcp_magichour.media_tools.still_video", side_effect=fake_still):
            await self.call("join_production_clips", production=p["production"], made={})
        self.assertEqual(held, [(".png", b"\x89PNG", 8.0)])
        self.assertEqual(self.joined[-1][0], ["clip-1-held.mp4"])

    async def test_a_source_beat_must_stand_alone_and_be_used_as_it_is(self):
        bad = self.users_clip_story("api-assets/x/mine.mp4")
        bad["beats"][0]["reference"] = {"kind": "none"}
        p = await self.call("plan_video_production", story=bad)
        self.assertFalse(p["ok"])
        two = self.users_clip_story("api-assets/x/mine.mp4")
        two["beats"].append({"seconds": 3, "text": "A second beat.", "clip": 1})
        p = await self.call("plan_video_production", story=two)
        self.assertFalse(p["ok"])
        self.assertEqual(self.posts, [])

    async def test_upload_needs_one_source_and_a_known_type(self):
        for arguments in [{"file_name": "a.mp4"}, {"file_name": "a.mp4", "url": "https://x.test/a.mp4", "data_base64": "eA=="},
                          {"file_name": "a.exe", "data_base64": "eA=="}, {"file_name": "a.mp4", "data_base64": "not base64!"}]:
            with self.subTest(arguments=arguments), self.assertRaises(ToolError):
                await self.call("upload_media", **arguments)
        self.assertEqual(self.uploads, [])

    async def test_without_ffmpeg_the_file_steps_say_so_and_spend_nothing(self):
        p = await self.planned("c4")
        with patch("mcp_magichour.media_tools.have_ffmpeg", return_value=False):
            for name, arguments in [("create_production_clip", {"clip": 3, "made": {"2": "a"}}), ("join_production_clips", {"made": {"1": "a"}})]:
                with self.subTest(tool=name), self.assertRaises(ToolError):
                    await self.call(name, production=p["production"], **arguments)
        self.assertEqual(self.posts, [])

    async def test_a_changed_production_value_is_refused(self):
        with self.assertRaises(ToolError):
            await self.call("create_production_clip", production="not-a-plan", clip=1)
        self.assertEqual(self.posts, [])


class AnswerParserTests(unittest.TestCase):
    def test_a_length_needs_a_unit_or_a_short_answer(self):
        self.assertEqual(parse_seconds("a 10-second clip"), 10)
        self.assertEqual(parse_seconds("for 6 seconds"), 6)
        self.assertEqual(parse_seconds("10"), 10)
        self.assertEqual(parse_seconds("about 15"), 15)
        self.assertEqual(parse_seconds("make a short clip of her"), 8)
        self.assertIsNone(parse_seconds("a social post announcing 20% off"))
        self.assertIsNone(parse_seconds("the words are 'Open 9 to 5 every day'"))

    def test_no_music_is_not_a_request_for_music(self):
        self.assertIsNone(parse_sound("Rain only. No music and no narrator."))
        self.assertEqual(parse_sound("with the natural sound of the waves, no music, no words"), "ambient")
        self.assertEqual(parse_sound("with music"), "music")
        self.assertEqual(parse_sound("no sound"), "none")
        self.assertEqual(parse_sound("a warm voice-over"), "narration")

    def test_a_platform_or_a_ratio_gives_the_shape(self):
        self.assertEqual(parse_aspect("Instagram story"), "9:16")
        self.assertEqual(parse_aspect("printed A4 (3:4)"), "9:16")
        self.assertEqual(parse_aspect("for YouTube"), "16:9")
        self.assertIsNone(parse_aspect("for my meditation app"))


class SheetRuleTests(unittest.TestCase):
    WAVES = "a clip of waves at sunset for my meditation app"

    def test_the_create_tool_argument_names_are_read_as_sheet_rows(self):
        result = plan_sheet([{"role": "user", "text": self.WAVES}], "create_video_from_text", [
            {"name": "style.prompt", "value": "Gentle ocean waves at sunset.", "source": "derived"},
            {"name": "end_seconds", "value": 5, "source": "default"},
            {"name": "aspect_ratio", "value": "16:9", "source": "default"},
            {"name": "audio", "value": True, "source": "default"},
            {"name": "model", "value": "ltx-2.5", "source": "default"},
        ])
        sheet = {row["name"]: row for row in result["params"]}
        self.assertEqual(sheet["subject"]["value"], "Gentle ocean waves at sunset.")
        self.assertNotIn("model", sheet)
        self.assertEqual([q["param"] for q in result["questions"]], ["seconds", "aspect", "sound"])

    def test_your_call_is_not_a_default_for_a_question_that_was_not_asked(self):
        asked = " ".join(QUESTIONS[name] for name in ("subject", "seconds", "aspect"))
        result = plan_sheet([
            {"role": "user", "text": self.WAVES}, {"role": "assistant", "text": asked},
            {"role": "user", "text": "Your call on what it shows, 10 seconds, for TikTok."},
        ], "create_video_from_text", [
            {"name": "subject", "value": "waves at sunset", "source": "derived"},
            {"name": "seconds", "value": 10, "source": "user"}, {"name": "aspect", "value": "9:16", "source": "user"},
            {"name": "sound", "value": None, "source": "missing"},
        ])
        sheet = {row["name"]: row for row in result["params"]}
        self.assertEqual((sheet["seconds"]["value"], sheet["aspect"]["value"]), (10, "9:16"))
        self.assertEqual(sheet["sound"]["source"], "missing")
        self.assertEqual(result["questions"], [{"param": "sound", "question": QUESTIONS["sound"]}])

    def test_a_file_name_is_not_read_as_a_shape(self):
        result = plan_sheet(
            [{"role": "user", "text": "make a short clip of her\n[Attached: portrait-woman.jpg — Magic Hour file_path: api-assets/x/y.jpg]"}],
            "create_video_from_image", [{"name": "motion", "value": None, "source": "missing"}],
            [{"name": "portrait-woman.jpg", "kind": "image", "aspect": "1:1"}])
        sheet = {row["name"]: row for row in result["params"]}
        self.assertEqual(sheet["aspect"]["source"], "missing")
        self.assertEqual(sheet["seconds"]["value"], 8)

    def test_a_length_from_an_earlier_message_is_kept_on_the_next_turn(self):
        result = plan_sheet([
            {"role": "user", "text": "make a short clip of her"},
            {"role": "assistant", "text": QUESTIONS["motion"] + " " + QUESTIONS["sound"]},
            {"role": "user", "text": "She smiles and looks around a little. Silent."},
        ], "create_video_from_image", [
            {"name": "motion", "value": "she smiles and looks around a little", "source": "user"},
            {"name": "sound", "value": "none", "source": "user"}, {"name": "seconds", "value": None, "source": "missing"},
            {"name": "aspect", "value": "1:1", "source": "user"},
        ], [{"name": "portrait-woman.jpg", "kind": "image", "aspect": "1:1"}])
        sheet = {row["name"]: row for row in result["params"]}
        self.assertEqual((sheet["seconds"]["value"], sheet["seconds"]["source"]), (8, "derived"))
        self.assertNotIn("seconds", [q["param"] for q in result["questions"]])

    def test_a_paraphrase_of_a_stated_edit_is_kept_and_an_unstated_edit_is_asked(self):
        car = [{"name": "car.jpg", "kind": "image"}]
        stated = plan_sheet([{"role": "user", "text": "remove the people from this"}], "edit_image", [
            {"name": "edit", "value": "Remove all people and fill their place with natural background.", "source": "derived"}], car)
        self.assertEqual(stated["questions"], [])
        unstated = plan_sheet([{"role": "user", "text": "I need a shot of my perfume for the website"}], "edit_image", [
            {"name": "edit", "value": "Create a product shot for a website", "source": "user"}], car)
        self.assertIn("edit", [q["param"] for q in unstated["questions"]])

    def test_an_open_look_or_optional_words_row_is_not_asked(self):
        result = plan_sheet([{"role": "user", "text": "remove the people from this"}], "edit_image", [
            {"name": "edit", "value": "remove the people", "source": "user"},
            {"name": "look", "value": None, "source": "missing"}, {"name": "exact_words", "value": None, "source": "missing"},
            {"name": "keep", "value": None, "source": "missing"}], [{"name": "car.jpg", "kind": "image"}])
        self.assertTrue(result["ready"])
        poster = plan_sheet([{"role": "user", "text": "make a poster for our open mic night"}], "generate_image", [
            {"name": "subject", "value": "a poster for our open mic night", "source": "user"},
            {"name": "look", "value": None, "source": "missing"}, {"name": "exact_words", "value": None, "source": "missing"}])
        self.assertEqual([q["param"] for q in poster["questions"]], ["exact_words"])

    def test_an_answer_about_sound_is_read_from_the_newest_message(self):
        result = plan_sheet([
            {"role": "user", "text": self.WAVES}, {"role": "assistant", "text": QUESTIONS["sound"]},
            {"role": "user", "text": "gentle ambient wave sound"},
        ], "create_video_from_text", [
            {"name": "subject", "value": "waves at sunset", "source": "derived"},
            {"name": "seconds", "value": 10, "source": "user"}, {"name": "aspect", "value": "9:16", "source": "user"},
        ])
        self.assertEqual({row["name"]: row["value"] for row in result["params"]}["sound"], "ambient")


if __name__ == "__main__":
    unittest.main()


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_download_names_its_client(self):
        """Wikimedia answers 403 to the default httpx User-Agent."""
        import tempfile
        from mcp_magichour import video_production_tool as vpt
        seen = {}

        def answer(request):
            seen["ua"] = request.headers.get("user-agent")
            return httpx.Response(200, content=b"jpg")
        real = httpx.AsyncClient
        with patch.object(vpt.httpx, "AsyncClient", lambda **kw: real(**{**kw, "transport": httpx.MockTransport(answer)})), tempfile.TemporaryDirectory() as d:
            await vpt._download("https://commons.wikimedia.org/wiki/Special:FilePath/x.jpg", Path(d) / "x.jpg")
        self.assertEqual(seen["ua"], vpt.USER_AGENT)


class PublicLinkTests(unittest.IsolatedAsyncioTestCase):
    """The server fetches links the host passes. A link must not reach the server's own network."""

    async def asyncSetUp(self):
        import tempfile
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {"MAGIC_HOUR_ALLOW_PRIVATE_URLS": ""}))

    def resolving(self, answers):
        """getaddrinfo that gives each host the addresses in `answers`."""
        async def getaddrinfo(host, port, **kw):
            return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in answers[host]]
        loop = asyncio.get_running_loop()
        return patch.object(loop, "getaddrinfo", getaddrinfo)

    async def test_a_link_to_a_private_or_local_address_is_refused(self):
        from mcp_magichour import video_production_tool as vpt
        for host, address in [("metadata.test", "169.254.169.254"), ("local.test", "127.0.0.1"), ("lan.test", "10.0.0.7"),
                              ("v6.test", "::1"), ("mapped.test", "::ffff:127.0.0.1")]:
            with self.subTest(address), self.resolving({host: [address]}), self.assertRaises(ToolError) as caught:
                await vpt._download(f"http://{host}/x.mp4", self.folder / "x.mp4")
            self.assertIn("private or local", str(caught.exception))

    async def test_a_redirect_to_a_private_address_is_refused(self):
        from mcp_magichour import video_production_tool as vpt
        sent = []

        async def inner(request):
            sent.append((str(request.url), request.headers["host"], request.extensions.get("sni_hostname")))
            return httpx.Response(302, headers={"location": "http://inside.test/secret"})
        with self.resolving({"cdn.test": ["93.184.215.14"], "inside.test": ["192.168.1.5"]}), \
                patch.object(httpx.AsyncHTTPTransport, "handle_async_request", lambda self, request: inner(request)), \
                self.assertRaises(ToolError):
            await vpt._download("https://cdn.test/a.mp4", self.folder / "a.mp4")
        # The first hop went to the checked address, with the name kept for the certificate; the second never left.
        self.assertEqual(sent, [("https://93.184.215.14/a.mp4", "cdn.test", "cdn.test")])

    async def test_a_download_stops_at_the_size_limit_and_a_web_page_is_refused(self):
        from mcp_magichour import video_production_tool as vpt
        real = httpx.AsyncClient

        def client(body, length=None):
            headers = {"content-length": str(length)} if length else {}
            return patch.object(vpt.httpx, "AsyncClient", lambda **kw: real(**{**kw, "transport": httpx.MockTransport(lambda r: httpx.Response(200, content=body, headers=headers))}))
        with client(b"\x00" * 2048), self.assertRaises(ToolError) as caught:
            await vpt._download("https://cdn.test/big.mp4", self.folder / "big.mp4", max_bytes=1024)
        self.assertIn("at most", str(caught.exception))
        with client(b"#EXTM3U\n#EXTINF:2,\n/tmp/magic-hour-kept/another-account.mp4\n"), self.assertRaises(ToolError) as caught:
            await vpt._download("https://cdn.test/x.m3u8", self.folder / "x.mp4")
        self.assertIn("playlist", str(caught.exception))
        with client(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 100):
            await vpt._download("https://cdn.test/ok.mp4", self.folder / "ok.mp4")

    async def test_a_link_to_this_servers_own_film_is_read_from_its_folder(self):
        from mcp_magichour import video_production_tool as vpt
        name = "ab" * 16 + ".mp4"
        with patch.object(vpt, "FILMS_DIR", self.folder), patch.dict(os.environ, {"MAGIC_HOUR_MCP_PUBLIC_URL": "http://127.0.0.1:8000"}):
            (self.folder / name).write_bytes(b"\x00film")
            out = await vpt._download(f"http://127.0.0.1:8000/films/{name}", self.folder / "copy.mp4")
            self.assertEqual(out.read_bytes(), b"\x00film")
            with self.assertRaises(ToolError):
                await vpt._download("http://127.0.0.1:8000/films/../secret.mp4", self.folder / "copy2.mp4")


class PrivacyAndDiskTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_narration_goes_to_openai_only_when_the_check_is_turned_on(self):
        import tempfile
        from mcp_magichour import media_tools, video_production_tool as vpt
        narration = Path(self.enterContext(tempfile.TemporaryDirectory())) / "narration.wav"
        media_tools._ffmpeg(["-f", "lavfi", "-i", "sine=d=0.3", str(narration)])   # a real sound, so only the switch stops it
        sent = []

        class Refused:
            def __init__(self, *a, **k):
                sent.append(k)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}), patch.object(vpt.httpx, "AsyncClient", Refused):
            os.environ.pop("MAGIC_HOUR_TRANSCRIBE_CHECK", None)
            self.assertIsNone(await vpt._transcribe(narration))
        self.assertEqual(sent, [])

    async def test_a_film_stored_in_blob_leaves_no_local_copy(self):
        import tempfile
        from mcp_magichour import video_production_tool as vpt
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        film, layers = folder / "f.mp4", folder / "f.layers"
        film.write_bytes(b"film")
        layers.mkdir()
        (layers / "music.wav").write_bytes(b"wav")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BLOB_READ_WRITE_TOKEN", None)
            vpt._drop_local(film)   # served from this folder: kept
        self.assertTrue(film.exists() and layers.exists())
        with patch.dict(os.environ, {"BLOB_READ_WRITE_TOKEN": "test"}):
            vpt._drop_local(film)
        self.assertFalse(film.exists() or layers.exists())
