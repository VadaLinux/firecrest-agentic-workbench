"""Minimal JSON Lines audit log that never serializes secrets or payloads."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


class AuditLog:
    """Append allowlisted audit records to a JSON Lines file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def write(
        self,
        *,
        actor: str,
        action: str,
        system: str,
        template: str | None,
        parameters_hash: str | None,
        job_id: str | None,
        outcome: str,
        reason: str | None = None,
        exception_type: str | None = None,
        http_status: int | None = None,
    ) -> None:
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "actor": actor,
            "action": action,
            "system": system,
            "template": template,
            "parameters_hash": parameters_hash,
            "job_id": job_id,
            "outcome": outcome,
        }
        if reason is not None:
            record["reason"] = reason
        if exception_type is not None:
            record["exception_type"] = exception_type
        if http_status is not None:
            record["http_status"] = http_status
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as audit_file:
            audit_file.write(json.dumps(record, sort_keys=True) + "\n")
