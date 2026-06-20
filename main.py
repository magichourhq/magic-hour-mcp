import uvicorn
from dotenv import load_dotenv

load_dotenv()

from mcp_magichour.server import app  # noqa: E402

if __name__ == "__main__":
    # Standalone dev server: MCP endpoint is at the root "/", not "/mcp".
    # The host app mounts the same `app` at "/mcp" in production.
    uvicorn.run(app, host="127.0.0.1", port=8000)
