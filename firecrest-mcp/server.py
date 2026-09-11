#!/usr/bin/env python3
"""MCP server exposing FirecREST to an agent.

Run it:

    python server.py --dry-run          # start and announce the tool surface
    python server.py                    # stdio transport (what MetaMCP uses)
    python server.py --transport http   # Streamable HTTP on --host/--port

The tool surface is deliberately small. Five tools, matching `ARCHITECTURE.md`
exactly — a wider surface is harder for an agent to choose from correctly, and
harder for CSCS staff to audit.

Every docstring below is written for the *agent* as its reader. The MCP server
publishes them as the tool descriptions, so they are the agent's only guidance
on when a tool applies — they are not developer documentation.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import sys
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from client import FirecRESTClient, FirecRESTConfig, FirecRESTError

logger = logging.getLogger("firecrest-mcp")

DOWNLOAD_DIR = Path("downloads")
MAX_INLINE_PREVIEW = 4000

server = MCPServer(
    name="firecrest-mcp",
    title="FirecREST",
    instructions=(
        "Tools for driving an HPC cluster through CSCS's FirecREST API: submit "
        "batch jobs, check their status, read their output, and browse the "
        "remote filesystem. Job ids returned by submit_job are accepted by "
        "get_job_status and get_job_log."
    ),
)

_client: FirecRESTClient | None = None


async def get_client() -> FirecRESTClient:
    """Create the FirecREST client on first use and reuse it afterwards.

    Lazy so that ``--dry-run`` can start the server without live credentials,
    and so a misconfiguration surfaces as a tool error rather than an import
    failure.
    """
    global _client
    if _client is None:
        _client = FirecRESTClient(FirecRESTConfig.from_env())
    return _client


def _fail(exc: Exception) -> dict[str, Any]:
    """Turn a failure into something the agent can reason about and report."""
    if isinstance(exc, FirecRESTError):
        result: dict[str, Any] = {"ok": False, "error": str(exc)}
        if exc.status is not None:
            result["http_status"] = exc.status
        if exc.body is not None:
            result["detail"] = exc.body
        return result
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


@server.tool()
async def submit_job(script: str, system: str, account: str | None = None) -> dict[str, Any]:
    """Submit a batch job script to the HPC cluster and get back its job id.

    Use this when the user asks to run, launch, submit or start something on the
    cluster — an analysis, a simulation, a script, a command.

    `script` is the complete batch script text, including its `#SBATCH` header.
    Always send a full script: a minimal one needs at least a partition, e.g.

        #!/bin/bash
        #SBATCH --job-name=my-job
        #SBATCH --output=my-job.out
        #SBATCH --partition=part01
        echo hello

    `system` is the cluster to run on (`part01`/`part02` are the partitions of
    the demo system `cluster`). If you do not know the system name, call
    list_files on a path you do know, or check the FirecREST documentation —
    do not guess.

    `account` is the scheduler project account, only needed if the cluster
    requires one; it is safe to omit.

    Returns the job id, plus the remote paths where the job's stdout and stderr
    will appear. Submission is asynchronous but this waits for the scheduler to
    accept the job, so a returned job id is a real, queued job.
    """
    try:
        client = await get_client()
        result = await client.submit_job(script=script, system=system, account=account)
        return {
            "ok": True,
            "jobid": result.jobid,
            "system": system,
            "stdout_path": result.job_file_out,
            "stderr_path": result.job_file_err,
            "next_step": f"Call get_job_status with job_id={result.jobid} to watch it.",
        }
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent as data
        return _fail(exc)


@server.tool()
async def get_job_status(job_id: str) -> dict[str, Any]:
    """Report the current state of a job you previously submitted.

    Use this to answer "is it done?", "did it finish?", "how long did it take?",
    or to wait for a job before reading its output. Call it with a job id from
    submit_job.

    The `state` field is Slurm's own value: typically `PENDING`, `RUNNING`,
    `COMPLETED`, `FAILED`, `CANCELLED` or `TIMEOUT`. Only treat `COMPLETED` as
    success — and note that `COMPLETED` with a non-zero `exit_code` means the
    script ran but the command inside it failed, so read the log before
    reporting success.

    This reads the scheduler's accounting record, which is authoritative. Do not
    infer job state from anything else: polling a job too early raises an error
    saying no record exists yet rather than returning a misread status.

    If the result carries a `warning`, the accounting record was not yet
    complete when it was read — re-query before telling the user anything about
    the job's name or partition.
    """
    try:
        client = await get_client()
        status = await client.get_job_status(job_id)
        job = status.model_dump(exclude_none=True)
        result: dict[str, Any] = {"ok": True, "job": job}
        # Reproduced against the demo stack: while a job is PENDING, sacct
        # reports a placeholder record — name "allocation", empty partition —
        # which is replaced by the real values once the job is allocated.
        # The empty partition is the tell, so do not let an agent report it.
        if not job.get("partition"):
            result["warning"] = (
                "This accounting record is a placeholder, not the job's real "
                "identity: while a job is PENDING the scheduler reports no "
                "partition and a generic name. Report only the state now, and "
                "re-query once the job starts before naming it or its partition."
            )
        return result
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


@server.tool()
async def list_files(path: str) -> dict[str, Any]:
    """List a directory on the cluster's filesystem.

    Use this to explore where things are: find a job's working directory, locate
    input files, check whether an output was written, or discover a user's home
    directory. Call it before submitting a job that needs a path you are unsure
    about, and after a job finishes to see what it produced.

    `path` must be absolute (for example `/home`, or `/home/<user>`). Listing a
    file rather than a directory is an error, not an empty result.

    Each entry has `name`, `type` (`-` file, `d` directory, `l` symlink),
    `size` in bytes, `permissions`, `user`, `group` and `last_modified`.
    """
    try:
        client = await get_client()
        entries = await client.list_files(path)
        return {
            "ok": True,
            "path": path,
            "count": len(entries),
            "entries": [e.model_dump(exclude_none=True) for e in entries],
        }
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


@server.tool()
async def download_file(path: str) -> dict[str, Any]:
    """Fetch a file from the cluster and return its contents.

    Use this to read a file the user asked about, to inspect a job's output when
    you know its path, or to show a configuration or input file. Prefer
    get_job_log when you want a job's stdout/stderr and only know its job id,
    because that tool already knows where the output lives.

    Text files come back in `content`. Large files and binary files are saved
    locally instead, with `saved_to` giving the local path and `preview` holding
    the beginning — check `truncated` before assuming you have the whole file.
    """
    try:
        client = await get_client()
        payload = await client.download_file(path)

        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        local = DOWNLOAD_DIR / Path(path).name
        local.write_bytes(payload)

        result: dict[str, Any] = {
            "ok": True,
            "path": path,
            "size_bytes": len(payload),
            "saved_to": str(local),
        }
        try:
            text = payload.decode()
        except UnicodeDecodeError:
            result["encoding"] = "binary"
            result["preview_base64"] = base64.b64encode(
                payload[: MAX_INLINE_PREVIEW // 2]
            ).decode()
            result["truncated"] = True
        else:
            result["content"] = text[:MAX_INLINE_PREVIEW]
            result["truncated"] = len(text) > MAX_INLINE_PREVIEW
        return result
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


@server.tool()
async def get_job_log(job_id: str) -> dict[str, Any]:
    """Read a job's stdout and stderr, to find out what it printed or why it failed.

    Use this to answer "why did my job fail?", "what did it print?", or after
    get_job_status shows a job is finished. This is the tool to reach for when a
    job's outcome is unclear — it returns what the job actually wrote, so
    diagnose from that rather than from the script or from what was expected.

    Two things to know:

    - Only jobs submitted through this server can be read, because FirecREST
      does not let a bare job id be mapped back to its output paths. If the job
      was submitted elsewhere, the error says so — use list_files and
      download_file to read the file directly in that case.
    - A job that printed nothing is not necessarily a job that failed. Check the
      state with get_job_status before drawing a conclusion.

    `stdout`, `stderr` and their local paths are returned; the same content is
    also written under `logs/`, which is where the documentation indexer picks
    up failure evidence from.
    """
    try:
        client = await get_client()
        log = await client.get_job_log(job_id)
        return {"ok": True, "log": log.model_dump(exclude_none=True)}
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def _announce(mode: str) -> None:
    tools = ["submit_job", "get_job_status", "list_files", "download_file", "get_job_log"]
    print(f"firecrest-mcp server ({mode})", file=sys.stderr)
    for name in tools:
        print(f"  tool: {name}", file=sys.stderr)
    try:
        config = FirecRESTConfig.from_env()
    except FirecRESTError as exc:
        print(f"  configuration: NOT READY — {exc}", file=sys.stderr)
        print(
            "  The server starts anyway; tool calls will report the same problem.",
            file=sys.stderr,
        )
    else:
        print(
            f"  configuration: {config.base_url} as system '{config.system}', "
            f"logs in {config.log_dir}",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MCP server exposing FirecREST to an agent.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="start the server and report the configured tool surface and target",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
        help="stdio (default, used by MetaMCP) or Streamable HTTP",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host")
    parser.add_argument("--port", type=int, default=8765, help="HTTP bind port")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if args.dry_run:
        _announce("dry-run")

    if args.transport == "http":
        logger.info("serving Streamable HTTP on %s:%d", args.host, args.port)
        server.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        logger.info("serving stdio")
        server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
