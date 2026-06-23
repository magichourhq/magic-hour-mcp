import typing
from typing import Optional

import httpx
from magic_hour.types.models import V1AiVoiceClonerCreateResponse, V1AiVoiceGeneratorCreateResponse
from magic_hour.types.params.v1_ai_voice_generator_create_body_style import V1AiVoiceGeneratorCreateBodyStyle
from mcp.server.fastmcp import Context
from mcp.server.fastmcp.utilities.types import Audio
from pydantic import BaseModel, Field

from ..client import get_client
from ..instance import mcp
from ..util import omit_none

_VOICE_NAMES_BY_LOWER = {
    name.lower(): name
    for name in typing.get_args(typing.get_type_hints(V1AiVoiceGeneratorCreateBodyStyle)["voice_name"])
}


def _resolve_voice_name(voice_name: str) -> str:
    """Match against the SDK's preset list case-insensitively, since the SDK itself enforces an exact-case enum."""
    resolved = _VOICE_NAMES_BY_LOWER.get(voice_name.lower())
    if resolved is None:
        raise ValueError(f"Unknown voice_name '{voice_name}'. See https://magichour.ai/create/ai-voice-generator for valid presets.")
    return resolved


async def _fetch_audio(url: str) -> Audio:
    """Download a completed audio clip so it can be embedded directly in the tool result."""
    async with httpx.AsyncClient() as http_client:
        response = await http_client.get(url)
        response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    if content_type.startswith("audio/"):
        extension = content_type.split("/", 1)[1].split(";", 1)[0]
    else:
        extension = httpx.URL(url).path.rsplit(".", 1)[-1] if "." in httpx.URL(url).path else "mp3"

    return Audio(data=response.content, format=extension)


@mcp.tool(structured_output=False)
async def get_audio_project(id: str, ctx: Context):
    """Check the status of an audio project.

    Once status is complete, the generated audio is returned inline
    (in addition to the status json) so it can be played directly.
    """
    async with get_client(ctx) as client:
        response = await client.v1.audio_projects.get(id=id)

    result = [response]
    if response.status == "complete":
        for download in response.downloads:
            result.append(await _fetch_audio(download.url))
    return result


@mcp.tool()
async def delete_audio_project(id: str, ctx: Context) -> str:
    """Permanently delete a rendered audio project. Not reversible."""
    async with get_client(ctx) as client:
        await client.v1.audio_projects.delete(id=id)
    return f"Deleted audio project {id}"


# ---------- AI Voice Generator ----------


class VoiceGeneratorStyle(BaseModel):
    prompt: str = Field(description="The text to speak.")
    voice_name: str = Field(
        description="One of our preset voice names, e.g. 'Morgan Freeman', 'David Attenborough', 'SpongeBob SquarePants'. "
        "Hundreds of presets exist (celebrities, characters, public figures); the API rejects an unknown name."
    )


@mcp.tool()
async def create_ai_voice_generator(
    style: VoiceGeneratorStyle, ctx: Context, name: Optional[str] = None
) -> V1AiVoiceGeneratorCreateResponse:
    """Generate speech audio from text using a preset voice. 0.05 credits per character. Returns immediately with an id; poll with get_audio_project."""
    voice_style = style.model_dump(exclude_none=True)
    voice_style["voice_name"] = _resolve_voice_name(style.voice_name)
    async with get_client(ctx) as client:
        return await client.v1.ai_voice_generator.create(style=voice_style, **omit_none(name=name))


# ---------- AI Voice Cloner ----------


class VoiceClonerAssets(BaseModel):
    audio_file_path: str = Field(description="Source audio sample to clone the voice from.")


class VoiceClonerStyle(BaseModel):
    prompt: str = Field(description="The text the cloned voice should speak.")


@mcp.tool()
async def create_ai_voice_cloner(
    assets: VoiceClonerAssets, style: VoiceClonerStyle, ctx: Context, name: Optional[str] = None
) -> V1AiVoiceClonerCreateResponse:
    """Clone a voice from an audio sample and generate speech. 0.05 credits per character. Returns immediately with an id; poll with get_audio_project."""
    async with get_client(ctx) as client:
        return await client.v1.ai_voice_cloner.create(
            assets=assets.model_dump(exclude_none=True),
            style=style.model_dump(exclude_none=True),
            **omit_none(name=name),
        )
