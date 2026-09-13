# Handover — state of the workbench

Written 2026-09-11, **refreshed 2026-09-13** against the live host and the workspace
board (issue VDLP-13) rather than re-derived from memory — the 09-11 snapshot below
had gone stale in several places since. Read this first, then `AGENTS.md`, then the
report for the prompt you are picking up.

## Where the build stands

Prompts 01–05 are **done and documented**. 06 is now done and verified end to end,
not just designed. 07 is genuinely still not started (blocked on a human). 08 shipped
a partial delivery.

| # | Prompt | State | Evidence |
|---|---|---|---|
| 01 | FirecREST demo stack | done | issue #1, `docs/reports/01-firecrest-demo-stack.md` |
| 02 | `firecrest-mcp` wrapper | done | issue #2, `docs/reports/02-firecrest-mcp-wrapper.md` |
| 03 | MetaMCP gateway | done | issue #3, `docs/reports/03-metamcp-setup.md` |
| 04 | DocMind corpus + `query_docs` | done | issue #4, `docs/reports/04-docmind-corpus.md` |
| 05 | Hermes ↔ MetaMCP | **done** | Multica issue `VDLP-12` ("Prompt 05: point Hermes at MetaMCP and run the e2e loop"); `docs/HERMES.md`, `docs/e2e-test-transcript.md` |
| 06 | Multica orchestration | **done and verified live** | GitHub issue #5, design in `docs/MULTICA.md`; live evidence below; GHCR tag check not yet verified |
| 07 | Telegram channel | **not started** | blocked on a human: a BotFather bot token and the Multica workspace channel binding, both done in the web UI — neither is something an agent can do |
| 08 | Failure scenario | **partial delivery** | `docs/failure-scenario-transcript.md`, Multica issue `VDLP-5` (done), merged PR #7 — see the dedicated note below; re-run through Hermes now that 05 is done to actually close it |

### Prompt 06, with evidence (verified 2026-09-13, live)

- **Multica self-host installed and running.** `multica daemon status` reports a
  running daemon (`server_url: http://localhost:8081`, uptime ~24 h) with three
  registered agent providers (`claude`, `hermes`, `codex`).
- **Hermes connected as a runtime and visible on the board.** `multica runtime list`
  shows `Hermes (VadaLinux.lan)` (`be5af7cd-f432-49ce-b4ec-f52501904226`,
  provider `hermes`) with `status: online`, alongside the Claude Code and Codex
  runtimes — all three `online` as of this check.
- **Test issue filed → picked up → worked → review, with zero manual steps beyond
  filing and approving,** happened repeatedly, not once: `VDLP-2`, `VDLP-3`, `VDLP-4`
  (parent) / `VDLP-5` (child), `VDLP-10`, and `VDLP-11` are all `status: done`, each
  with a PR opened by the assigned agent and merged after review. In this repo alone,
  `gh pr list --state all` shows PR #6 (`VDLP-3`), #7 (`VDLP-5`/prompt 08), and #8
  (`VDLP-11`) merged; `VDLP-2` and `VDLP-10` shipped the same way against the
  `cscs-knowledge` repo. `VDLP-5`'s own comment history additionally shows an agent
  run hitting a real `402 Insufficient credits` error from the Hermes provider
  mid-task and recovering to deliver — evidence the loop runs a real, billed
  inference call, not a mock.
- **GHCR tag checked — left unchecked, deliberately.** `docs/MULTICA.md` itself
  still says *"the published GHCR tag has not been checked yet. Do that before
  choosing an install path"* (line 214), and nothing else in the repo or the issue
  history records that check happening. The self-host install clearly did complete by
  some path, but there is no record of which install path was chosen or that the
  GHCR-tag precondition was verified first — so the box stays unchecked rather than
  being marked done on inference.

### Prompt 08, read carefully — it is not "done"

`docs/failure-scenario-transcript.md` (delivered by `VDLP-5`, merged as PR #7) is a
**partial** delivery of the original prompt, on two separate axes:

1. **At the time it was written, the `query_docs` half of it hadn't happened.** The
   transcript's own "DocMind retrieval check" section is explicit: ingestion of the
   transcript hit `SnapshotLockTimeoutError` (another ingestion already held the
   lock), and it says outright *"no `query_docs` question or answer is represented as
   an end-to-end verification for job 13."* The failure diagnosis itself is solid and
   evidence-backed (Slurm's own `FAILED` / exit code `3:0` agrees with the stderr
   line), but that diagnosis was produced by reading the raw scheduler and log
   output directly — not by a working `query_docs` round trip.
   **Update as of this refresh:** the corpus has since been reingested (consistent
   with the nightly reingest timer), and asking `query_docs` live just now — *"Why
   did job 13 fail on the FirecREST demo cluster?"* — retrieves the transcript and
   answers correctly, citing it. So the gap the transcript records is closed in
   practice, but **the transcript file itself has not been updated to say so** — a
   reader of that file alone would still believe the verification never happened.
2. **It does not route through Hermes/MetaMCP or Telegram**, which are steps 2 and 4
   of the original prompt 08 spec. Grepping the transcript for `Hermes`, `MetaMCP`,
   and `Telegram` returns zero matches — every tool call in it (`list_files`,
   `submit_job`, `get_job_status`, `get_job_log`) is recorded as a direct FirecREST
   tool call, with no channel attribution at all. This is a rehearsal of the
   diagnosis logic, not an end-to-end channel test.

**Side track, outside this prompt sequence:** production Alps runs FirecREST v2, not
the v1.16.1 everything above is built against. This has moved further than the 09-11
snapshot recorded:

- The v2 demo was brought up and gap-analysed — `docs/hot-cache-v2.md` and
  `docs/reports/05-firecrest-v2-gap.md` — and that work **has since landed on
  `main`** (`VDLP-3`, merged PR #6), not merely written up separately as before.
- A real v2 client shipped — `firecrest-mcp/client_v2.py` (`VDLP-11`, merged PR #8) —
  and `firecrest-mcp/server.py` now switches between `client.py` and `client_v2.py`
  on `FIRECREST_API_VERSION` (`v1` by default, `v2` opt-in). So this is no longer
  "written but nothing changed as a result": v1 stays the default and untouched, and
  v2 is a real, selectable code path, just not the one wired into the running
  `firecrest-mcp.service` today.

## What is running right now

Re-verified 2026-09-13 directly against the host (`docker compose ps` per stack,
`systemctl --user status`, a live `query_docs` call) — not assumed from the 09-11 or
09-12 snapshots.

| Component | Where | Health |
|---|---|---|
| FirecREST demo stack | `~/Sviluppo/firecrest/deploy/demo` | 15 services up, Kong healthy, API `:8000` |
| `firecrest-mcp` | `0.0.0.0:8765` (Streamable HTTP), user unit `firecrest-mcp.service` | active/running, 5 tools |
| MetaMCP | `:12008`, namespace `firecrest-workbench` | container healthy, aggregated endpoint still serves exactly 6 tools |
| DocMind app | `:8501` | healthy, snapshot `20260913T044207-f74bae7c` active (refreshed since 09-11; `query_docs` verified live against it, see prompt 08 note above) |
| `docmind-mcp` | `:8770` (Streamable HTTP) | healthy, 1 tool |
| Ollama (DocMind's) | internal only | healthy, `qwen3:4b-instruct` |
| Multica daemon | `http://localhost:8081` | running, ~24h uptime, 3 online runtimes (`claude`, `hermes`, `codex`) |

The FirecREST v2 demo container from the side track (`docs/hot-cache-v2.md`) was a
throwaway verification run, not a persistent service — it is not expected to still be
running and was not re-checked here.

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

**This is now automatic.** Two systemd *user* units bring everything up at boot, without
a login, via `loginctl enable-linger gavadala`:

| Unit | Scope | Role |
|---|---|---|
| `firecrest-workbench-stacks.service` | user | oneshot; runs `scripts/workbench-up.sh` |
| `firecrest-mcp.service` | user | long-lived; the wrapper process, `Restart=always` |

```bash
systemctl --user status firecrest-workbench-stacks firecrest-mcp
systemctl --user restart firecrest-mcp            # after editing the wrapper
journalctl --user -u firecrest-workbench-stacks   # what the boot bring-up did
./scripts/workbench-up.sh --status                # what is up right now
```

They are user units **because they have to be, not by preference**. Everything they
execute lives under the user's home, and under SELinux a *system* unit runs as `init_t`,
which is denied both `execute` and `read` on `user_home_t`:

```
avc: denied { execute } scontext=system_u:system_r:init_t:s0
                       tcontext=unconfined_u:object_r:user_home_t:s0
           name="workbench-up.sh"
avc: denied { read }    ... name="python" tclass=lnk_file
```

The system-unit variant fails with `status=203/EXEC` and `Permission denied`, which
reads as a filesystem permission problem and is not one. Relabelling is not a fix: the
whole virtualenv and its libraries would need it, and `restorecon` would undo it. A user
unit runs in the user's own domain, allowed to execute what the user owns — the same
reason `hermes-gateway.service` on this machine is a user unit.

One consequence worth knowing: a user unit's process may not have the `docker` group in
its group set if the membership was added after the session that owns the user manager
was created. `scripts/workbench-up.sh` detects this and falls back to `sg docker -c`,
the same workaround used by hand everywhere else in this project.

The units are versioned in `systemd/` in this repo — the copies in
`~/.config/systemd/user/` are installed from there.

### The manual fallback

The restart policies still differ per project, and knowing which is which saves time in
both directions — assuming things come back when they do not, or restarting what is
already healthy.

| Project | Policy | After a reboot |
|---|---|---|
| DocMind (`app`, `ollama`, `qdrant`, `docmind-mcp`) | `restart: unless-stopped` | **comes back on its own** |
| MetaMCP `postgres` | `restart: unless-stopped` | comes back |
| MetaMCP `app` | `restart: "no"` | **stays down** |
| FirecREST demo stack (15 services) | no restart policy | **all stay down** |
| `firecrest-mcp` | not a container at all — the user unit owns it | **stays down without the unit** |

Confirmed by a real reboot on 2026-09-12: DocMind and `metamcp-pg` came up unattended;
all fifteen demo-stack containers, MetaMCP, and the `firecrest-mcp` process did not —
the last of which is what the units above now fix. If the units are ever missing or
disabled, bring up only what is not already running:

```bash
# 1. FirecREST demo stack (15 services; the cluster container is the slow one)
cd ~/Sviluppo/firecrest/deploy/demo && sg docker -c "docker compose up -d"

# 2. MetaMCP
cd ~/Sviluppo/metamcp && sg docker -c "docker compose up -d"

# 3. DocMind — only if it did not already self-start
cd ~/Sviluppo/docmind-ai-llm && sg docker -c "docker compose up -d"

# 4. firecrest-mcp — a host process, not a container.
systemctl --user start firecrest-mcp
```

Step 4 is the one most easily forgotten, and its absence presents as a MetaMCP tool list
missing its five FirecREST tools — a symptom that points at MetaMCP rather than at the
missing process.

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

## Next: prompt 05 — still genuinely open

Prompt 05 points Hermes at the MetaMCP endpoint and runs a dedicated end-to-end test.
Prompt 06 landing (Hermes online as a runtime, real issues worked end to end through
it) removes the "is Hermes even reachable" question but does **not** substitute for
this prompt — nobody has filed the specific e2e test this prompt asks for and written
it up. Tracked as Multica issue `VDLP-12` (`todo`). It is the only one of the seven
that **modifies the agent's own configuration** rather than a component, which is why
it was left for a fresh session with explicit agreement.

Before starting it:

- The endpoint URL and API key are in `.secrets/metamcp-api-key.txt` (gitignored, never
  print the value).
- Confirm the six-tool surface first — the test is meaningless against a different set.
- Expect `docmind__query_docs` to take ~45 s. If it times out, check the MetaMCP timeout
  settings and `metamcp_admin.py --timeout` before suspecting the tool.

## Prompt 07 — still blocked on a human

Needs a BotFather bot token and the Multica workspace channel binding done in the web
UI. No agent can do either step; nothing to pick up here without Gabriele.

## Prompt 08 — shipped, but read the caveat above before treating it as closed

Delivered as `docs/failure-scenario-transcript.md` (`VDLP-5`, merged PR #7): a
deliberately failing job was submitted through the FirecREST stack, its log ingested
into DocMind, and a diagnosis produced from the raw evidence. See "Prompt 08, read
carefully" above for what is still open — the transcript's own retrieval-check
section was written before ingestion succeeded, and the delivered record never routed
through Hermes/MetaMCP or Telegram. Two things learned along the way that any related
follow-up should account for: job logs must be staged as `.txt` or they are silently
skipped, and several identical trivial logs will be rejected as duplicate content.
