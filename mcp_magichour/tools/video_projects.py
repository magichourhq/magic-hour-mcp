from typing import List, Literal, Optional

from magic_hour.types.models import (
    V1AiTalkingPhotoCreateResponse,
    V1AnimationCreateResponse,
    V1AudioToVideoCreateResponse,
    V1AutoSubtitleGeneratorCreateResponse,
    V1FaceSwapCreateResponse,
    V1ImageToVideoCreateResponse,
    V1LipSyncCreateResponse,
    V1TextToVideoCreateResponse,
    V1VideoProjectsGetResponse,
    V1VideoToVideoCreateResponse,
)
from mcp.server.fastmcp import Context
from pydantic import BaseModel, Field

from ..client import get_client
from ..instance import mcp
from ..util import omit_none


@mcp.tool()
async def get_video_project(id: str, ctx: Context) -> V1VideoProjectsGetResponse:
    """Check the status of a video project. `downloads` is populated once status is complete."""
    async with get_client(ctx) as client:
        return await client.v1.video_projects.get(id=id)


@mcp.tool()
async def delete_video_project(id: str, ctx: Context) -> str:
    """Permanently delete a rendered video project. Not reversible."""
    async with get_client(ctx) as client:
        await client.v1.video_projects.delete(id=id)
    return f"Deleted video project {id}"


# ---------- Text-to-Video ----------


class TextToVideoStyle(BaseModel):
    prompt: str = Field(description="The prompt used for the video.")
    quality_mode: Optional[Literal["quick", "studio"]] = Field(None, description="Quality tier for generation.")


@mcp.tool()
async def create_text_to_video(
    end_seconds: float,
    style: TextToVideoStyle,
    ctx: Context,
    aspect_ratio: Optional[Literal["16:9", "1:1", "9:16"]] = None,
    audio: Optional[bool] = None,
    model: Optional[
        Literal[
            "default", "kling-1.6", "kling-2.5", "kling-2.5-audio", "kling-3.0", "ltx-2", "ltx-2.3",
            "seedance", "seedance-2.0", "sora-2", "veo3.1", "veo3.1-audio", "veo3.1-lite", "wan-2.2",
        ]
    ] = None,
    name: Optional[str] = None,
    orientation: Optional[Literal["landscape", "portrait", "square"]] = None,
    resolution: Optional[Literal["480p", "720p", "1080p", "4k"]] = None,
) -> V1TextToVideoCreateResponse:
    """Generate a video purely from a text prompt. end_seconds: 1-60. Returns `{id, credits_charged}` immediately; poll with get_video_project."""
    async with get_client(ctx) as client:
        return await client.v1.text_to_video.create(
            end_seconds=end_seconds,
            style=style.model_dump(exclude_none=True),
            **omit_none(aspect_ratio=aspect_ratio, audio=audio, model=model, name=name, orientation=orientation, resolution=resolution),
        )


# ---------- Image-to-Video ----------


class ImageToVideoAssets(BaseModel):
    image_file_path: str = Field(description="The source image to animate.")
    end_image_file_path: Optional[str] = Field(None, description="Optional image to use as the last frame of the video.")


class ImageToVideoStyle(BaseModel):
    prompt: Optional[str] = Field(None, description="The prompt used for the video.")
    high_quality: Optional[bool] = None
    quality_mode: Optional[Literal["quick", "studio"]] = None


@mcp.tool()
async def create_image_to_video(
    assets: ImageToVideoAssets,
    end_seconds: float,
    ctx: Context,
    audio: Optional[bool] = None,
    height: Optional[int] = None,
    model: Optional[
        Literal[
            "default", "kling-1.6", "kling-2.5", "kling-2.5-audio", "kling-3.0", "ltx-2", "ltx-2.3",
            "seedance", "seedance-2.0", "sora-2", "veo3.1", "veo3.1-audio", "veo3.1-lite", "wan-2.2",
        ]
    ] = None,
    name: Optional[str] = None,
    resolution: Optional[Literal["480p", "720p", "1080p", "4k"]] = None,
    style: Optional[ImageToVideoStyle] = None,
    width: Optional[int] = None,
) -> V1ImageToVideoCreateResponse:
    """Animate an image into a video. end_seconds: 1-60. Sora 2 only supports 9:16 or 16:9 source images. Returns `{id, credits_charged}` immediately; poll with get_video_project."""
    async with get_client(ctx) as client:
        return await client.v1.image_to_video.create(
            assets=assets.model_dump(exclude_none=True),
            end_seconds=end_seconds,
            **omit_none(
                audio=audio,
                height=height,
                model=model,
                name=name,
                resolution=resolution,
                style=style.model_dump(exclude_none=True) if style else None,
                width=width,
            ),
        )


# ---------- Video-to-Video ----------


class VideoToVideoAssets(BaseModel):
    video_source: Literal["file", "youtube"] = Field(description="Choose your video source.")
    video_file_path: Optional[str] = Field(None, description="Required if video_source is 'file'.")
    youtube_url: Optional[str] = Field(None, description="Required if video_source is 'youtube'.")


class VideoToVideoStyle(BaseModel):
    art_style: str = Field(description="One of our art style presets, e.g. 'Minecraft', 'Watercolor', 'Pixel', 'Origami'.")
    model: Optional[
        Literal["3D Anime", "Absolute Reality", "Dreamshaper", "Flat 2D Anime", "Kaywaii", "Soft Anime", "Western Anime", "default"]
    ] = Field(None, description="Model used to render the chosen art style.")
    prompt: Optional[str] = Field(None, description="Required if prompt_type is 'custom' or 'append_default'.")
    prompt_type: Optional[Literal["append_default", "custom", "default"]] = None
    version: Optional[Literal["default", "v1", "v2"]] = Field(
        None, description="'v1' = more detail/closer prompt adherence. 'v2' = faster, more consistent."
    )


@mcp.tool()
async def create_video_to_video(
    assets: VideoToVideoAssets,
    end_seconds: float,
    start_seconds: float,
    style: VideoToVideoStyle,
    ctx: Context,
    fps_resolution: Optional[Literal["FULL", "HALF"]] = None,
    height: Optional[int] = None,
    name: Optional[str] = None,
    width: Optional[int] = None,
) -> V1VideoToVideoCreateResponse:
    """Restyle an existing video clip with an art style. Returns `{id, credits_charged}` immediately; poll with get_video_project."""
    async with get_client(ctx) as client:
        return await client.v1.video_to_video.create(
            assets=assets.model_dump(exclude_none=True),
            end_seconds=end_seconds,
            start_seconds=start_seconds,
            style=style.model_dump(exclude_none=True),
            **omit_none(fps_resolution=fps_resolution, height=height, name=name, width=width),
        )


# ---------- Animation ----------


class AnimationAssets(BaseModel):
    audio_source: Literal["file", "none", "youtube"] = Field(description="Optionally add an audio source for the video.")
    audio_file_path: Optional[str] = Field(None, description="Required if audio_source is 'file'.")
    youtube_url: Optional[str] = Field(None, description="Required if audio_source is 'youtube'.")
    image_file_path: Optional[str] = Field(None, description="Optional first frame for the video.")


class AnimationStyle(BaseModel):
    art_style: str = Field(description="One of our art style presets, e.g. 'Studio Ghibli Film Still', 'Cyberpunk', 'Low Poly'.")
    art_style_custom: Optional[str] = Field(None, description="Required if art_style is 'Custom'.")
    camera_effect: str = Field(description="One of our camera effect presets, e.g. 'Simple Zoom In', 'Bounce Out', 'Pan Left'.")
    prompt_type: Literal["ai_choose", "custom", "use_lyrics"] = Field(
        description="'custom' needs prompt. 'use_lyrics' needs assets.audio_source to be 'file' or 'youtube'."
    )
    prompt: Optional[str] = Field(None, description="Required if prompt_type is 'custom'.")
    transition_speed: int = Field(description="1-10. Higher = more rapid transitions between frames.")


@mcp.tool()
async def create_animation(
    assets: AnimationAssets,
    end_seconds: float,
    fps: float,
    height: int,
    style: AnimationStyle,
    width: int,
    ctx: Context,
    name: Optional[str] = None,
) -> V1AnimationCreateResponse:
    """Create an AI animation video. Credit cost scales with fps and end_seconds. Returns `{id, credits_charged}` immediately; poll with get_video_project."""
    async with get_client(ctx) as client:
        return await client.v1.animation.create(
            assets=assets.model_dump(exclude_none=True),
            end_seconds=end_seconds,
            fps=fps,
            height=height,
            style=style.model_dump(exclude_none=True),
            width=width,
            **omit_none(name=name),
        )


# ---------- AI Talking Photo ----------


class TalkingPhotoAssets(BaseModel):
    image_file_path: str = Field(description="The source image to animate.")
    audio_file_path: str = Field(description="The audio file to sync with the image.")


class TalkingPhotoStyle(BaseModel):
    generation_mode: Optional[Literal["expressive", "pro", "prompted", "realistic", "stable", "standard"]] = Field(
        None, description="Controls overall motion style. Defaults to 'realistic'."
    )
    intensity: Optional[float] = None
    prompt: Optional[str] = Field(None, description="Only used when generation_mode is 'prompted'.")


@mcp.tool()
async def create_ai_talking_photo(
    assets: TalkingPhotoAssets,
    end_seconds: float,
    start_seconds: float,
    ctx: Context,
    max_resolution: Optional[int] = None,
    name: Optional[str] = None,
    style: Optional[TalkingPhotoStyle] = None,
) -> V1AiTalkingPhotoCreateResponse:
    """Animate a photo to talk in sync with an audio clip. Max clip length depends on style.generation_mode: realistic 180s, prompted 45s. Returns `{id, credits_charged}` immediately; poll with get_video_project."""
    async with get_client(ctx) as client:
        return await client.v1.ai_talking_photo.create(
            assets=assets.model_dump(exclude_none=True),
            end_seconds=end_seconds,
            start_seconds=start_seconds,
            **omit_none(max_resolution=max_resolution, name=name, style=style.model_dump(exclude_none=True) if style else None),
        )


# ---------- Audio-to-Video ----------


class AudioToVideoAssets(BaseModel):
    audio_file_path: str = Field(description="The audio file to drive the video.")
    image_file_path: Optional[str] = Field(None, description="Optional reference image for the initial frame.")


class AudioToVideoStyle(BaseModel):
    prompt: Optional[str] = Field(None, description="Prompt to guide the visual style of the video.")


@mcp.tool()
async def create_audio_to_video(
    assets: AudioToVideoAssets,
    end_seconds: float,
    ctx: Context,
    name: Optional[str] = None,
    resolution: Optional[Literal["480p", "720p", "1080p"]] = None,
    start_seconds: Optional[float] = None,
    style: Optional[AudioToVideoStyle] = None,
) -> V1AudioToVideoCreateResponse:
    """Generate a video visualization driven by an audio clip. Returns `{id, credits_charged}` immediately; poll with get_video_project."""
    async with get_client(ctx) as client:
        return await client.v1.audio_to_video.create(
            assets=assets.model_dump(exclude_none=True),
            end_seconds=end_seconds,
            **omit_none(
                name=name,
                resolution=resolution,
                start_seconds=start_seconds,
                style=style.model_dump(exclude_none=True) if style else None,
            ),
        )


# ---------- Auto Subtitle Generator ----------


class AutoSubtitleAssets(BaseModel):
    video_file_path: str = Field(description="The video to add subtitles to.")


class AutoSubtitleCustomConfig(BaseModel):
    font: Optional[str] = Field(None, description="Font name from Google Fonts.")
    font_size: Optional[float] = None
    font_style: Optional[str] = Field(None, description="e.g. 'normal', 'italic', 'bold'.")
    text_color: Optional[str] = Field(None, description="Hex color.")
    highlighted_text_color: Optional[str] = Field(None, description="Hex color for the currently spoken word.")
    stroke_color: Optional[str] = Field(None, description="Hex color for the text outline.")
    stroke_width: Optional[float] = None
    vertical_position: Optional[str] = Field(None, description="e.g. 'top', 'center', 'bottom'.")
    horizontal_position: Optional[str] = Field(None, description="e.g. 'left', 'center', 'right'.")


class AutoSubtitleStyle(BaseModel):
    """At least one of template or custom_config must be set. If both, custom_config overrides the template's defaults."""

    template: Optional[Literal["cinematic", "highlight", "karaoke", "minimalist"]] = None
    custom_config: Optional[AutoSubtitleCustomConfig] = None


@mcp.tool()
async def create_auto_subtitle_generator(
    assets: AutoSubtitleAssets,
    end_seconds: float,
    start_seconds: float,
    style: AutoSubtitleStyle,
    ctx: Context,
    name: Optional[str] = None,
) -> V1AutoSubtitleGeneratorCreateResponse:
    """Automatically generate and burn in subtitles for a video. Returns `{id, credits_charged}` immediately; poll with get_video_project."""
    async with get_client(ctx) as client:
        return await client.v1.auto_subtitle_generator.create(
            assets=assets.model_dump(exclude_none=True),
            end_seconds=end_seconds,
            start_seconds=start_seconds,
            style=style.model_dump(exclude_none=True),
            **omit_none(name=name),
        )


# ---------- Face Swap (video) ----------


class FaceMapping(BaseModel):
    original_face: str = Field(description="Face from the target video, from detect_faces output ('<frame>-<index>.png').")
    new_face: str = Field(description="Replacement face image (URL or uploaded file_path).")


class VideoFaceSwapAssets(BaseModel):
    video_source: Literal["file", "youtube"] = Field(description="Choose your video source.")
    video_file_path: Optional[str] = Field(None, description="Required if video_source is 'file'.")
    youtube_url: Optional[str] = Field(None, description="Required if video_source is 'youtube'.")
    face_swap_mode: Optional[Literal["all-faces", "individual-faces"]] = Field(
        None,
        description="'all-faces' (default) swaps every detected face using image_file_path. "
        "'individual-faces' swaps specific faces via face_mappings (run detect_faces first).",
    )
    image_file_path: Optional[str] = Field(None, description="Required if face_swap_mode is 'all-faces'.")
    face_mappings: Optional[List[FaceMapping]] = Field(None, description="Required if face_swap_mode is 'individual-faces'.")


class VideoFaceSwapStyle(BaseModel):
    version: Optional[Literal["default", "v1", "v2"]] = Field(
        None, description="'v1' = better skin/texture, weaker identity. 'v2' = faster, stronger identity preservation."
    )


@mcp.tool()
async def create_face_swap(
    assets: VideoFaceSwapAssets,
    end_seconds: float,
    start_seconds: float,
    ctx: Context,
    height: Optional[int] = None,
    name: Optional[str] = None,
    style: Optional[VideoFaceSwapStyle] = None,
    width: Optional[int] = None,
) -> V1FaceSwapCreateResponse:
    """Swap face(s) in a video clip. Returns `{id, credits_charged}` immediately; poll with get_video_project."""
    async with get_client(ctx) as client:
        return await client.v1.face_swap.create(
            assets=assets.model_dump(exclude_none=True),
            end_seconds=end_seconds,
            start_seconds=start_seconds,
            **omit_none(height=height, name=name, style=style.model_dump(exclude_none=True) if style else None, width=width),
        )


# ---------- Lip Sync ----------


class LipSyncAssets(BaseModel):
    video_source: Literal["file", "youtube"] = Field(description="Choose your video source.")
    video_file_path: Optional[str] = Field(None, description="Required if video_source is 'file'.")
    youtube_url: Optional[str] = Field(None, description="Required if video_source is 'youtube'.")
    audio_file_path: str = Field(description="The audio file to sync the video's mouth movement to.")


class LipSyncStyle(BaseModel):
    generation_mode: Optional[Literal["lite", "pro", "standard"]] = Field(
        None, description="'lite' (default) is fastest/cheapest. 'pro' is most natural and accurate."
    )


@mcp.tool()
async def create_lip_sync(
    assets: LipSyncAssets,
    end_seconds: float,
    start_seconds: float,
    ctx: Context,
    height: Optional[int] = None,
    max_fps_limit: Optional[float] = None,
    name: Optional[str] = None,
    style: Optional[LipSyncStyle] = None,
    width: Optional[int] = None,
) -> V1LipSyncCreateResponse:
    """Sync a video's mouth movement to an audio track. Returns `{id, credits_charged}` immediately; poll with get_video_project."""
    async with get_client(ctx) as client:
        return await client.v1.lip_sync.create(
            assets=assets.model_dump(exclude_none=True),
            end_seconds=end_seconds,
            start_seconds=start_seconds,
            **omit_none(
                height=height,
                max_fps_limit=max_fps_limit,
                name=name,
                style=style.model_dump(exclude_none=True) if style else None,
                width=width,
            ),
        )
