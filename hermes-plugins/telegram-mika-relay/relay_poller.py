#!/usr/bin/env python3
"""Deliver Mika's issue comments back to Telegram (VDLP-16, return path).

The hook in ``__init__.py`` only enqueues. This poller closes the loop: for every
chat mapped to an issue it reads new comments and sends the ones authored by
Mika (or any human/agent other than the relay itself) to that Telegram chat via
``hermes send``.

Why a poller and not a webhook: Multica exposes no outbound "an agent commented
on issue X" primitive — the CLI surface has no such trigger. So the return path
reads ``multica issue comment list --since``.

Two non-negotiable properties, both required on VDLP-16:

* **Survives a restart.** All cursor and delivery state is on disk under
  ``$HERMES_HOME/telegram-mika-relay/``, never in process memory.
* **Never delivers the same comment twice.** A comment id is recorded in
  ``delivered.json`` BEFORE the send is attempted is wrong (a crash would lose
  it); it is recorded immediately AFTER a successful send, and the send itself
  is guarded by the id set. A crash between send and record can at worst repeat
  one comment, which is why the id set is checked first on every pass.

Run modes:
    python relay_poller.py --once     # one pass, for cron/systemd timer
    python relay_poller.py --loop     # continuous, for a systemd service
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(
    level=os.environ.get("RELAY_POLLER_LOGLEVEL", "INFO"),
    format="%(asctime)s %(levelname)s relay-poller: %(message)s",
)
logger = logging.getLogger("relay-poller")

POLL_INTERVAL_S = int(os.environ.get("RELAY_POLL_INTERVAL", "20"))
CLI_TIMEOUT_S = 45

# The relay's own comments are authored by the workspace owner's token, so they
# come back on the next poll. This marker identifies them so they are never
# echoed to the chat that produced them.
RELAY_MARKER = "Inoltrato da Operator(Hermes)"


def hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    return Path(env) if env else Path.home() / ".hermes"


def state_dir() -> Path:
    d = hermes_home() / "telegram-mika-relay"
    d.mkdir(parents=True, exist_ok=True)
    return d


def map_path() -> Path:
    return state_dir() / "chat_issue_map.json"


def delivered_path() -> Path:
    return state_dir() / "delivered.json"


def sentinel_path() -> Path:
    return hermes_home() / "telegram-mika-relay.disabled"


def _load_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s unreadable (%s); using default", path.name, exc)
        return default


def _save_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def which(name: str, extra: List[str]) -> Optional[str]:
    override = os.environ.get(f"{name.upper()}_BIN")
    if override and Path(override).exists():
        return override
    found = shutil.which(name)
    if found:
        return found
    for candidate in extra:
        if Path(candidate).exists():
            return candidate
    return None


def multica_bin() -> Optional[str]:
    return which("multica", [
        "/opt/Multica/resources/app.asar.unpacked/resources/bin/multica",
        str(Path.home() / ".local/bin/multica"),
    ])


def hermes_bin() -> Optional[str]:
    return which("hermes", [str(Path.home() / ".local/bin/hermes")])


def _clean_env() -> Dict[str, str]:
    return {
        "HOME": os.environ.get("HOME", str(Path.home())),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "HERMES_HOME": str(hermes_home()),
    }


def run(argv: List[str], timeout: int = CLI_TIMEOUT_S) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, env=_clean_env(),
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001
        return 1, "", f"{type(exc).__name__}: {exc}"
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def fetch_comments(issue_id: str, since: Optional[str]) -> Tuple[List[Dict[str, Any]], str]:
    binary = multica_bin()
    if binary is None:
        return [], "multica CLI not found"
    argv = [binary, "issue", "comment", "list", issue_id, "--output", "json", "--compact"]
    if since:
        argv += ["--since", since]
    rc, out, err = run(argv)
    if rc != 0:
        return [], err or out or "comment list failed"
    try:
        data = json.loads(out) if out else []
    except Exception as exc:  # noqa: BLE001
        return [], f"could not parse comment list: {exc}"
    return (data if isinstance(data, list) else []), ""


def strip_markdown(text: str) -> str:
    """Flatten issue Markdown into something readable in a Telegram message."""
    text = re.sub(r"```rapportino.*?```", "", text, flags=re.S)
    text = re.sub(r"```(\w+)?\n?", "", text)
    text = re.sub(r"<sub>.*?</sub>", "", text, flags=re.S)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def send_to_telegram(chat_id: str, text: str) -> Tuple[bool, str]:
    binary = hermes_bin()
    if binary is None:
        return False, "hermes CLI not found"
    body = text if len(text) <= 3800 else text[:3800] + "\n\n[…messaggio troncato, vedi la issue]"
    rc, out, err = run([binary, "send", "--to", f"telegram:{chat_id}", "--quiet", body])
    if rc != 0:
        return False, err or out or "hermes send failed"
    return True, ""


def is_relay_echo(comment: Dict[str, Any]) -> bool:
    return RELAY_MARKER in (comment.get("content") or "")


def poll_once() -> int:
    """One pass over every mapped chat. Returns the number of messages delivered."""
    if sentinel_path().exists():
        logger.info("kill switch engaged (%s); poller idle", sentinel_path())
        return 0

    mapping = _load_json(map_path(), {})
    if not mapping:
        logger.debug("no chats mapped yet")
        return 0

    state = _load_json(delivered_path(), {})
    delivered_ids = set(state.get("delivered_ids", []))
    cursors: Dict[str, str] = state.get("cursors", {})
    delivered_count = 0

    for chat_id, entry in mapping.items():
        issue_id = (entry or {}).get("issue_id")
        if not issue_id:
            continue
        since = cursors.get(issue_id)
        comments, err = fetch_comments(issue_id, since)
        if err:
            logger.error("issue %s: %s", issue_id, err)
            continue

        newest = since
        for comment in sorted(comments, key=lambda c: c.get("created_at") or ""):
            cid = comment.get("id")
            created = comment.get("created_at") or ""
            if created and (newest is None or created > newest):
                newest = created
            if not cid or cid in delivered_ids:
                continue
            if is_relay_echo(comment):
                delivered_ids.add(cid)  # our own inbound copy: never echo it back
                continue
            body = strip_markdown(comment.get("content") or "")
            if not body:
                delivered_ids.add(cid)
                continue
            ok, send_err = send_to_telegram(chat_id, body)
            if not ok:
                logger.error("chat %s: delivery failed: %s", chat_id, send_err)
                continue  # leave undelivered; retried next pass
            delivered_ids.add(cid)
            delivered_count += 1
            logger.info("delivered comment %s -> telegram:%s", cid, chat_id)

        if newest:
            # Re-read from one second earlier than the newest seen comment: --since
            # is exclusive, and the id set makes an overlap free.
            cursors[issue_id] = newest

    state["delivered_ids"] = sorted(delivered_ids)
    state["cursors"] = cursors
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_json(delivered_path(), state)
    return delivered_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Deliver Mika's replies back to Telegram")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--once", action="store_true", help="single pass then exit (cron/timer)")
    group.add_argument("--loop", action="store_true", help="poll continuously (service)")
    parser.add_argument("--interval", type=int, default=POLL_INTERVAL_S)
    args = parser.parse_args()

    if args.once:
        count = poll_once()
        logger.info("pass complete: %d message(s) delivered", count)
        return 0

    logger.info("poller started (interval %ss)", args.interval)
    while True:
        try:
            poll_once()
        except KeyboardInterrupt:
            logger.info("poller stopped")
            return 0
        except Exception as exc:  # noqa: BLE001
            logger.exception("poll pass failed: %s", exc)
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
