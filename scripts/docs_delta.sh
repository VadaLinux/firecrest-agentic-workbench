#!/usr/bin/env bash
# Watch the CSCS documentation sources for changes and hand the update to systemd.
#
# Design: this script only does the CHEAP part — fetch, compare, update the working
# copies, record the new revision. It never runs the ingestion itself; it starts
# firecrest-reingest.service and lets systemd own the long job. That split exists
# because an ingestion lasts minutes to hours, and anything that backgrounds-and-yields
# from a session would be orphaned when that session's turn ends.
#
# Sequencing guard: firecrest-reingest.timer is a recurring nightly timer, so it
# ALWAYS has a pending NextElapseUSecRealtime — that value alone can't tell "the
# full run is imminent" from "the full run is 23 hours away". The guard therefore
# only skips when that next elapse is within REINGEST_GUARD_WINDOW_SEC of now. Two
# ingestions cannot overlap — the snapshot writer lock is exclusive — so when the
# scheduled full run is about to fire, it must be allowed to go first.
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

# How close to the nightly full re-ingestion's next elapse counts as "imminent"
# and worth deferring to. firecrest-reingest.timer fires once a day, so this only
# needs to be wider than how often this watcher itself runs.
REINGEST_GUARD_WINDOW_SEC="${REINGEST_GUARD_WINDOW_SEC:-5400}"  # 90 minutes

# --------------------------------------------------- guard: don't collide with the
# imminent scheduled full re-ingestion
if systemctl --user list-timers firecrest-reingest.timer --all --no-legend 2>/dev/null \
        | grep -q firecrest-reingest; then
    NEXT=$(systemctl --user show firecrest-reingest.timer -p NextElapseUSecRealtime 2>/dev/null | cut -d= -f2)
    if [ -n "$NEXT" ]; then
        NEXT_EPOCH=$(date -d "$NEXT" +%s 2>/dev/null || true)
        NOW_EPOCH=$(date +%s)
        if [ -n "$NEXT_EPOCH" ]; then
            DELTA=$(( NEXT_EPOCH - NOW_EPOCH ))
            if [ "$DELTA" -ge 0 ] && [ "$DELTA" -lt "$REINGEST_GUARD_WINDOW_SEC" ]; then
                log "SKIP: the nightly full re-ingestion is imminent ($NEXT, in $((DELTA / 60))min). Not acting."
                log "      Two ingestions cannot overlap; the full run goes first."
                exit 0
            else
                log "INFO: nightly full re-ingestion next elapse is $NEXT (in $((DELTA / 60))min, outside the ${REINGEST_GUARD_WINDOW_SEC}s guard window) — proceeding."
            fi
        else
            log "WARN: could not parse NextElapseUSecRealtime ('$NEXT') — proceeding without the timer guard."
        fi
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
