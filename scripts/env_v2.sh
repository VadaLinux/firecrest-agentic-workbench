#!/usr/bin/env bash
# Bring up (or tear down) a local FirecREST v2 development stack for
# CRIGAMO-21: firecrest + slurm (dummy cluster) + keycloak, cloned fresh
# from the official eth-cscs/firecrest-v2 repo if not already present.
#
# This is deliberately narrower than scripts/workbench-up.sh (which brings up
# the whole agentic workbench): it only starts what firecrest-mcp's v2 client
# and scripts/smoke_local.py need — no MetaMCP, no DocMind, no PBS, no MinIO.
#
# Usage:
#     env_up.sh            clone (if needed) and start the v2 demo stack
#     env_up.sh --status   report what is running, change nothing
#     env_down.sh          stop the stack (containers kept, not removed)
#
# Env overrides (all optional):
#     WORKSPACE_ROOT   parent dir for the clone (default: ~/Sviluppo, same
#                       convention as scripts/workbench-up.sh and
#                       scripts/docs_delta.sh)
#     F7T_V2_DIR        clone location (default: $WORKSPACE_ROOT/firecrest-v2-upstream —
#                       the same path scripts/docs_delta.sh already expects
#                       for this repo, so both scripts share one clone)
#     F7T_V2_REPO_URL   upstream URL (default: https://github.com/eth-cscs/firecrest-v2.git)
#
# What this script does NOT do: it does not write firecrest-mcp/.env. Copy
# firecrest-mcp/.env.example to .env yourself and fill in the FIRECREST_V2_*
# section — see docs/local-env.md.

set -uo pipefail

REPO="${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
WORKSPACE_ROOT="${WORKSPACE_ROOT:-${HOME}/Sviluppo}"
F7T_V2_DIR="${F7T_V2_DIR:-${WORKSPACE_ROOT}/firecrest-v2-upstream}"
F7T_V2_REPO_URL="${F7T_V2_REPO_URL:-https://github.com/eth-cscs/firecrest-v2.git}"
OVERRIDE_FILE="${REPO}/docs/patches/firecrest-v2-docker-compose.override.yml"
SERVICES=(firecrest slurm keycloak keycloak-create-user)

log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
fail() { log "FATAL: $*"; exit 1; }

compose() {
    ( cd "$F7T_V2_DIR" && docker compose -f docker-compose.yml -f "$OVERRIDE_FILE" "$@" )
}

status() {
    echo "=== firecrest-v2 containers ==="
    docker ps -a --filter 'name=firecrest-v2-' --format '{{.Names}}\t{{.Status}}' | sort
    echo
    echo "=== reachability ==="
    curl -sS -o /dev/null -w '  gateway :8000   -> HTTP %{http_code}\n' http://localhost:8000/status/systems 2>/dev/null \
        || echo "  gateway :8000   -> unreachable"
    curl -sS -o /dev/null -w '  keycloak :18080 -> HTTP %{http_code}\n' http://localhost:18080/auth/realms/kcrealm 2>/dev/null \
        || echo "  keycloak :18080 -> unreachable"
}

down() {
    [ -d "$F7T_V2_DIR" ] || { log "nothing to stop: $F7T_V2_DIR does not exist"; return 0; }
    log "stopping firecrest-v2 stack"
    compose stop "${SERVICES[@]}"
}

if [ "${1:-}" = "--status" ]; then
    status
    exit 0
fi

if [ "${1:-}" = "down" ]; then
    down
    exit 0
fi

log "=== FirecREST v2 local env bring-up ==="

for _ in $(seq 1 30); do
    docker info >/dev/null 2>&1 && break
    sleep 2
done
docker info >/dev/null 2>&1 || fail "docker daemon not reachable after 60s"

if [ ! -d "$F7T_V2_DIR/.git" ]; then
    log "cloning $F7T_V2_REPO_URL -> $F7T_V2_DIR"
    mkdir -p "$WORKSPACE_ROOT"
    git clone --quiet "$F7T_V2_REPO_URL" "$F7T_V2_DIR" || fail "clone failed"
else
    log "reusing existing clone at $F7T_V2_DIR (not touching its contents, per project rule)"
fi

# The clone ships test-only keys/secrets (fine — they're the upstream
# project's own published demo credentials, not ours, and they only ever
# authenticate a throwaway container on localhost). Two host-side fixups are
# needed for THIS host's container/SELinux setup, applied to the clone's
# working tree only — never to a file this repository tracks:
#
# 1. SSH private keys: readable by the firecrest container's own non-root
#    user (uid 5678 in the image), which a 0400 (owner-only) mode blocks
#    when the file's owning uid on the host differs from the container uid.
#    Loosening to 0644 matches the mode the files have in the upstream repo
#    itself (`git show` reports 100644), so this is a revert-to-tracked-mode,
#    not a weakening.
chmod 644 "$F7T_V2_DIR"/build/environment/keys/*-key 2>/dev/null || true

# 2. SELinux (enforcing on this host — see docs/ENVIRONMENT.md): every
#    bind-mounted path the clone's compose file uses has label
#    user_home_t, which a confined container_t process cannot read, so a
#    container silently gets `PermissionError` / an empty import instead of
#    a clear SELinux denial. chcon (not a boolean, not permissive mode) is
#    the same fix docs/ENVIRONMENT.md documents for the v1 demo stack.
if command -v chcon >/dev/null 2>&1 && [ "$(getenforce 2>/dev/null || echo Disabled)" != "Disabled" ]; then
    log "relabelling bind-mounted paths for SELinux (container_file_t)"
    chcon -R -t container_file_t \
        "$F7T_V2_DIR/build/secrets" \
        "$F7T_V2_DIR/build/environment/keys" \
        "$F7T_V2_DIR/f7t-api-config.local-env.yaml" \
        "$F7T_V2_DIR/src" \
        "$F7T_V2_DIR/build/docker/keycloak/config.json" \
        "$F7T_V2_DIR/build/docker/slurm-cluster/ssh/sshd_config_base" \
        2>/dev/null || log "WARNING: chcon failed for one or more paths — bind mounts may 403/permission-deny; see docs/local-env.md"
fi

log "building images (first run compiles Slurm from source — several minutes)"
compose build "${SERVICES[@]}" || fail "build failed"

log "starting: ${SERVICES[*]}"
compose up -d "${SERVICES[@]}" || fail "up failed"

log "waiting for firecrest gateway on :8000"
for _ in $(seq 1 30); do
    code="$(curl -sS -o /dev/null -w '%{http_code}' http://localhost:8000/status/systems 2>/dev/null || echo 000)"
    # 401 is the expected answer with no token — it proves the gateway is up
    # and enforcing auth, same shape as the v1 stack's own end-to-end check.
    [ "$code" = "401" ] && break
    sleep 2
done

status
log "=== bring-up finished ==="
log "next: copy firecrest-mcp/.env.example to firecrest-mcp/.env, fill in"
log "      FIRECREST_V2_* (see docs/local-env.md), then run:"
log "        python scripts/smoke_local.py"
