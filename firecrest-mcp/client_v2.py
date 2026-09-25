"""FirecREST v2 adapter backed exclusively by pyfirecrest 3.10.0.

The MCP boundary is async while ``firecrest.v2.Firecrest`` is synchronous.
This module isolates the synchronous client in worker threads and is the sole
v2 FirecREST implementation; core orchestration never imports pyfirecrest.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Callable

from pydantic import BaseModel

from client import FileEntry, FirecRESTError

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_OUTPUT_BYTES = 65536


class JobSubmit(BaseModel):
    """A v2 submission plus its output paths when metadata is available."""

    jobid: str
    job_file: str | None = None
    job_file_out: str | None = None
    job_file_err: str | None = None


class JobStatus(BaseModel):
    """One v2 job state normalized for the existing MCP response surface."""

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
    """Bounded job output with explicit truncation metadata."""

    jobid: str
    stdout: str | None = None
    stderr: str | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
    truncated: bool = False
    note: str | None = None


@dataclass(frozen=True)
class FirecRESTConfigV2:
    """Connection settings read from the independent ``FIRECREST_V2_*`` namespace."""

    base_url: str
    token_url: str
    client_id: str
    client_secret: str
    system: str
    timeout_s: float = DEFAULT_TIMEOUT_S
    log_dir: Path = field(default_factory=lambda: Path("logs"))
    verify_ssl: bool = True

    @classmethod
    def from_env(
        cls, env_file: str | os.PathLike[str] | None = None
    ) -> "FirecRESTConfigV2":
        _load_dotenv(Path(env_file) if env_file else Path(__file__).with_name(".env"))

        def need(name: str) -> str:
            if value := os.environ.get(name):
                return value
            raise FirecRESTError(f"{name} is not set; configure firecrest-mcp/.env.")

        return cls(
            base_url=need("FIRECREST_V2_BASE_URL").rstrip("/"),
            token_url=need("FIRECREST_V2_TOKEN_URL"),
            client_id=need("FIRECREST_V2_CLIENT_ID"),
            client_secret=need("FIRECREST_V2_CLIENT_SECRET"),
            system=need("FIRECREST_V2_SYSTEM"),
            timeout_s=float(os.environ.get("FIRECREST_V2_TIMEOUT", DEFAULT_TIMEOUT_S)),
            log_dir=Path(os.environ.get("FIRECREST_V2_LOG_DIR", "logs")),
            verify_ssl=os.environ.get("FIRECREST_V2_VERIFY_SSL", "true").lower()
            not in {"0", "false", "no"},
        )


def _load_dotenv(path: Path) -> None:
    """Load local configuration without logging its values."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _find_job(records: list[dict[str, Any]], job_id: str) -> dict[str, Any]:
    for record in records:
        if str(record.get("jobId")) == str(job_id):
            return record
    raise FirecRESTError("Job record not found")


def _http_status(exc: Exception) -> int | None:
    responses = getattr(exc, "responses", None)
    return getattr(responses[-1], "status_code", None) if responses else None


class FirecRESTClientV2:
    """Async-compatible adapter around ``firecrest.v2.Firecrest``.

    A factory can be injected for no-network unit tests. The production factory
    uses pyfirecrest's ``ClientCredentialsAuth`` and synchronous v2 client.
    """

    def __init__(
        self,
        config: FirecRESTConfigV2,
        *,
        factory: Callable[[FirecRESTConfigV2], Any] | None = None,
    ) -> None:
        self.config = config
        self._client = (factory or self._create_client)(config)

    @staticmethod
    def _create_client(config: FirecRESTConfigV2) -> Any:
        from firecrest import ClientCredentialsAuth
        from firecrest.v2 import Firecrest

        auth = ClientCredentialsAuth(
            config.client_id, config.client_secret, config.token_url
        )
        auth.timeout = config.timeout_s
        auth.disable_client_logging = True
        client = Firecrest(config.base_url, auth, verify=config.verify_ssl)
        client.disable_client_logging = True
        return client

    async def aclose(self) -> None:
        if close := getattr(self._client, "close_session", None):
            await asyncio.to_thread(close)

    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return await asyncio.to_thread(
                getattr(self._client, method), *args, **kwargs
            )
        except Exception as exc:
            raise FirecRESTError(
                f"FirecREST v2 {method} failed", status=_http_status(exc)
            ) from exc

    async def list_systems(self) -> list[dict[str, Any]]:
        """Return systems using ``Firecrest.systems()``."""
        return await self._call("systems")

    async def submit_job(
        self,
        script: str | None = None,
        system: str | None = None,
        account: str | None = None,
        *,
        working_directory: str | None = None,
        partition: str | None = None,
        script_path: str | None = None,
    ) -> JobSubmit:
        """Submit with ``Firecrest.submit`` after the core guards approve it."""
        if bool(script) == bool(script_path):
            raise FirecRESTError(
                "submit_job needs exactly one of script or script_path"
            )
        if not working_directory or not working_directory.startswith("/"):
            raise FirecRESTError("submit_job needs an absolute working_directory")
        system = system or self.config.system
        response = await self._call(
            "submit",
            system,
            working_directory,
            script_str=script,
            script_remote_path=script_path,
            account=account,
            partition=partition,
        )
        job_id = str(response.get("jobid", response.get("jobId", "")))
        if not job_id:
            raise FirecRESTError("FirecREST accepted no job id")
        try:
            metadata = await self.get_job_metadata(job_id, system)
        except FirecRESTError:
            metadata = {}
        return JobSubmit(
            jobid=job_id,
            job_file=script_path,
            job_file_out=metadata.get("standardOutput"),
            job_file_err=metadata.get("standardError"),
        )

    async def get_job_status(self, job_id: str, system: str | None = None) -> JobStatus:
        """Read state using ``Firecrest.job_info(system, job_id)``."""
        record = _find_job(
            await self._call("job_info", system or self.config.system, job_id), job_id
        )
        status = record.get("status") or {}
        times = record.get("time") or {}
        return JobStatus(
            jobid=str(record.get("jobId", job_id)),
            name=record.get("name"),
            state=status.get("state"),
            state_reason=status.get("stateReason"),
            exit_code=status.get("exitCode"),
            interrupt_signal=status.get("interruptSignal"),
            partition=record.get("partition"),
            nodes=record.get("nodes"),
            account=record.get("account"),
            cluster=record.get("cluster"),
            group=record.get("group"),
            user=record.get("user"),
            working_directory=record.get("workingDirectory"),
            priority=record.get("priority"),
            elapsed_time=times.get("elapsed"),
            start_time=times.get("start"),
            end_time=times.get("end"),
        )

    async def get_job_metadata(
        self, job_id: str, system: str | None = None
    ) -> dict[str, Any]:
        """Read stdout/stderr paths using ``Firecrest.job_metadata``."""
        records = await self._call("job_metadata", system or self.config.system, job_id)
        return _find_job(records, job_id)

    async def cancel_job(
        self, job_id: str, system: str | None = None
    ) -> dict[str, Any]:
        """Cancel using ``Firecrest.cancel_job(system, job_id)``."""
        return await self._call("cancel_job", system or self.config.system, job_id)

    async def list_files(self, path: str, system: str | None = None) -> list[FileEntry]:
        records = await self._call("list_files", system or self.config.system, path)
        return [
            FileEntry(
                name=record.get("name", ""),
                type=record.get("type"),
                size=str(record.get("size"))
                if record.get("size") is not None
                else None,
                permissions=record.get("permissions"),
                user=record.get("user"),
                group=record.get("group"),
                last_modified=record.get("lastModified"),
                link_target=record.get("linkTarget"),
            )
            for record in records
        ]

    async def download_file(self, path: str, system: str | None = None) -> bytes:
        """Download using pyfirecrest into a temporary local file."""
        with NamedTemporaryFile() as output:
            await self._call(
                "download", system or self.config.system, path, output.name
            )
            return Path(output.name).read_bytes()

    async def get_job_log(self, job_id: str, system: str | None = None) -> JobLog:
        """Read at most 64 KiB per stream, preferring ``job_metadata`` paths.

        The "-api" (REST scheduler) system cannot expose job metadata
        (``job_metadata`` answers 501 there — see docs/local-env.md), so when
        metadata yields no paths this falls back to Slurm's own default
        ``<working_directory>/slurm-<jobid>.out``, same as scripts/smoke_local.py.
        """
        system = system or self.config.system
        try:
            metadata = await self.get_job_metadata(job_id, system)
        except FirecRESTError:
            metadata = {}
        if not metadata.get("standardOutput") and not metadata.get("standardError"):
            status = await self.get_job_status(job_id, system)
            if status.working_directory:
                metadata = {
                    "standardOutput": f"{status.working_directory}/slurm-{job_id}.out"
                }
        log = JobLog(jobid=str(job_id))
        for metadata_key, stream in (
            ("standardOutput", "stdout"),
            ("standardError", "stderr"),
        ):
            if remote_path := metadata.get(metadata_key):
                output = await self._call(
                    "head", system, remote_path, num_bytes=DEFAULT_OUTPUT_BYTES
                )
                text = (
                    output.get("content", "")
                    if isinstance(output, dict)
                    else str(output)
                )
                setattr(log, stream, text)
                setattr(
                    log, f"{stream}_path", str(self._write_log(job_id, stream, text))
                )
        log.truncated = any(
            text is not None and len(text.encode()) >= DEFAULT_OUTPUT_BYTES
            for text in (log.stdout, log.stderr)
        )
        if log.stdout is None and log.stderr is None:
            log.note = "No readable stdout or stderr was returned."
        return log

    def _write_log(self, job_id: str, stream: str, text: str) -> Path:
        self.config.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.log_dir / f"v2-{job_id}.{stream}.log"
        path.write_text(text)
        return path
