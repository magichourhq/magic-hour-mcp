from pathlib import Path

MCP_APP_VIEW_URI = "ui://magic-hour/project-result-v1.html"
MCP_APP_VIEW_PATH = "/app/project-result"
MCP_APP_VIEW_URL = f"https://mcp.magichour.ai{MCP_APP_VIEW_PATH}"
MCP_APP_ASSET_PATH = "/app/project-result-assets"
MCP_APP_MEDIA_ORIGIN = "https://videos.magichour.ai"
MCP_APP_DIST_PATH = Path(__file__).with_name("static") / "project-result"
MCP_APP_VIEW_HTML = (MCP_APP_DIST_PATH / "index.html").read_text(encoding="utf-8")
MCP_APP_VIEW_CSP = (
    "default-src 'none'; "
    "connect-src https://mcp.magichour.ai; "
    "frame-ancestors https://chatgpt.com https://claude.ai; "
    "form-action 'none'; "
    f"img-src {MCP_APP_MEDIA_ORIGIN}; "
    f"media-src {MCP_APP_MEDIA_ORIGIN}; "
    "script-src https://mcp.magichour.ai; "
    "style-src https://mcp.magichour.ai; "
    "base-uri https://mcp.magichour.ai"
)
