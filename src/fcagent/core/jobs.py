"""Approved-template job preparation and human-approved submission guards."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

from fcagent.core.approval import ApprovalStore, load_public_key
from fcagent.core.audit import AuditLog
from fcagent.core.errors import GuardrailDenied
from fcagent.core.settings import Limits, is_normalized_absolute, path_within

__all__ = [
    "GuardrailDenied",
    "JobClient",
    "JobService",
    "PreparedJob",
    "SAFE_STRING",
    "TemplateSpec",
    "parameters_hash",
]

SAFE_STRING = re.compile(r"^[A-Za-z0-9_.-]+$")


class JobClient(Protocol):
    """The MCP v2 adapter capability used only after every guardrail succeeds."""

    def submit_job(
        self, script: str, system: str, working_directory: str, partition: str
    ) -> str: ...


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


def _sha256(text: str | bytes) -> str:
    data = text.encode() if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class PreparedJob:
    """A human-approved, verified submission ready to send to FirecREST."""

    prepared_id: str
    script: str
    context: dict[str, Any]
    approval: dict[str, Any]

    @property
    def partition(self) -> str:
        return str(self.context["partition"])


class JobService:
    """Validate, render, audit and gate a job on explicit human approval."""

    def __init__(
        self,
        *,
        template_directory: Path,
        limits: Limits,
        audit: AuditLog,
        client: JobClient | None,
        store: ApprovalStore | None = None,
        public_key_path: Path | None = None,
    ) -> None:
        self.template_directory = template_directory.resolve()
        self.limits = limits
        self.audit = audit
        self.client = client
        self.store = store
        if store is not None and store.audit is None:
            store.audit = audit
        self.public_key_path = public_key_path

    # ------------------------------------------------------------ prepare
    def list_templates(self) -> list[dict[str, Any]]:
        """Describe every approved template and its typed parameters."""
        found = []
        for schema in sorted(self.template_directory.glob("*.yaml")):
            if not (schema.with_suffix(".sh")).is_file():
                continue
            raw = yaml.safe_load(schema.read_text()) or {}
            found.append(
                {"template": schema.stem, "parameters": raw.get("parameters", {})}
            )
        return found

    def _check_system(self, system: str) -> None:
        """Refuse a target that is not allowlisted, before any record exists."""
        if not isinstance(system, str) or system not in self.limits.allowed_systems:
            exc = GuardrailDenied("unknown_system")
            allowed = ", ".join(sorted(self.limits.allowed_systems))
            exc.next_step = f"{exc.next_step} Allowed systems: {allowed}."
            raise exc

    def _check_working_directory(self, working_directory: str) -> None:
        if not is_normalized_absolute(working_directory):
            raise GuardrailDenied("working_directory_invalid")
        if not path_within(working_directory, self.limits.allowed_working_directories):
            exc = GuardrailDenied("working_directory_not_allowed")
            allowed = ", ".join(sorted(self.limits.allowed_working_directories))
            exc.next_step = f"{exc.next_step} Allowed: {allowed}."
            raise exc

    def render(
        self,
        *,
        template: str,
        parameters: dict[str, Any],
        working_directory: str,
    ) -> tuple[TemplateSpec, str]:
        """Validate every guardrail and render; raises GuardrailDenied."""
        spec = self._load_template(template)
        self._validate(parameters, spec, working_directory)
        return spec, self._render(spec, parameters)

    def prepare(
        self,
        *,
        actor: str,
        system: str,
        template: str,
        parameters: dict[str, Any],
        working_directory: str,
        account: str | None = None,
    ) -> dict[str, Any]:
        """Render, store a pending record and return a human-readable summary."""
        if self.store is None:
            raise RuntimeError("No approval store is configured")
        digest = parameters_hash(parameters)
        try:
            self._check_system(system)
            self._check_working_directory(working_directory)
            spec, script = self.render(
                template=template,
                parameters=parameters,
                working_directory=working_directory,
            )
        except GuardrailDenied as exc:
            self._audit(
                actor,
                "prepare_job",
                system[:64] if isinstance(system, str) else "",
                template,
                digest,
                "denied",
                exc.reason,
            )
            raise
        created_at, expires_at = self.store.expiry()
        node_hours = round(
            int(parameters.get("nodes", 1))
            * int(parameters.get("time_minutes", 0))
            / 60,
            4,
        )
        context = {
            "template": template,
            "template_sha256": _sha256(spec.path.read_bytes()),
            "script_sha256": _sha256(script),
            "parameters": parameters,
            "parameters_hash": digest,
            "system": system,
            "partition": str(parameters["partition"]),
            "working_directory": working_directory,
            "account": account,
            "created_at": created_at,
            "expires_at": expires_at,
            "node_hours": node_hours,
        }
        record = self.store.create(context)
        self._audit(
            actor,
            "prepare_job",
            system,
            template,
            digest,
            "prepared",
            prepared_id=record["prepared_id"],
        )
        return {
            "prepared_id": record["prepared_id"],
            "expires_at": expires_at,
            "summary": summarize(context),
            "next_step": (
                "Ask a human to run `fcagent approve "
                f"{record['prepared_id']}`; then call submit_job(prepared_id)."
            ),
        }

    # ------------------------------------------------------------- submit
    def claim(self, *, actor: str, prepared_id: str) -> PreparedJob:
        """Verify the signed approval and consume it once, before any remote call."""
        if self.store is None or self.public_key_path is None:
            raise GuardrailDenied("approval_key_not_configured")
        context: dict[str, Any] = {}
        try:
            key = load_public_key(self.public_key_path)
            holder: dict[str, str] = {}

            def check(ctx: dict[str, Any]) -> None:
                context.update(ctx)
                _, script = self.render(
                    template=ctx["template"],
                    parameters=ctx["parameters"],
                    working_directory=ctx["working_directory"],
                )
                spec = self._load_template(ctx["template"])
                if (
                    _sha256(script) != ctx["script_sha256"]
                    or _sha256(spec.path.read_bytes()) != ctx["template_sha256"]
                    or parameters_hash(ctx["parameters"]) != ctx["parameters_hash"]
                ):
                    raise GuardrailDenied("prepared_context_changed")
                holder["script"] = script

            record = self.store.claim(prepared_id, key, check=check)
        except GuardrailDenied as exc:
            self._audit(
                actor,
                "submit_job",
                context.get("system", ""),
                context.get("template"),
                context.get("parameters_hash"),
                "denied",
                exc.reason,
                prepared_id=prepared_id,
            )
            raise
        return PreparedJob(
            prepared_id, holder["script"], record["context"], record["approval"]
        )

    def submit(self, *, actor: str, prepared_id: str) -> dict[str, Any]:
        """Synchronous submission through the injected adapter (used by tests)."""
        job = self.claim(actor=actor, prepared_id=prepared_id)
        ctx = job.context
        try:
            if self.client is None:
                raise RuntimeError("No job client is configured")
            job_id = self.client.submit_job(
                job.script, ctx["system"], ctx["working_directory"], job.partition
            )
        except Exception as exc:
            self._audit(
                actor,
                "submit_job",
                ctx["system"],
                ctx["template"],
                ctx["parameters_hash"],
                "error",
                None,
                exception_type=type(exc).__name__,
                http_status=getattr(exc, "status", None),
                prepared_id=prepared_id,
            )
            raise
        self.audit_submitted(actor, job, job_id)
        return {"job_id": job_id}

    def audit_submitted(self, actor: str, job: PreparedJob, job_id: str) -> None:
        ctx = job.context
        self._audit(
            actor,
            "submit_job",
            ctx["system"],
            ctx["template"],
            ctx["parameters_hash"],
            "submitted",
            None,
            job_id=job_id,
            prepared_id=job.prepared_id,
            approved_by=job.approval["approver"],
        )

    def _audit(
        self,
        actor: str,
        action: str,
        system: str,
        template: str | None,
        digest: str | None,
        outcome: str,
        reason: str | None = None,
        **extra: Any,
    ) -> None:
        self.audit.write(
            actor=actor,
            action=action,
            system=system,
            template=template,
            parameters_hash=digest,
            job_id=extra.pop("job_id", None),
            outcome=outcome,
            reason=reason,
            **extra,
        )

    # ---------------------------------------------------------- internals
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
        working_directory: str,
    ) -> None:
        if not is_normalized_absolute(working_directory):
            raise GuardrailDenied("working_directory_invalid")
        if set(parameters) != set(spec.parameters):
            raise GuardrailDenied("template_parameters_invalid")
        for name, definition in spec.parameters.items():
            self._validate_value(name, parameters[name], definition)
        nodes = parameters.get("nodes")
        if isinstance(nodes, int) and nodes > self.limits.max_nodes:
            exc = GuardrailDenied("max_nodes_exceeded")
            exc.next_step = (
                f"{exc.next_step} The limit is max_nodes={self.limits.max_nodes}."
            )
            raise exc
        minutes = parameters.get("time_minutes")
        if isinstance(minutes, int) and minutes > self.limits.max_time_minutes:
            exc = GuardrailDenied("max_time_exceeded")
            exc.next_step = (
                f"{exc.next_step} The limit is "
                f"max_time_minutes={self.limits.max_time_minutes}."
            )
            raise exc
        partition = parameters.get("partition")
        if partition is not None and partition not in self.limits.allowed_partitions:
            exc = GuardrailDenied("partition_not_allowed")
            allowed = ", ".join(sorted(self.limits.allowed_partitions))
            exc.next_step = f"{exc.next_step} Allowed partitions: {allowed}."
            raise exc

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


def summarize(context: dict[str, Any]) -> str:
    """Human-readable approval summary bound to the prepared_id."""
    params = ", ".join(f"{k}={v}" for k, v in sorted(context["parameters"].items()))
    return (
        f"Template {context['template']} on system {context['system']} "
        f"(partition {context['partition']}); parameters: {params}; "
        f"nodes={context['parameters'].get('nodes')}, "
        f"time={context['parameters'].get('time_minutes')} min, "
        f"estimated {context['node_hours']} node-hours; "
        f"working directory {context['working_directory']}; "
        f"valid until {context['expires_at']}; "
        f"script sha256 {context['script_sha256'][:16]}…"
    )
