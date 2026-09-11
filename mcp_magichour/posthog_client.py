"""Process-wide PostHog client for server-side analytics."""

import atexit
import os
from collections.abc import Mapping
from typing import Literal

from posthog import Posthog


AnalyticsEvent = Literal[
    "mcp_tool_completed",
    "media_project_resolved",
    "oauth_connection_completed",
]
ProjectType = Literal["video", "image", "audio"]
ToolOutcome = Literal["success", "error"]


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

    if not project_token:
        if _is_debug():
            raise _missing_configuration_error("POSTHOG_PROJECT_TOKEN")
        return None

    if not host:
        if _is_debug():
            raise _missing_configuration_error("POSTHOG_HOST")
        return None

    return Posthog(
        project_token,
        host=host,
        enable_exception_autocapture=True,
    )


class Analytics:
    """Typed, no-op-safe analytics facade."""

    def __init__(self, client: Posthog | None) -> None:
        self._client = client

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

    def capture_mcp_tool_completed(
        self, *, tool_name: str, outcome: ToolOutcome
    ) -> None:
        self._capture(
            "mcp_tool_completed", {"tool_name": tool_name, "outcome": outcome}
        )

    def capture_exception(self, exception: BaseException) -> None:
        if self._client is not None:
            self._client.capture_exception(exception)

    def shutdown(self) -> None:
        if self._client is not None:
            self._client.shutdown()


analytics = Analytics(initialize_posthog())

atexit.register(analytics.shutdown)
