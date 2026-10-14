# Human approval and reproducibility cards

## Flow

1. `list_templates` — approved templates and their typed parameters.
2. `prepare_job(template, parameters, system, working_directory)` — validates the
   guardrails (nodes, time, partitions), renders the script, stores a *pending*
   record and returns a `prepared_id`, an expiry and a readable summary
   (template, parameters, system, partition, nodes, time, estimated node-hours).
3. A **human** runs `fcagent approve <prepared_id>` in an interactive terminal. The
   CLI shows the summary, asks for `yes`, and signs the record with an Ed25519
   private key. The approver identity comes from the OS session, not from argv.
   `fcagent reject <prepared_id>` and `fcagent show <prepared_id>` also exist.
4. `submit_job(prepared_id)` — the MCP server holds only the **public** key. Before
   any FirecREST call it verifies the signature over a versioned, canonical
   context (format version, `prepared_id`, status, template/script/parameter
   hashes, system, partition, working directory, account, expiry, approver, UID,
   timestamp), re-renders the script to check the hashes, and consumes the record
   exactly once (`approved → consumed`) under an exclusive file lock with atomic
   writes. A second or concurrent submit is refused.

There is no approval tool in the MCP surface and no runtime flag to disable the
gate. Tests use fixtures only. Refusals return `reason` and a `next_step`.

> **Breaking change.** `submit_job` no longer accepts `script` or `approved_by`.
> Callers must use `prepare_job` → `fcagent approve` → `submit_job(prepared_id)`.

## Configuration (paths, never secret values)

| Variable | Meaning |
|---|---|
| `FCAGENT_STATE_DIR` | absolute state dir (default `<repo>/runs`, gitignored). In production use a directory outside the checkout and the agent workspace, owned by the approving identity. |
| `FCAGENT_APPROVAL_PUBLIC_KEY` | absolute path of the PEM public key (MCP server). |
| `FCAGENT_APPROVAL_PRIVATE_KEY` | absolute path of the PEM private key (approval CLI only). |
| `FCAGENT_APPROVAL_TTL` | approval validity in seconds (default 900). |

Key files must be regular files (no symlinks), absolute paths; the private key
must not be accessible to group/other (0600), the public key not writable by
group/other. Create a pair with `fcagent keygen --private <path> --public <path>`.
The server never receives the private key. Provisioning users, keys or systemd
settings is done by the operator; nothing here is executed automatically.

## Known limits (decision D2 — read before presenting this)

This gate is a **process control**, not a cryptographic guarantee against the agent.
On the current host the agents and the MCP service run as `master`, which has
passwordless `sudo`. An agent with a shell can read the private key or run
`fcagent approve` itself. The control prevents an agent from submitting through the
MCP tools without a recorded, signed human approval step; it does **not** stop a
compromised or malicious agent with shell access. Do not present it otherwise.

**Future work (D1):** run the agents and MCP server as an unprivileged account
without sudo, and keep the private key under a separate approver identity that the
agent runtime cannot reach. Then the signature becomes a technical barrier.

## Reproducibility cards

Every successful `submit_job` writes `<state_dir>/jobs/<jobId>/provenance.json` and
`provenance.md` (gitignored with `runs/`): template and typed parameters, SHA-256
of the template, the **rendered script** and the parameters, system, partition,
FirecREST server version, pyfirecrest version, workbench git commit, job id,
submit/start/end times, final state and exit code, output paths and hashes, the
trace identifiers and who approved. Cards never contain tokens or keys.

`X-Correlation-ID` is the one pyfirecrest sent for the call. `X-Request-ID` is only
recorded when a real response exposed it; otherwise it is `null` with a note, and is
never generated client-side.

`get_provenance(job_id)` returns the card. `rerun_from_provenance(job_id)` re-renders
the card's template and parameters, refuses if the rendered-script or template hash
differs from the card, and otherwise returns a new `prepared_id` that **still needs
a fresh `fcagent approve`**.
