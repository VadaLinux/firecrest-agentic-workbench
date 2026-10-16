"""Reproducibility cards: ``<state_dir>/<jobId>/provenance.{json,md}``.

A card never contains secrets, tokens or key material. Trace identifiers are
copied from real FirecREST exchanges; when none were observed they are ``null``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
from pathlib import Path
from typing import Any

from fcagent.core.errors import GuardrailDenied

JOB_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
SCHEMA_VERSION = 1
TERMINAL_STATES = frozenset(
    {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL"}
)


AI_AGENT_MAX_LENGTH = 128


def _clean_declared(value: str | None) -> str | None:
    """Normalize a declared label: printable text, trimmed, at most 128 chars.

    Unset, blank or non-printable values become ``None``; they are never guessed.
    """
    if value is None:
        return None
    value = value.strip()
    if not value or not value.isprintable():
        return None
    return value[:AI_AGENT_MAX_LENGTH]


def ai_agent_from_env() -> dict[str, str | None] | None:
    """AI client/model as *declared* by the operator; ``None`` when neither is set.

    Reads only ``FCAGENT_AI_CLIENT`` and ``FCAGENT_AI_MODEL`` (never keys, URLs or
    headers). A value that is unset is ``null``; nothing is inferred.
    """
    client = _clean_declared(os.environ.get("FCAGENT_AI_CLIENT"))
    model = _clean_declared(os.environ.get("FCAGENT_AI_MODEL"))
    if client is None and model is None:
        return None
    return {"client": client, "model": model}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def workbench_commit(root: Path) -> str | None:
    try:
        out = subprocess.run(  # noqa: S603
            ["git", "-C", str(root), "rev-parse", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def summarize(card: dict[str, Any]) -> dict[str, Any]:
    """Compact, key-facts-first view of a card.

    Only values already in the card are copied (nothing is invented, missing ones are
    ``None``). Hashes and the workbench commit are given in full. Tool output can be
    cut at the end, so this block goes BEFORE the full card in `get_provenance`.
    """
    versions = card.get("versions") or {}
    approval = card.get("approval") or {}
    hashes = card.get("hashes") or {}
    summary: dict[str, Any] = {
        "job_id": card.get("job_id"),
        "state": card.get("state"),
        "exit_code": card.get("exit_code"),
        "workbench_commit": versions.get("workbench_commit"),
        "approved_by": approval.get("approver"),
        "approved_at": approval.get("approved_at"),
        "template": card.get("template"),
        "template_sha256": hashes.get("template_sha256"),
        "script_sha256": hashes.get("script_sha256"),
        "outputs": [
            {"path": o.get("path"), "sha256": o.get("sha256")}
            for o in (card.get("outputs") or [])
        ],
        "firecrest_server": versions.get("firecrest_server"),
        "pyfirecrest": versions.get("pyfirecrest"),
    }
    if card.get("rerun_of") is not None:
        summary["rerun_of"] = card["rerun_of"]
    return summary


class ProvenanceStore:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir

    def _dir(self, job_id: str) -> Path:
        if not JOB_ID.fullmatch(job_id) or job_id in {".", ".."}:
            raise GuardrailDenied("provenance_not_found")
        return self.state_dir / "jobs" / job_id

    def read(self, job_id: str) -> dict[str, Any]:
        path = self._dir(job_id) / "provenance.json"
        if not path.is_file():
            raise GuardrailDenied("provenance_not_found")
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise GuardrailDenied("provenance_not_found") from exc

    def write(self, job_id: str, card: dict[str, Any]) -> Path:
        directory = self._dir(job_id)
        directory.mkdir(parents=True, exist_ok=True)
        card = {"schema": SCHEMA_VERSION, "job_id": job_id, **card}
        for name, text in (
            ("provenance.json", json.dumps(card, indent=2, sort_keys=True) + "\n"),
            ("provenance.md", render_markdown(card)),
        ):
            tmp = directory / f".{name}.{secrets.token_hex(4)}.tmp"
            tmp.write_text(text)
            os.replace(tmp, directory / name)
        return directory / "provenance.json"

    def update(self, job_id: str, **fields: Any) -> dict[str, Any]:
        card = self.read(job_id)
        card.update(fields)
        self.write(job_id, card)
        return card

    def has(self, job_id: str) -> bool:
        """True only for jobs submitted through this server (a card exists)."""
        try:
            return (self._dir(job_id) / "provenance.json").is_file()
        except GuardrailDenied:
            return False

    def record_status(self, job_id: str, job: dict[str, Any]) -> dict[str, Any] | None:
        """Update state/exit code/times from a status record; no-op if unmanaged."""
        if not self.has(job_id):
            return None
        card = self.read(job_id)
        times = dict(card.get("times") or {})
        if job.get("start_time") is not None:
            times["started"] = job["start_time"]
        state = job.get("state")
        if state in TERMINAL_STATES and job.get("end_time") is not None:
            times["ended"] = job["end_time"]
        return self.update(
            job_id, state=state, exit_code=job.get("exit_code"), times=times
        )

    def record_output(
        self, job_id: str, remote_path: str, payload: bytes
    ) -> dict[str, Any] | None:
        """Link a downloaded output to a managed job with its SHA-256."""
        if not self.has(job_id):
            return None
        card = self.read(job_id)
        outputs = [o for o in card.get("outputs", []) if o["path"] != remote_path]
        outputs.append(
            {"path": remote_path, "sha256": hashlib.sha256(payload).hexdigest()}
        )
        return self.update(job_id, outputs=outputs)

    def record_outputs(self, job_id: str, paths: list[Path]) -> dict[str, Any]:
        outputs = [
            {"path": str(p), "sha256": sha256_file(p)} for p in paths if p.is_file()
        ]
        return self.update(job_id, outputs=outputs)

    def initial_card(
        self,
        *,
        context: dict[str, Any],
        approval: dict[str, Any],
        prepared_id: str,
        job_id: str,
        versions: dict[str, Any],
        trace: dict[str, Any] | None,
        submitted_at: str,
    ) -> dict[str, Any]:
        return {
            "template": context["template"],
            "parameters": context["parameters"],
            "hashes": {
                "script_sha256": context["script_sha256"],
                "template_sha256": context["template_sha256"],
                "parameters_hash": context["parameters_hash"],
                "inputs": [],
            },
            "system": context["system"],
            "partition": context["partition"],
            "working_directory": context["working_directory"],
            "account": context["account"],
            "versions": versions,
            "times": {"submitted": submitted_at, "started": None, "ended": None},
            "state": None,
            "exit_code": None,
            "outputs": [],
            "trace": trace
            or {
                "x_request_id": None,
                "x_correlation_id": None,
                "note": "none observed",
            },
            "ai_agent": ai_agent_from_env(),
            "approval": {
                "prepared_id": prepared_id,
                "approver": approval["approver"],
                "approved_at": approval["approved_at"],
            },
        }


def render_markdown(card: dict[str, Any]) -> str:
    hashes = card.get("hashes", {})
    lines = [
        f"# Reproducibility card — job {card.get('job_id')}",
        "",
        f"- Template: `{card.get('template')}`",
        f"- Parameters: `{json.dumps(card.get('parameters'), sort_keys=True)}`",
        f"- System / partition: `{card.get('system')}` / `{card.get('partition')}`",
        f"- Working directory: `{card.get('working_directory')}`",
        f"- Rendered script SHA-256: `{hashes.get('script_sha256')}`",
        f"- Versions: `{json.dumps(card.get('versions'), sort_keys=True)}`",
        f"- Times: `{json.dumps(card.get('times'), sort_keys=True)}`",
        f"- State / exit code: `{card.get('state')}` / `{card.get('exit_code')}`",
        f"- Approved by: `{(card.get('approval') or {}).get('approver')}`"
        f" at `{(card.get('approval') or {}).get('approved_at')}`",
        f"- Trace IDs: `{json.dumps(card.get('trace'), sort_keys=True)}`",
        f"- AI agent (declared): `{json.dumps(card.get('ai_agent'), sort_keys=True)}`",
        "",
        "## Outputs",
    ]
    outputs = card.get("outputs") or []
    lines += [f"- `{o['path']}` — `{o['sha256']}`" for o in outputs] or ["- none yet"]
    return "\n".join(lines) + "\n"
