from mcp.server.fastmcp import Context


class AuthError(Exception):
    """Missing or malformed Authorization header."""


def get_api_key(ctx: Context) -> str:
    """Pull the Magic Hour API key straight off the request's Bearer header."""
    request = ctx.request_context.request
    if request is None:
        raise AuthError("No HTTP request on this context. Server must run over Streamable HTTP.")

    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("Missing or malformed Authorization header. Send 'Authorization: Bearer <magic_hour_api_key>'.")

    return token.strip()
