from magic_hour.types.models import V1VideoProjectsGetResponse
from mcp.server.fastmcp import Context

from ..client import get_client
from ..instance import mcp


@mcp.tool()
async def get_video_project(id: str, ctx: Context) -> V1VideoProjectsGetResponse:
    """Check the status of a video project. `downloads` is populated once status is complete."""
    async with get_client(ctx) as client:
        return await client.v1.video_projects.get(id=id)


@mcp.tool()
async def delete_video_project(id: str, ctx: Context) -> str:
    """Permanently delete a rendered video project. Not reversible."""
    async with get_client(ctx) as client:
        await client.v1.video_projects.delete(id=id)
    return f"Deleted video project {id}"
