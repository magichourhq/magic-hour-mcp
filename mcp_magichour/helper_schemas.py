"""Document structured helper results without changing their existing wire format."""

PROJECT_WAIT_OUTPUT_SCHEMA = {
    "type": "object",
    "description": "Existing project status and exact output links. A wait timeout is not a generation failure; resume with the same ID.",
    "properties": {
        "project_type": {"type": "string", "enum": ["image", "video", "audio"], "description": "Kind of project being retrieved."},
        "id": {"type": "string", "description": "Existing project identifier, retained for subsequent status checks."},
        "status": {"type": "string", "description": "Project status such as complete, error or canceled; timeout means only this wait call ended."},
        "downloads": {
            "type": "array", "description": "Available output files with exact signed URLs. May be absent until completion.",
            "items": {"type": "object", "properties": {"url": {"type": "string", "description": "Use this signed download URL unchanged, including all query parameters."}}, "required": ["url"]},
        },
        "exact_download_urls": {"type": "array", "description": "Convenience list of the same unmodified signed output URLs.", "items": {"type": "string"}},
        "download_expiration_metadata": {
            "type": "array", "description": "Expiration metadata only. Never append it to a download URL.",
            "items": {"type": "object", "properties": {
                "download_index": {"type": "string", "description": "Zero-based index in the upstream download list, encoded as text."},
                "expires_at": {"type": "string", "description": "Expiration timestamp provided by the API."},
                "note": {"type": "string", "description": "Guidance for preserving the signed URL."},
            }},
        },
        "message": {"type": "string", "description": "Wait timeout or recovery guidance, when present."},
        "last_project": {"type": "object", "description": "Last observed API project snapshot if the wait timed out after a successful status check."},
    },
    "required": ["project_type"],
    "additionalProperties": True,
}

WORKFLOW_GUIDE_OUTPUT_SCHEMA = {
    "type": "object",
    "description": "Static workflow planning guidance. Does not verify account access, eligibility, current price or output quality.",
    "properties": {
        "workflow": {"type": "string", "description": "Requested workflow identifier."},
        "title": {"type": "string", "description": "Human-readable workflow purpose."},
        "input": {"type": "string", "description": "Required source media and other inputs."},
        "product_url": {"type": "string", "description": "Relevant Magic Hour product page."},
        "api_docs_url": {"type": "string", "description": "API documentation for the selected workflow."},
        "creation_tool": {"type": ["string", "null"], "description": "MCP creation tool, or null for general API integration guidance."},
        "steps": {"type": "array", "items": {"type": "string"}, "description": "Ordered upload, creation and retrieval steps."},
        "example_request": {"type": "string", "description": "Example user request for this workflow."},
        "follow_up": {"type": "string", "description": "How to reuse or edit an accepted result."},
        "review": {"type": "string", "description": "Output checks and workflow-specific limitations."},
        "models_and_costs_url": {"type": "string", "description": "Documentation of model constraints and credit costs."},
        "evaluation_url": {"type": "string", "description": "Guidance for evaluating completed media outputs."},
        "starter_recipes_url": {"type": "string", "description": "API starter recipes."},
        "guidance": {"type": "array", "items": {"type": "string"}, "description": "Account, cost, recovery and usage caveats."},
    },
    "required": ["workflow", "title", "input", "product_url", "api_docs_url", "steps", "example_request", "follow_up", "review", "models_and_costs_url", "evaluation_url", "starter_recipes_url", "guidance"],
    "additionalProperties": True,
}
