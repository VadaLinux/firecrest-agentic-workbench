#!/usr/bin/env python3
"""MCP server exposing FirecREST to an agent.

Run it:

    python server.py --dry-run          # start and announce the tool surface
    python server.py                    # stdio transport (what MetaMCP uses)
    python server.py --transport http   # Streamable HTTP on --host/--port

The tool surface is deliberately small. A small tool surface, matching `ARCHITECTURE.md`
exactly — a wider surface is harder for an agent to choose from correctly, and
harder for CSCS staff to audit.

Every docstring below is written for the *agent* as its reader. The MCP server
publishes them as the tool descriptions, so they are the agent's only guidance
on when a tool applies — they are not developer documentation.
"""

from __future__ import annotations

import argparse
import base64
import logging
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mcp.server.mcpserver import MCPServer

from client import FirecRESTClient, FirecRESTConfig, FirecRESTError
from client import _load_dotenv as _load_env_file
from client_v2 import FirecRESTClientV2, FirecRESTConfigV2
from fcagent.core.approval import ApprovalStore
from fcagent.core.audit import AuditLog
from fcagent.core.errors import GuardrailDenied
from fcagent.core.jobs import JobService
from fcagent.core.provenance import ProvenanceStore, workbench_commit
from fcagent.core.settings import CoreSettings, path_within

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

_client: FirecRESTClient | FirecRESTClientV2 | None = None


def _api_version() -> str:
    """Which FirecREST major version to speak, chosen once at startup.

    Unset or ``v1`` keeps today's default (``client.py`` against the v1 demo
    stack) untouched. Set ``FIRECREST_API_VERSION=v2`` to talk FirecREST v2
    (``client_v2.py``) instead — the two are mutually exclusive per server
    process, per Option B of ``docs/reports/05-firecrest-v2-gap.md``.

    ``FIRECREST_API_VERSION`` has to be readable before either config class's
    own ``from_env()`` runs — that call is what *this flag* decides between —
    so the ``.env`` file is loaded here too, not left to one of them to do as
    a side effect.
    """
    _load_env_file(Path(__file__).with_name(".env"))
    version = os.environ.get("FIRECREST_API_VERSION", "v1").strip().lower()
    if version not in ("v1", "v2"):
        raise FirecRESTError(
            f"FIRECREST_API_VERSION={version!r} is not supported — use 'v1' or 'v2'."
        )
    return version


async def get_client() -> FirecRESTClient | FirecRESTClientV2:
    """Create the FirecREST client on first use and reuse it afterwards.

    Lazy so that ``--dry-run`` can start the server without live credentials,
    and so a misconfiguration surfaces as a tool error rather than an import
    failure.
    """
    global _client
    if _client is None:
        if _api_version() == "v2":
            config_v2 = FirecRESTConfigV2.from_env()
            _require_allowed_default(config_v2.system)
            _require_allowed_working_directory(config_v2.working_directory)
            _client = FirecRESTClientV2(config_v2)
        else:
            config_v1 = FirecRESTConfig.from_env()
            _require_allowed_default(config_v1.system)
            _client = FirecRESTClient(config_v1)
    return _client


def _fail(exc: Exception) -> dict[str, Any]:
    """Return only a safe exception type and optional HTTP status to the agent."""
    result: dict[str, Any] = {"ok": False, "error": type(exc).__name__}
    if isinstance(exc, FirecRESTError) and exc.status is not None:
        result["http_status"] = exc.status
    if getattr(exc, "approval_consumed", False):
        # Only set for failures AFTER a successful claim (see _submit_prepared).
        result["approval_consumed"] = True
        result["next_step"] = (
            "The approval was used up by this attempt and cannot be reused. Do not "
            "retry this prepared_id: tell the user, prepare the job again and ask "
            "for a new approval."
        )
    return result


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


def _core() -> tuple[CoreSettings, JobService]:
    """Build the job service from environment-configured paths (no secrets)."""
    settings = CoreSettings.from_env()
    service = JobService(
        template_directory=settings.template_directory,
        limits=settings.limits,
        audit=AuditLog(settings.audit_path),
        client=None,
        store=ApprovalStore(settings.state_dir, settings.approval_ttl_seconds),
        public_key_path=settings.approval_public_key,
    )
    return settings, service


def _require_allowed_working_directory(default: str | None) -> None:
    """Refuse a configured default working directory the limits file does not allow."""
    if default is None:
        return
    allowed = CoreSettings.from_env().limits.allowed_working_directories
    if not path_within(default, allowed):
        raise FirecRESTError(
            "the default working directory is not under allowed_working_directories "
            f"({', '.join(sorted(allowed))}); fix the limits file or "
            "FIRECREST_V2_WORKING_DIRECTORY."
        )


def _require_allowed_default(default: str) -> None:
    """Refuse a default system that the limits file does not allow."""
    allowed = CoreSettings.from_env().limits.allowed_systems
    if default not in allowed:
        raise FirecRESTError(
            f"the default FirecREST system {default[:64]!r} is not in "
            f"allowed_systems ({', '.join(sorted(allowed))}); fix the limits "
            "file or the FIRECREST_*SYSTEM setting."
        )


def _check_default_system() -> None:
    """Startup check. Credentials stay lazy: an unset system is checked on first use."""
    name = "FIRECREST_V2_SYSTEM" if _api_version() == "v2" else "FIRECREST_SYSTEM"
    default = os.environ.get(name)
    if default is None and _api_version() == "v1":
        default = "cluster"  # client.py's own default
    if default:
        _require_allowed_default(default)
    if _api_version() == "v2":
        _require_allowed_working_directory(
            os.environ.get("FIRECREST_V2_WORKING_DIRECTORY") or None
        )


def _denied(exc: GuardrailDenied) -> dict[str, Any]:
    return {
        "ok": False,
        "error": "GuardrailDenied",
        "reason": exc.reason,
        "next_step": exc.next_step,
    }


@server.tool()
async def list_templates() -> dict[str, Any]:
    """List the approved job templates and the typed parameters each one needs.

    Call this first when the user wants to run something on the cluster. Only
    these templates can be run; each parameter has a type (`int` with min/max,
    `enum` with allowed values, or a safe `string` of letters, digits, `_`, `.`
    and `-`). Then call prepare_job with a template name and every parameter.
    `systems` lists the allowed target systems and the default one; omit `system`
    in prepare_job to use the default.
    """
    try:
        _, service = _core()
        return {
            "ok": True,
            "templates": service.list_templates(),
            "systems": {
                "allowed": sorted(service.limits.allowed_systems),
                "default": (await get_client()).config.system,
            },
            "working_directory": {
                "allowed": sorted(service.limits.allowed_working_directories),
                "default": getattr(
                    (await get_client()).config, "working_directory", None
                ),
            },
        }
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


@server.tool()
async def prepare_job(
    template: str,
    parameters: dict[str, Any],
    working_directory: str | None = None,
    system: str | None = None,
    account: str | None = None,
    actor: str = "mcp",
) -> dict[str, Any]:
    """Prepare an approved-template job and get a `prepared_id` awaiting a human.

    This is step 1 of running anything on the cluster. Choose a `template` from
    list_templates and pass every declared, typed value in `parameters`. Limits
    (nodes, time, partition) are checked and the template is rendered, but
    NOTHING is submitted. You get a `prepared_id`, an expiry, and a readable
    `summary` (template, parameters, system, partition, nodes, time and an
    estimate in node-hours) to show the user.

    Next: tell the user to run `fcagent approve <prepared_id>` themselves. You
    cannot approve a job. After they confirm, call submit_job(prepared_id). If
    the approval expires or anything changes, prepare again. Never send a free
    script: only templates are accepted.

    Omit `system` to use the default target; list_templates shows the allowed
    systems and the default. A system that is not allowed is refused here, before
    anything can be approved. The resolved system appears in the `summary`.
    `working_directory` is optional: omit it to use the default shown by
    list_templates (`working_directory.default`); if that is `null` you must pass
    one. It must be a normalized absolute path under an allowed directory (also
    listed there); anything else is refused before anything can be approved.
    """
    try:
        _, service = _core()
        if system is None:
            system = (await get_client()).config.system
        if working_directory is None:
            working_directory = getattr(
                (await get_client()).config, "working_directory", None
            )
            if working_directory is None:
                raise GuardrailDenied("working_directory_invalid")
        prepared = service.prepare(
            actor=actor,
            system=system,
            template=template,
            parameters=parameters,
            working_directory=working_directory,
            account=account,
        )
        return {"ok": True, **prepared}
    except GuardrailDenied as exc:
        return _denied(exc)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


def _requested_job_name(job_id: str) -> str | None:
    """The `job_name` parameter recorded in the card (what was asked, not reported)."""
    try:
        card = ProvenanceStore(CoreSettings.from_env().state_dir).read(job_id)
        name = card.get("parameters", {}).get("job_name")
        return name if isinstance(name, str) else None
    except Exception:  # noqa: BLE001 - unmanaged job or unreadable card
        return None


def _update_card_status(job_id: str, job: dict[str, Any]) -> None:
    """Refresh the card of a job this server submitted; unmanaged jobs are ignored."""
    try:
        ProvenanceStore(CoreSettings.from_env().state_dir).record_status(job_id, job)
    except Exception:  # noqa: BLE001 - provenance never breaks a status query
        logger.warning("could not update provenance for job %s", job_id)


async def _versions(
    client: FirecRESTClientV2, settings: CoreSettings
) -> dict[str, Any]:
    try:
        import importlib.metadata as md

        pyfc = md.version("pyfirecrest")
    except Exception:  # noqa: BLE001
        pyfc = None
    root = settings.template_directory.parent
    return {
        "firecrest_server": await client.get_server_version(),
        "pyfirecrest": pyfc,
        "workbench_commit": workbench_commit(root),
    }


async def _submit_prepared(
    service: JobService, settings: CoreSettings, prepared_id: str, actor: str
) -> dict[str, Any]:
    job = service.claim(actor=actor, prepared_id=prepared_id)
    try:
        return await _submit_claimed(service, settings, job, prepared_id, actor)
    except Exception as exc:
        # The record is consumed by the claim above: it can never be used again.
        exc.approval_consumed = True  # type: ignore[attr-defined]
        raise


async def _submit_claimed(
    service: JobService,
    settings: CoreSettings,
    job: Any,
    prepared_id: str,
    actor: str,
) -> dict[str, Any]:
    ctx = job.context
    client = await get_client()
    if not isinstance(client, FirecRESTClientV2):
        raise FirecRESTError(
            "approved-template submission requires FIRECREST_API_VERSION=v2"
        )
    try:
        result = await client.submit_job(
            script=job.script,
            system=ctx["system"],
            account=ctx["account"],
            working_directory=ctx["working_directory"],
            partition=job.partition,
        )
    except Exception as exc:
        service.audit.write(
            actor=actor,
            action="submit_job",
            system=ctx["system"],
            template=ctx["template"],
            parameters_hash=ctx["parameters_hash"],
            job_id=None,
            outcome="error",
            exception_type=type(exc).__name__,
            http_status=getattr(exc, "status", None),
            prepared_id=prepared_id,
        )
        raise
    # From here on the job EXISTS on the cluster: nothing below may turn this call
    # into an error, or the agent would prepare a duplicate.
    trace = getattr(client, "last_trace", None)  # before any further client call
    warnings: list[str] = []
    try:
        service.audit_submitted(actor, job, result.jobid)
    except Exception:  # noqa: BLE001
        logger.exception("audit write failed after submit of job %s", result.jobid)
        warnings.append("audit_error")
    try:
        store = ProvenanceStore(settings.state_dir)
        store.write(
            result.jobid,
            store.initial_card(
                context=ctx,
                approval=job.approval,
                prepared_id=prepared_id,
                job_id=result.jobid,
                versions=await _versions(client, settings),
                trace=trace,
                submitted_at=datetime.now(UTC).isoformat(),
            ),
        )
        provenance = f"Call get_provenance with job_id={result.jobid}."
    except Exception:  # noqa: BLE001
        logger.exception("provenance write failed for job %s", result.jobid)
        warnings.append("provenance_error")
        provenance = (
            "The reproducibility card could not be written; the job WAS submitted. "
            "Do not prepare it again; tell the operator."
        )
    response: dict[str, Any] = {
        "ok": True,
        "jobid": result.jobid,
        "system": ctx["system"],
        "stdout_path": result.job_file_out,
        "stderr_path": result.job_file_err,
        "provenance": provenance,
        "next_step": f"Call get_job_status with job_id={result.jobid} to watch it.",
    }
    if getattr(result, "paths_source", None):
        response["paths_source"] = result.paths_source
    if getattr(result, "note", None):
        response["note"] = result.note
    if warnings:
        response["warnings"] = warnings
        response["provenance_error"] = "provenance_error" in warnings
    return response


@server.tool()
async def submit_job(prepared_id: str, actor: str = "mcp") -> dict[str, Any]:
    """Submit a job that a human has already approved, using its `prepared_id`.

    This is the last step. The required order is: list_templates -> prepare_job
    (choose an approved template and pass its typed parameters; you get a
    `prepared_id`) -> the human runs `fcagent approve <prepared_id>` -> you call
    submit_job(prepared_id). Do not send scripts or parameters here: only the
    exact, approved job is sent, and only once.

    If the job is not approved yet, expired, rejected, already used or changed,
    the result explains why in `reason` and what to do in `next_step`: follow it
    (usually: ask the user to approve, or prepare again). Do not retry in a loop.

    On success you get the job id and a reproducibility card is stored (see
    get_provenance). `stdout_path`/`stderr_path` are the scheduler's paths, or
    `null` when it does not report them; then `note` says why. If the approved
    template itself declares output files with only `%j` as pattern, the paths are
    derived from it and marked `paths_source: "script"` (derived, not reported by
    the scheduler); any other Slurm pattern leaves the path `null`. To read the
    output use get_job_log(job_id) rather than guessing a path.
    """
    try:
        settings, service = _core()
        return await _submit_prepared(service, settings, prepared_id, actor)
    except GuardrailDenied as exc:
        return _denied(exc)
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent as data
        return _fail(exc)


@server.tool()
async def get_provenance(job_id: str) -> dict[str, Any]:
    """Return the reproducibility card of a job that was submitted through here.

    Use this when the user asks how a job was run, wants to reproduce it, or
    needs evidence for a result: template and parameters, hashes of the rendered
    script and files, system, partition, FirecREST/pyfirecrest/workbench
    versions, times, state, exit code, output hashes, trace identifiers and who
    approved it. Missing trace identifiers are `null`, never invented.
    """
    try:
        settings, _ = _core()
        return {
            "ok": True,
            "provenance": ProvenanceStore(settings.state_dir).read(job_id),
        }
    except GuardrailDenied as exc:
        return _denied(exc)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


@server.tool()
async def rerun_from_provenance(job_id: str, actor: str = "mcp") -> dict[str, Any]:
    """Prepare a new run of a past job from its reproducibility card.

    The template and parameters of the card are re-rendered and the SHA-256 of
    the rendered script and of the template must match the card; if they differ
    the rerun is refused. A match returns a new `prepared_id` that still needs
    `fcagent approve <prepared_id>` from a human before submit_job.
    """
    try:
        _, service = _core()
        store = ProvenanceStore(CoreSettings.from_env().state_dir)
        card = store.read(job_id)
        hashes = card.get("hashes", {})
        spec, script = service.render(
            template=card["template"],
            parameters=card["parameters"],
            working_directory=card["working_directory"],
        )
        import hashlib

        if hashlib.sha256(script.encode()).hexdigest() != hashes.get(
            "script_sha256"
        ) or hashlib.sha256(spec.path.read_bytes()).hexdigest() != hashes.get(
            "template_sha256"
        ):
            raise GuardrailDenied("provenance_hash_mismatch")
        prepared = service.prepare(
            actor=actor,
            system=card["system"],
            template=card["template"],
            parameters=card["parameters"],
            working_directory=card["working_directory"],
            account=card.get("account"),
        )
        return {"ok": True, "rerun_of": job_id, **prepared}
    except GuardrailDenied as exc:
        return _denied(exc)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


@server.tool()
async def cancel_job(job_id: str) -> dict[str, Any]:
    """Cancel a pending or running FirecREST v2 job by its job id.

    Use this only when the user explicitly asks to stop, cancel or abort a job.
    It is unavailable for the legacy FirecREST v1 demo transport. A successful
    result confirms FirecREST accepted the cancellation request; call
    get_job_status afterwards to observe the scheduler's final state.
    """
    try:
        client = await get_client()
        if not isinstance(client, FirecRESTClientV2):
            raise FirecRESTError("cancel_job requires FIRECREST_API_VERSION=v2")
        await client.cancel_job(job_id)
        return {"ok": True, "jobid": job_id, "cancel_requested": True}
    except Exception as exc:  # noqa: BLE001
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

    If the scheduler reports the name `allocation`, `note` says it is not the
    requested name and, for jobs submitted here, `requested_job_name` gives the
    name that was asked for (labelled as requested, not as reported).

    If the result carries a `warning`, the accounting record was not yet
    complete when it was read — re-query before telling the user anything about
    the job's name or partition.
    """
    try:
        client = await get_client()
        status = await client.get_job_status(job_id)
        job = status.model_dump(exclude_none=True)
        result: dict[str, Any] = {"ok": True, "job": job}
        _update_card_status(job_id, job)
        # Reproduced against the v1 demo stack only: while a job is PENDING,
        # sacct reports a placeholder record — name "allocation", empty
        # partition — which is replaced by the real values once the job is
        # allocated. v2 has no such indirection (one call, real state inline,
        # docs/hot-cache-v2.md §0), so this caveat does not apply there.
        if job.get("name") == "allocation":
            result["note"] = (
                "The scheduler reports the job name as `allocation` (observed with "
                "the REST scheduler connection on FirecREST 2.6.1); it is not the "
                "name requested in the template. Do not quote it as the job name."
            )
            requested = _requested_job_name(job_id)
            if requested:
                result["requested_job_name"] = requested
        if isinstance(client, FirecRESTClient) and not job.get("partition"):
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
async def download_file(path: str, job_id: str | None = None) -> dict[str, Any]:
    """Fetch a file from the cluster and return its contents.

    Use this to read a file the user asked about, to inspect a job's output when
    you know its path, or to show a configuration or input file. Prefer
    get_job_log when you want a job's stdout/stderr and only know its job id,
    because that tool already knows where the output lives.

    Text files come back in `content`. Large files and binary files are saved
    locally instead, with `saved_to` giving the local path and `preview` holding
    the beginning — check `truncated` before assuming you have the whole file.

    Pass `job_id` when the file is an output of a job submitted through
    submit_job: its path and SHA-256 are then recorded in the job's
    reproducibility card. Without `job_id` nothing is recorded.
    """
    try:
        client = await get_client()
        payload = await client.download_file(path)
        if job_id:
            try:
                ProvenanceStore(CoreSettings.from_env().state_dir).record_output(
                    job_id, path, payload
                )
            except Exception:  # noqa: BLE001 - provenance never breaks a download
                logger.warning("could not record output for job %s", job_id)

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
    tools = [
        "list_templates",
        "prepare_job",
        "submit_job",
        "get_provenance",
        "rerun_from_provenance",
        "cancel_job",
        "get_job_status",
        "list_files",
        "download_file",
        "get_job_log",
    ]
    print(f"firecrest-mcp server ({mode})", file=sys.stderr)
    for name in tools:
        print(f"  tool: {name}", file=sys.stderr)
    try:
        version = _api_version()
        config = (
            FirecRESTConfigV2.from_env()
            if version == "v2"
            else FirecRESTConfig.from_env()
        )
    except FirecRESTError as exc:
        print(f"  configuration: NOT READY — {exc}", file=sys.stderr)
        print(
            "  The server starts anyway; tool calls will report the same problem.",
            file=sys.stderr,
        )
    else:
        print(
            f"  configuration: FirecREST {version} at {config.base_url} as system "
            f"'{config.system}', logs in {config.log_dir}",
            file=sys.stderr,
        )


# A DNS name: dot-separated labels of letters, digits and hyphens, no leading or trailing
# hyphen in a label, no port, no scheme, no user, no wildcard. Used for FIRECREST_MCP_PUBLIC_HOST.
_DNS_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$"
)


def _http_transport_security():
    """DNS-rebinding allowlist for the Streamable HTTP transport.

    The MCP SDK turns on DNS-rebinding protection by itself only when the bind host is
    localhost, and then allows only localhost Host/Origin values. Behind a Cloudflare
    tunnel the requests arrive with the public hostname in ``Host:`` and would be refused
    with 421. The public hostname comes from ``FIRECREST_MCP_PUBLIC_HOST`` in ``.env`` and
    is added to the allowlist; it is never hard-coded here. Without the variable the SDK
    default is used unchanged. The bind host is not changed by this function.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    public_host = os.environ.get("FIRECREST_MCP_PUBLIC_HOST", "").strip().lower()
    if not public_host:
        return None  # SDK default, as before this change
    # An all-numeric last label is an IPv4 literal, not a DNS name: reject it too.
    looks_like_ipv4 = re.fullmatch(r"[0-9.]+", public_host) is not None
    if looks_like_ipv4 or not _DNS_HOSTNAME.fullmatch(public_host):
        raise FirecRESTError(
            "FIRECREST_MCP_PUBLIC_HOST must be a bare DNS hostname such as mcp.example.org "
            "(no scheme, port, path, user, wildcard or IP address); got an invalid value. "
            "Fix firecrest-mcp/.env and start again."
        )
    local = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=local + [public_host, f"{public_host}:*"],
        allowed_origins=[
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://[::1]:*",
            f"https://{public_host}",
        ],
    )


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except FirecRESTError as exc:
        # Configuration errors are reported in one clean line, not as a traceback.
        print(f"firecrest-mcp: configuration error: {exc}", file=sys.stderr)
        return 2


def _main(argv: list[str] | None = None) -> int:
    # The allowlist below is read from the environment at startup. The .env file is loaded
    # here, before that read, so a value that lives only in firecrest-mcp/.env is seen.
    # _load_env_file does not override variables that are already set.
    _load_env_file(Path(__file__).with_name(".env"))
    parser = argparse.ArgumentParser(
        description="MCP server exposing FirecREST to an agent."
    )
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
    else:
        _check_default_system()

    if args.transport == "http":
        logger.info("serving Streamable HTTP on %s:%d", args.host, args.port)
        server.run(
            transport="streamable-http",
            host=args.host,
            port=args.port,
            transport_security=_http_transport_security(),
        )
    else:
        logger.info("serving stdio")
        server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
