# Prompt 02 completion report — FirecREST MCP wrapper

**Task:** `prompts/02-firecrest-mcp-wrapper.md`
**Status:** complete, with declared deviations (see §5)
**Date:** 2026-09-11
**Depends on:** prompt 01 (demo stack running)

---

## 1. Objective

Build `firecrest-mcp/`: a Python MCP server wrapping the FirecREST API, so an agent
can submit and inspect HPC jobs through tool calls instead of raw HTTP. Five tools,
no more; authentication and asynchrony hidden from the caller; unit tests that do
not need the demo stack.

## 2. What was built

| File | Purpose |
|---|---|
| `client.py` | The only module that talks to FirecREST. Token caching, `X-Machine-Name`, task unwrapping, typed request/response models. |
| `server.py` | MCP server exposing exactly five tools, with docstrings written for the agent as reader. `--dry-run`, stdio and Streamable HTTP transports. |
| `tests/test_client.py` | 20 unit tests, every FirecREST call mocked with `respx` over `httpx`. |
| `scripts/mcp_smoke_test.py` | Drives a real job lifecycle through the MCP protocol against the running demo stack. |
| `.env.example` | Every variable documented, with the demo values as a worked example. |
| `requirements.txt` / `requirements-dev.txt` | Runtime and test dependencies, kept separate. |

Stack: Python 3.13.14, `mcp` 2.2.0 (official SDK), `httpx` 0.28.1, `pydantic` 2.13.5,
`respx` 0.23.1, `pytest` 9.1.1.

The tool surface is exactly what `ARCHITECTURE.md` specifies — `submit_job`,
`get_job_status`, `list_files`, `download_file`, `get_job_log` — and the smoke test
asserts the set matches rather than trusting it.

## 3. Verification against the definition of done

| Requirement | Result |
|---|---|
| `pytest` passes with the demo stack stopped | ✅ `20 passed` with the stack stopped, verified by actually running `docker compose stop` first |
| `python server.py --dry-run` starts without error | ✅ exit 0, announces the five tools and the resolved target |
| With the stack running, a manual MCP client call to `submit_job` returns a real job id | ✅ over the real MCP protocol via stdio, job created and completed |
| No secrets committed; `.env` gitignored; `.env.example` complete | ✅ `git check-ignore` confirms; `.env.example` lists every variable the client reads |

Full lifecycle observed through the MCP protocol:

```
connected to firecrest-mcp (protocol 2025-11-25)

=== tool surface ===
["download_file", "get_job_log", "get_job_status", "list_files", "submit_job"]

=== submit_job ===        {"ok": true, "jobid": "9", ...}
=== get_job_status ===    {"ok": true, "job": {"state": "COMPLETED", "exit_code": "0:0", ...}}
=== get_job_log ===       {"ok": true, "log": {"stdout": "hello-from-mcp\n", ...}}
=== list_files ===        {"ok": true, "path": "/home", "count": 4}

OK: tool surface correct, job submitted, reached COMPLETED, output readable.
```

## 4. Design decisions worth reviewing

**`get_job_log` needs a local submission registry.** FirecREST cannot map a bare job
id back to its output file paths — those are only carried in the *submission* task's
payload, and the path contains a task-specific UUID. `client.py` therefore writes
`logs/submissions.json` mapping `jobid → {job_file_out, job_file_err}` at submit
time. A consequence worth stating plainly: **`get_job_log` can only read jobs
submitted through this client.** The error message says so and points at
`list_files` + `download_file` as the alternative, rather than guessing a path.

**Errors are returned as data, not raised.** Every tool catches its exceptions and
returns `{"ok": false, "error": ..., "http_status": ...}`, so the agent can explain a
failure instead of the whole call collapsing.

**`X-Machine-Name` is hidden.** The agent passes a system name to `submit_job` and
never learns that it becomes a header.

## 5. Declared deviations from the prompt

1. **`submit_job(script, system, account)` is not a JSON POST.** The prompt says to
   build typed request models for the endpoints wrapped, and `ARCHITECTURE.md`
   documents an inline `script` argument. This FirecREST revision has no endpoint
   that accepts an inline script: `POST /compute/jobs` answers **405 Method Not
   Allowed** (see §6.1). The documented signature is preserved; the script is written
   to a temporary file and sent as a multipart upload to `/compute/jobs/upload`.
2. **`.env.example` documents more than the three variables named in the prompt.**
   `FIRECREST_TOKEN_URL` is required and cannot be derived from `FIRECREST_BASE_URL`:
   the Keycloak token issuer is on a different host and port from the Kong gateway.
   `FIRECREST_SYSTEM`, `FIRECREST_TIMEOUT`, `FIRECREST_LOG_DIR` and
   `FIRECREST_VERIFY_SSL` are also documented, each with its default.
3. **`get_job_status` calls `/compute/acct`, not `/compute/jobs/{jobid}`.** The
   obvious endpoint is unusable — see §6.3. The deviation is from the naïve reading
   of the API, not from the prompt, but it is the kind of thing a reviewer will
   query.
4. **`AGENTS.md`'s Build & test section was rewritten** (prompt step 8 allows this).
   The original commands would not work: they assumed a system-wide `pip install`
   and a bare `pytest`, and no venv. A `requirements-dev.txt` split was also added,
   because the original single file would have put pytest in the deployment path.
5. **The `mcp` SDK is 2.x, not 1.x.** `FastMCP` was renamed to `MCPServer` in that
   release, and result fields are snake_case (`server_info`, not `serverInfo`).

## 6. Findings that affect prompts 03–08

### 6.1 There is no inline job submission — `POST /compute/jobs` is 405

```
POST /compute/jobs  {"script": "..."}   →  405 Method Not Allowed
```

Attempted with a JSON body, with `application/x-www-form-urlencoded`, and with
alternative key names (`job`, `scriptPath`, `targetPath`) — all 405, which is a
routing answer, not a validation one. Only `/compute/jobs/upload` (multipart) and
`/compute/jobs/path` (a script already on the remote filesystem) exist.

This contradicts `ARCHITECTURE.md`'s tool signature, and any wrapper built from the
generic FirecREST v2 documentation would be written against an endpoint that is not
there.

### 6.2 A PENDING job reports a placeholder accounting record

For roughly the first three seconds after submission — while the job is `PENDING` —
`sacct` returns a record whose name is `allocation` and whose partition is empty.
The real values appear once the job is allocated:

```
  poll 0  t+ 1.35s  name=allocation     partition=             state=PENDING
  poll 1  t+ 2.66s  name=allocation     partition=             state=PENDING
  poll 2  t+ 3.98s  name=timing-probe   partition=part01       state=RUNNING
```

It tracks the state, not wall-clock: with a 3-second delay the first query is already
correct. This is precisely when an agent looks — it submits and immediately asks —
so `get_job_status` returns a `warning` whenever the partition is empty, and the
tool's docstring tells the agent to report only the state in that case.

(Worth noting how this was found: it first appeared as a single anomalous reading
which I could not reproduce in twelve polls and initially wrote up as "observed
once". The smoke test then hit it again on the next run, which forced the real
explanation. The first write-up was wrong, and only re-running the failing case
revealed it.)

### 6.3 `GET /compute/jobs/{jobid}` cannot be used for status

It answers with an async task whose `data` is a queue string. For a job that had
already completed it reported `{"data": "Queued", "status": "100"}`. Job state must
come from `/compute/acct?jobs=<comma-joined ids>`, which is what `pyfirecrest`
itself uses internally.

### 6.4 The upstream client pins a much older pyfirecrest

The bundled demo client pins `pyfirecrest` **1.5.1**; current is 3.10.0. It works
against this API revision, but any decision to adopt `pyfirecrest` in the wrapper
should be made deliberately rather than by copying the client — this wrapper uses
`httpx` directly, which keeps the dependency surface small and the API version
explicit.

## 7. Next step

Prompt 03 — run MetaMCP and register this server as its source. The server supports
**stdio** (what MetaMCP will use) and Streamable HTTP, so either transport is
available. `docs/METAMCP.md` will be created there.

Open question carried forward: the v1.16.1-vs-v2 spec discrepancy from the prompt 01
report is still unresolved, and it decides whether this wrapper should later be
rebuilt against the v2 API surface or kept as-is.

**Update (2026-09-12):** resolved. Production Alps runs v2; the demo stack this
report is about stays v1.16.1 (`docs/hot-cache.md`). v2 has since been brought up
and verified on its own — `docs/hot-cache-v2.md` — with a gap analysis and a
proposal to keep this wrapper's v1 client as-is and add a second client for v2
rather than rebuild in place (`docs/reports/05-firecrest-v2-gap.md`).
