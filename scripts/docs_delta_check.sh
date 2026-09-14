#!/usr/bin/env bash
# Deterministic replacement for the 22:00 "docs delta" autopilot (VDLP-19).
#
# No LLM anywhere in this path. It is a template filled from git/GitHub state.
#
# What it does:
#   1. Read the baseline commit recorded at .bibliotecario/last-source-commit on
#      origin/main of VadaLinux76/cscs-knowledge (read-only: `git show`, no checkout).
#   2. Fetch eth-cscs/cscs-docs and compare its origin/main HEAD against that baseline.
#   3. Unchanged -> log and exit 0. No issue, no comment. The baseline lives in git,
#      not in this schedule, so a missed or no-op night self-heals: the next run
#      diffs from the same point and can never lose a delta.
#   4. Changed -> ask GitHub for the file-level diff between the two commits and file
#      a "docs delta — {date}" issue (unassigned — this script never starts an agent
#      run) with the list of changed files, then close it `done`: the script's own
#      output is the whole deliverable, nothing further needs doing on that issue.
#
# What it deliberately does NOT do: it does not advance
# .bibliotecario/last-source-commit. That file only moves once the wiki has actually
# been regenerated from the new source (Bibliotecario's own contract, step 4, in its
# own PR) — advancing it here would let a delta be "seen" without ever being acted on.
#
# Never writes to eth-cscs/cscs-docs (fetch only). Never commits secrets.
#
# Usage:
#   docs_delta_check.sh                            # normal run
#   docs_delta_check.sh <baseline-sha>              # override the baseline read from
#                                                    # git — for testing the "changed"
#                                                    # path against real history
#                                                    # without touching real state.
#   docs_delta_check.sh <baseline-sha> --dry-run    # same, but print the report
#                                                    # instead of filing an issue.

set -uo pipefail

CSCS_DOCS_DIR="${CSCS_DOCS_DIR:-/home/gavadala/Sviluppo/cscs-docs}"
KNOWLEDGE_DIR="${KNOWLEDGE_DIR:-/home/gavadala/Sviluppo/cscs-knowledge}"
UPSTREAM_REPO="${UPSTREAM_REPO:-eth-cscs/cscs-docs}"
BASELINE_PATH=".bibliotecario/last-source-commit"

# multica issue create --description-file refuses a path outside the CLI's
# current working directory (MUL-4252, anti stale-file-from-another-run
# guard). mktemp's default (/tmp) is always outside it, so report files are
# staged in a scratch dir under the CALLER's cwd instead — this matches how
# the systemd units invoke these scripts (WorkingDirectory=the repo clone),
# so `multica`'s cwd and this scratch dir are the same tree.
SCRATCH_DIR="${SCRATCH_DIR:-$PWD/.reports}"
mkdir -p "$SCRATCH_DIR"
mk_report() { mktemp "$SCRATCH_DIR/report.XXXXXX.md"; }

BASELINE_OVERRIDE=""
DRY_RUN=0
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        *) BASELINE_OVERRIDE="$1"; shift ;;
    esac
done

log() { printf '%s  %s\n' "$(date -u '+%Y-%m-%d %H:%M:%S UTC')" "$*"; }

# A truly fatal failure (can't even read state) still gets reported as an issue,
# not swallowed. Best-effort: if `multica` itself is unreachable this still exits
# non-zero with the error on stderr/stdout for whoever runs the unit to see.
fail() {
    local stage="$1" cmd="$2" err="$3"
    log "FAILED at ${stage}"
    log "  command: ${cmd}"
    log "  error:   ${err}"
    local report
    report="$(mk_report)"
    {
        printf '## docs delta check — FAILED (%s)\n\n' "$(date -u +%F)"
        printf 'Stage: **%s**\n\n' "$stage"
        printf 'Command:\n```\n%s\n```\n\n' "$cmd"
        printf 'Error (verbatim):\n```\n%s\n```\n' "$err"
    } > "$report"
    multica issue create \
        --title "Bibliotecario: docs delta check failed — $(date -u +%F)" \
        --description-file "$report" \
        --priority high \
        --status todo 2>&1 | sed 's/^/  /'
    rm -f "$report"
    exit 1
}

# ---------------------------------------------------------------- 1. baseline
if [ -n "$BASELINE_OVERRIDE" ]; then
    log "--- using baseline override (test mode): $BASELINE_OVERRIDE"
    BASELINE="$BASELINE_OVERRIDE"
else
    log "--- reading baseline from origin/main:${BASELINE_PATH} (${KNOWLEDGE_DIR})"
    [ -d "$KNOWLEDGE_DIR/.git" ] || fail "baseline" "test -d $KNOWLEDGE_DIR/.git" "no git checkout at $KNOWLEDGE_DIR"

    FETCH_ERR=$(git -C "$KNOWLEDGE_DIR" fetch --quiet origin main 2>&1)
    FETCH_RC=$?
    [ "$FETCH_RC" -eq 0 ] || fail "baseline" "git -C $KNOWLEDGE_DIR fetch origin main" "$FETCH_ERR"

    BASELINE=$(git -C "$KNOWLEDGE_DIR" show "origin/main:${BASELINE_PATH}" 2>&1)
    BASELINE_RC=$?
    [ "$BASELINE_RC" -eq 0 ] || fail "baseline" "git -C $KNOWLEDGE_DIR show origin/main:${BASELINE_PATH}" "$BASELINE"
    BASELINE=$(printf '%s' "$BASELINE" | tr -d '[:space:]')
    [ -n "$BASELINE" ] || fail "baseline" "git show origin/main:${BASELINE_PATH}" "file exists but is empty"
fi
log "  baseline: $BASELINE"

# ---------------------------------------------------------------- 2. current
log "--- fetching ${UPSTREAM_REPO} (${CSCS_DOCS_DIR})"
[ -d "$CSCS_DOCS_DIR/.git" ] || fail "fetch" "test -d $CSCS_DOCS_DIR/.git" "no git checkout at $CSCS_DOCS_DIR"

FETCH_ERR=$(git -C "$CSCS_DOCS_DIR" fetch --quiet origin main 2>&1)
FETCH_RC=$?
[ "$FETCH_RC" -eq 0 ] || fail "fetch" "git -C $CSCS_DOCS_DIR fetch origin main" "$FETCH_ERR"

CURRENT=$(git -C "$CSCS_DOCS_DIR" rev-parse origin/main 2>&1)
CURRENT_RC=$?
[ "$CURRENT_RC" -eq 0 ] || fail "fetch" "git -C $CSCS_DOCS_DIR rev-parse origin/main" "$CURRENT"
log "  current : $CURRENT"

# ---------------------------------------------------------------- 3. compare
if [ "$BASELINE" = "$CURRENT" ]; then
    log "No change since baseline. Nothing to do — posting nothing."
    exit 0
fi

log "CHANGED: $BASELINE -> $CURRENT"

# ---------------------------------------------------------- 4. diff + file issue
DIFF_JSON=$(gh api "repos/${UPSTREAM_REPO}/compare/${BASELINE}...${CURRENT}" 2>&1)
DIFF_RC=$?
[ "$DIFF_RC" -eq 0 ] || fail "diff" "gh api repos/${UPSTREAM_REPO}/compare/${BASELINE}...${CURRENT}" "$DIFF_JSON"

FILES=$(printf '%s' "$DIFF_JSON" | jq -r '.files[] | "- `" + .filename + "` (" + .status + ", +" + (.additions|tostring) + "/-" + (.deletions|tostring) + ")"' 2>&1)
JQ_RC=$?
[ "$JQ_RC" -eq 0 ] || fail "diff" "jq -r '.files[]...' (parsing gh api compare response)" "$FILES"

FILE_COUNT=$(printf '%s' "$DIFF_JSON" | jq -r '.files | length')
TOTAL_COMMITS=$(printf '%s' "$DIFF_JSON" | jq -r '.total_commits')

REPORT=$(mk_report)
{
    printf '## docs delta — %s\n\n' "$(date -u +%F)"
    printf '`%s` HEAD moved: `%s` -> `%s` (%s commit(s), %s file(s) changed).\n\n' \
        "$UPSTREAM_REPO" "$BASELINE" "$CURRENT" "$TOTAL_COMMITS" "$FILE_COUNT"
    printf 'Compare: https://github.com/%s/compare/%s...%s\n\n' "$UPSTREAM_REPO" "$BASELINE" "$CURRENT"
    printf '### Changed files\n\n%s\n\n' "$FILES"
    printf -- '---\n_Generated by scripts/docs_delta_check.sh — no LLM in this path._\n\n'
    printf '_Note: this issue records the delta only. The knowledge-base baseline (`%s`) advances separately, when the wiki is actually regenerated from this source._\n' "$BASELINE_PATH"
} > "$REPORT"

log "--- filing issue"
if [ "$DRY_RUN" -eq 1 ]; then
    log "--dry-run: would have filed issue with this description:"
    cat "$REPORT"
    rm -f "$REPORT"
    exit 0
fi
CREATE_OUT=$(multica issue create \
    --title "docs delta — $(date -u +%F)" \
    --description-file "$REPORT" \
    --status "done" \
    --output json 2>&1)
CREATE_RC=$?
rm -f "$REPORT"
[ "$CREATE_RC" -eq 0 ] || fail "issue create" "multica issue create --title 'docs delta — $(date -u +%F)' --description-file <report> --status done --output json" "$CREATE_OUT"

ISSUE_IDENTIFIER=$(printf '%s' "$CREATE_OUT" | jq -r '.identifier')
ISSUE_ID=$(printf '%s' "$CREATE_OUT" | jq -r '.id')
log "  filed: $ISSUE_IDENTIFIER ($ISSUE_ID)"
log "done"
exit 0
