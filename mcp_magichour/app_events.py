"""Bounded, anonymous telemetry for the sandboxed project-result app."""

import json
import re

from starlette.background import BackgroundTask
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import request_response

from .posthog_client import analytics


MAX_EVENT_BYTES = 256
UI_ERRORS = {
    "bridge_connection", "bridge_transport",
    "result_missing", "result_missing_download", "result_unsafe_download",
    "media_load", "media_network", "media_decode", "media_unsupported",
    "download_rejected", "download_failed", "fullscreen_rejected", "fullscreen_failed",
    "runtime_error", "unhandled_rejection", "render_error",
}
RESULT_EVENTS = {"preview_loaded", "playback_started", "download_requested"}

async def capture_app_event(request: Request) -> Response:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_EVENT_BYTES:
            return Response(status_code=413)
        body.extend(chunk)
    try:
        properties = json.loads(body)
    except (ValueError, RecursionError):
        return Response(status_code=400)

    if not isinstance(properties, dict):
        return Response(status_code=400)

    if properties.keys() == {"error"} and isinstance(properties["error"], str) and properties["error"] in UI_ERRORS:
        event = "mcp_app_error"
    elif (
        properties.keys() == {"event", "project_id", "project_type", "output_id"}
        and isinstance(properties["event"], str)
        and properties["event"] in RESULT_EVENTS
        and isinstance(properties["project_id"], str)
        and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", properties["project_id"])
        and isinstance(properties["project_type"], str)
        and properties["project_type"] in {"image", "video", "audio"}
        and isinstance(properties["output_id"], str)
        and re.fullmatch(r"[0-9a-f]{64}", properties["output_id"])
    ):
        event = "mcp_app_result"
    else:
        return Response(status_code=400)

    return Response(
        status_code=202,
        headers={"Cache-Control": "no-store"},
        background=BackgroundTask(analytics.capture, event, properties),
    )


# MCP hosts can give the iframe an opaque ("null") origin. No credentials or
# identifying headers are forwarded to analytics. Result events contain only a
# project ID, media type and hashed output path; they are anonymous UI reports,
# not authenticated proof of account ownership or a completed download.
app_events = CORSMiddleware(
    request_response(capture_app_event),
    allow_origins=["*"],
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)
