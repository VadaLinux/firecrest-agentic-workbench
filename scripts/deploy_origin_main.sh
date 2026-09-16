#!/usr/bin/env bash
# Safely align the working clone executed by the user units with origin/main.
#
# This is deliberately a deploy, not a reset: it only fast-forwards a clean local
# main branch.  Local tracked edits, a different branch, local-only commits and a
# divergent history are all reported and left untouched.  Untracked files (such as
# the user's .claude/ directory) are reported but do not block a safe fast-forward.

set -euo pipefail

REPO="${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOCK_FILE="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/firecrest-workbench-deploy.lock"

log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
fail() { log "FATAL: $*"; exit 1; }

[ -d "$REPO/.git" ] || fail "not a Git working tree: $REPO"

# The service takes this exclusive lock.  Each workload service holds it shared for
# its entire ExecStart, so a merge cannot change its source tree while it is running.
exec {lock_fd}>"$LOCK_FILE"
flock -x "$lock_fd"

log "checking deployment clone: $REPO"
log "fetching origin"
git -C "$REPO" fetch origin || fail "git fetch origin failed"

branch="$(git -C "$REPO" symbolic-ref --quiet --short HEAD || true)"
if [ "$branch" != "main" ]; then
    fail "refusing deployment: checked-out branch is '${branch:-detached HEAD}', expected 'main'"
fi

tracked_changes="$(git -C "$REPO" status --porcelain --untracked-files=no)"
if [ -n "$tracked_changes" ]; then
    log "tracked working-tree changes detected:"
    printf '%s\n' "$tracked_changes"
    fail "refusing deployment: tracked local work is not modified or discarded"
fi

untracked_changes="$(git -C "$REPO" status --porcelain --untracked-files=normal | awk '/^\?\?/ { print }')"
if [ -n "$untracked_changes" ]; then
    log "untracked files are present (allowed; never removed):"
    printf '%s\n' "$untracked_changes"
fi

behind="$(git -C "$REPO" rev-list --count HEAD..origin/main)"
ahead="$(git -C "$REPO" rev-list --count origin/main..HEAD)"
log "drift: behind=$behind ahead=$ahead tracked_dirty=no"

if [ "$behind" -eq 0 ] && [ "$ahead" -eq 0 ]; then
    log "deployment clone already matches origin/main"
    exit 0
fi

if [ "$ahead" -ne 0 ]; then
    fail "refusing deployment: local main is ahead of or diverged from origin/main (behind=$behind ahead=$ahead)"
fi

if ! git -C "$REPO" merge-base --is-ancestor HEAD origin/main; then
    fail "refusing deployment: origin/main is not a fast-forward of HEAD"
fi

log "fast-forwarding main by $behind commit(s)"
git -C "$REPO" merge --ff-only origin/main || fail "fast-forward failed; local work was left untouched"
log "deployment complete: $(git -C "$REPO" rev-parse --short HEAD)"
