#!/usr/bin/env bash
# Deterministic replacement for the 07:00 "nightly report" autopilot (VDLP-19).
#
# No LLM anywhere in this path. It is a template filled from git/GitHub/host/
# container evidence.
#
# Unlike the delta check (docs_delta_check.sh), a missed report is NOT
# self-healing by default: the source data (the host ingestion log) is append-
# only but the "which night is next" state has to live somewhere. This script
# keeps that state in the issue tracker itself — it searches for the most
# recent issue titled "nightly report — YYYY-MM-DD" and reports on the FIRST
# night after that date, not "last night". A skipped morning is therefore
# recovered on the following run instead of being lost, at the cost of the
# report sometimes running a day "behind" wall-clock time until it catches up.
#
# CRITICAL per the autopilot's original description: the host result file
# (docmind-corpus-full.result.json) and the container's live CURRENT pointer
# can diverge — an ingestion killed on the host can keep running detached
# inside the container and promote CURRENT after the host process is gone.
# This script reads BOTH, every run, and reports both when they disagree
# rather than picking one. That evidence is destructive: result.json is
# overwritten by the next ingestion run, so a divergence not captured now is
# not recoverable later. This is why runtime pointer capture happens
# unconditionally, before any git/API failure could short-circuit the run.
#
# Usage:
#   nightly_report.sh                     # normal run: derive target date from
#                                          # the last "nightly report — DATE" issue
#   nightly_report.sh --date YYYY-MM-DD   # override the target date (testing)
#   nightly_report.sh --dry-run           # compute and print the report, skip
#                                          # the multica issue create call
#
# Never writes to eth-cscs/cscs-docs. Never commits secrets.

# shellcheck disable=SC2016
# The printf format strings below are markdown and contain literal backticks.
# Single quotes are required, not incidental: with double quotes the shell would
# treat those backticks as command substitution. Values are passed as printf
# arguments (%s), never interpolated into the format string.

set -uo pipefail

TZ_NAME="Europe/Zurich"
CONTAINER="${CONTAINER:-docmind-ai-llm-app-1}"
HOST_LOG="${HOST_LOG:-/home/gavadala/Sviluppo/docmind-corpus-full.log}"
RESULT_FILE="${RESULT_FILE:-/home/gavadala/Sviluppo/docmind-corpus-full.result.json}"
DOCKER() { sg docker -c "$*"; }

# multica issue create --description-file refuses a path outside the CLI's
# current working directory (MUL-4252, anti stale-file-from-another-run
# guard). mktemp's default (/tmp) is always outside it, so report files are
# staged in a scratch dir under the CALLER's cwd instead — this matches how
# the systemd units invoke these scripts (WorkingDirectory=the repo clone),
# so `multica`'s cwd and this scratch dir are the same tree.
SCRATCH_DIR="${SCRATCH_DIR:-$PWD/.reports}"
mkdir -p "$SCRATCH_DIR"
mk_report() { mktemp "$SCRATCH_DIR/report.XXXXXX.md"; }

TARGET_DATE=""
DRY_RUN=0
while [ $# -gt 0 ]; do
    case "$1" in
        --date) TARGET_DATE="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

log() { printf '%s  %s\n' "$(date -u '+%Y-%m-%d %H:%M:%S UTC')" "$*" >&2; }

fail() {
    local stage="$1" cmd="$2" err="$3"
    log "FAILED at ${stage}"
    log "  command: ${cmd}"
    log "  error:   ${err}"
    local report
    report="$(mk_report)"
    {
        printf '## nightly report — FAILED (%s)\n\n' "$(date -u +%F)"
        printf 'Stage: **%s**\n\n' "$stage"
        printf 'Command:\n```\n%s\n```\n\n' "$cmd"
        printf 'Error (verbatim):\n```\n%s\n```\n' "$err"
    } > "$report"
    if [ "$DRY_RUN" -eq 1 ]; then
        log "--dry-run: would have filed:"
        cat "$report" >&2
    else
        multica issue create \
            --title "Bibliotecario: nightly report failed — $(date -u +%F)" \
            --description-file "$report" \
            --priority high \
            --status todo 2>&1 | sed 's/^/  /'
    fi
    rm -f "$report"
    exit 1
}

# ------------------------------------------------ 0. capture live pointers FIRST
# result.json and CURRENT are both mutable/overwritten by the next ingestion run.
# Capture them before anything else can fail and lose the chance.
log "--- capturing live state (before anything else can fail)"

# jq_field <json> <filter> <fallback>: evaluate a jq filter against possibly-
# malformed JSON, returning fallback (not a mix of partial stdout plus an
# unrelated error string) when parsing fails. A plain `x=$(jq ...) || x=fallback`
# does not work here: on a parse error jq can still emit partial stdout before
# failing, which lands in $x, while `|| echo fallback` only prints alongside it
# instead of replacing it.
jq_field() {
    local json="$1" filter="$2" fallback="$3" out rc
    out=$(printf '%s' "$json" | jq -r "$filter" 2>/dev/null)
    rc=$?
    if [ "$rc" -eq 0 ]; then
        printf '%s' "$out"
    else
        printf '%s' "$fallback"
    fi
}

RESULT_JSON="{}"
RESULT_PARSE_ERROR=""
if [ -f "$RESULT_FILE" ]; then
    RESULT_JSON=$(cat "$RESULT_FILE" 2>&1) || RESULT_JSON="{}"
    if ! printf '%s' "$RESULT_JSON" | jq -e . >/dev/null 2>&1; then
        RESULT_PARSE_ERROR=$(printf '%s' "$RESULT_JSON" | jq -e . 2>&1 >/dev/null)
        log "  WARNING: $RESULT_FILE is not valid JSON: $RESULT_PARSE_ERROR"
    fi
else
    log "  no result file at $RESULT_FILE"
fi
RESULT_OK=$(jq_field "$RESULT_JSON" '.ok // "unknown"' "unparseable")
RESULT_SNAPSHOT_BEFORE=$(jq_field "$RESULT_JSON" '.snapshot_before // "?"' "?")
RESULT_SNAPSHOT_AFTER=$(jq_field "$RESULT_JSON" '.snapshot_after // "?"' "?")
RESULT_FINISHED_AT=$(jq_field "$RESULT_JSON" '.finished_at // "?"' "?")

LIVE_CURRENT=$(DOCKER "docker exec $CONTAINER cat /app/data/storage/CURRENT" 2>&1 | tr -d '\r')
LIVE_CURRENT_RC=$?
CONTAINER_NOW_UTC=$(DOCKER "docker exec $CONTAINER date -u +%FT%TZ" 2>&1 | tr -d '\r')
log "  result.json: ok=$RESULT_OK before=$RESULT_SNAPSHOT_BEFORE after=$RESULT_SNAPSHOT_AFTER finished_at=$RESULT_FINISHED_AT"
if [ "$LIVE_CURRENT_RC" -eq 0 ]; then
    log "  live CURRENT (container): $LIVE_CURRENT   (container clock: $CONTAINER_NOW_UTC, host: $(date -u +%FT%TZ))"
else
    log "  could not read live CURRENT from container: $LIVE_CURRENT"
    LIVE_CURRENT="<unreadable: $LIVE_CURRENT>"
fi

DIVERGED=0
if [ "$LIVE_CURRENT_RC" -eq 0 ] && [ "$RESULT_SNAPSHOT_AFTER" != "?" ] && [ "$RESULT_SNAPSHOT_AFTER" != "$LIVE_CURRENT" ]; then
    DIVERGED=1
    log "  DIVERGENCE: result.json says snapshot_after=$RESULT_SNAPSHOT_AFTER but live CURRENT=$LIVE_CURRENT"
fi

# ---------------------------------------------------------- 1. target date
if [ -z "$TARGET_DATE" ]; then
    log "--- deriving target date from the last filed 'nightly report — DATE' issue"
    SEARCH_OUT=$(multica issue search "nightly report" --include-closed --output json --limit 50 2>&1)
    SEARCH_RC=$?
    [ "$SEARCH_RC" -eq 0 ] || fail "target-date" "multica issue search 'nightly report' --include-closed --output json --limit 50" "$SEARCH_OUT"

    LAST_DATE=$(printf '%s' "$SEARCH_OUT" \
        | jq -r '.issues[].title' 2>/dev/null \
        | grep -E '^nightly report — [0-9]{4}-[0-9]{2}-[0-9]{2}' \
        | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' \
        | sort -u | tail -1)

    if [ -z "$LAST_DATE" ]; then
        log "  no prior 'nightly report — DATE' issue found — first run, targeting today"
        TARGET_DATE=$(date -u +%F)
    else
        log "  last report filed for: $LAST_DATE"
        TARGET_DATE=$(date -u -d "$LAST_DATE + 1 day" +%F)
    fi
fi
log "  target date: $TARGET_DATE"

# The night this report covers is target_date-1 22:00 through target_date 08:00,
# Europe/Zurich (matches the 22:00/23:00 nightly schedule with slack either side).
PREV_DATE=$(date -u -d "$TARGET_DATE - 1 day" +%F)
WINDOW_START_EPOCH=$(date -d "TZ=\"$TZ_NAME\" $PREV_DATE 20:00:00" +%s)
WINDOW_END_EPOCH=$(date -d "TZ=\"$TZ_NAME\" $TARGET_DATE 08:00:00" +%s)
NOW_EPOCH=$(date +%s)

if [ "$NOW_EPOCH" -lt "$WINDOW_END_EPOCH" ]; then
    log "Window for $TARGET_DATE (ends $(date -d @"$WINDOW_END_EPOCH")) has not closed yet (now: $(date)). Nothing to report yet — will retry as the same target next run."
    exit 0
fi

# ---------------------------------------------------------- 2. delta cross-ref
log "--- looking up the delta check issue for $PREV_DATE"
DELTA_TITLE="docs delta — ${PREV_DATE}"
DELTA_SEARCH=$(multica issue search "docs delta" --include-closed --output json --limit 50 2>&1)
DELTA_RC=$?
[ "$DELTA_RC" -eq 0 ] || fail "delta-lookup" "multica issue search 'docs delta' --include-closed --output json --limit 50" "$DELTA_SEARCH"

DELTA_MATCH=$(printf '%s' "$DELTA_SEARCH" | jq -r --arg t "$DELTA_TITLE" '.issues[] | select(.title == $t) | .identifier' 2>/dev/null | head -1)
if [ -z "$DELTA_MATCH" ]; then
    DELTA_LINE="No issue titled \`$DELTA_TITLE\` found — the delta check did not run (or has not yet). Not inferring whether there was a delta."
else
    DELTA_STATUS=$(printf '%s' "$DELTA_SEARCH" | jq -r --arg t "$DELTA_TITLE" '.issues[] | select(.title == $t) | .status' 2>/dev/null | head -1)
    DELTA_LINE="\`$DELTA_MATCH\` — \"$DELTA_TITLE\" (status: $DELTA_STATUS)."
fi
log "  $DELTA_LINE"

# ---------------------------------------------------------- 3. ingestion log window
log "--- scanning $HOST_LOG for runs in [$PREV_DATE 20:00, $TARGET_DATE 08:00] $TZ_NAME"
LOG_SECTION=""
if [ -f "$HOST_LOG" ]; then
    LOG_SECTION=$(awk -v start="$WINDOW_START_EPOCH" -v end="$WINDOW_END_EPOCH" -v tz="$TZ_NAME" '
        function to_epoch(ts,    cmd, epoch) {
            cmd = "date -d \"TZ=\\\"" tz "\\\" " ts "\" +%s 2>/dev/null"
            cmd | getline epoch
            close(cmd)
            return epoch
        }
        /^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}/ {
            ts = substr($0, 1, 19)
            epoch = to_epoch(ts)
            in_window = (epoch >= start && epoch <= end)
        }
        in_window { print }
    ' "$HOST_LOG")
else
    log "  no host log at $HOST_LOG"
fi

RUN_STARTS=$(printf '%s' "$LOG_SECTION" | grep -c '=== DocMind full re-ingestion ===' || true)
RUN_OK=$(printf '%s' "$LOG_SECTION" | grep -c '^[0-9-]* [0-9:]*  OK: new snapshot' || true)
RUN_FATAL=$(printf '%s' "$LOG_SECTION" | grep '  FATAL:' || true)
RUN_LAST_LINES=$(printf '%s' "$LOG_SECTION" | grep -E 'OK: new snapshot|FATAL:|ingestion exit=' || true)

if [ "$RUN_STARTS" -eq 0 ]; then
    INGEST_SUMMARY="No ingestion run started in this window. Either nothing was scheduled or the timer did not fire — check \`systemctl --user list-timers firecrest-reingest.timer\`."
else
    INGEST_SUMMARY="${RUN_STARTS} run(s) started in this window, ${RUN_OK} reported success (\"OK: new snapshot\")."
fi
log "  $INGEST_SUMMARY"

# ---------------------------------------------------------- 4. compose report
REPORT=$(mk_report)
{
    printf '## nightly report — %s\n\n' "$TARGET_DATE"

    if [ "$DIVERGED" -eq 1 ]; then
        printf '**HEADLINE: `result.json` and the live `CURRENT` pointer disagree.**\n\n'
        printf -- '- `result.json` snapshot_after: `%s` (finished_at %s)\n' "$RESULT_SNAPSHOT_AFTER" "$RESULT_FINISHED_AT"
        printf -- '- live `CURRENT` in the container: `%s`\n' "$LIVE_CURRENT"
        printf -- '- `query_docs` is actually serving: **`%s`** (the container CURRENT pointer, not result.json)\n\n' "$LIVE_CURRENT"
    elif [ "$RESULT_OK" = "unparseable" ]; then
        printf '**HEADLINE: `result.json` could not be parsed, so it cannot be compared against the live `CURRENT` pointer.**\n\n'
        printf -- '- live `CURRENT` in the container: `%s`\n' "$LIVE_CURRENT"
        printf -- '- `query_docs` is actually serving: **`%s`**\n\n' "$LIVE_CURRENT"
    fi

    printf '### 1. Delta check (%s)\n\n%s\n\n' "$PREV_DATE" "$DELTA_LINE"

    printf '### 2. Ingestion log (%s)\n\n%s\n\n' "$HOST_LOG" "$INGEST_SUMMARY"
    if [ -n "$RUN_LAST_LINES" ]; then
        printf '```\n%s\n```\n\n' "$RUN_LAST_LINES"
    fi
    if [ -n "$RUN_FATAL" ]; then
        printf '**Failures found in this window:**\n```\n%s\n```\n\n' "$RUN_FATAL"
    fi

    printf '### 3. Host result file (%s)\n\n' "$RESULT_FILE"
    if [ -n "$RESULT_PARSE_ERROR" ]; then
        printf '**WARNING: this file is not valid JSON** (`jq` error: `%s`). Raw content shown below; fields parsed from it above may be defaulted/unknown.\n\n' "$RESULT_PARSE_ERROR"
    fi
    printf '```json\n%s\n```\n\n' "$RESULT_JSON"

    printf '### 4. Live snapshot pointer (container: %s)\n\n' "$CONTAINER"
    printf -- '- `CURRENT`: `%s`\n' "$LIVE_CURRENT"
    printf -- '- container clock (UTC): `%s`, host clock: `%s` (converted for comparison)\n\n' "$CONTAINER_NOW_UTC" "$(date -u +%FT%TZ)"

    if [ "$DIVERGED" -eq 0 ] && [ "$RESULT_OK" != "unparseable" ] && [ "$RUN_STARTS" -gt 0 ] && [ -z "$RUN_FATAL" ]; then
        printf '### Summary\n\nAll green and consistent: delta check present, ingestion ran, result.json and live CURRENT agree.\n'
    fi

    printf -- '\n---\n_Generated by scripts/nightly_report.sh — no LLM in this path._\n'
} > "$REPORT"

log "--- report composed ($(wc -l < "$REPORT") lines)"

if [ "$DRY_RUN" -eq 1 ]; then
    log "--dry-run: printing report instead of filing it"
    cat "$REPORT"
    rm -f "$REPORT"
    exit 0
fi

CREATE_OUT=$(multica issue create \
    --title "nightly report — ${TARGET_DATE}" \
    --description-file "$REPORT" \
    --status "done" \
    --output json 2>&1)
CREATE_RC=$?
rm -f "$REPORT"
[ "$CREATE_RC" -eq 0 ] || fail "issue create" "multica issue create --title 'nightly report — ${TARGET_DATE}' --description-file <report> --status done --output json" "$CREATE_OUT"

ISSUE_IDENTIFIER=$(printf '%s' "$CREATE_OUT" | jq -r '.identifier')
ISSUE_ID=$(printf '%s' "$CREATE_OUT" | jq -r '.id')
log "  filed: $ISSUE_IDENTIFIER ($ISSUE_ID)"
log "done"
exit 0
