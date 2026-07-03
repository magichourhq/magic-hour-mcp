from __future__ import annotations

import asyncio
import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from fastmcp import FastMCP
from fastmcp.server.providers.openapi import MCPType, RouteMap
from fastmcp.tools.base import ToolResult
from fastmcp.utilities.types import Audio, Image
from mcp.types import TextContent
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from .openapi_auth import BearerPassthroughAuth, BearerPassthroughMiddleware, current_authorization_header
from .openapi_policies import apply_magic_hour_policies, customize_openapi_component

ProjectType = Literal["video", "image", "audio"]

DEFAULT_API_BASE_URL = "https://api.magichour.ai"
DEFAULT_OPENAPI_PATH = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"
API_TIMEOUT = httpx.Timeout(60.0, connect=10.0, read=60.0, write=60.0, pool=10.0)
API_LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10)
API_RETRIES = 2
DEFAULT_MEDIA_FETCH_MAX_BYTES = 15 * 1024 * 1024
TERMINAL_PROJECT_STATUSES = {"complete", "error", "canceled"}
SIGNED_DOWNLOAD_GUIDANCE = (
    "Returns sanitized download fields. Use `exact_download_urls[n]` or `downloads[n].url` exactly as returned; "
    "do not shorten it, remove query parameters, or append expiration metadata."
)


def load_openapi_spec(path: str | os.PathLike[str] = DEFAULT_OPENAPI_PATH) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as spec_file:
        return json.load(spec_file)


def build_api_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=os.getenv("MAGIC_HOUR_API_BASE_URL", DEFAULT_API_BASE_URL),
        auth=BearerPassthroughAuth(),
        timeout=API_TIMEOUT,
        limits=API_LIMITS,
        transport=httpx.AsyncHTTPTransport(retries=API_RETRIES),
    )


def create_mcp() -> FastMCP:
    spec_path = os.getenv("MAGIC_HOUR_OPENAPI_PATH", str(DEFAULT_OPENAPI_PATH))
    spec = apply_magic_hour_policies(load_openapi_spec(spec_path))

    mcp = FastMCP.from_openapi(
        openapi_spec=spec,
        client=build_api_client(),
        name="magic-hour",
        route_maps=[
            RouteMap(methods=["POST"], pattern=r".*", mcp_type=MCPType.TOOL, mcp_tags={"write-operation"}),
            RouteMap(pattern=r".*", mcp_type=MCPType.TOOL),
        ],
        mcp_component_fn=customize_openapi_component,
    )

    register_custom_tools(mcp)
    return mcp


def register_custom_tools(mcp: FastMCP) -> None:
    @mcp.tool(name="ping", description="Check that the Magic Hour MCP server is reachable.")
    def ping() -> str:
        return "pong"

    @mcp.tool(
        name="wait_for_video_project",
        description=(
            "Poll a video project until it completes, errors, is canceled, or times out. "
            f"{SIGNED_DOWNLOAD_GUIDANCE}"
        ),
    )
    async def wait_for_video_project(id: str, poll_interval_seconds: float = 2.0, timeout_seconds: float = 300.0) -> ToolResult:
        return await _wait_for_project_result("video", id, poll_interval_seconds, timeout_seconds)

    @mcp.tool(
        name="wait_for_image_project",
        description=(
            "Poll an image project until it completes, errors, is canceled, or times out. Returns the final "
            "project JSON and, when complete, attempts to inline image downloads for Inspector or compatible "
            f"clients. {SIGNED_DOWNLOAD_GUIDANCE}"
        ),
    )
    async def wait_for_image_project(
        id: str,
        poll_interval_seconds: float = 2.0,
        timeout_seconds: float = 180.0,
        include_inline_downloads: bool = True,
        max_inline_downloads: int = 4,
        max_bytes_per_download: int = DEFAULT_MEDIA_FETCH_MAX_BYTES,
    ) -> ToolResult:
        return await _wait_for_project_result(
            "image",
            id,
            poll_interval_seconds,
            timeout_seconds,
            include_inline_downloads=include_inline_downloads,
            max_inline_downloads=max_inline_downloads,
            max_bytes_per_download=max_bytes_per_download,
        )

    @mcp.tool(
        name="wait_for_audio_project",
        description=(
            "Poll an audio project until it completes, errors, is canceled, or times out. Returns the final "
            "project JSON and, when complete, attempts to inline audio downloads for Inspector or compatible "
            f"clients. {SIGNED_DOWNLOAD_GUIDANCE}"
        ),
    )
    async def wait_for_audio_project(
        id: str,
        poll_interval_seconds: float = 2.0,
        timeout_seconds: float = 180.0,
        include_inline_downloads: bool = True,
        max_inline_downloads: int = 4,
        max_bytes_per_download: int = DEFAULT_MEDIA_FETCH_MAX_BYTES,
    ) -> ToolResult:
        return await _wait_for_project_result(
            "audio",
            id,
            poll_interval_seconds,
            timeout_seconds,
            include_inline_downloads=include_inline_downloads,
            max_inline_downloads=max_inline_downloads,
            max_bytes_per_download=max_bytes_per_download,
        )

    @mcp.tool(
        name="upload_file_to_presigned_url",
        description=(
            "Upload a local file from the MCP server's filesystem to a presigned `upload_url` returned by "
            "the upload-URL endpoint. Use this for local CLI testing when the server can read the file path; "
            "remote web-chat users still need a browser or backend upload bridge."
        ),
    )
    async def upload_file_to_presigned_url(upload_url: str, local_file_path: str, content_type: str | None = None) -> dict[str, Any]:
        path = Path(local_file_path).expanduser().resolve()
        headers = {"Content-Type": content_type} if content_type else None
        async with httpx.AsyncClient(timeout=API_TIMEOUT, follow_redirects=True) as client:
            with path.open("rb") as file_obj:
                response = await client.put(upload_url, content=file_obj, headers=headers)
            response.raise_for_status()

        return {"uploaded": True, "status_code": response.status_code, "local_file_path": str(path)}

    _register_media_fetch_tool(mcp, "image")
    _register_media_fetch_tool(mcp, "audio")


def _register_media_fetch_tool(mcp: FastMCP, media_type: Literal["image", "audio"]) -> None:
    tool_name = f"fetch_{media_type}_download"
    description = (
        f"Fetch a {media_type} `downloads[n].url` from a completed {media_type} project and return it as inline MCP "
        f"{media_type} content for Inspector or compatible clients. Pass the exact full signed URL from "
        "`downloads[n].url` without trimming query parameters; `expires_at` is separate metadata, not part of the URL."
    )

    @mcp.tool(name=tool_name, description=description)
    async def fetch_download(download_url: str, max_bytes: int = DEFAULT_MEDIA_FETCH_MAX_BYTES):
        data, mime_type = await _fetch_media_bytes(download_url, expected_prefix=f"{media_type}/", max_bytes=max_bytes)
        if media_type == "image":
            return _media_content("image", data, mime_type)
        return _media_content("audio", data, mime_type)


async def _wait_for_project(
    project_type: ProjectType,
    project_id: str,
    poll_interval_seconds: float,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    path = f"/v1/{project_type}-projects/{project_id}"

    async with build_api_client() as client:
        while True:
            response = await client.get(path, headers={"Authorization": current_authorization_header()})
            response.raise_for_status()
            project = response.json()
            status = project.get("status")

            if status in TERMINAL_PROJECT_STATUSES:
                return project

            if asyncio.get_running_loop().time() >= deadline:
                return {
                    "status": "timeout",
                    "message": f"Timed out waiting for {project_type} project {project_id}.",
                    "last_project": project,
                }

            await asyncio.sleep(max(poll_interval_seconds, 0.5))


async def _wait_for_project_result(
    project_type: ProjectType,
    project_id: str,
    poll_interval_seconds: float,
    timeout_seconds: float,
    *,
    include_inline_downloads: bool = False,
    max_inline_downloads: int = 0,
    max_bytes_per_download: int = DEFAULT_MEDIA_FETCH_MAX_BYTES,
) -> ToolResult:
    project = await _wait_for_project(project_type, project_id, poll_interval_seconds, timeout_seconds)
    return await _project_to_tool_result(
        project_type,
        project,
        include_inline_downloads=include_inline_downloads,
        max_inline_downloads=max_inline_downloads,
        max_bytes_per_download=max_bytes_per_download,
    )


async def _fetch_media_bytes(download_url: str, expected_prefix: str, max_bytes: int) -> tuple[bytes, str]:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be greater than 0.")

    async with httpx.AsyncClient(timeout=API_TIMEOUT, follow_redirects=True) as client:
        async with client.stream("GET", download_url) as response:
            response.raise_for_status()
            mime_type = _resolve_media_mime_type(
                download_url,
                response.headers.get("Content-Type"),
                expected_prefix,
            )

            media = bytearray()
            async for chunk in response.aiter_bytes():
                media.extend(chunk)
                if len(media) > max_bytes:
                    raise ValueError(
                        f"Downloaded media is too large for inline MCP content ({len(media)} bytes > {max_bytes} bytes)."
                    )

    return bytes(media), mime_type


async def _project_to_tool_result(
    project_type: ProjectType,
    project: dict[str, Any],
    *,
    include_inline_downloads: bool,
    max_inline_downloads: int,
    max_bytes_per_download: int,
) -> ToolResult:
    content: list[Any] = [
        TextContent(type="text", text=_project_status_text(project_type, project))
    ]

    download_urls = _project_download_urls(project)
    status = str(project.get("status", "unknown"))

    if status == "complete" and download_urls:
        content.append(
            TextContent(
                type="text",
                text=_project_download_guidance_text(project_type, project),
            )
        )

    if status == "complete" and _can_inline_media(project_type, include_inline_downloads, max_inline_downloads):
        media_project_type = project_type
        assert media_project_type in {"image", "audio"}
        for index, download_url in enumerate(download_urls[:max_inline_downloads], start=1):
            try:
                data, mime_type = await _fetch_media_bytes(
                    download_url,
                    expected_prefix=f"{media_project_type}/",
                    max_bytes=max_bytes_per_download,
                )
            except Exception as exc:
                content.append(
                    TextContent(
                        type="text",
                        text=f"Inline {project_type} download {index} could not be fetched: {exc}",
                    )
                )
                continue

            if media_project_type == "image":
                content.append(_media_content("image", data, mime_type))
            else:
                content.append(_media_content("audio", data, mime_type))

        remaining_downloads = len(download_urls) - max_inline_downloads
        if remaining_downloads > 0:
            content.append(
                TextContent(
                    type="text",
                    text=f"{remaining_downloads} additional {project_type} download(s) were not inlined.",
                )
            )
    elif status == "complete" and not download_urls:
        content.append(
            TextContent(
                type="text",
                text=f"The completed {project_type} project did not include any download URLs.",
            )
        )

    return ToolResult(content=content, structured_content=_project_structured_content_for_agent(project))


def _can_inline_media(project_type: ProjectType, include_inline_downloads: bool, max_inline_downloads: int) -> bool:
    return include_inline_downloads and max_inline_downloads > 0 and project_type in {"image", "audio"}


def _media_content(media_type: Literal["image", "audio"], data: bytes, mime_type: str) -> Any:
    if media_type == "image":
        return Image(data=data).to_image_content(mime_type=mime_type)
    return Audio(data=data).to_audio_content(mime_type=mime_type)


def _project_download_urls(project: dict[str, Any]) -> list[str]:
    downloads = project.get("downloads")
    if not isinstance(downloads, list):
        return []

    urls: list[str] = []
    for item in downloads:
        if isinstance(item, dict):
            url = item.get("url")
            if isinstance(url, str) and url:
                urls.append(url)
    return urls


def _project_structured_content_for_agent(project: dict[str, Any]) -> dict[str, Any]:
    structured_project = dict(project)
    downloads = project.get("downloads")
    if not isinstance(downloads, list):
        return structured_project

    exact_download_urls: list[str] = []
    download_expiration_metadata: list[dict[str, str]] = []
    normalized_downloads: list[dict[str, str]] = []

    for index, item in enumerate(downloads):
        if not isinstance(item, dict):
            continue

        url = item.get("url")
        expires_at = item.get("expires_at")

        if isinstance(url, str) and url:
            exact_download_urls.append(url)
            normalized_downloads.append({"url": url})

        if isinstance(expires_at, str) and expires_at:
            download_expiration_metadata.append(
                {
                    "download_index": str(index),
                    "expires_at": expires_at,
                    "note": "Metadata only. Do not append this value to the download URL.",
                }
            )

    structured_project["downloads"] = normalized_downloads
    structured_project["exact_download_urls"] = exact_download_urls
    structured_project["download_expiration_metadata"] = download_expiration_metadata
    return structured_project


def _project_status_text(project_type: str, project: dict[str, Any]) -> str:
    status = str(project.get("status", "unknown"))
    project_id = str(project.get("id", "unknown"))
    download_count = len(_project_download_urls(project))

    if status == "complete":
        return f"{project_type.title()} project {project_id} completed with {download_count} download(s)."
    if status == "error":
        error = project.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return f"{project_type.title()} project {project_id} failed: {message}"
        return f"{project_type.title()} project {project_id} failed."
    if status == "canceled":
        return f"{project_type.title()} project {project_id} was canceled."
    if status == "timeout":
        message = project.get("message")
        if isinstance(message, str) and message:
            return message
        return f"Timed out waiting for {project_type} project {project_id}."

    return f"{project_type.title()} project {project_id} is {status}."


def _project_download_guidance_text(
    project_type: str,
    project: dict[str, Any],
) -> str:
    downloads = project.get("downloads")
    lines = [
        f"IMPORTANT: Use only `downloads[n].url` as the exact full signed {project_type} download URL.",
        "Do not shorten the URL.",
        "Do not remove query parameters.",
        "Do not append `downloads[n].expires_at` to the URL.",
        "`expires_at` is metadata only and is never part of the clickable/downloadable URL.",
    ]

    if isinstance(downloads, list):
        for index, item in enumerate(downloads, start=1):
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            expires_at = item.get("expires_at")
            if isinstance(url, str) and url:
                lines.append(f"EXACT_DOWNLOAD_URL[{index - 1}] = {url}")
            if isinstance(expires_at, str) and expires_at:
                lines.append(
                    f"EXPIRES_AT[{index - 1}] = {expires_at} (metadata only; do not append to the URL)"
                )

    return "\n".join(lines)


def _resolve_media_mime_type(download_url: str, header_value: str | None, expected_prefix: str) -> str:
    if header_value:
        mime_type = header_value.partition(";")[0].strip().lower()
        if mime_type.startswith(expected_prefix):
            return mime_type

    guessed_type, _ = mimetypes.guess_type(urlparse(download_url).path)
    if guessed_type and guessed_type.startswith(expected_prefix):
        return guessed_type

    if expected_prefix == "image/":
        return "image/png"
    if expected_prefix == "audio/":
        return "audio/wav"

    return "application/octet-stream"


mcp = create_mcp()

middleware = [
    Middleware(BearerPassthroughMiddleware),
    Middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["mcp-protocol-version", "mcp-session-id", "Authorization", "Content-Type"],
        expose_headers=["mcp-session-id"],
    ),
]

# Path "/" preserves the existing repo convention: standalone dev runs at root,
# and a host app can mount this ASGI app at "/mcp" without producing "/mcp/mcp".
app = mcp.http_app(path="/", middleware=middleware)
lifespan = app.lifespan
