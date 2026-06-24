import typing
import logging
import difflib
from typing import Optional

import httpx
from magic_hour.types.models import V1AiVoiceClonerCreateResponse, V1AiVoiceGeneratorCreateResponse
from magic_hour.types.params.v1_ai_voice_generator_create_body_style import V1AiVoiceGeneratorCreateBodyStyle
from mcp.server.fastmcp import Context
from mcp.server.fastmcp.utilities.types import Audio
from pydantic import BaseModel, Field

from ..client import build_http_client, get_client
from ..errors import MagicHourToolError, translate_http_error
from ..instance import mcp
from ..util import omit_none

logger = logging.getLogger(__name__)

_VOICE_NAMES = tuple(
    sorted(typing.get_args(typing.get_type_hints(V1AiVoiceGeneratorCreateBodyStyle)["voice_name"]))
)
_VOICE_NAMES_BY_LOWER = {name.lower(): name for name in _VOICE_NAMES}


class VoicePresetList(BaseModel):
    total_presets: int = Field(description="Total number of preset voices known to the server.")
    matching_presets: int = Field(description="Number of presets returned after applying any optional filter.")
    voice_names: list[str] = Field(description="Voice preset names you can pass to create_ai_voice_generator.style.voice_name.")


def _list_voice_names(query: Optional[str] = None, limit: Optional[int] = None) -> list[str]:
    names = list(_VOICE_NAMES)
    if query:
        needle = query.strip().lower()
        names = [name for name in names if needle in name.lower()]
    if limit is not None:
        names = names[: max(limit, 0)]
    return names


def _resolve_voice_name(voice_name: str) -> str:
    """Match against the SDK's preset list case-insensitively, since the SDK itself enforces an exact-case enum."""
    resolved = _VOICE_NAMES_BY_LOWER.get(voice_name.lower())
    if resolved is None:
        suggestions = difflib.get_close_matches(voice_name, _VOICE_NAMES, n=5, cutoff=0.6)
        suggestion_text = f" Closest presets: {', '.join(suggestions)}." if suggestions else ""
        raise ValueError(
            f"Unknown voice_name '{voice_name}'. Use list_ai_voice_presets to browse valid presets.{suggestion_text}"
        )
    return resolved


async def _fetch_audio(url: str) -> Audio:
    """Download a completed audio clip so it can be embedded directly in the tool result."""
    try:
        async with build_http_client(follow_redirects=True) as http_client:
            response = await http_client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as error:
        raise translate_http_error(error, during="downloading generated audio output") from error

    content_type = response.headers.get("content-type", "")
    if content_type.startswith("audio/"):
        extension = content_type.split("/", 1)[1].split(";", 1)[0]
    else:
        extension = httpx.URL(url).path.rsplit(".", 1)[-1] if "." in httpx.URL(url).path else "mp3"

    return Audio(data=response.content, format=extension)


@mcp.tool(structured_output=False)
async def get_audio_project(id: str, ctx: Context):
    """Check the status of an audio project.

    After an audio `create_*` call, clients should usually wait briefly and poll
    this tool automatically until status becomes `complete`, `error`, or `canceled`.
    Once status is complete, `downloads` contains direct URLs and the generated
    audio is also returned inline (in addition to the status json) so clients
    can show both a downloadable link and an inline player.
    """
    async with get_client(ctx) as client:
        response = await client.v1.audio_projects.get(id=id)

    result = [response]
    if response.status == "complete":
        for index, download in enumerate(response.downloads, start=1):
            try:
                result.append(await _fetch_audio(download.url))
            except MagicHourToolError as error:
                logger.warning(
                    "Failed to embed audio output for project %s download %s: %s",
                    id,
                    index,
                    error,
                )
                await ctx.warning(str(error))
    return result


@mcp.tool()
async def delete_audio_project(id: str, ctx: Context) -> str:
    """Permanently delete a rendered audio project. Not reversible."""
    async with get_client(ctx) as client:
        await client.v1.audio_projects.delete(id=id)
    return f"Deleted audio project {id}"


@mcp.tool()
def list_ai_voice_presets(query: Optional[str] = None, limit: Optional[int] = None) -> VoicePresetList:
    """List the preset voices available to create_ai_voice_generator.

    Call this when the user wants to browse voices or when you need to confirm
    the exact preset spelling before generating audio. Filtering is case-insensitive.
    """
    voice_names = _list_voice_names(query=query, limit=limit)
    return VoicePresetList(
        total_presets=len(_VOICE_NAMES),
        matching_presets=len(voice_names),
        voice_names=voice_names,
    )


# ---------- AI Voice Generator ----------


class VoiceGeneratorStyle(BaseModel):
    prompt: str = Field(description="The text to speak.")
    voice_name: str = Field(
        description="One of our preset voice names, e.g. 'Morgan Freeman', 'David Attenborough', 'SpongeBob SquarePants'. "
        "Use list_ai_voice_presets to browse or filter the available preset names before calling this tool."
    )


@mcp.tool()
async def create_ai_voice_generator(
    style: VoiceGeneratorStyle, ctx: Context, name: Optional[str] = None
) -> V1AiVoiceGeneratorCreateResponse:
    """Generate speech audio from text using a preset voice. 0.05 credits per character. If you do not already know the exact preset, call list_ai_voice_presets first. Returns `{id, credits_charged}` immediately; if the user wants the finished audio, wait briefly and then poll with get_audio_project until completion."""
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
    """Clone a voice from an audio sample and generate speech. 0.05 credits per character. Returns `{id, credits_charged}` immediately; if the user wants the finished audio, wait briefly and then poll with get_audio_project until completion."""
    async with get_client(ctx) as client:
        return await client.v1.ai_voice_cloner.create(
            assets=assets.model_dump(exclude_none=True),
            style=style.model_dump(exclude_none=True),
            **omit_none(name=name),
        )
