"""Copies of the files this server uploads to Magic Hour, so the server can read them again.

Magic Hour's uploads are write-only: a `file_path` (api-assets/<account>/...) feeds a generation job, but no API
hands the bytes back. Joining, trimming or taking a frame from a user's clip happens on this server with ffmpeg, so
the server keeps its own copy of every file that passes through it, keyed by the file_path Magic Hour gave it.

- With KEPT_BLOB_READ_WRITE_TOKEN set (a private Vercel Blob store), the copy goes there under kept/. Every read
  needs the token; there is no public link. Never the public store of finished files (BLOB_READ_WRITE_TOKEN).
- Else a local folder (MAGIC_HOUR_KEPT_DIR, default <tmp>/magic-hour-kept).
- A copy is deleted KEEP_DAYS after its upload (Blob: x-ttl-days; the folder: by file age).
- A copy is read only for the account that uploaded it: the file_path names that account.

A file uploaded straight to Magic Hour, around this server, has no copy: `read` then raises NotKept and the caller
tells the host how to pass the file instead.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import time
from pathlib import Path

import httpx

# The REST calls of Vercel's own SDK (@vercel/blob 2.9.0): its API address and version, its private-access header.
BLOB_API = "https://vercel.com/api/blob"
BLOB_API_VERSION = "13"
TOKEN_VARIABLE = "KEPT_BLOB_READ_WRITE_TOKEN"
KEEP_DAYS = 7
logger = logging.getLogger(__name__)


class NotKept(Exception):
    """This server has no copy of the file: it never saw the bytes, or the copy expired."""


class NotYours(Exception):
    """The file_path names another account than the caller's."""


def is_file_path(value: str) -> bool:
    """A Magic Hour upload path, as get_upload_urls returns it (api-assets/<account>/<name>.<ext>)."""
    return value.startswith("api-assets/")


def account_of(file_path: str) -> str:
    """The account a file_path names: api-assets/<account>/<name>. Empty when it names none."""
    parts = file_path.split("/")
    return parts[1] if len(parts) >= 3 and parts[0] == "api-assets" else ""


def _key(file_path: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", file_path)


def _dir() -> Path:
    return Path(os.getenv("MAGIC_HOUR_KEPT_DIR", Path(tempfile.gettempdir()) / "magic-hour-kept"))


def _expired(copy: Path) -> bool:
    return time.time() - copy.stat().st_mtime > KEEP_DAYS * 86400


def _store_url(token: str, file_path: str) -> str:
    """The copy's private address. A read-write token is vercel_blob_rw_<store id>_<secret>."""
    return f"https://{token.split('_')[3]}.private.blob.vercel-storage.com/kept/{_key(file_path)}"


def keep(file_path: str, data: bytes) -> None:
    token = os.getenv(TOKEN_VARIABLE)
    if token:
        httpx.put(f"{BLOB_API}/", params={"pathname": f"kept/{_key(file_path)}"}, content=data, timeout=120,
                  headers={"authorization": f"Bearer {token}", "x-api-version": BLOB_API_VERSION,
                           "x-vercel-blob-access": "private", "x-add-random-suffix": "0", "x-allow-overwrite": "1",
                           "x-ttl-days": str(KEEP_DAYS)}).raise_for_status()
        return
    folder = _dir()
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.iterdir():   # the folder has no expiry of its own
        try:
            if old.is_file() and _expired(old):
                old.unlink()
        except OSError:
            pass   # another worker removed it first
    (folder / _key(file_path)).write_bytes(data)


def keep_if_possible(file_path: str, data: bytes) -> bool:
    """keep(), but a store that fails does not fail the upload: the file is on Magic Hour and the generation can go on.
    Only a later join, trim or frame of this file then asks the host to pass the file again."""
    try:
        keep(file_path, data)
        return True
    except (httpx.HTTPError, OSError) as error:
        logger.warning("kept_copy_failed exception_type=%s", type(error).__name__)
        return False


def read(file_path: str, out: Path, account: str) -> Path:
    """Write the kept copy of file_path to out. NotYours when file_path names another account than `account`,
    NotKept when this server has no copy."""
    if not account or account_of(file_path) != account:
        raise NotYours(file_path)
    token = os.getenv(TOKEN_VARIABLE)
    if token:
        response = httpx.get(_store_url(token, file_path), headers={"authorization": f"Bearer {token}"}, timeout=120)
        if response.status_code == 404:
            raise NotKept(file_path)
        response.raise_for_status()
        out.write_bytes(response.content)
        return out
    copy = _dir() / _key(file_path)
    if not copy.exists() or _expired(copy):
        raise NotKept(file_path)
    out.write_bytes(copy.read_bytes())
    return out


def not_kept_message(file_path: str) -> str:
    return (f"This server has no copy of {file_path}, and Magic Hour never hands a file back. The file was uploaded "
            f"straight to Magic Hour, more than {KEEP_DAYS} days ago, or the copy could not be stored. Upload the file "
            "again with upload_media (its URL, or its bytes as data_base64) and pass the file_path that returns, or "
            "pass a URL of the file. Nothing was charged.")


def not_yours_message(file_path: str) -> str:
    return (f"{file_path} was uploaded by another Magic Hour account. Pass a file this account uploaded with "
            "upload_media, or a URL of the file. Nothing was charged.")
