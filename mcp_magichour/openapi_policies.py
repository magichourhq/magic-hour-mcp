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

# MCP-facing metadata is keyed by the canonical API route. The HTTP contract
# and synced OpenAPI source remain unchanged.
API_TOOL_METADATA = {
    ("GET", "/v1/account"): (
        "get_account_info",
        "Get account info",
        "Returns credit balance and subscription details for the connected account. Use to inspect "
        "account information before generation. Read-only; this is not a request-specific cost "
        "quote or model-eligibility check.",
    ),
    ("GET", "/v1/saved-items"): (
        "list_saved_items",
        "List saved items",
        "Lists the account's active saved characters, references, voice recordings, moodboards and "
        "brand kits, newest first. Reusable paths are in each item's assets[].file_path; temporary "
        "signed URLs support preview/download. Read-only; pass next_cursor as cursor for the next "
        "page.",
    ),
    ("POST", "/v1/files/upload-urls"): (
        "get_upload_urls",
        "Get upload URLs",
        "Creates presigned upload URL and file-path pairs for local media. Use when a generation "
        "needs a file the user has locally: upload raw bytes to the returned upload_url, then pass "
        "its file_path to the create tool. Does not upload file bytes or start a generation.",
    ),
    ("GET", "/v1/face-detection/{id}"): (
        "get_face_detection_results",
        "Get face detection results",
        "Returns a face-detection job's status and detected faces. Use with the id from "
        "detect_faces to inspect progress and choose face mappings before a selected-face swap. "
        "Read-only; faces may populate while processing.",
    ),
    ("POST", "/v1/face-detection"): (
        "detect_faces",
        "Detect faces",
        "Starts an asynchronous face-detection task for a supplied image or video and returns its "
        "id and charge information. Use before a selected-face swap, then retrieve its status and "
        "detected faces with get_face_detection_results. Current API documentation describes "
        "detection as free; pricing may change.",
    ),
    ("GET", "/v1/video-projects/{id}"): (
        "get_video_project",
        "Get video project",
        "Returns a video project's status and, once complete, its download URLs. Use to check "
        "progress with the id from a video create tool. Read-only.",
    ),
    ("DELETE", "/v1/video-projects/{id}"): (
        "delete_video_project",
        "Delete video project",
        "Deletes the video project and attempts to delete its completed output. Cannot be undone; "
        "confirm with the user first.",
    ),
    ("POST", "/v1/ai-talking-photo"): (
        "create_talking_photo",
        "Create talking photo",
        "Animates a still portrait so it speaks an existing audio recording. Use with a portrait "
        "photo and an audio file; text alone is not accepted and this tool does not synthesize "
        "speech. Starts a background job that costs credits; returns a project id for "
        "wait_for_video_project.",
    ),
    ("POST", "/v1/ai-ugc-ad"): (
        "create_spoken_ad_video",
        "Create spoken ad video",
        "Creates a creator-style spoken ad video from a prepared image and a text script. Use when "
        "the image is ready and the dialogue should be generated from text. Starts a background "
        "job that costs credits; returns a project id for wait_for_video_project.",
    ),
    ("POST", "/v1/ai-video-editor"): (
        "edit_video",
        "Edit video",
        "Edits the appearance or content of a supplied video segment using a text prompt. Use with "
        "a source video, the requested changes and the clip end time. Starts a background job that "
        "costs credits; returns a project id for wait_for_video_project.",
    ),
    ("POST", "/v1/ai-video-translator"): (
        "translate_video",
        "Translate video",
        "Translates speech in a supplied video segment into a supported target language. Starts a "
        "background job that costs credits; returns a project id for wait_for_video_project.",
    ),
    ("POST", "/v1/ai-video-upscaler"): (
        "upscale_video",
        "Upscale video",
        "Upscales a supplied video segment to a selected output resolution while preserving its "
        "aspect ratio. Starts a background job that costs credits; returns a project id for "
        "wait_for_video_project.",
    ),
    ("POST", "/v1/animation"): (
        "create_animation",
        "Create animation",
        "Creates a stylized animated video with art and camera settings, using a custom prompt or "
        "an audio-derived prompt mode. Can use an initial image and audio. Credits depend on frame "
        "rate and duration; returns a project id for wait_for_video_project.",
    ),
    ("POST", "/v1/audio-to-video"): (
        "create_video_from_audio",
        "Create video from audio",
        "Generates an audio-guided video from a supplied audio track, with an optional starting "
        "image and visual prompt. Starts a background job that costs credits; returns a project id "
        "for wait_for_video_project.",
    ),
    ("POST", "/v1/auto-subtitle-generator"): (
        "add_subtitles_to_video",
        "Add subtitles to video",
        "Adds automatically generated subtitles to a supplied video segment using a subtitle "
        "preset or custom text styling. Starts a background job that costs credits; returns a "
        "project id for wait_for_video_project.",
    ),
    ("POST", "/v1/character-replace"): (
        "replace_character_in_video",
        "Replace character in video",
        "Replaces a subject in a video with a character from a reference image, or transfers the "
        "video's motion onto that character. Starts a background job that costs credits; returns a "
        "project id for wait_for_video_project.",
    ),
    ("POST", "/v1/face-swap"): (
        "swap_face_in_video",
        "Swap face in video",
        "Swaps faces in an existing video: all faces with a reference image, or selected faces "
        "using detect_faces results. Starts a background job that costs credits; returns a project "
        "id for wait_for_video_project.",
    ),
    ("POST", "/v1/image-to-video"): (
        "create_video_from_image",
        "Create video from image",
        "Animates a supplied image into a video using motion instructions and available model "
        "options. Starts a background job that costs credits; returns a project id for "
        "wait_for_video_project.",
    ),
    ("POST", "/v1/lip-sync"): (
        "create_lip_sync_video",
        "Create lip sync video",
        "Adjusts an existing video's lip movements to match supplied replacement audio. For a "
        "still portrait plus audio, use create_talking_photo. Starts a background job that costs "
        "credits; returns a project id for wait_for_video_project.",
    ),
    ("POST", "/v1/text-to-video"): (
        "create_video_from_text",
        "Create video from text",
        "Generates a video from a text prompt, with optional supported reference inputs and model "
        "settings. To animate an existing starting image, use create_video_from_image. Starts a "
        "background job that costs credits; returns a project id for wait_for_video_project.",
    ),
    ("POST", "/v1/video-to-video"): (
        "restyle_video",
        "Restyle video",
        "Restyles an existing video clip with an art-style preset or a custom prompt. Supports "
        "v1/v2 presets, not v3. Starts a background job that costs credits; returns a project id "
        "for wait_for_video_project.",
    ),
    ("GET", "/v1/image-projects/{id}"): (
        "get_image_project",
        "Get image project",
        "Returns an image project's status and, once complete, its download URLs. Use to check "
        "progress with the id from an image create tool. Read-only.",
    ),
    ("DELETE", "/v1/image-projects/{id}"): (
        "delete_image_project",
        "Delete image project",
        "Deletes the image project and attempts to delete its completed output. Cannot be undone; "
        "confirm with the user first.",
    ),
    ("POST", "/v1/ai-clothes-changer"): (
        "change_clothes_in_photo",
        "Change clothes in photo",
        "Changes clothing in a person photo using a supplied garment reference image. Costs 25 "
        "credits. Starts a background job; returns a project id for wait_for_image_project.",
    ),
    ("POST", "/v1/ai-face-editor"): (
        "edit_face_in_photo",
        "Edit face in photo",
        "Adjusts facial expression, pose and gaze in a supplied photo using numeric controls. "
        "Costs 1 credit per edit; output size depends on the subscription tier. Starts a "
        "background job; returns a project id for wait_for_image_project.",
    ),
    ("POST", "/v1/ai-gif-generator"): (
        "create_gif_or_short_video",
        "Create GIF or short video",
        "Generates a short animation from a text prompt, with GIF, MP4 or WebM output. Costs 50 "
        "credits. Starts a background job; returns a project id for wait_for_image_project.",
    ),
    ("POST", "/v1/ai-image-editor"): (
        "edit_image",
        "Edit image",
        "Edits one or more supplied images using a text prompt. For prompt-only creation use "
        "generate_image. Costs credits; returns a project id for wait_for_image_project. Model "
        "eligibility depends on the account plan; the default model is not available on every "
        "plan.",
    ),
    ("POST", "/v1/ai-headshot-generator"): (
        "generate_headshot",
        "Generate headshot",
        "Creates a headshot from a supplied photo containing one detectable face, with optional "
        "appearance instructions. Costs 50 credits. Starts a background job; returns a project id "
        "for wait_for_image_project.",
    ),
    ("POST", "/v1/ai-image-generator"): (
        "generate_image",
        "Generate image",
        "Generates one or more images from a text prompt, with model, output size and image count "
        "options. Costs credits; returns a project id for wait_for_image_project.",
    ),
    ("POST", "/v1/ai-image-upscaler"): (
        "upscale_image",
        "Upscale image",
        "Upscales a supplied image 2x or 4x. Cost depends on scale and mode (25-200 credits); "
        "returns a project id for wait_for_image_project.",
    ),
    ("POST", "/v1/ai-meme-generator"): (
        "create_meme",
        "Create meme",
        "Creates a meme from a topic and template selection. Costs 10 credits. Starts a background "
        "job; returns a project id for wait_for_image_project.",
    ),
    ("POST", "/v1/ai-qr-code-generator"): (
        "create_qr_code",
        "Create QR code",
        "Creates a stylized QR code from supplied text or a URL and an art-style description. "
        "Currently costs 0 credits. Starts a background job; returns a project id for "
        "wait_for_image_project.",
    ),
    ("POST", "/v1/body-swap"): (
        "place_person_in_scene",
        "Place person in scene",
        "Places a person from one supplied photo into a supplied scene image. Cost depends on "
        "resolution (from 50 credits at 640px); returns a project id for wait_for_image_project.",
    ),
    ("POST", "/v1/face-swap-photo"): (
        "swap_face_in_photo",
        "Swap face in photo",
        "Swaps faces in a supplied photo using a source face image or selected face mappings. "
        "Costs 10 credits; output size depends on subscription. Returns a project id for "
        "wait_for_image_project.",
    ),
    ("POST", "/v1/head-swap"): (
        "swap_head_in_photo",
        "Swap head in photo",
        "Swaps a head from a supplied image onto a supplied body image. Costs 10 credits; returns "
        "a project id for wait_for_image_project.",
    ),
    ("POST", "/v1/image-background-remover"): (
        "remove_image_background",
        "Remove image background",
        "Removes an image's background, optionally replacing it with another supplied image. Costs "
        "5 credits. Starts a background job; returns a project id for wait_for_image_project.",
    ),
    ("POST", "/v1/photo-colorizer"): (
        "colorize_photo",
        "Colorize photo",
        "Colorizes a black-and-white or faded photo. Costs 10 credits; returns a project id for "
        "wait_for_image_project.",
    ),
    ("GET", "/v1/audio-projects/{id}"): (
        "get_audio_project",
        "Get audio project",
        "Returns an audio project's status and, once complete, its download URLs. Use to check "
        "progress with the id from an audio create tool. Read-only.",
    ),
    ("DELETE", "/v1/audio-projects/{id}"): (
        "delete_audio_project",
        "Delete audio project",
        "Deletes the audio project and attempts to delete its completed output. Cannot be undone; "
        "confirm with the user first.",
    ),
    ("POST", "/v1/ai-music-generator"): (
        "generate_music",
        "Generate music",
        "Generates music from a description, with optional custom lyrics or instrumental-only "
        "output. Costs 1 credit per requested second; returns a project id for "
        "wait_for_audio_project.",
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
        "`create_talking_photo` with the original image and audio.",
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

    metadata = API_TOOL_METADATA.get((method, path))
    if metadata:
        operation["operationId"], operation["summary"], operation["description"] = metadata

    properties = (
        operation.get("requestBody", {}).get("content", {}).get("application/json", {})
        .get("schema", {}).get("properties", {})
    )
    if method == "POST" and path in GENERATION_PARAMETER_DESCRIPTIONS:
        for property_path, description in GENERATION_PARAMETER_DESCRIPTIONS[path].items():
            parameter = properties.get(property_path[0], {})
            for name in property_path[1:]:
                parameter = parameter.get("properties", {}).get(name, {})
            if parameter:
                parameter["description"] = description

    if method == "POST" and path in {"/v1/ai-image-editor", "/v1/ai-image-generator"}:
        model = properties.get("model", {})
        if isinstance(model.get("description"), str):
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
            "For prompt-only creation, use `generate_image` instead."
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

    if additions and not metadata:
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
