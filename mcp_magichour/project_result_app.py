import os
from html import escape
from pathlib import Path

MCP_APP_VIEW_URI = "ui://magic-hour/project-result-v1.html"
MCP_APP_VIEW_PATH = "/app/project-result"
MCP_APP_ASSET_PATH = "/app/project-result-assets"
MCP_APP_SERVER_ORIGIN = "https://mcp.magichour.ai"
MCP_APP_ORIGIN = os.getenv(
    "MCP_APP_ORIGIN",
    f"https://{os.getenv('VERCEL_URL', 'mcp.magichour.ai')}",
).rstrip("/")
MCP_APP_MEDIA_ORIGIN = "https://videos.magichour.ai"
MCP_APP_DIST_PATH = Path(__file__).with_name("static") / "project-result"
MCP_APP_MIME_TYPE = "text/html;profile=mcp-app"
_MCP_APP_CSP_PLACEHOLDER = "__MCP_APP_CSP__"
MCP_APP_VIEW_CSP = (
    "default-src 'none'; "
    f"connect-src {MCP_APP_SERVER_ORIGIN} {MCP_APP_ORIGIN}; "
    "frame-ancestors https://chatgpt.com https://claude.ai; "
    "form-action 'none'; "
    f"img-src {MCP_APP_MEDIA_ORIGIN}; "
    f"media-src {MCP_APP_MEDIA_ORIGIN}; "
    f"script-src {MCP_APP_ORIGIN}; "
    f"style-src {MCP_APP_ORIGIN}; "
    "base-uri 'none'"
)


def read_mcp_app_html() -> str:
    try:
        app_html = (MCP_APP_DIST_PATH / "index.html").read_text(encoding="utf-8")
    except FileNotFoundError:
        raise RuntimeError("MCP App frontend is missing; run `npm --prefix web run build`.") from None
    return app_html.replace(
        _MCP_APP_CSP_PLACEHOLDER,
        escape(MCP_APP_VIEW_CSP, quote=False).replace('"', "&quot;"),
    )
