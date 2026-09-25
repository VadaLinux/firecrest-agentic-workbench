#!/usr/bin/env python3
"""Talk to firecrest-mcp over stdio the way a real MCP client does.

This is the "manual MCP client call" required by prompt 02: it starts the
server as a subprocess, lists its tools, and drives a complete job lifecycle
end to end. It needs the FirecREST demo stack running (see docs/hot-cache.md).

    python scripts/mcp_smoke_test.py

Exit code is non-zero if the tool surface or the job lifecycle is not as
expected, so it doubles as a check in CI or before a demo.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

HERE = Path(__file__).resolve().parent.parent
EXPECTED_TOOLS = {
    "submit_job",
    "cancel_job",
    "get_job_status",
    "list_files",
    "download_file",
    "get_job_log",
}
V2_PARAMETERS = {
    "nodes": 1,
    "time_minutes": 1,
    "partition": "part01",
    "job_name": "f7t-mcp-smoke",
    "message": "hello-from-mcp",
}


def show(label: str, payload) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(payload, indent=2, default=str)[:1800])


def tool_result(result) -> dict:
    """MCP returns content blocks plus optional structured content.

    Our tools answer with a single JSON object, so prefer the structured form and
    fall back to parsing the text block.
    """
    if getattr(result, "structured_content", None):
        return result.structured_content
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text}
    return {}


async def main() -> int:
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(HERE / "server.py")],
        env={**os.environ},
        cwd=str(HERE),
    )
    failures: list[str] = []

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(
                f"connected to {init.server_info.name} "
                f"(protocol {init.protocol_version})"
            )

            listed = await session.list_tools()
            names = {t.name for t in listed.tools}
            show("tool surface", sorted(names))
            if names != EXPECTED_TOOLS:
                failures.append(
                    f"tool surface mismatch: expected {sorted(EXPECTED_TOOLS)}, got {sorted(names)}"
                )

            submit = tool_result(
                await session.call_tool(
                    "submit_job",
                    {
                        "template": "hello",
                        "parameters": V2_PARAMETERS,
                        "approved_by": "local-smoke",
                        "system": os.environ.get(
                            "FIRECREST_V2_SYSTEM", "cluster-slurm-api"
                        ),
                        "working_directory": os.environ.get(
                            "FIRECREST_V2_WORKING_DIRECTORY", "/home/fireuser"
                        ),
                    },
                )
            )
            show("submit_job", submit)
            if not submit.get("ok"):
                failures.append(f"submit_job failed: {submit.get('error')}")
                print("\nFAILED:", *failures, sep="\n  ")
                return 1
            jobid = submit["jobid"]

            state = None
            status: dict = {}
            for _ in range(30):
                status = tool_result(
                    await session.call_tool("get_job_status", {"job_id": jobid})
                )
                state = (status.get("job") or {}).get("state")
                if state in {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"}:
                    break
                await asyncio.sleep(2)
            show("get_job_status", status)
            if state != "COMPLETED":
                failures.append(f"job {jobid} state is {state!r}, expected COMPLETED")

            log = tool_result(await session.call_tool("get_job_log", {"job_id": jobid}))
            show("get_job_log", log)
            stdout = (log.get("log") or {}).get("stdout") or ""
            if "hello-from-mcp" not in stdout:
                failures.append(
                    f"job stdout did not contain the expected marker: {stdout!r}"
                )

            files = tool_result(
                await session.call_tool("list_files", {"path": "/home"})
            )
            show("list_files", {k: v for k, v in files.items() if k != "entries"})
            if not files.get("ok"):
                failures.append(f"list_files failed: {files.get('error')}")

    if failures:
        print("\nFAILED:", *failures, sep="\n  ")
        return 1
    print(
        "\nOK: tool surface correct, job submitted, reached COMPLETED, output readable."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
