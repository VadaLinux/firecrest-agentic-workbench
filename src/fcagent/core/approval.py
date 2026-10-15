"""Human approval gate: persistent prepared records signed with Ed25519.

An agent calls ``prepare_job`` which stores a *pending* record. Only the
``fcagent approve`` CLI, run by a human, holds the private key and signs it.
``submit_job`` holds only the public key: it verifies the signature, the expiry
and the context, and consumes the record exactly once under an exclusive lock.

Known limit (decision D2): this is a *process* control. An agent with a shell
and sudo on the same host can read the private key or run the CLI itself.
See docs/reproducibility.md.
"""

from __future__ import annotations

import base64
import binascii
import fcntl
import json
import os
import re
import secrets
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from fcagent.core.audit import AuditLog
from fcagent.core.errors import GuardrailDenied

FORMAT_VERSION = 1
DEFAULT_TTL_SECONDS = 900
REJECT_REASONS = frozenset(
    {"not_intended", "wrong_parameters", "too_expensive", "policy", "other"}
)
PREPARED_ID = re.compile(r"^[A-Za-z0-9_-]{32,64}$")


def new_prepared_id() -> str:
    """A fresh id that never starts with ``-``, so `fcagent approve <id>` is safe.

    The accepted format (``PREPARED_ID``) is unchanged: existing records stay valid.
    """
    while True:
        candidate = secrets.token_urlsafe(24)
        if candidate[0] != "-":
            return candidate


STATUSES = frozenset({"pending", "approved", "rejected", "consumed"})
CONTEXT_FIELDS = frozenset(
    {
        "template",
        "template_sha256",
        "script_sha256",
        "parameters",
        "parameters_hash",
        "system",
        "partition",
        "working_directory",
        "account",
        "created_at",
        "expires_at",
        "node_hours",
    }
)


def canonical(obj: Any) -> bytes:
    """Deterministic, versioned serialization used for every signature."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def _now() -> datetime:
    return datetime.now(UTC)


def _check_key_file(path: Path, *, private: bool) -> Path:
    if not path.is_absolute():
        raise GuardrailDenied("approval_key_invalid")
    try:
        info = path.lstat()
    except OSError as exc:
        raise GuardrailDenied("approval_key_invalid") from exc
    forbidden = 0o077 if private else 0o022
    if not stat.S_ISREG(info.st_mode) or info.st_mode & forbidden:
        raise GuardrailDenied("approval_key_invalid")
    return path


def load_private_key(path: Path) -> Ed25519PrivateKey:
    """Load the approver key. Only the approval CLI calls this."""
    _check_key_file(path, private=True)
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (ValueError, TypeError, OSError) as exc:
        raise GuardrailDenied("approval_key_invalid") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise GuardrailDenied("approval_key_invalid")
    return key


def load_public_key(path: Path) -> Ed25519PublicKey:
    """Load the verification key used by the MCP server."""
    _check_key_file(path, private=False)
    try:
        key = serialization.load_pem_public_key(path.read_bytes())
    except (ValueError, TypeError, OSError) as exc:
        raise GuardrailDenied("approval_key_invalid") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise GuardrailDenied("approval_key_invalid")
    return key


def public_pem(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def generate_keypair(private_path: Path, public_path: Path) -> None:
    """Create a new key pair; refuses to overwrite. Run by the operator only."""
    if not private_path.is_absolute() or not public_path.is_absolute():
        raise GuardrailDenied("approval_key_invalid")
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    for path, data, mode in (
        (private_path, pem, 0o600),
        (public_path, public_pem(key.public_key()), 0o644),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)


def _signed_payload(record: dict[str, Any]) -> bytes:
    approval = record["approval"]
    return canonical(
        {
            "format": FORMAT_VERSION,
            "prepared_id": record["prepared_id"],
            "status": "approved",
            "context": record["context"],
            "approver": approval["approver"],
            "approver_uid": approval["approver_uid"],
            "sudo_user": approval["sudo_user"],
            "approved_at": approval["approved_at"],
        }
    )


class ApprovalStore:
    """File-backed store of prepared records under ``state_dir``."""

    def __init__(
        self,
        state_dir: Path,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        audit: AuditLog | None = None,
    ) -> None:
        self.audit = audit
        self.state_dir = state_dir
        self.ttl_seconds = ttl_seconds
        self.prepared_dir = state_dir / "prepared"
        self.consumed_dir = state_dir / "consumed"
        self.lock_path = state_dir / ".lock"

    # -- low level -----------------------------------------------------
    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.prepared_dir.mkdir(parents=True, exist_ok=True)
        self.consumed_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _path(self, prepared_id: str) -> Path:
        if not PREPARED_ID.fullmatch(prepared_id):
            raise GuardrailDenied("prepared_not_found")
        return self.prepared_dir / f"{prepared_id}.json"

    def _read(self, prepared_id: str) -> dict[str, Any]:
        path = self._path(prepared_id)
        if not path.exists():
            raise GuardrailDenied("prepared_not_found")
        try:
            record = json.loads(path.read_text())
            if (
                record["format"] != FORMAT_VERSION
                or record["prepared_id"] != prepared_id
                or record["status"] not in STATUSES
                or set(record["context"]) != CONTEXT_FIELDS
            ):
                raise ValueError("unexpected record")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise GuardrailDenied("prepared_record_invalid") from exc
        return record

    def _write(self, record: dict[str, Any]) -> None:
        path = self._path(record["prepared_id"])
        tmp = path.with_suffix(f".{secrets.token_hex(4)}.tmp")
        with tmp.open("w") as handle:
            handle.write(json.dumps(record, sort_keys=True))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)

    @staticmethod
    def _expired(record: dict[str, Any], now: datetime) -> bool:
        try:
            return now >= datetime.fromisoformat(record["context"]["expires_at"])
        except (ValueError, TypeError) as exc:
            raise GuardrailDenied("prepared_record_invalid") from exc

    def _audit(
        self,
        record: dict[str, Any],
        *,
        actor: str,
        action: str,
        outcome: str,
        reason: str | None = None,
        approved_by: str | None = None,
    ) -> None:
        """Allowlisted audit event; never writes parameters, scripts or keys."""
        if self.audit is None:
            return
        ctx = record["context"]
        self.audit.write(
            actor=actor,
            action=action,
            system=str(ctx.get("system", "")),
            template=ctx.get("template"),
            parameters_hash=ctx.get("parameters_hash"),
            job_id=None,
            outcome=outcome,
            reason=reason,
            prepared_id=record["prepared_id"],
            approved_by=approved_by,
        )

    # -- operations ----------------------------------------------------
    def create(self, context: dict[str, Any]) -> dict[str, Any]:
        record = {
            "format": FORMAT_VERSION,
            "prepared_id": new_prepared_id(),
            "status": "pending",
            "context": context,
            "approval": None,
        }
        with self._locked():
            self._write(record)
        return record

    def expiry(self, now: datetime | None = None) -> tuple[str, str]:
        start = now or _now()
        return start.isoformat(), (
            start + timedelta(seconds=self.ttl_seconds)
        ).isoformat()

    def get(self, prepared_id: str) -> dict[str, Any]:
        with self._locked():
            return self._read(prepared_id)

    def approve(
        self,
        prepared_id: str,
        private_key: Ed25519PrivateKey,
        *,
        approver: str,
        approver_uid: int,
        sudo_user: str | None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        moment = now or _now()
        with self._locked():
            record = self._read(prepared_id)
            if record["status"] != "pending":
                raise GuardrailDenied("approval_not_pending")
            if self._expired(record, moment):
                self._audit(
                    record,
                    actor=approver,
                    action="approve_job",
                    outcome="expired",
                    reason="approval_expired",
                )
                raise GuardrailDenied("approval_expired")
            record["status"] = "approved"
            record["approval"] = {
                "approver": approver,
                "approver_uid": approver_uid,
                "sudo_user": sudo_user,
                "approved_at": moment.isoformat(),
            }
            signature = private_key.sign(_signed_payload(record))
            record["approval"]["signature"] = base64.b64encode(signature).decode()
            self._write(record)
            self._audit(
                record,
                actor=approver,
                action="approve_job",
                outcome="approved",
                approved_by=approver,
            )
            return record

    def reject(
        self, prepared_id: str, *, rejector: str, reason: str = "other"
    ) -> dict[str, Any]:
        if reason not in REJECT_REASONS:
            raise GuardrailDenied("reject_reason_invalid")
        with self._locked():
            record = self._read(prepared_id)
            if record["status"] != "pending":
                raise GuardrailDenied("approval_not_pending")
            record["status"] = "rejected"
            self._write(record)
            self._audit(
                record,
                actor=rejector,
                action="reject_job",
                outcome="rejected",
                reason=reason,
            )
            return record

    def claim(
        self,
        prepared_id: str,
        public_key: Ed25519PublicKey,
        *,
        check: Callable[[dict[str, Any]], None] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Verify and consume ``approved -> consumed`` atomically (once only)."""
        moment = now or _now()
        with self._locked():
            if (self.consumed_dir / prepared_id).exists():
                raise GuardrailDenied("prepared_already_consumed")
            record = self._read(prepared_id)
            status = record["status"]
            if status == "pending":
                raise GuardrailDenied("approval_pending")
            if status == "rejected":
                raise GuardrailDenied("approval_rejected")
            if status == "consumed":
                raise GuardrailDenied("prepared_already_consumed")
            self._verify(record, public_key)
            if self._expired(record, moment):
                self._audit(
                    record,
                    actor="mcp",
                    action="submit_job",
                    outcome="expired",
                    reason="approval_expired",
                )
                raise GuardrailDenied("approval_expired")
            if check is not None:
                check(record["context"])
            (self.consumed_dir / prepared_id).write_text(moment.isoformat())
            record["status"] = "consumed"
            self._write(record)
            return record

    @staticmethod
    def _verify(record: dict[str, Any], public_key: Ed25519PublicKey) -> None:
        try:
            approval = record["approval"]
            signature = base64.b64decode(approval["signature"], validate=True)
            payload = _signed_payload(record)
        except (KeyError, TypeError, binascii.Error) as exc:
            raise GuardrailDenied("approval_signature_invalid") from exc
        try:
            public_key.verify(signature, payload)
        except InvalidSignature as exc:
            raise GuardrailDenied("approval_signature_invalid") from exc
