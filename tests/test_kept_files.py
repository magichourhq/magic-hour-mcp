"""Kept copies of uploads are private, expire after 7 days and are read only for the uploader's account.
No network: Blob and Magic Hour calls are fakes. 0 credits."""
import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastmcp.exceptions import ToolError

from mcp_magichour import kept_files, openapi_server
from mcp_magichour import video_production_tool as vpt

MINE = "api-assets/acc1/clip.mp4"
THEIRS = "api-assets/acc2/clip.mp4"
TOKEN = "vercel_blob_rw_StoreAbc123_secretpart"


class Response:
    def __init__(self, status: int, content: bytes = b""):
        self.status_code, self.content = status, content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class LocalFolderTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        env = {k: v for k, v in os.environ.items() if k != kept_files.TOKEN_VARIABLE}
        self.enterContext(patch.dict(os.environ, {**env, "MAGIC_HOUR_KEPT_DIR": str(self.dir)}, clear=True))

    def test_the_uploader_reads_the_copy_back(self):
        kept_files.keep(MINE, b"bytes")
        self.assertEqual(kept_files.read(MINE, self.dir / "out.mp4", "acc1").read_bytes(), b"bytes")

    def test_another_account_never_reads_the_copy(self):
        kept_files.keep(THEIRS, b"bytes")
        for account in ("acc1", ""):
            with self.assertRaises(kept_files.NotYours):
                kept_files.read(THEIRS, self.dir / "out.mp4", account)
        for odd in ("api-assets/clip.mp4", "api-assets//clip.mp4", "api-assets/acc1"):   # names no account
            with self.assertRaises(kept_files.NotYours):
                kept_files.read(odd, self.dir / "out.mp4", "acc1")

    def test_a_copy_older_than_7_days_is_gone(self):
        kept_files.keep(MINE, b"old")
        copy = self.dir / kept_files._key(MINE)
        eight_days = time.time() - 8 * 86400
        os.utime(copy, (eight_days, eight_days))
        with self.assertRaises(kept_files.NotKept):
            kept_files.read(MINE, self.dir / "out.mp4", "acc1")
        kept_files.keep("api-assets/acc1/new.mp4", b"new")   # the next upload removes it
        self.assertFalse(copy.exists())
        self.assertTrue((self.dir / kept_files._key("api-assets/acc1/new.mp4")).exists())

    def test_the_public_store_token_alone_never_takes_a_kept_copy(self):
        with patch.dict(os.environ, {"BLOB_READ_WRITE_TOKEN": TOKEN}), \
             patch.object(kept_files.httpx, "put", side_effect=AssertionError("sent to Blob")):
            kept_files.keep(MINE, b"bytes")
        self.assertTrue((self.dir / kept_files._key(MINE)).exists())


class PrivateStoreTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {kept_files.TOKEN_VARIABLE: TOKEN}))
        self.out = Path(tempfile.mkdtemp()) / "out.mp4"

    def test_the_upload_is_private_has_no_random_name_and_expires_in_7_days(self):
        with patch.object(kept_files.httpx, "put", return_value=Response(200)) as put:
            kept_files.keep(MINE, b"bytes")
        args, kwargs = put.call_args
        self.assertEqual((args[0], kwargs["params"]), ("https://vercel.com/api/blob/", {"pathname": "kept/api-assets_acc1_clip.mp4"}))
        headers = kwargs["headers"]
        self.assertEqual((headers["x-vercel-blob-access"], headers["x-ttl-days"], headers["x-add-random-suffix"]), ("private", "7", "0"))
        self.assertEqual(headers["authorization"], f"Bearer {TOKEN}")

    def test_the_read_goes_to_the_private_address_with_the_token(self):
        with patch.object(kept_files.httpx, "get", return_value=Response(200, b"bytes")) as get:
            kept_files.read(MINE, self.out, "acc1")
        self.assertEqual(get.call_args[0][0], "https://StoreAbc123.private.blob.vercel-storage.com/kept/api-assets_acc1_clip.mp4")
        self.assertEqual(get.call_args[1]["headers"], {"authorization": f"Bearer {TOKEN}"})
        self.assertEqual(self.out.read_bytes(), b"bytes")

    def test_a_store_that_fails_does_not_fail_the_upload(self):
        # Review: a Blob error after the Magic Hour upload failed the whole call, and a paid clip with it.
        with patch.object(kept_files.httpx, "put", side_effect=kept_files.httpx.ConnectError("down")), \
             self.assertLogs(kept_files.logger, "WARNING") as logs:
            self.assertFalse(kept_files.keep_if_possible(MINE, b"bytes"))
        assert TOKEN not in "".join(logs.output) and "ConnectError" in logs.output[0]
        with patch.object(kept_files.httpx, "put", return_value=Response(200)):
            self.assertTrue(kept_files.keep_if_possible(MINE, b"bytes"))

    def test_a_missing_or_expired_copy_is_not_kept_and_another_account_makes_no_call(self):
        with patch.object(kept_files.httpx, "get", return_value=Response(404)):
            with self.assertRaises(kept_files.NotKept):
                kept_files.read(MINE, self.out, "acc1")
        with patch.object(kept_files.httpx, "get", side_effect=AssertionError("read another account's copy")):
            with self.assertRaises(kept_files.NotYours):
                kept_files.read(THEIRS, self.out, "acc1")


class OwnerCheckTests(unittest.TestCase):
    """The tools read a copy through _read_kept, which asks Magic Hour whose key it is."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        env = {k: v for k, v in os.environ.items() if k != kept_files.TOKEN_VARIABLE}
        self.enterContext(patch.dict(os.environ, {**env, "MAGIC_HOUR_KEPT_DIR": str(self.dir)}, clear=True))
        self.enterContext(patch.object(openapi_server, "current_authorization_header", return_value="Bearer key-of-acc1"))
        self.calls = []

        async def api(method, path, body=None):
            self.calls.append(path)
            return {"id": "acc1"}
        self.enterContext(patch.object(vpt, "_api", api))
        vpt._ACCOUNTS.clear()
        kept_files.keep(MINE, b"mine")
        kept_files.keep(THEIRS, b"theirs")

    def test_the_uploader_gets_the_file_and_the_account_is_asked_once(self):
        for _ in range(2):
            self.assertEqual(asyncio.run(vpt._read_kept(MINE, self.dir / "out.mp4")).read_bytes(), b"mine")
        self.assertEqual(self.calls, ["/v1/account"])

    def test_another_accounts_file_path_is_refused_with_a_message(self):
        with self.assertRaises(ToolError) as refused:
            asyncio.run(vpt._read_kept(THEIRS, self.dir / "out.mp4"))
        self.assertIn("another Magic Hour account", str(refused.exception))
        self.assertFalse((self.dir / "out.mp4").exists())


if __name__ == "__main__":
    unittest.main()
