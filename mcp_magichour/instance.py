from mcp.server.fastmcp import FastMCP

# streamable_http_path="/" so the host can mount this whole app at "/mcp"
# without ending up with "/mcp/mcp".
mcp = FastMCP("magic-hour", streamable_http_path="/")
