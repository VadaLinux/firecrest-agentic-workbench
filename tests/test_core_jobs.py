"""Guardrail and audit tests without a FirecREST client or network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fcagent.core.audit import AuditLog
from fcagent.core.jobs import GuardrailDenied, JobService, PreparedSubmission
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
def service(tmp_path: Path) -> tuple[JobService, Client, Path]:
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
            limits=Limits(2, 30, frozenset({"debug", "batch"})),
            audit=AuditLog(audit_path),
            client=client,
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
        "approved_by": "reviewer",
        "working_directory": "/home/demo",
    }
    values.update(overrides)
    return values


def audit_record(path: Path) -> dict[str, object]:
    return json.loads(path.read_text().strip())


def test_prepare_submission_validates_before_the_mcp_adapter(
    service: tuple[JobService, Client, Path],
) -> None:
    jobs, client, audit_path = service
    prepared = jobs.prepare_submission(**request())
    assert isinstance(prepared, PreparedSubmission)
    assert prepared.script == "#!/bin/bash\necho hello 1 debug\n"
    assert prepared.partition == "debug"
    assert len(prepared.parameters_hash) == 64
    assert not client.calls
    assert not audit_path.exists()


def test_dry_run_validates_then_renders_without_remote_call(
    service: tuple[JobService, Client, Path],
) -> None:
    jobs, client, audit_path = service
    result = jobs.submit_job(**request(dry_run=True))
    assert result == {"script": "#!/bin/bash\necho hello 1 debug\n", "dry_run": True}
    assert not client.calls
    assert audit_record(audit_path)["outcome"] == "dry_run"


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
        jobs.prepare_submission(**data)
    assert not client.calls
    record = audit_record(audit_path)
    assert record["outcome"] == "denied"
    assert record["reason"] == "parameter_invalid:message"
    assert value not in audit_path.read_text()


@pytest.mark.parametrize(
    "kwargs, reason",
    [
        ({"template": "unknown"}, "template_not_allowed"),
        ({"approved_by": None}, "approved_by_required"),
        (
            {
                "parameters": {
                    "nodes": 3,
                    "time_minutes": 2,
                    "partition": "debug",
                    "message": "hi",
                }
            },
            "max_nodes_exceeded",
        ),
        (
            {
                "parameters": {
                    "nodes": 1,
                    "time_minutes": 31,
                    "partition": "debug",
                    "message": "hi",
                }
            },
            "max_time_exceeded",
        ),
        (
            {
                "parameters": {
                    "nodes": 1,
                    "time_minutes": 2,
                    "partition": "gpu",
                    "message": "hi",
                }
            },
            "partition_not_allowed",
        ),
    ],
)
def test_all_guardrail_rejections_are_audited_and_dry_run_is_not_a_bypass(
    service: tuple[JobService, Client, Path], kwargs: dict[str, object], reason: str
) -> None:
    jobs, client, audit_path = service
    with pytest.raises(GuardrailDenied, match=reason):
        jobs.prepare_submission(**request(**kwargs, dry_run=True))
    assert not client.calls
    assert audit_record(audit_path)["outcome"] == "denied"


def test_audit_and_error_metadata_never_include_configured_secret(
    service: tuple[JobService, Client, Path], caplog: pytest.LogCaptureFixture
) -> None:
    jobs, client, audit_path = service
    parameters = {
        "nodes": 1,
        "time_minutes": 2,
        "partition": "debug",
        "message": SECRET + ";",
    }
    with pytest.raises(GuardrailDenied):
        jobs.prepare_submission(**request(parameters=parameters))
    assert not client.calls
    assert SECRET not in audit_path.read_text()
    assert SECRET not in caplog.text


def test_error_audit_records_only_type_and_http_status(
    service: tuple[JobService, Client, Path],
) -> None:
    jobs, _, audit_path = service
    jobs.client = FailingClient()
    with pytest.raises(ClientFailure, match=SECRET):
        jobs.submit_job(**request())
    record = audit_record(audit_path)
    assert record["outcome"] == "error"
    assert record["exception_type"] == "ClientFailure"
    assert record["http_status"] == 503
    assert SECRET not in audit_path.read_text()


def test_settings_loads_paths_and_limits_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    config = root / "config"
    config.mkdir(parents=True)
    (config / "limits.yaml").write_text(
        "max_nodes: 4\nmax_time_minutes: 20\nallowed_partitions: [debug]\n"
    )
    monkeypatch.setenv("FCAGENT_ROOT", str(root))
    settings = CoreSettings.from_env()
    assert settings.template_directory == root / "templates"
    assert settings.audit_path == root / "logs/audit.jsonl"
    assert settings.limits == Limits(4, 20, frozenset({"debug"}))


def test_success_submits_only_after_validation(
    service: tuple[JobService, Client, Path],
) -> None:
    jobs, client, audit_path = service
    assert jobs.submit_job(**request()) == {"job_id": "42", "dry_run": False}
    assert client.calls == [
        ("#!/bin/bash\necho hello 1 debug\n", "cluster", "/home/demo", "debug")
    ]
    record = audit_record(audit_path)
    assert record["outcome"] == "submitted"
    assert "parameters" not in record
