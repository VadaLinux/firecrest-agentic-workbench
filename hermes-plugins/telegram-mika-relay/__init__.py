"""Relay inbound Telegram messages into Mika's Multica queue (VDLP-16).

Hermes carries the Telegram binding as *transport*. gabriele.vadala's
conversational counterpart in this workspace is Mika, so a Telegram DM must not
be answered by Hermes's own agent loop — it is deposited on a Multica issue
assigned to Mika, and Mika's reply is delivered back to Telegram asynchronously
by ``relay_poller.py``.

Design decisions (approved on VDLP-16, see README.md):

1. Mapping     — one persistent issue per Telegram chat, reused. The
                 ``chat_id -> issue_id`` map is on disk so it survives a gateway
                 or host restart.
2. Round-trip  — asynchronous. This hook only enqueues; the poller delivers.
3. Scope       — everything is relayed except an explicit allow-list of local
                 utility commands (never a content heuristic).

Failure policy — the single most important rule here: **fail open, loudly.**
If the relay cannot reach Multica, the user's message must not disappear. We
say so on the channel, verbatim error included, and then fall through to
Hermes's own agent loop (the pre-VDLP-16 behaviour, now subordinated rather
than deleted). Returning ``{"action": "skip"}`` after a failed relay would be
the worst possible outcome: the user wrote, nobody answered, nobody knows.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("plugins.telegram-mika-relay")

PLUGIN_NAME = "telegram-mika-relay"

# --- Scope -----------------------------------------------------------------
# Decision 3: local handling is defined by an EXPLICIT list, never by guessing
# whether a message "looks like" utility chat. A wrong guess fails silently,
# which is the failure mode we are here to remove. Anything not on this list is
# relayed. Names are the canonical Hermes command names (hermes_cli/commands.py).
LOCAL_COMMANDS = frozenset({
    "start", "new", "clear", "reset", "stop", "status", "help", "commands",
    "model", "reasoning", "usage", "context", "compress", "history", "sessions",
    "whoami", "version", "platform", "platforms", "voice", "busy", "queue",
    "steer", "pause", "resume", "restart", "update", "debug", "tools",
    "toolsets", "skills", "plugins", "cron", "kanban", "memory", "config",
    "profile", "login", "logout", "approve", "deny", "approvals", "yolo",
})

# --- Kill switch -----------------------------------------------------------
# One command must be enough to fall back to the previous behaviour at 23:00 on
# a bad night, WITHOUT uninstalling the plugin:
#     touch ~/.hermes/telegram-mika-relay.disabled
# or  HERMES_TELEGRAM_MIKA_RELAY=off
SENTINEL_NAME = "telegram-mika-relay.disabled"
ENV_SWITCH = "HERMES_TELEGRAM_MIKA_RELAY"

MIKA_AGENT_ID = "de6591b7-a3ee-4aca-befc-230d197cdd57"

# Multica CLI call budget. The hook sits in the gateway's inbound path, so a
# hung CLI must never wedge message handling.
CLI_TIMEOUT_S = 45

# A chat whose issue was closed longer ago than this gets a NEW issue rather
# than a reopen. Rationale: a thread resumed after days of silence reads as a
# non-sequitur appended to settled work, and the reader of the board cannot
# tell where the old conversation ended and the new one began. Under the
# threshold, reopening preserves the context that makes the conversation
# legible — which is the whole reason decision 1 chose one issue per chat.
STALE_REOPEN_SECONDS = 7 * 24 * 3600


def _hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    return Path.home() / ".hermes"


def state_dir() -> Path:
    d = _hermes_home() / "telegram-mika-relay"
    d.mkdir(parents=True, exist_ok=True)
    return d


def map_path() -> Path:
    """Persistent ``chat_id -> issue`` map (decision 1: survives a restart)."""
    return state_dir() / "chat_issue_map.json"


def relay_enabled() -> bool:
    """False when the kill switch is engaged (sentinel file or env var)."""
    if (_hermes_home() / SENTINEL_NAME).exists():
        return False
    return os.environ.get(ENV_SWITCH, "on").strip().lower() not in {
        "0", "off", "false", "no", "disable", "disabled",
    }


# --- Multica CLI -----------------------------------------------------------

def multica_bin() -> Optional[str]:
    """Locate the ``multica`` binary.

    The gateway's PATH is Hermes's own venv PATH and does NOT contain the
    Multica desktop bin dir, so ``shutil.which`` alone is not enough.
    """
    override = os.environ.get("MULTICA_BIN")
    if override and Path(override).exists():
        return override
    found = shutil.which("multica")
    if found:
        return found
    for candidate in (
        "/opt/Multica/resources/app.asar.unpacked/resources/bin/multica",
        str(Path.home() / ".local/bin/multica"),
    ):
        if Path(candidate).exists():
            return candidate
    return None


def clean_env() -> Dict[str, str]:
    """Minimal subprocess environment.

    The gateway process holds every provider API key in ``os.environ`` and none
    of them belong in a subprocess. Auth comes from ``~/.multica/config.json``,
    which is bound to the workspace owner — the relay posts as a
    human-authorized client, not as an agent actor. ``MULTICA_TASK_*`` variables
    are deliberately NOT forwarded: a task-scoped token would tie every relayed
    message to whatever run happened to be live.
    """
    return {
        "HOME": os.environ.get("HOME", str(Path.home())),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }


def run_multica(args, timeout: int = CLI_TIMEOUT_S,
                cwd: Optional[Path] = None) -> Tuple[int, str, str]:
    """Run the Multica CLI. Returns ``(returncode, stdout, stderr)``.

    *cwd* matters for any call passing ``--content-file`` / ``--description-file``:
    the CLI refuses paths outside the working directory unless
    ``--allow-external-file`` is given (MUL-4252), so those calls pass a bare
    filename and set *cwd* to the directory holding it.
    """
    binary = multica_bin()
    if binary is None:
        return 127, "", "multica CLI not found on this host (PATH, MULTICA_BIN, known install dirs)"
    try:
        proc = subprocess.run(
            [binary, *args], capture_output=True, text=True, timeout=timeout,
            env=clean_env(), cwd=str(cwd) if cwd else None,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"multica {' '.join(args[:2])} timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
        return 1, "", f"{type(exc).__name__}: {exc}"
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


# --- chat -> issue mapping -------------------------------------------------

_map_lock = threading.Lock()


def load_map() -> Dict[str, Any]:
    try:
        with open(map_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("chat->issue map unreadable (%s); starting empty", exc)
        return {}


def save_map(data: Dict[str, Any]) -> None:
    path = map_path()
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _issue_status(issue_id: str) -> Optional[Dict[str, Any]]:
    rc, out, _err = run_multica(["issue", "get", issue_id, "--output", "json"])
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except Exception:  # noqa: BLE001
        return None


def _issue_is_usable(issue: Dict[str, Any]) -> bool:
    """True when we should keep appending to this issue rather than open a new one.

    Closed issues are reopened (decision 1) unless they have been closed long
    enough that resuming the thread would be unreadable — see
    ``STALE_REOPEN_SECONDS``.
    """
    status = (issue.get("status") or "").lower()
    if status not in {"done", "cancelled"}:
        return True
    updated = issue.get("updated_at") or ""
    try:
        from datetime import datetime, timezone
        closed_at = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - closed_at).total_seconds()
    except Exception:  # noqa: BLE001
        return True  # unparseable timestamp: prefer reopening over board noise
    return age <= STALE_REOPEN_SECONDS


def _create_chat_issue(chat_id: str, chat_name: str, workdir: Path) -> Tuple[Optional[str], str]:
    """Create the persistent issue for a Telegram chat, assigned to Mika."""
    title = f"Telegram — {chat_name or chat_id}"
    body_file = workdir / f"relay-issue-{chat_id}.md"
    body_file.write_text(
        "Conversazione Telegram inoltrata automaticamente da Operator(Hermes).\n\n"
        f"Chat Telegram: `{chat_id}`" + (f" ({chat_name})" if chat_name else "") + "\n\n"
        "Hermes è il trasporto di questo canale, non la sua voce: ogni messaggio "
        "che arriva qui viene depositato come commento su questa issue e la "
        "risposta di Mika viene riconsegnata su Telegram.\n\n"
        "Rispondi commentando questa issue — il relay consegna il commento in chat.\n",
        encoding="utf-8",
    )
    try:
        # --status backlog, NOT todo: creating a todo issue already assigned to
        # Mika dispatches a run immediately, and the relay comment that follows
        # dispatches a second one — the user is answered twice on Telegram for
        # one message. Parking the issue makes the first relayed comment the
        # single trigger. Observed live on VDLP-34.
        rc, out, err = run_multica([
            "issue", "create", "--title", title,
            "--description-file", body_file.name,
            "--assignee-id", MIKA_AGENT_ID,
            "--status", "backlog", "--priority", "medium",
            "--output", "json",
        ], cwd=workdir)
    finally:
        with_suppress_unlink(body_file)
    if rc != 0:
        return None, err or out or "issue create failed"
    try:
        return json.loads(out).get("id"), ""
    except Exception as exc:  # noqa: BLE001
        return None, f"could not parse issue create output: {exc}"


def with_suppress_unlink(path: Path) -> None:
    try:
        path.unlink()
    except Exception:  # noqa: BLE001
        pass


def resolve_issue_for_chat(chat_id: str, chat_name: str, workdir: Path) -> Tuple[Optional[str], str]:
    """Return ``(issue_id, error)`` for this chat, creating/reopening as needed."""
    with _map_lock:
        mapping = load_map()
        entry = mapping.get(chat_id) or {}
        issue_id = entry.get("issue_id")

        if issue_id:
            issue = _issue_status(issue_id)
            if issue is None:
                # The issue vanished (deleted) or the API is unreachable. Treat a
                # missing issue as "make a new one"; a broken API will fail again
                # on the comment call and be reported there.
                issue_id = None
            elif _issue_is_usable(issue):
                status = (issue.get("status") or "").lower()
                if status in {"done", "cancelled"}:
                    rc, _out, err = run_multica(
                        ["issue", "status", issue_id, "in_progress", "--no-start"]
                    )
                    if rc != 0:
                        return None, f"could not reopen {issue_id}: {err}"
            else:
                issue_id = None  # closed too long ago: start a fresh thread

        if not issue_id:
            issue_id, err = _create_chat_issue(chat_id, chat_name, workdir)
            if not issue_id:
                return None, err
            mapping[chat_id] = {
                "issue_id": issue_id,
                "chat_name": chat_name,
                "created_at": time.time(),
            }
            save_map(mapping)
        return issue_id, ""


# --- Relay -----------------------------------------------------------------

def _comment_body(text: str, user_name: str, chat_id: str, message_id: str) -> str:
    return (
        f"**Messaggio Telegram da {user_name or 'utente'}**\n\n"
        f"{text}\n\n"
        f"<sub>Inoltrato da Operator(Hermes) — chat `{chat_id}`, messaggio `{message_id}`. "
        f"La risposta a questo commento viene consegnata in chat.</sub>\n"
    )


def relay_message(text: str, chat_id: str, chat_name: str, user_name: str,
                  message_id: str) -> Tuple[bool, str, str]:
    """Push one Telegram message into Mika's queue.

    Returns ``(ok, issue_id, error)``.
    """
    workdir = state_dir()
    issue_id, err = resolve_issue_for_chat(chat_id, chat_name, workdir)
    if not issue_id:
        return False, "", err

    body_file = workdir / f"relay-comment-{chat_id}-{message_id or int(time.time())}.md"
    body_file.write_text(_comment_body(text, user_name, chat_id, message_id), encoding="utf-8")
    try:
        # cwd=workdir + bare filename: the CLI refuses --content-file paths
        # outside the working directory unless --allow-external-file (MUL-4252).
        rc, out, err = run_multica(
            ["issue", "comment", "add", issue_id,
             "--content-file", body_file.name, "--output", "json"],
            cwd=workdir,
        )
    finally:
        with_suppress_unlink(body_file)
    if rc != 0:
        return False, issue_id, err or out or "comment add failed"

    # Mark the issue in_progress so the board shows a conversation waiting on
    # Mika rather than a silent todo. Best-effort: a failure here does not mean
    # the message was lost, and must not be reported as one.
    run_multica(["issue", "status", issue_id, "in_progress", "--no-start"])
    return True, issue_id, ""


# --- Hook ------------------------------------------------------------------

def _command_name(text: str) -> Optional[str]:
    """Canonical command name for ``/foo@bot bar``-style text, else None."""
    stripped = (text or "").strip()
    if not stripped.startswith("/"):
        return None
    token = stripped[1:].split()[0] if len(stripped) > 1 else ""
    return token.split("@", 1)[0].lower() or None


def _send_on_channel(gateway, platform, chat_id: str, text: str) -> None:
    """Best-effort side-channel send through the gateway's own adapter."""
    try:
        adapter = gateway.adapters.get(platform)
        if adapter is None:
            logger.error("no adapter for %s; cannot report relay failure on channel", platform)
            return
        import asyncio
        coro = adapter.send(str(chat_id), text)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(coro)
        else:  # pragma: no cover - the hook runs on the gateway's loop thread
            asyncio.run(coro)
    except Exception as exc:  # noqa: BLE001
        logger.error("failed to send relay notice on channel: %s", exc)


def pre_gateway_dispatch(event=None, gateway=None, session_store=None, **kwargs):
    """Intercept inbound Telegram messages and relay them into Mika's queue.

    ``{"action": "skip"}``  — relayed; Hermes's agent loop must not answer.
    ``None``                — not ours (other platform / local command), or the
                              relay failed and we are falling back to Hermes
                              after saying so on the channel.
    """
    try:
        source = getattr(event, "source", None)
        if source is None:
            return None
        platform = getattr(source, "platform", None)
        platform_name = getattr(platform, "value", platform)
        if platform_name != "telegram":
            return None

        if not relay_enabled():
            logger.info("telegram-mika-relay disabled by kill switch; Hermes answers locally")
            return None

        text = (getattr(event, "text", "") or "").strip()
        if not text:
            return None  # media-only: nothing to relay as text, let Hermes handle it

        command = _command_name(text)
        if command is not None and command in LOCAL_COMMANDS:
            logger.info("telegram-mika-relay: /%s is a local utility command, not relayed", command)
            return None

        chat_id = str(getattr(source, "chat_id", "") or "")
        chat_name = getattr(source, "chat_name", "") or getattr(source, "user_name", "") or ""
        user_name = getattr(source, "user_name", "") or ""
        message_id = str(getattr(event, "message_id", "") or "")

        ok, issue_id, err = relay_message(text, chat_id, chat_name, user_name, message_id)

        if ok:
            logger.info("telegram-mika-relay: relayed chat=%s -> issue=%s", chat_id, issue_id)
            _send_on_channel(
                gateway, platform, chat_id,
                "📨 Passo il messaggio a Mika — ti rispondo qui appena replica.",
            )
            return {"action": "skip", "reason": "relayed-to-mika"}

        # FAIL OPEN, LOUDLY. The message must not vanish.
        logger.error("telegram-mika-relay: relay FAILED chat=%s: %s", chat_id, err)
        _send_on_channel(
            gateway, platform, chat_id,
            "⚠️ Non sono riuscito a passare il messaggio a Mika:\n"
            f"`{err}`\n\n"
            "Rispondo io direttamente qui sotto — ma questo messaggio non è "
            "arrivato sulla board.",
        )
        return None

    except Exception as exc:  # noqa: BLE001
        # A crash here must never eat a message: fall through to Hermes.
        logger.exception("telegram-mika-relay hook error: %s", exc)
        return None


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", pre_gateway_dispatch)
