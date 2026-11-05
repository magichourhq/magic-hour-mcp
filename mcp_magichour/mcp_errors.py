from __future__ import annotations

import jsonschema
import mcp.types as mt
from fastmcp import FastMCP
from mcp.shared.exceptions import McpError


def install_structured_tool_errors(mcp: FastMCP) -> None:
    handler = mcp._mcp_server.request_handlers[mt.CallToolRequest]

    async def handle(request: mt.CallToolRequest) -> mt.ServerResult:
        tool = await mcp.get_tool(request.params.name)
        if tool is None:
            raise _invalid_params(f"Unknown tool: {request.params.name!r}")

        # OpenAPI-generated schemas omit additionalProperties; an unknown arg on a
        # parameterless GET would otherwise be sent upstream as a request body.
        schema = {"additionalProperties": False, **tool.parameters}
        try:
            jsonschema.validate(request.params.arguments or {}, schema)
        except jsonschema.ValidationError as error:
            if request.params.name in {"upload_chatgpt_image", "upload_chatgpt_media"}:
                field = "image" if request.params.name == "upload_chatgpt_image" else "file"
                raise _invalid_params(
                    f"Invalid attachment input for {request.params.name!r}. Pass the native attachment "
                    f"in {field!r}; direct MCP callers need a file object with download_url and file_id "
                    "(optional mime_type and file_name), not a bare file ID, filename or local path. "
                    + ("Set media_type to image, video or audio. " if field == "file" else "")
                    + "If the client did not supply a downloadable file, reattach it through the client's "
                    "file picker; do not invent a download URL."
                ) from None
            raise _invalid_params(
                f"Invalid arguments for tool {request.params.name!r}: {error.message}"
            ) from error

        return await handler(request)

    mcp._mcp_server.request_handlers[mt.CallToolRequest] = handle


def _invalid_params(message: str) -> McpError:
    return McpError(mt.ErrorData(code=mt.INVALID_PARAMS, message=message))
