#!/usr/bin/env bash
# Plan B: replay the pre-recorded demo transcript. No network, no FirecREST, no model.
# Usage: demo/replay.sh [file ...]   (default: all demo/prerecorded/*.txt in order)
set -eu
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
[ -f "$DIR/.env" ] && { set -a; . "$DIR/.env"; set +a; }
DELAY="${DEMO_REPLAY_DELAY:-1}"
case "$DELAY" in ''|*[!0-9.]*) echo "invalid DEMO_REPLAY_DELAY" >&2; exit 2;; esac
if [ "$#" -gt 0 ]; then files=("$@"); else files=("$DIR"/prerecorded/*.txt); fi
echo "[SAMPLE] TEMPLATE, NOT REAL OUTPUT: <PLACEHOLDER> values must be replaced with captured output."
for f in "${files[@]}"; do
  [ -f "$f" ] || { echo "missing $f" >&2; exit 1; }
  while IFS= read -r line || [ -n "$line" ]; do
    printf '[SAMPLE] %s\n' "$line"
    sleep "$DELAY"
  done < "$f"
done
