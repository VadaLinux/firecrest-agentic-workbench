# Multica board — agents, wiring, and the documentation project

Session log for the Multica orchestration setup (2026-09-12). Read this first after
a reboot or a shutdown mid-work: it records what was built, what is pending, and the
exact commands to resume.

## Agents on the board

| Agent | Role | Runtime | Model | MCP servers |
|---|---|---|---|---|
| `Mika` | Chief of Staff (Multica built-in) | local | (default) | `docmind-readonly` |
| `Operator` | Operates FirecREST, answers from docs | Hermes (local) | `deepseek/deepseek-v4-flash-0731` | `firecrest-workbench` |

- `Operator` id `95546a46-c506-4182-b22b-658ac0cfd3cd`, Hermes runtime
  `be5af7cd-f432-49ce-b4ec-f52501904226`. No `--thinking-level` (Hermes rejects it).
  `max_concurrent_tasks: 2`. Instructions in
  `~/.multica/operator-instructions.txt` (also the source of the identity/rules).
- Mika has the read-only `docmind-readonly` server — only `query_docs`, no FirecREST
  job tools. This is the least-privilege fix that the `retrieval-vs-recall` example
  argued for.

## MCP servers in the workspace library

| name | transport | endpoint | tool surface |
|---|---|---|---|
| `firecrest-workbench` (id `f1105dba-…`) | http | `http://localhost:12008/metamcp/firecrest-workbench/mcp` | 5 FirecREST + `query_docs` |
| `docmind-readonly` (id `0a42c4bc-…`) | http | `http://localhost:8770/mcp` | only `query_docs` |

`docmind-mcp` (port 8770) exposes exactly one tool, `query_docs`, and is read-only by
construction — it is the right endpoint for a conversational agent. `Operator` gets
the full MetaMCP endpoint; `Mika` gets only the read-only one.

## Documentation project: `~/Sviluppo/cscs-knowledge`

A citation-grounded reference on how CSCS/Alps work, maintained by agents. Committed
locally as git repo `fe244a4` (branch, no remote yet — `gh` not authenticated).

- `AGENTS.md` — the contract: source-of-truth hierarchy
  (hot-cache > `query_docs` > `[unverified]`), citation rules, one-sentence-per-line,
  sentence-case headings, `## Known issues` convention.
- `sources.md` — index of sources and how to cite them.
- `sections/` — seven stub pages awaiting population.
- **Issue `VDLP-1`** (`01a0953b-c27c-70cd-af9d-dbd81a747e1c`): "Populate the CSCS
  knowledge base from query_docs", assigned to Mika, status **todo, not started**.
  Start it with:
  ```bash
  multica issue assign 01a0953b-c27c-70cd-af9d-dbd81a747e1c --to Mika
  ```
  Deferred so Mika does not contend with the corpus ingestion for CPU.

## CSCS documentation corpus ingestion

Flattened corpus (`docs/` from `eth-cscs/cscs-docs`, 137 `.md`, 1,217 KB,
path-encoded names) copied into the DocMind app container:

- corpus: `/app/data/corpus-cscs` (on the `docmind_data` volume — survives recreate)
- script: `/app/docmind_ingest.py` (docker-cp'd — **lost on container recreate**)

Run (inside the app container):
```bash
sg docker -c "docker exec docmind-ai-llm-app-1 /app/.venv/bin/python /app/docmind_ingest.py /app/data/corpus-cscs"
```
The script starts with `recover_snapshot_transactions`, so an interrupted run
recovers and can simply be re-run. If the container was recreated first, re-copy the
script:
```bash
sg docker -c "docker cp scripts/docmind_ingest.py docmind-ai-llm-app-1:/app/docmind_ingest.py"
```
The old 4-file corpus (hot-cache, API spec, job logs) merges automatically.

## DuckDB json extension

DuckDB auto-installs `json` on first use but `extensions.duckdb.org` serves only IPv6
and the Docker network is IPv4-only, so the app container cannot fetch it and
ingestion dies. Fix: fetch on the host, bind-mount read-only.

- `scripts/fetch_duckdb_json_ext.sh` downloads, verifies sha256, relabels for SELinux.
- `docs/patches/docmind-docker-compose.override.yml` binds it into the app container.
- Committed `aae6ff8`. **Not yet applied** — the container was not recreated so as
  not to interrupt the in-flight ingestion. Apply after ingestion completes:
  ```bash
  cd ~/Sviluppo/docmind-ai-llm && docker compose -f docker-compose.yml \
    -f ~/Sviluppo/firecrest-agentic-workbench/docs/patches/docmind-docker-compose.override.yml up -d app
  ```
  (then re-copy `docmind_ingest.py` if a re-ingest is needed).

## Resource notes

- Disk is at 19% (85G/464G) — not a constraint.
- The ingestion saturates all 8 threads (load ~45-48); the run was `renice`d to
  nice=10 so the laptop stays usable. Do not start another heavy agent run
  concurrently.

## Verification pending (do after ingestion)

1. Confirm a new snapshot activated (`multica`/DocMind `current_snapshot_id`).
2. Query `query_docs` on the Apertus tool-use question — the expected answer is that
   the `-thinking` variants have tool use disabled, per `services/inference/api.md`.
3. Apply the DuckDB mount (above).
4. Start `VDLP-1` for Mika.
