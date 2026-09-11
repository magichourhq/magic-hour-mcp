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


AnalyticsEvent = Literal[
    "media_project_resolved",
    "oauth_connection_completed",
]
ProjectType = Literal["video", "image", "audio"]
POSTHOG_TOKEN_PLACEHOLDER = "phc_your_project_token_here"
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
    host = os.getenv("POSTHOG_HOST")

    if not project_token or project_token == POSTHOG_TOKEN_PLACEHOLDER:
        if _is_debug():
            raise _missing_configuration_error("POSTHOG_PROJECT_TOKEN")
        logger.warning(
            "PostHog analytics disabled: POSTHOG_PROJECT_TOKEN is missing or unconfigured"
        )
        return None

    if not host:
        if _is_debug():
            raise _missing_configuration_error("POSTHOG_HOST")
        logger.warning("PostHog analytics disabled: POSTHOG_HOST is missing")
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

    def capture_oauth_connection_completed(self) -> None:
        self._capture("oauth_connection_completed")

    def capture_media_project_resolved(
        self, *, project_type: ProjectType, status: str
    ) -> None:
        self._capture(
            "media_project_resolved", {"project_type": project_type, "status": status}
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
