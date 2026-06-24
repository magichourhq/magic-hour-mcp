from typing import Any

import httpx
from make_api_request import ApiError


class AuthError(Exception):
    """Missing or malformed Authorization header."""


class MagicHourToolError(Exception):
    """Tool-friendly error surfaced back through MCP."""


def _stringify_body(body: Any) -> str | None:
    if isinstance(body, dict):
        for key in ("message", "error", "detail"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return str(body) if body else None
    if isinstance(body, list):
        return str(body) if body else None
    if isinstance(body, str) and body.strip():
        return body.strip()
    return None


def translate_api_error(error: ApiError) -> MagicHourToolError:
    """Turn Magic Hour SDK errors into short, actionable MCP tool errors."""
    message = _stringify_body(error.body)

    if error.status_code == 401:
        return MagicHourToolError(
            "Magic Hour rejected the API key (401 Unauthorized). "
            "Check that the Authorization header is 'Bearer <magic_hour_api_key>' and that the key is active."
        )

    if error.status_code == 402:
        detail = f" {message}" if message else ""
        return MagicHourToolError(
            "Magic Hour rejected the request with 402. "
            "This usually means the account lacks credits or plan access."
            f"{detail}"
        )

    if error.status_code == 429:
        detail = f" {message}" if message else ""
        return MagicHourToolError(
            "Magic Hour rate limited the request (429). Please wait a moment and retry."
            f"{detail}"
        )

    if error.status_code is not None:
        if message:
            return MagicHourToolError(f"Magic Hour API request failed with {error.status_code}: {message}")
        return MagicHourToolError(f"Magic Hour API request failed with {error.status_code}.")

    if message:
        return MagicHourToolError(f"Magic Hour API request failed: {message}")
    return MagicHourToolError("Magic Hour API request failed.")


def translate_http_error(error: httpx.HTTPError, *, during: str) -> MagicHourToolError:
    """Turn network-layer failures into short, actionable MCP tool errors."""
    if isinstance(error, httpx.TimeoutException):
        return MagicHourToolError(f"Timed out while {during}. Please retry.")

    if isinstance(error, httpx.HTTPStatusError):
        return MagicHourToolError(
            f"Failed while {during}: upstream file server returned {error.response.status_code}."
        )

    message = str(error).strip()
    if message:
        return MagicHourToolError(f"Network error while {during}: {message}")
    return MagicHourToolError(f"Network error while {during}.")
