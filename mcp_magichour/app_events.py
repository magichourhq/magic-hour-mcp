"""Bounded, anonymous telemetry for the sandboxed project-result app."""

import json

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

    if (
        not isinstance(properties, dict)
        or properties.keys() != {"error"}
        or not isinstance(properties["error"], str)
        or properties["error"] not in UI_ERRORS
    ):
        return Response(status_code=400)

    return Response(
        status_code=202,
        headers={"Cache-Control": "no-store"},
        background=BackgroundTask(analytics.capture, "mcp_app_error", properties),
    )


# MCP hosts can give the iframe an opaque ("null") origin. No credentials or
# identifying headers are forwarded to analytics.
app_events = CORSMiddleware(
    request_response(capture_app_event),
    allow_origins=["*"],
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)
