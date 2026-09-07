from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from mcp.types import ToolAnnotations


PROJECT_TAG_TO_ASSET = {
    "Video Projects": "video",
    "Image Projects": "image",
    "Audio Projects": "audio",
}

PROJECT_WAIT_TOOL_BY_ASSET = {
    "video": "wait_for_video_project",
    "image": "wait_for_image_project",
    "audio": "wait_for_audio_project",
}

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
PROJECT_DETAIL_PATHS = {
    f"/v1/{asset}-projects/{{id}}" for asset in PROJECT_TAG_TO_ASSET.values()
}
GENERIC_ACTION_REPLACEMENTS = {"do": "perform", "get": "retrieve", "run": "execute"}
LOGGING_GUIDANCE = (
    "Calls write private diagnostic logs, including for status and download reads."
)
REVIEWED_GENERATION_PATHS = {
    f"/v1/{name}"
    for name in (
        "ai-talking-photo",
        "ai-video-editor",
        "animation",
        "audio-to-video",
        "auto-subtitle-generator",
        "character-replace",
        "face-swap",
        "image-to-video",
        "lip-sync",
        "text-to-video",
        "video-to-video",
        "ai-clothes-changer",
        "ai-face-editor",
        "ai-gif-generator",
        "ai-image-editor",
        "ai-headshot-generator",
        "ai-image-generator",
        "ai-image-upscaler",
        "ai-meme-generator",
        "ai-qr-code-generator",
        "body-swap",
        "face-swap-photo",
        "head-swap",
        "image-background-remover",
        "photo-colorizer",
        "ai-voice-generator",
        "ai-voice-cloner",
    )
}


def private_tool_annotations(*, destructive: bool = False) -> ToolAnnotations:
    # OpenAI's review definition includes log writes. ToolCallLoggingMiddleware
    # runs for every tool, including otherwise read-only helpers.
    return ToolAnnotations(
        readOnlyHint=False, openWorldHint=False, destructiveHint=destructive
    )


def apply_magic_hour_policies(openapi_spec: dict[str, Any]) -> dict[str, Any]:
    """Return an OpenAPI copy with MCP-specific guidance added by group policy."""
    spec = deepcopy(openapi_spec)

    for path, path_item in spec.get("paths", {}).items():
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            if operation_id := operation.get("operationId"):
                operation["operationId"] = normalize_mcp_tool_name(operation_id)
            _apply_operation_policy(
                path=path, method=method.upper(), operation=operation
            )

    return spec


def normalize_mcp_tool_name(operation_id: str) -> str:
    """Convert an OpenAPI operation ID to a descriptive snake_case MCP tool name."""
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", operation_id)
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    words = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower().split("_")
    normalized = "_".join(GENERIC_ACTION_REPLACEMENTS.get(word, word) for word in words)
    if len(normalized) < 4:
        raise ValueError(
            f"MCP tool name must contain at least 4 characters: {operation_id!r}"
        )
    return normalized


def _apply_operation_policy(
    *, path: str, method: str, operation: dict[str, Any]
) -> None:
    tags = set(operation.get("tags") or [])
    additions: list[str] = [LOGGING_GUIDANCE]

    asset_type = next(
        (asset for tag, asset in PROJECT_TAG_TO_ASSET.items() if tag in tags), None
    )

    if path == "/v1/files/upload-urls":
        additions.append(
            "This only creates presigned upload URLs. For local files, upload the raw bytes to each returned "
            "`upload_url` outside the generation call, then pass the matching `file_path` into the create tool."
        )

    if method == "POST" and path == "/v1/face-detection":
        additions.append(
            "This starts an async face-detection task and returns an `id`. Use the face-detection details "
            "endpoint with that id to retrieve detected faces before doing individual face swaps."
        )

    if method == "POST" and asset_type:
        wait_tool = PROJECT_WAIT_TOOL_BY_ASSET[asset_type]
        additions.append(
            f"This starts an async {asset_type} generation job and returns `id` plus `credits_charged` immediately. "
            f"If the user wants the finished result, call the `{wait_tool}` helper with the returned id, or poll "
            f"the matching `GET /v1/{asset_type}-projects/{{id}}` endpoint until status is `complete`, `error`, "
            "or `canceled`. Completed projects include `downloads` with direct URLs. The custom wait helper also "
            "returns `exact_download_urls` separately from expiration metadata."
        )
        additions.append(
            "Creates a new private project and charges the applicable Magic Hour credits; it does not publish "
            "the result. Only start generation when the user asks to create or edit media, not for ideas, "
            "prompts, or instructions alone. Do not repeat a create call to check progress; reuse its id."
        )

    if method == "GET" and path in PROJECT_DETAIL_PATHS:
        additions.append(
            "Use this after a create tool to poll job status. When status is `complete`, surface the `downloads` "
            "URLs to the user; if status is `error`, surface the error message."
        )
        additions.append(
            "Each `downloads[n].url` is already the full signed download URL. Use it exactly as returned. "
            "Do not shorten it, strip query parameters, or append `expires_at` onto the URL string."
        )

    if _operation_mentions_file_path(operation):
        additions.append(
            "For `*_file_path` values, prefer an existing Magic Hour file path or a `file_path` returned by "
            "the upload-URL endpoint after the file bytes are uploaded. Direct public media URLs may work when "
            "they are stable, fetchable, and return raw file bytes, but hotlinked URLs can fail; when in doubt, "
            "use the presigned upload flow first and pass the returned `file_path`."
        )

    if additions:
        operation["description"] = _append_mcp_guidance(
            operation.get("description", ""), additions
        )


def _operation_mentions_file_path(operation: dict[str, Any]) -> bool:
    return "_file_path" in repr(operation)


def _append_mcp_guidance(description: str, additions: list[str]) -> str:
    existing = description.strip()
    guidance = "MCP guidance:\n" + "\n".join(f"- {addition}" for addition in additions)
    if not existing:
        return guidance
    if "MCP guidance:" in existing:
        return existing
    return f"{existing}\n\n{guidance}"


def customize_openapi_component(route: Any, component: Any) -> None:
    """Small runtime component policy for tags; text policy is applied to the spec."""
    method = str(getattr(route, "method", "")).upper()
    path = str(getattr(route, "path", ""))
    route_tags = set(getattr(route, "tags", []) or [])

    # Fail closed when the OpenAPI sync introduces a new kind of operation.
    # These groups were reviewed against the API handlers, not tool names.
    project_details = path in PROJECT_DETAIL_PATHS
    supported = (
        (project_details and method in {"GET", "DELETE"})
        or (method == "GET" and path == "/v1/face-detection/{id}")
        or (
            method == "POST" and path in {"/v1/files/upload-urls", "/v1/face-detection"}
        )
        or (
            method == "POST"
            and path in REVIEWED_GENERATION_PATHS
            and bool(route_tags.intersection(PROJECT_TAG_TO_ASSET))
        )
    )
    if not supported:
        raise ValueError(f"Review MCP side effects before exposing {method} {path}")
    component.annotations = private_tool_annotations(destructive=method == "DELETE")

    tags = getattr(component, "tags", None)
    if tags is None:
        return
    tags.add("magic-hour")

    if method == "POST":
        tags.add("write-operation")
    if path == "/v1/files/upload-urls":
        tags.add("upload")
    if route_tags.intersection(PROJECT_TAG_TO_ASSET):
        tags.add("generation")
