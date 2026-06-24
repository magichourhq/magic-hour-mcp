import logging
from typing import List, Literal, Optional

from magic_hour.types.models import (
    V1AiClothesChangerCreateResponse,
    V1AiFaceEditorCreateResponse,
    V1AiGifGeneratorCreateResponse,
    V1AiHeadshotGeneratorCreateResponse,
    V1AiImageEditorCreateResponse,
    V1AiImageGeneratorCreateResponse,
    V1AiImageUpscalerCreateResponse,
    V1AiMemeGeneratorCreateResponse,
    V1AiQrCodeGeneratorCreateResponse,
    V1BodySwapCreateResponse,
    V1FaceSwapPhotoCreateResponse,
    V1HeadSwapCreateResponse,
    V1ImageBackgroundRemoverCreateResponse,
    V1PhotoColorizerCreateResponse,
)
from mcp.server.fastmcp import Context
import httpx
from mcp.server.fastmcp.utilities.types import Image
from pydantic import BaseModel, Field

from ..client import build_http_client, get_client
from ..errors import MagicHourToolError, translate_http_error
from ..instance import mcp
from ..util import omit_none

logger = logging.getLogger(__name__)


async def _fetch_image(url: str) -> Image:
    """Download a completed image so it can be embedded directly in the tool result."""
    try:
        async with build_http_client(follow_redirects=True) as http_client:
            response = await http_client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as error:
        raise translate_http_error(error, during="downloading generated image output") from error

    content_type = response.headers.get("content-type", "")
    if content_type.startswith("image/"):
        extension = content_type.split("/", 1)[1].split(";", 1)[0]
    else:
        extension = httpx.URL(url).path.rsplit(".", 1)[-1] if "." in httpx.URL(url).path else "png"

    return Image(data=response.content, format=extension)


@mcp.tool(structured_output=False)
async def get_image_project(id: str, ctx: Context):
    """Check the status of an image project.

    After an image `create_*` call, clients should usually wait briefly and poll
    this tool automatically until status becomes `complete`, `error`, or `canceled`.
    Once status is complete, `downloads` contains direct URLs and the generated
    image(s) are also returned inline (in addition to the status json) so clients
    can show both a downloadable link and an inline preview.
    """
    async with get_client(ctx) as client:
        response = await client.v1.image_projects.get(id=id)

    result = [response]
    if response.status == "complete":
        for index, download in enumerate(response.downloads, start=1):
            try:
                result.append(await _fetch_image(download.url))
            except MagicHourToolError as error:
                logger.warning(
                    "Failed to embed image output for project %s download %s: %s",
                    id,
                    index,
                    error,
                )
                await ctx.warning(str(error))
    return result


@mcp.tool()
async def delete_image_project(id: str, ctx: Context) -> str:
    """Permanently delete a rendered image project. Not reversible."""
    async with get_client(ctx) as client:
        await client.v1.image_projects.delete(id=id)
    return f"Deleted image project {id}"


# ---------- AI Image Generator ----------


class ImageGeneratorStyle(BaseModel):
    prompt: str = Field(description="The prompt used for the image(s).")
    tool: Optional[str] = Field(
        None,
        description="Art style preset, e.g. 'general', 'ai-anime-generator', 'ai-logo-generator'. Defaults to 'general'.",
    )
    quality_mode: Optional[Literal["pro", "standard"]] = Field(None, description="Quality tier for generation.")


@mcp.tool()
async def create_ai_image_generator(
    image_count: int,
    style: ImageGeneratorStyle,
    ctx: Context,
    aspect_ratio: Optional[Literal["16:9", "1:1", "9:16"]] = None,
    model: Optional[
        Literal[
            "default", "flux-schnell", "flux-2-klein", "z-image-turbo", "seedream-v4",
            "nano-banana", "nano-banana-2", "nano-banana-pro", "gpt-image-2", "seedream",
        ]
    ] = None,
    name: Optional[str] = None,
    resolution: Optional[Literal["640px", "1k", "2k", "4k", "auto"]] = None,
) -> V1AiImageGeneratorCreateResponse:
    """Create AI image(s) from a text prompt. Returns `{id, credits_charged}` immediately; if the user wants the finished image, wait briefly and then poll with get_image_project until completion."""
    async with get_client(ctx) as client:
        return await client.v1.ai_image_generator.create(
            image_count=image_count,
            style=style.model_dump(exclude_none=True),
            **omit_none(aspect_ratio=aspect_ratio, model=model, name=name, resolution=resolution),
        )


# ---------- AI Image Editor ----------


class ImageEditorAssets(BaseModel):
    image_file_path: Optional[str] = Field(None, description="A single source image to edit.")
    image_file_paths: Optional[List[str]] = Field(
        None, description="Multiple source images to edit (up to 10). Use this OR image_file_path."
    )


class ImageEditorStyle(BaseModel):
    prompt: str = Field(description="The prompt used to edit the image.")
    model: Optional[Literal["default", "Nano Banana", "Seedream"]] = Field(None, description="Editing model to use.")


@mcp.tool()
async def create_ai_image_editor(
    assets: ImageEditorAssets,
    style: ImageEditorStyle,
    ctx: Context,
    aspect_ratio: Optional[Literal["auto", "16:9", "9:16", "4:3", "3:2", "1:1", "4:5", "2:3"]] = None,
    image_count: Optional[int] = None,
    model: Optional[
        Literal["default", "qwen-edit", "flux-2-klein", "nano-banana", "nano-banana-2", "nano-banana-pro", "gpt-image-2", "seedream-v4", "seedream-v4.5"]
    ] = None,
    name: Optional[str] = None,
    resolution: Optional[Literal["auto", "640px", "1k", "2k", "4k"]] = None,
) -> V1AiImageEditorCreateResponse:
    """Edit existing image(s) with an AI prompt. Returns `{id, credits_charged}` immediately; if the user wants the finished image, wait briefly and then poll with get_image_project until completion."""
    async with get_client(ctx) as client:
        return await client.v1.ai_image_editor.create(
            assets=assets.model_dump(exclude_none=True),
            style=style.model_dump(exclude_none=True),
            **omit_none(aspect_ratio=aspect_ratio, image_count=image_count, model=model, name=name, resolution=resolution),
        )


# ---------- AI Image Upscaler ----------


class ImageUpscalerAssets(BaseModel):
    image_file_path: str = Field(description="The image to upscale.")


class ImageUpscalerStyle(BaseModel):
    mode: Optional[Literal["pro", "creative"]] = Field(
        None, description="'pro' is faster and skips enhancement. 'creative' uses the enhancement field. Defaults to 'creative'."
    )
    enhancement: Optional[Literal["Resemblance", "Balanced", "Creative"]] = Field(
        None, description="Enhancement strength, used when mode is 'creative'."
    )
    prompt: Optional[str] = Field(None, description="Guides the result. Only used when enhancement is 'Creative'.")


@mcp.tool()
async def create_ai_image_upscaler(
    assets: ImageUpscalerAssets,
    scale_factor: float,
    style: ImageUpscalerStyle,
    ctx: Context,
    name: Optional[str] = None,
) -> V1AiImageUpscalerCreateResponse:
    """Upscale an image. scale_factor must be 2 or 4 (4x needs Creator/Pro/Business tier). Returns `{id, credits_charged}` immediately; if the user wants the finished image, wait briefly and then poll with get_image_project until completion."""
    async with get_client(ctx) as client:
        return await client.v1.ai_image_upscaler.create(
            assets=assets.model_dump(exclude_none=True),
            scale_factor=scale_factor,
            style=style.model_dump(exclude_none=True),
            **omit_none(name=name),
        )


# ---------- AI Clothes Changer ----------


class ClothesChangerAssets(BaseModel):
    person_file_path: str = Field(description="Image of the person.")
    garment_file_path: str = Field(description="Image of the garment/outfit.")
    garment_type: Optional[Literal["entire_outfit", "upper_body", "lower_body", "dresses"]] = Field(
        None, description="Which part of the outfit to swap. Defaults to entire outfit."
    )


@mcp.tool()
async def create_ai_clothes_changer(
    assets: ClothesChangerAssets, ctx: Context, name: Optional[str] = None
) -> V1AiClothesChangerCreateResponse:
    """Change the outfit on a person in a photo. 25 credits per photo. Returns `{id, credits_charged}` immediately; if the user wants the finished image, wait briefly and then poll with get_image_project until completion."""
    async with get_client(ctx) as client:
        return await client.v1.ai_clothes_changer.create(
            assets=assets.model_dump(exclude_none=True), **omit_none(name=name)
        )


# ---------- AI Face Editor ----------


class FaceEditorAssets(BaseModel):
    image_file_path: str = Field(description="The image whose face will be edited.")


class FaceEditorStyle(BaseModel):
    """All numeric fields are -100 to 100, in increments of 5, default 0."""

    enhance_face: Optional[bool] = Field(None, description="Enhance face features.")
    eyebrow_direction: Optional[float] = None
    eye_gaze_horizontal: Optional[float] = None
    eye_gaze_vertical: Optional[float] = None
    eye_open_ratio: Optional[float] = None
    lip_open_ratio: Optional[float] = None
    head_roll: Optional[float] = None
    head_pitch: Optional[float] = None
    head_yaw: Optional[float] = None
    mouth_grim: Optional[float] = None
    mouth_pout: Optional[float] = None
    mouth_purse: Optional[float] = None
    mouth_smile: Optional[float] = None
    mouth_position_horizontal: Optional[float] = None
    mouth_position_vertical: Optional[float] = None


@mcp.tool()
async def create_ai_face_editor(
    assets: FaceEditorAssets, style: FaceEditorStyle, ctx: Context, name: Optional[str] = None
) -> V1AiFaceEditorCreateResponse:
    """Tweak facial features (eyes, mouth, head angle) on an image. Costs 1 frame. Returns `{id, credits_charged}` immediately; if the user wants the finished image, wait briefly and then poll with get_image_project until completion."""
    async with get_client(ctx) as client:
        return await client.v1.ai_face_editor.create(
            assets=assets.model_dump(exclude_none=True),
            style=style.model_dump(exclude_none=True),
            **omit_none(name=name),
        )


# ---------- AI GIF Generator ----------


class GifGeneratorStyle(BaseModel):
    prompt: str = Field(description="The prompt used for the GIF.")


@mcp.tool()
async def create_ai_gif_generator(
    style: GifGeneratorStyle,
    ctx: Context,
    name: Optional[str] = None,
    output_format: Optional[Literal["gif", "mp4", "webm"]] = None,
) -> V1AiGifGeneratorCreateResponse:
    """Create an animated GIF from a prompt. 50 credits. Returns `{id, credits_charged}` immediately; if the user wants the finished asset, wait briefly and then poll with get_image_project until completion."""
    async with get_client(ctx) as client:
        return await client.v1.ai_gif_generator.create(
            style=style.model_dump(exclude_none=True), **omit_none(name=name, output_format=output_format)
        )


# ---------- AI Headshot Generator ----------


class HeadshotAssets(BaseModel):
    image_file_path: str = Field(description="Image with one detectable face, used to generate the headshot.")


class HeadshotStyle(BaseModel):
    prompt: Optional[str] = Field(None, description="Optional prompt to customize the headshot's style.")


@mcp.tool()
async def create_ai_headshot_generator(
    assets: HeadshotAssets,
    ctx: Context,
    name: Optional[str] = None,
    style: Optional[HeadshotStyle] = None,
) -> V1AiHeadshotGeneratorCreateResponse:
    """Generate a professional headshot from a photo. 50 credits. Returns `{id, credits_charged}` immediately; if the user wants the finished image, wait briefly and then poll with get_image_project until completion."""
    async with get_client(ctx) as client:
        return await client.v1.ai_headshot_generator.create(
            assets=assets.model_dump(exclude_none=True),
            **omit_none(name=name, style=style.model_dump(exclude_none=True) if style else None),
        )


# ---------- AI Meme Generator ----------


class MemeGeneratorStyle(BaseModel):
    topic: str = Field(description="The topic of the meme.")
    template: Literal[
        "Random", "Drake Hotline Bling", "Galaxy Brain", "Two Buttons", "Gru's Plan",
        "Bike Fall", "Change My Mind", "Disappointed Guy", "Is This a Pigeon",
        "Panik Kalm Panik", "Side Eyeing Chloe", "Tuxedo Winnie The Pooh", "Waiting Skeleton",
    ] = Field(description="One of our meme templates.")
    search_web: Optional[bool] = Field(None, description="Whether to search the web for meme content.")


@mcp.tool()
async def create_ai_meme_generator(
    style: MemeGeneratorStyle, ctx: Context, name: Optional[str] = None
) -> V1AiMemeGeneratorCreateResponse:
    """Create an AI generated meme. 10 credits. Returns `{id, credits_charged}` immediately; if the user wants the finished image, wait briefly and then poll with get_image_project until completion."""
    async with get_client(ctx) as client:
        return await client.v1.ai_meme_generator.create(
            style=style.model_dump(exclude_none=True), **omit_none(name=name)
        )


# ---------- AI QR Code Generator ----------


class QrCodeStyle(BaseModel):
    art_style: str = Field(
        description="A preset name, e.g. 'Watercolor', 'Cyberpunk City', 'Ink Landscape', 'Minecraft', 'Spaceship'."
    )


@mcp.tool()
async def create_ai_qr_code_generator(
    content: str, style: QrCodeStyle, ctx: Context, name: Optional[str] = None
) -> V1AiQrCodeGeneratorCreateResponse:
    """Create an artistic QR code that still scans. content is the URL/text it encodes. 0 credits. Returns `{id, credits_charged}` immediately; if the user wants the finished image, wait briefly and then poll with get_image_project until completion."""
    async with get_client(ctx) as client:
        return await client.v1.ai_qr_code_generator.create(
            content=content, style=style.model_dump(exclude_none=True), **omit_none(name=name)
        )


# ---------- Body Swap ----------


class BodySwapAssets(BaseModel):
    person_file_path: str = Field(description="Image of the person to place into the scene.")
    scene_file_path: str = Field(description="Target scene image (background).")


@mcp.tool()
async def create_body_swap(
    assets: BodySwapAssets,
    resolution: Literal["640px", "1k", "2k", "4k"],
    ctx: Context,
    name: Optional[str] = None,
) -> V1BodySwapCreateResponse:
    """Swap a person into a scene image. Credits scale with resolution (from 100 credits). Returns `{id, credits_charged}` immediately; if the user wants the finished image, wait briefly and then poll with get_image_project until completion."""
    async with get_client(ctx) as client:
        return await client.v1.body_swap.create(
            assets=assets.model_dump(exclude_none=True), resolution=resolution, **omit_none(name=name)
        )


# ---------- Face Swap Photo ----------


class FaceMapping(BaseModel):
    original_face: str = Field(description="Face from the target image, from detect_faces output ('<frame>-<index>.png').")
    new_face: str = Field(description="Replacement face image (URL or uploaded file_path).")


class FaceSwapPhotoAssets(BaseModel):
    target_file_path: str = Field(description="Image containing the face(s) to be replaced.")
    face_swap_mode: Optional[Literal["all-faces", "individual-faces"]] = Field(
        None,
        description="'all-faces' (default) swaps every detected face using source_file_path. "
        "'individual-faces' swaps specific faces via face_mappings (run detect_faces first).",
    )
    source_file_path: Optional[str] = Field(None, description="Required if face_swap_mode is 'all-faces'.")
    face_mappings: Optional[List[FaceMapping]] = Field(None, description="Required if face_swap_mode is 'individual-faces'.")


@mcp.tool()
async def create_face_swap_photo(
    assets: FaceSwapPhotoAssets, ctx: Context, name: Optional[str] = None
) -> V1FaceSwapPhotoCreateResponse:
    """Swap face(s) in a photo. 10 credits per photo. Returns `{id, credits_charged}` immediately; if the user wants the finished image, wait briefly and then poll with get_image_project until completion."""
    async with get_client(ctx) as client:
        return await client.v1.face_swap_photo.create(
            assets=assets.model_dump(exclude_none=True), **omit_none(name=name)
        )


# ---------- Head Swap ----------


class HeadSwapAssets(BaseModel):
    body_file_path: str = Field(description="Image that receives the swapped head.")
    head_file_path: str = Field(description="Image of the head to place on the body.")


@mcp.tool()
async def create_head_swap(
    assets: HeadSwapAssets,
    ctx: Context,
    max_resolution: Optional[int] = None,
    name: Optional[str] = None,
) -> V1HeadSwapCreateResponse:
    """Swap a head onto a body image. 10 credits per image. Returns `{id, credits_charged}` immediately; if the user wants the finished image, wait briefly and then poll with get_image_project until completion."""
    async with get_client(ctx) as client:
        return await client.v1.head_swap.create(
            assets=assets.model_dump(exclude_none=True), **omit_none(max_resolution=max_resolution, name=name)
        )


# ---------- Image Background Remover ----------


class BackgroundRemoverAssets(BaseModel):
    image_file_path: str = Field(description="The image to remove the background from.")
    background_image_file_path: Optional[str] = Field(
        None, description="Optional replacement background image."
    )


@mcp.tool()
async def create_image_background_remover(
    assets: BackgroundRemoverAssets, ctx: Context, name: Optional[str] = None
) -> V1ImageBackgroundRemoverCreateResponse:
    """Remove (or replace) the background of an image. 5 credits. Returns `{id, credits_charged}` immediately; if the user wants the finished image, wait briefly and then poll with get_image_project until completion."""
    async with get_client(ctx) as client:
        return await client.v1.image_background_remover.create(
            assets=assets.model_dump(exclude_none=True), **omit_none(name=name)
        )


# ---------- Photo Colorizer ----------


class PhotoColorizerAssets(BaseModel):
    image_file_path: str = Field(description="The black-and-white image to colorize.")


@mcp.tool()
async def create_photo_colorizer(
    assets: PhotoColorizerAssets, ctx: Context, name: Optional[str] = None
) -> V1PhotoColorizerCreateResponse:
    """Colorize a black-and-white photo. 10 credits. Returns `{id, credits_charged}` immediately; if the user wants the finished image, wait briefly and then poll with get_image_project until completion."""
    async with get_client(ctx) as client:
        return await client.v1.photo_colorizer.create(
            assets=assets.model_dump(exclude_none=True), **omit_none(name=name)
        )
