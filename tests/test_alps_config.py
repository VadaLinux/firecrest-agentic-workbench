"""The shipped Alps profile (config/limits.alps.yaml + templates/alps/).

Values were validated live on production Alps, system daint, on 2026-11-03
(docs/hot-cache-v2.md §8). No network: only the guardrails are exercised.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from fcagent.core.audit import AuditLog
from fcagent.core.jobs import GuardrailDenied, JobService
from fcagent.core.settings import Limits

REPO = Path(__file__).resolve().parent.parent
ALPS_LIMITS = REPO / "config/limits.alps.yaml"
ALPS_TEMPLATES = REPO / "templates/alps"
PARAMS = {
    "nodes": 1,
    "time_minutes": 5,
    "partition": "debug",
    "job_name": "alps-hello",
    "message": "hello",
}


def _service(tmp_path: Path) -> JobService:
    return JobService(
        template_directory=ALPS_TEMPLATES,
        limits=Limits.from_yaml(ALPS_LIMITS),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        client=None,
    )


def test_alps_templates_stay_within_alps_limits() -> None:
    limits = yaml.safe_load(ALPS_LIMITS.read_text())
    for schema in ALPS_TEMPLATES.glob("*.yaml"):
        spec = yaml.safe_load(schema.read_text())["parameters"]
        assert spec["nodes"]["max"] <= limits["max_nodes"]
        assert spec["time_minutes"]["max"] <= limits["max_time_minutes"]
        assert set(spec["partition"]["values"]) <= set(limits["allowed_partitions"])
        script = schema.with_suffix(".sh").read_text()
        assert "--output=slurm-%j.out" in script  # what get_job_log falls back to
        for name in spec:
            assert "{{" + name + "}}" in script


def test_alps_hello_renders_for_daint_debug(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service._check_system("daint")
    service._check_working_directory("/users/course_941716d1")
    _, script = service.render(
        template="hello",
        parameters=dict(PARAMS),
        working_directory="/users/course_941716d1",
    )
    assert "#SBATCH --partition=debug" in script


@pytest.mark.parametrize(
    "override",
    [{"partition": "normal"}, {"nodes": 3}, {"time_minutes": 31}],
)
def test_alps_profile_refuses_production_partitions_and_big_jobs(
    tmp_path: Path, override: dict
) -> None:
    with pytest.raises(GuardrailDenied):
        _service(tmp_path).render(
            template="hello",
            parameters={**PARAMS, **override},
            working_directory="/users/course_941716d1",
        )


def test_alps_profile_refuses_other_systems_and_paths(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with pytest.raises(GuardrailDenied):
        service._check_system("cluster-slurm-api")
    with pytest.raises(GuardrailDenied):
        service._check_working_directory("/tmp")
