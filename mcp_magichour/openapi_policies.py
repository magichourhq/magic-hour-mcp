from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from fastmcp.tools.base import Tool
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

# Synced boilerplate can mix inputs/options from different endpoints. Keep the
# task descriptions specific while preserving upstream schemas and tool names.
GENERATION_TOOL_METADATA = {
    "/v1/image-to-video": (
        "Animate a supplied image into video",
        "Animate a supplied product photo, portrait or scene. Requires `assets.image_file_path` "
        "and `end_seconds`. Use the approved image directly; edit only for a requested visual "
        "change or a source problem that prevents the shot. Prompt for motion while preserving "
        "the supplied appearance. Model, duration, resolution and audio support vary. Generation "
        "consumes credits; the queued video charge is an estimate, with the final charge available "
        "on the completed project.",
    ),
    "/v1/video-to-video": (
        "Restyle an existing video segment",
        (
            "Restyle an existing video segment with an art-style preset or a custom prompt. "
            "Requires `assets.video_source` with its matching `assets.video_file_path` or "
            "`assets.youtube_url`, `start_seconds`, `end_seconds` and `style.art_style`. "
            "For a custom prompt, set `style.prompt_type` to `custom` or `append_default` "
            "and provide `style.prompt`; with `default`, the supplied prompt is ignored. "
            "This API supports v1/v2 presets, not v3. Generation consumes credits; the queued "
            "video charge is an estimate, with the final charge available on the completed project."
        ),
    ),
    "/v1/text-to-video": (
        "Generate video from text",
        "Generate a new scene from `style.prompt` and `end_seconds` when no supplied starting image is required. "
        "To animate an approved product photo or portrait, use `image_to_video_create_video` instead.",
    ),
    "/v1/ai-talking-photo": (
        "Make a still portrait speak from audio",
        "Requires a still portrait in `assets.image_file_path` and an existing recording in "
        "`assets.audio_file_path`, plus `start_seconds` and `end_seconds`. Text alone is not "
        "accepted; this tool does not synthesize speech. For existing video plus replacement "
        "audio, use `lip_sync_create_video` instead.",
    ),
    "/v1/lip-sync": (
        "Lip-sync existing video to replacement audio",
        "Match existing video to replacement audio. Requires `assets.audio_file_path`, "
        "`assets.video_source` and its matching `assets.video_file_path` or `assets.youtube_url`, "
        "plus `start_seconds` and `end_seconds`. For a still portrait plus audio, use "
        "`ai_talking_photo_create_talking_photo`. Text alone is not an audio input. Generation "
        "consumes credits; the queued video charge is an estimate, with the final charge available "
        "on the completed project.",
    ),
    "/v1/ai-image-editor": (
        "Edit supplied images",
        "Edit supplied images using `assets.image_file_paths` and `style.prompt`. For prompt-only "
        "creation use `ai_image_generator_create_image`. Editing is not a required preparation "
        "step before image-to-video when the approved source already meets the brief. Check model "
        "eligibility: the omitted/default model is not universally available on Free.",
    ),
    "/v1/face-swap": (
        "Swap faces in an existing video",
        "Requires `start_seconds`, `end_seconds` and `assets.video_source` with its matching "
        "`assets.video_file_path` or `assets.youtube_url`. Use `assets.image_file_path` for "
        "all-faces mode, or `assets.face_mappings` for individual-faces mode after face detection. "
        "Generation consumes credits; the queued video charge is an estimate, with the final "
        "charge available on the completed project.",
    ),
    "/v1/character-replace": (
        "Replace or animate a character using video and a reference image",
        "Replace a video subject or transfer video motion onto a reference character image. "
        "Requires `assets.video_file_path`, `assets.image_file_path` and `end_seconds`. "
        "Model support controls duration, resolution and subject selection; see parameter schemas. "
        "Generation consumes credits; the queued video charge is an estimate, with the final "
        "charge available on the completed project.",
    ),
}

# Reviewer-facing parameter copy describes selection without ranking models.
GENERATION_PARAMETER_DESCRIPTIONS = {
    "/v1/image-to-video": {
        ("model",): "The video generation model. With `default`, paid tiers use `kling-3.0` and Free uses "
        "`ltx-2.5`. Duration, resolution, audio and reference support depend on the selected model. "
        "Deprecated model values ending in `-audio` enable `audio`.",
    },
    "/v1/text-to-video": {
        ("model",): "The video generation model. With `default`, paid tiers use `kling-3.0` and Free uses "
        "`ltx-2.5`. Duration, resolution, audio and reference support depend on the selected model. "
        "Deprecated model values ending in `-audio` enable `audio`.",
    },
    "/v1/ai-talking-photo": {
        ("style", "generation_mode"): "Controls motion style. `realistic` animates the supplied portrait; "
        "`prompted` also accepts a scene prompt. Deprecated aliases remain supported: `pro` and "
        "`stable` select `realistic`; `standard` and `expressive` select `prompted`.",
    },
    "/v1/lip-sync": {
        ("style", "generation_mode"): "Select the lip-sync mode. `lite` costs 1 credit per video frame. "
        "`standard` costs 1 credit per video frame; `pro` costs 2. Both `standard` and `pro` require "
        "visible mouth movement in the opening seconds of the input video. `pro` requires a Creator, "
        "Pro or Business tier. For a still image, including a static image saved as video, use "
        "`ai_talking_photo_create_talking_photo` with the original image and audio.",
    },
    "/v1/face-swap": {
        ("style", "version"): "Select face-swap version `v1` or `v2`. `default` uses the server-selected "
        "version, which may change over time.",
        ("assets", "face_swap_mode"): "Choose which faces to swap. `all-faces` uses one source image "
        "for all detected faces (`image_file_path` required). `individual-faces` uses exact "
        "`face_mappings`.",
    },
    "/v1/face-swap-photo": {
        ("assets", "face_swap_mode"): "Choose which faces to swap. `all-faces` uses one source image "
        "for all detected faces (`source_file_path` required). `individual-faces` uses exact "
        "`face_mappings`.",
    },
    "/v1/video-to-video": {
        ("style", "version"): "Select restyling version `v1` or `v2`. `default` uses the selected art "
        "style's default version. Some styles default to v3, which this API does not support. "
        "For `Studio Ghibli`, explicitly set `version` to `v2` for its supported preset.",
        ("style", "model"): "Select a listed rendering model for the art style. `default` uses the "
        "selected art style's default model.",
        ("style", "prompt_type"): "`default` uses the art style's preset prompt and ignores `prompt`. "
        "`custom` uses the supplied `prompt`; v1 still adds its LoRA prompt to apply the art style. "
        "`append_default` appends the preset prompt to the supplied `prompt`.",
    },
}

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
GENERATION_ESTIMATE_PATHS = (
    "/v1/ai-image-generator/estimate",
    "/v1/ai-image-editor/estimate",
    "/v1/image-to-video/estimate",
    "/v1/text-to-video/estimate",
)
PROJECT_DETAIL_PATHS = {f"/v1/{asset}-projects/{{id}}" for asset in PROJECT_TAG_TO_ASSET.values()}
GENERIC_ACTION_REPLACEMENTS = {"do": "perform", "get": "retrieve", "run": "execute"}

# Populate only with verified generic/non-celebrity API voice_name strings.
# Empty means no voice is approved; omit Voice Generator entirely from MCP.
APPROVED_VOICE_NAMES: tuple[str, ...] = ()
VOICE_PRESET_DESCRIPTION = "Select one of Magic Hour’s approved preset voices."
LIKENESS_GENERATION_PATHS = {
    "/v1/face-swap", "/v1/face-swap-photo", "/v1/head-swap", "/v1/body-swap",
    "/v1/lip-sync", "/v1/ai-talking-photo", "/v1/character-replace", "/v1/ai-clothes-changer",
}
LIKENESS_USAGE_GUIDANCE = (
    "Use only with the user's own likeness or content they are authorized to use. "
    "Do not use for impersonation, deception, sexual content, or content involving minors."
)


def apply_magic_hour_policies(openapi_spec: dict[str, Any]) -> dict[str, Any]:
    """Return an OpenAPI copy with MCP-specific guidance added by group policy."""
    spec = deepcopy(openapi_spec)
    paths = spec.get("paths", {})
    paths.pop("/v1/ai-voice-cloner", None)
    if not APPROVED_VOICE_NAMES:
        paths.pop("/v1/ai-voice-generator", None)

    for path, path_item in paths.items():
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            if path == "/v1/ai-voice-generator" and method.lower() == "post":
                _apply_voice_generator_policy(operation)
            if path == "/v1/ai-meme-generator" and method.lower() == "post":
                # Keep public-figure template names out of the reviewer-facing
                # schema. Random is the existing generic API template option.
                properties = operation["requestBody"]["content"]["application/json"]["schema"]["properties"]
                properties["style"]["properties"]["template"] = {
                    "type": "string",
                    "enum": ["Random"],
                    "description": "Select a random meme template.",
                }
            if operation_id := operation.get("operationId"):
                operation["operationId"] = normalize_mcp_tool_name(operation_id)
            _apply_operation_policy(path=path, method=method.upper(), operation=operation)

    return spec


def _apply_voice_generator_policy(operation: dict[str, Any]) -> None:
    # Synced descriptions, defaults and examples may recommend unapproved voices
    # anywhere in this operation, including nested schemas and SDK snippets.
    def remove_upstream_copy(value: Any) -> None:
        if isinstance(value, dict):
            if "description" in value:
                value["description"] = ""
            for key in ("example", "examples", "default", "title", "externalDocs", "x-codeSamples"):
                value.pop(key, None)
            for child in value.values():
                remove_upstream_copy(child)
        elif isinstance(value, list):
            for child in value:
                remove_upstream_copy(child)

    remove_upstream_copy(operation)
    operation["summary"] = "AI Voice Generator"
    operation["description"] = (
        f"Generate speech from text. {VOICE_PRESET_DESCRIPTION} "
        "Each character costs 0.1 credits. The cost is rounded up to the nearest whole number."
    )
    # Require the known input shape: fail startup rather than expose a changed
    # upstream schema without this policy. Replace the whole voice schema so
    # future enum/const/combinator changes cannot bypass the allowlist.
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    properties = schema["properties"]
    style = properties["style"]
    if "voice_name" not in style["properties"]:
        raise ValueError("Upstream Voice Generator schema is missing voice_name")
    schema["required"] = list(dict.fromkeys([*schema.get("required", []), "style"]))
    style["required"] = list(dict.fromkeys([*style.get("required", []), "voice_name"]))
    properties["name"]["description"] = "Give your audio a custom name for easy identification."
    style["description"] = "The content used to generate speech."
    style["properties"]["prompt"]["description"] = "Text used to generate speech. The character limit is 1000 characters."
    style["properties"]["voice_name"] = {
        "type": "string",
        "enum": list(APPROVED_VOICE_NAMES),
        "description": VOICE_PRESET_DESCRIPTION,
    }
    output = operation["responses"]["200"]["content"]["application/json"]["schema"]["properties"]
    output["id"]["description"] = "Unique ID of the generated audio project."
    output["credits_charged"]["description"] = "Credits charged for audio generation."


def normalize_mcp_tool_name(operation_id: str) -> str:
    """Convert an OpenAPI operation ID to a descriptive snake_case MCP tool name."""
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", operation_id)
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    words = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower().split("_")
    normalized = "_".join(GENERIC_ACTION_REPLACEMENTS.get(word, word) for word in words)
    if len(normalized) < 4:
        raise ValueError(f"MCP tool name must contain at least 4 characters: {operation_id!r}")
    return normalized


def _apply_operation_policy(*, path: str, method: str, operation: dict[str, Any]) -> None:
    tags = set(operation.get("tags") or [])
    additions: list[str] = []

    if method == "POST" and path in GENERATION_TOOL_METADATA:
        operation["summary"], operation["description"] = GENERATION_TOOL_METADATA[path]

    if method == "POST" and path in GENERATION_PARAMETER_DESCRIPTIONS:
        properties = operation["requestBody"]["content"]["application/json"]["schema"]["properties"]
        for property_path, description in GENERATION_PARAMETER_DESCRIPTIONS[path].items():
            parameter = properties[property_path[0]]
            for name in property_path[1:]:
                parameter = parameter["properties"][name]
            parameter["description"] = description

    if method == "POST" and path in {"/v1/ai-image-editor", "/v1/ai-image-generator"}:
        model = operation["requestBody"]["content"]["application/json"]["schema"]["properties"]["model"]
        model["description"] = model["description"].replace(
            "Use the model we recommend, which will change over time. This is recommended unless you need "
            "a specific model. This is the default behavior.",
            "Uses the server-selected default model, which may change over time.",
        )

    asset_type = next((asset for tag, asset in PROJECT_TAG_TO_ASSET.items() if tag in tags), None)

    if path == "/v1/files/upload-urls":
        additions.append(
            "This only creates presigned upload URLs. For local files, upload the raw bytes to each returned "
            "`upload_url` outside the generation call, then pass the matching `file_path` into the create tool."
        )

    if method == "POST" and path == "/v1/ai-image-editor":
        additions.append(
            "Requires one or more source images in `assets.image_file_paths`. "
            "For prompt-only creation, use `ai_image_generator_create_image` instead."
        )

    if method == "POST" and path == "/v1/face-detection":
        additions.append(
            "This starts an async face-detection task and returns an `id`. Use the face-detection details "
            "endpoint with that id to retrieve detected faces before doing individual face swaps."
        )

    is_estimate = method == "POST" and path in GENERATION_ESTIMATE_PATHS
    if is_estimate:
        additions.append(
            "Read-only preflight: returns cost and eligibility, not generated media. "
            "Does not create a project or spend credits. A ready result is a snapshot, not a reservation; "
            "blocked or unavailable does not authorize generation. No wait or polling tool is needed."
        )

    if method == "POST" and asset_type and not is_estimate:
        wait_tool = PROJECT_WAIT_TOOL_BY_ASSET[asset_type]
        additions.append(
            f"Starts an async {asset_type} job; returns `id` and `credits_charged`. "
            f"For finished results, call `{wait_tool}` with that id or poll "
            f"`GET /v1/{asset_type}-projects/{{id}}` until `complete`, `error`, or `canceled`. "
            "Results include `downloads`; wait helpers add `exact_download_urls`. "
            "Preserve signed URLs and query parameters; never append expiration metadata."
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
            "For `*_file_path`, use an existing Magic Hour path or the upload-URL endpoint's `file_path` "
            "after uploading raw bytes to its presigned URL. "
            + (
                "An unavailable input check is not approval to generate."
                if is_estimate
                else "Direct public media URLs may work if stable and fetchable as raw bytes; "
                "hotlinked URLs can fail. Prefer the upload-URL endpoint when unsure."
            )
        )

    if additions:
        operation["description"] = _append_mcp_guidance(operation.get("description", ""), additions)
    if method == "POST" and path in LIKENESS_GENERATION_PATHS:
        description = operation.get("description", "").rstrip()
        if not description.startswith(LIKENESS_USAGE_GUIDANCE):
            operation["description"] = f"{LIKENESS_USAGE_GUIDANCE}\n\n{description}"


def _operation_mentions_file_path(operation: dict[str, Any]) -> bool:
    return "_file_path" in repr(operation)


def _append_mcp_guidance(description: str, additions: list[str]) -> str:
    existing = description.strip()
    guidance = "MCP guidance:\n" + "\n".join(f"- {addition}" for addition in additions)
    if not existing:
        return guidance
    if "MCP guidance:" in existing:
        return existing
    return f"{guidance}\n\n{existing}"


def customize_openapi_component(route: Any, component: Any) -> None:
    """Small runtime component policy for tags; text policy is applied to the spec."""
    tags = getattr(component, "tags", None)
    if tags is None:
        return

    tags.add("magic-hour")

    method = str(getattr(route, "method", "")).upper()
    path = str(getattr(route, "path", ""))
    route_tags = set(getattr(route, "tags", []) or [])
    is_estimate = method == "POST" and path in GENERATION_ESTIMATE_PATHS

    if isinstance(component, Tool):
        read_only = method == "GET" or is_estimate
        component.title = getattr(route, "summary", None) or component.name
        component.annotations = ToolAnnotations(
            title=component.title,
            readOnlyHint=read_only,
            idempotentHint=True if is_estimate else None,
            # Only project deletion removes data; generation creates new projects.
            destructiveHint=method == "DELETE",
            openWorldHint=bool(re.search(r"_file_paths?\b|_urls?\b|direct URL|searchWeb\b", repr(component.parameters))),
        )

    if is_estimate:
        tags.add("read-operation")
    elif method == "POST":
        tags.add("write-operation")
    if path == "/v1/files/upload-urls":
        tags.add("upload")
    if route_tags.intersection(PROJECT_TAG_TO_ASSET) and not is_estimate:
        tags.add("generation")
