"""OpenAPI-backed Magic Hour MCP server.

The public imports stay stable for existing integration code:
`from mcp_magichour.server import app, lifespan`.
"""

from starlette.requests import Request
from starlette.responses import PlainTextResponse

from .openapi_server import app, lifespan, mcp
from .posthog_client import analytics


async def capture_unhandled_exception(_: Request, exc: Exception) -> PlainTextResponse:
    """Report ASGI exceptions that Starlette catches before they leave the process."""
    analytics.capture_exception(exc)
    return PlainTextResponse("Internal Server Error", status_code=500)


app.add_exception_handler(Exception, capture_unhandled_exception)
