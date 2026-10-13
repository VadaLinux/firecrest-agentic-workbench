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
    assert server._http_transport_security() is None


def test_public_host_is_added_to_localhost_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIRECREST_MCP_PUBLIC_HOST", "Mcp.Example.org")
    settings = server._http_transport_security()
    assert settings is not None
    assert settings.enable_dns_rebinding_protection is True
    assert "mcp.example.org" in settings.allowed_hosts
    assert "mcp.example.org:*" in settings.allowed_hosts
    assert "127.0.0.1:*" in settings.allowed_hosts
    assert "https://mcp.example.org" in settings.allowed_origins


@pytest.mark.parametrize(
    "bad",
    [
        "https://x.org",  # scheme
        "x y",  # space
        "*",  # wildcard
        "localhost",  # not a public name
        "a/b",  # path
        "evil.com:*",  # port wildcard
        "evil.com:8443",  # explicit port
        "user@host.example.org",  # userinfo
        "127.0.0.1",  # IPv4 literal
        "10.0.0.5",  # IPv4 literal (private)
        "[::1]",  # IPv6 literal
        "-bad.example.org",  # label starts with hyphen
        "bad-.example.org",  # label ends with hyphen
        "under_score.example.org",  # underscore not allowed
        "a..b.example.org",  # empty label
    ],
)
def test_invalid_public_host_is_rejected(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv("FIRECREST_MCP_PUBLIC_HOST", bad)
    with pytest.raises(FirecRESTError):
        server._http_transport_security()


@pytest.mark.parametrize("good", ["mcp.example.org", "a.b", "my-host.lab.example.org"])
def test_valid_public_host_is_accepted(
    monkeypatch: pytest.MonkeyPatch, good: str
) -> None:
    monkeypatch.setenv("FIRECREST_MCP_PUBLIC_HOST", good)
    assert server._http_transport_security() is not None
