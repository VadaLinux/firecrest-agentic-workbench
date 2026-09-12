#!/usr/bin/env bash
# Re-ingest the full DocMind corpus: CSCS docs + FirecREST v2 docs + job logs.
#
# Run by firecrest-reingest.service (a systemd user timer), not by hand, though it
# works by hand too. It is written to be the whole job: build the corpus, stage it
# into the container, run the ingestion, then verify and report.
#
# Why a systemd unit rather than an agent run: the ingestion takes minutes to hours
# and must not be orphaned when some session's turn ends. systemd owns it, logs it,
# and does not care whether anyone is watching.
#
# Why the corpus is rebuilt every time rather than reused: the previous corpus was
# built by a script that dropped every file whose name had no supported extension,
# which silently discarded the Containerfiles and environment-variable lists under
# docs/software/. scripts/build_corpus.py renames those to .txt instead of dropping
# them, so the rebuild is how the gap gets closed. It also brings in the v2 docs,
# which the corpus never had.
#
# Exit status: 0 only when a NEW snapshot was activated. Anything else is a failure
# and is reported as one.

set -uo pipefail

REPO="${REPO:-/home/gavadala/Sviluppo/firecrest-agentic-workbench}"
CSCS_DOCS="${CSCS_DOCS:-/home/gavadala/Sviluppo/cscs-docs/docs}"
V2_DOCS="${V2_DOCS:-/home/gavadala/Sviluppo/firecrest-v2-src/docs}"
V2_SPEC="${V2_SPEC:-/home/gavadala/Sviluppo/firecrest-v2-corpus/openapi.json}"
# Job logs written by firecrest-mcp's get_job_log. AGENTS.md names this directory as
# what DocMind ingests for the failure-scenario demo, but this script had no source
# pointing at it, so neither a manual run nor the timer ever picked a log up. The
# `.log` suffix is renamed to `.txt` by build_corpus.py; byte-identical logs (the
# trivial `hello` submissions) are dropped by its content dedup rather than
# hard-erroring the way docmind_ingest.py does.
LOGS="${LOGS:-$REPO/firecrest-mcp/logs}"
STAGE="${STAGE:-/home/gavadala/Sviluppo/docmind-corpus-full}"
CONTAINER="${CONTAINER:-docmind-ai-llm-app-1}"
RESULT_FILE="${RESULT_FILE:-/home/gavadala/Sviluppo/docmind-corpus-full.result.json}"
LOG="${LOG:-/home/gavadala/Sviluppo/docmind-corpus-full.log}"

exec > >(tee -a "$LOG") 2>&1

log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
die() { log "FATAL: $*"; printf '{"ok": false, "stage": "%s", "error": "%s"}\n' "${1:-unknown}" "${2:-$*}" > "$RESULT_FILE"; exit 1; }

# Docker needs the group workaround on this host.
DOCKER() { sg docker -c "$*"; }

log "=== DocMind full re-ingestion ==="

# ---------------------------------------------------------------- 1. build corpus
log "--- 1/5 building the corpus"
[ -d "$CSCS_DOCS" ] || die "corpus" "CSCS docs not found at $CSCS_DOCS"
[ -d "$V2_DOCS" ]   || die "corpus" "v2 docs not found at $V2_DOCS"

SOURCES=("$CSCS_DOCS:docs" "$V2_DOCS:v2docs")
# Job logs are optional: a fresh checkout has none. Include them when the directory
# is there so the timer picks up whatever get_job_log has written since.
[ -d "$LOGS" ] && SOURCES+=("$LOGS:joblogs")
# The v2 OpenAPI spec is optional but valuable; include it when present.
if [ -f "$V2_SPEC" ]; then
    cp -f "$V2_SPEC" "$V2_DOCS/firecrest-v2-openapi.json" 2>/dev/null || true
fi

python3 "$REPO/scripts/build_corpus.py" --out "$STAGE" "${SOURCES[@]}" \
    || die "corpus" "build_corpus.py failed"

STAGED=$(find "$STAGE" -maxdepth 1 -type f | wc -l)
log "  staged $STAGED files"
[ "$STAGED" -gt 100 ] || die "corpus" "only $STAGED files staged, expected hundreds"

# ------------------------------------------------------------------ 2. container up
log "--- 2/5 checking the app container"
DOCKER "docker inspect $CONTAINER >/dev/null 2>&1" \
    || die "container" "$CONTAINER not found"
HEALTH=$(DOCKER "docker inspect $CONTAINER --format '{{.State.Health.Status}}'" | tr -d '\r')
log "  health: $HEALTH"
[ "$HEALTH" = "healthy" ] || die "container" "$CONTAINER is $HEALTH, not healthy"

# ------------------------------------------------- 3. stage into the container
log "--- 3/5 staging corpus into the container"
DOCKER "docker exec $CONTAINER rm -rf /app/data/corpus-full" || die "staging" "could not clear old corpus"
DOCKER "docker cp '$STAGE' $CONTAINER:/app/data/corpus-full" || die "staging" "docker cp failed"

# The ingestion script is docker-cp'd, not mounted, so a recreated container loses
# it. Copy it every time — that failure mode is exactly why this is a step.
DOCKER "docker cp '$REPO/scripts/docmind_ingest.py' $CONTAINER:/app/docmind_ingest.py" \
    || die "staging" "could not copy docmind_ingest.py"

IN=$(DOCKER "docker exec $CONTAINER sh -c 'ls /app/data/corpus-full | wc -l'" | tr -d '\r')
log "  $IN files in the container"
[ "$IN" -gt 100 ] || die "staging" "only $IN files landed in the container"

# --------------------------------------------------------------- dry-run stop
if [ "${DRY_RUN:-0}" = "1" ]; then
    log "DRY_RUN=1 — everything up to the ingestion is verified; stopping here."
    BEFORE=$(DOCKER "docker exec $CONTAINER cat /app/data/storage/CURRENT" | tr -d '\r')
    log "  snapshot would be compared against: $BEFORE"
    log "  staged file sample in container:"
    DOCKER "docker exec $CONTAINER sh -c 'ls /app/data/corpus-full | head -5'" | sed 's/^/    /'
    DOCKER "docker exec $CONTAINER sh -c 'ls /app/data/corpus-full | tail -3'" | sed 's/^/    /'
    printf '{"ok": true, "dry_run": true, "staged_files": %s, "in_container": %s, "snapshot": "%s"}\n' \
        "$STAGED" "$IN" "$BEFORE" > "$RESULT_FILE"
    exit 0
fi

# ------------------------------------------------------------------- 4. ingest
log "--- 4/5 running the ingestion (this is the long part)"
BEFORE=$(DOCKER "docker exec $CONTAINER cat /app/data/storage/CURRENT" | tr -d '\r')
log "  snapshot before: $BEFORE"

START=$(date +%s)
# Hold off suspend/idle for the duration. This runs on the HOST, not inside the
# container: the container is minimal and has no systemd-inhibit. The machine may be
# left unattended overnight, and a suspended laptop would stall the ingestion — the
# snapshot recovery would cope, but the job would silently not finish, which is worse.
systemd-inhibit --what=sleep:idle --mode=block \
    --why="DocMind corpus re-ingestion" \
    sg docker -c "docker exec $CONTAINER /app/.venv/bin/python /app/docmind_ingest.py /app/data/corpus-full"
RC=$?
ELAPSED=$(( $(date +%s) - START ))
log "  ingestion exit=$RC after $((ELAPSED / 60))m $((ELAPSED % 60))s"

# ------------------------------------------------------------------ 5. verify
log "--- 5/5 verifying"
AFTER=$(DOCKER "docker exec $CONTAINER cat /app/data/storage/CURRENT" | tr -d '\r')
log "  snapshot after : $AFTER"

VEC=$(DOCKER "docker exec $CONTAINER /app/.venv/bin/python -c \"
import qdrant_client
c = qdrant_client.QdrantClient(url='http://qdrant:6333')
import json
print(json.dumps({n: c.count(n).count for n in [x.name for x in c.get_collections().collections] if 'docmind_docs__' in n}))
\"" | tr -d '\r')

if [ "$RC" -ne 0 ]; then
    log "FAILED: ingestion exited $RC"
    printf '{"ok": false, "stage": "ingest", "exit": %s, "elapsed_s": %s, "snapshot_before": "%s", "snapshot_after": "%s", "vectors": %s}\n' \
        "$RC" "$ELAPSED" "$BEFORE" "$AFTER" "${VEC:-{}}" > "$RESULT_FILE"
    exit 1
fi

if [ "$BEFORE" = "$AFTER" ]; then
    log "FAILED: CURRENT did not change — no new snapshot was activated"
    printf '{"ok": false, "stage": "verify", "reason": "snapshot unchanged", "snapshot": "%s", "elapsed_s": %s}\n' \
        "$AFTER" "$ELAPSED" > "$RESULT_FILE"
    exit 1
fi

log "OK: new snapshot $AFTER, ingested in $((ELAPSED / 60))m"
printf '{"ok": true, "snapshot_before": "%s", "snapshot_after": "%s", "elapsed_s": %s, "staged_files": %s, "vectors": %s, "finished_at": "%s"}\n' \
    "$BEFORE" "$AFTER" "$ELAPSED" "$STAGED" "${VEC:-{}}" "$(date -Is)" > "$RESULT_FILE"
log "result written to $RESULT_FILE"
exit 0
