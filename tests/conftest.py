"""Shared fixtures: an approval key pair and a no-network job service."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from fcagent.core.approval import ApprovalStore, generate_keypair, load_private_key


@pytest.fixture
def keys(tmp_path: Path) -> tuple[Path, Path]:
    private, public = (
        tmp_path / "keys" / "approver.pem",
        tmp_path / "keys" / "approver.pub",
    )
    generate_keypair(private, public)
    return private, public


@pytest.fixture
def approver_key(keys: tuple[Path, Path]) -> Ed25519PrivateKey:
    return load_private_key(keys[0])


def approve(store: ApprovalStore, prepared_id: str, key: Ed25519PrivateKey) -> None:
    store.approve(prepared_id, key, approver="alice", approver_uid=1000, sudo_user=None)
