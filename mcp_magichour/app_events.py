"""Bounded, anonymous telemetry for the sandboxed project-result app."""

import json

from starlette.background import BackgroundTask
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import request_response

from .posthog_client import analytics


MAX_EVENT_BYTES = 4096
EVENT_VALUES = {
    "stage": {
        "boot", "runtime", "render", "bridge", "tool_result", "media_preview",
        "download", "fullscreen",
    },
    "outcome": {"started", "succeeded", "failed", "cancelled", "requested"},
    "reason": {
        "uncaught_error", "unhandled_rejection", "connect_failed",
        "transport_error", "tool_error", "missing_result", "project_failed",
        "missing_download", "unsafe_download", "load_failed", "host_rejected",
        "request_failed", "media_aborted", "media_network",
        "media_decode", "media_unsupported",
    },
    "media_type": {"image", "video", "audio", "media"},
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

    if not isinstance(properties, dict) or not {"stage", "outcome"} <= properties.keys():
        return Response(status_code=400)
    for key, value in properties.items():
        if key == "output_count":
            if type(value) is not int or not 0 <= value <= 1000:
                return Response(status_code=400)
        elif (
            key not in EVENT_VALUES
            or not isinstance(value, str)
            or value not in EVENT_VALUES[key]
        ):
            return Response(status_code=400)

    return Response(
        status_code=202,
        headers={"Cache-Control": "no-store"},
        background=BackgroundTask(analytics.capture_app_event, properties),
    )


# MCP hosts can give the iframe an opaque ("null") origin. No credentials or
# identifying headers are forwarded to analytics.
app_events = CORSMiddleware(
    request_response(capture_app_event),
    allow_origins=["*"],
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)
