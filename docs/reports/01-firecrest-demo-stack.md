# Prompt 01 completion report — FirecREST demo stack

**Task:** `prompts/01-firecrest-demo-stack.md`
**Status:** complete, with three declared deviations (see §6)
**Date:** 2026-09-11
**Repo:** https://github.com/VadaLinux/firecrest-agentic-workbench
**Commit at time of writing:** `0a3e3e9`

---

## 1. Objective

Stand up a fully local FirecREST stack (Keycloak + Kong + dummy Slurm cluster), prove a job
can be submitted through it and its stdout retrieved, and capture the exact endpoints and
payloads used — the seed of the "hot cache" that later prompts (02, 04, 05) depend on.

No real CSCS credentials are involved; the whole exercise runs offline on one laptop.

## 2. Environment

| Item | Value |
|---|---|
| Host | openSUSE Leap 16.0, kernel 6.12, x86_64, SELinux `Enforcing` |
| Container runtime | Docker 29.4.0-ce + Compose 2.33.1 (installed from `repo-oss` during this task) |
| FirecREST API version | **1.16.1** (per `doc/openapi/firecrest-api.yaml`) |
| Clone | `~/Sviluppo/firecrest` (sibling of this repo, gitignored) |
| Gateway / Keycloak | `http://localhost:8000` / `http://localhost:8080` |

## 3. What was done

1. Installed Docker and Docker Compose (`sudo zypper -n install docker docker-compose`),
   enabled and started `docker.service`, added the working user to the `docker` group.
2. Cloned `eth-cscs/firecrest` into a sibling directory. No content inside the clone was
   modified (`git status` clean).
3. Applied `chmod 400` to `deploy/test-build/environment/keys/ca-key` and `user-key` as
   `deploy/demo/README.md` requires.
4. Built the stack with `docker compose build`. The `cluster` image compiles Slurm from
   source; total build time was on the order of 10–20 minutes, as the upstream README warns.
5. Brought the stack up with `docker compose up -d`.
6. Diagnosed and fixed a full-stack startup failure (§5).
7. Submitted an `echo hello` job through the gateway using the bundled demo client's own
   credentials, waited for completion, and retrieved its stdout.
8. Wrote `docs/hot-cache.md` with every call used, recorded verbatim.

## 4. Result

15 of 15 services are running. Only `kong` declares a healthcheck, so "Up" alone proves
little; the stack was therefore verified functionally, end to end:

**Job submission, via the API gateway**

```
POST http://localhost:8000/compute/jobs/upload     (multipart, file=echo-hello.sh)
Headers: Authorization: Bearer <JWT>   X-Machine-Name: cluster
→ {"success": "Task created", "task_id": "27c5ca07b15a7f388bd62e63c0e1eb3e",
   "task_url": "/tasks/27c5ca07b15a7f388bd62e63c0e1eb3e"}
```

**Completion, confirmed against Slurm itself**

```
$ docker exec cluster sacct -a -X --format=JobID,JobName,Partition,State,ExitCode
JobID  JobName     Partition  State      ExitCode
1      echo-hello  part01     COMPLETED  0:0
2      echo-hello  part01     COMPLETED  0:0
```

**Stdout retrieved**

```
$ curl -s ... "http://localhost:8000/utilities/view?targetPath=<job_file_out>"
{"description": "Success to head file.", "output": "hello\n"}

$ curl -s -OJ ... "http://localhost:8000/utilities/download?sourcePath=<job_file_out>"
HTTP 200  Content-Type: application/octet-stream
          Content-Length: 6
          Content-Disposition: attachment; filename=echo-hello.out
          body: hello\n
```

**Second verification — through the bundled demo client itself**

The job was also submitted through `examples/UI-client-credentials/` (the actual bundled
client; see §6.1 for why it was hard to locate), driven headlessly over HTTP:

```
POST http://localhost:9100/submit_job
  jobName=echo-hello-client  partition=part01  numberOfNodes=1  steps=1
→ {"data": "Batch started"}

$ docker exec cluster sacct -a -X --format=JobID,JobName,Partition,State,ExitCode,Elapsed
3  echo-hell+  part01  COMPLETED  0:0  00:00:30

GET /list_jobs → jobid 3, name echo-hello-client_1, state COMPLETED

GET /utilities/view?targetPath=/home/service-account-firecrest-sample/firecrest/<task>/job-3.out
→ "echo-hello-client_1 started on Fri Sep 11 15:25:28 UTC 2026
   echo-hello-client_1 finished on Fri Sep 11 15:25:58 UTC 2026"
```

This also confirms that the client's pinned `pyfirecrest` **1.5.1** talks to this API
revision successfully — worth knowing, because it is the version the bundled client ships and
it is far behind the current 3.x line.

## 5. Blocker encountered and resolved

### Symptom

Immediately after `docker compose up -d`, 8 of 15 services exited with code 1, while the
others stayed up. Service logs showed permission failures on files whose Unix permissions
were correct:

```
kong:    error parsing declarative config file /kong.yml:  /kong.yml: Permission denied
tasks:   Error: '/var/log/tasks.gunicorn.log' isn't writable [PermissionError(13)]
storage: Error: '/var/log/storage.gunicorn.log' isn't writable [PermissionError(13)]
```

`kong/kong.yml` on the host was plain `-rw-r--r--`, owned by the invoking user. Nothing in
the demo configuration was wrong.

### Root cause

The host runs **SELinux in `Enforcing` mode**. Files under the home directory carry the
label `user_home_t`, which processes confined as `container_t` cannot read — regardless of
Unix permission bits.

```
$ getenforce
Enforcing
$ ls -Z deploy/demo/kong/kong.yml
unconfined_u:object_r:user_home_t:s0 kong/kong.yml
```

This is an environment property, not a defect in FirecREST, and it is invisible to anyone
developing on a machine without SELinux.

### Which systems this affects

The problem is specific to **SELinux-enabled** distributions. It does **not** occur on
AppArmor-based ones, because AppArmor does not implement label-based file access control —
there is no `user_home_t` equivalent, so bind mounts behave normally.

| Mandatory access control | Default on | Affected by this issue |
|---|---|---|
| SELinux | RHEL family (RHEL, CentOS Stream, Fedora, Rocky, Alma); **openSUSE Leap 16 / SLES 16** | yes |
| AppArmor | Ubuntu, Debian; openSUSE Leap ≤ 15.x | no |

Note that SUSE switched defaults: apparmor was the traditional SUSE answer, but **SELinux is
the default in openSUSE Leap 16.0 and SLES 16**. Most third-party summaries still describe
"Ubuntu, Debian and SUSE" as AppArmor territory, which is now wrong for SUSE and would send
anyone debugging this on Leap 16 down the wrong path.

Confirmed on the build host used here (openSUSE Leap 16.0): `getenforce` returns `Enforcing`,
`/etc/selinux/config` has `SELINUX=enforcing`, and the `apparmor` package is not installed
at all.

Practical check before assuming this is the cause:

```bash
getenforce 2>/dev/null        # SELinux present and enforcing?
ls -Z <bind-mounted-file>     # does it carry user_home_t?
sestatus -v                   # full policy details
```

On a Debian or Ubuntu host, `getenforce` will simply not exist — and that is the expected,
healthy answer there, not a missing prerequisite.

### Fix

Relabel only the host paths that are actually bind-mounted into the stack:

```bash
sudo chcon -R -t container_file_t \
  ~/Sviluppo/firecrest/deploy/demo \
  ~/Sviluppo/firecrest/deploy/test-build/environment/keys \
  ~/Sviluppo/firecrest/doc/openapi
```

then `docker compose down && docker compose up -d`. All 15 services came up clean and no
errors appear in any service log.

The label is stored in a filesystem xattr, so it survives a reboot but **is reverted by
`restorecon`** or any full system relabel — worth knowing before demo day.

The stack was **not** left with SELinux set to permissive; weakening host security was an
unacceptable workaround for a three-command fix.

## 6. Declared deviations from the prompt

1. ~~**The bundled demo client application was not used directly.**~~ **Resolved.** The
   deviation was real and has since been closed: the job was re-submitted through the
   bundled demo client itself. The confusion was that the client's documented location is
   wrong — `deploy/demo/README.md` points at `src/tests/template_client/`, which no longer
   exists upstream, and the demo README advertises a web client on port 7000 that no compose
   service publishes. The actual client is `examples/UI-client-credentials/` (Flask +
   SocketIO, default port 9090, `pyfirecrest` 1.5.1), and it works against this API revision.
   Job 3 in §4 above was submitted through it. The initial submission via the gateway using
   the bundled client's own credentials was a valid but weaker form of verification; the
   stronger form is now also on record. Details: `docs/hot-cache.md` §10.
2. **SELinux labels inside the `firecrest` clone were changed** (`deploy/demo`,
   `deploy/test-build/environment/keys`, `doc/openapi`). Prompt 01 step 6 forbids modifying
   the clone. File *contents* are untouched and the clone's `git status` is clean, but this
   is a metadata change and is disclosed as such.
3. **A pre-existing commit** (`62b0d12`, fixing the broken Hermes repo link in `README.md`)
   predates prompt 01's execution, and the Git repository + GitHub remote were initialised
   before the prompt's "nothing else changes" rule was read. Both were agreed with the repo
   owner beforehand.

Within this repository, the only change attributable to prompt 01 is the addition of
`docs/hot-cache.md`.

## 7. Findings beyond the definition of done

These cost real time to discover and materially affect prompts 02–05.

### 7.1 The spec shipped is v1.16.1, not v2

`README.md` and `ARCHITECTURE.md` both describe "the FirecREST v2 OpenAPI spec", and
`ARCHITECTURE.md` states the wrapper is built from it. The spec actually present in
`doc/openapi/firecrest-api.yaml` declares `version: 1.16.1`, and its endpoint shapes differ
from the generic v2 documentation. **Needs verification with CSCS before prompt 02 is
considered done**, because the wrapper's request models depend on which version is real.

### 7.2 The machine name is an HTTP header

Every call except `/status/*` and `/tasks/*` requires:

```
X-Machine-Name: cluster
```

Omit it and the response is `400 {"description": "No machine name given"}` — including for
URLs that look correct, such as `/compute/jobs/cluster`, which returns the same 400 rather
than a 404. `?machine=` and `?machineName=` are both ignored.

Consequence for design: `ARCHITECTURE.md` specifies `submit_job(script, system, account)`.
The `system` argument must be realised as this header, not as a path segment.

### 7.3 Most endpoints are asynchronous

They return `{"success": "Task created", "task_id": ...}`, and the real payload must be
collected from `GET /tasks/{task_id}`.

This applies to `GET /compute/jobs` **and to `GET /compute/jobs/{jobid}`**, which reads like
a synchronous status lookup but is itself a task. Polling it naively yields no job state at
all — the state is one level further down.

Worse, the status task reports:

```json
{"data": "Queued", "status": "100"}
```

for a job that has **already completed successfully**. Task status is a snapshot of the API
call, not of the job. During this task that nearly caused a completed job to be reported as
failed. Ground truth must come from `sacct` or from the job's output file. The wrapper's
`get_job_status(job_id)` must be written with this in mind.

### 7.4 `/utilities/download` uses `sourcePath`, not `targetPath`

Every other `/utilities/*` endpoint takes `targetPath`. `/utilities/download` is inconsistent,
and fails badly rather than cleanly:

```
GET /utilities/download?targetPath=<path>   →  HTTP 500 Internal Server Error
```

Server-side traceback, reproduced on three consecutive attempts
(`deploy/demo/logs/firecrest/utilities.log`):

```
ERROR [app.py:1414] Exception on /download [GET]
  File "/utilities.py", line 740, in download
    file_name = os.path.basename(path)
TypeError: expected str, bytes or os.PathLike object, not NoneType
```

Upstream cause, `src/utilities/utilities.py`:

- line 730 — `resp = common_fs_operation(request, "fsize")` reads `targetPath`
- line 739 — `path = request.args.get("sourcePath")` reads `sourcePath`, which is `None`

Both work when supplied together; `sourcePath` alone works:

```
?sourcePath=<path>                          →  200, octet-stream, correct body
?targetPath=<p>&sourcePath=<p>              →  200
```

This is a genuine upstream bug: a missing required parameter should be a 400, not an
unhandled HTTP 500. Worth reporting to `eth-cscs/firecrest`. It directly constrains prompt
02: `download_file()` must send `sourcePath` and must not be written by analogy with the
other `/utilities/*` tools.

### 7.5 Operational notes for demo day

- The `deploy/demo` compose file sets **no restart policy**. `docker.service` is enabled and
  will start, but the containers will not come back after a reboot. `docker compose up -d`
  must be run before any rehearsal or the live session.
- The `f7t-base` container exiting with code 0 is normal — it is a build-stage container,
  not a long-running service. It should not be mistaken for a failed service.
- The stack's first build compiles Slurm inside the `cluster` image and takes 10–20 minutes.
  Rehearse from a warm image cache, and never rebuild on demo day.

### 7.6 Upstream documentation points at paths that no longer exist

Three separate references in this revision send a reader to the wrong place:

- `deploy/demo/README.md` says the client source is at `src/tests/template_client/`. That
  directory does not exist; only `src/tests/automated_tests` remains.
- The same README says the demo web client is at `localhost:7000`. No service in
  `deploy/demo/docker-compose.yml` publishes that port, and none is a client at all.
- `README.md` and `ARCHITECTURE.md` describe the API as v2 while the shipped spec is v1.16.1
  (§7.1).

None of these are fatal, but each costs a reader time, and all three land in the first hour of
trying to reproduce the build. Worth reporting upstream.

## 8. Artefacts produced

| Artefact | Location |
|---|---|
| Verified call shortlist (raw curl + observed responses), incl. §10 on the bundled client | `docs/hot-cache.md` |
| This report | `docs/reports/01-firecrest-demo-stack.md` |
| Job flow script (token → submit → poll → stdout) | `/tmp/f7t_flow.py` |
| Bundled demo client, built and configured for the local stack | `/tmp/f7t-demo-client` (`firecrest-live` image, container on `:9100`) |
| Reusable bring-up procedure and pitfalls | skill `firecrest-demo-stack` |

## 9. Next step

Prompt 02 — build `firecrest-mcp/`. Two open questions to settle first:

1. Is the target API v1.16.1 (as shipped here) or v2 (as the prose claims)? The request
   models depend on the answer.
2. Should the `system` parameter be surfaced to the agent as a tool argument even though it
   only ever becomes the `X-Machine-Name` header? `ARCHITECTURE.md` says yes; the demo only
   has one system, so it is untested with more than one.
