from __future__ import annotations

from collections.abc import MutableMapping, MutableSequence
from copy import deepcopy
from typing import Any, Literal

from fastmcp.exceptions import ToolError
from fastmcp.tools.base import Tool
from pydantic import BaseModel, Field

ProjectType = Literal["video", "image", "audio"]

# Each native attachment fills the corresponding field in the existing API request.
IMAGE_ASSET_FIELDS = {
    "detect_faces": {"image": "target_file_path"},
    "create_talking_photo": {"image": "image_file_path"},
    "create_spoken_ad_video": {"image": "image_file_path"},
    "create_animation": {"image": "image_file_path"},
    "create_video_from_audio": {"image": "image_file_path"},
    "replace_character_in_video": {"character_image": "image_file_path"},
    "swap_face_in_video": {"source_face": "image_file_path"},
    "create_video_from_image": {"image": "image_file_path", "last_frame": "end_image_file_path"},
    "restyle_video": {"first_frame": "initial_image_path"},
    "change_clothes_in_photo": {"person_image": "person_file_path", "garment_image": "garment_file_path"},
    "edit_face_in_photo": {"image": "image_file_path"},
    "edit_image": {"images": "image_file_paths"},
    "generate_headshot": {"image": "image_file_path"},
    "upscale_image": {"image": "image_file_path"},
    "place_person_in_scene": {"person_image": "person_file_path", "scene_image": "scene_file_path"},
    "swap_face_in_photo": {"source_face": "source_file_path", "target_image": "target_file_path"},
    "swap_head_in_photo": {"head_image": "head_file_path", "body_image": "body_file_path"},
    "remove_image_background": {"image": "image_file_path", "background_image": "background_image_file_path"},
    "colorize_photo": {"image": "image_file_path"},
}

VIDEO_ASSET_FIELDS = {
    "detect_faces": {"video": "target_file_path"},
    "edit_video": {"video": "video_file_path"},
    "translate_video": {"video": "video_file_path"},
    "upscale_video": {"video": "video_file_path"},
    "add_subtitles_to_video": {"video": "video_file_path"},
    "replace_character_in_video": {"video": "video_file_path"},
    "swap_face_in_video": {"video": "video_file_path"},
    "create_lip_sync_video": {"video": "video_file_path"},
    "restyle_video": {"video": "video_file_path"},
}
AUDIO_ASSET_FIELDS = {
    "create_talking_photo": {"audio": "audio_file_path"},
    "create_animation": {"audio": "audio_file_path"},
    "create_video_from_audio": {"audio": "audio_file_path"},
    "create_lip_sync_video": {"audio": "audio_file_path"},
    "ai_voice_cloner_create_audio": {"audio": "audio_file_path"},
}

ASSET_FIELDS: dict[ProjectType, dict[str, dict[str, str]]] = {
    "image": IMAGE_ASSET_FIELDS,
    "video": VIDEO_ASSET_FIELDS,
    "audio": AUDIO_ASSET_FIELDS,
}

# Reference indices are one-based within each media list. Existing file paths can
# appear alongside attachments without changing the order of named references.
REFERENCE_GROUPS: dict[str, tuple[tuple[str, ...], str, dict[ProjectType, tuple[str, int]]]] = {
    "edit_video": (("references",), "file_path", {"image": ("reference_images", 10)}),
    "create_video_from_text": (
        ("references",),
        "file_path",
        {"image": ("reference_images", 30), "video": ("reference_videos", 10), "audio": ("reference_audio", 10)},
    ),
    "swap_face_in_video": (("assets", "face_mappings"), "new_face", {"image": ("replacement_faces", 5)}),
    "swap_face_in_photo": (("assets", "face_mappings"), "new_face", {"image": ("replacement_faces", 5)}),
}


class ChatGPTFile(BaseModel):
    download_url: str = Field(description="Exact HTTPS download URL supplied for the native ChatGPT attachment, including its signed query.")
    file_id: str = Field(description="Native ChatGPT file identifier paired with download_url; not a local filesystem path.")
    mime_type: str = Field(default="", description="Attachment MIME type, when supplied by ChatGPT.")
    file_name: str = Field(default="", description="Original attachment filename, when supplied by ChatGPT.")


def attachment_fields(tool_name: str) -> list[str]:
    names = [name for tools in ASSET_FIELDS.values() for name in tools.get(tool_name, {})]
    if reference := REFERENCE_GROUPS.get(tool_name):
        names.extend(name for name, _ in reference[2].values())
    return names


def _file_schema(description: str, max_items: int | None = None) -> dict[str, Any]:
    file = {**ChatGPTFile.model_json_schema(), "additionalProperties": False}
    if max_items is None:
        return {**file, "description": description}
    return {"type": "array", "items": file, "minItems": 1, "maxItems": max_items, "description": description}


def add_chatgpt_media_inputs(tool: Tool) -> None:
    schema = tool.parameters
    properties = schema.get("properties", {})
    assets = properties.get("assets", {})
    asset_properties = assets.get("properties", {})
    attachment_names = []
    asset_alternatives: dict[str, list[str]] = {}
    alternatives = []

    for media_type, tools in ASSET_FIELDS.items():
        for name, field in tools.get(tool.name, {}).items():
            if field not in asset_properties:
                continue
            asset = asset_properties[field]
            max_items = asset["maxItems"] if asset.get("type") == "array" else None
            description = f"Attach the {name.replace('_', ' ')} from ChatGPT for this input."
            if tool.name == "create_video_from_image" and name == "image":
                description = "The ChatGPT photo to use as the video's first frame."
            if max_items is not None:
                description += " Keep the files in the order the user requested."
            source = f"{media_type}_source"
            if "file" in asset_properties.get(source, {}).get("enum", []):
                description += f" Set assets.{source} to file."
            properties[name] = _file_schema(description, max_items)
            attachment_names.append(name)
            asset_alternatives.setdefault(field, []).append(name)

    for field, names in asset_alternatives.items():
        if field in assets.get("required", []):
            assets["required"].remove(field)
            alternatives.append({"anyOf": [
                *({"required": [name]} for name in names),
                {"required": ["assets"], "properties": {"assets": {"required": [field]}}},
            ]})

    if attachment_names and not assets.get("required"):
        assets.pop("required", None)
        if "assets" in schema.get("required", []):
            schema["required"].remove("assets")
            if not alternatives:
                alternatives.append({"anyOf": [
                    {"required": ["assets"]},
                    *({"required": [name]} for name in attachment_names),
                ]})

    if reference := REFERENCE_GROUPS.get(tool.name):
        group_path, file_field, reference_fields = reference
        group = properties.get(group_path[0], {})
        if len(group_path) == 2:
            group = group.get("properties", {}).get(group_path[1], {})
        item = group.get("items", {})
        if file_field in item.get("required", []):
            item["required"].remove(file_field)
            item["oneOf"] = [{"required": [file_field]}]
            for media_type, (name, limit) in reference_fields.items():
                max_items = min(group["maxItems"], limit)
                index = f"{media_type}_index"
                properties[name] = _file_schema(
                    f"Attached {media_type} files. Set {index} in each matching entry to its position in this list, starting at 1.",
                    max_items,
                )
                item["properties"][index] = {
                    "type": "integer", "minimum": 1, "maximum": max_items,
                    "description": f"Position in {name}, starting at 1. Use exactly one attachment index or {file_field}.",
                }
                item["oneOf"].append({"required": [index]})
                attachment_names.append(name)

    if alternatives:
        if len(alternatives) == 1 and "anyOf" not in schema:
            schema.update(alternatives[0])
        else:
            schema.setdefault("allOf", []).extend(alternatives)
    if attachment_names:
        if schema.get("required") == []:
            del schema["required"]
        tool.meta = {**(tool.meta or {}), "openai/fileParams": attachment_names}
        tool.description = (tool.description or "") + (
            " Use the named attachment fields first for ChatGPT files. Ask which file goes where "
            "if their roles are unclear. If a file fails, ask the user to attach it again "
            "and keep the requested settings."
        )


def prepare_chatgpt_media(
    tool_name: str, arguments: dict[str, Any]
) -> tuple[dict[str, Any], list[tuple[MutableMapping | MutableSequence, str | int, ChatGPTFile, ProjectType]]]:
    reference = REFERENCE_GROUPS.get(tool_name)
    has_index = False
    if reference:
        group_path, _, reference_fields = reference
        group = arguments if len(group_path) == 1 else arguments.get(group_path[0], {})
        entries = group.get(group_path[-1], [])
        has_index = any(f"{media_type}_index" in entry for entry in entries for media_type in reference_fields)

    if not has_index and not any(field in arguments for field in attachment_fields(tool_name)):
        return arguments, []

    # Check every assignment before transferring any file.
    args = deepcopy(arguments)
    uploads = []
    for media_type, tools in ASSET_FIELDS.items():
        for name, field in tools.get(tool_name, {}).items():
            if name not in args:
                continue
            assets = args.setdefault("assets", {})
            if field in assets:
                raise ToolError(f"Choose one file for {field.replace('_file_path', '').replace('_', ' ')}.")
            source = f"{media_type}_source"
            if source in assets and assets[source] != "file":
                raise ToolError(f"Set {source} to file to use the attached {media_type}.")
            value = args.pop(name)
            if isinstance(value, list):
                assets[field] = [None] * len(value)
                uploads.extend(
                    (assets[field], i, ChatGPTFile.model_validate(file), media_type) for i, file in enumerate(value)
                )
            else:
                assets[field] = None
                uploads.append((assets, field, ChatGPTFile.model_validate(value), media_type))

    if reference:
        group_path, file_field, reference_fields = reference
        group = args if len(group_path) == 1 else args.get(group_path[0], {})
        entries = group.get(group_path[-1], [])
        for media_type, (name, _) in reference_fields.items():
            index = f"{media_type}_index"
            files = args.pop(name, [])
            assignments = [(entry, entry.pop(index) - 1) for entry in entries if index in entry]
            if {i for _, i in assignments} != set(range(len(files))):
                raise ToolError(f"Match every file in {name} to an entry using {index} in {group_path[-1]}.")
            uploads.extend((entry, file_field, ChatGPTFile.model_validate(files[i]), media_type) for entry, i in assignments)

    return args, uploads
