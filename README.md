# FirecREST Agentic Workbench

An agentic layer on top of [FirecREST](https://github.com/eth-cscs/firecrest) (CSCS's RESTful gateway to HPC) that lets a researcher drive job submission, monitoring and file transfer on Alps through natural language, managed like a teammate on a board, and reachable from Telegram.

Built for the **FirecREST Hackathon: Automating Your Workflow with FirecREST** (CSCS, ETH Zürich, 3 November 2026).

> Everything in this repo is buildable on a single laptop against CSCS's official [FirecREST demo stack](https://github.com/eth-cscs/firecrest/blob/master/deploy/demo/README.md) (Keycloak + Kong + a dummy Slurm cluster) — no real CSCS credentials are needed to develop and rehearse the demo.

## Why this exists

FirecREST already solves "give HPC a REST API." This project answers the next question: **how does a researcher who isn't an HPC expert actually use it day to day?** The answer we propose: assign it work like you'd assign work to a colleague, get pinged in Telegram when it needs you, and never have to remember curl syntax or API parameters — the agent looks that up itself.

## Architecture

```
Issue / natural-language request
        │
        ▼
   ┌─────────┐   assigns work to   ┌─────────┐   tool calls (MCP)   ┌──────────┐
   │ Multica │ ───────────────────>│ Hermes  │ ────────────────────>│ MetaMCP  │
   │ (board) │<─── status, review ─│ (agent) │                      │(gateway) │
   └────┬────┘                      └─────────┘                      └────┬─────┘
        │                                                        ┌────────┴────────┐
        │ Telegram channel                                       ▼                 ▼
        ▼                                              ┌──────────────────┐ ┌──────────────┐
   researcher's phone                                    │ FirecREST MCP     │ │ DocMind (RAG) │
   (file issues, get                                      │ tool (submit_job, │ │ on FirecREST  │
   notified, approve)                                     │ status, files)    │ │ docs + logs   │
                                                          └─────────┬─────────┘ └───────┬───────┘
                                                                    ▼                    │
                                                          ┌───────────────────┐          │
                                                          │ FirecREST demo    │<─────────┘
                                                          │ stack (Keycloak,  │  "hot cache" of the
                                                          │ Kong, dummy Slurm)│  most-used endpoints
                                                          └───────────────────┘  (docs/INFERENCE.md,
                                                                                  llms.txt-style)
```

See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for the full breakdown of each component and why it's there.

## Components

| Component | Role | Repo |
|---|---|---|
| [Hermes](https://github.com/OpenBMB/Hermes) *(agent CLI)* | Executes the work: turns a request into MCP tool calls | your existing runtime |
| [Multica](https://github.com/multica-ai/multica) | Board where issues get assigned to Hermes like a teammate; Telegram channel | self-hosted (Docker) |
| [MetaMCP](https://github.com/metatool-ai/metamcp) | Aggregates the FirecREST tool + DocMind tool behind one authenticated endpoint | self-hosted (Docker) |
| [DocMind](https://github.com/BjornMelin/docmind-ai-llm) | Local-first RAG over FirecREST docs + job logs, so the agent doesn't hallucinate API parameters | self-hosted |
| `firecrest-mcp/` (this repo) | Thin MCP wrapper over the FirecREST v2 OpenAPI spec | this repo |
| [FirecREST demo stack](https://github.com/eth-cscs/firecrest/tree/master/deploy/demo) | Keycloak + Kong + dummy Slurm cluster, runs fully offline | eth-cscs/firecrest |

## Repo layout

```
.
├── README.md              you are here
├── ARCHITECTURE.md         detailed architecture and data flow
├── AGENTS.md                conventions Hermes/Multica read automatically
├── docs/
│   ├── TELEGRAM.md          how the Telegram channel is wired to Multica
│   └── INFERENCE.md         swapping local Ollama for CSCS's own inference API
├── prompts/                 the exact task prompts given to Hermes to build each piece
│   ├── 00-overview.md
│   ├── 01-firecrest-demo-stack.md
│   ├── 02-firecrest-mcp-wrapper.md
│   ├── 03-metamcp-setup.md
│   ├── 04-docmind-corpus.md
│   ├── 05-hermes-metamcp-integration.md
│   ├── 06-multica-orchestration.md
│   ├── 07-telegram-channel.md
│   └── 08-failure-scenario-demo.md
└── CSCS-PROPOSAL.md         the one-pager version proposed to CSCS (local inference on Apertus)
```

## Quickstart (laptop, offline)

```bash
# 1. FirecREST demo stack
git clone https://github.com/eth-cscs/firecrest.git
cd firecrest/deploy/demo
chmod 400 ../test-build/environment/keys/ca-key ../test-build/environment/keys/user-key
docker compose up -d          # first build compiles Slurm from source, budget time for it

# 2. MetaMCP gateway
docker run -d --name metamcp -p 3000:3000 ghcr.io/metatool-ai/metamcp:latest

# 3. DocMind (local RAG)
git clone https://github.com/BjornMelin/docmind-ai-llm.git
cd docmind-ai-llm && docker compose up --build -d

# 4. this repo's FirecREST MCP wrapper
cd firecrest-mcp && pip install -r requirements.txt && python server.py

# 5. give Hermes the prompts in prompts/, in order
```

Full step-by-step: [`prompts/00-overview.md`](./prompts/00-overview.md).

## Status

- Hackathon application survey: submitted, referencing this repo.
- **Participation confirmed** by CSCS.
- Event: **3 November 2026, 10:30–17:00 CET**, OAT ETH Zürich building, rooms S15/S16, 14th floor (Andreasstrasse 5, 8050 Zürich). Course fee CHF 80 (lunch + coffee breaks included).
- Tutors on the day: Ivano Bonesana, Juan Pablo Dorsch, Eirini Koutsaniti, Francesco Pagnamenta, Elia Palme, Rafael Sarmiento (all CSCS/ETH Zurich).

See [`CSCS-PROPOSAL.md`](./CSCS-PROPOSAL.md) for the pitch text and the [preparation checklist](./prompts/00-overview.md) for what still needs to be built before the 3rd.

## License

MIT for everything in this repo. Upstream components keep their own licenses (Multica: Apache 2.0 + additional conditions; MetaMCP: MIT; DocMind: MIT; FirecREST: BSD-3-Clause).
