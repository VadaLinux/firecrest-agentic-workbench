"""Guardrail and audit tests without a FirecREST client or network."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from conftest import approve
from fcagent.core.approval import ApprovalStore, load_private_key, load_public_key
from fcagent.core.audit import AuditLog
from fcagent.core.jobs import GuardrailDenied, JobService
from fcagent.core.provenance import ProvenanceStore
from fcagent.core.settings import CoreSettings, Limits

SECRET = "fictional-secret-must-never-appear"


class Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []

    def submit_job(
        self, script: str, system: str, working_directory: str, partition: str
    ) -> str:
        self.calls.append((script, system, working_directory, partition))
        return "42"


class ClientFailure(RuntimeError):
    status = 503


class FailingClient:
    def submit_job(self, *_: object) -> str:
        raise ClientFailure(SECRET)


@pytest.fixture
def service(tmp_path: Path, keys: tuple[Path, Path]) -> tuple[JobService, Client, Path]:
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "hello.sh").write_text(
        "#!/bin/bash\necho {{message}} {{nodes}} {{partition}}\n"
    )
    (templates / "hello.yaml").write_text(
        "parameters:\n"
        "  nodes: {type: int, min: 1, max: 64}\n"
        "  time_minutes: {type: int, min: 1, max: 1440}\n"
        "  partition: {type: enum, values: [debug, batch, gpu]}\n"
        "  message: {type: string}\n"
    )
    audit_path = tmp_path / "logs" / "audit.jsonl"
    client = Client()
    return (
        JobService(
            template_directory=templates,
            limits=Limits(
                2,
                30,
                frozenset({"debug", "batch"}),
                frozenset({"cluster"}),
                frozenset({"/home/demo"}),
            ),
            audit=AuditLog(audit_path),
            client=client,
            store=ApprovalStore(tmp_path / "state"),
            public_key_path=keys[1],
        ),
        client,
        audit_path,
    )


def request(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "actor": "researcher",
        "system": "cluster",
        "template": "hello",
        "parameters": {
            "nodes": 1,
            "time_minutes": 2,
            "partition": "debug",
            "message": "hello",
        },
        "working_directory": "/home/demo",
    }
    values.update(overrides)
    return values


def audit_lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def prepared(jobs: JobService, **overrides: object) -> str:
    return str(jobs.prepare(**request(**overrides))["prepared_id"])  # type: ignore[arg-type]


def test_prepare_returns_readable_summary_with_node_hours(
    service: tuple[JobService, Client, Path],
) -> None:
    jobs, client, _ = service
    result = jobs.prepare(**request())  # type: ignore[arg-type]
    assert len(result["prepared_id"]) >= 32
    assert "estimated 0.0333 node-hours" in result["summary"]
    assert "partition debug" in result["summary"]
    assert "fcagent approve" in result["next_step"]
    assert not client.calls


def test_submit_without_approval_is_rejected(
    service: tuple[JobService, Client, Path],
) -> None:
    jobs, client, audit_path = service
    pid = prepared(jobs)
    with pytest.raises(GuardrailDenied, match="approval_pending") as info:
        jobs.submit(actor="researcher", prepared_id=pid)
    assert "fcagent approve" in info.value.next_step
    assert not client.calls
    assert audit_lines(audit_path)[-1]["outcome"] == "denied"


def test_full_approved_flow_submits_once(
    service: tuple[JobService, Client, Path], approver_key: object
) -> None:
    jobs, client, audit_path = service
    pid = prepared(jobs)
    approve(jobs.store, pid, approver_key)  # type: ignore[arg-type]
    assert jobs.submit(actor="researcher", prepared_id=pid) == {"job_id": "42"}
    assert client.calls[0][0] == "#!/bin/bash\necho hello 1 debug\n"
    last = audit_lines(audit_path)[-1]
    assert last["outcome"] == "submitted"
    assert last["approved_by"] == "alice"
    assert last["prepared_id"] == pid
    with pytest.raises(GuardrailDenied, match="prepared_already_consumed"):
        jobs.submit(actor="researcher", prepared_id=pid)
    assert len(client.calls) == 1


def test_expired_approval_is_rejected(
    service: tuple[JobService, Client, Path], approver_key: object
) -> None:
    jobs, client, _ = service
    jobs.store.ttl_seconds = -1  # type: ignore[union-attr]
    pid = prepared(jobs)
    with pytest.raises(GuardrailDenied, match="approval_expired"):
        approve(jobs.store, pid, approver_key)  # type: ignore[arg-type]
    jobs.store.ttl_seconds = 900  # type: ignore[union-attr]
    pid = prepared(jobs)
    approve(jobs.store, pid, approver_key)  # type: ignore[arg-type]
    path = jobs.store._path(pid)  # type: ignore[union-attr]
    record = json.loads(path.read_text())
    record["context"]["expires_at"] = "2000-01-01T00:00:00+00:00"
    path.write_text(json.dumps(record))
    with pytest.raises(GuardrailDenied, match="approval_signature_invalid"):
        jobs.submit(actor="researcher", prepared_id=pid)
    assert not client.calls


def test_template_changed_after_approval_is_rejected(
    service: tuple[JobService, Client, Path], approver_key: object
) -> None:
    jobs, client, _ = service
    pid = prepared(jobs)
    approve(jobs.store, pid, approver_key)  # type: ignore[arg-type]
    (jobs.template_directory / "hello.sh").write_text("#!/bin/bash\necho evil\n")
    with pytest.raises(GuardrailDenied, match="prepared_context_changed"):
        jobs.submit(actor="researcher", prepared_id=pid)
    assert not client.calls


@pytest.mark.parametrize("field", ["parameters", "partition", "working_directory"])
def test_altered_parameters_only_break_the_signature(
    service: tuple[JobService, Client, Path], approver_key: object, field: str
) -> None:
    jobs, client, _ = service
    pid = prepared(jobs)
    approve(jobs.store, pid, approver_key)  # type: ignore[arg-type]
    path = jobs.store._path(pid)  # type: ignore[union-attr]
    record = json.loads(path.read_text())
    if field == "parameters":
        record["context"]["parameters"]["message"] = "other"
    elif field == "partition":
        record["context"]["partition"] = "batch"
    else:
        record["context"]["working_directory"] = "/tmp"
    path.write_text(json.dumps(record))
    with pytest.raises(GuardrailDenied, match="approval_signature_invalid"):
        jobs.submit(actor="researcher", prepared_id=pid)
    assert not client.calls


def test_approval_of_another_prepared_id_is_rejected(
    service: tuple[JobService, Client, Path], approver_key: object
) -> None:
    jobs, client, _ = service
    first, second = prepared(jobs), prepared(jobs)
    approve(jobs.store, first, approver_key)  # type: ignore[arg-type]
    other = json.loads(jobs.store._path(first).read_text())  # type: ignore[union-attr]
    target = json.loads(jobs.store._path(second).read_text())  # type: ignore[union-attr]
    target["status"], target["approval"] = "approved", other["approval"]
    jobs.store._path(second).write_text(json.dumps(target))  # type: ignore[union-attr]
    with pytest.raises(GuardrailDenied, match="approval_signature_invalid"):
        jobs.submit(actor="researcher", prepared_id=second)
    assert not client.calls


def test_tampered_signature_and_wrong_key_are_rejected(
    service: tuple[JobService, Client, Path], tmp_path: Path
) -> None:
    jobs, client, _ = service
    pid = prepared(jobs)
    other = Ed25519PrivateKey.generate()
    approve(jobs.store, pid, other)  # type: ignore[arg-type]
    with pytest.raises(GuardrailDenied, match="approval_signature_invalid"):
        jobs.submit(actor="researcher", prepared_id=pid)
    assert not client.calls


def test_corrupt_or_unexpected_store_records_are_rejected(
    service: tuple[JobService, Client, Path], approver_key: object
) -> None:
    jobs, client, _ = service
    pid = prepared(jobs)
    path = jobs.store._path(pid)  # type: ignore[union-attr]
    path.write_text("{not json")
    with pytest.raises(GuardrailDenied, match="prepared_record_invalid"):
        jobs.submit(actor="researcher", prepared_id=pid)
    path.write_text(json.dumps({"format": 99}))
    with pytest.raises(GuardrailDenied, match="prepared_record_invalid"):
        jobs.submit(actor="researcher", prepared_id=pid)
    with pytest.raises(GuardrailDenied, match="prepared_not_found"):
        jobs.submit(actor="researcher", prepared_id="../etc/passwd")
    assert not client.calls


def test_rejected_record_cannot_be_submitted(
    service: tuple[JobService, Client, Path],
) -> None:
    jobs, client, _ = service
    pid = prepared(jobs)
    jobs.store.reject(pid, rejector="bob")  # type: ignore[union-attr]
    with pytest.raises(GuardrailDenied, match="approval_rejected"):
        jobs.submit(actor="researcher", prepared_id=pid)
    assert not client.calls


def test_concurrent_double_submit_only_one_wins(
    service: tuple[JobService, Client, Path], approver_key: object
) -> None:
    jobs, client, _ = service
    pid = prepared(jobs)
    approve(jobs.store, pid, approver_key)  # type: ignore[arg-type]
    outcomes: list[str] = []

    def attempt() -> None:
        try:
            jobs.submit(actor="researcher", prepared_id=pid)
            outcomes.append("ok")
        except GuardrailDenied as exc:
            outcomes.append(exc.reason)

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count("ok") == 1
    assert outcomes.count("prepared_already_consumed") == 7
    assert len(client.calls) == 1


def test_missing_public_key_blocks_submission(
    service: tuple[JobService, Client, Path], approver_key: object
) -> None:
    jobs, client, _ = service
    pid = prepared(jobs)
    approve(jobs.store, pid, approver_key)  # type: ignore[arg-type]
    jobs.public_key_path = None
    with pytest.raises(GuardrailDenied, match="approval_key_not_configured"):
        jobs.submit(actor="researcher", prepared_id=pid)
    assert not client.calls


def test_unsafe_key_files_are_refused(tmp_path: Path, keys: tuple[Path, Path]) -> None:
    private, public = keys
    private.chmod(0o644)
    with pytest.raises(GuardrailDenied, match="approval_key_invalid"):
        load_private_key(private)
    with pytest.raises(GuardrailDenied, match="approval_key_invalid"):
        load_private_key(Path("relative.pem"))
    link = tmp_path / "link.pub"
    link.symlink_to(public)
    with pytest.raises(GuardrailDenied, match="approval_key_invalid"):
        load_public_key(link)
    public.chmod(0o666)
    with pytest.raises(GuardrailDenied, match="approval_key_invalid"):
        load_public_key(public)


@pytest.mark.parametrize(
    "value", ["bad;rm", "bad|pipe", "bad$dollar", "bad`tick", "bad\nnewline"]
)
def test_unsafe_template_strings_are_denied_before_rendering(
    service: tuple[JobService, Client, Path], value: str
) -> None:
    jobs, client, audit_path = service
    data = request(
        parameters={
            "nodes": 1,
            "time_minutes": 2,
            "partition": "debug",
            "message": value,
        }
    )
    with pytest.raises(GuardrailDenied, match="parameter_invalid:message"):
        jobs.prepare(**data)  # type: ignore[arg-type]
    assert not client.calls
    record = audit_lines(audit_path)[-1]
    assert record["outcome"] == "denied"
    assert record["reason"] == "parameter_invalid:message"
    assert value not in audit_path.read_text()


def _params(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "nodes": 1,
        "time_minutes": 2,
        "partition": "debug",
        "message": "hi",
    }
    base.update(changes)
    return base


@pytest.mark.parametrize(
    "kwargs, reason",
    [
        ({"template": "unknown"}, "template_not_allowed"),
        ({"parameters": _params(nodes=3)}, "max_nodes_exceeded"),
        ({"parameters": _params(time_minutes=31)}, "max_time_exceeded"),
        ({"parameters": _params(partition="gpu")}, "partition_not_allowed"),
        ({"working_directory": "relative"}, "working_directory_invalid"),
    ],
)
def test_guardrail_rejections_are_audited_and_carry_next_step(
    service: tuple[JobService, Client, Path], kwargs: dict[str, object], reason: str
) -> None:
    jobs, client, audit_path = service
    with pytest.raises(GuardrailDenied, match=reason) as info:
        jobs.prepare(**request(**kwargs))  # type: ignore[arg-type]
    assert info.value.next_step
    assert not client.calls
    assert audit_lines(audit_path)[-1]["reason"] == reason


def test_state_never_contains_secrets_or_private_key(
    service: tuple[JobService, Client, Path], approver_key: object
) -> None:
    jobs, _, audit_path = service
    pid = prepared(jobs)
    approve(jobs.store, pid, approver_key)  # type: ignore[arg-type]
    text = jobs.store._path(pid).read_text() + audit_path.read_text()  # type: ignore[union-attr]
    assert "PRIVATE" not in text and SECRET not in text


def test_provenance_card_roundtrip_and_output_hashes(tmp_path: Path) -> None:
    store = ProvenanceStore(tmp_path)
    ctx = {
        "template": "hello",
        "parameters": {"nodes": 1},
        "script_sha256": "a" * 64,
        "template_sha256": "b" * 64,
        "parameters_hash": "c" * 64,
        "system": "cluster",
        "partition": "debug",
        "working_directory": "/home/demo",
        "account": None,
    }
    approval = {"approver": "alice", "approved_at": "2026-10-14T00:00:00+00:00"}
    card = store.initial_card(
        context=ctx,
        approval=approval,
        prepared_id="pid",
        job_id="7",
        versions={"pyfirecrest": "3.10.0"},
        trace=None,
        submitted_at="2026-10-14T00:00:01+00:00",
    )
    store.write("7", card)
    assert store.read("7")["trace"]["x_request_id"] is None
    out = tmp_path / "out.txt"
    out.write_text("data")
    updated = store.record_outputs("7", [out])
    assert updated["outputs"][0]["sha256"] == hashlib.sha256(b"data").hexdigest()
    assert "Reproducibility card" in (tmp_path / "jobs/7/provenance.md").read_text()
    with pytest.raises(GuardrailDenied, match="provenance_not_found"):
        store.read("../x")


def test_audit_trail_covers_prepare_approve_reject_expiry_submit(
    service: tuple[JobService, Client, Path], approver_key: object
) -> None:
    jobs, _, audit_path = service
    ok, rejected, expired = prepared(jobs), prepared(jobs), prepared(jobs)
    approve(jobs.store, ok, approver_key)  # type: ignore[arg-type]
    jobs.submit(actor="researcher", prepared_id=ok)
    jobs.store.reject(rejected, rejector="bob", reason="policy")  # type: ignore[union-attr]
    with pytest.raises(GuardrailDenied, match="reject_reason_invalid"):
        jobs.store.reject(expired, rejector="bob", reason="my password is hunter2")  # type: ignore[union-attr]
    jobs.store.ttl_seconds = -1  # type: ignore[union-attr]
    late = prepared(jobs)
    with pytest.raises(GuardrailDenied, match="approval_expired"):
        approve(jobs.store, late, approver_key)  # type: ignore[arg-type]
    records = audit_lines(audit_path)
    events = {(r["action"], r["outcome"]) for r in records}
    assert {
        ("prepare_job", "prepared"),
        ("approve_job", "approved"),
        ("reject_job", "rejected"),
        ("approve_job", "expired"),
        ("submit_job", "submitted"),
    } <= events
    approved = next(
        r
        for r in records
        if r["action"] == "approve_job" and r["outcome"] == "approved"
    )
    assert approved["actor"] == "alice" and approved["approved_by"] == "alice"
    assert approved["ts"] and approved["prepared_id"] == ok
    rej = next(r for r in records if r["action"] == "reject_job")
    assert rej["actor"] == "bob" and rej["reason"] == "policy"
    text = audit_path.read_text()
    for forbidden in (
        "hunter2",
        "PRIVATE",
        "signature",
        "#!/bin/bash",
        "message",
        "echo",
    ):
        assert forbidden not in text


def test_settings_reject_relative_state_and_key_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("FCAGENT_ROOT", str(root))
    monkeypatch.setenv("FCAGENT_STATE_DIR", "runs")
    with pytest.raises(ValueError, match="FCAGENT_STATE_DIR must be an absolute"):
        CoreSettings.from_env()
    monkeypatch.setenv("FCAGENT_STATE_DIR", "/var/lib/fcagent")
    monkeypatch.setenv("FCAGENT_APPROVAL_PUBLIC_KEY", "pub.pem")
    with pytest.raises(ValueError, match="FCAGENT_APPROVAL_PUBLIC_KEY must be"):
        CoreSettings.from_env()
    monkeypatch.setenv("FCAGENT_APPROVAL_PUBLIC_KEY", "/etc/fcagent/approval.pub")
    settings = CoreSettings.from_env()
    assert settings.state_dir == Path("/var/lib/fcagent")
    monkeypatch.delenv("FCAGENT_STATE_DIR")
    assert CoreSettings.from_env().state_dir == root / "runs"


def test_provenance_status_and_output_only_for_managed_jobs(tmp_path: Path) -> None:
    store = ProvenanceStore(tmp_path)
    assert store.record_status("5", {"state": "COMPLETED"}) is None
    assert store.record_output("5", "/x", b"d") is None
    assert not (tmp_path / "jobs").exists()
    store.write("5", {"times": {"submitted": "t0", "started": None, "ended": None}})
    store.record_status("5", {"state": "RUNNING", "start_time": 10})
    running = store.read("5")
    assert running["state"] == "RUNNING" and running["times"]["ended"] is None
    store.record_status(
        "5", {"state": "COMPLETED", "exit_code": 0, "start_time": 10, "end_time": 20}
    )
    done = store.read("5")
    assert (done["state"], done["exit_code"]) == ("COMPLETED", 0)
    assert done["times"] == {"submitted": "t0", "started": 10, "ended": 20}
    store.record_output("5", "/home/demo/out.txt", b"data")
    assert store.read("5")["outputs"] == [
        {"path": "/home/demo/out.txt", "sha256": hashlib.sha256(b"data").hexdigest()}
    ]


def test_limits_require_allowed_systems(tmp_path: Path) -> None:
    base = (
        "max_nodes: 2\nmax_time_minutes: 30\nallowed_partitions: [a]\n"
        "allowed_working_directories: [/home/x]\n"
    )
    missing = tmp_path / "m.yaml"
    missing.write_text(base)
    with pytest.raises(ValueError, match="allowed_systems"):
        Limits.from_yaml(missing)
    empty = tmp_path / "e.yaml"
    empty.write_text(base + "allowed_systems: []\n")
    with pytest.raises(ValueError, match="allowed_systems"):
        Limits.from_yaml(empty)
    ok = tmp_path / "ok.yaml"
    ok.write_text(base + "allowed_systems: [s1, s2]\n")
    assert Limits.from_yaml(ok).allowed_systems == frozenset({"s1", "s2"})


# ---- working directory allowlist, prepared_id, limit values (CRIGAMO-140) ----
from fcagent.core import approval as approval_module  # noqa: E402
from fcagent.core.settings import is_normalized_absolute, path_within  # noqa: E402


@pytest.mark.parametrize(
    "value",
    [
        "relative/x",
        "",
        "/a/../b",
        "/a/./b",
        "/a//b",
        "/a/b/",
        "/a\x00b",
        "/" + "x" * 600,
    ],
)
def test_non_normalized_paths_are_rejected_not_rewritten(value: str) -> None:
    assert is_normalized_absolute(value) is False
    assert path_within(value, frozenset({"/a"})) is False


def test_path_within_is_component_wise() -> None:
    allowed = frozenset({"/home/fireuser"})
    assert path_within("/home/fireuser", allowed)
    assert path_within("/home/fireuser/run1", allowed)
    assert not path_within("/home/fireuser2", allowed)
    assert not path_within("/home", allowed)
    assert not path_within("/home/fireuser/../x", allowed)


@pytest.mark.parametrize("entry", ["/", "relative", "/a/../b", "/a//b", "/a/"])
def test_limits_reject_bad_allowed_working_directories(
    tmp_path: Path, entry: str
) -> None:
    f = tmp_path / "l.yaml"
    f.write_text(
        "max_nodes: 2\nmax_time_minutes: 30\nallowed_partitions: [a]\n"
        f"allowed_systems: [s]\nallowed_working_directories: ['{entry}']\n"
    )
    with pytest.raises(ValueError, match="allowed_working_directories"):
        Limits.from_yaml(f)


def test_limits_require_allowed_working_directories(tmp_path: Path) -> None:
    f = tmp_path / "l.yaml"
    f.write_text(
        "max_nodes: 2\nmax_time_minutes: 30\nallowed_partitions: [a]\n"
        "allowed_systems: [s]\n"
    )
    with pytest.raises(ValueError, match="allowed_working_directories"):
        Limits.from_yaml(f)


def test_prepared_id_starting_with_dash_is_discarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = iter(["-" + "a" * 31, "-" + "b" * 31, "Zx" + "c" * 30])
    monkeypatch.setattr(
        approval_module.secrets, "token_urlsafe", lambda _n: next(values)
    )
    assert approval_module.new_prepared_id() == "Zx" + "c" * 30


def test_generated_prepared_ids_never_start_with_dash_and_match_the_format() -> None:
    for _ in range(5000):
        pid = approval_module.new_prepared_id()
        assert not pid.startswith("-")
        assert approval_module.PREPARED_ID.fullmatch(pid)
    assert approval_module.PREPARED_ID.fullmatch("-" + "a" * 31)  # old ids stay valid


@pytest.mark.parametrize(
    "wd, reason",
    [
        ("/etc", "working_directory_not_allowed"),
        ("/home/demo2", "working_directory_not_allowed"),
        ("/home/demo/../x", "working_directory_invalid"),
        ("/home//demo", "working_directory_invalid"),
        ("relative", "working_directory_invalid"),
    ],
)
def test_working_directory_is_refused_before_any_record(
    service: tuple[JobService, Client, Path], wd: str, reason: str
) -> None:
    jobs, client, audit_path = service
    secret_path = wd + "/MARKER-do-not-echo"
    with pytest.raises(GuardrailDenied) as info:
        jobs.prepare(**request(working_directory=secret_path))  # type: ignore[arg-type]
    assert info.value.reason == reason
    assert "MARKER-do-not-echo" not in info.value.next_step
    assert not client.calls
    assert not list((jobs.store.prepared_dir).glob("*.json"))  # type: ignore[union-attr]
    assert audit_lines(audit_path)[-1]["reason"] == reason


def test_allowed_working_directory_subpath_is_accepted(
    service: tuple[JobService, Client, Path],
) -> None:
    jobs, _, _ = service
    assert prepared(jobs)  # request() uses /home/demo
    jobs.prepare(**request(working_directory="/home/demo/run1"))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"parameters": _params(nodes=3)}, "max_nodes=2"),
        ({"parameters": _params(time_minutes=31)}, "max_time_minutes=30"),
        ({"parameters": _params(partition="gpu")}, "batch, debug"),
    ],
)
def test_next_step_reports_the_configured_limit_only(
    service: tuple[JobService, Client, Path], kwargs: dict[str, object], expected: str
) -> None:
    jobs, _, _ = service
    with pytest.raises(GuardrailDenied) as info:
        jobs.prepare(**request(**kwargs))  # type: ignore[arg-type]
    assert expected in info.value.next_step
    assert "gpu" not in info.value.next_step
