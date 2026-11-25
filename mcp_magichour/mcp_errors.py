from __future__ import annotations

import json

import httpx
import jsonschema
import mcp.types as mt
from fastmcp import FastMCP
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.shared.exceptions import McpError

from . import gates
from .chatgpt_media import attachment_fields
from .openapi_policies import IMAGE_DEFAULT_MODELS, sorted_resolutions


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
                                "Explain that the connected account lacks credits. State the shortfall if the API reports it. "
                                "Lead with [Add credits](https://magichour.ai/pricing#credit-pack) for a one-time purchase. "
                                "If the user asks about a subscription, show https://magichour.ai/pricing. "
                                "Ask them to use the same Magic Hour account and return here to continue. "
                                "A credit purchase does not unlock subscription-only models or resolutions. "
                                "When the user asks to continue, refresh get_account_info and recheck the "
                                "original request's eligibility; balance alone is not proof. Preserve supplied "
                                "media and explicit settings. Do not purchase, change plans or retry "
                                "generation automatically.",
                            ),
                            "subscription_required": (
                                402,
                                "Explain which part of the request requires a subscription if the API says. "
                                "Offer [View plans](https://magichour.ai/pricing) and an option on the current plan "
                                "if you can verify it. Name a plan only if verified to unlock the requested settings. "
                                "Preserve the request. Ask before changing explicit settings, and wait for the user "
                                "to continue after any plan change. Recheck eligibility and credits before submitting.",
                            ),
                            "plan_upgrade_required": (
                                402,
                                "Explain the specific model, duration or resolution limit reported by the API. "
                                "Offer an option on the current plan if you can verify it. When a plan change can "
                                "resolve the reported limit, offer [View plans](https://magichour.ai/pricing). "
                                "Name a plan only if verified to unlock the requested settings. Preserve the request. "
                                "Ask before changing explicit settings, and wait for the user to continue after any "
                                "plan change. Recheck eligibility and credits before submitting.",
                            ),
                            "not_found": (404, "Check the existing resource ID and connected account. Do not create a replacement generation to recover a missing result."),
                            "unprocessable_entity": (422, "Correct the values described by the API. Explain the constraint and offer a compatible model or setting if known. A subscription will not fix an unsupported model setting. Preserve the user's explicit model, references and settings; ask before changing them. Do not retry the unchanged request."),
                            "internal_server_error": (500, "For a creation request, acceptance may be uncertain: recover its existing project ID before any new create. For retrieval, retry using the same project ID. Do not automatically retry creation."),
                        }.get(code)
                        message = body.get("message")
                        if recovery and recovery[0] == cause.response.status_code and isinstance(message, str):
                            # Keep only the API's public error contract, not raw validation
                            # objects, request arguments or response headers.
                            next_action = recovery[1]
                            if code == "unprocessable_entity" and _has_local_asset_path(context.message.arguments):
                                next_action += (
                                    " Local sandbox paths such as /mnt/data/... are not uploaded API assets; "
                                    "this server cannot read the client's sandbox. Remove the local path and use the "
                                    "tool's named attachment field if it supports the file. Otherwise, for a native image attachment, "
                                    "call upload_chatgpt_image(image=<attachment>); for video or audio, call "
                                    "upload_chatgpt_media with the attachment in file and media_type set to video "
                                    "or audio to match the attachment. "
                                    "Replace each local asset path with that upload's returned file_path. "
                                    "Pass the real attachment, not its local path or a bare file ID. If no "
                                    "downloadable attachment is available, ask the user to reattach it through "
                                    "the client's file picker. Do not invent download URLs or file IDs. "
                                    "Do not automatically retry generation."
                                )
                            payload = {"error": {
                                "code": code,
                                "message": message,
                                "http_status": cause.response.status_code,
                                "next_action": next_action,
                            }}
                            shortfall = body.get("credit_shortfall")
                            if (
                                code == "insufficient_credits"
                                and isinstance(shortfall, (int, float))
                                and not isinstance(shortfall, bool)
                            ):
                                payload["error"]["credit_shortfall"] = shortfall
                            return ToolResult(
                                content=json.dumps(payload), structured_content=payload, is_error=True,
                            )
                # Attachment helpers suppress their cause but retain context. Their
                # source/upload host errors must not be classified as API rejections.
                cause = cause.__cause__ or cause.__context__
            raise


class UnsupportedResolutionMiddleware(Middleware):
    def __init__(self, rules: dict[str, dict[str, set[str]]]) -> None:
        self.rules = rules

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        models = self.rules.get(context.message.name)
        arguments = context.message.arguments or {}
        model, resolution = arguments.get("model") or "default", arguments.get("resolution")
        # The API selects the default editor model for the caller's plan.
        if context.message.name == "edit_image" and model == "default":
            return await call_next(context)
        if (
            models
            and isinstance(model, str) and model in models
            and isinstance(resolution, str) and resolution != "auto"
            and resolution not in models[model]
        ):
            supported_by = sorted(
                name for name, resolutions in models.items() if name != "default" and resolution in resolutions
            )
            name = model
            if model == "default":
                name = f"the default model ({IMAGE_DEFAULT_MODELS[context.message.name]})"
            order = sorted_resolutions(models[model] | {resolution})
            lower = [value for value in order[:order.index(resolution)] if value in models[model]]
            higher = [value for value in order[order.index(resolution) + 1:] if value in models[model]]
            options = [f"switch to a model that supports {resolution} ({', '.join(supported_by) or 'none'})"]
            if lower:
                options.append(f"keep {name} and go down to {lower[-1]}")
            if higher:
                options.append(f"keep {name} and go up to {higher[0]}")
            payload = {"error": {
                "code": "unsupported_resolution",
                "message": (
                    f"{name.replace('the default', 'The default', 1)} does not support {resolution}. It supports "
                    f"{', '.join(sorted_resolutions(models[model]))}. Nothing was generated or charged."
                ),
                "supported_models": supported_by,
                "closest_lower_resolution": lower[-1] if lower else None,
                "closest_higher_resolution": higher[0] if higher else None,
                "next_action": (
                    f"Tell the user {name} can't make {resolution}. Verify the account can use "
                    "each model at its proposed resolution before offering these choices: "
                    + "; ".join(options)
                    + ". Ask which they want before generating."
                ),
            }}
            return ToolResult(content=json.dumps(payload), structured_content=payload, is_error=True)
        return await call_next(context)


def _has_local_asset_path(value: object) -> bool:
    if isinstance(value, dict):
        for field, item in value.items():
            if field == "file_path" or field.endswith(("_file_path", "_file_paths")):
                paths = item if isinstance(item, list) else [item]
                if any(
                    isinstance(path, str) and path.startswith(("/", "./", "../", "~/", "file:", "sandbox:"))
                    for path in paths
                ):
                    return True
            if _has_local_asset_path(item):
                return True
    elif isinstance(value, list):
        return any(_has_local_asset_path(item) for item in value)
    return False


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
        if gates.MODE in ("warn", "refuse") and request.params.name in gates.SPENDING:
            # The gates add plan_token to the listed schema; the gate middleware removes it before the tool runs.
            schema["properties"] = {**schema.get("properties", {}), "plan_token": {"type": "string"}}
        if request.params.arguments:
            request.params.arguments = _parse_stringified_attachments(
                request.params.name, request.params.arguments
            )
        try:
            jsonschema.validate(request.params.arguments or {}, schema)
        except jsonschema.ValidationError as error:
            file_fields = attachment_fields(request.params.name)
            detail_error = error
            if file_fields and error.validator == "anyOf":
                # Preserve the missing-assets error for clients still using file paths.
                detail_error = next(
                    (option for option in error.context
                     if option.validator == "required" and option.validator_value == ["assets"]),
                    error,
                )
            detail, data = _validation_detail(detail_error)
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
            if file_fields and (
                any(field in file_fields for field in data["field_path"][:1])
                or error.validator in {"anyOf", "oneOf"}
            ):
                next_action = (
                    "Choose the file again using the ChatGPT attachment picker, "
                    "or use an existing Magic Hour file path. Keep the requested settings."
                )
                data["next_action"] = next_action
                detail = f"{detail} {next_action}"
            raise _invalid_params(
                f"Invalid arguments for tool {request.params.name!r}: {detail}", data
            ) from None

        return await handler(request)

    mcp._mcp_server.request_handlers[mt.CallToolRequest] = handle


_UPLOAD_ATTACHMENT_FIELDS = {"upload_chatgpt_image": ["image"], "upload_chatgpt_media": ["file"]}


def _parse_stringified_attachments(tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
    # Some clients send an attachment as a JSON-encoded string. Only JSON objects are
    # decoded, so plain strings such as paths or URLs are left for normal validation.
    def parse(value: object) -> object:
        if isinstance(value, str) and value.lstrip().startswith("{"):
            try:
                parsed = json.loads(value)
            except (ValueError, RecursionError):
                return value
            if isinstance(parsed, dict):
                # Attachment fields are flat strings. Emptying a nested value keeps validation
                # the same as for a native object without carrying deep input onward.
                return {
                    key: type(item)() if isinstance(item, (dict, list)) else item
                    for key, item in parsed.items()
                }
        return value

    fields = _UPLOAD_ATTACHMENT_FIELDS.get(tool_name) or attachment_fields(tool_name)
    result = arguments
    for field in fields:
        value = arguments.get(field)
        parsed = [parse(item) for item in value] if isinstance(value, list) else parse(value)
        if parsed != value:
            result = {**result, field: parsed}
    return result


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
    allowed_text = ""
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
        allowed = expected["allowed_fields"]
        if allowed:
            # Some clients expose only the error message to the agent.
            names = ", ".join(allowed[:25]) + (f", and {len(allowed) - 25} more" if len(allowed) > 25 else "")
            allowed_text = f" Allowed fields: {names}."
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
    return f"{detail} at {field}.{allowed_text}", {
        "type": "invalid_arguments",
        "field_path": path,
        "constraint": constraint,
        "expected": expected,
        "next_action": next_action,
    }


def _invalid_params(message: str, data: dict[str, object] | None = None) -> McpError:
    return McpError(mt.ErrorData(code=mt.INVALID_PARAMS, message=message, data=data))
