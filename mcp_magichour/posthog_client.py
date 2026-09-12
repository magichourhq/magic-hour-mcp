"""Process-wide PostHog client for server-side analytics."""

import atexit
import logging
import os
from collections.abc import Mapping
from typing import Literal

from fastmcp import FastMCP
from posthog import Posthog
from posthog.mcp import MCPAnalyticsOptions, McpAnalytics, instrument
from starlette.types import ASGIApp, Receive, Scope, Send


OAuthCodeEvent = Literal[
    "oauth_authorization_code_issued",
    "oauth_authorization_code_lookup_missed",
    "oauth_connection_completed",
]
AnalyticsEvent = Literal[
    "media_project_resolved",
    "media_inline_download_failed",
    "mcp_app_event",
    "oauth_request_failed",
    "oauth_authorization_viewed",
    "mcp_authentication_challenged",
    OAuthCodeEvent,
]
ProjectType = Literal["video", "image", "audio"]
POSTHOG_TOKEN_PLACEHOLDER = "phc_your_project_token_here"
DEFAULT_POSTHOG_HOST = "https://us.i.posthog.com"
logger = logging.getLogger(__name__)


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

    def _capture(
        self, event: AnalyticsEvent, properties: Mapping[str, object] | None = None
    ) -> None:
        if self._client is not None:
            self._client.capture(
                event, properties=dict(properties) if properties else None
            )

    def capture_oauth_code_event(
        self,
        event: OAuthCodeEvent,
        *,
        authorization_code_hash: str,
        code_store_id: str,
        code_ttl_seconds: int,
        occurred_at: float,
    ) -> None:
        self._capture(
            event,
            {
                "authorization_code_hash": authorization_code_hash,
                "code_store_id": code_store_id,
                "code_ttl_seconds": code_ttl_seconds,
                "occurred_at": occurred_at,
            },
        )

    def capture_media_project_resolved(
        self, *, project_type: ProjectType, status: str, download_count: int
    ) -> None:
        self._capture(
            "media_project_resolved",
            {"project_type": project_type, "status": status, "download_count": download_count},
        )

    def capture_media_inline_download_failed(
        self, *, project_type: ProjectType, error_type: str, http_status: int | None
    ) -> None:
        self._capture(
            "media_inline_download_failed",
            {"project_type": project_type, "error_type": error_type, "http_status": http_status},
        )

    def capture_app_event(self, properties: Mapping[str, object]) -> None:
        self._capture("mcp_app_event", properties)

    def capture_oauth_authorization_viewed(self) -> None:
        self._capture("oauth_authorization_viewed")

    def capture_mcp_authentication_challenged(
        self, *, reason: Literal["missing_bearer", "malformed_bearer"]
    ) -> None:
        self._capture("mcp_authentication_challenged", {"reason": reason})

    def capture_oauth_failure(
        self, *, stage: Literal["authorize", "token", "register"],
        reason: str,
        http_status: int,
    ) -> None:
        self._capture(
            "oauth_request_failed",
            {"stage": stage, "reason": reason, "http_status": http_status},
        )

    def capture_exception(self, exception: BaseException) -> None:
        if self._client is not None:
            self._client.capture_exception(exception)

    def instrument_mcp(self, server: FastMCP) -> None:
        if self._client is not None:
            self._mcp = instrument(
                server,
                self._client,
                MCPAnalyticsOptions(logger=logger.info),
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
