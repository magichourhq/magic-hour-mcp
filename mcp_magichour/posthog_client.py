"""Process-wide PostHog client for server-side analytics."""

import atexit
import hashlib
import logging
import os
from collections.abc import Mapping
from typing import Literal

from fastmcp import FastMCP
from posthog import Posthog
from posthog.mcp import (
    MCPAnalyticsOptions,
    McpAnalytics,
    UserIdentity,
    get_request_headers,
    instrument,
)
from starlette.types import ASGIApp, Receive, Scope, Send


AnalyticsEvent = Literal[
    "media_project_resolved",
    "media_inline_download_failed",
    "mcp_app_error",
]
ProjectType = Literal["video", "image", "audio"]
POSTHOG_TOKEN_PLACEHOLDER = "phc_your_project_token_here"
DEFAULT_POSTHOG_HOST = "https://us.i.posthog.com"
logger = logging.getLogger(__name__)


def api_key_distinct_id(token: str) -> str:
    fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"mcp_api_key_{fingerprint}"


def _identify_request(_: object, extra: object) -> UserIdentity | None:
    headers = get_request_headers(extra) or {}
    scheme, _, token = headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return UserIdentity(distinct_id=api_key_distinct_id(token.strip()))


def _is_debug() -> bool:
    return os.getenv("DEBUG", "").lower() == "true" or os.getenv(
        "ENVIRONMENT", ""
    ).lower() in {
        "development",
        "dev",
    }


def _missing_configuration_error(variable: str) -> RuntimeError:
    return RuntimeError(
        f"{variable} variable required by PostHog is missing or un-configured, this causes events to be silently missed. "
        f"This error stops appearing once {variable} is configured"
    )


def initialize_posthog() -> Posthog | None:
    """Create the shared PostHog client when its environment is configured."""
    project_token = os.getenv("POSTHOG_PROJECT_TOKEN")
    host = os.getenv("POSTHOG_HOST") or DEFAULT_POSTHOG_HOST

    if not project_token or project_token == POSTHOG_TOKEN_PLACEHOLDER:
        if _is_debug():
            raise _missing_configuration_error("POSTHOG_PROJECT_TOKEN")
        logger.warning(
            "PostHog analytics disabled: POSTHOG_PROJECT_TOKEN is missing or unconfigured"
        )
        return None

    return Posthog(
        project_token,
        host=host,
        enable_exception_autocapture=True,
        sync_mode=True,
    )


class Analytics:
    """Typed, no-op-safe analytics facade."""

    def __init__(self, client: Posthog | None) -> None:
        self._client = client
        self._mcp: McpAnalytics | None = None

    def capture(
        self, event: AnalyticsEvent, properties: Mapping[str, object] | None = None
    ) -> None:
        if self._client is not None:
            self._client.capture(
                event, properties=dict(properties) if properties else None
            )

    async def capture_media_project_resolved(
        self, *, distinct_id: str, project_type: ProjectType, status: str, download_count: int
    ) -> None:
        await self.capture_mcp(
            "media_project_resolved",
            {"project_type": project_type, "status": status, "download_count": download_count},
            distinct_id=distinct_id,
        )

    def capture_exception(self, exception: BaseException) -> None:
        if self._client is not None:
            self._client.capture_exception(exception)

    def instrument_mcp(self, server: FastMCP) -> None:
        if self._client is not None:
            self._mcp = instrument(
                server,
                self._client,
                MCPAnalyticsOptions(
                    context=False,
                    identify=_identify_request,
                    logger=logger.info,
                ),
            )

    async def capture_mcp(
        self, event: AnalyticsEvent, properties: Mapping[str, object] | None = None, *, distinct_id: str
    ) -> None:
        if self._client is not None:
            self._client.capture(
                event, distinct_id=distinct_id, properties=dict(properties) if properties else None
            )

    async def flush_mcp(self) -> None:
        if self._mcp is not None:
            await self._mcp.flush()

    def shutdown(self) -> None:
        if self._client is not None:
            self._client.shutdown()


analytics = Analytics(initialize_posthog())


class PostHogFlushMiddleware:
    """Finish MCP capture work before a serverless request is suspended."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await self.app(scope, receive, send)
        finally:
            await analytics.flush_mcp()


atexit.register(analytics.shutdown)
