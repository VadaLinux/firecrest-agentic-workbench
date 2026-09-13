# Hermes ↔ MetaMCP (Prompt 05)

Prompt 05's job: point the native Hermes CLI/daemon at the `firecrest-workbench`
MetaMCP namespace (prompt 03) so Hermes can call `submit_job`, `get_job_status`,
`get_job_log`, `list_files`, `download_file` and `query_docs` as ordinary tools,
then prove it end to end.

This is distinct from the separate **Multica Agent** called "Hermes"
(`049c24e2-dbd9-448e-9619-e98c960d071d`) that also exists in this workspace with its
own role contract (multi-channel gateway + FirecREST/DocMind operator). That agent's
own workspace MCP binding is a different, still-open item — see "Known gap" below.
Both configurations are complementary, not redundant.

## What was done

Connected the native Hermes install to MetaMCP:

```bash
hermes mcp add firecrest-workbench --url http://localhost:12008/metamcp/firecrest-workbench/mcp --auth header
```

(API key from `.secrets/metamcp-api-key.txt` in this repo — gitignored, entered
without printing it, per `scripts/read_metamcp_api_key.sh`.)

This required an explicit human go-ahead first, since it reconfigures the tool
source of the same Hermes runtime the human uses directly — see the discussion on
`VDLP-12` before this was run. Confirmation ("fatto, connessione effettuata.") was
given after gabriele.vadala ran the connection himself.

### Verification

```bash
$ hermes mcp list
  Name                 Transport                       Tools   Status
  firecrest-workbench  http://localhost:12008/me...    all     ✓ enabled

$ hermes mcp test firecrest-workbench
Testing 'firecrest-workbench'...
  Transport: HTTP → http://localhost:12008/metamcp/firecrest-workbench/mcp
    Authorization: ***
  ✓ Connected (1704ms)
  ✓ Tools discovered: 6

    docmind__query_docs
    firecrest__list_files
    firecrest__download_file
    firecrest__submit_job
    firecrest__get_job_status
    firecrest__get_job_log
```

All six tools MetaMCP's `firecrest-workbench` namespace exposes are visible and
callable from Hermes — no other MCP server is configured.

## End-to-end test

The exact instruction from this issue was run in a fresh `hermes chat --oneshot`
session (no prior context, no memory of this repo's docs beyond what the session
itself retrieved):

> "Submit the echo-hello job on the demo cluster, wait for it to finish, and tell
> me what it printed. If you're not sure about the right parameters, check the
> docs first."

Full transcript: `docs/e2e-test-transcript.md`. Summary of what happened:

1. Hermes called `docmind__query_docs` (through MetaMCP) to check parameters
   before calling `submit_job` — it did **not** submit from memory.
2. It submitted the echo-hello job via `firecrest__submit_job` (system `cluster`,
   partition `part01`) — job id `14`.
3. It polled `firecrest__get_job_status` until `state: COMPLETED`.
4. It read the output via `firecrest__get_job_log` and reported: `hello`.

Every FirecREST/DocMind call in the transcript is namespaced
`mcp__firecrest_workbench__*` — routed through MetaMCP only. No direct
`firecrest-mcp` or DocMind call bypassing MetaMCP appears anywhere in the session.

## Known gap — not resolved by this issue

The Hermes **Multica-agent**'s own workspace MCP binding to `firecrest-workbench`
(the same server Operator uses) could not be attached from an agent actor:

```
multica agent mcp add 049c24e2-dbd9-448e-9619-e98c960d071d f1105dba-e30a-4154-8cde-aca91eed343e
```

returns a 403 permission error — including retried on Operator's already-bound
server, so this looks like a write gated to a human workspace owner/admin, not a
bug specific to the new agent. This is an open item for gabriele.vadala to run
himself from the Multica web UI; do not assume it is done.
