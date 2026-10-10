"""Real MCP client demo: quote first, explicitly authorize, then reconnect/poll."""

import argparse
import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

from fastmcp import Client


def show(result):
    print(json.dumps({k: v for k, v in result.items() if k != "plan_token"}, indent=2))


def save(path, state):
    # Private ledger holds the quote token and job IDs, never the API key.
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(state, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


async def call(endpoint, key, name, arguments):
    # Fresh connection each time demonstrates recovery independent of a session.
    async with Client(endpoint, auth=key) as client:
        tools = await client.list_tools()
        if name not in {tool.name for tool in tools}:
            raise RuntimeError(f"{name} unavailable; deploy the matching MCP/API changes first")
        result = await client.call_tool(name, arguments, timeout=120, raise_on_error=False)
        data = result.structured_content
        if not isinstance(data, dict):
            raise RuntimeError(f"{name} did not return a structured object")
        return data


async def run(args):
    key = os.environ["MAGIC_HOUR_API_KEY"]
    ledger = Path(args.ledger).expanduser()
    if args.resume:
        state = json.loads(ledger.read_text())
        if state["endpoint"] != args.endpoint:
            raise ValueError("Resume with the same MCP endpoint as the original plan")
    else:
        if ledger.exists():
            raise ValueError("Ledger already exists; use --resume or a new --ledger")
        if not args.image_file_path or not args.max_credits:
            raise ValueError("Planning requires --image-file-path and --max-credits")
        arguments = {
            "image_file_path": args.image_file_path,
            "creative_instructions": args.instructions,
            "variations": args.variations,
            "aspect_ratio": args.aspect_ratio,
            "duration_seconds": args.duration_seconds,
            "max_credits": args.max_credits,
        }
        if args.model:
            arguments["model"] = args.model
        if args.resolution:
            arguments["resolution"] = args.resolution
        plan = await call(args.endpoint, key, "plan_video_variations", arguments)
        if plan.get("status") != "planned":
            show(plan)
            raise RuntimeError("No usable plan returned; no generation submitted")
        state = {"endpoint": args.endpoint, "plan": plan}
        save(ledger, state)
        show(plan)
        print(f"Plan saved privately: {ledger}. Review before --resume --execute.")
        return

    plan = state["plan"]
    if args.execute:
        if args.approved_max_credits is None:
            raise ValueError("Paid execution requires --approved-max-credits")
        show(plan)
        result = await call(args.endpoint, key, "execute_video_variations", {
            "plan_token": plan["plan_token"],
            "approved_max_credits": args.approved_max_credits,
        })
        state["execution"] = result
        save(ledger, state)
        show(result)

    deadline = time.monotonic() + args.timeout_seconds
    while time.monotonic() < deadline:
        status = await call(args.endpoint, key, "video_variations_status", {
            "plan_token": plan["plan_token"],
        })
        state["status"] = status
        save(ledger, state)
        show(status)
        if status.get("status") == "error":
            return
        projects = status.get("projects", [])
        terminal = {"complete", "completed", "failed", "error", "canceled", "cancelled"}
        if projects and all(project.get("status") in terminal for project in projects):
            return
        if any(project.get("status") == "not_submitted" for project in projects):
            print("Submission incomplete; review recovery, then replay --execute with the same ledger.")
            return
        await asyncio.sleep(max(args.poll_seconds, status.get("retry_after_seconds", 5)))
    print("Jobs still running; rerun with --resume. No generation resubmitted.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="https://mcp.magichour.ai/")
    parser.add_argument("--image-file-path")
    parser.add_argument("--instructions", default=(
        "Take this product photo and generate three different short vertical videos. "
        "Preserve the product identity; use distinct camera movements."
    ))
    parser.add_argument("--variations", type=int, default=3)
    parser.add_argument("--aspect-ratio", choices=["9:16", "16:9", "1:1", "input"], default="9:16")
    parser.add_argument("--duration-seconds", type=int, default=5)
    parser.add_argument("--model")
    parser.add_argument("--resolution")
    parser.add_argument("--max-credits", type=int)
    parser.add_argument("--approved-max-credits", type=int)
    parser.add_argument("--ledger", default="video-variations.private.json")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=15)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    if args.execute and not args.resume:
        parser.error("--execute requires --resume after reviewing a saved plan")
    if args.poll_seconds < 5 or args.timeout_seconds < 1:
        parser.error("--poll-seconds must be >=5; --timeout-seconds must be positive")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
