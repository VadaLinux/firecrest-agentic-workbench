---
title: "FirecREST Agentic Workbench"
subtitle: "Natural-language HPC workflows, with guardrails and reproducibility"
---

## The problem

FirecREST gives HPC a REST API, but a researcher who is not an HPC expert still has to
remember endpoints, job-script syntax and scheduler options, and has no cheap way to
prove later that a result can be reproduced.

## What we built

An agentic layer on top of FirecREST. The researcher asks in plain language
("run the matrix benchmark on 1 node and tell me when it's done"); the agent does the rest.

## Architecture

Request in natural language → agent (Hermes) → MCP gateway → **FirecREST MCP wrapper** →
FirecREST → Slurm. Every FirecREST call goes through a single client module; no other
component talks to the API.

## What is new

- **Guardrailed templates:** jobs are launched from vetted templates with enforced limits.
- **Human approval** in a separate terminal (`fcagent approve`) before anything is submitted.
- **Guardrail refusals:** dangerous or out-of-policy requests are blocked, and nothing is submitted.
- **Reproducibility card:** template, parameters, script hash and, after download, the
  output hash are recorded. A relaunch from the card checks the template and script
  hashes automatically; the new output hash is compared with the card by hand.

## Limits of the approval gate

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

## Trust by design

> You are interacting with an AI agent; every job needs a human approval.

1. **Human in the loop:** no job is submitted without a signed human approval.
2. **Traceable:** reproducibility cards and an audit log that never stores parameters or scripts; the card records the declared AI client and model.
3. **Minimal data, limited time:** only the approver's OS user name is personal data; retention defaults to 90 days with a dry-run cleanup.
4. **Honest limits:** we adopt GDPR, Swiss FADP and EU AI Act principles by design; this is not legal advice and not a statement of conformity. See `docs/trust-and-compliance.md`.

## Try it

Everything runs against the official FirecREST demo stack; no real credentials are needed.

Repository: <https://github.com/VadaLinux/firecrest-agentic-workbench>

![Repository QR code](assets/qr-repo.png){ width=3cm }

*FirecREST Hackathon, CSCS / ETH Zurich, 3 November 2026.*
