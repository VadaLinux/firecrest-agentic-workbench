# Agent conventions for this repo

This file is read automatically by Multica and Hermes when they pick up an issue in this project. Keep it short; it's the first thing an agent loads before touching anything.

## What this repo is

An MCP wrapper and supporting docs/config for driving CSCS's FirecREST API through an agent. See `README.md` and `ARCHITECTURE.md` before making structural changes.

## Build & test

```bash
cd firecrest-mcp
pip install -r requirements.txt
pytest                          # unit tests against a mocked FirecREST client
python server.py --dry-run      # starts the MCP server against the local demo stack
```

The FirecREST demo stack (`docker compose up -d` from `firecrest/deploy/demo`) must be running for anything beyond `--dry-run`.

## Conventions

- All FirecREST calls go through `firecrest-mcp/client.py`. Do not call `pyfirecrest` or raw HTTP from anywhere else.
- Every new MCP tool needs: a docstring the agent can read as its tool description, a unit test with a mocked response, and one line added to `docs/hot-cache.md` if it's likely to be used often.
- Never commit real CSCS client secrets, API keys, or JWTs. `.env.example` documents every variable; copy it to `.env`, which is gitignored.
- Job logs written by `get_job_log` land in `logs/` (gitignored) and are what DocMind ingests for the failure-scenario demo — don't redirect that path without updating `docs/INFERENCE.md` and the DocMind ingestion config together.

## Scope for autonomous agents

An agent picking up an issue in this repo may: edit `firecrest-mcp/`, edit docs, add tests, open a PR.
An agent should NOT: change MetaMCP's exposed namespace/auth config, or touch anything under `docs/TELEGRAM.md`'s bot token setup, without a human explicitly assigning that as the issue.
