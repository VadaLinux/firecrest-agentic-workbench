"""Validated configuration for approved FirecREST job templates."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Limits:
    """Submission limits that must be checked before contacting FirecREST."""

    max_nodes: int
    max_time_minutes: int
    allowed_partitions: frozenset[str]
    allowed_systems: frozenset[str]

    @classmethod
    def from_yaml(cls, path: Path) -> "Limits":
        raw: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
        systems = raw.get("allowed_systems")
        if (
            not isinstance(systems, list)
            or not systems
            or not all(isinstance(name, str) and name for name in systems)
        ):
            raise ValueError(
                "limits file must define a non-empty list `allowed_systems` "
                f"({path.name})"
            )
        return cls(
            max_nodes=int(raw["max_nodes"]),
            max_time_minutes=int(raw["max_time_minutes"]),
            allowed_partitions=frozenset(raw["allowed_partitions"]),
            allowed_systems=frozenset(systems),
        )


@dataclass(frozen=True)
class CoreSettings:
    """Paths and limits, read without retaining credential values."""

    template_directory: Path
    limits: Limits
    audit_path: Path
    state_dir: Path
    approval_public_key: Path | None
    approval_ttl_seconds: int

    @classmethod
    def from_env(cls) -> "CoreSettings":
        def absolute(name: str, value: str) -> Path:
            path = Path(value)
            if not path.is_absolute():
                raise ValueError(f"{name} must be an absolute path")
            return path

        root = Path(
            os.environ.get("FCAGENT_ROOT", Path(__file__).resolve().parents[3])
        ).resolve()
        return cls(
            template_directory=Path(
                os.environ.get("FCAGENT_TEMPLATE_DIR", root / "templates")
            ),
            limits=Limits.from_yaml(
                Path(os.environ.get("FCAGENT_LIMITS_FILE", root / "config/limits.yaml"))
            ),
            audit_path=Path(
                os.environ.get("FCAGENT_AUDIT_PATH", root / "logs/audit.jsonl")
            ),
            state_dir=(
                absolute("FCAGENT_STATE_DIR", os.environ["FCAGENT_STATE_DIR"])
                if os.environ.get("FCAGENT_STATE_DIR")
                else root / "runs"
            ),
            approval_public_key=(
                absolute(
                    "FCAGENT_APPROVAL_PUBLIC_KEY",
                    os.environ["FCAGENT_APPROVAL_PUBLIC_KEY"],
                )
                if os.environ.get("FCAGENT_APPROVAL_PUBLIC_KEY")
                else None
            ),
            approval_ttl_seconds=int(os.environ.get("FCAGENT_APPROVAL_TTL", "900")),
        )
