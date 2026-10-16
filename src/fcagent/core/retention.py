"""Retention cleanup for audit lines, prepared records and reproducibility cards.

Dry-run is the default; nothing is deleted unless ``apply=True``. Paths come from
``CoreSettings``. Symlinks are never followed. Records whose shape or timestamps
cannot be understood are kept and reported, never deleted.
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import shutil
import stat
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fcagent.core.approval import PREPARED_ID
from fcagent.core.provenance import JOB_ID, TERMINAL_STATES

DEFAULT_RETENTION_DAYS = 90
CARD_FILES = ("provenance.json", "provenance.md")
DEAD_PREPARED = frozenset({"consumed", "rejected"})
LIVE_PREPARED = frozenset({"pending", "approved"})


class RetentionError(Exception):
    """Invalid configuration; the run stops without touching anything."""


def retention_days(environ: dict[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    raw = env.get("FCAGENT_RETENTION_DAYS")
    if raw is None:
        return DEFAULT_RETENTION_DAYS
    try:
        days = int(raw.strip())
    except ValueError as exc:
        raise RetentionError(
            f"FCAGENT_RETENTION_DAYS must be a positive integer, got {raw!r}"
        ) from exc
    if days <= 0:
        raise RetentionError(
            f"FCAGENT_RETENTION_DAYS must be a positive integer, got {raw!r}"
        )
    return days


def _parse_ts(value: Any) -> datetime | None:
    """ISO-8601 string, or epoch seconds (int/float) as FirecREST v2 reports them."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        if not math.isfinite(value) or value < 0:
            return None
        try:
            return datetime.fromtimestamp(value, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _is_plain_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _is_plain_dir(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except OSError:
        return False


@dataclass
class Report:
    apply: bool
    days: int
    cutoff: str
    prepared_deleted: list[str] = field(default_factory=list)
    cards_deleted: list[str] = field(default_factory=list)
    audit_lines_removed: int = 0
    audit_lines_kept: int = 0
    audit_skipped: str | None = None
    kept_unparseable: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _clean_prepared(
    prepared_dir: Path, cutoff: datetime, now: datetime, apply: bool, report: Report
) -> None:
    if not _is_plain_dir(prepared_dir):
        return
    for path in sorted(prepared_dir.glob("*.json")):
        if not PREPARED_ID.fullmatch(path.stem) or not _is_plain_file(path):
            continue
        try:
            record = json.loads(path.read_text())
            status = record["status"]
            expires = _parse_ts(record["context"]["expires_at"])
        except (OSError, ValueError, KeyError, TypeError):
            expires, status = None, None
        if expires is None or status not in DEAD_PREPARED | LIVE_PREPARED:
            report.kept_unparseable.append(f"prepared/{path.name}")
            continue
        if status in DEAD_PREPARED:
            eligible = expires < cutoff
        else:
            eligible = expires <= now
        if not eligible:
            continue
        report.prepared_deleted.append(path.stem)
        if apply:
            path.unlink()


def _clean_cards(jobs_dir: Path, cutoff: datetime, apply: bool, report: Report) -> None:
    if not _is_plain_dir(jobs_dir):
        return
    for directory in sorted(jobs_dir.iterdir()):
        name = directory.name
        if name in {".", ".."} or not JOB_ID.fullmatch(name):
            continue
        if not _is_plain_dir(directory):
            continue
        card_path = directory / "provenance.json"
        if not _is_plain_file(card_path):
            continue
        try:
            card = json.loads(card_path.read_text())
            state = card.get("state")
            raw_ended = (card.get("times") or {}).get("ended")
            ended = _parse_ts(raw_ended)
        except (OSError, ValueError, AttributeError):
            report.kept_unparseable.append(f"jobs/{name}")
            continue
        if state not in TERMINAL_STATES:
            continue
        if ended is None:
            if raw_ended is not None:
                report.kept_unparseable.append(f"jobs/{name}")
            continue
        if ended >= cutoff:
            continue
        report.cards_deleted.append(name)
        if apply:
            for filename in CARD_FILES:
                target = directory / filename
                if _is_plain_file(target):
                    target.unlink()
            try:
                directory.rmdir()
            except OSError:
                pass  # other files present: leave the directory


def _clean_audit(
    audit_path: Path, cutoff: datetime, apply: bool, report: Report
) -> None:
    if not _is_plain_file(audit_path):
        return
    before = audit_path.stat()
    raw = audit_path.read_bytes()
    kept: list[bytes] = []
    for line in raw.splitlines(keepends=True):
        try:
            moment = _parse_ts(json.loads(line)["ts"])
        except (ValueError, KeyError, TypeError):
            moment = None  # unparseable or no ts: keep
        if moment is not None and moment < cutoff:
            report.audit_lines_removed += 1
        else:
            kept.append(line)
    report.audit_lines_kept = len(kept)
    if not apply or report.audit_lines_removed == 0:
        return
    tmp = audit_path.with_name(f".{audit_path.name}.retention.tmp")
    tmp.write_bytes(b"".join(kept))
    shutil.copymode(audit_path, tmp)
    after = audit_path.stat()
    if (after.st_mtime_ns, after.st_size) != (before.st_mtime_ns, before.st_size):
        tmp.unlink()
        report.audit_skipped = "audit file changed during the run; nothing rewritten"
        report.audit_lines_removed = 0
        return
    os.replace(tmp, audit_path)


def run_retention(
    *,
    state_dir: Path,
    audit_path: Path,
    days: int,
    apply: bool = False,
    now: datetime | None = None,
) -> Report:
    moment = now or datetime.now(UTC)
    cutoff = moment - timedelta(days=days)
    report = Report(apply=apply, days=days, cutoff=cutoff.isoformat())
    if apply:
        state_dir.mkdir(parents=True, exist_ok=True)
        with (state_dir / ".lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                _clean_prepared(state_dir / "prepared", cutoff, moment, True, report)
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
    else:
        _clean_prepared(state_dir / "prepared", cutoff, moment, False, report)
    _clean_cards(state_dir / "jobs", cutoff, apply, report)
    _clean_audit(audit_path, cutoff, apply, report)
    return report
