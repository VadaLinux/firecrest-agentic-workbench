# Report 05 — FirecREST v1 → v2 gap analysis

**Task:** establish the v2 path — bring up the v2 demo, record what it actually does,
propose (not build) how `firecrest-mcp/` should support it.
**Status:** demo brought up and exercised; gap table and design proposal below;
implementation deliberately out of scope for this report (see "Delivery").
**Date:** 2026-09-12.
**Sources:** `docs/hot-cache.md` (v1, previously verified), `docs/hot-cache-v2.md`
(v2, verified this session against `ghcr.io/eth-cscs/firecrest-v2-demo:latest`,
digest `sha256:3dedfc6415191b86386e688938f31c1f1204fb473e0c3318eb92aea2fbd0226d`,
app version `2.6.0`), and the running container's own source
(`/app/firecrest`, `/app/lib` inside the image) and `/openapi.json`.

## 1. Standing up the demo — what it took, and what that itself proves

`docker run -p 8025:8025 -p 5025:5025 -p 3000:3000 --pull always
ghcr.io/eth-cscs/firecrest-v2-demo:latest` (host port 3000 remapped to 3001 here only
because something else already held 3000) brings up exactly one process: a launcher
that expects the operator to already have SSH access to a real HPC login node with a
working Slurm or PBS scheduler. Unlike v1's `deploy/demo`, no cluster ships in the
image. This is itself the first and largest finding: **v2's own "demo" is not a
self-contained thing to compare v1 against; it's a configuration wizard for pointing
the real v2 codebase at whatever HPC system you already have.** No such system was
available here, so a minimal throwaway stand-in (real `sshd` + coreutils, scripted
`sbatch`/`squeue`/`sacct`/`scontrol`/`sinfo`) was built to drive it — full details and
the caveats that come with that choice are in `docs/hot-cache-v2.md`'s header and
§4. Reusing the v1 demo's own dummy Slurm cluster for this was tried first and
rejected: that cluster's `sshd` only accepts certificates from its own CA, and every
session is forced through a wrapper script (`ssh_command_wrapper.sh`) that expects
the executed command to itself be encoded as a single-use SSH certificate's
`force-command` option — a security mechanism specific to how v1's own internal
services (`compute`, `utilities`) call out to the login node, not something a generic
SSH client (like v2's launcher) can satisfy. Making it satisfy that would mean either
neutering v1's login-node security model while it is the project's "running
configuration," or editing files inside the cloned upstream `firecrest` repo — both
against this project's rules, so that path was abandoned in favour of a clean,
separate stand-in that touches neither the v1 stack nor its clone.

A genuine, reproducible defect surfaced along the way: the launcher's own `/boot`
handler writes its settings back to YAML using a dumper that can't be read by the
loader `firecrest`'s own startup uses, so every guided boot crashes the process it
just started (`docs/hot-cache-v2.md` §2). Everything below was captured after
working around that by hand, restarting the process with a cleaned config.

## 2. Gap table

| Aspect | v1 (1.16.1, demo) | v2 (2.6.0, this demo) | Consequence for the wrapper |
|---|---|---|---|
| **System addressing** | `X-Machine-Name` HTTP header, every call except `/status/*`/`/tasks/*` | URL path segment: `/{system_name}/...` on every route | Every request-builder needs the system name interpolated into the path, not a header dict entry |
| **Async model** | Almost everything returns `{"task_id": ...}`; must poll `GET /tasks/{id}` | Nothing returns a task reference; responses are the real payload, same HTTP round trip | `_resolve_task`/`_poll_task` in `firecrest-mcp/client.py` have no v2 equivalent to call — a v2 client is strictly simpler here |
| **Job status flow** | `GET /compute/acct?jobs=` → `GET /tasks/{id}` (two calls); `/compute/jobs/{id}` is a trap that reads stale queue state | `GET /compute/{system}/jobs/{job_id}` (one call), real state inline | The v1-specific "don't trust `/compute/jobs/{jobid}` directly" warning becomes moot; the equivalent v2 route is the only one and it's correct |
| **Job submission** | Multipart upload of the script (`POST /compute/jobs/upload`); inline JSON is `405` | JSON only: inline `job.script` text, or `job.scriptPath` to a file already on the remote filesystem; no multipart route exists | Submitting a *local* script now needs an explicit upload step first (filesystem domain) if you don't want to inline the text — a two-call flow where v1 was one call |
| **List files** | `GET /utilities/ls?targetPath=<path>` | `GET /filesystem/{system}/ops/ls?path=<path>` | Parameter renamed `targetPath` → `path`; otherwise directly analogous |
| **Download file (small)** | `GET /utilities/download?sourcePath=<path>` — and *only* `sourcePath` works; `targetPath` alone 500s (a v1 bug, `docs/hot-cache.md`) | `GET /filesystem/{system}/ops/download?path=<path>` | The v1 `sourcePath`-vs-`targetPath` inconsistency is gone in v2 — one parameter name everywhere. One less special case to carry |
| **Download file (large)** | No separate path; same `/utilities/download` regardless of size | Separate `POST /filesystem/{system}/transfer/download` (`transferDirectives`-shaped body), presumably S3-presigned, not exercised here (no S3 backend stood up) | A v2 client needs a size-based branch v1 never needed, or can defer this until large-file transfer is actually required |
| **Job log** | No dedicated endpoint; wrapper keeps its own `logs/submissions.json` to map job id → `job_file_out`/`job_file_err`, then `GET /utilities/view` | No dedicated endpoint either, but `GET /compute/{system}/jobs/{job_id}/metadata` returns `standardOutput`/`standardError` *from FirecREST itself* | v2 needs no local submission-record bookkeeping for this — the paths are queryable server-side. `firecrest-mcp/client.py`'s `_remember_submission`/`_submissions_file` machinery has no reason to exist in a v2 client |
| **Auth token TTL** | 300 s (real Keycloak, client-credentials) | ~1 year in this demo (a throwaway RSA key the launcher process generates at startup; not indicative of real Alps IdP behaviour) | The wrapper's 30-second-early refresh margin is a v1-shaped assumption; a v2 client should read `expires_in` from the response rather than hardcode a margin tuned for 300 s |
| **Token issuer vs. gateway** | Separate ports (Keycloak :8080, Kong :8000) | Still separate in this demo (launcher :8025, FirecREST :5025) | `FIRECREST_TOKEN_URL` staying independent of `FIRECREST_BASE_URL` (already true in `firecrest-mcp/.env.example`) is a pattern that survives, for unrelated reasons on each side |
| **Error shape** | Ad hoc per endpoint: `{"description": ..., "error": ...}`, `{"data": "...", "status": "..."}`, etc. | One consistent envelope everywhere: `{"errorType", "message", "causedBy", "data", "user"}` | A v2 client can have one error-parsing path instead of per-endpoint special cases |
| **HTTP status discipline** | `400` for a missing/wrong machine name even where `404` would fit; `500` for a missing-parameter case that should be `400` (`/utilities/download`) | Clean `404` for unknown system, `401` for missing auth, in what was exercised here | Fewer status-code special cases to work around in a v2 client |
| **Response tracing** | None observed | `f7t-appversion`, `f7t-timestamp`, `x-request-id`, `x-correlation-id` on every response | Free debugging aid a v2 client can log and surface on error |

## 3. Design proposal for `firecrest-mcp/`

Three shapes were considered for where a v2 path sits.

**Option A — rebuild `client.py` in place for v2, drop v1.** Rejected outright: the
project explicitly still needs v1 (the demo stack CSCS ships for learning, and
whatever CSCS keeps running for the hackathon), and Alps is v2. Both have to keep
working; this isn't a migration, it's a fork in what "FirecREST" means depending on
which system you're pointed at.

**Option B — a second, independent client (`client_v2.py`), with `server.py` picking
one whole client at startup based on config (e.g. `FIRECREST_API_VERSION=v1|v2`).**

- *For:* Simplest to reason about and test in isolation — v2's request/response
  models, auth handling, and error shapes are different enough (§2) that trying to
  share code would mean threading version checks through nearly every method.
  `AGENTS.md`'s rule ("all FirecREST calls go through `firecrest-mcp/client.py`")
  would need updating to "through `firecrest-mcp/client*.py`", which is a small,
  honest change to make. Matches this project's own precedent of keeping v1's
  documented behaviour untouched while adding new material alongside it
  (`docs/hot-cache.md` next to `docs/hot-cache-v2.md`, not a rewrite of one into the
  other).
- *Against:* Duplicates the boilerplate that genuinely is the same on both sides —
  token caching/refresh scaffolding, `httpx` client lifecycle, the five MCP tool
  *signatures* themselves (`submit_job`, `get_job_status`, `list_files`,
  `download_file`, `get_job_log` don't need to look any different to the agent
  calling them).

**Option C — one client, a transport seam underneath.** A shared
`FirecRESTClient` keeps today's public method signatures; a small internal
`_Transport` (or `_ApiV1`/`_ApiV2` pair) implements, per version: how the system
name is applied to a request (header vs. path), whether a response needs task
resolution, and how errors are unwrapped. Chosen per-system at config time (so a
future multi-system deployment could point one system at a v1 host and another at
v2 — realistic, since CSCS ran both simultaneously right up to the v1
decommission).

- *For:* The agent-facing surface (`server.py`'s tool definitions, and everything
  that calls into `client.py` today) doesn't change at all. The genuinely shared
  concerns (token lifecycle, timeouts, logging) live once.
- *Against:* Real risk of the seam leaking — e.g. `get_job_log`'s v1 implementation
  depends on `logs/submissions.json`, which a v2 backend has no need for at all
  (§2); forcing both through one method signature either drags v1's bookkeeping
  into the v2 path uselessly, or the "shared" method ends up branching on version
  internally anyway, which is Option B with extra ceremony.

**Recommendation: Option B**, specifically because of the `get_job_log` case above —
it's not a detail, it's evidence that the *shape* of what each version needs
plumbed through is different, not just the wire format. A thin shared module
(token-cache class, `httpx.AsyncClient` factory, the `FirecRESTError` type) can still
be factored out and imported by both `client.py` and a new `client_v2.py`, without
forcing one call surface to serve two request-lifecycle models. `server.py` would
gain a small factory (`get_client(system_config) -> FirecRESTClient | FirecRESTClientV2`)
keyed off which API version a configured system speaks, so the agent-facing tool
functions stay version-agnostic even though the two clients underneath are not.

This report does not implement any of this — per the task, the design should be
reviewable on its own first.

## 4. Open items for whoever picks this up next

- No S3 backend was available to exercise `POST /filesystem/{system}/transfer/download`
  (or its upload counterpart) even once; `docs/hot-cache-v2.md` §3 only confirms the
  endpoint's request-validation shape, not a real transfer.
- `/status/{system}/partitions`, `/reservations`, `/nodes`, `/userinfo` are real,
  implemented v2 routes not exercised here because the stand-in scheduler doesn't
  implement the `scontrol`/`sacctmgr` invocations they need — see
  `docs/hot-cache-v2.md` §4's closing note. None of the five calls this project
  wraps depend on them, but a fuller v2 client eventually would.
- Nothing here was run against real CSCS credentials or real Alps; both this file
  and `docs/hot-cache-v2.md` describe demo behaviour only, some of it against a
  stand-in scheduler built for this task. Confirming any of §2 against production
  Alps before relying on it there is still open work.
