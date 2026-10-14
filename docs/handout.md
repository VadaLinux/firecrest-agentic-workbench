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
("run the matrix benchmark on 4 nodes and tell me when it's done"); the agent does the rest.

## Architecture

Request in natural language → agent (Hermes) → MCP gateway → **FirecREST MCP wrapper** →
FirecREST → Slurm. Every FirecREST call goes through a single client module; no other
component talks to the API.

## What is new

- **Guardrailed templates:** jobs are launched from vetted templates with enforced limits.
- **Human approval** before anything is submitted.
- **Guardrail refusals:** dangerous or out-of-policy requests are blocked, and nothing is submitted.
- **Reproducibility card:** inputs, template, parameters and output hashes are recorded;
  a job can be relaunched from the card and its outputs verified by hash.

## Try it

Everything runs against the official FirecREST demo stack; no real credentials are needed.

Repository: <https://github.com/VadaLinux/firecrest-agentic-workbench>

![Repository QR code](assets/qr-repo.png){ width=3cm }

*FirecREST Hackathon, CSCS / ETH Zurich, 3 November 2026.*
