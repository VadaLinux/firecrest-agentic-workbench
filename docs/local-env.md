# Local FirecREST v2 environment

`make env-up` clones `eth-cscs/firecrest-v2` into
`~/Sviluppo/firecrest-v2-upstream` (or `$F7T_V2_DIR`) and starts its official
self-contained compose stack: FirecREST, dummy Slurm, and Keycloak. It does
not start the whole workbench, PBS, or MinIO.

## Start and stop
```bash
make env-up       # first build compiles Slurm and takes several minutes
make env-status   # gateway :8000 returns 401; Keycloak :18080 returns 200
make env-down     # stops containers, preserves them for the next env-up
```

The override maps Keycloak to `http://localhost:18080` because this workstation's
Multica backend uses 8080. It keeps Docker builds amd64-only and the upstream clone
unchanged. Under SELinux, `env-up` relabels only bind-mounted inputs as
`container_file_t` (see `docs/ENVIRONMENT.md`).

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
