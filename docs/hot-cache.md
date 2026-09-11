# FirecREST hot cache

Curated llms.txt-style shortlist of the FirecREST calls this project actually uses, with
raw `curl` and the real responses observed against the local demo stack.

This is the "fast path" the agent checks *before* falling back to full RAG retrieval over
the OpenAPI spec (see `ARCHITECTURE.md` → "The hot cache"). Everything below was executed
and verified; nothing here is paraphrased from documentation.

- **Demo stack revision verified against:** FirecREST API `1.16.1` (per `doc/openapi/firecrest-api.yaml`)
- **Gateway:** `http://localhost:8000` (Kong). Keycloak: `http://localhost:8080`. Keycloak admin: `http://localhost:8080` (`admin`/`admin2`)
- **Verified on:** 2026-09-11, openSUSE Leap 16, Docker 29.4.0-ce, SELinux enforcing

> Note: the repo README and `ARCHITECTURE.md` refer to "the FirecREST v2 OpenAPI spec".
> The spec actually shipped in this demo revision is **v1.16.1**, and the endpoints differ
> (`X-Machine-Name` header rather than a `{system}` path segment). Trust this file over the
> prose until upstream is re-checked.

## 0. Two things that cause almost every failure

**1. The machine name is an HTTP header, not a path segment.**
Every call except `/status/*` and `/tasks/*` requires:

```
X-Machine-Name: cluster
```

Omit it and you get `400 {"description": "No machine name given"}` — including on URLs that
*look* right such as `/compute/jobs/cluster` (which returns the same 400, not a 404).

**2. Most endpoints are asynchronous.**
They return `{"success": "Task created", "task_id": "...", "task_url": "/tasks/..."}`.
The real payload must then be collected from `GET /tasks/{task_id}`. This applies to
`/compute/jobs` and `/compute/partitions`, and *also* to `GET /compute/jobs/{jobid}` — a call
that reads like a synchronous status lookup but is itself a task.

## 1. Authentication (OAuth2 client credentials, 300 s JWT)

```bash
TOK=$(curl -s -X POST \
  "http://localhost:8080/auth/realms/kcrealm/protocol/openid-connect/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials" \
  -d "client_id=firecrest-sample" \
  -d "client_secret=<see deploy/demo/demo_client/client_secrets.json>")
```

Observed: `token_type: Bearer`, `expires_in: 300`. The identity presented to FirecREST is
`service-account-firecrest-sample`.

Caveat worth knowing: the `firecrest-sample` client carries only the realm roles
`offline_access` and `uma_authorization` — **not** `firecrest-sa`. Calls still succeed on
this demo revision, so the demo's OPA/authz path is more permissive than `common.env`
(`F7T_AUTH_ROLE='firecrest-sa'`) suggests. Do not assume a real Alps tenant behaves the same.

## 2. Identity probe

```bash
curl -s -H "Authorization: Bearer $TOK" -H "X-Machine-Name: cluster" \
  http://localhost:8000/utilities/whoami
```
```json
{"description": "User information", "output": "service-account-firecrest-sample"}
```
This one *is* synchronous — a useful cheap liveness + auth check.

## 3. System availability (no `X-Machine-Name` needed)

```bash
curl -s -H "Authorization: Bearer $TOK" http://localhost:8000/status/systems
```
```json
{"description": "List of systems with status and description.",
 "out": [{"description": "System ready", "status": "available", "system": "cluster"},
         {"description": "System ready", "status": "available", "system": "cluster"}]}
```

## 4. Submit a job (multipart upload of a local script)

```bash
curl -s -X POST \
  -H "Authorization: Bearer $TOK" \
  -H "X-Machine-Name: cluster" \
  -F "file=@/tmp/echo-hello.sh" \
  http://localhost:8000/compute/jobs/upload
```
```json
{"success": "Task created",
 "task_id": "27c5ca07b15a7f388bd62e63c0e1eb3e",
 "task_url": "/tasks/27c5ca07b15a7f388bd62e63c0e1eb3e"}
```

Script used (`/tmp/echo-hello.sh`) — `--partition` is required, the demo exposes `part01`/`part02`:

```bash
#!/bin/bash
#SBATCH --job-name=echo-hello
#SBATCH --output=echo-hello.out
#SBATCH --error=echo-hello.err
#SBATCH --time=00:01:00
#SBATCH --partition=part01

echo hello
```

The alternative submissions are `POST /compute/jobs` (inline JSON) and
`POST /compute/jobs/path` (a script already on the remote filesystem, multipart with
`targetPath`). `account` is optional for all three; `env` takes a serialized JSON dict.

## 5. Collect the submission task → job ID

```bash
curl -s -H "Authorization: Bearer $TOK" -H "X-Machine-Name: cluster" \
  http://localhost:8000/tasks/27c5ca07b15a7f388bd62e63c0e1eb3e
```
Relevant fields inside `task.data`:

```json
{"jobid": 1,
 "job_file":     "/home/service-account-firecrest-sample/firecrest/<task_id>/echo-hello.sh",
 "job_file_out": "/home/service-account-firecrest-sample/firecrest/<task_id>/echo-hello.out",
 "job_file_err": "/home/service-account-firecrest-sample/firecrest/<task_id>/echo-hello.err"}
```

Job scripts and their outputs land under the service account's home, keyed by task ID — so
the stdout path is derivable from the task ID alone.

## 6. Job status

`GET /compute/jobs/{jobid}` is asynchronous; unwrap it through `/tasks/`.

```bash
curl -s -H "Authorization: Bearer $TOK" -H "X-Machine-Name: cluster" \
  "http://localhost:8000/compute/jobs"
```
→ `task_id` → `GET /tasks/{task_id}` yields `{"data": {}, "description": "Finished successfully", "status": "200"}`.

The authoritative state is Slurm's, one level down:

```bash
docker exec cluster sacct -a -X --format=JobID,JobName,Partition,State,ExitCode
```
```
1  echo-hello  part01  COMPLETED  0:0
```

Observed trap: the task payload for a status query reports `{"data": "Queued", "status": "100"}`
even for a job that has already finished. Task status is a snapshot of the *API call*, not of
the job. Use `sacct`/the job output as ground truth, not the task's `data` field.

## 7. Read job output

```bash
curl -s -H "Authorization: Bearer $TOK" -H "X-Machine-Name: cluster" \
  "http://localhost:8000/utilities/view?targetPath=/home/service-account-firecrest-sample/firecrest/<task_id>/echo-hello.out"
```
```json
{"description": "Success to head file.", "output": "hello\n"}
```

To get the bytes as a file rather than as JSON text, use `/utilities/download` — and note the
parameter is **`sourcePath`**, not `targetPath` (see "Parameter inconsistency" below):

```bash
curl -s -OJ -H "Authorization: Bearer $TOK" -H "X-Machine-Name: cluster" \
  "http://localhost:8000/utilities/download?sourcePath=<path>"
```
Observed response: `200`, `Content-Type: application/octet-stream`,
`Content-Length: 6`, `Content-Disposition: attachment; filename=echo-hello.out`, body `hello\n`.

## 8. Filesystem

The parameter is **`targetPath`** — `?path=` returns
`400 {"description": "Error on ls operation", "error": "'targetPath' not specified"}`.

```bash
curl -s -H "Authorization: Bearer $TOK" -H "X-Machine-Name: cluster" \
  "http://localhost:8000/utilities/ls?targetPath=/home"
curl -s -H "Authorization: Bearer $TOK" -H "X-Machine-Name: cluster" \
  "http://localhost:8000/utilities/stat?targetPath=<path>"
curl -s -H "Authorization: Bearer $TOK" -H "X-Machine-Name: cluster" \
  "http://localhost:8000/utilities/head?targetPath=<path>"
```

`/utilities/ls` returns entries with `name`, `group`, `permissions`, `last_modified`,
`link_target`. `/utilities/stat` returns a numeric `mode` (33204 = `0o100664`).

## 9. `get_job_log` and the failure-scenario demo

`ARCHITECTURE.md` lists `get_job_log(job_id)` as a wrapper tool and `logs/` as what DocMind
ingests. On this revision the job's own stdout/stderr are reachable through
`/utilities/view` and `/utilities/head` at the `job_file_out` / `job_file_err` paths from
step 5, so prompt 02's wrapper can implement `get_job_log` on top of those.

## 10. The bundled demo client, and how to drive it headlessly

`prompts/01-firecrest-demo-stack.md` step 4 asks for a job submitted "through the demo
client's own workflow". Finding that client takes longer than using it:

- `deploy/demo/demo_client/` holds only `config.py` and `client_secrets.json` — config
  fragments, not an application.
- `src/tests/template_client/`, which `deploy/demo/README.md` tells you to look at, **no
  longer exists** upstream (only `src/tests/automated_tests` remains). The README is stale.
- **The real client is `examples/UI-client-credentials/`** — a Flask + SocketIO app built by
  its own `Makefile`.
- There is **no client service in `deploy/demo/docker-compose.yml`** at all (17 services, none
  of them a client), and nothing maps port 7000 as the demo README claims. The client's own
  default port is **9090**.

It works against this API revision with the `pyfirecrest` version it pins (1.5.1), which is
contemporary with FirecREST v1.x — consistent with the v1.16.1 spec identified in §0.

### Configuration that works against the local demo stack

Copy `src/config.py.orig` → `src/config.py` and set:

| Setting | Value |
|---|---|
| `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | the demo's `firecrest-sample` client |
| `OIDC_AUTH_REALM` | `kcrealm` |
| `OIDC_AUTH_BASE_URL` | `http://localhost:8080` |
| `FIRECREST_URL` | `http://localhost:8000` |
| `SYSTEM_NAME` | `cluster` |
| `SYSTEM_PARTITIONS` | `['part01', 'part02']` |
| `SYSTEM_RESERVATION` | `None` |
| `USER_GROUP` | `service-account-firecrest-sample` |
| `SYSTEM_CONSTRAINTS` | `[]` |
| `CLIENT_PORT` | `9100` (see below) |

`client.py` builds the token URL as
`f"{OIDC_AUTH_BASE_URL}/auth/realms/{OIDC_AUTH_REALM}/protocol/openid-connect/token"`, so
`OIDC_AUTH_BASE_URL` must **not** include the trailing `/auth`.

**Port collision:** `CLIENT_PORT = 9090` conflicts with the demo stack's own `openapi`
service, which maps host `9090` → container `8080`. The client dies at startup with
`Address in use`. Use 9100.

### Running it

Because the client resolves Keycloak and Kong via `localhost`, it needs host networking —
which also means the `-p 9090:9090` in the example `Makefile` is dropped:

```bash
cp -r <clone>/examples/UI-client-credentials /tmp/f7t-demo-client   # build outside the clone
cd /tmp/f7t-demo-client && mkdir -p log
cp src/config.py.orig src/config.py   # then edit as per the table above
docker build -f ./docker/Dockerfile -t firecrest-live .
docker run -d --network host -v /tmp/f7t-demo-client/log:/var/log --name firecrest-live firecrest-live
```

### Driving it without a browser

All of these are plain JSON endpoints, so the client is scriptable:

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | landing page; also performs the system-availability probe |
| `/list_files?path=<dir>` | GET | Pyfirecrest-backed directory listing |
| `/list_jobs` | GET | status of jobs the client submitted |
| `/submit_job` | POST | form fields: `jobName`, `numberOfNodes`, `partition`, `constraint`, `steps`, `isPostProcess` |
| `/results` | GET | result download — **broken, see below** |

Verified end-to-end:

```
POST /submit_job  (jobName=echo-hello-client, partition=part01, numberOfNodes=1, steps=1)
→ {"data": "Batch started"}

$ docker exec cluster sacct -a -X --format=JobID,JobName,Partition,State,ExitCode,Elapsed
3  echo-hell+  part01  COMPLETED  0:0  00:00:30

GET /list_jobs → jobid 3, name echo-hello-client_1, state COMPLETED
GET /utilities/view?targetPath=/home/service-account-firecrest-sample/firecrest/<task>/job-3.out
→ "echo-hello-client_1 started on Fri Sep 11 15:25:28 UTC 2026
   echo-hello-client_1 finished on Fri Sep 11 15:25:58 UTC 2026"
```

### Two things about the client worth knowing

**Its "workflow" is deliberately fake.** `src/sbatch_templates/demo.sh.tmpl` sleeps 30 s,
writes a placeholder `out_${step}0.00.pyfrs`, and chains steps with
`#SBATCH --dependency=afterok`. It is a UI demo of the *plumbing*, not a real computation.
The 30-second elapsed time in `sacct` above is the template's `sleep`, not work.

**`GET /results` is broken.** It returns `{"data": "Download error: 'jobDir'"}` — the
handler reads a global `JOB_DIR` that the browser/SocketIO path populates but the direct
`/submit_job` path does not. Retrieve results via `/utilities/view` instead.

## Parameter inconsistency: `/utilities/download` uses `sourcePath`

Every other `/utilities/*` endpoint takes the path as **`targetPath`**. `/utilities/download`
is the exception: its size check runs through `targetPath` but its filename lookup reads
**`sourcePath`**. Passing only `targetPath` therefore crashes rather than erroring cleanly:

```
GET /utilities/download?targetPath=<path>   →  HTTP 500 Internal Server Error
```

Server-side traceback (`deploy/demo/logs/firecrest/utilities.log`, reproduced on 3 consecutive
attempts):

```
ERROR [app.py:1414] Exception on /download [GET]
  File "/utilities.py", line 740, in download
    file_name = os.path.basename(path)
TypeError: expected str, bytes or os.PathLike object, not NoneType
```

Upstream cause, in `src/utilities/utilities.py`:

- line 730 — `resp = common_fs_operation(request, "fsize")` reads `targetPath`
- line 739 — `path = request.args.get("sourcePath")` reads `sourcePath`, which is `None`

Correct usage, verified working:

```
GET /utilities/download?sourcePath=<path>       →  200, application/octet-stream, body returned
GET /utilities/download?targetPath=<p>&sourcePath=<p>  →  200 (both accepted)
```

Worth reporting upstream as a 400-on-missing-parameter bug rather than a 500. Relevant to the
MCP wrapper: `download_file(path)` must send `sourcePath`, and must not be built by analogy
with the other `/utilities/*` tools. `/utilities/view` (text) and `/utilities/head` are the
working alternatives when only the contents matter.
