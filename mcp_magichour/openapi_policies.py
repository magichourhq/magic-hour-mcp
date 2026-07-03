from __future__ import annotations

from copy import deepcopy
from typing import Any


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
PROJECT_DETAIL_PATHS = {f"/v1/{asset}-projects/{{id}}" for asset in PROJECT_TAG_TO_ASSET.values()}


def apply_magic_hour_policies(openapi_spec: dict[str, Any]) -> dict[str, Any]:
    """Return an OpenAPI copy with MCP-specific guidance added by group policy."""
    spec = deepcopy(openapi_spec)

    for path, path_item in spec.get("paths", {}).items():
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            _apply_operation_policy(path=path, method=method.upper(), operation=operation)

    return spec


def _apply_operation_policy(*, path: str, method: str, operation: dict[str, Any]) -> None:
    tags = set(operation.get("tags") or [])
    additions: list[str] = []

    asset_type = next((asset for tag, asset in PROJECT_TAG_TO_ASSET.items() if tag in tags), None)

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
            "Any `*_file_path` value can be a public URL, an existing Magic Hour file path, or a `file_path` "
            "returned by the upload-URL endpoint after the file bytes are uploaded."
        )

    if additions:
        operation["description"] = _append_mcp_guidance(operation.get("description", ""), additions)


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
    tags = getattr(component, "tags", None)
    if tags is None:
        return

    tags.add("magic-hour")

    method = str(getattr(route, "method", "")).upper()
    path = str(getattr(route, "path", ""))
    route_tags = set(getattr(route, "tags", []) or [])

    if method == "POST":
        tags.add("write-operation")
    if path == "/v1/files/upload-urls":
        tags.add("upload")
    if route_tags.intersection(PROJECT_TAG_TO_ASSET):
        tags.add("generation")
