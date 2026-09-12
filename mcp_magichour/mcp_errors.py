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
            raise _invalid_params(
                f"Invalid arguments for tool {request.params.name!r}: {error.message}"
            ) from error

        return await handler(request)

    mcp._mcp_server.request_handlers[mt.CallToolRequest] = handle


def _invalid_params(message: str) -> McpError:
    return McpError(mt.ErrorData(code=mt.INVALID_PARAMS, message=message))
