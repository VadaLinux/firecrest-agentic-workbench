#!/usr/bin/env python3
"""End-to-end smoke test against the local FirecREST v2 dev environment.

CRIGAMO-21: verifies the environment `make env-up` starts (see
docs/local-env.md) with the real pyfirecrest package — token acquisition,
listing systems, submitting a "hello world" job, polling it to completion,
and reading its output back. This is a standalone check of the *environment*,
independent of firecrest-mcp/client_v2.py (which has its own unit tests,
mocked, in firecrest-mcp/tests/test_client_v2.py).

Usage:
    pip install pyfirecrest==3.10.0   # or: pip install -r requirements-smoke.txt
    cp firecrest-mcp/.env.example firecrest-mcp/.env   # fill in FIRECREST_V2_*
    python scripts/smoke_local.py

Reads the same FIRECREST_V2_* variables as firecrest-mcp/client_v2.py, from
firecrest-mcp/.env if present (see docs/local-env.md for what each one means
and where its value comes from in the local compose demo).

Exit code is non-zero if any step fails, so this doubles as a CI-style gate
before a demo — the same role scripts/mcp_smoke_test.py plays for v1.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
ENV_FILE = HERE / "firecrest-mcp" / ".env"


def load_dotenv(path: Path) -> None:
    """Minimal .env loader — mirrors client_v2.py's own, no extra dependency."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def need(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"FATAL: {name} is not set. Copy firecrest-mcp/.env.example to "
            f".env and fill in the FIRECREST_V2_* section — see docs/local-env.md.",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def show(label: str, payload) -> None:
    print(f"\n=== {label} ===")
    print(payload)


def main() -> int:
    load_dotenv(ENV_FILE)

    try:
        import firecrest as f7t
    except ImportError:
        print(
            "FATAL: pyfirecrest is not installed. Run:\n"
            "  pip install pyfirecrest==3.10.0",
            file=sys.stderr,
        )
        return 1

    base_url = need("FIRECREST_V2_BASE_URL")
    token_url = need("FIRECREST_V2_TOKEN_URL")
    client_id = need("FIRECREST_V2_CLIENT_ID")
    client_secret = need("FIRECREST_V2_CLIENT_SECRET")
    system = need("FIRECREST_V2_SYSTEM")

    # 1. Token — client-credentials, same flow client_v2.py's own _access_token()
    #    uses (docs/hot-cache-v2.md §1).
    auth = f7t.ClientCredentialsAuth(client_id, client_secret, token_url)
    client = f7t.v2.Firecrest(firecrest_url=base_url, authorization=auth)
    try:
        token = auth.get_access_token()
    except Exception as exc:  # pyfirecrest raises plain exceptions on transport errors
        print(f"FATAL: could not obtain a token from {token_url}: {exc}", file=sys.stderr)
        return 1
    show("token", f"acquired, {len(token)} chars")

    # 2. Systems — proves the gateway is reachable and the token is accepted.
    try:
        systems = client.systems()
    except f7t.FirecrestException as exc:
        print(f"FATAL: GET systems failed: {exc}\n{exc.responses}", file=sys.stderr)
        return 1
    names = [s["name"] for s in systems]
    show("systems", names)
    if system not in names:
        print(
            f"FATAL: FIRECREST_V2_SYSTEM={system!r} is not one of the systems "
            f"this gateway reports: {names}",
            file=sys.stderr,
        )
        return 1

    # 3. Submit a "hello world" job. No explicit standardOutput/standardError:
    #    Slurm's own default is slurm-<jobid>.out in the working directory,
    #    which step 5 below reads back.
    working_dir = "/home/fireuser"
    try:
        result = client.submit(
            system_name=system,
            working_dir=working_dir,
            script_str="#!/bin/bash\necho hello-firecrest-v2-smoke\n",
        )
    except f7t.FirecrestException as exc:
        print(f"FATAL: job submission failed: {exc}\n{exc.responses}", file=sys.stderr)
        return 1
    job_id = str(result["jobId"])
    show("submit", f"jobId={job_id}")

    # 4. Poll to completion. wait_for_job is pyfirecrest's own polling helper
    #    (GET /compute/{system}/jobs/{job_id} — no task indirection in v2,
    #    docs/hot-cache-v2.md §0).
    try:
        jobs = client.wait_for_job(system, job_id, timeout=120)
    except Exception as exc:
        print(f"FATAL: job did not complete within timeout: {exc}", file=sys.stderr)
        return 1
    job = jobs[0] if isinstance(jobs, list) else jobs
    state = job.get("status", {}).get("state")
    exit_code = job.get("status", {}).get("exitCode")
    show("final job state", f"state={state} exitCode={exit_code}")
    if state != "COMPLETED" or exit_code != 0:
        print(f"FATAL: job did not complete successfully: {job}", file=sys.stderr)
        return 1

    # 5. Read the output back. job_metadata's standardOutput/standardError only
    #    works on the "-ssh" connection-mode system in this environment
    #    (docs/local-env.md); the "-api" (REST) system used above reports
    #    "Scheduler can't handle this request when configured to use rest
    #    connection mode" for metadata specifically — so the smoke test reads
    #    Slurm's own default output filename directly, same as verified by
    #    hand in this issue's closing comment.
    output_path = f"{working_dir}/slurm-{job_id}.out"
    try:
        content = client.view(system, output_path)
    except f7t.FirecrestException as exc:
        print(f"FATAL: could not read {output_path}: {exc}\n{exc.responses}", file=sys.stderr)
        return 1
    show("job output", repr(content))
    if "hello-firecrest-v2-smoke" not in content:
        print(f"FATAL: unexpected job output: {content!r}", file=sys.stderr)
        return 1

    print("\nOK — token, systems, submit, poll, output all verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
