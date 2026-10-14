#!/usr/bin/env bash
# Pre-flight check and reset for the 5-minute demo (runs on the VPS).
#
#   demo/run-demo.sh check   verify services, public endpoint and model (read-only)
#   demo/run-demo.sh reset   empty the demo output directories (see ALLOWED_DIRS)
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
  return "$fail"
}

case "${1:-}" in
  check) cmd_check;;
  reset) cmd_reset;;
  *) echo "usage: $0 {check|reset}" >&2; exit 2;;
esac
