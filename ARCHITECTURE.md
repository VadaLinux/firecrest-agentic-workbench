# Architecture

## Design principles

1. **The agent never talks to FirecREST directly.** It only ever calls MCP tools. This keeps auth, retries and rate limiting in one place (the FirecREST MCP wrapper), and means the same agent config works whether the backend is the local dummy cluster or a real Alps endpoint.
2. **Retrieval before generation.** Before the agent invents an API parameter, it checks the local "hot cache" (a short curated markdown, see below) and then DocMind's RAG index. This is the single biggest lever against hallucinated FirecREST calls.
3. **Work is assigned, not scripted.** Multica treats the agent as a board member: an issue is filed, the agent picks it up, comments as it works, and a human approves before anything is considered "done." This mirrors how a DevOps team already reviews infrastructure changes — it's not a new mental model for the researcher.
4. **One authenticated endpoint, not N tool integrations.** MetaMCP aggregates the FirecREST tool and the DocMind tool into a single namespace. Adding a third tool later (say, a Slurm-log analyzer) means one more entry in MetaMCP's config, not a new integration in every client.
5. **Local-first by default, swappable for production.** On a laptop, inference runs on Ollama with a small model. In the version proposed to CSCS (see `CSCS-PROPOSAL.md`), the same DocMind and Hermes configuration points at CSCS's own Inference API Service (`https://api.inference.cscs.ch/v1`, OpenAI-compatible, serving Apertus) instead — no code changes, one environment variable.

## Components in detail

### FirecREST MCP wrapper (`firecrest-mcp/`)

A thin Python MCP server built from the [FirecREST v2 OpenAPI spec](https://eth-cscs.github.io/firecrest-v2/) (optionally via [pyFirecREST](https://github.com/eth-cscs/pyfirecrest)). Exposes a small, deliberately minimal tool surface for the demo:

- `submit_job(script, system, account)` → job ID
- `get_job_status(job_id)` → state, timestamps
- `list_files(path)` / `download_file(path)`
- `get_job_log(job_id)` → stdout/stderr, used by the failure-scenario demo

Handles the OAuth2 client-credentials flow (short-lived JWT, 5-minute validity) transparently, so the agent never sees a token.

### MetaMCP

Runs as a single Docker container. Groups the FirecREST tool and the DocMind tool into one namespace, exposes it over Streamable HTTP with an API key. Hermes is configured with exactly one MCP endpoint — MetaMCP — rather than one per tool.

### DocMind

Local-first RAG (LlamaIndex + Qdrant + LangGraph 4-role supervisor: planner, retrieval, synthesis, validator). Ingests:

- the FirecREST v2 OpenAPI spec and getting-started docs
- the curated "hot cache" markdown (see below)
- job logs written by the FirecREST MCP wrapper, so "why did job X fail" is answerable from real data, not speculation

Exposed to the agent as a `query_docs` tool via MetaMCP.

### The "hot cache" (llms.txt-style)

A single curated markdown file (`docs/hot-cache.md`, generated during prep, not checked in raw) listing the 10–15 FirecREST calls and parameter patterns actually used in the demo. The agent checks this before falling back to full RAG retrieval over the whole OpenAPI spec — cuts latency and token cost, and gives a concrete talking point about cost/performance governance.

### Multica

Self-hosted board. An "agent" (Hermes, pointed at the MetaMCP endpoint) is added as a teammate with access scoped to this repo/project. Issues describe HPC tasks in plain language; Hermes picks them up, works on its own runtime (the laptop), and leaves the result in review. See `docs/TELEGRAM.md` for how this is also driven from a phone.

### Hermes

The actual execution runtime. Configured once with the MetaMCP endpoint as its tool source. Everything downstream of "Hermes gets assigned an issue" is the same whether the issue came from the Multica web board or from a Telegram message.

## Data flow for a typical request

```
"Run my analysis script on the GPU partition and tell me when it's done"
  → filed as a Multica issue (web or Telegram)
  → Multica assigns it to Hermes, spawns it on the laptop runtime
  → Hermes checks the hot cache / DocMind for the right submit_job parameters
  → Hermes calls submit_job via MetaMCP → FirecREST MCP wrapper → FirecREST demo stack
  → Hermes polls get_job_status, comments progress back on the Multica issue
  → on completion, downloads the result, attaches it to the issue, requests review
  → Multica notifies the Telegram channel
```

## Security notes

- FirecREST auth (JWT, 5-minute expiry) is handled entirely inside the MCP wrapper; neither Hermes nor MetaMCP ever hold long-lived HPC credentials.
- MetaMCP's own endpoint is protected with an API key (OAuth optional, see MetaMCP docs).
- Multica's security model scopes exactly which agents can run which tools per workspace — the hackathon demo agent should not have access to anything beyond the FirecREST/DocMind namespace.
