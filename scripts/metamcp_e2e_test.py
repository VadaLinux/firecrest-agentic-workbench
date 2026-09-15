#!/usr/bin/env python3
"""End-to-end test through MetaMCP, for prompt 03.

Proves the aggregated endpoint works, not just that the tools appear in a list:
submits a job through MetaMCP's `firecrest__submit_job` (so the call travels
MetaMCP → firecrest-mcp → FirecREST → Slurm), waits for it, and reads its output
back through `firecrest__get_job_log`.

    scripts/metamcp_e2e_test.py

Exits non-zero if the tool surface or the job lifecycle is wrong.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from metamcp_admin import Admin, api_key  # noqa: E402

ENDPOINT = "http://localhost:12008/metamcp/firecrest-workbench/mcp"
EXPECTED = {
    "firecrest__submit_job",
    "firecrest__get_job_status",
    "firecrest__list_files",
    "firecrest__download_file",
    "firecrest__get_job_log",
}

SCRIPT = """#!/bin/bash
#SBATCH --job-name=via-metamcp
#SBATCH --output=via-metamcp.out
#SBATCH --error=via-metamcp.err
#SBATCH --time=00:01:00
#SBATCH --partition=part01

echo hello-through-metamcp
"""


def show(label: str, payload) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(payload, indent=2, default=str)[:1200])


async def main() -> int:
    failures: list[str] = []
    async with Admin(ENDPOINT, api_key()) as admin:
        names = {t.name for t in await admin.tools()}
        firecrest = sorted(n for n in names if n.startswith("firecrest__"))
        show("tools exposed by the aggregated endpoint", firecrest)

        missing = EXPECTED - names
        if missing:
            failures.append(f"missing tools: {sorted(missing)}")
        extra = {n for n in firecrest} - EXPECTED
        if extra:
            failures.append(f"unexpected firecrest tools: {sorted(extra)}")

        submit = await admin.call("firecrest__submit_job",
                                  {"script": SCRIPT, "system": "cluster"})
        show("firecrest__submit_job (through MetaMCP)", submit)
        if not submit.get("ok"):
            failures.append(f"submit_job failed: {submit.get('error')}")
            print("\nFAILED:", *failures, sep="\n  ")
            return 1
        jobid = submit["jobid"]

        # Wait for the scheduler to finish with it.
        status: dict = {}
        for _ in range(20):
            await asyncio.sleep(2)
            status = await admin.call("firecrest__get_job_status", {"job_id": jobid})
            state = (status.get("job") or {}).get("state")
            if state in ("COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"):
                break
        show("firecrest__get_job_status", status)
        if (status.get("job") or {}).get("state") != "COMPLETED":
            failures.append(f"job {jobid} did not complete: {status}")

        log = await admin.call("firecrest__get_job_log", {"job_id": jobid})
        show("firecrest__get_job_log", log)
        stdout = (log.get("log") or {}).get("stdout") or ""
        if "hello-through-metamcp" not in stdout:
            failures.append(f"stdout missing the marker: {stdout!r}")

        files = await admin.call("firecrest__list_files", {"path": "/home"})
        show("firecrest__list_files (summary)",
             {k: v for k, v in files.items() if k != "entries"})
        if not files.get("ok"):
            failures.append(f"list_files failed: {files.get('error')}")

    if failures:
        print("\nFAILED:", *failures, sep="\n  ")
        return 1
    print("\nOK: 5 FirecREST tools aggregated through MetaMCP; job submitted, "
          "completed, and its output read back through the endpoint.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
