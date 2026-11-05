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
            raise _invalid_params(
                f"Unknown tool: {request.params.name!r}",
                {
                    "type": "unknown_tool",
                    "next_action": (
                        "Refresh tools/list and choose the current tool that matches the intended task. "
                        "Do not invent an alias or retry the obsolete name."
                    ),
                },
            )

        # OpenAPI-generated schemas omit additionalProperties; an unknown arg on a
        # parameterless GET would otherwise be sent upstream as a request body.
        schema = {"additionalProperties": False, **tool.parameters}
        try:
            jsonschema.validate(request.params.arguments or {}, schema)
        except jsonschema.ValidationError as error:
            detail, data = _validation_detail(error)
            if request.params.name in {"upload_chatgpt_image", "upload_chatgpt_media"}:
                field = "image" if request.params.name == "upload_chatgpt_image" else "file"
                raise _invalid_params(
                    f"Invalid attachment input for {request.params.name!r}. Pass the native attachment "
                    f"in {field!r}; direct MCP callers need a file object with download_url and file_id "
                    "(optional mime_type and file_name), not a bare file ID, filename or local path. "
                    + ("Set media_type to image, video or audio. " if field == "file" else "")
                    + "If the client did not supply a downloadable file, reattach it through the client's "
                    "file picker; do not invent a download URL.",
                    data,
                ) from None
            raise _invalid_params(
                f"Invalid arguments for tool {request.params.name!r}: {detail}", data
            ) from None

        return await handler(request)

    mcp._mcp_server.request_handlers[mt.CallToolRequest] = handle


def _validation_detail(error: jsonschema.ValidationError) -> tuple[str, dict[str, object]]:
    # Property names come from the tool schema, never unrecognized input keys.
    schema_path = list(error.absolute_schema_path)
    property_names = {
        schema_path[index + 1]
        for index, keyword in enumerate(schema_path[:-1])
        if keyword == "properties"
    }
    path = [
        part if isinstance(part, int) or part in property_names else "<field>"
        for part in error.absolute_path
    ]
    constraint = error.validator
    expected: dict[str, object] = {}
    next_action = (
        "Correct this field using the current tools/list schema. Preserve explicit requested settings; "
        "ask before changing them. Do not retry an unchanged creation request."
    )
    if constraint == "required":
        missing = [field for field in error.validator_value if field not in error.instance]
        expected["required"] = missing
        path.append(missing[0])
        detail = f"{missing[0]!r} is a required property"
    elif constraint == "additionalProperties":
        expected["allowed_fields"] = list(error.schema.get("properties", {}))
        detail = "An argument was unexpected"
        next_action = (
            "Refresh tools/list and remove arguments outside the listed allowed fields. "
            "Do not silently rename or discard an explicitly requested setting."
        )
    elif constraint == "type":
        expected["type"] = error.validator_value
        detail = f"The field is not of type {error.validator_value!r}"
    elif constraint == "enum":
        expected["enum"] = error.validator_value
        detail = "The field is not one of the allowed choices"
    elif constraint == "pattern":
        expected["pattern"] = error.validator_value
        detail = "The field does not match the required pattern"
    elif constraint in {"minItems", "minLength"} and error.validator_value == 1:
        expected[constraint] = 1
        detail = "The field should be non-empty"
    else:
        if constraint in {
            "const", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
            "minLength", "maxLength", "minItems", "maxItems", "multipleOf", "uniqueItems",
        }:
            expected[constraint] = error.validator_value
        detail = f"The field does not satisfy {constraint!r}"

    field = "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in path)
    return f"{detail} at {field}.", {
        "type": "invalid_arguments",
        "field_path": path,
        "constraint": constraint,
        "expected": expected,
        "next_action": next_action,
    }


def _invalid_params(message: str, data: dict[str, object] | None = None) -> McpError:
    return McpError(mt.ErrorData(code=mt.INVALID_PARAMS, message=message, data=data))
