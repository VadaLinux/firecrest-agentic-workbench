# Prompt 02 — Build the FirecREST MCP wrapper

You are working in this repo. The FirecREST demo stack from prompt 01 is running locally. Your task:

1. Create `firecrest-mcp/` as a Python MCP server (use the official `mcp` Python SDK). Do not use a from-scratch protocol implementation.
2. Pull the FirecREST v2 OpenAPI spec (linked from `https://eth-cscs.github.io/firecrest-v2/`) and generate/hand-write typed request models for the endpoints you'll wrap — don't hand-roll ad hoc dicts for the request bodies.
3. Implement exactly these tools, no more, matching the signatures in `ARCHITECTURE.md`:
   - `submit_job(script, system, account)`
   - `get_job_status(job_id)`
   - `list_files(path)`
   - `download_file(path)`
   - `get_job_log(job_id)`
4. Handle the OAuth2 client-credentials flow internally: request a token, cache it, refresh before the 5-minute expiry. Never surface the token to the tool's return value.
5. Every tool needs a docstring written as if the *agent* is the reader — describe what it does and when to use it, not just the parameter types.
6. Write `firecrest-mcp/tests/` with the FirecREST client mocked (use `respx` or similar against `httpx`) — no test may require the real demo stack to be running.
7. Add `firecrest-mcp/.env.example` documenting `FIRECREST_CLIENT_ID`, `FIRECREST_CLIENT_SECRET`, `FIRECREST_BASE_URL`. Do not commit a `.env`.
8. Update `AGENTS.md`'s "Build & test" section if the actual commands differ from what's written there.

## Definition of done

- `pytest` passes with the demo stack stopped.
- `python server.py --dry-run` starts without error.
- With the demo stack from prompt 01 running, a manual MCP client call to `submit_job` against the dummy cluster returns a real job ID within a few seconds.
- No secrets committed; `.env` is gitignored and `.env.example` has every variable it needs, with placeholder values.
