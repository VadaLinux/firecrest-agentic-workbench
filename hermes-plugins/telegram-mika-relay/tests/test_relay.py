"""Tests for the Telegram→Mika relay (VDLP-16).

No Multica API, no Telegram, no gateway: every external call is stubbed. The
properties under test are the ones the issue made non-negotiable — scope by
explicit list, fail-open-loudly, restart-durable mapping, no double delivery.

Run:  python3 -m pytest hermes-plugins/telegram-mika-relay/tests -q
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, PLUGIN_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


relay = _load("relay_plugin_under_test", "__init__.py")
poller = _load("relay_poller_under_test", "relay_poller.py")


class FakeSource:
    def __init__(self, platform="telegram", chat_id="5765971829",
                 user_name="Gabriele", chat_name="Gabriele Vadalà"):
        self.platform = platform
        self.chat_id = chat_id
        self.user_name = user_name
        self.chat_name = chat_name


class FakeEvent:
    def __init__(self, text, source=None, message_id="m1"):
        self.text = text
        self.source = source or FakeSource()
        self.message_id = message_id


class FakeAdapter:
    def __init__(self):
        self.sent = []

    async def send(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


class FakeGateway:
    def __init__(self):
        self.adapters = {"telegram": FakeAdapter()}


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv(relay.ENV_SWITCH, raising=False)
    yield tmp_path


def _sent_texts(gateway):
    return [t for _c, t in gateway.adapters["telegram"].sent]


# --- Decision 3: scope by explicit list -----------------------------------

def test_conversational_message_is_relayed(monkeypatch):
    calls = {}

    def fake_relay(text, chat_id, chat_name, user_name, message_id):
        calls.update(text=text, chat_id=chat_id)
        return True, "issue-1", ""

    monkeypatch.setattr(relay, "relay_message", fake_relay)
    gw = FakeGateway()
    result = relay.pre_gateway_dispatch(event=FakeEvent("Ciao, come va?"), gateway=gw)

    assert result == {"action": "skip", "reason": "relayed-to-mika"}
    assert calls["text"] == "Ciao, come va?"


@pytest.mark.parametrize("text", ["/status", "/new", "/help@hermes_bot", "/MODEL opus"])
def test_local_utility_commands_are_not_relayed(text, monkeypatch):
    monkeypatch.setattr(relay, "relay_message",
                        lambda *a, **k: pytest.fail("utility command must not be relayed"))
    assert relay.pre_gateway_dispatch(event=FakeEvent(text), gateway=FakeGateway()) is None


def test_unknown_slash_command_is_relayed_not_guessed(monkeypatch):
    """A command not on the list is relayed — the list is the whole rule."""
    monkeypatch.setattr(relay, "relay_message", lambda *a, **k: (True, "issue-1", ""))
    result = relay.pre_gateway_dispatch(event=FakeEvent("/deploy prod"), gateway=FakeGateway())
    assert result == {"action": "skip", "reason": "relayed-to-mika"}


def test_non_telegram_platform_is_ignored(monkeypatch):
    monkeypatch.setattr(relay, "relay_message",
                        lambda *a, **k: pytest.fail("only telegram is relayed"))
    event = FakeEvent("hello", source=FakeSource(platform="discord"))
    assert relay.pre_gateway_dispatch(event=event, gateway=FakeGateway()) is None


# --- The failure requirement: fail open, loudly ---------------------------

def test_relay_failure_reports_error_and_falls_back(monkeypatch):
    monkeypatch.setattr(relay, "relay_message",
                        lambda *a, **k: (False, "", "connection refused"))
    gw = FakeGateway()
    result = relay.pre_gateway_dispatch(event=FakeEvent("Ciao"), gateway=gw)

    # None = Hermes answers (fallback preserved, not deleted).
    assert result is None
    notice = "\n".join(_sent_texts(gw))
    assert "connection refused" in notice
    assert "Mika" in notice


def test_hook_crash_never_eats_the_message(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(relay, "relay_message", boom)
    assert relay.pre_gateway_dispatch(event=FakeEvent("Ciao"), gateway=FakeGateway()) is None


def test_missing_multica_cli_is_a_reported_error(monkeypatch):
    monkeypatch.setattr(relay, "multica_bin", lambda: None)
    rc, _out, err = relay.run_multica(["issue", "get", "x"])
    assert rc == 127
    assert "not found" in err


# --- Kill switch ----------------------------------------------------------

def test_sentinel_file_disables_relay(isolated_home, monkeypatch):
    monkeypatch.setattr(relay, "relay_message",
                        lambda *a, **k: pytest.fail("kill switch must stop the relay"))
    (isolated_home / relay.SENTINEL_NAME).touch()
    assert relay.relay_enabled() is False
    assert relay.pre_gateway_dispatch(event=FakeEvent("Ciao"), gateway=FakeGateway()) is None


def test_env_var_disables_relay(monkeypatch):
    monkeypatch.setenv(relay.ENV_SWITCH, "off")
    assert relay.relay_enabled() is False


# --- Decision 1: persistent chat -> issue map -----------------------------

def test_mapping_is_reused_and_survives_restart(monkeypatch, isolated_home):
    created = []

    def fake_create(chat_id, chat_name, workdir):
        created.append(chat_id)
        return "issue-42", ""

    monkeypatch.setattr(relay, "_create_chat_issue", fake_create)
    monkeypatch.setattr(relay, "_issue_status",
                        lambda i: {"id": i, "status": "in_progress", "updated_at": ""})

    first, _ = relay.resolve_issue_for_chat("chat-1", "Gabriele", isolated_home)
    # Simulate a restart: nothing in memory, only the file on disk.
    second, _ = relay.resolve_issue_for_chat("chat-1", "Gabriele", isolated_home)

    assert first == second == "issue-42"
    assert created == ["chat-1"], "issue must be created once, then reused"
    on_disk = json.loads(relay.map_path().read_text())
    assert on_disk["chat-1"]["issue_id"] == "issue-42"


def test_closed_issue_is_reopened(monkeypatch, isolated_home):
    relay.save_map({"chat-1": {"issue_id": "issue-7"}})
    monkeypatch.setattr(relay, "_issue_status", lambda i: {
        "id": i, "status": "done",
        "updated_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(),
    })
    reopened = []
    monkeypatch.setattr(relay, "run_multica",
                        lambda args, **k: (reopened.append(args), (0, "", ""))[1])

    issue_id, err = relay.resolve_issue_for_chat("chat-1", "G", isolated_home)
    assert (issue_id, err) == ("issue-7", "")
    assert any("in_progress" in a for a in reopened)


def test_long_closed_issue_starts_a_new_thread(monkeypatch, isolated_home):
    relay.save_map({"chat-1": {"issue_id": "issue-old"}})
    monkeypatch.setattr(relay, "_issue_status", lambda i: {
        "id": i, "status": "done", "updated_at": "2020-01-01T00:00:00Z",
    })
    monkeypatch.setattr(relay, "_create_chat_issue", lambda *a: ("issue-new", ""))

    issue_id, _ = relay.resolve_issue_for_chat("chat-1", "G", isolated_home)
    assert issue_id == "issue-new"


def test_issue_is_created_parked_so_mika_runs_once(monkeypatch, isolated_home):
    """Regression (observed live on VDLP-34): creating the issue as `todo`
    dispatched a Mika run, and the relay comment dispatched a second one — the
    user was answered twice on Telegram for one message. The issue must be
    created parked so the first relayed comment is the single trigger."""
    seen = {}

    def fake_run(args, **kwargs):
        if args[:2] == ["issue", "create"]:
            seen["argv"] = args
            return 0, json.dumps({"id": "issue-1"}), ""
        return 0, "", ""

    monkeypatch.setattr(relay, "run_multica", fake_run)
    relay._create_chat_issue("chat-1", "G", isolated_home)

    argv = seen["argv"]
    status = argv[argv.index("--status") + 1]
    assert status == "backlog", "a todo issue triggers a duplicate Mika run"


# --- Return path: no double delivery --------------------------------------

def test_poller_delivers_once_and_survives_restart(monkeypatch, isolated_home):
    (isolated_home / "telegram-mika-relay").mkdir(parents=True, exist_ok=True)
    poller._save_json(poller.map_path(), {"5765971829": {"issue_id": "issue-1"}})

    comment = {"id": "c1", "created_at": "2026-09-14T10:00:00Z",
               "content": "Ciao Gabriele, sono Mika."}
    monkeypatch.setattr(poller, "fetch_comments", lambda i, since: ([comment], ""))
    sends = []
    monkeypatch.setattr(poller, "send_to_telegram",
                        lambda chat, text: (sends.append((chat, text)), (True, ""))[1])

    assert poller.poll_once() == 1
    assert poller.poll_once() == 0, "the same comment must never be sent twice"
    assert len(sends) == 1
    assert "Mika" in sends[0][1]

    state = json.loads(poller.delivered_path().read_text())
    assert "c1" in state["delivered_ids"]


def test_poller_never_echoes_the_relays_own_comment(monkeypatch, isolated_home):
    poller._save_json(poller.map_path(), {"chat-1": {"issue_id": "issue-1"}})
    echo = {"id": "c9", "created_at": "2026-09-14T10:00:00Z",
            "content": f"**Messaggio Telegram da Gabriele**\n\nCiao\n\n<sub>{poller.RELAY_MARKER} …</sub>"}
    monkeypatch.setattr(poller, "fetch_comments", lambda i, since: ([echo], ""))
    monkeypatch.setattr(poller, "send_to_telegram",
                        lambda *a: pytest.fail("must not echo our own relay comment"))
    assert poller.poll_once() == 0


def test_failed_send_is_retried_next_pass(monkeypatch, isolated_home):
    poller._save_json(poller.map_path(), {"chat-1": {"issue_id": "issue-1"}})
    comment = {"id": "c2", "created_at": "2026-09-14T10:00:00Z", "content": "risposta"}
    monkeypatch.setattr(poller, "fetch_comments", lambda i, since: ([comment], ""))

    monkeypatch.setattr(poller, "send_to_telegram", lambda *a: (False, "network down"))
    assert poller.poll_once() == 0

    monkeypatch.setattr(poller, "send_to_telegram", lambda *a: (True, ""))
    assert poller.poll_once() == 1, "an undelivered comment must be retried, not dropped"


def test_poller_idles_when_kill_switch_engaged(isolated_home, monkeypatch):
    poller._save_json(poller.map_path(), {"chat-1": {"issue_id": "issue-1"}})
    (isolated_home / "telegram-mika-relay.disabled").touch()
    monkeypatch.setattr(poller, "fetch_comments",
                        lambda *a: pytest.fail("poller must idle when disabled"))
    assert poller.poll_once() == 0


def test_rapportino_block_is_stripped_from_telegram_text():
    body = "Fatto.\n\n```rapportino\ndata: 2026-09-14\nvoce: x\n```"
    assert "rapportino" not in poller.strip_markdown(body)
    assert poller.strip_markdown(body).startswith("Fatto.")
