"""Host/Origin allowlist for the Streamable HTTP transport (public hostname behind a tunnel)."""

from __future__ import annotations

import pytest

import server
from client import FirecRESTError


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FIRECREST_MCP_PUBLIC_HOST", raising=False)


def test_unset_keeps_sdk_default(clean_env: None) -> None:
    # No variable: the SDK decides, exactly as before this change.
    assert server._http_transport_security("127.0.0.1") is None


def test_public_host_is_added_to_localhost_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECREST_MCP_PUBLIC_HOST", "Mcp.Example.org")
    settings = server._http_transport_security("127.0.0.1")
    assert settings is not None
    assert settings.enable_dns_rebinding_protection is True
    assert "mcp.example.org" in settings.allowed_hosts
    assert "mcp.example.org:*" in settings.allowed_hosts
    assert "127.0.0.1:*" in settings.allowed_hosts
    assert "https://mcp.example.org" in settings.allowed_origins


@pytest.mark.parametrize("bad", ["https://x.org", "x y", "*", "localhost", "a/b"])
def test_invalid_public_host_is_rejected(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("FIRECREST_MCP_PUBLIC_HOST", bad)
    with pytest.raises(FirecRESTError):
        server._http_transport_security("127.0.0.1")
