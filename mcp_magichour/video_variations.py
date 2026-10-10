"""Budgeted orchestration over the existing Image-to-Video and project APIs."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from collections.abc import Callable
from typing import Annotated, Any, Literal
from uuid import uuid4

import httpx
from fastmcp import FastMCP
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent, ToolAnnotations
from pydantic import Field

from .oauth_compat import oauth_authentication_challenge
from .openapi_auth import current_authorization_header
from .posthog_client import analytics, api_key_distinct_id

AspectRatio = Literal["9:16", "16:9", "1:1", "input"]
logger = logging.getLogger(__name__)
WORKFLOW_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["status", "recovery"],
    "properties": {
        "status": {"type": "string", "enum": ["planned", "submitted", "pending", "partial", "complete", "error"]},
        "workflow_id": {"type": "string"},
        "plan_token": {"type": "string"},
        "estimate_credits": {"type": "integer"},
        "maximum_credits": {"type": "integer"},
        "budget_credits": {"type": "integer"},
        "credits_charged": {"type": "number"},
        "billing_final": {"type": "boolean"},
        "retry_after_seconds": {"type": "number"},
        "code": {"type": "string"},
        "message": {"type": "string"},
        "recovery": {"type": "string"},
        "projects": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "index", "prompt", "model", "resolution", "duration_seconds", "maximum_credits"],
                "properties": {
                    "id": {"type": "string"}, "index": {"type": "integer"},
                    "prompt": {"type": "string"}, "model": {"type": "string"},
                    "resolution": {"type": "string"}, "duration_seconds": {"type": "number"},
                    "maximum_credits": {"type": "integer"}, "status": {"type": "string"},
                    "credits_charged": {"type": "number"},
                    "exact_download_urls": {"type": "array", "items": {"type": "string"}},
                    "download_expiration_metadata": {"type": "array", "items": {"type": "object"}},
                    "width": {"type": ["number", "null"]}, "height": {"type": ["number", "null"]},
                    "error": {},
                },
            },
        },
    },
}
RESUME_GUIDANCE = (
    "Save this plan_token. Poll video_variations_status with the same token. "
    "After a lost response, retry execute_video_variations with the same token and approval; "
    "existing project IDs are replayed without a new charge. Never create a new plan to retry. "
    "Failed jobs are not automatically regenerated; a new generation needs a new quote and approval."
)


class WorkflowError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _signing_secret() -> bytes:
    secret = os.environ.get("MCP_WORKFLOW_SIGNING_SECRET", "").encode()
    if len(secret) < 32:
        raise WorkflowError("workflow_not_configured", "Budgeted workflows require a server-only MCP_WORKFLOW_SIGNING_SECRET of at least 32 bytes.")
    return secret


def _tenant() -> str:
    return api_key_distinct_id(current_authorization_header().partition(" ")[2])


def _encode_plan(plan: dict[str, Any]) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({**plan, "tenant": _tenant()}, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(_signing_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _decode_plan(plan_token: str) -> dict[str, Any]:
    secret = _signing_secret()
    try:
        payload, signature = plan_token.split(".")
        expected = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError("signature")
        plan = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if plan["version"] != 1 or not hmac.compare_digest(plan["tenant"], _tenant()) or not 1 <= len(plan["requests"]) <= 6:
            raise ValueError("version")
    except (ValueError, KeyError, TypeError):
        raise WorkflowError("invalid_plan", "Plan is invalid or belongs to another API key. Use the original account and token.") from None
    if time.time() > plan["expires_at"]:
        raise WorkflowError("expired_plan", "Plan expired after 24 hours. Read existing project IDs with the project tools; do not resubmit blindly.")
    return plan


def _result(data: dict[str, Any], *, error: bool = False) -> ToolResult:
    return ToolResult(content=[TextContent(type="text", text=json.dumps(data))], structured_content=data, is_error=error)


def _error_result(error: Exception, **context: Any) -> ToolResult:
    code = "upstream_unavailable"
    message = "API response unavailable. Submission outcome may be unknown."
    retry_after = 5
    if isinstance(error, WorkflowError):
        code, message = error.code, str(error)
    elif isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        code = f"api_{status}"
        # Do not return arbitrary upstream bodies that may contain signed URLs or credentials.
        message = f"Magic Hour API returned HTTP {status}."
        if status == 429:
            message += " Rate limit reached."
        if status in {400, 402, 422}:
            message += " Check input preparation, account entitlement, compatible model settings, and credit budget."
        if status in {401, 403}:
            message += " Reconnect the original account."
        try:
            detail = error.response.json().get("message")
            if isinstance(detail, str) and len(detail) <= 600 and not any(value in detail.lower() for value in ("http", "mhk_", "bearer")):
                message += f" {detail}"
        except (ValueError, AttributeError):
            pass
        try:
            retry_after = max(1, min(300, int(error.response.headers.get("Retry-After", "5"))))
        except ValueError:
            pass
    recovery = RESUME_GUIDANCE if context.get("workflow_id") else "Correct the input or account restriction, then request a quote. No generation was submitted by this call."
    if code in {"invalid_plan", "expired_plan"}:
        recovery = "Use the original account and saved project IDs to inspect existing jobs. Do not start a replacement generation until its spending is reconciled and approved."
    result = _result({**context, "status": "partial" if context.get("projects") else "error", "code": code,
                     "message": message, "retry_after_seconds": retry_after, "recovery": recovery}, error=True)
    if code == "api_401":
        result.meta = {"mcp/www_authenticate": [oauth_authentication_challenge()]}
    return result


async def _require_capability(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.get("/v1/account")
    response.raise_for_status()
    account = response.json()
    if account.get("capabilities", {}).get("image_to_video_budget_v1") is not True:
        # Older APIs silently discard unknown fields, including dry_run, and would charge.
        raise WorkflowError("unsupported_api", "This API deployment does not advertise budgeted Image-to-Video v1. No generation was submitted.")
    return account


async def _capture(event: str, properties: dict[str, Any]) -> None:
    try:
        await analytics.capture_mcp(event, properties,
            distinct_id=api_key_distinct_id(current_authorization_header().partition(" ")[2]))
    except Exception as error:
        logger.warning("Workflow analytics unavailable: %s", type(error).__name__)


def _summary(plan: dict[str, Any]) -> dict[str, Any]:
    return {key: plan[key] for key in ("workflow_id", "estimate_credits", "maximum_credits", "budget_credits")}


def register_video_variation_tools(mcp: FastMCP, api_client_factory: Callable[[], httpx.AsyncClient]) -> None:
    @mcp.tool(
        name="plan_video_variations", title="Plan Product Photo Video Variations",
        description=(
            "Start here for 'take this product photo and make three short vertical videos under my budget'. "
            "Validate account, source image, model, duration and resolution; quote distinct creative variations without charging. "
            "Budget is Magic Hour credits, not dollars. Example: uploaded product image, variations=3, aspect_ratio='9:16', "
            "duration_seconds=5, max_credits=1000. A requested ratio requires an uploaded source already in that ratio; "
            "crop/upload first using caller tools. This workflow does not invent cropping, branding, or guaranteed quality. "
            "Present the quote and prompts to the user before execute_video_variations."
        ),
        output_schema=WORKFLOW_OUTPUT_SCHEMA,
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True),
    )
    async def plan_video_variations(
        image_file_path: Annotated[str, Field(min_length=1, max_length=2048, description="Uploaded Magic Hour image file_path; HTTPS image URLs supported only with aspect_ratio='input'.")],
        creative_instructions: Annotated[str, Field(min_length=1, max_length=4000, description="Product identity, desired motion, lighting, composition and constraints.")],
        max_credits: Annotated[int, Field(ge=1, le=1000000, description="Maximum total credit spend for all variations, including any repeated submission of this plan.")],
        variations: Annotated[int, Field(ge=1, le=6)] = 3,
        aspect_ratio: AspectRatio = "9:16",
        duration_seconds: Annotated[int, Field(ge=1, le=60)] = 5,
        model: Annotated[str | None, Field(max_length=100, description="Explicit supported API model. Omit for account-compatible default; never silently replace explicit preferences.")] = None,
        resolution: Literal["360p", "480p", "720p", "1080p", "4k"] | None = None,
        variation_prompts: Annotated[list[Annotated[str, Field(min_length=1, max_length=4000)]] | None, Field(max_length=6, description="Optional distinct creative directions, exactly one per variation; each appended to creative_instructions.")] = None,
    ) -> ToolResult:
        try:
            _signing_secret()
            directions = variation_prompts or [
                "Slow push-in toward the product; soft studio lighting.",
                "Gentle orbit around the product; crisp directional lighting.",
                "Smooth upward camera reveal; warm natural lighting.",
                "Subtle lateral camera slide; dramatic rim lighting.",
                "Static camera with shifting light; minimalist backdrop.",
                "Slow pull-back revealing the setting; bright diffuse lighting.",
            ][:variations]
            if len(directions) != variations or len(set(directions)) != variations:
                raise WorkflowError("invalid_variations", "Provide exactly one distinct creative direction per variation.")
            per_clip_budget = max_credits // variations
            if per_clip_budget < 1:
                raise WorkflowError("budget_exceeded", "Budget cannot cover the requested number of variations.")
            workflow_id = f"mcp_video_{uuid4().hex}"
            requests, projects = [], []
            async with api_client_factory() as client:
                account = await _require_capability(client)
                for index, direction in enumerate(directions):
                    body = {"assets": {"image_file_path": image_file_path},
                            "style": {"prompt": f"{creative_instructions}\n{direction}"},
                            "end_seconds": duration_seconds, "aspect_ratio": aspect_ratio,
                            "idempotency_key": f"{workflow_id}:{index}", "max_credits": per_clip_budget,
                            "audio": False, "name": f"Product video {index + 1}"}
                    if model is not None:
                        body["model"] = model
                    if resolution is not None:
                        body["resolution"] = resolution
                    # The envelope omits legacy required top-level fields. Older API
                    # deployments reject it instead of stripping dry_run and charging.
                    candidates = [model] if model is not None else ["ltx-2.5", "default"]
                    for candidate in candidates:
                        response = await client.post("/v1/image-to-video", json={"budgeted_request": {**body, "model": candidate, "dry_run": True}})
                        if response.is_success or response.status_code not in {400, 402, 422}:
                            break
                    response.raise_for_status()
                    quote = response.json()
                    credits = quote.get("credits_estimated")
                    if (quote.get("dry_run") is not True or type(credits) is not int or credits < 1
                        or credits > per_clip_budget or quote.get("credits_maximum") != credits
                        or not isinstance(quote.get("project_id"), str)
                        or not isinstance(quote.get("model"), str) or not isinstance(quote.get("resolution"), str)):
                        raise WorkflowError("invalid_quote", "API did not return a valid bounded quote. Do not execute this workflow.")
                    if model is not None and model != "default" and quote["model"] != model:
                        raise WorkflowError("model_replaced", "API replaced the explicitly requested model. Review the supported model and plan again with user authorization.")
                    body.update(model=quote["model"], resolution=quote["resolution"], max_credits=credits)
                    requests.append(body)
                    projects.append({"id": quote["project_id"], "index": index,
                                     "prompt": body["style"]["prompt"], "model": quote["model"],
                                     "resolution": quote["resolution"], "duration_seconds": duration_seconds,
                                     "maximum_credits": credits})
            maximum = sum(project["maximum_credits"] for project in projects)
            if maximum > account["credits"]:
                raise WorkflowError("insufficient_credits", "Account balance cannot cover the quoted variations.")
            plan = {"version": 1, "workflow_id": workflow_id, "account_id": account["id"], "expires_at": time.time() + 86400,
                    "estimate_credits": maximum, "maximum_credits": maximum, "budget_credits": max_credits,
                    "requests": requests, "projects": projects}
            await _capture("video_workflow_planned", {"workflow_id": workflow_id, "account_id": plan["account_id"],
                "variations": variations, "maximum_credits": maximum})
            return _result({**_summary(plan), "status": "planned", "projects": projects,
                            "plan_token": _encode_plan(plan), "recovery": RESUME_GUIDANCE})
        except (WorkflowError, httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            return _error_result(error)

    @mcp.tool(
        name="execute_video_variations", title="Generate Approved Product Video Variations",
        description=(
            "Paid execution of a saved plan_video_variations quote. Call only after user authorizes that exact quote; "
            "approved_max_credits must equal its maximum_credits. Consumes existing account credits, never purchases credits. "
            "Returns async project IDs immediately. Reuse the identical plan_token after timeout/reconnect: "
            "API idempotency prevents duplicate jobs and charges. Poll video_variations_status; do not plan again to retry."
        ),
        output_schema=WORKFLOW_OUTPUT_SCHEMA,
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
    )
    async def execute_video_variations(
        plan_token: Annotated[str, Field(min_length=1, max_length=100000)],
        approved_max_credits: Annotated[int, Field(ge=1, description="Exact quoted maximum explicitly approved by the user; this is paid authorization.")],
    ) -> ToolResult:
        projects: list[dict[str, Any]] = []
        context: dict[str, Any] = {}
        try:
            plan = _decode_plan(plan_token)
            context = _summary(plan)
            if approved_max_credits != plan["maximum_credits"]:
                raise WorkflowError("approval_required", "Present this quote and obtain authorization for its exact maximum_credits before execution.")
            async with api_client_factory() as client:
                await _require_capability(client)
                for body, project in zip(plan["requests"], plan["projects"]):
                    response = await client.post("/v1/image-to-video", json={"budgeted_request": body})
                    response.raise_for_status()
                    submitted = response.json()
                    if submitted.get("id") != project["id"]:
                        raise WorkflowError("unexpected_project", "API returned an unexpected project ID. Stop submission; inspect account projects before further execution.")
                    projects.append({**project, "status": "submitted", "credits_charged": submitted["credits_charged"]})
            return _result({**context, "status": "submitted", "projects": projects,
                            "retry_after_seconds": 5, "recovery": RESUME_GUIDANCE})
        except (WorkflowError, httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            return _error_result(error, **context, projects=projects)

    @mcp.tool(
        name="video_variations_status", title="Check Product Video Variations",
        description=(
            "Read all jobs from a saved plan_token, including after reconnect. Returns completed exact signed video URLs, "
            "per-variation prompts/model/resolution/duration, observed dimensions, actual reported credit charges and errors. "
            "No new generation or retry charge. Pending jobs: poll again after retry_after_seconds. Failed jobs: show failure, "
            "check refunded credits; new creative retries require a new quote and user authorization. URLs expire; poll again to refresh."
        ),
        output_schema=WORKFLOW_OUTPUT_SCHEMA,
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    )
    async def video_variations_status(plan_token: Annotated[str, Field(min_length=1, max_length=100000)]) -> ToolResult:
        projects: list[dict[str, Any]] = []
        context: dict[str, Any] = {}
        try:
            plan = _decode_plan(plan_token)
            context = _summary(plan)
            async with api_client_factory() as client:
                for project in plan["projects"]:
                    response = await client.get(f"/v1/video-projects/{project['id']}")
                    if response.status_code == 404:
                        projects.append({**project, "status": "not_submitted", "exact_download_urls": []})
                        continue
                    response.raise_for_status()
                    result = response.json()
                    downloads = result.get("downloads") or []
                    urls = [item["url"] for item in downloads if isinstance(item.get("url"), str)]
                    projects.append({**project, "status": result["status"], "credits_charged": result["credits_charged"],
                                     "width": result.get("width"), "height": result.get("height"),
                                     "exact_download_urls": urls if result["status"] == "complete" else [],
                                     "download_expiration_metadata": [{"download_index": i, "expires_at": item["expires_at"]}
                                         for i, item in enumerate(downloads) if item.get("expires_at")],
                                     "error": result.get("error")})
            complete = all(p["status"] == "complete" and p["exact_download_urls"] for p in projects)
            terminal = all(p["status"] in {"complete", "error", "canceled"} for p in projects)
            failed = any(p["status"] in {"error", "canceled"} or (p["status"] == "complete" and not p["exact_download_urls"]) for p in projects)
            status = "complete" if complete else "partial" if failed else "pending"
            await _capture("video_workflow_observed", {"workflow_id": plan["workflow_id"], "account_id": plan["account_id"],
                "status": status, "billing_final": terminal, "credits_charged": sum(p.get("credits_charged", 0) for p in projects)})
            return _result({**context, "status": status, "projects": projects,
                            "credits_charged": sum(p.get("credits_charged", 0) for p in projects),
                            "billing_final": terminal, "retry_after_seconds": 5, "recovery": RESUME_GUIDANCE}, error=failed)
        except (WorkflowError, httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            return _error_result(error, **context, projects=projects)
