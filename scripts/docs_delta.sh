#!/usr/bin/env bash
# Watch the CSCS documentation sources for changes and hand the update to systemd.
#
# Design: this script only does the CHEAP part — fetch, compare, update the working
# copies, record the new revision. It never runs the ingestion itself; it starts
# firecrest-reingest.service and lets systemd own the long job. That split exists
# because an ingestion lasts minutes to hours, and anything that backgrounds-and-yields
# from a session would be orphaned when that session's turn ends.
#
# Sequencing guard: while firecrest-reingest.timer still has a pending elapse (i.e. the
# one-shot full re-ingestion is still scheduled), this exits without acting. Two
# ingestions cannot overlap — the snapshot writer lock is exclusive — so the scheduled
# full run must be allowed to go first.
#
# State: the last revision seen per source repo, in $STATE. A change is a HEAD move.
#
# Exit status: 0 whether or not there were changes (a no-change night is a success).
# The output says which case it was.

set -uo pipefail

REPO="${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
WORKSPACE_ROOT="${WORKSPACE_ROOT:-${HOME}/Sviluppo}"
STATE="${STATE:-${WORKSPACE_ROOT}/docs-watch.state}"

# Source trees the corpus is built from, and the upstream repos they come from.
# "local_dir|upstream_clone_dir|github_url"
SOURCE_SPECS=(
    "${WORKSPACE_ROOT}/cscs-docs|${WORKSPACE_ROOT}/cscs-docs|https://github.com/eth-cscs/cscs-docs"
    "${WORKSPACE_ROOT}/firecrest-v2-src|${WORKSPACE_ROOT}/firecrest-v2-upstream|https://github.com/eth-cscs/firecrest-v2"
)

log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

# --------------------------------------------------- guard: don't collide with the
# scheduled full re-ingestion
if systemctl --user list-timers firecrest-reingest.timer --all --no-legend 2>/dev/null \
        | grep -q firecrest-reingest; then
    NEXT=$(systemctl --user show firecrest-reingest.timer -p NextElapseUSecRealtime 2>/dev/null | cut -d= -f2)
    if [ -n "$NEXT" ]; then
        log "SKIP: the one-shot full re-ingestion is still scheduled ($NEXT). Not acting."
        log "      Two ingestions cannot overlap; the full run goes first."
        exit 0
    fi
fi

# --------------------------------------------------- guard: never run two at once
if systemctl --user is-active --quiet firecrest-reingest.service 2>/dev/null; then
    log "SKIP: firecrest-reingest.service is already running."
    exit 0
fi

touch "$STATE"
CHANGED=0
SUMMARY=""

for spec in "${SOURCE_SPECS[@]}"; do
    IFS='|' read -r local_dir upstream_dir url <<< "$spec"
    name=$(basename "$url")

    if [ ! -d "$upstream_dir/.git" ]; then
        log "$name: no clone at $upstream_dir — cloning"
        git clone --depth 1 -q "$url" "$upstream_dir" || { log "  clone FAILED"; continue; }
    fi

    git -C "$upstream_dir" fetch --quiet --depth 1 origin || { log "$name: fetch FAILED"; continue; }
    REMOTE=$(git -C "$upstream_dir" rev-parse origin/HEAD 2>/dev/null \
             || git -C "$upstream_dir" rev-parse origin/master 2>/dev/null \
             || git -C "$upstream_dir" rev-parse origin/main 2>/dev/null)
    [ -n "$REMOTE" ] || { log "$name: cannot resolve remote HEAD"; continue; }

    RECORDED=$(grep "^$name=" "$STATE" 2>/dev/null | tail -1 | cut -d= -f2)

    if [ "$REMOTE" = "$RECORDED" ]; then
        log "$name: unchanged ($REMOTE)"
        continue
    fi

    log "$name: CHANGED  ${RECORDED:-<none>} -> $REMOTE"
    CHANGED=1
    SUMMARY="${SUMMARY}${name}: ${RECORDED:-<none>} -> ${REMOTE}; "

    # Update the working copy the corpus is built from.
    #
    # Two shapes exist here and they must not be conflated:
    #   * the clone IS the tree the corpus reads (cscs-docs) — updating it is just a
    #     checkout, and copying "from itself" would delete the docs;
    #   * the corpus reads a separate export (firecrest-v2-src/docs) fed from a clone
    #     kept elsewhere — that one needs the copy.
    git -C "$upstream_dir" checkout --quiet "$REMOTE" 2>/dev/null || true
    if [ "$local_dir" != "$upstream_dir" ]; then
        if [ -d "$upstream_dir/docs" ]; then
            rm -rf "$local_dir/docs"
            mkdir -p "$local_dir"
            cp -r "$upstream_dir/docs" "$local_dir/"
            log "  updated $local_dir/docs from $REMOTE"
        fi
    else
        log "  checked out $REMOTE in place (the clone is the corpus source)"
    fi
done

if [ "$CHANGED" -eq 0 ]; then
    log "No documentation changes. Nothing to do."
    exit 0
fi

# Record the revisions we have now acted on.
: > "$STATE"
for spec in "${SOURCE_SPECS[@]}"; do
    IFS='|' read -r _local_dir upstream_dir url <<< "$spec"
    name=$(basename "$url")
    rev=$(git -C "$upstream_dir" rev-parse HEAD 2>/dev/null)
    [ -n "$rev" ] && echo "$name=$rev" >> "$STATE"
done

log "Changes detected — starting firecrest-reingest.service"
# --no-block: for a Type=oneshot unit, a plain `start` blocks until the service
# exits. The ingestion runs for hours; this script's own unit has
# TimeoutStartSec=20min and would be killed long before the ingestion finishes,
# taking this watcher down with it (it does not affect the ingestion itself,
# which is a separate unit with TimeoutStartSec=infinity — but it leaves
# firecrest-docs-watch.service in a failed state every time it fires a change).
# --no-block enqueues the job and returns immediately, keeping this script short.
systemctl --user start --no-block firecrest-reingest.service 2>&1 | sed 's/^/  /'
log "delta: $SUMMARY"
log "done — systemd owns the ingestion from here"
exit 0
