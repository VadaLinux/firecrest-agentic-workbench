# Local FirecREST v2 environment

`make env-up` clones `eth-cscs/firecrest-v2` into
`~/Sviluppo/firecrest-v2-upstream` (or `$F7T_V2_DIR`) and starts its official
self-contained compose stack: FirecREST, dummy Slurm, and Keycloak. It does
not start the whole workbench, PBS, or MinIO.

## Pinned version: FirecREST 2.6.1

Tested against FirecREST **2.6.1**. The clone is checked out at the tag
`F7T_V2_REF` (default `2.6.1`), not the default branch.

- Source tag `2.6.1` → commit `e6b5a418c7aa1f9f5bacea413883fc8a1f8eeb7c`
  (`git ls-remote --tags https://github.com/eth-cscs/firecrest-v2.git`).
- Image `ghcr.io/eth-cscs/firecrest-v2-demo:2.6.1` → index digest
  `sha256:ed12f77ea5f21964805562743f02e22e810fe742246826a01111f411b27f494c`
  (amd64 `sha256:119044bdb486d24dd5abfc2f00450bcc5abd9096c17846d70e0d8480d346750f`,
  arm64 `sha256:699cf4851e61ba8474fbd31d59d4451d2483e2a975e55e67dcef7cdd03c5bcc8`).
- Client: `pyfirecrest==3.10.0`, unchanged.

Changes from 2.5.x that matter to this repo (from the 2.6.0 release notes, see
upstream releases): job history defaults to 24 h, timeouts now return 504, generic
SSH errors now return 502. `get_job_status` takes an optional `time_window` to look
further back. The 2.6.1 release notes list a SSH timeout fix on `process_setup` and
a minimum JWT TTL of 5 s; this repo does not call `process_setup` directly.

## Start and stop
```bash
make env-up       # first build compiles Slurm and takes several minutes
make env-status   # gateway :8000 returns 401; Keycloak :18080 returns 200
make env-down     # stops containers, preserves them for the next env-up
```

### Only the v2 stack, via `workbench-up.sh --only-v2`

`scripts/workbench-up.sh --only-v2` brings up **only** the FirecREST v2 services
(`firecrest`, `slurm`, `keycloak`, `keycloak-create-user`) from the clone at
`FIRECREST_V2_DIR`, with the versioned override in `docs/patches/`. It does not start
the v1 demo stack, MetaMCP or DocMind, and it does not read `~/.hermes/.env` or the
OmniRoute settings. It requires `FIRECREST_V2_DIR` and fails with an explicit message
if it is missing.

```bash
FIRECREST_V2_DIR=$HOME/Sviluppo/firecrest-demo/firecrest-v2 \
  ~/Sviluppo/firecrest-agentic-workbench/scripts/workbench-up.sh --only-v2
```

The no-argument form (`workbench-up.sh`) keeps its previous behaviour and still starts
all the stacks. `--status` is unchanged.

The systemd unit `systemd/firecrest-demo-v2.service` runs this command. It is a system
unit with `User=master` and `SupplementaryGroups=docker`, and it is not enabled by the
repository: installing and enabling it is an operator step with `sudo`. Stop the stack
with `docker compose down` in the clone (without `-v`); stopping the unit does not do it.

The override maps Keycloak to `http://localhost:18080` because this workstation's
Multica backend uses 8080. It keeps Docker builds amd64-only and never edits upstream
tracked content. `env-up` does adjust host-local key permissions and, under SELinux,
bind-mount labels (`container_file_t`; see `docs/ENVIRONMENT.md`).

### The MCP server as a system unit (`systemd/firecrest-mcp-v2.service`)

The FirecREST v2 MCP server runs as a system unit on the VPS, separate from the
workstation's user unit `systemd/firecrest-mcp.service`, which is not changed. It binds
`127.0.0.1:8765` only; the server has no authentication of its own, so it must never be
bound to `0.0.0.0`. Public access goes through a Cloudflare tunnel protected by Cloudflare
Access (see below).

The unit sets `User=master`, `FIRECREST_API_VERSION=v2`, and absolute log paths under
`firecrest-mcp/logs/` (`FIRECREST_V2_LOG_DIR`, `FCAGENT_AUDIT_PATH`), which are the only
writable locations. Secrets stay in `firecrest-mcp/.env` (mode 600, gitignored).

#### Public hostname behind the tunnel

Through the tunnel, requests arrive with `Host:` set to the public name, not `127.0.0.1`.
The MCP SDK enables DNS-rebinding protection by itself only for a localhost bind, and then
allows only localhost values, so it would answer `421`. The server therefore reads the
public hostname from `.env`:

```dotenv
FIRECREST_MCP_PUBLIC_HOST=mcp.example.org   # bare hostname, set by the owner
```

When it is set, the allowlist adds that host (and `https://<host>` as Origin) to the
localhost entries. When it is not set, the SDK default is used unchanged. The variable is
rejected if it is a URL, contains a slash or a space, or is `*` or `localhost`.

Checked locally: `Host: 127.0.0.1:<port>` and the public host return 200, an unknown host
returns 421 (end-to-end test with the installed `mcp` SDK, nothing exposed).

## Configure and smoke-test

Copy `firecrest-mcp/.env.example` to gitignored `firecrest-mcp/.env`, then set
`FIRECREST_V2_CLIENT_SECRET` from the local demo's upstream documentation; do not
commit it or use a production credential. Keep:

```dotenv
FIRECREST_V2_BASE_URL=http://localhost:8000
FIRECREST_V2_TOKEN_URL=http://localhost:18080/auth/realms/kcrealm/protocol/openid-connect/token
FIRECREST_V2_CLIENT_ID=firecrest-test-client
FIRECREST_V2_SYSTEM=cluster-slurm-api
```

```bash
python -m pip install pyfirecrest==3.10.0
python scripts/smoke_local.py
```

The test obtains a token, lists systems, submits `hello world`, waits for `COMPLETED`/exit
zero, then reads `slurm-<jobid>.out`. The `-api` system cannot expose job metadata in its REST
scheduler mode, so it reads Slurm's default output.
