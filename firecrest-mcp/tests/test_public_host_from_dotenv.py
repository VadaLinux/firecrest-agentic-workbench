"""The public hostname set only in firecrest-mcp/.env must reach the transport allowlist.

Regression for PR #35 review: `_http_transport_security()` used to read os.environ before
`.env` was loaded, so a hostname that lived only in `.env` was ignored. These tests put the
variable ONLY in a `.env` file next to a copy of server.py, never in the process environment,
and then start the real server to check what it accepts.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = "mcp-test.example.org"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _post(port: int, host: str) -> int:
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        }
    ).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/mcp",
        method="POST",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Host": host,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


@pytest.fixture
def server_from_dotenv_only(tmp_path: Path):
    """Run a copy of the server whose only source for the public host is its own .env."""
    workdir = tmp_path / "firecrest-mcp"
    workdir.mkdir()
    shutil.copy(ROOT / "server.py", workdir / "server.py")
    for name in ("client.py", "client_v2.py"):
        shutil.copy(ROOT / name, workdir / name)
    (workdir / ".env").write_text(
        f"FIRECREST_MCP_PUBLIC_HOST={PUBLIC}\nFIRECREST_API_VERSION=v2\n",
        encoding="utf-8",
    )

    env = {k: v for k, v in os.environ.items() if k != "FIRECREST_MCP_PUBLIC_HOST"}
    env["PYTHONPATH"] = os.pathsep.join(
        # fcagent lives in the repo's top-level src/, one level above firecrest-mcp/.
        [str(ROOT), str(ROOT.parent / "src")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "server.py",
            "--transport",
            "http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=workdir,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            if proc.poll() is not None:
                raise AssertionError(proc.stderr.read().decode(errors="replace"))
            try:
                if _post(port, f"127.0.0.1:{port}") in (200, 400, 406):
                    break
            except OSError:
                pass
            time.sleep(0.3)
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_public_host_only_in_dotenv_is_accepted(server_from_dotenv_only: int) -> None:
    assert _post(server_from_dotenv_only, PUBLIC) == 200


def test_foreign_host_is_still_rejected(server_from_dotenv_only: int) -> None:
    assert _post(server_from_dotenv_only, "evil.example.com") == 421
