#!/usr/bin/env bash
# Pre-flight check and reset for the 5-minute demo (runs on the VPS).
#
#   demo/run-demo.sh check   verify services, public endpoint and model (read-only)
#   demo/run-demo.sh reset   empty the demo output directories (see ALLOWED_DIRS) and,
#                            if DEMO_STATE_DIR is set, the gate sub-directories
#                            prepared/ consumed/ jobs/ inside it (never the audit log)
#
# This script never talks to FirecREST. It only calls scripts/workbench-up.sh --status
# and scripts/omniroute_check.py. Configuration comes from the gitignored demo/.env.
set -u

ROOT="${DEMO_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)}"
# The only directories reset may empty. Relative to ROOT, must be gitignored.
ALLOWED_DIRS=("demo/.output" "logs/demo")

if [ -f "$ROOT/demo/.env" ]; then
  set -a; . "$ROOT/demo/.env"; set +a
fi

fail=0
ok()   { echo "OK    $*"; }
bad()  { echo "FAIL  $*"; fail=1; }

cmd_check() {
  if "$ROOT/scripts/workbench-up.sh" --status >/dev/null 2>&1; then
    ok "workbench services"
  else
    bad "workbench services (run scripts/workbench-up.sh)"
  fi

  if [ -n "${DEMO_PUBLIC_URL:-}" ]; then
    if curl -fsS -o /dev/null --max-time 5 "$DEMO_PUBLIC_URL" 2>/dev/null; then
      ok "public endpoint reachable"
    else
      # Cloudflare Access may answer 302/403 to anonymous probes: reachable is enough.
      code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$DEMO_PUBLIC_URL" 2>/dev/null || true)
      case "$code" in
        30?|401|403) ok "public endpoint reachable (HTTP $code, access-protected)";;
        *) bad "public endpoint (HTTP ${code:-none})";;
      esac
    fi
  else
    bad "DEMO_PUBLIC_URL not set in demo/.env"
  fi

  if python3 "$ROOT/scripts/omniroute_check.py" >/dev/null 2>&1; then
    ok "model: primary (OMNIROUTE_MODEL)"
  elif [ -n "${DEMO_FALLBACK_MODEL:-}" ] \
    && OMNIROUTE_MODEL="$DEMO_FALLBACK_MODEL" python3 "$ROOT/scripts/omniroute_check.py" >/dev/null 2>&1; then
    ok "model: primary down, FALLBACK available. Set OMNIROUTE_MODEL=\$DEMO_FALLBACK_MODEL"
  else
    bad "no model available. Use Plan B: demo/replay.sh"
  fi

  [ "$fail" -eq 0 ] && echo "READY" || echo "NOT READY"
  return "$fail"
}

# Gate state of the demo (decision D2: must live OUTSIDE the checkout and the agent
# workspace). Only these known sub-directories are emptied; the audit log never is.
GATE_SUBDIRS=("prepared" "consumed" "jobs")
PROTECTED_STATE_DIR="/var/lib/fcagent"
# The operator creates this file inside the demo state dir (see the systemd drop-in
# example). Without it, reset refuses to touch the directory.
STATE_MARKER=".fcagent-demo-state"

# contains PARENT CHILD: true if CHILD is PARENT or lies below it. Both get a trailing
# slash first, so PARENT=/ works and /var/lib/fcagent-demo is not "inside" /var/lib/fcagent.
contains() {
  local p="${1%/}/" c="${2%/}/"
  [[ "$c" == "$p"* ]]
}

reset_state_dir() {
  local sd="${DEMO_STATE_DIR:-}" ws real audit sub
  [ -n "$sd" ] || { echo "SKIP  DEMO_STATE_DIR not set: gate state untouched"; return 0; }
  ws="${DEMO_WORKSPACE_DIR:-$(cd "$ROOT/.." && pwd -P)}"
  audit="${FCAGENT_AUDIT_PATH:-}"
  case "$sd" in /*) ;; *) bad "state dir must be absolute"; return 1;; esac
  case "$sd" in *..*) bad "state dir must not contain '..'"; return 1;; esac
  [ -L "$sd" ] && { bad "state dir is a symlink"; return 1; }
  [ -d "$sd" ] || { bad "state dir does not exist"; return 1; }
  real=$(cd "$sd" && pwd -P)
  [ "$real" = "$sd" ] || { bad "state dir path is not canonical ($real)"; return 1; }
  [ "$real" = "/" ] && { bad "refusing /"; return 1; }
  contains "$real" "$PROTECTED_STATE_DIR" && { bad "state dir is or contains the production state dir"; return 1; }
  contains "$ROOT" "$real" && { bad "state dir is inside the repository"; return 1; }
  contains "$ws" "$real" && { bad "state dir is inside the agent workspace"; return 1; }
  contains "$real" "$ROOT" && { bad "state dir contains the repository"; return 1; }
  if [ -n "$audit" ] && contains "$real" "$audit"; then
    bad "state dir contains the audit log"; return 1
  fi
  if [ -L "$real/$STATE_MARKER" ] || [ ! -f "$real/$STATE_MARKER" ]; then
    bad "missing marker file $STATE_MARKER: not a demo state dir"; return 1
  fi
  for sub in "${GATE_SUBDIRS[@]}"; do
    if [ -L "$real/$sub" ]; then bad "$sub is a symlink"; return 1; fi
  done
  for sub in "${GATE_SUBDIRS[@]}"; do
    if [ -d "$real/$sub" ]; then find "$real/$sub" -mindepth 1 -delete; fi
  done
  ok "reset gate state in $real (${GATE_SUBDIRS[*]})"
}

cmd_reset() {
  local rel dir
  for rel in "${ALLOWED_DIRS[@]}"; do
    dir="$ROOT/$rel"
    case "$rel" in /*|*..*|"") bad "invalid path $rel"; continue;; esac
    if ! git -C "$ROOT" check-ignore -q "$rel/" 2>/dev/null; then
      bad "refusing to touch $rel: not gitignored"
      continue
    fi
    mkdir -p "$dir"
    real=$(cd "$dir" && pwd -P)
    case "$real" in
      "$ROOT/$rel") ;;
      *) bad "refusing $rel: resolves outside the repository ($real)"; continue;;
    esac
    find "$real" -mindepth 1 -delete
    ok "reset $rel"
  done
  reset_state_dir || fail=1
  return "$fail"
}

case "${1:-}" in
  check) cmd_check;;
  reset) cmd_reset;;
  *) echo "usage: $0 {check|reset}" >&2; exit 2;;
esac
