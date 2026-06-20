from magic_hour.types.models import V1AudioProjectsGetResponse
from mcp.server.fastmcp import Context

from ..client import get_client
from ..instance import mcp


@mcp.tool()
async def get_audio_project(id: str, ctx: Context) -> V1AudioProjectsGetResponse:
    """Check the status of an audio project. `downloads` is populated once status is complete."""
    async with get_client(ctx) as client:
        return await client.v1.audio_projects.get(id=id)


@mcp.tool()
async def delete_audio_project(id: str, ctx: Context) -> str:
    """Permanently delete a rendered audio project. Not reversible."""
    async with get_client(ctx) as client:
        await client.v1.audio_projects.delete(id=id)
    return f"Deleted audio project {id}"
