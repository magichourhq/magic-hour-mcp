from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

MCP_APP_VIEW_URI = "ui://magic-hour/project-result-v1.html"
MCP_APP_VIEW_PATH = "/app/project-result"
MCP_APP_VIEW_URL = f"https://mcp.magichour.ai{MCP_APP_VIEW_PATH}"
MCP_APP_MEDIA_ORIGIN = "https://videos.magichour.ai"
MCP_APP_VIEW_HTML = (Path(__file__).with_name("static") / "project-result.html").read_text(encoding="utf-8")


def _inline_asset(tag: str) -> str:
    match = re.search(rf"<{tag}>(.*?)</{tag}>", MCP_APP_VIEW_HTML, re.DOTALL)
    if match is None:
        raise RuntimeError(f"Built MCP App is missing its inline {tag} element.")
    return match.group(1)


def _csp_hash(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


MCP_APP_VIEW_CSP = (
    "default-src 'none'; "
    "connect-src https://mcp.magichour.ai; "
    "frame-ancestors https://chatgpt.com https://claude.ai; "
    "form-action 'none'; "
    f"img-src {MCP_APP_MEDIA_ORIGIN}; "
    f"media-src {MCP_APP_MEDIA_ORIGIN}; "
    f"script-src 'sha256-{_csp_hash(_inline_asset('script'))}'; "
    f"style-src 'sha256-{_csp_hash(_inline_asset('style'))}'; "
    "base-uri https://mcp.magichour.ai"
)
