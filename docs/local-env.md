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

The override maps Keycloak to `http://localhost:18080` because this workstation's
Multica backend uses 8080. It keeps Docker builds amd64-only and never edits upstream
tracked content. `env-up` does adjust host-local key permissions and, under SELinux,
bind-mount labels (`container_file_t`; see `docs/ENVIRONMENT.md`).

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
