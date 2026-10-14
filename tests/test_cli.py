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
