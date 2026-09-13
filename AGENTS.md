# Agent conventions for this repo

This file is read automatically by Multica and Hermes when they pick up an issue in this project. Keep it short; it's the first thing an agent loads before touching anything.

## What this repo is

An MCP wrapper and supporting docs/config for driving CSCS's FirecREST API through an agent. See `README.md` and `ARCHITECTURE.md` before making structural changes.

## Build & test

```bash
cd firecrest-mcp
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt       # runtime only
.venv/bin/pip install -r requirements-dev.txt   # adds pytest, respx

.venv/bin/python -m pytest                      # unit tests, all FirecREST calls mocked
.venv/bin/python server.py --dry-run            # starts the MCP server, announces its tool surface

# full lifecycle against the running demo stack (submit → status → log → files)
.venv/bin/python scripts/mcp_smoke_test.py
```

`pytest` must pass with the demo stack **stopped** — no test may depend on it.
The demo stack (`docker compose up -d` from `firecrest/deploy/demo`) is only needed
for `mcp_smoke_test.py` and for live tool calls.

Configuration comes from `firecrest-mcp/.env` (copy `.env.example`). Note that
`FIRECREST_TOKEN_URL` is required in addition to the gateway URL: the Keycloak token
issuer listens on a different port and cannot be derived from `FIRECREST_BASE_URL`.

## Conventions

- All FirecREST calls go through `firecrest-mcp/client*.py` (`client.py` for v1, `client_v2.py` for v2 — see `docs/hot-cache-v2.md`). Do not call `pyfirecrest` or raw HTTP from anywhere else.
- Every new MCP tool needs: a docstring the agent can read as its tool description, a unit test with a mocked response, and one line added to `docs/hot-cache.md` if it's likely to be used often.
- Never commit real CSCS client secrets, API keys, or JWTs. `.env.example` documents every variable; copy it to `.env`, which is gitignored.
- Job logs written by `get_job_log` land in `logs/` (gitignored) and are what DocMind ingests for the failure-scenario demo — don't redirect that path without updating `docs/INFERENCE.md` and the DocMind ingestion config together.

## Scope for autonomous agents

An agent picking up an issue in this repo may: edit `firecrest-mcp/`, edit docs, add tests, open a PR.
An agent should NOT: change MetaMCP's exposed namespace/auth config, or touch anything under `docs/TELEGRAM.md`'s bot token setup, without a human explicitly assigning that as the issue.
