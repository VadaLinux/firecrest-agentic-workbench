"""Approval CLI: refuses without a TTY and never takes the approver from argv."""

from __future__ import annotations

from pathlib import Path

import pytest

from fcagent import cli


def test_approve_refuses_without_interactive_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FCAGENT_STATE_DIR", str(tmp_path))
    assert cli.main(["approve", "x" * 32]) == 2
    assert "interactive terminal" in capsys.readouterr().err


def test_cli_has_no_approver_option() -> None:
    with pytest.raises(SystemExit):
        cli.main(["approve", "x" * 32, "--approver", "me"])


def test_reject_writes_audit_with_os_identity_and_controlled_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from fcagent.core.approval import ApprovalStore

    monkeypatch.setenv("FCAGENT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("FCAGENT_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    store = ApprovalStore(tmp_path / "state")
    ctx = {
        k: None
        for k in __import__("fcagent.core.approval", fromlist=["x"]).CONTEXT_FIELDS
    }
    ctx.update(parameters={}, system="cluster", template="hello")
    pid = store.create(ctx)["prepared_id"]
    assert cli.main(["reject", pid, "--reason", "policy"]) == 0
    record = json.loads((tmp_path / "audit.jsonl").read_text().splitlines()[-1])
    assert record["action"] == "reject_job" and record["reason"] == "policy"
    assert record["actor"] == cli._identity()[0]
    with pytest.raises(SystemExit):
        cli.main(["reject", pid, "--reason", "free text with secret"])
