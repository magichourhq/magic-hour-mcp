import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from magic_hour import AsyncClient
from magic_hour.environment import Environment
from mcp.server.fastmcp import Context

from .auth import get_api_key


def _environment() -> Environment:
    # Set MAGIC_HOUR_ENVIRONMENT=mock to hit the free mock server instead of production.
    if os.getenv("MAGIC_HOUR_ENVIRONMENT", "").lower() == "mock":
        return Environment.MOCK_SERVER
    return Environment.ENVIRONMENT


@asynccontextmanager
async def get_client(ctx: Context) -> AsyncIterator[AsyncClient]:
    """Build a Magic Hour client scoped to this one request's API key."""
    token = get_api_key(ctx)
    async with httpx.AsyncClient(timeout=60) as http_client:
        yield AsyncClient(token=token, httpx_client=http_client, environment=_environment())
