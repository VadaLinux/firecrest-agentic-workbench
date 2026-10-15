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

## Target system allowlist

`config/limits.yaml` must define `allowed_systems`, a non-empty list of the FirecREST
system names a job may target (for example `cluster-slurm-api`). It is **mandatory**:
a limits file without it, including a custom `FCAGENT_LIMITS_FILE`, makes the server
fail to start.

- `prepare_job` refuses a system that is not in the list (`unknown_system`) before any
  record exists, and the refusal is audited. `system` is optional: when omitted the
  default is used.
- The default is the one the active FirecREST client is configured with:
  `FIRECREST_V2_SYSTEM` (v2) or `FIRECREST_SYSTEM` (v1, client default `cluster`). It
  **must be in `allowed_systems`**, otherwise the server refuses to start or to create
  its client.
- `list_templates` returns `systems: {allowed, default}`. The summary shown to the
  approver and the prepared record always carry the resolved system.
- If a submit fails after the approval was used (`approval_consumed: true`), the same
  `prepared_id` can never be reused: prepare again and ask for a new approval.

## Working directory allowlist

`config/limits.yaml` must also define `allowed_working_directories`, a non-empty list
of **normalized absolute** path prefixes (not `/`, no `.`/`..`/`//`, no trailing slash).
It is **mandatory**, like `allowed_systems`: a limits file without it, including a
custom `FCAGENT_LIMITS_FILE`, makes the server fail to start.

- A job's `working_directory` must be normalized (relative paths, `.`/`..` components,
  `//`, a trailing slash and values over 512 characters are refused, never rewritten)
  and inside an allowed prefix, compared by path component (`/home/fireuser/x` is
  inside `/home/fireuser`, `/home/fireuser2` is not). Otherwise `prepare_job` refuses
  it (`working_directory_invalid` / `working_directory_not_allowed`) before any record
  exists; the refusal is audited and the `next_step` lists the allowed prefixes without
  repeating the input. `rerun_from_provenance` applies the same rule.
- `working_directory` is optional in `prepare_job` only if
  `FIRECREST_V2_WORKING_DIRECTORY` is configured for the client. If it is not set there
  is no default (`list_templates` reports `working_directory.default: null`) and the
  caller must pass one. A configured default must be inside the allowlist, otherwise
  the server refuses to start or to create its client.
- `list_templates` returns `working_directory: {allowed, default}`.

Other refusals now state the configured limit in `next_step` (`max_nodes`,
`max_time_minutes`, the allowed partitions). Prepared ids never start with `-`, so
`fcagent approve <id>` cannot mistake them for an option.

## Operator procedure (not executed by this repository)

Nothing below is run by tests, CI or the PR; Gabriele (or the operator) applies it.

1. Create the state dir, owned by the identity that runs the MCP service, outside the
   checkout and the agent workspace (example `/var/lib/fcagent`, mode 0750).
2. Generate the key pair as the approver: `fcagent keygen --private <approver-only path>
   --public /etc/fcagent/approval.pub`. The public file must be a regular file not
   writable by group/other.
3. Install the drop-in from `systemd/firecrest-mcp-v2.approval.conf.example` as
   `/etc/systemd/system/firecrest-mcp-v2.service.d/approval.conf` (sets
   `FCAGENT_STATE_DIR`, `FCAGENT_APPROVAL_PUBLIC_KEY`, `ReadWritePaths`), then
   `systemctl daemon-reload` and restart the service.
4. The approver runs the CLI with the same `FCAGENT_STATE_DIR`,
   `FCAGENT_AUDIT_PATH` and `FCAGENT_APPROVAL_PRIVATE_KEY` (path) in the shell.

`FCAGENT_STATE_DIR` and `FCAGENT_APPROVAL_PUBLIC_KEY` must be absolute: a relative
value is refused at startup. Without the drop-in the default `<repo>/runs` is not
writable by the hardened service, so the gate fails closed (no submission).

## Audit trail

`AuditLog` (JSONL, allowlisted fields only) records `prepare_job`, `approve_job`,
`reject_job`, `submit_job` (submitted / denied / error / expired) with actor,
timestamp, `prepared_id`, template, parameter hash and, for approvals, the approver.
Reject takes a controlled reason (`--reason not_intended|wrong_parameters|too_expensive|policy|other`);
free text is not accepted, so no secret can enter the audit. Parameters, scripts and
keys are never written.

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

The card is kept up to date for jobs submitted through this server only:
`get_job_status` refreshes `state`, `exit_code` and the start/end times, and
`download_file(path, job_id=<id>)` records the path and SHA-256 of the downloaded
output. Jobs not submitted here have no card and keep working unchanged.

`get_provenance(job_id)` returns the card. `rerun_from_provenance(job_id)` re-renders
the card's template and parameters, refuses if the rendered-script or template hash
differs from the card, and otherwise returns a new `prepared_id` that **still needs
a fresh `fcagent approve`**.
