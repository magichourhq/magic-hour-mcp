from typing import List, Literal, Optional

from magic_hour.types.models import (
    V1FaceDetectionCreateResponse,
    V1FaceDetectionGetResponse,
    V1FilesUploadUrlsCreateResponse,
)
from mcp.server.fastmcp import Context
from pydantic import BaseModel, Field

from ..client import get_client
from ..instance import mcp


class UploadUrlRequest(BaseModel):
    type: Literal["audio", "image", "video"] = Field(description="Kind of asset being uploaded.")
    extension: str = Field(description="File extension without the dot, e.g. 'mp4', 'png', 'mp3'.")


@mcp.tool()
async def generate_upload_urls(
    items: List[UploadUrlRequest], ctx: Context
) -> V1FilesUploadUrlsCreateResponse:
    """Get pre-signed URLs to upload local files to Magic Hour storage.

    PUT the raw file bytes to each returned `upload_url`, then use the
    matching `file_path` in a create_* tool's *_file_path field.
    """
    async with get_client(ctx) as client:
        return await client.v1.files.upload_urls.create(
            items=[{"type_": i.type, "extension": i.extension} for i in items]
        )


@mcp.tool()
async def detect_faces(
    target_file_path: str, ctx: Context, confidence_score: Optional[float] = None
) -> V1FaceDetectionCreateResponse:
    """Detect faces in an image or video.

    Use the returned task id with get_face_detection to fetch results, needed
    for the individual-faces mode of create_face_swap / create_face_swap_photo.
    """
    kwargs = {}
    if confidence_score is not None:
        kwargs["confidence_score"] = confidence_score

    async with get_client(ctx) as client:
        return await client.v1.face_detection.create(
            assets={"target_file_path": target_file_path}, **kwargs
        )


@mcp.tool()
async def get_face_detection(id: str, ctx: Context) -> V1FaceDetectionGetResponse:
    """Get the status and detected faces for a face detection task."""
    async with get_client(ctx) as client:
        return await client.v1.face_detection.get(id=id)
