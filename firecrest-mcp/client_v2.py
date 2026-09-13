"""A second, independent FirecREST client — for FirecREST v2.

This is **not** a rewrite of ``client.py``; it exists alongside it, per Option
B of the design in ``docs/reports/05-firecrest-v2-gap.md``. Do not import
this module's internals into ``client.py`` or vice versa beyond the two
narrow, version-agnostic things reused below — v1 and v2 are different enough
(task polling, header vs. path addressing, error shapes) that sharing more
than that just threads a version check through every method (see the report's
"Option C — rejected" discussion).

Two properties of the FirecREST v2 API this module exists to hide from the
caller, mirroring the two ``client.py``'s docstring calls out for v1
(``docs/hot-cache-v2.md`` §0):

1. **The system is a URL path segment** (``/{system_name}/...``), not a
   header. There is no ``X-Machine-Name`` anywhere in v2.
2. **Nothing is asynchronous.** Every call this module makes gets the real
   payload in one HTTP round trip — no task id, no polling. v2 has no
   ``/tasks/*`` surface at all.

A third difference is deliberately *not* hidden, the same way v1's
``sourcePath`` oddity isn't hidden there: ``GET .../ops/view`` (used by
``get_job_log`` to read a file's content) is always called with an explicit
``size``. Its own documented default (5 MiB) can exceed a cluster's
configured ceiling — the v2 demo's own stock config does exactly that,
turning every default-size call into a 404 (``docs/hot-cache-v2.md`` §3).
Rather than special-case that one cluster's ceiling, this client just never
relies on the server picking a size for it, on any cluster.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

from client import FileEntry, FirecRESTError

logger = logging.getLogger(__name__)

#: Refresh a cached JWT this many seconds before it actually expires. Applied
#: on top of whatever ``expires_in`` the token response actually reports, so
#: it works the same whether that's v1's real ~300s Keycloak tokens or the v2
#: demo launcher's ~1-year throwaway ones — nothing here assumes a TTL.
TOKEN_REFRESH_MARGIN_S = 30.0

DEFAULT_TIMEOUT_S = 30.0

#: Size (bytes) requested from ``GET .../ops/view`` when reading a file's
#: content, chosen to sit comfortably under any cluster's configured
#: ``max_ops_file_size`` — the v2 demo's own is 1 MiB (``docs/hot-cache-v2.md``
#: §3). ``download_file`` has no such ceiling to worry about; this only
#: matters for the preview/log-tail path ``get_job_log`` uses.
DEFAULT_VIEW_SIZE_BYTES = 65536


# --------------------------------------------------------------------------
# Request/response models
#
# Hand-written from the running v2 app's own /openapi.json (app_version
# 2.6.0) and the live behaviour recorded in docs/hot-cache-v2.md — the same
# two sources docs/reports/05-firecrest-v2-gap.md verified against.
# --------------------------------------------------------------------------


class JobSubmit(BaseModel):
    """Result of a successful submission.

    Unlike v1's ``JobSubmit``, there is no ``task_id`` — v2's submit response
    *is* the result, not a reference to poll. ``job_file_out``/``job_file_err``
    are filled in with one extra read of ``GET .../jobs/{id}/metadata`` right
    after submission, since ``POST .../jobs`` itself only ever returns
    ``{"jobId": ...}``.
    """

    jobid: str
    job_file: str | None = None
    job_file_out: str | None = None
    job_file_err: str | None = None


class JobStatus(BaseModel):
    """One job's state, as returned by ``GET /compute/{system}/jobs/{job_id}``.

    Flattened from the nested ``status``/``time`` objects the API actually
    returns, field names kept close to v1's ``JobAcct`` where the concepts
    overlap so the agent-facing shape doesn't look arbitrarily different
    depending on which version answered.
    """

    jobid: str
    name: str | None = None
    state: str | None = None
    state_reason: str | None = None
    exit_code: int | None = None
    interrupt_signal: int | None = None
    partition: str | None = None
    nodes: str | None = None
    account: str | None = None
    cluster: str | None = None
    group: str | None = None
    user: str | None = None
    working_directory: str | None = None
    priority: int | None = None
    elapsed_time: int | None = None
    start_time: int | None = None
    end_time: int | None = None


class JobLog(BaseModel):
    """Captured job output, as persisted under ``logs/``."""

    jobid: str
    stdout: str | None = None
    stderr: str | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
    note: str | None = None


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


@dataclass
class FirecRESTConfigV2:
    """Connection settings for a FirecREST v2 gateway, read from ``FIRECREST_V2_*``.

    Deliberately a separate variable namespace from v1's ``FIRECREST_*`` —
    the two versions can be configured side by side in one ``.env`` without
    either overwriting the other, which is what lets ``server.py`` pick one
    at startup without the choice breaking the other (``FIRECREST_API_VERSION``).
    """

    base_url: str
    token_url: str
    client_id: str
    client_secret: str
    system: str
    timeout_s: float = DEFAULT_TIMEOUT_S
    log_dir: Path = field(default_factory=lambda: Path("logs"))
    verify_ssl: bool = True

    @classmethod
    def from_env(cls, env_file: str | os.PathLike[str] | None = None) -> FirecRESTConfigV2:
        """Build from environment variables, optionally loading a ``.env`` first."""
        if env_file is not None:
            _load_dotenv(Path(env_file))
        else:
            _load_dotenv(Path(__file__).with_name(".env"))

        def need(name: str) -> str:
            value = os.environ.get(name)
            if not value:
                raise FirecRESTError(
                    f"{name} is not set. Copy firecrest-mcp/.env.example to .env "
                    f"and fill in the FIRECREST_V2_* section (see docs/hot-cache-v2.md)."
                )
            return value

        return cls(
            base_url=need("FIRECREST_V2_BASE_URL").rstrip("/"),
            token_url=need("FIRECREST_V2_TOKEN_URL"),
            client_id=need("FIRECREST_V2_CLIENT_ID"),
            client_secret=need("FIRECREST_V2_CLIENT_SECRET"),
            system=need("FIRECREST_V2_SYSTEM"),
            timeout_s=float(os.environ.get("FIRECREST_V2_TIMEOUT", DEFAULT_TIMEOUT_S)),
            log_dir=Path(os.environ.get("FIRECREST_V2_LOG_DIR", "logs")),
            verify_ssl=os.environ.get("FIRECREST_V2_VERIFY_SSL", "true").lower()
            not in ("0", "false", "no"),
        )


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader; python-dotenv is used when available.

    A small standalone copy of ``client.py``'s loader rather than an import
    of it — it's private to that module, and duplicating 15 lines here keeps
    this module resolvable on its own without reaching into another module's
    internals.
    """
    if not path.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(path, override=False)
        return
    except ImportError:
        pass
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------


class FirecRESTClientV2:
    """Async FirecREST v2 client with transparent auth and URL-path addressing.

    Use as an async context manager so the underlying connection pool is
    closed deterministically::

        async with FirecRESTClientV2(config) as client:
            result = await client.submit_job(script, "daint")
    """

    def __init__(
        self,
        config: FirecRESTConfigV2,
        *,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.config = config
        self._http = http_client or httpx.AsyncClient(
            timeout=config.timeout_s, verify=config.verify_ssl
        )
        self._owns_http = http_client is None
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    async def __aenter__(self) -> FirecRESTClientV2:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # -- authentication ----------------------------------------------------

    async def _access_token(self) -> str:
        """Return a valid JWT, refreshing when it is close to expiry."""
        if self._token and time.monotonic() < self._token_expires_at - TOKEN_REFRESH_MARGIN_S:
            return self._token

        response = await self._http.post(
            self.config.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code != 200:
            raise FirecRESTError(
                "Token request rejected — check FIRECREST_V2_CLIENT_ID/SECRET and "
                "FIRECREST_V2_TOKEN_URL",
                status=response.status_code,
                body=_safe_json(response),
            )
        payload = response.json()
        token = payload["access_token"]
        self._token = token
        self._token_expires_at = time.monotonic() + float(payload.get("expires_in", 300))
        logger.debug("acquired v2 token, expires in %ss", payload.get("expires_in"))
        return token

    async def _headers(self) -> dict[str, str]:
        # No X-Machine-Name here: v2 addresses the system in the URL path,
        # applied by each caller below, not in a header.
        return {"Authorization": f"Bearer {await self._access_token()}"}

    # -- low-level calls ---------------------------------------------------

    async def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        response = await self._http.get(
            self.config.base_url + path, params=params, headers=await self._headers()
        )
        return _unwrap(response, path)

    async def _post_json(self, path: str, *, json_body: dict[str, Any]) -> Any:
        response = await self._http.post(
            self.config.base_url + path, json=json_body, headers=await self._headers()
        )
        return _unwrap(response, path)

    # -- public operations -------------------------------------------------

    async def submit_job(
        self,
        script: str | None = None,
        system: str | None = None,
        account: str | None = None,
        *,
        env: dict[str, str] | None = None,
        job_name: str = "firecrest-job",
        working_directory: str | None = None,
        stdout_name: str | None = None,
        stderr_name: str | None = None,
        script_path: str | None = None,
    ) -> JobSubmit:
        """Submit a batch job and return its job id.

        v2 has no multipart upload route at all (``docs/hot-cache-v2.md`` §4):
        a job's script is either inline text (`script`) or the path to a file
        already on the remote filesystem (`script_path`, e.g. uploaded first
        with a filesystem call) — exactly one of the two, never both.

        `working_directory` must be an **absolute** path. A relative value
        such as ``"."`` is accepted by this endpoint and the job's script
        really does land in the real home directory on disk — but that is
        not the finding: verified live against the v2 demo, both
        ``GET .../jobs/{id}`` (`workingDirectory`) and
        ``GET .../jobs/{id}/metadata`` (`standardOutput`/`standardError`)
        echo the relative value straight back, unresolved, and
        ``GET .../ops/view``/``.../ops/download`` — what :meth:`get_job_log`
        and :meth:`download_file` need to read that output back — both
        reject any path that isn't absolute. A job submitted with a relative
        `working_directory` is therefore real but unreadable through this
        client. Rather than guess at the remote home directory, this raises
        instead of accepting one: call :meth:`list_files` on a path you
        already know (or the equivalent MCP tool) to find an absolute one.
        """
        if not script and not script_path:
            raise FirecRESTError(
                "submit_job needs either `script` text or `script_path` pointing at "
                "a file already on the remote filesystem."
            )
        if script and script_path:
            raise FirecRESTError("submit_job takes `script` or `script_path`, not both.")
        if not working_directory or not working_directory.startswith("/"):
            raise FirecRESTError(
                "submit_job needs an absolute `working_directory` "
                f"(got {working_directory!r}). A relative one is accepted by "
                "FirecREST v2 and the job runs fine, but its output paths come "
                "back unresolved and get_job_log/download_file cannot read them "
                "— see submit_job's docstring. Call list_files first if you "
                "don't already know an absolute path on this system."
            )

        system = system or self.config.system
        job: dict[str, Any] = {
            "name": job_name,
            "workingDirectory": working_directory,
        }
        if script_path:
            job["scriptPath"] = script_path
        else:
            job["script"] = script
        if account:
            job["account"] = account
        if env:
            job["env"] = env
        if stdout_name:
            job["standardOutput"] = stdout_name
        if stderr_name:
            job["standardError"] = stderr_name

        payload = await self._post_json(f"/compute/{system}/jobs", json_body={"job": job})
        jobid = payload.get("jobId") if isinstance(payload, dict) else None
        if not jobid:
            raise FirecRESTError("Submission was not accepted", body=payload)
        jobid = str(jobid)

        stdout_path = stderr_path = None
        try:
            meta = await self.get_job_metadata(jobid, system=system)
        except FirecRESTError as exc:
            # The job was accepted; only the paths-for-free convenience read
            # failed. Not fatal — the caller still gets a real job id back.
            logger.warning("submit_job: could not read back metadata for job %s: %s", jobid, exc)
        else:
            stdout_path = meta.get("standardOutput")
            stderr_path = meta.get("standardError")

        return JobSubmit(
            jobid=jobid,
            job_file=script_path,
            job_file_out=stdout_path,
            job_file_err=stderr_path,
        )

    async def get_job_status(self, job_id: str, system: str | None = None) -> JobStatus:
        """Return a job's current state.

        One call, ``GET /compute/{system}/jobs/{job_id}`` — the real state
        inline, no task indirection and no v1-shaped "don't trust this
        endpoint" caveat: this is the only status endpoint v2 has, and it's
        correct (``docs/hot-cache-v2.md`` §0, §4).
        """
        system = system or self.config.system
        payload = await self._get(f"/compute/{system}/jobs/{job_id}")
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
        for entry in jobs:
            if str(entry.get("jobId")) == str(job_id):
                return _job_status_from_entry(entry)
        raise FirecRESTError(f"No status entry for job {job_id} on {system}.", body=payload)

    async def get_job_metadata(self, job_id: str, system: str | None = None) -> dict[str, Any]:
        """Return a job's raw metadata record (script, stdin/stdout/stderr paths).

        Used internally by :meth:`submit_job` and :meth:`get_job_log`, and
        exposed publicly because it's the one place v2 tells you where a
        job's output actually landed — no local bookkeeping required, unlike
        v1's ``logs/submissions.json`` (``docs/hot-cache-v2.md`` §4).
        """
        system = system or self.config.system
        payload = await self._get(f"/compute/{system}/jobs/{job_id}/metadata")
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
        for entry in jobs:
            if str(entry.get("jobId")) == str(job_id):
                return entry
        raise FirecRESTError(f"No metadata for job {job_id} on {system}.", body=payload)

    async def list_files(self, path: str, system: str | None = None) -> list[FileEntry]:
        """List a directory on the target system.

        Parameter is `path` — v2 renamed v1's `targetPath`
        (``docs/hot-cache-v2.md`` §3).
        """
        system = system or self.config.system
        payload = await self._get(f"/filesystem/{system}/ops/ls", params={"path": path})
        entries = payload.get("output", []) if isinstance(payload, dict) else []
        if isinstance(entries, dict):  # a single entry is returned unwrapped
            entries = [entries]
        return [_file_entry_from_v2(e) for e in entries]

    async def download_file(self, path: str, system: str | None = None) -> bytes:
        """Download a file's bytes.

        Parameter is `path`, the same name every ``ops/*`` call uses in v2 —
        v1's `sourcePath`-vs-`targetPath` inconsistency does not exist here
        (``docs/hot-cache-v2.md`` §3).
        """
        system = system or self.config.system
        response = await self._http.get(
            f"{self.config.base_url}/filesystem/{system}/ops/download",
            params={"path": path},
            headers=await self._headers(),
        )
        if response.status_code != 200:
            raise FirecRESTError(
                "download_file failed", status=response.status_code, body=_safe_json(response)
            )
        return response.content

    async def _view_file(self, path: str, system: str | None = None,
                          size: int = DEFAULT_VIEW_SIZE_BYTES) -> str:
        """Read a file's content as text via ``ops/view``.

        Always sends an explicit `size` — see the module docstring for why
        relying on the endpoint's own default is not safe to do generically.
        """
        system = system or self.config.system
        payload = await self._get(
            f"/filesystem/{system}/ops/view", params={"path": path, "size": size}
        )
        return payload.get("output", "") if isinstance(payload, dict) else str(payload)

    async def get_job_log(self, job_id: str, system: str | None = None) -> JobLog:
        """Collect a job's stdout and stderr, and persist them under ``logs/``.

        v2 needs no local submission record to do this — unlike v1, the
        output paths come straight from FirecREST itself via
        :meth:`get_job_metadata`, so this works for any job id that exists on
        the system, not only ones submitted through this client
        (``docs/hot-cache-v2.md`` §4).
        """
        system = system or self.config.system
        try:
            meta = await self.get_job_metadata(job_id, system=system)
        except FirecRESTError as exc:
            raise FirecRESTError(
                f"Could not look up output paths for job {job_id} on {system}: {exc}"
            ) from exc

        log = JobLog(jobid=str(job_id))
        for stream, key in (("stdout", "standardOutput"), ("stderr", "standardError")):
            remote = meta.get(key)
            if not remote:
                continue
            try:
                text = await self._view_file(remote, system=system)
            except FirecRESTError as exc:
                logger.warning("could not read %s for job %s: %s", stream, job_id, exc)
                continue
            setattr(log, stream, text)
            local = self._write_log(job_id, stream, text)
            setattr(log, f"{stream}_path", str(local))
        if log.stdout is None and log.stderr is None:
            log.note = (
                "Neither stdout nor stderr could be read. The job may still be "
                "running, or its output paths may have been cleaned up."
            )
        return log

    def _write_log(self, job_id: str, stream: str, text: str) -> Path:
        """Persist a job's output; DocMind ingests this directory.

        Filenames are prefixed ``v2-`` so a v1 and a v2 job that happen to
        share a numeric job id never collide in ``logs/`` when both clients
        are in use against the same log directory.
        """
        self.config.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.log_dir / f"v2-{job_id}.{stream}.log"
        path.write_text(text)
        return path


def _job_status_from_entry(entry: dict[str, Any]) -> JobStatus:
    status = entry.get("status") or {}
    time_ = entry.get("time") or {}
    return JobStatus(
        jobid=str(entry.get("jobId")),
        name=entry.get("name"),
        state=status.get("state"),
        state_reason=status.get("stateReason"),
        exit_code=status.get("exitCode"),
        interrupt_signal=status.get("interruptSignal"),
        partition=entry.get("partition"),
        nodes=entry.get("nodes"),
        account=entry.get("account"),
        cluster=entry.get("cluster"),
        group=entry.get("group"),
        user=entry.get("user"),
        working_directory=entry.get("workingDirectory"),
        priority=entry.get("priority"),
        elapsed_time=time_.get("elapsed"),
        start_time=time_.get("start"),
        end_time=time_.get("end"),
    )


def _file_entry_from_v2(entry: dict[str, Any]) -> FileEntry:
    size = entry.get("size")
    return FileEntry(
        name=entry.get("name", ""),
        type=entry.get("type"),
        size=str(size) if size is not None else None,
        permissions=entry.get("permissions"),
        user=entry.get("user"),
        group=entry.get("group"),
        last_modified=entry.get("lastModified"),
        link_target=entry.get("linkTarget"),
    )


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text[:400]


def _unwrap(response: httpx.Response, path: str) -> Any:
    """Return the parsed body, or raise a FirecRESTError describing the problem.

    v2 answers every error the same way — ``{"errorType", "message",
    "causedBy", "data", "user"}`` — on a status code that actually matches the
    problem (404 for an unknown system or job, 401 for missing auth). Unlike
    v1's ``_unwrap``, there is exactly one shape to parse, not a per-endpoint
    set of special cases (``docs/hot-cache-v2.md`` §5).
    """
    body = _safe_json(response)
    if response.status_code >= 400:
        message = body.get("message") if isinstance(body, dict) else None
        raise FirecRESTError(
            f"{path} failed: {message or 'request rejected'}",
            status=response.status_code,
            body=body,
        )
    if isinstance(body, dict) and body.get("errorType") == "error":
        raise FirecRESTError(f"{path} reported an error", body=body)
    return body
