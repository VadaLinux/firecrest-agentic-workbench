"""MCP submission-boundary tests for approved job templates."""

from __future__ import annotations

from pathlib import Path

import pytest

import server


@pytest.fixture
def core_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = Path(__file__).resolve().parents[2]
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("FCAGENT_ROOT", str(root))
    monkeypatch.setenv("FCAGENT_AUDIT_PATH", str(audit_path))
    return audit_path


async def test_submit_job_rejects_free_script_before_creating_client(
    core_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unavailable_client():
        raise AssertionError("the FirecREST client must not be reached")

    monkeypatch.setattr(server, "get_client", unavailable_client)
    result = await server.submit_job(
        script="#!/bin/bash\necho unsafe",
        system="cluster",
        working_directory="/home/demo",
    )
    assert result["ok"] is False
    assert result["error"] == "FirecRESTError"
    assert not core_env.exists()


async def test_submit_job_dry_run_applies_guardrails_without_creating_client(
    core_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unavailable_client():
        raise AssertionError("dry run must not reach FirecREST")

    monkeypatch.setattr(server, "get_client", unavailable_client)
    result = await server.submit_job(
        template="hello",
        parameters={
            "nodes": 1,
            "time_minutes": 1,
            "partition": "part01",
            "job_name": "test",
            "message": "bad;value",
        },
        approved_by="reviewer",
        system="cluster",
        working_directory="/home/demo",
        dry_run=True,
    )
    assert result["ok"] is False
    assert result["error"] == "GuardrailDenied"
    audit = core_env.read_text()
    assert '"outcome": "denied"' in audit
    assert "bad;value" not in audit


async def test_submit_job_dry_run_returns_only_the_rendered_approved_template(
    core_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unavailable_client():
        raise AssertionError("dry run must not reach FirecREST")

    monkeypatch.setattr(server, "get_client", unavailable_client)
    result = await server.submit_job(
        template="hello",
        parameters={
            "nodes": 1,
            "time_minutes": 1,
            "partition": "part01",
            "job_name": "test",
            "message": "hello",
        },
        approved_by="reviewer",
        system="cluster",
        working_directory="/home/demo",
        dry_run=True,
    )
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert "#SBATCH --nodes=1" in result["script"]
    assert not core_env.read_text().find('"outcome": "dry_run"') == -1
