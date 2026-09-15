"""The single place in this project that talks to FirecREST.

Everything the agent can do to FirecREST goes through :class:`FirecRESTClient`.
No other module may issue raw HTTP to FirecREST, and nothing may import
``pyfirecrest`` — see ``AGENTS.md``.

Two properties of the FirecREST API this module exists to hide:

1. **The machine name is an HTTP header** (``X-Machine-Name``), not a path
   segment. Callers pass a system name and never think about it again.
2. **Most endpoints are asynchronous.** They answer with a task reference that
   must be polled until it reaches status ``"200"``. :meth:`_resolve_task`
   absorbs that entirely.

A third is deliberately *not* hidden: ``download_file`` has to use the query
parameter ``sourcePath`` while every other file endpoint uses ``targetPath``.
That is an upstream inconsistency documented in ``docs/hot-cache.md``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

#: Refresh a cached JWT this many seconds before it actually expires. The demo
#: issues 300-second tokens, so this is a 10% safety margin.
TOKEN_REFRESH_MARGIN_S = 30.0

DEFAULT_TIMEOUT_S = 30.0
TASK_POLL_INTERVAL_S = 1.0
TASK_POLL_TIMEOUT_S = 180.0

#: A FirecREST task reports this status when the underlying operation succeeded.
TASK_STATUS_OK = "200"


class FirecRESTError(RuntimeError):
    """Any failure talking to FirecREST, with something the agent can act on."""

    def __init__(self, message: str, *, status: int | None = None, body: Any = None):
        self.status = status
        self.body = body
        detail = ""
        if status is not None:
            detail = f" (HTTP {status})"
        if body is not None:
            detail += f": {json.dumps(body, default=str)[:400]}"
        super().__init__(message + detail)


# --------------------------------------------------------------------------
# Request/response models
#
# Hand-written from the FirecREST spec shipped in the demo (v1.16.1) plus the
# live behaviour recorded in docs/hot-cache.md. There is no v2 spec in the
# demo revision, despite README.md and ARCHITECTURE.md referring to one.
# --------------------------------------------------------------------------


class TaskRef(BaseModel):
    """FirecREST's answer to an asynchronous call."""

    success: str | None = None
    task_id: str
    task_url: str | None = None
    description: str | None = None
    error: str | None = None


class Task(BaseModel):
    """State of an asynchronous FirecREST task."""

    task_id: str | None = None
    status: str | None = None
    description: str | None = None
    data: Any = None
    service: str | None = None
    system: str | None = None
    user: str | None = None
    created_at: str | None = None
    last_modify: str | None = None


class JobAcct(BaseModel):
    """One job's accounting record, as returned by ``/compute/acct``."""

    jobid: str
    name: str | None = None
    state: str | None = None
    partition: str | None = None
    nodes: str | None = None
    nodelist: str | None = None
    exit_code: str | None = None
    elapsed_time: str | None = None
    cpu_time: str | None = None
    start_time: str | None = None
    termination_time: str | None = None
    user: str | None = None


class JobSubmit(BaseModel):
    """Result of a successful submission."""

    jobid: str
    task_id: str
    job_file: str | None = None
    job_file_out: str | None = None
    job_file_err: str | None = None


class FileEntry(BaseModel):
    """One directory entry from ``/utilities/ls``."""

    name: str
    type: str | None = None
    size: str | None = None
    permissions: str | None = None
    user: str | None = None
    group: str | None = None
    last_modified: str | None = None
    link_target: str | None = None


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
class FirecRESTConfig:
    """Connection settings, normally read from the environment.

    ``token_url`` is separate from ``base_url`` because Keycloak and the Kong
    gateway listen on different ports and one cannot be derived from the other.
    """

    base_url: str
    token_url: str
    client_id: str
    client_secret: str
    system: str = "cluster"
    timeout_s: float = DEFAULT_TIMEOUT_S
    log_dir: Path = field(default_factory=lambda: Path("logs"))
    verify_ssl: bool = True

    @classmethod
    def from_env(cls, env_file: str | os.PathLike[str] | None = None) -> FirecRESTConfig:
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
                    f"and fill it in (see docs/hot-cache.md section 1)."
                )
            return value

        return cls(
            base_url=need("FIRECREST_BASE_URL").rstrip("/"),
            token_url=need("FIRECREST_TOKEN_URL"),
            client_id=need("FIRECREST_CLIENT_ID"),
            client_secret=need("FIRECREST_CLIENT_SECRET"),
            system=os.environ.get("FIRECREST_SYSTEM", "cluster"),
            timeout_s=float(os.environ.get("FIRECREST_TIMEOUT", DEFAULT_TIMEOUT_S)),
            log_dir=Path(os.environ.get("FIRECREST_LOG_DIR", "logs")),
            verify_ssl=os.environ.get("FIRECREST_VERIFY_SSL", "true").lower()
            not in ("0", "false", "no"),
        )


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader; python-dotenv is used when available."""
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


class FirecRESTClient:
    """Async FirecREST client with transparent auth and task unwrapping.

    Use as an async context manager so the underlying connection pool is
    closed deterministically::

        async with FirecRESTClient(config) as client:
            result = await client.submit_job(script, "cluster")
    """

    def __init__(
        self,
        config: FirecRESTConfig,
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

    async def __aenter__(self) -> FirecRESTClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # -- authentication ----------------------------------------------------

    async def _access_token(self) -> str:
        """Return a valid JWT, refreshing when it is close to expiry.

        FirecREST never sees this: callers of the public methods do not know a
        token exists.
        """
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
                "Token request rejected — check FIRECREST_CLIENT_ID/SECRET and "
                "FIRECREST_TOKEN_URL",
                status=response.status_code,
                body=_safe_json(response),
            )
        payload = response.json()
        token = payload["access_token"]
        self._token = token
        self._token_expires_at = time.monotonic() + float(payload.get("expires_in", 300))
        logger.debug("acquired token, expires in %ss", payload.get("expires_in"))
        return token

    async def _headers(self, system: str | None = None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {await self._access_token()}",
            "X-Machine-Name": system or self.config.system,
        }

    # -- low-level calls ---------------------------------------------------

    async def _get(self, path: str, *, params: dict[str, Any] | None = None,
                   system: str | None = None) -> Any:
        response = await self._http.get(
            self.config.base_url + path, params=params, headers=await self._headers(system)
        )
        return _unwrap(response, path)

    async def _post_upload(self, path: str, *, filename: str, content: bytes,
                           fields: dict[str, str] | None = None,
                           system: str | None = None) -> Any:
        response = await self._http.post(
            self.config.base_url + path,
            files={"file": (filename, content, "application/x-sh")},
            data=fields or {},
            headers=await self._headers(system),
        )
        return _unwrap(response, path)

    async def _resolve_task(self, path: str, *, params: dict[str, Any] | None = None,
                            system: str | None = None,
                            timeout_s: float = TASK_POLL_TIMEOUT_S) -> Task:
        """Run an asynchronous endpoint and return its finished task.

        The endpoint may answer synchronously (rarely); that case is handled by
        synthesising an already-finished :class:`Task`.
        """
        payload = await self._get(path, params=params, system=system)
        if isinstance(payload, dict) and "task_id" in payload:
            return await self._poll_task(payload["task_id"], timeout_s=timeout_s)
        return Task(task_id=None, status=TASK_STATUS_OK, data=payload)

    async def _poll_task(self, task_id: str, *, timeout_s: float = TASK_POLL_TIMEOUT_S) -> Task:
        deadline = time.monotonic() + timeout_s
        last: Task | None = None
        while True:
            raw = await self._get(f"/tasks/{task_id}")
            task_payload = raw.get("task", raw) if isinstance(raw, dict) else raw
            last = Task.model_validate(task_payload)
            if str(last.status) == TASK_STATUS_OK:
                return last
            if str(last.status) not in ("100", "110", None):
                raise FirecRESTError(
                    f"FirecREST task {task_id} failed: {last.description or last.status}",
                    body=last.data,
                )
            if time.monotonic() > deadline:
                raise FirecRESTError(
                    f"FirecREST task {task_id} did not finish within {timeout_s:.0f}s "
                    f"(last status {last.status}: {last.description})",
                )
            await asyncio.sleep(TASK_POLL_INTERVAL_S)

    # -- public operations -------------------------------------------------

    async def whoami(self, system: str | None = None) -> str:
        """Return the FirecREST identity behind the configured credentials.

        Synchronous upstream, and cheap — useful as a liveness and
        authentication check before doing anything expensive.
        """
        payload = await self._get("/utilities/whoami", system=system)
        return payload.get("output", "") if isinstance(payload, dict) else str(payload)

    async def systems(self) -> list[dict[str, Any]]:
        """List the systems FirecREST knows about, with availability status."""
        payload = await self._get("/status/systems")
        return payload.get("out", []) if isinstance(payload, dict) else []

    async def submit_job(
        self,
        script: str,
        system: str | None = None,
        account: str | None = None,
        *,
        env: dict[str, str] | None = None,
        job_name: str = "firecrest-job",
    ) -> JobSubmit:
        """Submit a batch script and return its job id.

        Despite ``ARCHITECTURE.md`` describing this as ``submit_job(script,
        system, account)``, this FirecREST revision has **no** endpoint that
        accepts an inline script as JSON — ``POST /compute/jobs`` answers
        ``405 Method Not Allowed``. The script is therefore written to a
        temporary file and sent as a multipart upload to
        ``/compute/jobs/upload``, which preserves the documented signature and
        hides the transport difference from the caller.
        """
        system = system or self.config.system
        fields: dict[str, str] = {}
        if account:
            fields["account"] = account
        if env:
            fields["env"] = json.dumps(env)

        filename = f"{job_name}.sh"
        payload = await self._post_upload(
            "/compute/jobs/upload",
            filename=filename,
            content=script.encode(),
            fields=fields,
            system=system,
        )
        task_id = payload.get("task_id") if isinstance(payload, dict) else None
        if not task_id:
            raise FirecRESTError("Submission was not accepted", body=payload)
        task = await self._poll_task(task_id, timeout_s=120.0)

        data = task.data if isinstance(task.data, dict) else {}
        jobid = data.get("jobid")
        if jobid is None:
            raise FirecRESTError(
                f"Submission task finished but carried no job id: {task.description}",
                body=task.data,
            )

        result = JobSubmit(
            jobid=str(jobid),
            task_id=task_id,
            job_file=data.get("job_file"),
            job_file_out=data.get("job_file_out"),
            job_file_err=data.get("job_file_err"),
        )
        self._remember_submission(result, system)
        return result

    async def get_job_status(self, job_id: str, system: str | None = None) -> JobAcct:
        """Return a job's accounting record.

        Uses ``/compute/acct``, which is what actually reports state. The
        obvious-looking ``GET /compute/jobs/{jobid}`` is *not* usable: it
        returns a task whose ``data`` is a stale queue string, and reports
        ``"Queued"`` even for a job that has already completed
        (see docs/hot-cache.md section 6).
        """
        system = system or self.config.system
        task = await self._resolve_task(
            "/compute/acct", params={"jobs": str(job_id)}, system=system
        )
        records = task.data if isinstance(task.data, list) else []
        for record in records:
            if str(record.get("jobid")) == str(job_id):
                return JobAcct.model_validate(record)
        raise FirecRESTError(
            f"No accounting record for job {job_id} on {system}. It may not have "
            f"started yet, or may have been purged from the scheduler.",
            body=task.data,
        )

    async def list_files(self, path: str, system: str | None = None) -> list[FileEntry]:
        """List a directory on the target system."""
        payload = await self._get(
            "/utilities/ls", params={"targetPath": path}, system=system or self.config.system
        )
        entries = payload.get("output", []) if isinstance(payload, dict) else []
        if isinstance(entries, dict):  # a single entry is returned unwrapped
            entries = [entries]
        return [FileEntry.model_validate(e) for e in entries]

    async def download_file(self, path: str, system: str | None = None) -> bytes:
        """Download a file's bytes.

        Note the query parameter: this endpoint wants ``sourcePath``, unlike
        every other ``/utilities/*`` call which wants ``targetPath``. Sending
        ``targetPath`` alone makes the server raise an unhandled ``TypeError``
        and answer HTTP 500, so this is not a stylistic choice.
        """
        response = await self._http.get(
            self.config.base_url + "/utilities/download",
            params={"sourcePath": path},
            headers=await self._headers(system or self.config.system),
        )
        if response.status_code != 200:
            raise FirecRESTError(
                "download_file failed", status=response.status_code, body=_safe_json(response)
            )
        return response.content

    async def get_job_log(self, job_id: str, system: str | None = None) -> JobLog:
        """Collect a job's stdout and stderr, and persist them under ``logs/``.

        FirecREST cannot map a bare job id back to its output paths, so this
        relies on the submission record written by :meth:`submit_job`. A job
        submitted by another process (or by the bundled demo client) will not be
        found — the raised error says so explicitly rather than guessing a path.
        """
        record = self._submissions().get(str(job_id))
        if not record:
            raise FirecRESTError(
                f"No submission record for job {job_id}. get_job_log can only read "
                f"logs of jobs submitted through this client; submit the job first, "
                f"or read the file directly with download_file() if you know its path."
            )

        log = JobLog(jobid=str(job_id))
        system = system or record.get("system") or self.config.system
        for stream, key in (("stdout", "job_file_out"), ("stderr", "job_file_err")):
            remote = record.get(key)
            if not remote:
                continue
            try:
                text = (await self.download_file(remote, system=system)).decode(
                    errors="replace"
                )
            except FirecRESTError as exc:
                logger.warning("could not read %s for job %s: %s", stream, job_id, exc)
                text = None
            setattr(log, stream, text)
            if text is not None:
                local = self._write_log(job_id, stream, text)
                setattr(log, f"{stream}_path", str(local))
        if log.stdout is None and log.stderr is None:
            log.note = (
                "Neither stdout nor stderr could be read. The job may still be "
                "running, or its output paths may have been cleaned up."
            )
        return log

    # -- submission bookkeeping -------------------------------------------

    @property
    def _submissions_file(self) -> Path:
        return self.config.log_dir / "submissions.json"

    def _submissions(self) -> dict[str, dict[str, Any]]:
        path = self._submissions_file
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning("submissions file at %s is unreadable; ignoring", path)
            return {}

    def _remember_submission(self, result: JobSubmit, system: str) -> None:
        """Record jobid → output paths so get_job_log can find them later."""
        self.config.log_dir.mkdir(parents=True, exist_ok=True)
        records = self._submissions()
        records[result.jobid] = {
            "system": system,
            "task_id": result.task_id,
            "job_file": result.job_file,
            "job_file_out": result.job_file_out,
            "job_file_err": result.job_file_err,
            "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self._submissions_file.write_text(json.dumps(records, indent=2, sort_keys=True))

    def _write_log(self, job_id: str, stream: str, text: str) -> Path:
        """Persist a job's output; DocMind ingests this directory."""
        self.config.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.log_dir / f"{job_id}.{stream}.log"
        path.write_text(text)
        return path


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text[:400]


def _unwrap(response: httpx.Response, path: str) -> Any:
    """Return the parsed body, or raise a FirecRESTError describing the problem."""
    if response.status_code >= 400:
        body = _safe_json(response)
        hint = ""
        if response.status_code == 400 and isinstance(body, dict):
            description = str(body.get("description", ""))
            if "No machine name given" in description:
                hint = (
                    " — the X-Machine-Name header was missing or empty; "
                    "check FIRECREST_SYSTEM"
                )
            elif "targetPath" in description:
                hint = " — this endpoint wants the targetPath query parameter"
        raise FirecRESTError(f"{path} failed{hint}", status=response.status_code, body=body)
    body = _safe_json(response)
    if isinstance(body, dict) and body.get("error"):
        raise FirecRESTError(f"{path} reported an error", body=body)
    return body


def new_job_id() -> str:
    """A short random suffix, handy for naming jobs uniquely."""
    return uuid.uuid4().hex[:8]
