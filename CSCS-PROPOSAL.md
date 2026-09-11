# FirecREST Agentic Workbench — proposal to CSCS

> **Status: participation confirmed.** Event on 3 November 2026, OAT ETH Zürich (rooms S15/S16, 14th floor). This document and the rest of the repo are what we're bringing to build on the day — see `prompts/` for the build order and `README.md` → Status for logistics.

## One sentence

Let a researcher submit, monitor and diagnose HPC jobs through natural language — from a chat app, not a terminal — with the whole stack running on CSCS's own open-model inference service, not a third-party API.

## What we bring to the hackathon

A working, laptop-runnable demo (repo: this one) combining:

- an MCP wrapper over FirecREST v2 (open-source, this repo)
- [Multica](https://github.com/multica-ai/multica) as the orchestration board — agents are assigned work like teammates, with a full execution log and human review gate
- [DocMind](https://github.com/BjornMelin/docmind-ai-llm) as a local RAG layer, so the agent answers from real FirecREST docs and real job logs instead of guessing API parameters
- [MetaMCP](https://github.com/metatool-ai/metamcp) aggregating both behind one authenticated endpoint
- a Telegram channel as a second, equally-first-class interface to the same board (see `docs/TELEGRAM.md`)

Everything is documented as a trail of GitHub issues and the exact prompts used to build each piece (`prompts/`), not just a finished artifact — the point is that the build process itself is reproducible by anyone at CSCS.

## What changes for a CSCS-hosted version

One deployment detail, and it's the one we think matters most to you: **inference runs on CSCS's own Inference API Service, serving Apertus**, not a laptop-local model or an external paid API. See `docs/INFERENCE.md` for the exact config diff — it's three environment variables, no code change, because DocMind already speaks the OpenAI-compatible protocol your inference API exposes.

This means:

- No new infrastructure for CSCS to stand up — the proposal consumes a service you've already launched.
- A governance story consistent with what Apertus is for: EU AI Act-aligned, fully open, auditable — rather than routing researcher queries through an opaque third-party model.
- Everything we demo live is architecturally identical to what we'd deploy — not a "trust us, production is different" hand-wave.

## Why this fits the hackathon's ask specifically

The call asked participants to "bring a workflow, tool, or automation challenge." Ours: **the FirecREST API is easy for a developer, but HPC access is still hard for the actual scientist writing the simulation.** This project is our attempt at closing that gap without asking CSCS to build or maintain anything beyond what already exists (FirecREST, the inference API) — it's glue, made of open-source pieces, documented well enough that a CSCS team could pick it up and extend it after the hackathon ends.

## Team / contact

Gabriele Vadalà — AI Solution Architect & DevOps, CRI.GA.MO. 3 SRLS. Background: 26 years enterprise infrastructure (Linux, cloud, DevOps), recent hands-on specialization in agentic AI, multi-agent orchestration on Kubernetes, and RAG pipelines in production. See `CV_Gabriele_Vadala_AgenticAI.md` for the full profile.
