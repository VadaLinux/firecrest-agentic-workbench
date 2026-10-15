# Demo script (5 minutes, English, on the VPS)

Request used on stage (to be confirmed by the presenter):
*"run the matrix benchmark on 1 node and tell me when it's done"*.
The demo stack has a single Slurm node, so the benchmark runs on 1 node. The
guardrail scene asks for 4 nodes, which is above `max_nodes` in `config/limits.yaml`.

Prerequisite (operator, once): the demo state dir `/var/lib/fcagent-demo` with the marker file `.fcagent-demo-state` and the systemd drop-in; remove the drop-in after the rehearsal to return to `/var/lib/fcagent`.

Two terminals: **A** (agent / MCP client) and **B** (human approver, `fcagent approve`).

| # | Where | Step |
|---|---|---|
| 1 | A | `list_templates` shows the approved templates (`hello`, `matrix`). |
| 2 | A | `prepare_job` (template `matrix`, 1 node): returns `prepared_id` and a readable summary. Nothing is submitted. |
| 3 | **B** | The human runs `FCAGENT_STATE_DIR=/var/lib/fcagent-demo fcagent approve <prepared_id>` (the same state dir as the service, with the usual `FCAGENT_AUDIT_PATH` and `FCAGENT_APPROVAL_PRIVATE_KEY`; see `systemd/firecrest-mcp-v2.demo-state.conf.example`), reads the summary and types `yes`. Show this on screen. |
| 4 | A | `submit_job(prepared_id)` returns `ok` and `jobid`. |
| 5 | A | `get_job_status` until COMPLETED, then `download_file(path, job_id)`. Download **before** reading the card: the output hash is recorded by the download. |
| 6 | A | `get_provenance(job_id)`: the card holds the SHA-256 of the template and of the rendered script and, after the download, the path and SHA-256 of the downloaded output. |
| 7 | A+B | `rerun_from_provenance(job_id)` re-renders the card and checks **automatically** only the template and script hashes; it returns a **new** `prepared_id` and the human approves again. The output hash of the new job is **not** compared automatically: compare it with the card by hand (download the new output, read the new card). |
| 8 | A | Guardrail: ask for 4 nodes. `prepare_job` is refused with `max_nodes_exceeded`. Nothing is submitted. |

## What to say about the gate (decision D2, quoted from `docs/reproducibility.md`)

> This gate is a **process control**, not a cryptographic guarantee against the agent.
> On the current host the agents and the MCP service run as `master`, which has
> passwordless `sudo`. An agent with a shell can read the private key or run
> `fcagent approve` itself. The control prevents an agent from submitting through the
> MCP tools without a recorded, signed human approval step; it does **not** stop a
> compromised or malicious agent with shell access. Do not present it otherwise.
>
> **Future work (D1):** run the agents and MCP server as an unprivileged account
> without sudo, and keep the private key under a separate approver identity that the
> agent runtime cannot reach. Then the signature becomes a technical barrier.

Do not present the gate as unbreakable.
