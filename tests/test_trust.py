"""AI-agent card field and retention cleanup (no network, no FirecREST)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fcagent.core.provenance import ProvenanceStore, ai_agent_from_env, render_markdown
from fcagent.core.retention import RetentionError, retention_days, run_retention

NOW = datetime(2026, 10, 15, tzinfo=UTC)
OLD = (NOW - timedelta(days=200)).isoformat()
NEW = (NOW - timedelta(days=5)).isoformat()
FUTURE = (NOW + timedelta(days=1)).isoformat()


def _card_ctx() -> dict:
    return {
        "template": "hello",
        "parameters": {},
        "script_sha256": "a",
        "template_sha256": "b",
        "parameters_hash": "c",
        "system": "s",
        "partition": "p",
        "working_directory": "/w",
        "account": None,
    }


def _initial(tmp_path: Path) -> dict:
    return ProvenanceStore(tmp_path).initial_card(
        context=_card_ctx(),
        approval={"approver": "a", "approved_at": "x"},
        prepared_id="p",
        job_id="1",
        versions={},
        trace=None,
        submitted_at="x",
    )


def test_ai_agent_both_set(monkeypatch, tmp_path):
    monkeypatch.setenv("FCAGENT_AI_CLIENT", "hermes")
    monkeypatch.setenv("FCAGENT_AI_MODEL", "some-model")
    card = _initial(tmp_path)
    assert card["ai_agent"] == {"client": "hermes", "model": "some-model"}
    assert "some-model" in render_markdown(card)


def test_ai_agent_none_and_partial(monkeypatch, tmp_path):
    monkeypatch.delenv("FCAGENT_AI_CLIENT", raising=False)
    monkeypatch.delenv("FCAGENT_AI_MODEL", raising=False)
    assert _initial(tmp_path)["ai_agent"] is None
    monkeypatch.setenv("FCAGENT_AI_MODEL", "m")
    assert ai_agent_from_env() == {"client": None, "model": "m"}


def test_ai_agent_long_and_unprintable(monkeypatch):
    monkeypatch.setenv("FCAGENT_AI_CLIENT", "x" * 500)
    monkeypatch.setenv("FCAGENT_AI_MODEL", "bad\x07\nvalue")
    assert ai_agent_from_env() == {"client": "x" * 128, "model": None}


def test_legacy_card_without_field_reads_and_renders(tmp_path):
    store = ProvenanceStore(tmp_path)
    store.write("2", {"template": "t", "state": "COMPLETED"})
    card = store.read("2")
    assert "ai_agent" not in card and card["schema"] == 1
    assert "AI agent" in render_markdown(card)


# -- retention ---------------------------------------------------------
def _prepared(state: Path, pid: str, status: str, expires: str) -> Path:
    directory = state / "prepared"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{pid}.json"
    path.write_text(json.dumps({"status": status, "context": {"expires_at": expires}}))
    return path


def _job(state: Path, job_id: str, job_state: str | None, ended: str | None) -> Path:
    directory = state / "jobs" / job_id
    directory.mkdir(parents=True)
    (directory / "provenance.json").write_text(
        json.dumps({"state": job_state, "times": {"ended": ended}})
    )
    (directory / "provenance.md").write_text("md")
    return directory


def _setup(tmp_path: Path):
    state = tmp_path / "state"
    pid = lambda c: c * 32  # noqa: E731
    paths = {
        "consumed_old": _prepared(state, pid("a"), "consumed", OLD),
        "rejected_new": _prepared(state, pid("b"), "rejected", NEW),
        "pending_expired": _prepared(state, pid("c"), "pending", OLD),
        "approved_live": _prepared(state, pid("d"), "approved", FUTURE),
        "approved_expired": _prepared(state, pid("e"), "approved", NEW),
    }
    _job(state, "10", "COMPLETED", OLD)
    _job(state, "11", "RUNNING", OLD)
    _job(state, "12", "COMPLETED", NEW)
    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        json.dumps({"ts": OLD, "n": 1})
        + "\nnot json at all\n"
        + json.dumps({"ts": NEW, "n": 2})
        + "\n"
    )
    return state, audit, paths


def test_dry_run_deletes_nothing(tmp_path):
    state, audit, paths = _setup(tmp_path)
    before = audit.read_text()
    report = run_retention(
        state_dir=state, audit_path=audit, days=90, apply=False, now=NOW
    )
    assert all(p.exists() for p in paths.values())
    assert (state / "jobs" / "10").exists() and audit.read_text() == before
    assert report.cards_deleted == ["10"] and report.audit_lines_removed == 1


def test_apply_only_eligible(tmp_path):
    state, audit, paths = _setup(tmp_path)
    run_retention(state_dir=state, audit_path=audit, days=90, apply=True, now=NOW)
    assert not paths["consumed_old"].exists()
    assert not paths["pending_expired"].exists()
    assert not paths["approved_expired"].exists()
    assert paths["rejected_new"].exists()
    assert paths["approved_live"].exists()  # not expired: preserved
    assert not (state / "jobs" / "10").exists()
    assert (state / "jobs" / "11").exists()  # non-terminal: preserved
    assert (state / "jobs" / "12").exists()
    lines = audit.read_text().splitlines()
    assert len(lines) == 2 and "not json at all" in lines[0]


def test_symlinks_ignored(tmp_path):
    state, audit, _ = _setup(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "provenance.json").write_text(
        json.dumps({"state": "COMPLETED", "times": {"ended": OLD}})
    )
    os.symlink(outside, state / "jobs" / "99")
    target = tmp_path / "target.json"
    target.write_text(
        json.dumps({"status": "consumed", "context": {"expires_at": OLD}})
    )
    os.symlink(target, state / "prepared" / ("f" * 32 + ".json"))
    run_retention(state_dir=state, audit_path=audit, days=90, apply=True, now=NOW)
    assert (outside / "provenance.json").exists() and target.exists()
    assert (state / "prepared" / ("f" * 32 + ".json")).is_symlink()


@pytest.mark.parametrize("value", ["0", "-3", "abc", "1.5", ""])
def test_invalid_retention_days_stops(value):
    with pytest.raises(RetentionError):
        retention_days({"FCAGENT_RETENTION_DAYS": value})


def test_retention_default():
    assert retention_days({}) == 90


def test_cards_with_epoch_ended(tmp_path):
    state = tmp_path / "state"
    old = int((NOW - timedelta(days=200)).timestamp())
    new = int((NOW - timedelta(days=5)).timestamp())
    _job(state, "20", "COMPLETED", old)
    _job(state, "21", "COMPLETED", new)
    _job(state, "22", "FAILED", "garbage")
    _job(state, "23", "FAILED", True)
    report = run_retention(
        state_dir=state, audit_path=tmp_path / "none", days=90, apply=True, now=NOW
    )
    assert report.cards_deleted == ["20"]
    assert not (state / "jobs" / "20").exists()
    assert (state / "jobs" / "21").exists()
    assert report.kept_unparseable == ["jobs/22", "jobs/23"]
