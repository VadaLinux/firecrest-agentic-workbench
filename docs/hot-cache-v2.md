# FirecREST v2 hot cache

Companion to `docs/hot-cache.md`, which remains authoritative for the v1.16.1 demo
stack and **must keep working** — nothing here changes it. This file exists because
production Alps now runs FirecREST v2 (v1 was decommissioned on Alps on 2025-12-05;
see the note at the top of `docs/hot-cache.md`), and this project needs a verified
picture of v2 before deciding how `firecrest-mcp/` should speak it.

- **Demo verified against:** `ghcr.io/eth-cscs/firecrest-v2-demo:latest`,
  digest `sha256:3dedfc6415191b86386e688938f31c1f1204fb473e0c3318eb92aea2fbd0226d`,
  pulled 2026-09-12. The FirecREST app itself reports `app_version: 2.6.0`
  (`GET /openapi.json` → `info.version`).
- **Launcher UI:** `http://localhost:8025/`. **FirecREST API gateway:**
  `http://localhost:5025/`. **Web UI:** container port 3000 (host-mapped to
  `3001` here because `3000` was already taken by another local service — not a
  property of the image).
- **Verified on:** 2026-09-12, openSUSE Leap 16, Docker 29.4.0-ce, alongside the
  still-running v1 demo stack (`docs/hot-cache.md`).
- **This is not the same kind of demo as v1's.** v1's `deploy/demo` compose stack
  bundles Keycloak, Kong, *and* a dummy Slurm cluster — everything needed is in the
  one stack. The v2 image is a **launcher**, not a self-contained cluster: it exposes
  a setup wizard (`/`) that expects you to plug in SSH access to **your own real HPC
  login node** (its own banner: *"The FirecREST v2 Demo Launcher will run FirecREST
  on your machine and connect to your local HPC cluster using your personal
  credentials"*). Until that wizard completes, the container only runs the launcher
  itself (`supervisord` → `launcher` program); the real `firecrest` process
  (port 5025) and `firecrest-ui` (port 3000) are defined with `autostart=false` and
  do not exist yet.
- **No cluster was available to point it at, so one was built for this
  verification only:** a throwaway container with `sshd` + real coreutils, plus
  minimal shell stand-ins for `sbatch`/`squeue`/`sacct`/`scontrol`/`sinfo` (source:
  `/tmp/v2fakecluster/*` on the machine this was run on, not committed to this
  repo). Everything under "Filesystem" below ran against **real** SSH + coreutils,
  no faking involved. Everything under "Compute" ran against the **scripted
  stand-in scheduler**, not real Slurm — the shapes of the requests and responses
  are FirecREST v2's own and are genuine; the *scheduling* is not. This is flagged
  inline wherever it applies. Demo behaviour — real or stood-in — is still not
  production behaviour; see the caveat CSCS itself puts in the launcher UI.

## 0. What actually changed, in the two things that broke almost everything in v1

**1. The system is a URL path segment, not a header.** Every FirecREST v2 route is
prefixed `/{system_name}/...` (e.g. `/compute/{system_name}/jobs`,
`/filesystem/{system_name}/ops/ls`) — confirmed directly from the running app's own
`GET /openapi.json` and from `firecrest/compute/router.py` /
`firecrest/filesystem/ops/router.py` inside the image
(`router = create_router(prefix="/compute/{system_name}/jobs", ...)`). There is no
`X-Machine-Name` header anywhere in v2. Calling an unknown system name gives a clean
`404 {"errorType": "error", "message": "System not found", ...}` — contrast v1's
`400 {"description": "No machine name given"}` for the equivalent mistake.

**2. The two-step task flow is gone.** `POST /compute/{system}/jobs`,
`GET /compute/{system}/jobs/{job_id}`, and every `/filesystem/.../ops/*` call are
answered directly, in one HTTP round trip, with the real payload — no
`{"success": "Task created", "task_id": ...}` envelope, no `GET /tasks/{id}` to
unwrap it. (There is, deliberately, no `/tasks/*` prefix left in the v2 OpenAPI
surface at all.) Internally the API still blocks on an SSH round-trip to the
scheduler/filesystem, but nothing about that is visible to the caller — it just
looks synchronous.

## 1. Authentication

The demo launcher plays the role Keycloak played in v1 — it hands out an OAuth2-shaped
client-credentials token, but the resemblance is only skin deep; the token issuer here
is the launcher's own FastAPI process, not a real IdP:

```bash
curl -s -X POST http://localhost:8025/token \
  -d "grant_type=client_credentials&client_id=demo&client_secret=x"
```
```json
{"access_token": "eyJhbGciOiJSUzI1NiIs...", "token_type": "bearer",
 "expires_in": 31556952, "refresh_token": "eyJhbGciOiJSUzI1NiIs..."}
```

Observed: `expires_in` is ~1 year (`31556952` s), against v1's 300 s — because this
token is signed by an RSA key the launcher generates fresh at container startup
(`lifespan()` in `launcher/main.py`), for exactly this one running container, and
`client_secret` is not checked at all (any value works; only `client_id` becomes the
JWT's `username`/`sub`). `GET /certs` serves the matching JWKS. None of this should be
taken as a statement about how real Alps issues or scopes tokens — that is a separate,
CSCS-operated IdP this demo does not stand in for.

**The token issuer and the API gateway are still two different ports**, same shape as
v1 (Keycloak :8080 vs Kong :8000) — here, launcher :8025 vs FirecREST :5025 — so
`FIRECREST_TOKEN_URL` being independent of `FIRECREST_BASE_URL` (see
`firecrest-mcp/.env.example`) is a pattern that survives from v1 to v2, even though
neither side is the same piece of software.

Every FirecREST v2 response carries request-tracing headers v1 never had:
`f7t-appversion`, `f7t-timestamp`, `x-request-id`, `x-correlation-id`.

## 2. Standing up the demo: what `/boot` actually requires, and a bug in it

The launcher wizard is three calls, made from the browser in `launcher/static/index.html`
but plain JSON underneath:

```bash
curl -s -X POST http://localhost:8025/sshconnection -H "Content-Type: application/json" \
  -d '{"hostname":"<login-node>","hostport":22,"proxyhost":null,"proxyport":null}'
# → {"message":"SSH hosts saved successfully."}

curl -s -X POST http://localhost:8025/credentials -H "Content-Type: application/json" \
  -d '{"username":"<user>","private_key":"<PEM, \n-escaped>","public_cert":null,"passphrase":null}'
# → {"message":"Credentials saved successfully.","user_home":"/home"}
# (runs `pwd` over SSH as a live reachability check; fails loudly with the real
#  SSH error if the key or host is wrong)

curl -s -X POST http://localhost:8025/boot -H "Content-Type: application/json" \
  -d '{"cluster_name":"<name>","scheduler_type":"slurm"}'
# → {"message":"Firecrest v2 started successfully.","access_token":"...",
#    "system_name":"<name>","scheduler":{"type":"slurm","version":"24.11.0","is_compatible":true}}
```

`/boot` runs `sinfo -V` (or `qstat --version` for PBS) over SSH to fingerprint the
scheduler, writes the whole settings object back to `/app/config/app-config.yaml`,
and asks `supervisord` to (re)start the real `firecrest` and `firecrest-ui`
processes.

**Bug, reproduced three times across container restarts:** the settings object
contains enum fields (`TokenEndpointAuthMethod`, `BackendServiceType`), and the
handler serialises them with plain `yaml.dump()`, which emits PyYAML's Python-object
tags (`!!python/object/apply:...`). `firecrest`'s own startup reads that same file
back with `yaml.safe_load()`, which cannot construct those tags and crashes before
the app object exists:

```
yaml.constructor.ConstructorError: could not determine a constructor for the tag
'tag:yaml.org,2002:python/object/apply:lib.models.token_endpoint_auth_method.TokenEndpointAuthMethod'
```

So, **as shipped, every `/boot` call breaks the `firecrest` process it just tried to
start.** `/boot`'s own HTTP response is unaffected (it succeeds and returns a token
before the restart happens), which makes the failure easy to miss — the wizard
*looks* like it worked. The workaround used for the rest of this document: load the
written YAML with `yaml.unsafe_load` (safe here — it's the app's own config, not
untrusted input), convert every `Enum` to its `.value`, re-dump with `yaml.safe_dump`,
then restart the `firecrest` process via its supervisor RPC interface
(`http://127.0.0.1:9001/RPC2`, `dummy`/`dummy`, the same interface `/boot` itself
calls). No source file in the image was modified — only the generated
`/app/config/app-config.yaml`. Worth reporting upstream: it means the demo's own
guided path does not produce a working FirecREST v2 instance without manual
intervention, on this revision.

Once the process is actually up, `GET /openapi.json` on port 5025 answers with the
real, current v2 surface — 33 paths, matching what's exercised below.

## 3. Filesystem — genuine, no scheduler involved

These ran over real SSH to a real (if minimal) coreutils environment; nothing here is
scripted or approximated.

```bash
curl -s "http://localhost:5025/filesystem/<system>/ops/ls?path=/home/demo" \
  -H "Authorization: Bearer $TOK"
```
```json
{"output": [
  {"name": "echo-hello.err", "type": "-", "linkTarget": null, "user": "demo",
   "group": "demo", "permissions": "rw-r--r--.", "lastModified": "2026-09-12T15:47:22",
   "size": "0"},
  {"name": "echo-hello.out", "type": "-", "linkTarget": null, "user": "demo",
   "group": "demo", "permissions": "rw-r--r--.", "lastModified": "2026-09-12T15:47:22",
   "size": "1"}
]}
```

`list_files(path)` → `GET /filesystem/{system}/ops/ls?path=<path>` — parameter is
**`path`**, not `targetPath` as in v1.

**`get_job_log` equivalent — content read.** v2 has no dedicated log endpoint, same
as v1; the content comes from a general-purpose file-view call once you know the
path (see §4 for how the path is found):

```bash
curl -s "http://localhost:5025/filesystem/<system>/ops/view?path=/home/demo/echo-hello.out&size=100" \
  -H "Authorization: Bearer $TOK"
# → {"output": "\n"}   -- the job's actual stdout; see the footnote for why it's
#                         just a newline instead of the expected "hello-v2"
```

**Bug, reproducible without any query params at all:** `/ops/view`'s own default for
`size` is `5 * 1024 * 1024` (5 MiB), but this cluster's own config sets
`max_ops_file_size: 1048576` (1 MiB) — and the handler checks the requested size
against that ceiling *before* honouring the default. Calling `/ops/view?path=...`
with no `size` at all therefore always fails on this demo's own stock config:

```
{"errorType": "error", "message": "`size` value must be less than 1048576 bytes", ...}
```

`download_file(path)` → `GET /filesystem/{system}/ops/download?path=<path>` (small,
synchronous, single response — this is the closest analogue to v1's
`/utilities/download`):

```bash
curl -s -D - "http://localhost:5025/filesystem/<system>/ops/download?path=/home/demo/echo-hello.out" \
  -H "Authorization: Bearer $TOK"
```
```
HTTP/1.1 200 OK
content-type: application/octet-stream
content-length: 1
f7t-appversion: 2.6.0
<body: "\n" -- 1 byte, same content as the view call above>
```

Parameter is **`path`** for every `ops/*` call, including download — the v1
`sourcePath`-vs-`targetPath` inconsistency (`docs/hot-cache.md` §"Parameter
inconsistency") **does not exist in v2**; one parameter name, uniformly.

**v2 also has a second, asynchronous download path** — `POST
/filesystem/{system}/transfer/download` — that returns a `transferDirectives`-shaped
job description (confirmed by the validation error it gives when that field is
omitted) rather than bytes directly; this is presumably the S3-presigned-URL path
for large files (`data_operation.data_transfer` in cluster config points at an S3
endpoint, `192.168.240.19:9000` by default, not present in a bare launcher
container). Not exercised — no S3 backend was stood up for this — but its existence
alongside the small-file `ops/download` is itself the finding: v2 splits "small file,
synchronous" from "large file, async/S3" into two different endpoints; v1 had one.

## 4. Compute — FirecREST's own request/response shapes are genuine; the scheduler answering them is a scripted stand-in

`submit_job` → `POST /compute/{system}/jobs`, one call, JSON body, **camelCase**:

```bash
curl -s -X POST http://localhost:5025/compute/<system>/jobs \
  -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" \
  -d '{"job": {
        "name": "echo-hello-v2",
        "workingDirectory": "/home/demo",
        "standardOutput": "echo-hello.out",
        "standardError": "echo-hello.err",
        "env": {"GREETING": "hello-v2"},
        "script": "#!/bin/bash\necho $GREETING\n"
      }}'
```
```json
{"jobId": "1"}
```

**There is no multipart script-upload variant in v2's compute API at all** — contrast
v1, where `POST /compute/jobs/upload` (multipart) was the working submission path and
inline JSON was `405`. In v2, submission only ever takes a script as inline text
(`job.script`) or as `job.scriptPath` pointing at a file already on the remote
filesystem — getting a local script onto the remote filesystem first is now
exclusively the filesystem domain's job (`ops/upload` or `transfer/upload`, §3),
fully decoupled from job submission. This is a real API shape change a v2 wrapper's
`submit_job` has to account for, not just a URL change.

`get_job_status` → `GET /compute/{system}/jobs/{job_id}`, one call, no task
indirection:

```bash
curl -s http://localhost:5025/compute/<system>/jobs/1 -H "Authorization: Bearer $TOK"
```
```json
{"jobs": [{
  "jobId": "1", "name": "echo-hello-v2",
  "status": {"state": "COMPLETED", "stateReason": "None", "exitCode": 0, "interruptSignal": 0},
  "tasks": [], "time": {"elapsed": 1, "start": 1789228045, "end": 1789228045, "suspended": 0, "limit": null},
  "account": "demo", "allocationNodes": 1, "cluster": "fakecluster", "group": "demo",
  "nodes": "fakenode0", "partition": "debug", "killRequestUser": null, "user": "demo",
  "workingDirectory": "/home/demo", "priority": 1
}]}
```

Confirms directly: the `GET /compute/acct?jobs=` → `GET /tasks/<id>` two-step from v1
does not exist in any form in v2 — `state` is inline, immediately, in the one call.

**`get_job_log` equivalent — path lookup.** There is still no single "give me the
log" endpoint (same as v1); `GET /compute/{system}/jobs/{job_id}/metadata` gives the
stdout/stderr paths, which §3's `ops/view` then reads:

```bash
curl -s http://localhost:5025/compute/<system>/jobs/1/metadata -H "Authorization: Bearer $TOK"
```
```json
{"jobs": [{"jobId": "1", "script": "#!/bin/bash\necho $GREETING\n",
  "standardInput": "/dev/null", "standardOutput": "/home/demo/echo-hello.out",
  "standardError": "/home/demo/echo-hello.err"}]}
```

So `get_job_log(job_id)` in v2 is a `metadata` call followed by an `ops/view` (or
`ops/head`/`ops/tail`) call — two calls, same as v1's local-submission-record +
`/utilities/view` composition, just with the path coming from FirecREST itself
instead of from the wrapper's own bookkeeping (`logs/submissions.json` in
`firecrest-mcp/client.py` — v2 would not need that file at all for this purpose,
since `/metadata` already knows the paths server-side).

**Not exercised, and worth being explicit about why:** `scontrol`/`sacctmgr`-backed
endpoints — `/status/{system}/partitions`, `/status/{system}/reservations`,
`/status/{system}/nodes`, `/status/{system}/userinfo` — all real, documented,
implemented routes that returned genuine errors against the stand-in scheduler
(`exit_status:1 std_err:scontrol: unsupported invocation...`, `sacctmgr: command not
found`) because the stand-in only implements the handful of invocations the five
calls above need. These are documented-and-implemented, not documented-but-missing —
the gap is in the test double, not in FirecREST v2.

## 5. Error shape

v2 errors are one consistent envelope everywhere, unlike v1's ad hoc
per-endpoint `{"description": ..., "error": ...}` shapes:

```json
{"errorType": "error", "message": "System not found", "causedBy": null,
 "data": null, "user": "demo"}
```
Missing/invalid auth: clean `401 {"errorType": "error", "message": "Not authenticated", ...}`.
Unknown system: clean `404`, not v1's `400` for the equivalent mistake (§0).

## 6. New finding from implementing `client_v2.py` (VDLP-11): relative `workingDirectory` is accepted but not resolved

Not exercised in the original verification session, surfaced while building
`firecrest-mcp/client_v2.py`. `POST /compute/{system}/jobs` accepts a relative
`workingDirectory` (e.g. `"."`) without complaint, and the job genuinely runs
with its output landing in the real home directory on disk (confirmed by
listing the directory afterwards). But neither of v2's own read-back calls
resolve that value to an absolute path — `GET /compute/{system}/jobs/{id}`
echoes `"workingDirectory": "."` back verbatim, and
`GET /compute/{system}/jobs/{id}/metadata` echoes `standardOutput`/
`standardError` as `"./name.out"`/`"./name.err"`, still relative. Both
`GET /filesystem/{system}/ops/view` and `.../ops/download` then reject those
same paths outright:

```json
{"errorType": "error", "message": "The provided path (./name.out) is not an absolute path.", ...}
```

So a job submitted with a relative `workingDirectory` is real and ran
correctly, but its own output paths — as reported by FirecREST itself — are
useless for the very filesystem calls needed to read that output back. There
is no v2 endpoint exercised here that reports an absolute home directory
either (`/status/{system}/userinfo` is one of the routes this demo's stand-in
scheduler can't answer, §4's closing note) — a caller has to already know an
absolute path, e.g. via `ops/ls` on a path it does know.

`client_v2.py`'s `submit_job` treats this as a hard input-validation error:
it requires an absolute `working_directory` and rejects a relative one
up front, rather than submitting a job whose output the client itself could
never read back.

## Footnote: the fake scheduler's known gap

The stand-in `sbatch` does not apply `--export=ALL,KEY=VALUE` to the script's
environment before running it (a fidelity gap in the 30-line stand-in, not a
FirecREST v2 behaviour) — so `echo $GREETING` above actually printed an empty line
in the real run, not `hello-v2`. Flagged here rather than silently smoothed over,
per this project's own citation rule: say when something you observed doesn't match
what the request implied.
