from __future__ import annotations

from collections.abc import MutableMapping, MutableSequence
from copy import deepcopy
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.tools.base import Tool
from pydantic import BaseModel, Field

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

# For arrays of named inputs, image_index keeps the attachment paired to its
# reference or face mapping even when some entries use existing asset paths.
IMAGE_REFERENCE_FIELDS = {
    "edit_video": (("references",), "file_path", "reference_images"),
    "create_video_from_text": (("references",), "file_path", "reference_images"),
    "swap_face_in_video": (("assets", "face_mappings"), "new_face", "replacement_faces"),
    "swap_face_in_photo": (("assets", "face_mappings"), "new_face", "replacement_faces"),
}


class ChatGPTFile(BaseModel):
    download_url: str = Field(description="Exact HTTPS download URL supplied for the native ChatGPT attachment, including its signed query.")
    file_id: str = Field(description="Native ChatGPT file identifier paired with download_url; not a local filesystem path.")
    mime_type: str = Field(default="", description="Attachment MIME type, when supplied by ChatGPT.")
    file_name: str = Field(default="", description="Original attachment filename, when supplied by ChatGPT.")


def image_attachment_fields(name: str) -> list[str]:
    fields = list(IMAGE_ASSET_FIELDS.get(name, {}))
    if reference := IMAGE_REFERENCE_FIELDS.get(name):
        fields.append(reference[2])
    return fields


def _image_schema(description: str, max_items: int | None = None) -> dict[str, Any]:
    image = {**ChatGPTFile.model_json_schema(), "additionalProperties": False}
    if max_items is None:
        return {**image, "description": description}
    return {"type": "array", "items": image, "minItems": 1, "maxItems": max_items, "description": description}


def add_chatgpt_image_inputs(tool: Tool) -> None:
    schema = tool.parameters
    properties = schema.get("properties", {})
    assets = properties.get("assets", {})
    asset_properties = assets.get("properties", {})
    attachment_names = []
    alternatives = []

    for name, field in IMAGE_ASSET_FIELDS.get(tool.name, {}).items():
        if field not in asset_properties:
            continue
        asset = asset_properties[field]
        max_items = asset["maxItems"] if asset.get("type") == "array" else None
        description = f"Attach the {name.replace('_', ' ')} from ChatGPT for this input."
        if tool.name == "create_video_from_image" and name == "image":
            description = "The ChatGPT photo to use as the video's first frame."
        if max_items is not None:
            description += " Keep the images in the order the user requested."
        properties[name] = _image_schema(description, max_items)
        attachment_names.append(name)
        if field in assets.get("required", []):
            assets["required"].remove(field)
            alternatives.append({"anyOf": [
                {"required": [name]},
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

    if reference := IMAGE_REFERENCE_FIELDS.get(tool.name):
        group_path, file_field, attachment_field = reference
        group = properties.get(group_path[0], {})
        if len(group_path) == 2:
            group = group.get("properties", {}).get(group_path[1], {})
        item = group.get("items", {})
        if file_field in item.get("required", []):
            max_items = min(group["maxItems"], 30)
            properties[attachment_field] = _image_schema(
                "Attached images. Set image_index in each matching entry to its position in this list, starting at 1.",
                max_items,
            )
            item["properties"]["image_index"] = {
                "type": "integer", "minimum": 1, "maximum": max_items,
                "description": f"Position in {attachment_field}, starting at 1. Use this or {file_field}.",
            }
            item["required"].remove(file_field)
            item["oneOf"] = [{"required": [file_field]}, {"required": ["image_index"]}]
            attachment_names.append(attachment_field)

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
            " Pass ChatGPT images in the named attachment fields. Ask which image goes where "
            "if their roles are unclear. If an attachment fails, ask the user to choose it again "
            "and keep the requested settings."
        )


def prepare_chatgpt_images(
    tool_name: str, arguments: dict[str, Any]
) -> tuple[dict[str, Any], list[tuple[MutableMapping | MutableSequence, str | int, ChatGPTFile]]]:
    reference = IMAGE_REFERENCE_FIELDS.get(tool_name)
    if reference:
        group_path = reference[0]
        group = arguments if len(group_path) == 1 else arguments.get(group_path[0], {})
        has_index = any("image_index" in entry for entry in group.get(group_path[-1], []))
    else:
        has_index = False
    if not has_index and not any(field in arguments for field in image_attachment_fields(tool_name)):
        return arguments, []

    # Build and check every assignment before uploading any image.
    args = deepcopy(arguments)
    uploads = []
    for attachment, field in IMAGE_ASSET_FIELDS.get(tool_name, {}).items():
        if attachment not in args:
            continue
        assets = args.setdefault("assets", {})
        if field in assets:
            raise ToolError(f"Choose one image for {attachment.replace('_', ' ')}.")
        value = args.pop(attachment)
        if isinstance(value, list):
            assets[field] = [None] * len(value)
            uploads.extend((assets[field], index, ChatGPTFile.model_validate(file)) for index, file in enumerate(value))
        else:
            uploads.append((assets, field, ChatGPTFile.model_validate(value)))

    if reference:
        group_path, file_field, attachment_field = reference
        group = args if len(group_path) == 1 else args.get(group_path[0], {})
        entries = group.get(group_path[-1], [])
        files = args.pop(attachment_field, [])
        assignments = [(entry, entry.pop("image_index") - 1) for entry in entries if "image_index" in entry]
        if {index for _, index in assignments} != set(range(len(files))):
            raise ToolError(f"Match every attached image to an entry using image_index in {group_path[-1]}.")
        uploads.extend((entry, file_field, ChatGPTFile.model_validate(files[index])) for entry, index in assignments)

    return args, uploads
