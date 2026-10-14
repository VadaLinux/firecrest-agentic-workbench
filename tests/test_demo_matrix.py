"""The repo's real matrix template renders within the guardrails (no network)."""

from pathlib import Path

import pytest
import yaml

from fcagent.core.audit import AuditLog
from fcagent.core.errors import GuardrailDenied
from fcagent.core.jobs import JobService
from fcagent.core.settings import Limits

REPO = Path(__file__).resolve().parent.parent
PARAMS = {
    "nodes": 1,
    "time_minutes": 10,
    "partition": "part01",
    "job_name": "matrix-demo",
    "size": 40,
}


def _service(tmp_path):
    raw = yaml.safe_load((REPO / "config/limits.yaml").read_text())
    limits = Limits(
        max_nodes=raw["max_nodes"],
        max_time_minutes=raw["max_time_minutes"],
        allowed_partitions=tuple(raw["allowed_partitions"]),
    )
    return JobService(
        template_directory=REPO / "templates",
        limits=limits,
        audit=AuditLog(tmp_path / "audit.jsonl"),
        client=None,
    )


def test_matrix_listed_and_rendered(tmp_path):
    svc = _service(tmp_path)
    assert "matrix" in {t["template"] for t in svc.list_templates()}
    _, script = svc.render(
        template="matrix", parameters=PARAMS, working_directory="/tmp/demo"
    )
    assert "--job-name=matrix-demo" in script and "{{" not in script


@pytest.mark.parametrize(
    ("override", "reason"),
    [({"nodes": 4}, "max_nodes_exceeded"), ({"time_minutes": 31}, None)],
)
def test_matrix_out_of_policy_is_denied(tmp_path, override, reason):
    svc = _service(tmp_path)
    with pytest.raises(GuardrailDenied) as exc:
        svc.render(
            template="matrix",
            parameters={**PARAMS, **override},
            working_directory="/tmp/demo",
        )
    if reason:
        assert reason in str(exc.value)
