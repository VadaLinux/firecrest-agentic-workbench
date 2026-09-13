# Prompt 03 completion report — MetaMCP as the single gateway

**Task:** `prompts/03-metamcp-setup.md`
**Status:** complete, with declared deviations (see §5)
**Date:** 2026-09-11
**Depends on:** prompts 01 (demo stack) and 02 (MCP wrapper)

---

## 1. Objective

Run MetaMCP locally, register the FirecREST MCP server from prompt 02 as a source
server, group it into a `firecrest-workbench` namespace, and host that namespace as a
public Streamable HTTP endpoint protected by an API key — so that prompt 05 can point
Hermes at exactly one MCP endpoint.

## 2. Final state

```
endpoint   http://localhost:12008/metamcp/firecrest-workbench/mcp   (Streamable HTTP)
namespace  firecrest-workbench   (uuid c7ad0d9a-76e6-4461-ac19-b65e3ed8bc1c)
auth       API key in Authorization: Bearer <key>
```

Tools exposed by the aggregated endpoint, and nothing else:

```
firecrest__download_file
firecrest__get_job_log
firecrest__get_job_status
firecrest__list_files
firecrest__submit_job
```

MetaMCP's own administrative tools are disabled on this endpoint. They were switched
on temporarily to perform the configuration and switched off again — leaving them on
would give the prompt 05 agent ~51 tools that reconfigure MetaMCP, which
`ARCHITECTURE.md` rules out.

## 3. Verification against the definition of done

| Requirement | Result |
|---|---|
| MetaMCP container healthy and reachable | ✅ both containers healthy; endpoint reachable, UI on `:12008` |
| `firecrest-workbench` namespace exposes exactly the 5 FirecREST tools | ✅ asserted by `scripts/metamcp_e2e_test.py`, which also fails on unexpected extras |
| A call to `submit_job` through the MetaMCP endpoint succeeds against the demo stack | ✅ job 10, submitted through the endpoint, `COMPLETED` |
| `docs/METAMCP.md` documents endpoint URL, namespace name, key rotation, no real key committed | ✅ and `git check-ignore` confirms `.secrets/` is ignored |

The full lifecycle, driven through MetaMCP rather than the wrapper directly:

```
=== tools exposed by the aggregated endpoint ===
["firecrest__download_file", "firecrest__get_job_log", "firecrest__get_job_status",
 "firecrest__list_files", "firecrest__submit_job"]

=== firecrest__submit_job (through MetaMCP) ===
{"ok": true, "jobid": "10", "system": "cluster", ...}

=== firecrest__get_job_status ===
{"ok": true, "job": {"jobid": "10", "name": "via-metamcp", "state": "COMPLETED",
                     "partition": "part01", "exit_code": "0:0"}}

=== firecrest__get_job_log ===
{"ok": true, "log": {"stdout": "hello-through-metamcp\n", ...}}
```

Authentication was checked in both directions: **401** without an API key, **200** with.

## 4. How the configuration was driven

Headlessly, through MetaMCP's own admin MCP tools (`metamcp-admin__*`), which are
exposed on the same endpoints it serves. No browser, no screenshots, and the whole
configuration is reproducible from `metamcp/.env` plus `scripts/metamcp_admin.py`.

- `scripts/make_metamcp_env.py` — generates `metamcp/.env` from upstream's
  `example.env`, with generated secrets and a declarative bootstrap block.
- `scripts/read_metamcp_api_key.sh` — reads the bootstrapped key out of Postgres into
  a gitignored file, without printing it.
- `scripts/metamcp_admin.py` — admin MCP client: list tools, dump input schemas, call
  anything, and `add-firecrest` to register and attach the wrapper.
- `scripts/metamcp_e2e_test.py` — asserts the tool surface and drives a job end to end.

## 5. Declared deviations from the prompt

1. **MetaMCP is built from source, not pulled.** The prompt says to run the published
   image. That image (`latest` = v2.4.22, revision `32b6e98`, December 2025)
   **predates the `BOOTSTRAP_*` variables entirely** — the compiled backend contains
   zero occurrences of them — so the declarative configuration the prompt's flow
   implies is impossible with it. Chosen with the repo owner; the upstream
   `docker-compose.yml` is left untouched and a `docker-compose.override.yml` supplies
   the local build.
2. **The port is 12008, not 3000.** The prompt's `docker run -p 3000:3000` comes from
   an outdated README snippet; the compose setup in the repository publishes 12008 for
   the frontend and binds the backend to 12009 inside the container. Documented in
   `docs/METAMCP.md` as the prompt allows ("or documented alternate port").
3. **The admin tools had to be enabled by writing the database directly.** The
   bootstrap's `EndpointConfig` supports `enable_auth`, `enable_auth_query` and
   `enable_auth_oauth` but **not** `enable_metamcp_admin_tools`, and there is no admin
   API. Without the admin tools there is no headless way to attach an MCP server to a
   namespace; enabling them requires the admin tools. The deadlock was broken with one
   `UPDATE endpoints SET enable_metamcp_admin_tools = true`, then reverted.
   `docs/METAMCP.md` documents the dance for key rotation.
4. **The prompt's Ollama step was not performed, because it is not needed.** The prompt
   states MetaMCP "needs an OpenAI-compatible key for some internal features". Searching
   the whole backend for `openai` returns **no occurrences**: the requirement is stale.
   Ollama was installed anyway, but for prompt 04 (DocMind), not for MetaMCP.
5. **`metamcp_refresh_namespace_tools` is not used.** It looks like the tool that makes
   MetaMCP enumerate an attached server's tools, but it requires the *caller* to supply
   the discovered tool list — it is the web UI telling the backend what it found.
   `metamcp_update_namespace` with `mcpServerUuids` is what actually attaches a server,
   after which MetaMCP discovers the tools itself.

## 6. Findings — six upstream defects

All six cost real time. Defects 2–4 are carried as commented patches in
`metamcp/Dockerfile`; a copy of the patch is committed at
`docs/patches/metamcp-dockerfile.patch` so it survives the gitignored clone.

1. **The published image is far behind `main`** and lacks the bootstrap feature
   completely. Either publish a matching image or say so prominently.
2. **The Dockerfile hardcodes a pnpm store path containing React's version**
   (`next@15.5.12_react-dom@19.1.2_react@19.1.2__react@19.1.2`). The lockfile resolves
   React **19.2.4**, which changes the directory name, so the `sed` that raises Next's
   proxy timeout from 30 s to 10 min fails with
   `sed: can't read ...: No such file or directory` and the build dies. Replaced with a
   `find` that matches the file rather than the version.
3. **`pnpm install --prod` in the runner stage aborts without `CI=true`**
   (`ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY`), because it always wants to remove the
   dev-dependency-laden `node_modules` copied from the builder. Added `ENV CI=true`.
4. **`pnpm add` after `pnpm install --prod` cannot work, in two different ways.** Plain,
   it dies with `ERR_PNPM_INCLUDED_DEPS_CONFLICT`; with `--prod` it clears the error but
   leaves `drizzle-kit` unlinked from `apps/backend/node_modules/.bin`, so the container
   starts and then fails with `Command "drizzle-kit" not found`. `drizzle-kit` is a
   devDependency of `apps/backend` that `docker-entrypoint.sh` needs at runtime for
   migrations. Promoting it in `package.json` before a *single* install is the way
   through. The step now ends with `test -x apps/backend/node_modules/.bin/drizzle-kit`,
   so this failure mode surfaces during the build instead of at container start.
5. **The runner stage does not copy `pnpm-lock.yaml`** — only `package.json` and
   `pnpm-workspace.yaml`. `pnpm install --prod` therefore resolves production
   dependencies *without the lockfile*, so the shipped image is not the tested
   dependency set. For a demo CSCS is meant to reproduce, that is a genuine defect.
6. **Bootstrap ordering bug, and the nastiest of the six.** Registration controls are
   applied *before* the bootstrap user is created, and the user is created by calling
   the sign-up endpoint those controls gate. Setting
   `BOOTSTRAP_DISABLE_REGISTRATION_UI=true` produces:

   ```
   ✓ Registration controls set: UI=false, SSO=false
   👥 Bootstrapping 1 user(s)...
   ERROR [Better Auth]: Failed to create user — New user registration is currently disabled.
   ⚠️ Skipping API key "firecrest-workbench" because user "admin@firecrest.local" was not found
   ```

   One failed user silently costs the API key, the namespace ownership and the endpoint,
   and the run still reports success. Leave registration open during the first boot.

## 7. Next step

Prompt 04 — DocMind. Two things are already in place for it: Ollama 0.34.0 is installed
and running as a systemd service with `qwen3:4b-instruct` pulled (verified: a chat
completion returned `OK-FIRECREST` in 8.9 s on CPU), and the namespace is ready to
receive a second tool.

Open question carried forward: the v1.16.1-vs-v2 spec discrepancy from the prompt 01
report is still unresolved.

**Update (2026-09-12):** resolved — see `docs/hot-cache-v2.md` and
`docs/reports/05-firecrest-v2-gap.md`.
