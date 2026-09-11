# Prompt 04 — DocMind corpus

Status: **complete**, except for the caveat in §7. Date: 2026-09-11.
Reconnaissance (stack, image, architecture) is in `04-docmind-recon.md`; this file
records what was built, what was verified, and what it cost.

## 1. What the prompt asked for

Ingest FirecREST's own documentation — the OpenAPI spec and the hot cache — into
DocMind, with GraphRAG off, then verify retrieval works well enough to answer three
questions with citable sources, and expose that as a `query_docs` tool in the MetaMCP
namespace.

## 2. The problem: there is no headless ingestion path

DocMind has no supported way to ingest a corpus without clicking. SPEC-026 / FR-024
provides only a *parsing* facade (`src/processing/ingestion_api.py`); indexing into
Qdrant and activating the result live in the UI layer
(`src/ui/ingest_adapter.py`, `src/pages/02_documents.py`), by design. There is no CLI,
no `[project.scripts]` entry point, and no HTTP endpoint.

Two approaches were tried:

**Streamlit's `AppTest` harness — insufficient.** It exposes the real widgets (the
`Add files` uploader, the `Ingest` button, and the `Build GraphRAG` checkbox that the
prompt requires to be off), so it can be driven headlessly without a browser. But it
executes the app script and returns without keeping the process-owned `JobManager`
alive (ADR-052), and ingestion is asynchronous by design. The job started, was torn
down mid-flight, and left `/app/data/uploads` empty. A polling loop that re-ran the
script did not help: each run creates a fresh session and loses both the upload and
the click. 70 polls, `uploads` never left 0.

**Direct call into the page's own helpers — works.** `scripts/docmind_ingest.py`
imports `src/pages/02_documents.py` with `importlib` under a synthetic module name
(safe: its `main()` is guarded by `if __name__ == "__main__"`) and calls the page's
own `_existing_corpus_inputs`, `_IngestTransaction`, `_physical_collection_names`,
`_plan_pending_inputs` and `_activate_ingest_generation`. Nothing is reimplemented, so
the snapshot activation logic and the physical collection naming rule are DocMind's,
not a copy that can drift.

The trade-off is explicit in the script: it depends on private names in a UI page. If
upstream renames them the script **fails loudly**, naming the missing symbol and
telling the operator to ingest manually from the UI — it does not break silently.

## 3. Two constraints of the ingestion API, both found by hitting them

**Unsupported extensions are skipped silently.** The API accepts only
`.pdf .txt .md .markdown .rst`. Two of the three corpora this prompt names — the
OpenAPI spec (`.yaml`) and job logs (`.log`) — are therefore not ingested at all, with
no error and no warning; the index simply looks built. The staging step renames them
to `.txt`. This is directly relevant to prompt 08, where a job log is the whole point
of the exercise.

**Duplicate content is a hard error.** Document ids are `doc-<sha256 of content>`, and
`require_unique_document_ids` rejects the *entire batch* — "Duplicate document_id in
ingestion batch" — if two files are byte-identical. Our first corpus had three job logs
all reading `hello-from-mcp`, which failed the whole run. The script deduplicates by
content hash, both in the source corpus and across anything already sitting in
`uploads` from an earlier attempt.

## 4. Ingesting: result and cost

4 files, ~171 KB (the hot cache, the OpenAPI spec, two distinct job logs) → **33
chunks**, snapshot `20260911T171839-f54356c0`, `CURRENT` updated, `committed: True`.

**771 s total (~12 min 51 s)**, of which `ingest_inputs` was 743 s. Almost all of it is
prefill and embedding on CPU:

```
17:06:16  begin snapshot
17:06:33  spaCy fallback      →  17 s
17:18:39  hybrid collection   → 726 s   ← the cost
17:18:39  snapshot finalized  → 0.3 s
```

Throughput ≈ 22 s per chunk. This scales linearly with corpus size: 1 MB would be over
an hour. On this hardware the corpus must be ingested **before** the demo, not during
it.

## 5. Retrieval verification

Three questions with answers verifiable in the corpus, run through
`scripts/docmind_query.py`:

| # | Question | Answer | Citations |
|---|---|---|---|
| 1 | How do I obtain an authentication token from Keycloak? | Correct — token URL, `grant_type=client_credentials`, `client_id` | 3 |
| 2 | Which HTTP header selects the machine? | Correct — `X-Machine-Name` | 3 |
| 3 | How do I submit a job to FirecREST? | Correct — `POST /compute/jobs/upload`, multipart, `Authorization`, `X-Machine-Name` | 3 |

Citations resolve to real provenance: `source_filename`, `page_number`,
`document_id`, `source_hash`.

Note on the citation field: the intuitive metadata keys (`file_name`, `filename`,
`source`, `file_path`) **do not exist**. The key is `source_filename`. Guessing would
have produced silently empty citations.

## 6. The router does not synthesise

`build_router_engine` builds its vector tool with `ResponseMode.NO_TEXT`
(`src/retrieval/router_factory.py`). `router.query()` therefore returns retrieved
nodes and an **empty** `.response`, by design; synthesis happens one layer up, in the
agent (`src/agents/tools/retrieval.py` consumes the nodes as "documents" and the
`MultiAgentCoordinator` composes the answer).

This cost real time to establish, and it is worth recording because the symptom is
misleading: reading `.response` yields `""` and the retrieval stack looks broken when
it is working exactly as designed. `docmind_query.py` synthesises explicitly, which is
what the agent layer does and what the prompt's three-field contract requires.

## 7. Configuration: four settings, one of which silently caps another

DocMind's defaults assume a GPU. On this CPU-only host the Ollama model runner crashed
outright on an ~8200-token prompt:

```
llama-server chat error: Post .../v1/chat/completions: EOF
[GIN] 500 | 3m6s | POST "/api/chat"
```

The same model answered a small prompt in 6.4 s, which isolated the cause to prompt
size rather than a broken model. The settings applied (in
`docs/patches/docmind-docker-compose.override.yml`, with the reasoning inline):

| Setting | Value | Why |
|---|---|---|
| `LLM_REQUEST__CONTEXT_WINDOW` | 16384 | 131072 assumes a GPU; the runner crashed |
| `LLM_REQUEST_TIMEOUT_SECONDS` | 600 | — |
| `AGENTS__DECISION_TIMEOUT` | 1000 | **required**, see below |
| `LLM_REQUEST__MAX_OUTPUT_TOKENS` | 512 | bounds generation |
| `RETRIEVAL__TOP_K` | 3 | prefill dominates; 10 chunks ≈ 8200 tokens |
| `RETRIEVAL__USE_RERANKING` | false | it never once succeeded — see §8 |

The trap: `src/config/llm_factory.py` computes

```python
timeout_s = min(settings.llm_request_timeout_seconds, settings.agents.decision_timeout)
```

so raising only the first has **no effect and reports no error** — the request simply
keeps timing out, which reads as "the timeout is still too low" rather than "a second
setting caps it". Defaults are 120 and 200, giving an effective 120 s.

Measured effect of cutting `top_k` and disabling reranking: synthesis fell from
613–651 s (still timing out) to **92.9 s**, and to **43.9 s** over MetaMCP once the
model stays warm.

## 8. The reranker never runs

Every query logged:

```
WARNING reranking:_postprocess_nodes - Text rerank timeout; fail-open
WARNING reranking:_emit_total_timeout - Total rerank budget exhausted; fail-open
```

The BGE cross-encoder `BAAI/bge-reranker-v2-m3` never completed within its budget on
this CPU, so the system **failed open and discarded its output on every query**. It was
consuming heavy CPU in competition with the LLM for a result that was then thrown away.
Disabling it therefore does not reduce answer quality — the output never contained
reranked results. The scores reported by `query_docs` are raw vector similarity.

This is a silent degradation by design ("fail open on timeouts" is an explicit
DocMind policy): the system logs a warning and proceeds, so nothing signals to a
caller that reranking was skipped.

## 9. `query_docs` in the MetaMCP namespace

DocMind's image ships no MCP package (`mcp` and `fastmcp` both absent), so a derived
image adds it:

```
docker build -f docs/patches/docmind-mcp.Dockerfile -t docmind-mcp:dev scripts/
```

Three packaging assumptions failed on the way and are recorded in the Dockerfile:
`python -m pip` → no pip in the venv; `uv` → not present in the final image; `pip` →
exists but at `/usr/local/bin/pip`, outside the venv. The working route is
`ensurepip` into the venv, then install with that interpreter.

The service runs `scripts/docmind_mcp.py` (transport `http`, port 8770) with the
scripts **bind-mounted rather than baked in** — `docker compose up -d` recreates
containers, and anything copied in goes stale exactly while it is being iterated on.

The aggregated MetaMCP endpoint now exposes exactly six tools:

```
docmind__query_docs
firecrest__download_file
firecrest__get_job_log
firecrest__get_job_status
firecrest__list_files
firecrest__submit_job
```

End-to-end through MetaMCP, `query_docs` returns the three required fields and a
correct answer in **43.9 s**:

```
answer   : The HTTP header that selects the machine in FirecREST is `X-Machine-Name`.
citations: 01-hot-cache.md p.1 (0.6279), 02-firecrest-api-spec.txt p.1 (0.5656, 0.5541)
```

### A MetaMCP limit worth knowing

MetaMCP caps every tool call at **60 s** (`MCP_TIMEOUT` /
`MCP_MAX_TOTAL_TIMEOUT`, both defaulting to `60000` ms, not surfaced in the UI and not
mentioned in its README). With a CPU-bound backend this makes the tool unreachable
through the aggregator while the backend works perfectly: the tool responds, MetaMCP
receives it, and the caller still sees a timeout — which naturally reads as "the RAG
tool is broken". Raised to 600000 ms. On CSCS GPU hardware the default would be
correct; the defect only appears where inference is slow, which is precisely our case.

## 10. A claim I made and had to retract

The first synthesised answer to question 3 ended with:

> "The response returns a task creation object with `task_id` and `task_url`, which
> must be used to retrieve the job status via `GET /tasks/{task_id}`."

I reported this to the team as a hallucination — a plausible endpoint the model
invented — and described it as the strongest evidence for the verified hot-cache fast
path.

**It was not a hallucination.** `GET /tasks/{task_id}` is documented in our own
`docs/hot-cache.md` (lines 33, 136, 296–297). The verified flow is two steps: read
`GET /compute/acct?jobs=<id>`, then unwrap the asynchronous task through
`GET /tasks/{id}`. The model cited the second step of a flow we had ourselves
verified and written down.

The error was mine. I checked the model's output against my recollection of the
verified endpoint rather than against the corpus — the same failure mode I was
attributing to the model: asserting something plausible without verifying it. The
lesson is not "RAG hallucinates" but "verification claims need to be checked against
their source too, including the ones that feel obvious". No part of this report rests
on the retracted claim.

## 11. Definition of done

- [x] Corpus ingested: hot cache, OpenAPI spec, job logs — 33 chunks, snapshot active
- [x] GraphRAG off (`use_graphrag=False`, `kg_present=False` in the router)
- [x] Three queries answered correctly with citations
- [x] `query_docs` returns `answer`, `citations`, `retrieved_chunks`
- [x] Registered in the MetaMCP namespace; six tools on the aggregated endpoint
- [x] `/status/*` and `/tasks/*` exception documented in the hot cache

## 12. Open items

- **The `.yaml`/`.log` extension gap is worked around, not fixed.** Our staging step
  renames; anything else feeding DocMind (prompt 08's failing job log included) has to
  know the rule.
- **spaCy model missing.** The image logs
  `spaCy model load failed (en_core_web_sm) [OSError]: falling back to blank('en')`, so
  NLP-dependent features run on an empty pipeline. Ingestion succeeds; whether it
  affects retrieval quality was not measured. Note that the ingested node metadata
  records `"model": "en_core_web_sm"` regardless — provenance that does not reflect
  what actually ran.
- **`AppTest` remains unusable for async jobs.** If DocMind ever exposes a headless
  ingestion entry point, `docmind_ingest.py` should be deleted in favour of it.
- **CPU timings are not demo timings.** Every number here is this laptop. GPU or the
  CSCS Inference API would change them by orders of magnitude, which is itself the
  argument for the architecture.
