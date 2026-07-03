"""OpenAPI-backed Magic Hour MCP server.

The public imports stay stable for existing integration code:
`from mcp_magichour.server import app, lifespan`.
"""

from .openapi_server import app, lifespan, mcp
