#!/usr/bin/env python3
"""Manual retention cleanup (dry-run unless --apply). See docs/trust-and-compliance.md.

Run `--apply` with the stack stopped: the audit log is appended without a lock.
"""

from __future__ import annotations

import argparse
import json
import sys

from fcagent.core.retention import RetentionError, retention_days, run_retention
from fcagent.core.settings import CoreSettings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually delete (default is a dry-run that only reports)",
    )
    args = parser.parse_args(argv)
    try:
        days = retention_days()
        settings = CoreSettings.from_env()
    except (RetentionError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    report = run_retention(
        state_dir=settings.state_dir,
        audit_path=settings.audit_path,
        days=days,
        apply=args.apply,
    )
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
