"""Read-only workflow selection; no account access or media generation."""

from copy import deepcopy
from typing import Literal

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

Workflow = Literal[
    "video-face-swap",
    "image-editor",
    "image-to-video",
    "video-generator",
    "lip-sync",
    "ugc-ads",
    "talking-photo",
    "media-apis",
]

DOCS = "https://docs.magichour.ai"
SITE = "https://magichour.ai"

# Product and API catalogs differ. Keep web-only workflows explicitly separate.
WORKFLOWS = {
    "video-face-swap": {
        "title": "Replace a face in an existing video",
        "input": "An existing video and an approved replacement face image.",
        "product_url": f"{SITE}/products/face-swap",
        "api_docs_url": f"{DOCS}/api-reference/video-projects/face-swap-video",
        "creation_tool": "face_swap_create_video",
        "review": "Check identity, occlusions and temporal stability. Obtain permission for the likenesses used.",
    },
    "image-editor": {
        "title": "Edit a product or brand image using instructions",
        "input": "Source images and a description of the requested edit.",
        "product_url": f"{SITE}/products/ai-image-editor",
        "api_docs_url": f"{DOCS}/api-reference/image-projects/ai-image-editor",
        "creation_tool": "ai_image_editor_create_image",
        "review": "Check unchanged regions, packaging, logos and text. Model and resolution eligibility depend on the account.",
    },
    "image-to-video": {
        "title": "Animate a product photo or reference image",
        "input": "A reference image and a motion instruction.",
        "product_url": f"{SITE}/products/image-to-video",
        "api_docs_url": f"{DOCS}/api-reference/video-projects/image-to-video",
        "creation_tool": "image_to_video_create_video",
        "review": "Check product shape, labels and motion. Use deterministic overlays for exact prices and legal copy.",
    },
    "video-generator": {
        "title": "Generate a new video from a text prompt",
        "input": "A description of a new scene; use image-to-video when a specific reference image matters.",
        "product_url": f"{SITE}/products/ai-video-generator",
        "api_docs_url": f"{DOCS}/api-reference/video-projects/text-to-video",
        "creation_tool": "text_to_video_create_video",
        "review": "Check prompt adherence, continuity, audio and output settings for the selected model.",
    },
    "lip-sync": {
        "title": "Sync existing footage to a replacement voiceover",
        "input": "An existing video and replacement audio for the selected segment.",
        "product_url": f"{SITE}/products/lip-sync",
        "api_docs_url": f"{DOCS}/api-reference/video-projects/lip-sync",
        "creation_tool": "lip_sync_create_video",
        "review": "Check mouth timing, pauses, face identity and cuts. Translation and voice production are separate steps.",
    },
    "ugc-ads": {
        "title": "Create a presenter-style product ad",
        "input": "Product information, permitted assets and an approved ad script.",
        "product_url": f"{SITE}/products/ai-ugc-ad-generator",
        "api_docs_url": None,
        "creation_tool": None,
        "review": "Use the web application for the UGC workflow. This MCP does not expose a dedicated UGC-ad endpoint. Review claims and do not present generated testimonials as real customer experience.",
    },
    "talking-photo": {
        "title": "Make a portrait speak",
        "input": "A portrait and audio or text supported by the current tool schema.",
        "product_url": f"{SITE}/products/ai-talking-photo",
        "api_docs_url": f"{DOCS}/api-reference/video-projects/ai-talking-photo",
        "creation_tool": "ai_talking_photo_create_talking_photo",
        "review": "Use talking photo for a still portrait; use lip sync for existing footage. Review facial motion and speech timing.",
    },
    "media-apis": {
        "title": "Integrate image and video generation into an application",
        "input": "The application's required media inputs, outputs, quality criteria and budget.",
        "product_url": f"{SITE}/api",
        "api_docs_url": f"{DOCS}/get-started/quick-start",
        "creation_tool": None,
        "review": "Select a task-specific endpoint. Compare cost per accepted output on matched inputs; do not infer production quality from mock responses.",
    },
}


def workflow_guide(workflow: str) -> dict:
    if workflow not in WORKFLOWS:
        raise ValueError(
            f"Unknown workflow: {workflow}. Choose one of: {', '.join(WORKFLOWS)}"
        )
    return {
        "workflow": workflow,
        **deepcopy(WORKFLOWS[workflow]),
        "models_and_costs_url": f"{DOCS}/api-reference/models",
        "evaluation_url": f"{DOCS}/integration/evaluating-media-apis",
        "starter_recipes_url": f"{DOCS}/get-started/starter-recipes",
        "guidance": [
            "This guide reads static documentation only; it spends no generation credits and does not verify account access.",
            "Inspect the current creation tool schema and check model eligibility and estimated credits before generating.",
            "Creation is asynchronous. Save the project ID, wait with the matching wait_for_*_project tool and preserve signed download URLs exactly.",
            "Commercial use requires a paid subscription, credit-pack purchase or agreed paid usage-based arrangement, plus rights to the assets used. Check current terms at https://magichour.ai/terms-of-service.",
        ],
    }


def register_workflow_guidance(mcp: FastMCP) -> None:
    @mcp.tool(
        name="workflow_guide_retrieve",
        title="Choose a media workflow",
        description=(
            "Read Magic Hour workflow guidance for face swap, image editing, image-to-video, "
            "video generation, lip sync, UGC ads, talking photos or API integration. "
            "Returns product and documentation URLs, exact tool names and review criteria; "
            "does not generate media, spend credits or check account access."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, openWorldHint=False
        ),
    )
    def workflow_guide_retrieve(workflow: Workflow) -> dict:
        return workflow_guide(workflow)

    @mcp.resource(
        "magic-hour://workflows/index",
        name="Magic Hour workflow index",
        description="Discover task-specific workflow guides and their resource URIs.",
        mime_type="application/json",
    )
    def workflow_index() -> dict:
        return {
            "workflows": [
                {
                    "workflow": key,
                    "title": guide["title"],
                    "uri": f"magic-hour://workflows/{key}",
                }
                for key, guide in WORKFLOWS.items()
            ]
        }

    @mcp.resource(
        "magic-hour://workflows/{workflow}",
        name="Magic Hour workflow guide",
        description="Read workflow inputs, product and API docs URLs, tool names and review criteria.",
        mime_type="application/json",
    )
    def workflow_resource(workflow: str) -> dict:
        return workflow_guide(workflow)
