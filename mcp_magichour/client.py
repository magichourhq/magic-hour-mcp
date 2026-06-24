import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from magic_hour import AsyncClient
from magic_hour.environment import Environment
from make_api_request import ApiError
from mcp.server.fastmcp import Context

from .auth import get_api_key
from .errors import translate_api_error, translate_http_error

logger = logging.getLogger(__name__)

API_TIMEOUT = httpx.Timeout(60.0, connect=10.0, read=60.0, write=60.0, pool=10.0)
API_LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10)
API_RETRIES = 2


def _environment() -> Environment:
    # Set MAGIC_HOUR_ENVIRONMENT=mock to hit the free mock server instead of production.
    if os.getenv("MAGIC_HOUR_ENVIRONMENT", "").lower() == "mock":
        return Environment.MOCK_SERVER
    return Environment.ENVIRONMENT


def build_http_client(*, follow_redirects: bool = False) -> httpx.AsyncClient:
    """Shared outbound HTTP policy for Magic Hour API calls and result downloads."""
    return httpx.AsyncClient(
        timeout=API_TIMEOUT,
        limits=API_LIMITS,
        follow_redirects=follow_redirects,
        transport=httpx.AsyncHTTPTransport(retries=API_RETRIES),
    )


@asynccontextmanager
async def get_client(ctx: Context) -> AsyncIterator[AsyncClient]:
    """Build a Magic Hour client scoped to this one request's API key."""
    token = get_api_key(ctx)
    async with build_http_client() as http_client:
        try:
            yield AsyncClient(token=token, httpx_client=http_client, environment=_environment())
        except ApiError as error:
            logger.warning(
                "Magic Hour API request failed",
                extra={"status_code": error.status_code, "path": error.response.request.url.path},
            )
            raise translate_api_error(error) from error
        except httpx.HTTPError as error:
            logger.warning("Network error while calling the Magic Hour API: %s", error)
            raise translate_http_error(error, during="calling the Magic Hour API") from error
