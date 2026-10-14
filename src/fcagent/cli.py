"""``fcagent`` command line: human approval of prepared jobs (channel A).

Only a human runs this. The approver identity is taken from the OS session, never
from argv. The private key path comes from ``FCAGENT_APPROVAL_PRIVATE_KEY``
(an absolute path, never the key itself). See docs/reproducibility.md for the
residual limits of this control.
"""

from __future__ import annotations

import argparse
import getpass
import os
import pwd
import sys
from pathlib import Path

from fcagent.core.approval import (
    REJECT_REASONS,
    ApprovalStore,
    generate_keypair,
    load_private_key,
)
from fcagent.core.audit import AuditLog
from fcagent.core.errors import GuardrailDenied
from fcagent.core.jobs import summarize
from fcagent.core.settings import CoreSettings


def _identity() -> tuple[str, int, str | None]:
    uid = os.getuid()
    try:
        name = pwd.getpwuid(uid).pw_name
    except KeyError:
        name = getpass.getuser()
    return name, uid, os.environ.get("SUDO_USER")


def _store() -> ApprovalStore:
    settings = CoreSettings.from_env()
    return ApprovalStore(
        settings.state_dir,
        settings.approval_ttl_seconds,
        AuditLog(settings.audit_path),
    )


def _cmd_show(args: argparse.Namespace) -> int:
    record = _store().get(args.prepared_id)
    print(f"status: {record['status']}")
    print(summarize(record["context"]))
    return 0


def _cmd_approve(args: argparse.Namespace) -> int:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("refused: approval needs an interactive terminal", file=sys.stderr)
        return 2
    raw = os.environ.get("FCAGENT_APPROVAL_PRIVATE_KEY")
    if not raw:
        print(
            "refused: FCAGENT_APPROVAL_PRIVATE_KEY (path) is not set", file=sys.stderr
        )
        return 2
    store = _store()
    record = store.get(args.prepared_id)
    print(f"status: {record['status']}")
    print(summarize(record["context"]))
    if input("Approve this job? type 'yes' to confirm: ").strip() != "yes":
        print("not approved")
        return 1
    name, uid, sudo_user = _identity()
    store.approve(
        args.prepared_id,
        load_private_key(Path(raw)),
        approver=name,
        approver_uid=uid,
        sudo_user=sudo_user,
    )
    print(f"approved by {name}")
    return 0


def _cmd_reject(args: argparse.Namespace) -> int:
    name, _, _ = _identity()
    _store().reject(args.prepared_id, rejector=name, reason=args.reason)
    print("rejected")
    return 0


def _cmd_keygen(args: argparse.Namespace) -> int:
    generate_keypair(Path(args.private), Path(args.public))
    print(f"wrote {args.private} (0600) and {args.public}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fcagent")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (
        ("approve", _cmd_approve),
        ("reject", _cmd_reject),
        ("show", _cmd_show),
    ):
        p = sub.add_parser(name)
        p.add_argument("prepared_id")
        if name == "reject":
            p.add_argument("--reason", choices=sorted(REJECT_REASONS), default="other")
        p.set_defaults(func=func)
    p = sub.add_parser("keygen", help="create an Ed25519 approval key pair")
    p.add_argument("--private", required=True)
    p.add_argument("--public", required=True)
    p.set_defaults(func=_cmd_keygen)
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except GuardrailDenied as exc:
        print(f"refused: {exc.reason}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
