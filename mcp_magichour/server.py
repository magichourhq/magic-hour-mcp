from contextlib import asynccontextmanager

from .instance import mcp

# Importing these registers their @mcp.tool() functions.
from .tools import audio_projects, files, image_projects, video_projects  # noqa: F401


@mcp.tool()
def ping() -> str:
    """Check that the server is reachable."""
    return "pong"


app = mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(_app):
    """Starts the MCP session manager's task group.

    Required whenever `app` is mounted as a sub-app on another ASGI server
    (e.g. FastAPI) -- a mounted app's own lifespan is not run automatically,
    so the host must pass this in (merged with its own lifespan if it has one).
    Not needed when running `app` directly, e.g. via `main.py`.
    """
    async with mcp.session_manager.run():
        yield
