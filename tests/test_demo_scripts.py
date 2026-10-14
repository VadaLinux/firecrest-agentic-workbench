"""Tests for demo/run-demo.sh, demo/replay.sh and scripts/build_handout.sh.

No network and no FirecREST: the sandbox repo uses stub scripts, so the tests also
run with the demo stack stopped.
"""

import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _sandbox(tmp_path, status_rc=0, omni_rc=0):
    root = tmp_path / "repo"
    (root / "demo").mkdir(parents=True)
    (root / "scripts").mkdir()
    shutil.copy(REPO / "demo/run-demo.sh", root / "demo/run-demo.sh")
    stub = root / "scripts/workbench-up.sh"
    stub.write_text(f"#!/bin/sh\nexit {status_rc}\n")
    stub.chmod(0o755)
    (root / "scripts/omniroute_check.py").write_text(
        f"import sys\nsys.exit({omni_rc})\n"
    )
    (root / ".gitignore").write_text("demo/.output/\nlogs/\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def _run(root, *args, env=None):
    e = {**os.environ, "DEMO_REPO_ROOT": str(root)}
    e.update(env or {})
    return subprocess.run(
        ["bash", str(root / "demo/run-demo.sh"), *args],
        capture_output=True,
        text=True,
        env=e,
    )


def test_check_fails_without_public_url(tmp_path):
    root = _sandbox(tmp_path)
    r = _run(root, "check", env={"DEMO_PUBLIC_URL": ""})
    assert r.returncode == 1
    assert "NOT READY" in r.stdout


def test_check_uses_fallback_model(tmp_path):
    root = _sandbox(tmp_path)
    # primary fails (stub exits 1 unless OMNIROUTE_MODEL is the fallback)
    (root / "scripts/omniroute_check.py").write_text(
        "import os,sys\nsys.exit(0 if os.environ.get('OMNIROUTE_MODEL')=='fb' else 1)\n"
    )
    r = _run(root, "check", env={"DEMO_FALLBACK_MODEL": "fb", "DEMO_PUBLIC_URL": ""})
    assert "FALLBACK available" in r.stdout


def test_check_no_model_points_to_plan_b(tmp_path):
    root = _sandbox(tmp_path, omni_rc=1)
    r = _run(root, "check", env={"DEMO_PUBLIC_URL": ""})
    assert "replay.sh" in r.stdout


def test_reset_only_empties_allowed_dirs(tmp_path):
    root = _sandbox(tmp_path)
    out = root / "demo/.output"
    out.mkdir()
    (out / "a.txt").write_text("x")
    keep = root / "keep.txt"
    keep.write_text("y")
    r = _run(root, "reset")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (out / "a.txt").exists()
    assert keep.exists()


def test_reset_refuses_non_ignored_dir(tmp_path):
    root = _sandbox(tmp_path)
    (root / ".gitignore").write_text("")
    out = root / "demo/.output"
    out.mkdir()
    (out / "a.txt").write_text("x")
    r = _run(root, "reset")
    assert r.returncode == 1
    assert (out / "a.txt").exists()


def test_reset_refuses_symlink_escape(tmp_path):
    root = _sandbox(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious").write_text("x")
    (root / "demo/.output").symlink_to(outside)
    r = _run(root, "reset")
    assert r.returncode == 1
    assert (outside / "precious").exists()


def test_replay_prints_transcripts():
    r = subprocess.run(
        ["bash", str(REPO / "demo/replay.sh")],
        capture_output=True,
        text=True,
        env={**os.environ, "DEMO_REPLAY_DELAY": "0"},
    )
    assert r.returncode == 0
    assert "BLOCKED by guardrail" in r.stdout


def test_scripts_do_not_call_firecrest_directly():
    for name in ("demo/run-demo.sh", "demo/replay.sh"):
        text = (REPO / name).read_text().lower()
        assert "pyfirecrest" not in text
        assert "/compute/" not in text


def test_build_handout_reports_missing_tools(tmp_path):
    r = subprocess.run(
        ["/bin/bash", str(REPO / "scripts/build_handout.sh")],
        capture_output=True,
        text=True,
        env={"PATH": str(tmp_path)},
    )
    assert r.returncode == 3
    assert "missing tools" in r.stderr
