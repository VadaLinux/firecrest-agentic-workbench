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
from client_v2 import FirecRESTClientV2, JobStatus

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
        self.config = SimpleNamespace(
            system="cluster-slurm-api", working_directory="/home/fireuser"
        )

    async def submit_job(self, **kwargs: Any) -> Any:  # type: ignore[override]
        self.calls.append(kwargs)
        return SimpleNamespace(jobid="99", job_file_out="/o", job_file_err="/e")


def _approve(env: SimpleNamespace, pid: str) -> None:
    env.store.approve(pid, env.key, approver="alice", approver_uid=1000, sudo_user=None)


async def _prepare(**over: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "template": "hello",
        "parameters": dict(PARAMS),
        "system": "cluster-slurm-api",
        "working_directory": "/home/fireuser",
    }
    args.update(over)
    return await server.prepare_job(**args)


async def test_submit_job_has_no_free_script_or_approved_by_argument() -> None:
    names = inspect.signature(
        server.submit_job.fn if hasattr(server.submit_job, "fn") else server.submit_job
    ).parameters
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


async def _use_fake(monkeypatch: pytest.MonkeyPatch, fake: Any = None) -> Any:
    fake = fake or FakeV2()

    async def client():
        return fake

    monkeypatch.setattr(server, "get_client", client)
    return fake


async def test_list_templates_matches_schema(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _use_fake(monkeypatch)
    result = await server.list_templates()
    hello = next(t for t in result["templates"] if t["template"] == "hello")
    schema = yaml.safe_load((ROOT / "templates/hello.yaml").read_text())
    assert hello["parameters"] == schema["parameters"]


def _doc(fn: Any) -> str:
    return inspect.getdoc(getattr(fn, "fn", fn)) or ""


def test_tool_descriptions_describe_the_approval_flow_not_free_scripts() -> None:
    submit, prepare = _doc(server.submit_job), _doc(server.prepare_job)
    for text in (submit, prepare, _doc(server.list_templates)):
        assert not re.search(
            r"full script|always send|#SBATCH|complete batch", text, re.I
        )
    assert "prepared_id" in submit and "fcagent approve" in submit
    assert "list_templates" in submit and "prepare_job" in submit
    assert "prepared_id" in prepare and "fcagent approve" in prepare
    assert "Never send a free" in prepare
    schema = yaml.safe_load((ROOT / "templates/hello.yaml").read_text())["parameters"]
    assert {"nodes", "partition"} <= set(schema)


async def test_status_and_download_update_the_card_of_managed_jobs_only(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import hashlib

    from client_v2 import JobStatus

    fake = FakeV2()

    async def client():
        return fake

    async def status(job_id: str, *a: Any, **k: Any) -> JobStatus:
        return JobStatus(
            jobid=job_id, state="COMPLETED", exit_code=0, start_time=10, end_time=20
        )

    async def download(path: str, *a: Any, **k: Any) -> bytes:
        return b"result"

    fake.get_job_status = status  # type: ignore[method-assign]
    fake.download_file = download  # type: ignore[method-assign]
    monkeypatch.setattr(server, "get_client", client)
    monkeypatch.chdir(tmp_path)
    prepared = await _prepare()
    _approve(env, prepared["prepared_id"])
    await server.submit_job(prepared_id=prepared["prepared_id"])

    assert (await server.get_job_status("99"))["ok"] is True
    saved = await server.download_file("/home/fireuser/slurm-99.out", job_id="99")
    assert saved["ok"] is True
    card = (await server.get_provenance("99"))["provenance"]
    assert card["state"] == "COMPLETED" and card["exit_code"] == 0
    assert card["times"]["started"] == 10 and card["times"]["ended"] == 20
    assert card["outputs"] == [
        {
            "path": "/home/fireuser/slurm-99.out",
            "sha256": hashlib.sha256(b"result").hexdigest(),
        }
    ]

    # A job this server never submitted keeps working and gets no card.
    assert (await server.get_job_status("7"))["ok"] is True
    assert (await server.download_file("/x/y.txt", job_id="7"))["ok"] is True
    assert not (env.state / "jobs" / "7").exists()


class PyFirecrestLike:
    """Mimics pyfirecrest 3.10: ``server_version`` is a blocking *method*."""

    def __init__(self) -> None:
        self.version_calls = 0

    def server_version(self) -> str:
        self.version_calls += 1
        return "2.6.1"

    def submit(self, *a: Any, **k: Any) -> dict[str, Any]:
        return {"jobId": "99"}

    def job_metadata(self, *a: Any, **k: Any) -> list[dict[str, Any]]:
        return [{"standardOutput": "/o", "standardError": "/e"}]


def _real_client(fake: Any, tmp_path: Path) -> FirecRESTClientV2:
    from client_v2 import FirecRESTConfigV2

    config = FirecRESTConfigV2(
        base_url="http://x",
        token_url="http://x/t",
        client_id="id",
        client_secret="s",
        system="cluster",
        log_dir=tmp_path / "logs",
    )
    client = FirecRESTClientV2(config, factory=lambda _: fake)
    client.last_trace = {"x_correlation_id": "submit-cid", "x_request_id": None}
    return client


async def test_server_version_method_is_serialized_and_keeps_submit_trace(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = PyFirecrestLike()
    real = _real_client(fake, tmp_path)

    async def submit(**kwargs: Any) -> Any:
        return SimpleNamespace(jobid="99", job_file_out="/o", job_file_err="/e")

    monkeypatch.setattr(real, "submit_job", submit)

    async def client():
        return real

    monkeypatch.setattr(server, "get_client", client)
    prepared = await _prepare()
    _approve(env, prepared["prepared_id"])
    result = await server.submit_job(prepared_id=prepared["prepared_id"])
    assert result["ok"] is True and result["jobid"] == "99"
    assert "warnings" not in result
    assert fake.version_calls == 1
    card = (await server.get_provenance("99"))["provenance"]
    assert card["versions"]["firecrest_server"] == "2.6.1"
    assert card["trace"]["x_correlation_id"] == "submit-cid"
    assert (await server.rerun_from_provenance("99"))["ok"] is True


async def test_get_server_version_never_raises_or_returns_non_strings(
    tmp_path: Path,
) -> None:
    class Broken:
        def server_version(self) -> str:
            raise RuntimeError("boom")

    assert await _real_client(Broken(), tmp_path).get_server_version() is None
    assert (
        await _real_client(PyFirecrestLike(), tmp_path).get_server_version() == "2.6.1"
    )

    class Weird:
        server_version = 123

    assert await _real_client(Weird(), tmp_path).get_server_version() is None


async def test_provenance_failure_after_submit_still_reports_the_job(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeV2()

    async def client():
        return fake

    def broken_write(self: Any, *a: Any, **k: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(server, "get_client", client)
    monkeypatch.setattr(server.ProvenanceStore, "write", broken_write)
    prepared = await _prepare()
    _approve(env, prepared["prepared_id"])
    result = await server.submit_job(prepared_id=prepared["prepared_id"])
    assert result["ok"] is True and result["jobid"] == "99"
    assert result["provenance_error"] is True
    assert "provenance_error" in result["warnings"]
    assert "WAS submitted" in result["provenance"]
    again = await server.submit_job(prepared_id=prepared["prepared_id"])
    assert again["reason"] == "prepared_already_consumed" and len(fake.calls) == 1
    audit = env.audit.read_text()
    assert '"outcome": "submitted"' in audit


async def test_slow_server_version_times_out_but_submit_reports_the_job(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import threading
    import time

    release = threading.Event()

    class Slow(PyFirecrestLike):
        def server_version(self) -> str:
            release.wait(10)
            return "2.6.1"

    real = _real_client(Slow(), tmp_path)
    real.config = SimpleNamespace(timeout_s=0.2)  # type: ignore[assignment]

    async def submit(**kwargs: Any) -> Any:
        return SimpleNamespace(jobid="99", job_file_out="/o", job_file_err="/e")

    monkeypatch.setattr(real, "submit_job", submit)

    async def client():
        return real

    monkeypatch.setattr(server, "get_client", client)
    prepared = await _prepare()
    _approve(env, prepared["prepared_id"])
    started = time.monotonic()
    try:
        result = await server.submit_job(prepared_id=prepared["prepared_id"])
    finally:
        release.set()
    assert time.monotonic() - started < 3
    assert result["ok"] is True and result["jobid"] == "99"
    assert "warnings" not in result
    card = (await server.get_provenance("99"))["provenance"]
    assert card["versions"]["firecrest_server"] is None
    assert card["trace"]["x_correlation_id"] == "submit-cid"


async def test_requested_job_name_and_allocation_note(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Client(server.FirecRESTClientV2):  # type: ignore[misc]
        def __init__(self) -> None:  # no network
            pass

        async def get_job_status(self, job_id, system=None, time_window=None):
            return JobStatus(jobid=job_id, name="allocation", state="COMPLETED")

    async def fake_client():
        return Client()

    monkeypatch.setattr(server, "get_client", fake_client)
    monkeypatch.setattr(server, "_requested_job_name", lambda _id: "demo-hello")
    out = await server.get_job_status("6")
    assert out["job"]["name"] == "allocation"
    assert "not the name requested" in out["note"]
    assert out["requested_job_name"] == "demo-hello"


async def test_no_allocation_note_for_a_real_name(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Client(server.FirecRESTClientV2):  # type: ignore[misc]
        def __init__(self) -> None:
            pass

        async def get_job_status(self, job_id, system=None, time_window=None):
            return JobStatus(jobid=job_id, name="x", state="COMPLETED")

    async def fake_client():
        return Client()

    monkeypatch.setattr(server, "get_client", fake_client)
    out = await server.get_job_status("6")
    assert "note" not in out and "requested_job_name" not in out


async def test_list_templates_exposes_allowed_and_default_system(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _use_fake(monkeypatch)
    systems = (await server.list_templates())["systems"]
    assert systems == {"allowed": ["cluster-slurm-api"], "default": "cluster-slurm-api"}


async def test_prepare_without_system_uses_client_default_and_shows_it(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _use_fake(monkeypatch)
    prepared = await _prepare(system=None)
    assert prepared["ok"] is True
    assert "cluster-slurm-api" in prepared["summary"]
    record = env.store.get(prepared["prepared_id"])
    assert record["context"]["system"] == "cluster-slurm-api"


async def test_unknown_system_is_refused_before_any_record_and_audited(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _use_fake(monkeypatch)
    out = await _prepare(system="cluster" + "x" * 500)
    assert out["ok"] is False and out["reason"] == "unknown_system"
    assert "cluster-slurm-api" in out["next_step"]
    assert "x" * 100 not in json.dumps(out)
    prepared_dir = env.state / "prepared"
    assert not prepared_dir.exists() or not list(prepared_dir.iterdir())
    audit = [json.loads(line) for line in env.audit.read_text().splitlines()]
    assert audit[-1]["outcome"] == "denied" and audit[-1]["reason"] == "unknown_system"
    assert len(audit[-1]["system"]) <= 64


async def test_rerun_with_a_no_longer_allowed_system_creates_no_record(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = await _use_fake(monkeypatch)
    prepared = await _prepare()
    _approve(env, prepared["prepared_id"])
    assert (await server.submit_job(prepared_id=prepared["prepared_id"]))["ok"]
    card = server.ProvenanceStore(env.state).read("99")
    card["system"] = "retired-system"
    server.ProvenanceStore(env.state).write("99", card)
    before = len(list((env.state / "prepared").iterdir()))
    out = await server.rerun_from_provenance("99")
    assert out["ok"] is False and out["reason"] == "unknown_system"
    assert len(list((env.state / "prepared").iterdir())) == before
    assert len(fake.calls) == 1


class _FailingV2(FakeV2):
    async def submit_job(self, **kwargs: Any) -> Any:  # type: ignore[override]
        raise server.FirecRESTError("boom", status=404)


async def test_failed_submit_after_claim_reports_approval_consumed(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _use_fake(monkeypatch, _FailingV2())
    prepared = await _prepare()
    _approve(env, prepared["prepared_id"])
    out = await server.submit_job(prepared_id=prepared["prepared_id"])
    assert out["ok"] is False and out["http_status"] == 404
    assert out["approval_consumed"] is True and "prepare" in out["next_step"]
    retry = await server.submit_job(prepared_id=prepared["prepared_id"])
    assert retry["reason"] == "prepared_already_consumed"


async def test_refusals_before_the_claim_never_say_approval_consumed(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _use_fake(monkeypatch)
    prepared = await _prepare()
    pending = await server.submit_job(prepared_id=prepared["prepared_id"])
    assert pending["reason"] == "approval_pending"
    assert "approval_consumed" not in pending
    unknown = await server.submit_job(prepared_id="A" * 32)
    assert "approval_consumed" not in unknown


def test_default_system_outside_the_allowlist_fails_with_a_clear_error(
    env: SimpleNamespace,
) -> None:
    with pytest.raises(server.FirecRESTError, match="allowed_systems"):
        server._require_allowed_default("cluster")
    server._require_allowed_default("cluster-slurm-api")


async def test_list_templates_exposes_working_directory_allowed_and_default(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _use_fake(monkeypatch)
    wd = (await server.list_templates())["working_directory"]
    assert wd == {"allowed": ["/home/fireuser"], "default": "/home/fireuser"}


async def test_prepare_without_working_directory_uses_the_configured_default(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _use_fake(monkeypatch)
    prepared = await _prepare(working_directory=None)
    assert prepared["ok"] is True
    record = env.store.get(prepared["prepared_id"])
    assert record["context"]["working_directory"] == "/home/fireuser"


async def test_no_default_means_working_directory_is_required(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = await _use_fake(monkeypatch)
    fake.config.working_directory = None
    assert (await server.list_templates())["working_directory"]["default"] is None
    out = await _prepare(working_directory=None)
    assert out["ok"] is False and out["reason"] == "working_directory_invalid"
    assert not (env.state / "prepared").exists() or not list(
        (env.state / "prepared").iterdir()
    )


async def test_guessed_working_directory_is_refused_without_a_record(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _use_fake(monkeypatch)
    out = await _prepare(working_directory="/home/master")
    assert out["ok"] is False and out["reason"] == "working_directory_not_allowed"
    assert (
        "/home/fireuser" in out["next_step"] and "/home/master" not in out["next_step"]
    )
    assert not (env.state / "prepared").exists() or not list(
        (env.state / "prepared").iterdir()
    )


async def test_rerun_with_a_no_longer_allowed_directory_creates_no_record(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = await _use_fake(monkeypatch)
    prepared = await _prepare()
    _approve(env, prepared["prepared_id"])
    assert (await server.submit_job(prepared_id=prepared["prepared_id"]))["ok"]
    card = server.ProvenanceStore(env.state).read("99")
    card["working_directory"] = "/srv/elsewhere"
    server.ProvenanceStore(env.state).write("99", card)
    before = len(list((env.state / "prepared").iterdir()))
    out = await server.rerun_from_provenance("99")
    assert out["ok"] is False and out["reason"] == "working_directory_not_allowed"
    assert len(list((env.state / "prepared").iterdir())) == before
    assert len(fake.calls) == 1


def test_default_working_directory_outside_the_allowlist_fails_clearly(
    env: SimpleNamespace,
) -> None:
    server._require_allowed_working_directory(None)  # no default: nothing to check
    server._require_allowed_working_directory("/home/fireuser/run")
    with pytest.raises(server.FirecRESTError, match="allowed_working_directories"):
        server._require_allowed_working_directory("/home/master")


def test_client_config_reads_the_optional_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import client_v2

    base = {
        "FIRECREST_V2_BASE_URL": "https://f.test",
        "FIRECREST_V2_TOKEN_URL": "https://a.test/t",
        "FIRECREST_V2_CLIENT_ID": "c",
        "FIRECREST_V2_CLIENT_SECRET": "s",
        "FIRECREST_V2_SYSTEM": "cluster-slurm-api",
    }
    for key, value in base.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("FIRECREST_V2_WORKING_DIRECTORY", raising=False)
    none = tmp_path / "none.env"
    none.write_text("")
    assert client_v2.FirecRESTConfigV2.from_env(none).working_directory is None
    monkeypatch.setenv("FIRECREST_V2_WORKING_DIRECTORY", "/home/fireuser")
    assert (
        client_v2.FirecRESTConfigV2.from_env(none).working_directory == "/home/fireuser"
    )


async def test_get_provenance_puts_the_summary_before_the_full_card(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _use_fake(monkeypatch)
    prepared = await _prepare()
    _approve(env, prepared["prepared_id"])
    assert (await server.submit_job(prepared_id=prepared["prepared_id"]))["ok"]
    out = await server.get_provenance("99")
    # field order is what survives a tail cut: summary strictly before the card
    assert list(out) == ["ok", "summary", "provenance"]
    text = json.dumps(out)
    assert text.index('"summary"') < text.index('"provenance"')
    summary, card = out["summary"], out["provenance"]
    assert summary["workbench_commit"] == card["versions"]["workbench_commit"]
    assert summary["workbench_commit"]  # full value, not shortened
    assert summary["approved_by"] == "alice"
    assert summary["script_sha256"] == card["hashes"]["script_sha256"]
    assert summary["template_sha256"] == card["hashes"]["template_sha256"]
    assert summary["state"] == card["state"] and summary["job_id"] == "99"


async def test_summary_never_invents_missing_values() -> None:
    from fcagent.core.provenance import summarize

    s = summarize({"job_id": "1"})
    assert s["workbench_commit"] is None and s["outputs"] == []
    assert s["approved_by"] is None and "rerun_of" not in s
    assert summarize({"job_id": "2", "rerun_of": "1"})["rerun_of"] == "1"
