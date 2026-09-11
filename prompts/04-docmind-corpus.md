# Prompt 04 — Ingest the FirecREST corpus into DocMind

Your task:

1. Clone `https://github.com/BjornMelin/docmind-ai-llm` as a sibling directory and bring it up via `docker compose up --build -d` (CPU profile; see `docs/INFERENCE.md` in this repo for the config that points it at Ollama for the laptop demo).
2. Pull a small model for the laptop demo: `docker compose exec ollama ollama pull qwen3:4b-instruct`.
3. Ingest, in this priority order:
   a. `docs/hot-cache.md` from this repo (from prompt 01) — this should end up highly retrievable, it's the "fast path."
   b. the FirecREST v2 OpenAPI spec and getting-started docs.
   c. once prompt 02's wrapper has run a few jobs, the contents of `firecrest-mcp/logs/` (job logs) — re-run ingestion after the failure-scenario demo (prompt 08) is rehearsed, so the failing job's log is actually in the index.
4. Confirm retrieval quality with 3 manual test queries via DocMind's chat page: one about a job-submission parameter, one about a file-transfer endpoint, one asking "why did job X fail" once a failed job's log has been ingested.
5. Do not enable GraphRAG or the multimodal image pipeline — out of scope for the demo, adds ingestion time without a payoff here.

## Definition of done

- DocMind's Chat page answers all 3 test queries correctly, citing the ingested source.
- Ingestion runs in under 5 minutes on CPU with the small model (if it doesn't, cut the corpus down before the hackathon, not on the day).
- DocMind is registered as a second MCP tool (`query_docs`) inside the `firecrest-workbench` namespace in MetaMCP (see MetaMCP's docs for exposing a Streamlit/API app as an MCP tool — if DocMind doesn't natively speak MCP, wrap its query API the same way `firecrest-mcp` wraps FirecREST's REST API).
