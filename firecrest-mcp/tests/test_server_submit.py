"""MCP submission-boundary tests: templates, human approval, provenance."""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from fcagent.core.approval import ApprovalStore, generate_keypair, load_private_key

import server
from client_v2 import FirecRESTClientV2

ROOT = Path(__file__).resolve().parents[2]
PARAMS = {
    "nodes": 1,
    "time_minutes": 1,
    "partition": "part01",
    "job_name": "test",
    "message": "hello",
}


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    private, public = tmp_path / "k" / "a.pem", tmp_path / "k" / "a.pub"
    generate_keypair(private, public)
    monkeypatch.setenv("FCAGENT_ROOT", str(ROOT))
    monkeypatch.setenv("FCAGENT_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("FCAGENT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("FCAGENT_APPROVAL_PUBLIC_KEY", str(public))
    return SimpleNamespace(
        audit=tmp_path / "audit.jsonl",
        store=ApprovalStore(tmp_path / "state"),
        key=load_private_key(private),
        state=tmp_path / "state",
    )


class FakeV2(FirecRESTClientV2):
    def __init__(self) -> None:  # no config / network
        self.calls: list[dict[str, Any]] = []
        self.last_trace = {"x_correlation_id": "cid-1", "x_request_id": None}

    async def submit_job(self, **kwargs: Any) -> Any:  # type: ignore[override]
        self.calls.append(kwargs)
        return SimpleNamespace(jobid="99", job_file_out="/o", job_file_err="/e")


def _approve(env: SimpleNamespace, pid: str) -> None:
    env.store.approve(pid, env.key, approver="alice", approver_uid=1000, sudo_user=None)


async def _prepare(**over: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "template": "hello",
        "parameters": dict(PARAMS),
        "system": "cluster",
        "working_directory": "/home/demo",
    }
    args.update(over)
    return await server.prepare_job(**args)


async def test_submit_job_has_no_free_script_or_approved_by_argument() -> None:
    names = inspect.signature(server.submit_job.fn if hasattr(server.submit_job, "fn") else server.submit_job).parameters
    assert "script" not in names and "approved_by" not in names


async def test_unknown_template_is_refused_with_next_step(
    env: SimpleNamespace,
) -> None:
    result = await _prepare(template="rm-everything")
    assert result["ok"] is False and result["reason"] == "template_not_allowed"
    assert "list_templates" in result["next_step"]


async def test_submit_before_approval_never_reaches_firecrest(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unavailable():
        raise AssertionError("the FirecREST client must not be reached")

    monkeypatch.setattr(server, "get_client", unavailable)
    prepared = await _prepare()
    assert prepared["ok"] is True and "fcagent approve" in prepared["next_step"]
    result = await server.submit_job(prepared_id=prepared["prepared_id"])
    assert result["ok"] is False and result["reason"] == "approval_pending"
    assert "fcagent approve" in result["next_step"]
    assert "approve_job" not in {t for t in dir(server) if "approve" in t}


async def test_full_flow_submits_once_and_writes_provenance(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeV2()

    async def client():
        return fake

    monkeypatch.setattr(server, "get_client", client)
    prepared = await _prepare()
    _approve(env, prepared["prepared_id"])
    result = await server.submit_job(prepared_id=prepared["prepared_id"])
    assert result["ok"] is True and result["jobid"] == "99"
    assert fake.calls[0]["script"].startswith("#!/bin/bash")
    again = await server.submit_job(prepared_id=prepared["prepared_id"])
    assert again["reason"] == "prepared_already_consumed" and len(fake.calls) == 1

    card = (await server.get_provenance("99"))["provenance"]
    assert card["approval"]["approver"] == "alice"
    assert card["trace"]["x_request_id"] is None
    assert card["trace"]["x_correlation_id"] == "cid-1"
    assert len(card["hashes"]["script_sha256"]) == 64
    assert "token" not in json.dumps(card).lower()

    rerun = await server.rerun_from_provenance("99")
    assert rerun["ok"] is True and rerun["rerun_of"] == "99"
    assert rerun["prepared_id"] != prepared["prepared_id"]
    retry = await server.submit_job(prepared_id=rerun["prepared_id"])
    assert retry["reason"] == "approval_pending"


async def test_rerun_refuses_when_template_changed(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeV2()

    async def client():
        return fake

    monkeypatch.setattr(server, "get_client", client)
    prepared = await _prepare()
    _approve(env, prepared["prepared_id"])
    await server.submit_job(prepared_id=prepared["prepared_id"])
    path = env.state / "jobs" / "99" / "provenance.json"
    card = json.loads(path.read_text())
    card["hashes"]["script_sha256"] = "0" * 64
    path.write_text(json.dumps(card))
    result = await server.rerun_from_provenance("99")
    assert result["ok"] is False and result["reason"] == "provenance_hash_mismatch"


async def test_unknown_provenance_is_reported(env: SimpleNamespace) -> None:
    result = await server.get_provenance("12345")
    assert result["ok"] is False and result["reason"] == "provenance_not_found"


async def test_list_templates_matches_schema(env: SimpleNamespace) -> None:
    result = await server.list_templates()
    hello = next(t for t in result["templates"] if t["template"] == "hello")
    schema = yaml.safe_load((ROOT / "templates/hello.yaml").read_text())
    assert hello["parameters"] == schema["parameters"]


def _doc(fn: Any) -> str:
    return inspect.getdoc(getattr(fn, "fn", fn)) or ""


def test_tool_descriptions_describe_the_approval_flow_not_free_scripts() -> None:
    submit, prepare = _doc(server.submit_job), _doc(server.prepare_job)
    for text in (submit, prepare, _doc(server.list_templates)):
        assert not re.search(r"full script|always send|#SBATCH|complete batch", text, re.I)
    assert "prepared_id" in submit and "fcagent approve" in submit
    assert "list_templates" in submit and "prepare_job" in submit
    assert "prepared_id" in prepare and "fcagent approve" in prepare
    assert "Never send a free" in prepare
    schema = yaml.safe_load((ROOT / "templates/hello.yaml").read_text())["parameters"]
    assert {"nodes", "partition"} <= set(schema)
