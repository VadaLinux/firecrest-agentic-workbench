#!/usr/bin/env bash
# Bring the whole FirecREST Agentic Workbench up, idempotently.
#
# Designed to run from a systemd oneshot unit at boot, and by hand at any time.
# Running it when everything is already healthy is a no-op, because
# `docker compose up -d` only starts what is stopped.
#
# Why a script rather than restart policies on the compose files:
#
#   * Three of the four components are compose projects owned by upstream repos. Adding
#     `restart:` to their compose files would mean editing clones that are gitignored
#     and get re-cloned — the change would silently disappear, and the FirecREST demo
#     stack is explicitly off-limits ("do not modify anything inside the cloned
#     firecrest repo", prompt 01 step 6).
#   * `firecrest-mcp` is not a container at all. It is a host process running from its
#     own virtualenv, so no restart policy anywhere can bring it back.
#
# The stack's own restart policies still apply and are not overridden — this only
# handles what they miss.
#
# Usage:
#     workbench-up.sh            bring everything up
#     workbench-up.sh --status   report what is running, change nothing

set -uo pipefail

REPO="${REPO:-/home/gavadala/Sviluppo/firecrest-agentic-workbench}"
FIREREST_DEMO="${FIREREST_DEMO:-/home/gavadala/Sviluppo/firecrest/deploy/demo}"
METAMCP_DIR="${METAMCP_DIR:-/home/gavadala/Sviluppo/metamcp}"
DOCMIND_DIR="${DOCMIND_DIR:-/home/gavadala/Sviluppo/docmind-ai-llm}"
FIREREST_MCP_PORT="${FIREREST_MCP_PORT:-8765}"

log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
fail() { log "FATAL: $*"; exit 1; }

# Docker access depends on how this runs, and the difference is not cosmetic:
#
#   * as root (a system unit) `docker` works directly;
#   * as the user (a user unit) it only works if the `docker` group is in the process's
#     group set. Adding a user to a group does NOT update sessions that already exist,
#     so a user manager started before the membership was granted will not have it.
#     `sg docker -c` re-reads membership from the group database, which is why it is the
#     workaround used everywhere else in this project — and why it is needed here too.
#
# Detected once, so a failure to reach Docker is reported before anything is started
# rather than halfway through.
if docker info >/dev/null 2>&1; then
    DOCKER_ACCESS=direct
elif sg docker -c "docker info" >/dev/null 2>&1; then
    DOCKER_ACCESS=sg
else
    DOCKER_ACCESS=none
fi

compose_in() {
    # compose_in <dir> <args...> — paths here are fixed literals, so the quoting through
    # sg's shell is safe.
    local dir="$1"; shift
    if [ "$DOCKER_ACCESS" = direct ]; then
        ( cd "$dir" && docker compose "$@" )
    else
        sg docker -c "cd $dir && docker compose $*"
    fi
}

# The three compose projects, in the order that matters: MetaMCP and DocMind reach the
# host, but neither depends on a container from another project, so this is a
# readability order rather than a hard dependency chain.
compose_up() {
    local name="$1" dir="$2"
    [ -d "$dir" ] || { log "SKIP $name: $dir does not exist"; return 0; }
    log "starting $name"
    compose_in "$dir" up -d 2>&1 | sed 's/^/    /' | tail -5
    local rc=${PIPESTATUS[0]}
    [ "$rc" -eq 0 ] && log "$name up" || log "WARNING: $name exited $rc"
    return 0
}

status() {
    echo "=== containers ==="
    docker ps --format '{{.Names}}\t{{.Status}}' | sort
    echo
    echo "=== firecrest-mcp (host process on :$FIREREST_MCP_PORT) ==="
    if ss -ltn "sport = :$FIREREST_MCP_PORT" 2>/dev/null | grep -q LISTEN; then
        echo "  listening"
    else
        echo "  NOT listening"
    fi
}

if [ "${1:-}" = "--status" ]; then
    status
    exit 0
fi

log "=== FirecREST Agentic Workbench bring-up ==="

# The unit orders after docker.service, but a oneshot can still win the race against the
# daemon finishing socket setup on a busy boot.
for _ in $(seq 1 30); do
    docker info >/dev/null 2>&1 && break
    sleep 2
done
docker info >/dev/null 2>&1 || fail "docker daemon not reachable after 60s"

compose_up "FirecREST demo stack" "$FIREREST_DEMO"
compose_up "MetaMCP" "$METAMCP_DIR"

# DocMind's synthesis backend is selectable (see the compose override). The local Ollama
# model takes ~290s per answer on this CPU-only host, which makes a multi-query agent run
# unusable, so when an OpenRouter key is available synthesis is pointed at it. Embeddings
# stay local either way — moving them would invalidate the ingested corpus and force a
# full re-ingest.
#
# The key lives in ~/.hermes/.env, which Docker Compose does not read: compose only loads
# <project>/.env. It is bridged into the environment here instead, so the stack picks it
# up both at boot and by hand. Without a key the override falls back to the local model
# and everything still works, just slowly.
OPENROUTER_ENV="${OPENROUTER_ENV:-$HOME/.hermes/.env}"
if [ -f "$OPENROUTER_ENV" ] && grep -q '^OPENROUTER_API_KEY=.\+' "$OPENROUTER_ENV"; then
    OPENROUTER_API_KEY="$(grep '^OPENROUTER_API_KEY=' "$OPENROUTER_ENV" | head -1 | cut -d= -f2-)"
    export OPENROUTER_API_KEY
    # Model is read from the same file so it can be changed without touching this script.
    DOCMIND_LLM_MODEL="${DOCMIND_LLM_MODEL:-$(grep '^OPENROUTER_LLM_MODEL=' "$OPENROUTER_ENV" | head -1 | cut -d= -f2-)}"
    export DOCMIND_LLM_BACKEND_CHOICE="${DOCMIND_LLM_BACKEND_CHOICE:-openai_compatible}"
    export DOCMIND_LLM_MODEL="${DOCMIND_LLM_MODEL:-anthropic/claude-3.5-haiku}"
    log "DocMind synthesis: OpenRouter (model $DOCMIND_LLM_MODEL)"
else
    log "DocMind synthesis: local Ollama (no OPENROUTER_API_KEY in $OPENROUTER_ENV)"
fi

compose_up "DocMind" "$DOCMIND_DIR"

# firecrest-mcp: a host process. The systemd unit owns its lifecycle, so here we only
# report it — starting a second copy would fail on the port and look like a crash.
if ss -ltn "sport = :$FIREREST_MCP_PORT" 2>/dev/null | grep -q LISTEN; then
    log "firecrest-mcp already listening on :$FIREREST_MCP_PORT"
elif systemctl --user is-active --quiet firecrest-mcp.service 2>/dev/null; then
    # At boot this script runs *before* firecrest-mcp.service, by the unit's own ordering
    # (it is After=this one, so the demo API is up before the first tool call). Warning
    # here would be noise about something that is about to start on its own.
    log "firecrest-mcp is not up yet; its unit owns it and starts next"
else
    log "WARNING: firecrest-mcp is NOT listening on :$FIREREST_MCP_PORT"
    log "         it is a host process owned by firecrest-mcp.service — check:"
    log "         systemctl --user status firecrest-mcp.service"
fi

log "=== bring-up finished ==="
log "verify the aggregated tool surface with:"
log "  cd $REPO && ./firecrest-mcp/.venv/bin/python scripts/metamcp_admin.py tools"
