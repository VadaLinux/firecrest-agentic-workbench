"""Tests for demo/run-demo.sh, demo/replay.sh and scripts/build_handout.sh.

No network and no FirecREST: the sandbox repo uses stub scripts, so the tests also
run with the demo stack stopped.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

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
    assert "max_nodes_exceeded" in r.stdout
    assert all(line.startswith("[SAMPLE]") for line in r.stdout.splitlines())


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


def _state(tmp_path, name="state"):
    sd = tmp_path / name
    for sub in ("prepared", "consumed", "jobs"):
        (sd / sub).mkdir(parents=True)
        (sd / sub / "x.json").write_text("{}")
    (sd / "keep.txt").write_text("k")
    (sd / ".fcagent-demo-state").write_text("")
    return sd


def _env(root, sd, **extra):
    ws = root.parent / "ws"
    ws.mkdir(exist_ok=True)
    return {"DEMO_STATE_DIR": str(sd), "DEMO_WORKSPACE_DIR": str(ws), **extra}


def test_reset_state_dir_clears_only_gate_subdirs(tmp_path):
    root = _sandbox(tmp_path)
    sd = _state(tmp_path)
    audit = tmp_path / "audit.jsonl"
    audit.write_text("a")
    r = _run(root, "reset", env=_env(root, sd, FCAGENT_AUDIT_PATH=str(audit)))
    assert r.returncode == 0, r.stdout + r.stderr
    for sub in ("prepared", "consumed", "jobs"):
        assert list((sd / sub).iterdir()) == []
    assert (sd / "keep.txt").exists() and audit.exists()


def _refused(tmp_path, sd, root=None, **extra):
    root = root or _sandbox(tmp_path)
    r = _run(root, "reset", env=_env(root, sd, **extra))
    assert r.returncode == 1, r.stdout + r.stderr
    return r


def test_reset_state_dir_refuses_relative(tmp_path):
    _state(tmp_path)
    _refused(tmp_path, "state")


def test_reset_state_dir_refuses_symlink(tmp_path):
    real = _state(tmp_path, "real")
    link = tmp_path / "link"
    link.symlink_to(real)
    _refused(tmp_path, link)
    assert (real / "prepared" / "x.json").exists()


def test_reset_state_dir_refuses_inside_repo(tmp_path):
    root = _sandbox(tmp_path)
    sd = root / "runs"
    for sub in ("prepared", "consumed", "jobs"):
        (sd / sub).mkdir(parents=True)
        (sd / sub / "x.json").write_text("{}")
    (sd / ".fcagent-demo-state").write_text("")
    _refused(tmp_path, sd, root=root)
    assert (sd / "prepared" / "x.json").exists()


def test_reset_state_dir_refuses_inside_workspace(tmp_path):
    root = _sandbox(tmp_path)
    sd = tmp_path / "ws" / "state"
    for sub in ("prepared", "consumed", "jobs"):
        (sd / sub).mkdir(parents=True)
        (sd / sub / "x.json").write_text("{}")
    (sd / ".fcagent-demo-state").write_text("")
    _refused(tmp_path, sd, root=root)
    assert (sd / "prepared" / "x.json").exists()


def test_reset_state_dir_refuses_dir_containing_audit(tmp_path):
    sd = _state(tmp_path)
    audit = sd / "audit.jsonl"
    audit.write_text("a")
    _refused(tmp_path, sd, FCAGENT_AUDIT_PATH=str(audit))
    assert (sd / "prepared" / "x.json").exists() and audit.exists()


def test_reset_state_dir_refuses_symlinked_subdir(tmp_path):
    sd = _state(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious").write_text("x")
    import shutil as _sh

    _sh.rmtree(sd / "jobs")
    (sd / "jobs").symlink_to(outside)
    _refused(tmp_path, sd)
    assert (outside / "precious").exists()


def test_reset_state_dir_refuses_production_dir():
    text = (REPO / "demo/run-demo.sh").read_text()
    assert 'PROTECTED_STATE_DIR="/var/lib/fcagent"' in text
    assert "is or contains the production state dir" in text


def test_matrix_template_within_guardrails():
    import yaml

    spec = yaml.safe_load((REPO / "templates/matrix.yaml").read_text())
    limits = yaml.safe_load((REPO / "config/limits.yaml").read_text())
    assert spec["parameters"]["time_minutes"]["max"] <= limits["max_time_minutes"]
    assert set(spec["parameters"]["partition"]["values"]) <= set(
        limits["allowed_partitions"]
    )
    script = (REPO / "templates/matrix.sh").read_text()
    for name in spec["parameters"]:
        assert "{{" + name + "}}" in script


def test_reset_state_dir_refuses_missing_marker(tmp_path):
    sd = _state(tmp_path)
    (sd / ".fcagent-demo-state").unlink()
    r = _refused(tmp_path, sd)
    assert "marker" in r.stdout
    assert (sd / "prepared" / "x.json").exists()


@pytest.mark.parametrize("path", ["/", "/var", "/var/lib"])
def test_reset_state_dir_refuses_root_and_ancestors_of_production(tmp_path, path):
    # Refused before anything is deleted, whatever the marker says.
    r = _refused(tmp_path, path)
    assert "refusing /" in r.stdout or "production state dir" in r.stdout


def test_reset_state_dir_refuses_ancestor_of_audit(tmp_path):
    sd = _state(tmp_path)
    audit = sd / "sub" / "audit.jsonl"
    audit.parent.mkdir()
    audit.write_text("a")
    r = _refused(tmp_path, sd, FCAGENT_AUDIT_PATH=str(audit))
    assert "audit" in r.stdout


def test_contains_helper_handles_root_and_siblings(tmp_path):
    script = (REPO / "demo/run-demo.sh").read_text()
    start = script.index("contains() {")
    fn = script[start : script.index("\n}\n", start) + 3]
    code = (
        fn
        + "contains / /var/lib/fcagent && echo A; "
        + "contains /var/lib/fcagent-demo /var/lib/fcagent || echo B; "
        + "contains /var/lib /var/lib/fcagent && echo C"
    )
    out = subprocess.run(["bash", "-c", code], capture_output=True, text=True)
    assert out.stdout.split() == ["A", "B", "C"]
