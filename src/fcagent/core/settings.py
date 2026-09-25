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

    @classmethod
    def from_yaml(cls, path: Path) -> "Limits":
        raw: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
        return cls(
            max_nodes=int(raw["max_nodes"]),
            max_time_minutes=int(raw["max_time_minutes"]),
            allowed_partitions=frozenset(raw["allowed_partitions"]),
        )


@dataclass(frozen=True)
class CoreSettings:
    """Paths and limits, read without retaining credential values."""

    template_directory: Path
    limits: Limits
    audit_path: Path

    @classmethod
    def from_env(cls) -> "CoreSettings":
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
        )
