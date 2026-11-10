from __future__ import annotations

import json

import httpx
import jsonschema
import mcp.types as mt
from fastmcp import FastMCP
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.shared.exceptions import McpError


class APIErrorRecoveryMiddleware(Middleware):
    """Give canonical API rejections a recovery action without retrying requests."""

    def __init__(self, api_base_url: httpx.URL) -> None:
        self.api_origin = api_base_url.copy_with(path="/", query=None, fragment=None)
        self.api_path = api_base_url.path.rstrip("/") + "/v1/"

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        try:
            return await call_next(context)
        except Exception as error:
            cause = error
            while cause is not None:
                if isinstance(cause, httpx.HTTPStatusError):
                    request = cause.request
                    if (
                        request.url.copy_with(path="/", query=None, fragment=None) == self.api_origin
                        and request.url.path.startswith(self.api_path)
                    ):
                        try:
                            body = cause.response.json()
                        except ValueError:
                            break
                        if not isinstance(body, dict):
                            break
                        code = body.get("code")
                        if not isinstance(code, str):
                            break
                        recovery = {
                            "invalid_request": (400, "Correct the fields described by the API using the current tool schema. Preserve explicit settings; ask before changing them. Do not retry the unchanged request."),
                            "insufficient_credits": (
                                402,
                                "Explain that the connected account lacks credits. Show the user "
                                "https://magichour.ai/pricing#credit-pack for one-time credit packs and "
                                "https://magichour.ai/pricing for subscription plans. Ask them to purchase "
                                "for the same connected Magic Hour account and return here to continue. "
                                "A credit purchase does not unlock subscription-only models or resolutions. "
                                "When the user asks to continue, refresh get_account_info and recheck the "
                                "original request's eligibility; balance alone is not proof. Preserve supplied "
                                "media and explicit settings. Do not purchase, change plans or retry "
                                "generation automatically.",
                            ),
                            "subscription_required": (402, "Explain that this request requires a subscription. Do not retry generation or change the subscription automatically."),
                            "plan_upgrade_required": (402, "Explain the API's plan restriction for this request. Ask before changing its model or settings; do not upgrade or retry automatically."),
                            "not_found": (404, "Check the existing resource ID and connected account. Do not create a replacement generation to recover a missing result."),
                            "unprocessable_entity": (422, "Correct the values described by the API. Preserve the user's explicit model, references and settings; ask before changing them. Do not retry the unchanged request."),
                            "internal_server_error": (500, "For a creation request, acceptance may be uncertain: recover its existing project ID before any new create. For retrieval, retry using the same project ID. Do not automatically retry creation."),
                        }.get(code)
                        message = body.get("message")
                        if recovery and recovery[0] == cause.response.status_code and isinstance(message, str):
                            # Keep only the API's public error contract, not raw validation
                            # objects, request arguments or response headers.
                            payload = {"error": {
                                "code": code,
                                "message": message,
                                "http_status": cause.response.status_code,
                                "next_action": recovery[1],
                            }}
                            return ToolResult(
                                content=json.dumps(payload), structured_content=payload, is_error=True,
                            )
                # Attachment helpers suppress their cause but retain context. Their
                # source/upload host errors must not be classified as API rejections.
                cause = cause.__cause__ or cause.__context__
            raise


def install_structured_tool_errors(mcp: FastMCP, tool_name_replacements: dict[str, str] | None = None) -> None:
    handler = mcp._mcp_server.request_handlers[mt.CallToolRequest]

    async def handle(request: mt.CallToolRequest) -> mt.ServerResult:
        tool = await mcp.get_tool(request.params.name)
        if tool is None:
            message = f"Unknown tool: {request.params.name!r}"
            data: dict[str, object] = {
                "type": "unknown_tool",
                "next_action": (
                    "Refresh tools/list and choose the current tool that matches the intended task. "
                    "Do not invent an alias or retry the obsolete name."
                ),
            }
            replacement = (tool_name_replacements or {}).get(request.params.name)
            if replacement and await mcp.get_tool(replacement) is not None:
                data["current_tool"] = replacement
                data["next_action"] = (
                    f"Read {replacement}'s current tools/list schema and call it if it matches the user's intent. "
                    "Preserve supplied inputs and explicit settings; ask before changing them. "
                    "The obsolete call was not executed."
                )
                # Some clients expose only the error message to the agent.
                message += f". Current tool: {replacement!r}. {data['next_action']}"
            raise _invalid_params(
                message,
                data,
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
                    f"Invalid attachment input for {request.params.name!r}: {detail} "
                    f"Pass the native attachment in {field!r}; direct MCP callers need a file object "
                    "with download_url and file_id "
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
