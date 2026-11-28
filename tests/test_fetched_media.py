"""A fetched file is judged by its bytes, not by its link; mix_audio has a length limit."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from PIL import Image

from mcp_magichour import media_helpers_tool as helpers
from mcp_magichour import media_tools


class FetchedByContentTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def serve(self, source: Path) -> None:
        async def fetch(media, out):
            out.write_bytes(source.read_bytes())
            return out
        self.enterContext(patch.object(helpers, "_fetch", fetch))

    def fetch(self, link, kinds=("image", "video")):
        folder = Path(tempfile.mkdtemp(dir=self.dir))
        return asyncio.run(helpers._fetch_media(link, folder, kinds))

    def test_a_picture_link_with_no_suffix_is_a_picture(self):
        Image.new("RGB", (64, 48), "red").save(self.dir / "shot.jpg")
        self.serve(self.dir / "shot.jpg")
        file, picture = self.fetch("https://cdn.test/image?id=7")
        assert picture and file.suffix == ".jpg"

    def test_a_tiff_is_a_picture_saved_as_png(self):
        # In testing, a TIFF from a museum link was read as a 0-second video.
        Image.new("RGB", (64, 48), "blue").save(self.dir / "scan.tiff")
        self.serve(self.dir / "scan.tiff")
        file, picture = self.fetch("https://museum.test/scan.tif")
        assert picture and file.suffix == ".png" and Image.open(file).size == (64, 48)

    def test_a_sound_file_is_refused_where_a_picture_or_video_is_needed(self):
        # In testing, reframe_media on an mp3 failed with "division by zero" and adjust_colours made a 0x0 "video".
        media_tools._ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(self.dir / "tone.mp3")])
        self.serve(self.dir / "tone.mp3")
        with self.assertRaises(ToolError) as caught:
            self.fetch("https://cdn.test/tone.mp3")
        assert "sound file with no picture" in str(caught.exception)
        file, picture = self.fetch("https://cdn.test/tone.mp3", ("audio", "video"))
        assert not picture and file.exists()

    def test_a_file_nothing_can_read_is_refused_plainly(self):
        (self.dir / "junk.bin").write_bytes(bytes(range(256)) * 8)
        self.serve(self.dir / "junk.bin")
        with self.assertRaises(ToolError) as caught:
            self.fetch("https://cdn.test/clip.mp4")
        assert "that this server can read" in str(caught.exception)


class MixLengthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        mcp = FastMCP("test")
        helpers.register_media_helpers(mcp)
        self.caller = await self.enterAsyncContext(Client(mcp))

    async def test_a_mix_longer_than_ten_minutes_is_refused_before_ffmpeg_runs(self):
        # In testing, mix_audio with seconds=1e6 wrote 63 GB to the disk.
        for arguments in ({"seconds": 1e6}, {"fade_out": -2}, {"seconds": 0}):
            with self.assertRaises(Exception) as caught:
                await self.caller.call_tool("mix_audio", {"tracks": [{"media": "https://cdn.test/a.wav"}], **arguments})
            assert "validation" in str(caught.exception).lower() or "less than" in str(caught.exception).lower()
        with self.assertRaises(Exception):
            await self.caller.call_tool("mix_audio", {"tracks": [{"media": "https://cdn.test/a.wav", "at": 3000}]})

    async def test_tracks_that_end_after_ten_minutes_are_refused(self):
        media_tools._ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(self.dir / "tone.wav")])

        async def fetch(media, folder, kinds=("image", "video")):
            return self.dir / "tone.wav", False
        rendered = []
        with patch.object(helpers, "_fetch_media", fetch), patch.object(media_tools, "mix_audio", lambda *a: rendered.append(a)):
            with self.assertRaises(ToolError) as caught:
                await self.caller.call_tool("mix_audio", {"tracks": [{"media": "a"}, {"media": "b", "at": 600}]})
        assert "at most 600 s" in str(caught.exception) and not rendered


class PlainErrorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.mcp = FastMCP("test")
        helpers.register_media_helpers(self.mcp)
        self.caller = await self.enterAsyncContext(Client(self.mcp))

    async def test_an_ffmpeg_failure_names_no_command_line_or_folder(self):
        # In testing, reframe_media with color "notacolour" returned the whole ffmpeg command, with the server's folders.
        Image.new("RGB", (64, 64), "red").save(self.dir / "logo.png")

        async def fetch(media, folder, kinds=("image", "video")):
            return self.dir / "logo.png", True
        with patch.object(helpers, "_fetch_media", fetch):
            with self.assertRaises(ToolError) as caught:
                await self.caller.call_tool("reframe_media", {"media": "a", "aspect": "9:16", "how": "pad", "color": "notacolour"})
        message = str(caught.exception)
        assert "ffmpeg could not process the file" in message
        assert str(self.dir) not in message and "-filter" not in message and "Command" not in message

    async def test_a_tool_error_and_other_tools_keep_their_own_text(self):
        @self.mcp.tool
        def other() -> int:
            raise ValueError("left as it is")
        with self.assertRaises(ToolError) as caught:
            await self.caller.call_tool("other", {})
        assert "left as it is" in str(caught.exception)
        with self.assertRaises(ToolError) as caught:
            await self.caller.call_tool("mix_audio", {"tracks": []})
        assert "Give at least one track." in str(caught.exception)


if __name__ == "__main__":
    unittest.main()
