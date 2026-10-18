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

WORKFLOWS = {
    "video-face-swap": {
        "title": "Replace a face in an existing video",
        "input": "An existing video and an approved replacement face image.",
        "product_url": f"{SITE}/products/face-swap",
        "api_docs_url": f"{DOCS}/api-reference/video-projects/face-swap-video",
        "creation_tool": "face_swap_create_video",
        "steps": [
            "Use the approved source face and target clip; upload local files with video_assets_generate_presigned_url and send their raw bytes to upload_url.",
            "For one selected person in a multi-person clip, use face_detection_detect_faces and face_detection_retrieve_details, then assets.face_swap_mode=individual-faces with assets.face_mappings; do not swap every face.",
            "Call face_swap_create_video for the requested start_seconds and end_seconds, then wait_for_video_project with the returned id.",
        ],
        "example_request": "Put my face into this short movie-style scene. Keep the other people unchanged and show me the finished clip.",
        "follow_up": "Reuse the accepted clip for subtitles or another edit; do not regenerate the scene merely to change its captions.",
        "review": "Check identity, occlusions and temporal stability. Obtain permission for the likenesses used.",
    },
    "image-editor": {
        "title": "Edit a portrait, scene or reference image",
        "input": "Source images and a description of the requested edit.",
        "product_url": f"{SITE}/products/ai-image-editor",
        "api_docs_url": f"{DOCS}/api-reference/image-projects/ai-image-editor",
        "creation_tool": "ai_image_editor_create_image",
        "steps": [
            "Use the supplied reference images. With no source image, use ai_image_generator_create_image instead of inventing image file paths.",
            "State what changes and what stays fixed: identity, pose, clothing, background, lighting and aspect ratio.",
            "Call ai_image_editor_create_image with supported account settings, then wait_for_image_project. Review the still before using it as a video reference.",
        ],
        "example_request": "Turn this portrait into a cinematic space-pilot scene. Preserve my face and keep the framing vertical for a short video.",
        "follow_up": "Animate the accepted still with image_to_video_create_video rather than recreating its identity from a text prompt.",
        "review": "Check identity, unchanged regions, anatomy and requested edits. Model and resolution eligibility depend on the account.",
    },
    "image-to-video": {
        "title": "Animate a portrait, scene or reference image",
        "input": "A reference image and a motion instruction.",
        "product_url": f"{SITE}/products/image-to-video",
        "api_docs_url": f"{DOCS}/api-reference/video-projects/image-to-video",
        "creation_tool": "image_to_video_create_video",
        "steps": [
            "Use the supplied still, or prepare a keyframe with Image Editor when identity or visual design must change first.",
            "Describe subject motion and one camera move; preserve identity, style, lighting and aspect ratio from the reference.",
            "Call image_to_video_create_video with a supported model, resolution and duration, then wait_for_video_project with the returned id.",
        ],
        "example_request": "Animate this portrait: the pilot turns toward a distant planet as the camera slowly pushes in. Keep the face and outfit consistent.",
        "follow_up": "Keep the accepted footage for captions, lip sync or later editing. For a new shot, reuse the same reference image.",
        "review": "Check identity, anatomy, motion and the first and last frames. Keep one clear action and camera move; add exact text in a separate editing step.",
    },
    "video-generator": {
        "title": "Generate a new video from a text prompt",
        "input": "A description of a new scene; use image-to-video when a specific reference image matters.",
        "product_url": f"{SITE}/products/ai-video-generator",
        "api_docs_url": f"{DOCS}/api-reference/video-projects/text-to-video",
        "creation_tool": "text_to_video_create_video",
        "steps": [
            "Use text-to-video for a new scene without a required likeness; use an image reference and image-to-video when identity must stay consistent.",
            "Specify the subject, one action, camera move, framing and mood. Request only audio and duration supported by the selected model.",
            "Call text_to_video_create_video, then wait_for_video_project with the returned id and review the actual result.",
        ],
        "example_request": "Create a short cinematic scene of a tiny astronaut discovering a glowing forest inside a glass bottle, with a slow orbiting camera.",
        "follow_up": "Edit or subtitle the accepted result instead of paying to regenerate unchanged footage.",
        "review": "Check prompt adherence, continuity, audio and output settings for the selected model.",
    },
    "lip-sync": {
        "title": "Sync existing footage to a replacement voiceover",
        "input": "An existing video and replacement audio for the selected segment.",
        "product_url": f"{SITE}/products/lip-sync",
        "api_docs_url": f"{DOCS}/api-reference/video-projects/lip-sync",
        "creation_tool": "lip_sync_create_video",
        "steps": [
            "Use existing footage and the approved replacement audio; upload local files before calling the creation tool.",
            "Choose a segment long enough for the complete sentence, including pauses; do not cut off the narration.",
            "Call lip_sync_create_video, then wait_for_video_project with the returned id and check speech timing before adding subtitles.",
        ],
        "example_request": "Make this character deliver my recorded line. Preserve the scene and let the entire sentence finish.",
        "follow_up": "Add captions to the accepted lip-synced clip rather than generating the performance again.",
        "review": "Check mouth timing, pauses, face identity and cuts. Translation and voice production are separate steps.",
    },
    "ugc-ads": {
        "title": "Create a scripted presenter video or ad",
        "input": "A prepared presenter/product image, an approved script and a supported duration.",
        "product_url": f"{SITE}/products/ai-ugc-ad-generator",
        "api_docs_url": f"{SITE}/openapi.json",
        "creation_tool": "ai_ugc_ad_create_video",
        "steps": [
            "Prepare the presenter image with Image Editor if needed; wait for the image result before creating the video.",
            "Pass assets.image_file_path, the approved script and end_seconds to ai_ugc_ad_create_video using supported account settings.",
            "Use wait_for_video_project with the returned id; review speech timing, identity and any spoken claims before reuse.",
        ],
        "example_request": "Use this prepared presenter image and my approved script to announce the new release. Let the complete sentence finish.",
        "follow_up": "Add subtitles to the accepted clip with auto_subtitle_generator_create_video; do not regenerate the presenter to change captions.",
        "review": "Compose the image separately with Image Editor if needed, then generate speech and video from the prepared image and script. Use permitted assets and review product fidelity, spoken claims and speech timing; do not present generated testimonials as real customer experience.",
    },
    "talking-photo": {
        "title": "Make a portrait speak",
        "input": "A portrait and an audio file, with start_seconds and end_seconds selecting the spoken segment. Text alone is not a valid input.",
        "product_url": f"{SITE}/products/ai-talking-photo",
        "api_docs_url": f"{DOCS}/api-reference/video-projects/ai-talking-photo",
        "creation_tool": "ai_talking_photo_create_talking_photo",
        "steps": [
            "Use a portrait and an existing approved recording. If the user has only text, obtain the audio first; this tool cannot turn text into speech.",
            "Optionally colorize an old photo with photo_colorizer_create_image and wait_for_image_project before animating it. Preserve the person's likeness.",
            "Call ai_talking_photo_create_talking_photo with both assets.image_file_path and assets.audio_file_path and a segment that includes the complete sentence, then wait_for_video_project.",
        ],
        "example_request": "Bring this old family portrait to life using my recording of the letter. Keep the face recognizable and include the full spoken passage.",
        "follow_up": "Use auto_subtitle_generator_create_video on the accepted clip when captions are requested; finish with wait_for_video_project.",
        "review": "Use talking photo for a still portrait; use lip sync for existing footage. Review facial motion and speech timing.",
    },
    "media-apis": {
        "title": "Integrate image and video generation into an application",
        "input": "The application's required media inputs, outputs, quality criteria and budget.",
        "product_url": f"{SITE}/api",
        "api_docs_url": f"{DOCS}/get-started/quick-start",
        "creation_tool": None,
        "steps": [
            "Choose the workflow that matches the required result, then inspect its current API schema and account eligibility.",
            "Authenticate, upload local inputs, create once, retain the project id and poll the matching project endpoint until terminal.",
            "Download the accepted output using the exact signed URL; retain the project id for recovery and reuse.",
        ],
        "example_request": "Help me add portrait-to-video generation to my app, including upload, completion polling, download and recovery without duplicate paid jobs.",
        "follow_up": "Measure real completion, usable output, retries and credits before expanding the workflow.",
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
            "A timeout or failed inline download is not permission to create a replacement job. Resume the same project id; after completion, retrieve its existing download URLs.",
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
            "Returns product and documentation URLs, exact tool names, ordered steps, "
            "example requests, follow-up edits and review criteria; "
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
        description="Read workflow inputs, tool steps, example requests, follow-up edits and review criteria.",
        mime_type="application/json",
    )
    def workflow_resource(workflow: str) -> dict:
        return workflow_guide(workflow)
