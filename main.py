import uvicorn
from dotenv import load_dotenv

load_dotenv()

from mcp_magichour.server import app  # noqa: E402

if __name__ == "__main__":
    # This standalone entry point serves the MCP endpoint at the root path.
    uvicorn.run(app, host="127.0.0.1", port=8000, lifespan="on")
