# Handover — state of the workbench

Written 2026-09-11 at the end of a long session. Purpose: resume tomorrow without
re-deriving anything. Read this first, then `AGENTS.md`, then the report for the
prompt you are picking up.

## Where the build stands

Prompts 01–04 are **done and documented**. 05–08 are not started.

| # | Prompt | State | Evidence |
|---|---|---|---|
| 01 | FirecREST demo stack | done | issue #1, `docs/reports/01-firecrest-demo-stack.md` |
| 02 | `firecrest-mcp` wrapper | done | issue #2, `docs/reports/02-firecrest-mcp-wrapper.md` |
| 03 | MetaMCP gateway | done | issue #3, `docs/reports/03-metamcp-setup.md` |
| 04 | DocMind corpus + `query_docs` | done | issue #4, `docs/reports/04-docmind-corpus.md` |
| 05 | Hermes ↔ MetaMCP | **next** | — |
| 06 | Multica orchestration | pending | — |
| 07 | Telegram channel | pending | needs a BotFather token from Gabriele |
| 08 | Failure scenario | pending | depends on 04 (done) — can start |

## What is running right now

| Component | Where | Health |
|---|---|---|
| FirecREST demo stack | `~/Sviluppo/firecrest/deploy/demo` | 15 services, Kong healthy, API `:8000` |
| `firecrest-mcp` | `0.0.0.0:8765` (Streamable HTTP) | 5 tools |
| MetaMCP | `:12008`, namespace `firecrest-workbench` | healthy |
| DocMind app | `:8501` | healthy, snapshot `20260911T171839-f54356c0` active |
| `docmind-mcp` | `:8770` (Streamable HTTP) | healthy, 1 tool |
| Ollama (DocMind's) | internal only | healthy, `qwen3:4b-instruct` |

The aggregated MetaMCP endpoint serves exactly **six** tools:

```
docmind__query_docs
firecrest__download_file
firecrest__get_job_log
firecrest__get_job_status
firecrest__list_files
firecrest__submit_job
```

## Bringing it all back up

Docker starts at boot, but **no compose file in this project has a restart policy**, and
MetaMCP's `app` service is explicitly `restart: "no"`. After a reboot, nothing comes
back on its own. In order:

```bash
# 1. FirecREST demo stack
cd ~/Sviluppo/firecrest/deploy/demo && sg docker -c "docker compose up -d"

# 2. MetaMCP
cd ~/Sviluppo/metamcp && sg docker -c "docker compose up -d"

# 3. DocMind (app, ollama, qdrant, docmind-mcp)
cd ~/Sviluppo/docmind-ai-llm && sg docker -c "docker compose up -d"

# 4. firecrest-mcp wrapper — HTTP mode
cd ~/Sviluppo/firecrest-agentic-workbench/firecrest-mcp
sg docker -c "docker run -d ..."   # see docs/reports/02-*.md for the exact invocation
```

`~/Sviluppo/docmind-ai-llm/docker-compose.override.yml` is a **copy** of
`docs/patches/docmind-docker-compose.override.yml` in this repo. The repo copy is the
source of truth; re-copy it after editing. Same for the MetaMCP Dockerfile patch in
`docs/patches/`.

The clones (`firecrest/`, `metamcp/`, `docmind-ai-llm/`) are gitignored, so the patches
in `docs/patches/` are the only durable record of the changes made to them.

## Verifying, component by component

```bash
# FirecREST
sg docker -c "docker compose -f ~/Sviluppo/firecrest/deploy/demo/docker-compose.yml ps"

# MetaMCP: the tool surface on the published endpoint (the only thing that counts)
cd ~/Sviluppo/firecrest-agentic-workbench
./firecrest-mcp/.venv/bin/python scripts/metamcp_admin.py tools

# DocMind stack
cd ~/Sviluppo/docmind-ai-llm && sg docker -c "docker compose ps"

# query_docs end to end through MetaMCP (~45 s on CPU)
cd ~/Sviluppo/firecrest-agentic-workbench
./firecrest-mcp/.venv/bin/python scripts/metamcp_admin.py call docmind__query_docs \
  '{"question":"Which HTTP header selects the machine in FirecREST?"}'
```

## Things that will bite if forgotten

- **`sg docker -c "..."`** — `gavadala` is in the `docker` group but the shell session
  predates it, so bare `docker` fails. Alternatively re-login.
- **`docker compose`**, never `docker-compose` — the hyphenated binary does not exist on
  this host.
- **`enable_metamcp_admin_tools` must be `false`.** It is a column on `endpoints`, not a
  `config` key. It is off now. Any task that needs MetaMCP's admin tools must switch it
  on, use them, and switch it back off before an agent is pointed at the endpoint —
  otherwise the agent sees ~50 admin tools it should never choose from.
- **Recreating a container discards `docker cp`.** The DocMind scripts are bind-mounted
  precisely to avoid this; anything else copied in is gone after `compose up -d`.
- **SELinux is enforcing.** Host files bind-mounted into containers need
  `chcon -t container_file_t <path>` or the container sees them unreadable despite
  correct Unix permissions. `restorecon` undoes it.
- **DocMind ingestion is a pre-demo step.** ~13 minutes for ~170 KB on CPU, linear in
  corpus size. Never ingest live.
- **MetaMCP caps tool calls at 60 s by default.** Raised to 600 s here
  (`MCP_TIMEOUT`, `MCP_MAX_TOTAL_TIMEOUT` in the `config` table). A fresh MetaMCP
  instance will not have this, and the symptom is a working backend that reads as broken.
- **`scripts/metamcp_admin.py` used to hardcode a 60 s client timeout.** Now
  configurable via `--timeout`, default 900 s. If a call through it times out, check
  `--timeout` before MetaMCP.

Full detail and evidence for all of these lives in the `docmind-stack` and
`mcp-gateway-configuration` Hermes skills, which were updated during this session.

## Corrections made during the session

Recorded so they are not re-derived, and so nobody acts on the retracted version:

1. **`GET /tasks/{task_id}` was reported as a hallucination and is not one.** It is
   documented in our own `docs/hot-cache.md` as the second step of a two-step flow
   (`GET /compute/acct?jobs=<id>`, then unwrap via `GET /tasks/{id}`). The error was
   one of ours, not the model's. See `04-docmind-corpus.md` §10.
2. **The offline flags are correct as shipped.** An earlier claim that `HF_HUB_OFFLINE=1`
   / `TRANSFORMERS_OFFLINE=1` would block model downloads was wrong — the Dockerfile
   bakes the models in and proves offline correctness during the build.
3. **SELinux scope is narrower than first reported.** It applies to RHEL-family *and*
   openSUSE Leap 16 / SLES 16 (SUSE changed its default), not to Ubuntu/Debian, where
   AppArmor does not enforce file labels. Corrected in report 01 and on issue #1.

## Next: prompt 05

Prompt 05 points Hermes at the MetaMCP endpoint and runs an end-to-end test. It is the
only one of the seven that **modifies the agent's own configuration** rather than a
component, which is why it was left for a fresh session with explicit agreement.

Before starting it:

- The endpoint URL and API key are in `.secrets/metamcp-api-key.txt` (gitignored, never
  print the value).
- Confirm the six-tool surface first — the test is meaningless against a different set.
- Expect `docmind__query_docs` to take ~45 s. If it times out, check the MetaMCP timeout
  settings and `metamcp_admin.py --timeout` before suspecting the tool.

## Prompt 08 can start in parallel

It depends on 04, which is done. It needs a deliberately failing job submitted through
the FirecREST stack, its log ingested into DocMind, and a diagnosis produced from it.
Two things learned today that it should account for: job logs must be staged as `.txt`
or they are silently skipped, and several identical trivial logs will be rejected as
duplicate content.
