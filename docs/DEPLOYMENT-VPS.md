# Remote deployment: FirecREST v2 and the MCP server behind Cloudflare Access

This document describes how the FirecREST v2 demo stack and the FirecREST MCP server
run on a single Linux VPS, and how the MCP endpoint is published on the internet
behind Cloudflare Access. It is the reference for installing, operating, verifying and
removing that deployment.

It complements [`local-env.md`](./local-env.md), which covers the same components on a
developer workstation. The deployment described here was installed and verified on
13 October 2026 (FirecREST 2.6.1, `pyfirecrest` 3.10.0, `mcp` SDK 2.x).

Values that identify a specific account are written as placeholders:

| Placeholder | Meaning |
|---|---|
| `mcp.example.org` | public hostname of the MCP endpoint |
| `<team>` | Cloudflare Zero Trust team name (`<team>.cloudflareaccess.com`) |
| `<tunnel-id>` | Cloudflare Tunnel UUID |
| `<repo>` | `/home/master/Sviluppo/firecrest-agentic-workbench` |
| `<v2>` | `/home/master/Sviluppo/firecrest-demo/firecrest-v2` (clone of `eth-cscs/firecrest-v2`, tag `2.6.1`) |

## Contents

1. [Architecture](#1-architecture)
2. [Security model](#2-security-model)
3. [Prerequisites](#3-prerequisites)
4. [Installation procedure](#4-installation-procedure)
5. [Connecting an MCP client](#5-connecting-an-mcp-client)
6. [Operations runbook](#6-operations-runbook)
7. [Verification checklist](#7-verification-checklist)
8. [Troubleshooting](#8-troubleshooting)
9. [Rollback and decommissioning](#9-rollback-and-decommissioning)
10. [Change history](#10-change-history)

## 1. Architecture

```
 MCP client (Claude Code, laptop)
        │  HTTPS, Streamable HTTP
        ▼
 ┌──────────────────────────────┐
 │ Cloudflare edge              │  Cloudflare Access application on mcp.example.org
 │  - Access login (email OTP)  │  policy: Allow, named e-mail addresses only
 │  - issues Access JWT         │
 └──────────────┬───────────────┘
                │ Cloudflare Tunnel (outbound-only connection from the VPS)
                ▼
 ┌─────────────────────────────────────────────── VPS ─────────────────────────┐
 │ cloudflared (system service)                                                 │
 │   ingress mcp.example.org → http://127.0.0.1:8765                            │
 │   originRequest.access.required = true  (validates the Access JWT)           │
 │                │                                                             │
 │                ▼                                                             │
 │ firecrest-mcp-v2.service      127.0.0.1:8765   MCP server (Python, venv)     │
 │   Host/Origin allowlist (DNS-rebinding protection)                           │
 │   guardrails: approved templates, limits, approver, audit log                │
 │                │ FirecREST v2 REST API + OAuth2 client credentials           │
 │                ▼                                                             │
 │ firecrest-demo-v2.service     Docker Compose (eth-cscs/firecrest-v2 2.6.1)   │
 │   firecrest  127.0.0.1:8000   gateway                                        │
 │   keycloak   127.0.0.1:18080  token issuer (realm kcrealm)                   │
 │   slurm      127.0.0.1:6820   dummy Slurm cluster (slurmrestd), ssh :2222    │
 └──────────────────────────────────────────────────────────────────────────────┘
```

### Components

| Component | Unit / container | Listens on | Started by |
|---|---|---|---|
| FirecREST v2 gateway | `firecrest-v2-firecrest-1` | `127.0.0.1:8000` (API), `127.0.0.1:5678` (debugpy, see below) | `firecrest-demo-v2.service` |
| Keycloak | `firecrest-v2-keycloak-1` | `127.0.0.1:18080` (HTTP), `127.0.0.1:9090` (management) | `firecrest-demo-v2.service` |
| Dummy Slurm cluster | `firecrest-v2-slurm-1` | `127.0.0.1:6820`, `127.0.0.1:2222`, `127.0.0.1:5665-5666` | `firecrest-demo-v2.service` |
| FirecREST MCP server | `firecrest-mcp-v2.service` | `127.0.0.1:8765` | systemd |
| Cloudflare Tunnel client | `cloudflared.service` | outbound only | systemd |

Every listening port is bound to the loopback interface. The host firewall (UFW) allows
only SSH (22/tcp); no rule is added for this deployment.

The upstream compose file starts the gateway under `debugpy` on port 5678, a development
setting of the demo stack. A debugger attached to that port can run arbitrary code inside
the gateway container, so it must stay on loopback: never change `F7T_HOST_IP` to a
non-loopback address. Other services already present on the host (for example a web
panel on `*:80`) are outside the scope of this document.

### Files on the host

| Path | Purpose | Versioned |
|---|---|---|
| `<repo>/systemd/firecrest-demo-v2.service` | unit source for the v2 stack | yes |
| `<repo>/systemd/firecrest-mcp-v2.service` | unit source for the MCP server | yes |
| `/etc/systemd/system/firecrest-*-v2.service` | installed copies (root:root 0644) | no |
| `<repo>/firecrest-mcp/.env` | FirecREST v2 client settings and public hostname (mode 0600) | no (gitignored) |
| `<repo>/firecrest-mcp/.venv/` | Python environment of the MCP server | no (gitignored) |
| `<repo>/firecrest-mcp/logs/` | job logs and `audit.jsonl` | no (gitignored) |
| `<repo>/firecrest-mcp/downloads/` | files fetched by `download_file` | no (gitignored) |
| `<repo>/templates/` | approved job templates | yes |
| `<repo>/config/limits.yaml` | submission limits (nodes, time, partitions) | yes |

## 2. Security model

The MCP server has **no authentication of its own**. Access control is layered in front
of it, and each layer is independent of the others:

| Layer | Control | Failure it covers |
|---|---|---|
| 1. Cloudflare Access | Only the e-mail addresses named in an *Allow* policy can obtain a session; login by one-time PIN | anonymous internet access |
| 2. Tunnel JWT validation | `cloudflared` rejects any request without a valid Access JWT for this application (`access.required: true`, application AUD tag) | a misconfigured or deleted Access policy |
| 3. Loopback bind | All services listen on `127.0.0.1`; UFW allows only SSH | direct access to the VPS address |
| 4. Host/Origin allowlist | The MCP SDK DNS-rebinding protection accepts only localhost and `FIRECREST_MCP_PUBLIC_HOST`; anything else gets `421` | DNS rebinding from a browser |
| 5. Job guardrails | Only approved templates can be submitted; parameters are validated against `config/limits.yaml`; an approver identifier is mandatory; every decision is written to `logs/audit.jsonl` | arbitrary code execution through `submit_job` |

Operational rules that follow from this model:

- **Create the Access application before the tunnel route.** A route without an Access
  application publishes the server to everyone.
- **Never bind the MCP server to `0.0.0.0`.**
- **Never put secrets in unit files.** They live in `firecrest-mcp/.env` (0600).
- The FirecREST credentials in `.env` are those of the public demo client
  (`firecrest-test-client`). Production credentials must never be used on this host.

## 3. Prerequisites

- Linux host with systemd, Docker Engine and the Compose plugin; the operating user
  (`master`) is in the `docker` group. Tested on Ubuntu 26.04 with AppArmor (no SELinux).
- `git`, `gh` (authenticated for the repository), and `uv` in `~/.local/bin`.
- `sudo` for installing units and `cloudflared`.
- A Cloudflare account that holds the DNS zone of the public hostname, with Zero Trust
  enabled.
- Free loopback ports: 8000, 5678, 8765, 18080, 9090, 6820, 2222, 5665-5666.

## 4. Installation procedure

Each step ends with a verification. Do not continue if a verification fails. Steps marked
**(sudo)** change the system and should be approved individually.

### Step 0: clone the repositories

```bash
git clone https://github.com/VadaLinux/firecrest-agentic-workbench.git <repo>
git -C <repo> rev-parse --short HEAD          # record the commit being deployed

git clone https://github.com/eth-cscs/firecrest-v2.git <v2>
git -C <v2> checkout 2.6.1
```

Verify: `git -C <v2> describe --tags` prints `2.6.1`.

### Step 1: configure the MCP server

```bash
cd <repo>/firecrest-mcp
cp .env.example .env && chmod 600 .env
```

Set at least the following keys in `.env` (see `.env.example` for the meaning of each):

```dotenv
FIRECREST_API_VERSION=v2
FIRECREST_V2_BASE_URL=http://localhost:8000
FIRECREST_V2_TOKEN_URL=http://localhost:18080/auth/realms/kcrealm/protocol/openid-connect/token
FIRECREST_V2_CLIENT_ID=firecrest-test-client
FIRECREST_V2_CLIENT_SECRET=<demo client secret from the firecrest-v2 user guide>
FIRECREST_V2_SYSTEM=cluster-slurm-api
FIRECREST_MCP_PUBLIC_HOST=mcp.example.org
```

Create the server's Python environment and its writable directories:

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
mkdir -p logs downloads
```

Verify:

```bash
stat -c '%a' .env                      # 600
git -C <repo> check-ignore -q firecrest-mcp/.env && echo ignored
.venv/bin/python server.py --help      # prints the usage text
```

### Step 2: install the v2 stack unit (sudo)

```bash
sudo install -o root -g root -m 644 <repo>/systemd/firecrest-demo-v2.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/firecrest-demo-v2.service
sudo systemctl enable firecrest-demo-v2.service
```

The unit runs `scripts/workbench-up.sh --only-v2`, which starts only the FirecREST v2
services. It is `Type=oneshot` with `RemainAfterExit=yes` and has no `ExecStop`.

### Step 3: start the v2 stack (sudo)

```bash
sudo systemctl start firecrest-demo-v2.service   # first start pulls/builds images: minutes
systemctl is-active firecrest-demo-v2.service    # active
docker ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'
```

Verify: three containers are up, Keycloak and Slurm report `healthy`, every published port
starts with `127.0.0.1:`, and:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/status/systems          # 401
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18080/auth/realms/kcrealm     # 200
```

### Step 4: smoke-test FirecREST v2

```bash
cd <repo>
uv run --with pyfirecrest==3.10.0 python scripts/smoke_local.py
```

Verify: the script ends with `OK — token, systems, submit, poll, output all verified.` It
prints the token length only, never the token.

### Step 5: install and start the MCP server unit (sudo)

```bash
sudo install -o root -g root -m 644 <repo>/systemd/firecrest-mcp-v2.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/firecrest-mcp-v2.service
sudo systemctl enable --now firecrest-mcp-v2.service
```

Verify the bind address and the Host allowlist:

```bash
ss -Htlnp 'sport = :8765'        # 127.0.0.1:8765 only

INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}'
for h in 127.0.0.1:8765 mcp.example.org evil.example.com; do
  o=; [ "$h" = mcp.example.org ] && o="-H Origin:https://$h"
  printf '%s -> ' "$h"
  curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8765/mcp \
    -H "Host: $h" $o -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' -d "$INIT"
done
# expected: 200, 200, 421
```

### Step 6: configure Cloudflare

Perform these sub-steps **in this order**.

**6.1 Login method.** In Zero Trust, *Settings → Authentication → Login methods*, make sure
**One-time PIN** is enabled.

**6.2 Access application.** *Access → Applications → Add an application → Self-hosted*:

| Field | Value |
|---|---|
| Name | `FirecREST MCP` |
| Session duration | 24 h |
| Public hostname | `mcp.example.org`, path empty (the whole hostname) |
| Policy | action **Allow**; include **Emails** = the named operators only |

Do not use *Bypass*, *Everyone* or IP-based rules. Record the application **AUD tag**.

**6.3 Tunnel.** Install `cloudflared` as a system service with a token-based tunnel
(*Networks → Tunnels*). Add one public hostname route:

| Field | Value |
|---|---|
| Hostname | `mcp.example.org`, path empty |
| Service | `http://127.0.0.1:8765` |
| HTTP Host header | empty (the public hostname must reach the server) |
| Access → Enforce JWT validation | **on**, application `FirecREST MCP` (AUD tag) |

The resulting remotely-managed ingress is:

```json
{"ingress": [
  {"hostname": "mcp.example.org", "service": "http://127.0.0.1:8765",
   "originRequest": {"access": {"required": true, "teamName": "<team>", "audTag": ["<aud>"]}}},
  {"service": "http_status:404"}
]}
```

**6.4 External verification**, from a host outside the VPS, without credentials:

```bash
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' https://mcp.example.org/mcp
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://mcp.example.org/mcp -d '{}'
```

Expected: `302` towards `https://<team>.cloudflareaccess.com/cdn-cgi/access/login/...` for
both. On the VPS, no new request from a non-loopback address may appear in the MCP log:

```bash
journalctl -u firecrest-mcp-v2 --since '-5 min' -o cat | grep 'HTTP/1.1'
```

Finally, open `https://mcp.example.org/mcp` in a browser, log in with an authorised e-mail
and the one-time PIN. The server answers
`{"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"Bad Request: Missing session ID"}}`,
which is the expected reply to a plain browser request.

## 5. Connecting an MCP client

### Claude Code

```bash
claude mcp add --transport http --scope user firecrest-v2 https://mcp.example.org/mcp
claude            # then run /mcp, select firecrest-v2, choose Authenticate
```

Cloudflare Access answers unauthenticated MCP requests with
`WWW-Authenticate: Cloudflare-Access resource_metadata=...`, which lets an MCP client
discover the authorisation server and open a browser for the Access login. After login,
`/mcp` lists six tools:

> **Status (14 October 2026):** the `WWW-Authenticate` header and the browser login are
> verified; the full interactive flow from Claude Code (`/mcp` → *Authenticate*) has not
> yet been tested end to end. Run check 8 of the [verification checklist](#7-verification-checklist)
> from the demo laptop before relying on it, and keep a service token (below) as a fallback.

| Tool | Purpose |
|---|---|
| `submit_job` | submit an approved template with declared parameters |
| `get_job_status` | job state and exit code |
| `get_job_log` | job standard output |
| `list_files` | list a remote directory |
| `download_file` | fetch a remote file (saved under `downloads/`) |
| `cancel_job` | cancel a job |

Example request for the agent: *"Submit the `hello` template on `cluster-slurm-api` with
1 node, 5 minutes, partition `part01`, job name `demo`, message `hello`, working directory
`/home/fireuser`, approved by `operator-ok`; then show its status and output."*

`approved_by` must match `^[A-Za-z0-9_.-]+$`; spaces or punctuation are rejected with
`approved_by_required`.

### Headless agents

Clients without a browser cannot complete the one-time PIN login. They need a Cloudflare
Access **service token** and a second policy with action *Service Auth* on the same
application; the client sends `CF-Access-Client-Id` and `CF-Access-Client-Secret` headers.
Agents running on the VPS itself should use `http://127.0.0.1:8765/mcp` directly.

## 6. Operations runbook

### Status

```bash
systemctl status firecrest-demo-v2 firecrest-mcp-v2 cloudflared --no-pager
docker ps --format '{{.Names}}\t{{.Status}}'
ss -Htlnp | grep -E ':(8000|8765|18080)\b'
```

### Logs

```bash
journalctl -u firecrest-mcp-v2 -f              # MCP server (requests, errors)
journalctl -u firecrest-demo-v2 -o cat          # bring-up of the v2 stack
docker logs --tail 100 firecrest-v2-firecrest-1 # FirecREST gateway
tail -f <repo>/firecrest-mcp/logs/audit.jsonl   # job submissions: submitted / denied, with reason
sudo journalctl -u cloudflared -f               # tunnel, configuration versions
```

### Restart

```bash
sudo systemctl restart firecrest-mcp-v2         # after a code or .env change
sudo systemctl restart cloudflared              # tunnel only
```

### Stop and start the v2 stack

The v2 unit has no `ExecStop`: stopping it does not stop the containers.

```bash
sudo systemctl stop firecrest-mcp-v2
(cd <v2> && docker compose down)               # never add -v: it deletes the volumes
sudo systemctl restart firecrest-demo-v2        # start again
sudo systemctl start firecrest-mcp-v2
```

### Update the deployed code

```bash
cd <repo>
git pull --ff-only
git log --oneline -1                            # record the new commit
uv pip install --python firecrest-mcp/.venv/bin/python -r firecrest-mcp/requirements.txt
sudo systemctl restart firecrest-mcp-v2
```

If a unit file changed in the repository, reinstall it with `install` and run
`systemctl daemon-reload` before restarting. Then run the [verification checklist](#7-verification-checklist).

### Change the public hostname

1. Update the Access application and the tunnel route in Cloudflare (Access first).
2. Set the new value of `FIRECREST_MCP_PUBLIC_HOST` in `.env` and restart `firecrest-mcp-v2`.
3. Run steps 5 and 6.4 of the verification.

### Reboot behaviour

Both units are enabled. After a reboot the v2 stack starts first and the MCP server follows
(`Wants=`/`After=firecrest-demo-v2.service`); `cloudflared` reconnects by itself.

## 7. Verification checklist

Run after installation, after every update and before a demo.

| # | Check | Command (on the VPS unless stated) | Expected |
|---|---|---|---|
| 1 | Units active | `systemctl is-active firecrest-demo-v2 firecrest-mcp-v2 cloudflared` | `active` ×3 |
| 2 | Loopback only | `ss -Htln \| awk '{print $4}' \| grep -E ':(8000\|5678\|8765\|18080\|9090\|6820\|2222\|5665\|5666)$'` | `127.0.0.1:` only |
| 3 | Firewall | `sudo ufw status` | only 22/tcp allowed |
| 4 | FirecREST | `scripts/smoke_local.py` (step 4) | `OK` |
| 5 | Allowlist | curl loop of step 5 | 200, 200, 421 |
| 6 | Access, anonymous | curl of step 6.4 from outside | 302 to `<team>.cloudflareaccess.com` |
| 7 | No anonymous traffic at the origin | `journalctl -u firecrest-mcp-v2` after check 6 | no new non-loopback request |
| 8 | Authenticated path | browser login, or Claude Code `/mcp` | `Missing session ID`, or six tools listed |
| 9 | Guardrails | `submit_job` without a template | refused (`FirecRESTError`) |

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `421 Invalid Host header` through the tunnel | `FIRECREST_MCP_PUBLIC_HOST` missing or different from the public hostname | Set it in `.env` and restart `firecrest-mcp-v2`. The value must be a bare DNS name: no scheme, port, underscore or IP |
| Server exits with `configuration error: FIRECREST_MCP_PUBLIC_HOST must be a bare DNS hostname` | Invalid value in `.env` | Correct the value. Hostnames with `_` are invalid in DNS and in public TLS certificates |
| Anonymous requests reach the server (visible in the MCP log) | No Access application on the hostname, or a *Bypass*/*Everyone* policy | Create or fix the Access application (step 6.2); enable JWT validation on the route (6.3) |
| Access login page offers no login method | One-time PIN not enabled | Step 6.1 |
| `Bad Request: Missing session ID` in a browser | Normal: a browser does not speak the MCP protocol | None |
| `submit_job` returns `FirecRESTError` | Free-form script, missing `system` or missing `template` | Use an approved template from `templates/` and pass `system` |
| `submit_job` returns `GuardrailDenied` | See `reason` in `logs/audit.jsonl`: `approved_by_required`, `working_directory_invalid`, `template_parameters_invalid`, limits exceeded | Fix the argument; `approved_by` must match `^[A-Za-z0-9_.-]+$` |
| `submit_job needs an absolute working_directory` | v2 requires a working directory | Pass e.g. `/home/fireuser` |
| `download_file` fails with a read-only error | `downloads/` missing or not writable | `mkdir -p <repo>/firecrest-mcp/downloads` as `master` |
| `ModuleNotFoundError: mcp` at start | Server venv missing or built from the wrong requirements | Recreate `firecrest-mcp/.venv` (step 1) |
| Pages served by the old code after an update | Service not restarted | `sudo systemctl restart firecrest-mcp-v2` |

## 9. Rollback and decommissioning

Take the MCP endpoint offline, keeping the stack:

```bash
# Cloudflare: delete the public hostname route of the tunnel (Access application may stay)
sudo systemctl disable --now firecrest-mcp-v2
```

Remove the whole deployment:

```bash
sudo systemctl disable --now firecrest-mcp-v2
(cd <v2> && docker compose down)
sudo systemctl disable firecrest-demo-v2
sudo rm /etc/systemd/system/firecrest-mcp-v2.service /etc/systemd/system/firecrest-demo-v2.service
sudo systemctl daemon-reload
# Cloudflare: delete the tunnel route, then the Access application and any service tokens
```

Revert to a previous version of the code with `git checkout <commit>` in `<repo>`, followed
by the update procedure in section 6.

## 10. Change history

| Date | Change | Reference |
|---|---|---|
| 2026-10-12 | `workbench-up.sh`: optional FirecREST v2 stack (`FIRECREST_V2_DIR`) | PR #33 |
| 2026-10-13 | `workbench-up.sh --only-v2` and `firecrest-demo-v2.service` | PR #34 |
| 2026-10-13 | `firecrest-mcp-v2.service` (loopback bind, hardening) and `FIRECREST_MCP_PUBLIC_HOST` allowlist | PR #35 |
| 2026-10-13 | `.env` loaded before the allowlist is built (fix: public host was rejected with 421) | PR #36 |
| 2026-10-13 | Cloudflare Access application, tunnel route with JWT validation, external verification | this document |
