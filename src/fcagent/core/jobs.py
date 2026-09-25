"""Approved-template job submission guards with dependency-injected execution."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

from fcagent.core.audit import AuditLog
from fcagent.core.settings import Limits

SAFE_STRING = re.compile(r"^[A-Za-z0-9_.-]+$")


class JobClient(Protocol):
    """The MCP v2 adapter capability used only after every guardrail succeeds."""

    def submit_job(
        self, script: str, system: str, working_directory: str, partition: str
    ) -> str: ...


class GuardrailDenied(ValueError):
    """A submission was rejected before template rendering or a remote call."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class TemplateSpec:
    """An allowlisted template and its restricted parameter schema."""

    name: str
    path: Path
    parameters: dict[str, dict[str, Any]]


def parameters_hash(parameters: dict[str, Any]) -> str:
    """Stable identifier for audit records; parameter values are never logged."""
    encoded = json.dumps(parameters, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class PreparedSubmission:
    """A guardrail-approved script with its safe audit identifier."""

    script: str
    parameters_hash: str
    partition: str


class JobService:
    """Validate, render, audit and optionally submit an approved job template."""

    def __init__(
        self,
        *,
        template_directory: Path,
        limits: Limits,
        audit: AuditLog,
        client: JobClient | None,
    ) -> None:
        self.template_directory = template_directory.resolve()
        self.limits = limits
        self.audit = audit
        self.client = client

    def prepare_submission(
        self,
        *,
        actor: str,
        system: str,
        template: str,
        parameters: dict[str, Any],
        approved_by: str | None,
        working_directory: str,
        dry_run: bool = False,
    ) -> PreparedSubmission | dict[str, Any]:
        """Run every guardrail and render before an MCP handler contacts FirecREST."""
        digest = parameters_hash(parameters)
        try:
            spec = self._load_template(template)
            self._validate(parameters, spec, approved_by, working_directory)
            script = self._render(spec, parameters)
        except GuardrailDenied as exc:
            self.audit.write(
                actor=actor,
                action="submit_job",
                system=system,
                template=template,
                parameters_hash=digest,
                job_id=None,
                outcome="denied",
                reason=exc.reason,
            )
            raise
        if dry_run:
            self.audit.write(
                actor=actor,
                action="submit_job",
                system=system,
                template=template,
                parameters_hash=digest,
                job_id=None,
                outcome="dry_run",
            )
            return {"script": script, "dry_run": True}
        return PreparedSubmission(script, digest, str(parameters["partition"]))

    def submit_job(
        self,
        *,
        actor: str,
        system: str,
        template: str,
        parameters: dict[str, Any],
        approved_by: str | None,
        working_directory: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Apply all guards, then render or submit through the injected adapter."""
        digest = parameters_hash(parameters)
        try:
            spec = self._load_template(template)
            self._validate(parameters, spec, approved_by, working_directory)
            script = self._render(spec, parameters)
        except GuardrailDenied as exc:
            self.audit.write(
                actor=actor,
                action="submit_job",
                system=system,
                template=template,
                parameters_hash=digest,
                job_id=None,
                outcome="denied",
                reason=exc.reason,
            )
            raise

        if dry_run:
            self.audit.write(
                actor=actor,
                action="submit_job",
                system=system,
                template=template,
                parameters_hash=digest,
                job_id=None,
                outcome="dry_run",
            )
            return {"script": script, "dry_run": True}

        try:
            if self.client is None:
                raise RuntimeError("No job client is configured")
            job_id = self.client.submit_job(
                script, system, working_directory, str(parameters["partition"])
            )
        except Exception as exc:
            self.audit.write(
                actor=actor,
                action="submit_job",
                system=system,
                template=template,
                parameters_hash=digest,
                job_id=None,
                outcome="error",
                exception_type=type(exc).__name__,
                http_status=getattr(exc, "status", None),
            )
            raise
        self.audit.write(
            actor=actor,
            action="submit_job",
            system=system,
            template=template,
            parameters_hash=digest,
            job_id=job_id,
            outcome="submitted",
        )
        return {"job_id": job_id, "dry_run": False}

    def _load_template(self, template: str) -> TemplateSpec:
        if not SAFE_STRING.fullmatch(template):
            raise GuardrailDenied("template_not_allowed")
        path = (self.template_directory / f"{template}.sh").resolve()
        if path.parent != self.template_directory or not path.is_file():
            raise GuardrailDenied("template_not_allowed")
        schema_path = path.with_suffix(".yaml")
        if not schema_path.is_file():
            raise GuardrailDenied("template_schema_missing")
        raw = yaml.safe_load(schema_path.read_text()) or {}
        return TemplateSpec(template, path, raw.get("parameters", {}))

    def _validate(
        self,
        parameters: dict[str, Any],
        spec: TemplateSpec,
        approved_by: str | None,
        working_directory: str,
    ) -> None:
        if not approved_by or not SAFE_STRING.fullmatch(approved_by):
            raise GuardrailDenied("approved_by_required")
        if not working_directory.startswith("/"):
            raise GuardrailDenied("working_directory_invalid")
        if set(parameters) != set(spec.parameters):
            raise GuardrailDenied("template_parameters_invalid")
        for name, definition in spec.parameters.items():
            self._validate_value(name, parameters[name], definition)
        nodes = parameters.get("nodes")
        if isinstance(nodes, int) and nodes > self.limits.max_nodes:
            raise GuardrailDenied("max_nodes_exceeded")
        minutes = parameters.get("time_minutes")
        if isinstance(minutes, int) and minutes > self.limits.max_time_minutes:
            raise GuardrailDenied("max_time_exceeded")
        partition = parameters.get("partition")
        if partition is not None and partition not in self.limits.allowed_partitions:
            raise GuardrailDenied("partition_not_allowed")

    @staticmethod
    def _validate_value(name: str, value: Any, definition: dict[str, Any]) -> None:
        kind = definition.get("type")
        if kind == "int":
            if (
                type(value) is not int
                or not definition["min"] <= value <= definition["max"]
            ):
                raise GuardrailDenied(f"parameter_invalid:{name}")
        elif kind == "enum":
            if type(value) is not str or value not in definition["values"]:
                raise GuardrailDenied(f"parameter_invalid:{name}")
        elif kind == "string":
            if type(value) is not str or not SAFE_STRING.fullmatch(value):
                raise GuardrailDenied(f"parameter_invalid:{name}")
        else:
            raise GuardrailDenied(f"parameter_schema_invalid:{name}")

    @staticmethod
    def _render(spec: TemplateSpec, parameters: dict[str, Any]) -> str:
        script = spec.path.read_text()
        for name, value in parameters.items():
            script = script.replace("{{" + name + "}}", str(value))
        if "{{" in script or "}}" in script:
            raise GuardrailDenied("template_rendering_invalid")
        return script
