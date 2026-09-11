# Prompt 04 — DocMind reconnaissance findings

**Status:** recon only; prompt 04 is in progress
**Date:** 2026-09-11
**Upstream:** `BjornMelin/docmind-ai-llm` @ `d32fb3c` (2026-07-18)

These are findings verified against the real repository and a running instance, not
readings of our own prose. They are recorded now because several of them contradict
claims this project makes elsewhere, and one of them breaks the prompt's own
instructions.

---

## 1. The prompt's model-pull command cannot work as written

`prompts/04-docmind-corpus.md` step 2 instructs:

```bash
docker compose exec ollama ollama pull qwen3:4b-instruct
```

Run against the upstream compose, this fails:

```
pulling manifest
Error: pull model manifest: Get "https://registry.ollama.ai/v2/library/qwen3/manifests/4b-instruct":
  dial tcp: lookup registry.ollama.ai on 127.0.0.11:53: server misbehaving
```

**Cause.** Upstream's `docker-compose.yml` declares two networks and marks the one
Ollama lives on as internal:

```yaml
networks:
  frontend:
  backend:
    internal: true          # <-- no egress
```

`ollama` is attached only to `backend`; the `app` service gets `frontend` as well.
An `internal: true` network has no route off the host, so the Ollama container cannot
resolve or reach `registry.ollama.ai`. Container-to-container traffic on `backend`
still works, which is why DocMind itself would talk to Ollama fine — only the *pull*
is impossible.

**Not a one-off.** A plain `getent hosts registry.ollama.ai` inside that container also
fails, so this is a routing property, not a transient registry problem.

**Ways through, in order of how much they change the upstream design:**

1. Give `ollama` a second network that is not internal — a three-line compose override
   adding `frontend` to the `ollama` service. Keeps upstream's architecture (DocMind
   owns its inference backend) and makes the stack self-contained.
2. Point `DOCMIND_OLLAMA_BASE_URL` at a host Ollama instead, and stop running the
   containerised one. Requires the host to have the model and the app to be allowed to
   reach it.
3. Pre-seed the `ollama` volume out of band.

This matters beyond the demo: anyone following the upstream README to run this on a
fresh machine hits the same wall.

## 2. DocMind ships its own Ollama, so a host install is not what it uses

`docker-compose.yml` pins `ollama/ollama:0.31.2` and sets
`DOCMIND_OLLAMA_BASE_URL: http://ollama:11434`. DocMind therefore runs its **own**
inference backend inside the compose project and never touches an Ollama installed on
the host.

Two consequences:

- Installing Ollama on the host (as this project did, for the "local-first" story) is
  **not** what makes the DocMind demo work. It is a second, unused inference server
  unless option 2 above is chosen deliberately. The host Ollama here is **0.34.0**,
  newer than the pinned **0.31.2**.
- Our `docs/INFERENCE.md` describes the laptop configuration purely in terms of
  environment variables, which reads as though any Ollama will do. It does not mention
  that DocMind brings its own, or at which version.

## 3. Our inference environment variable names are correct — verified

Against upstream's `.env.example`:

| Name in `docs/INFERENCE.md` | Exists upstream? |
|---|---|
| `DOCMIND_LLM_BACKEND` | ✅ `ollama \| openai_compatible \| vllm \| lmstudio \| llamacpp` |
| `DOCMIND_OLLAMA_BASE_URL` | ✅ |
| `DOCMIND_LLM_REQUEST__MODEL` | ✅ |
| `DOCMIND_OPENAI__BASE_URL` | ✅ |
| `DOCMIND_OPENAI__API_KEY` | ✅ |
| `DOCMIND_SECURITY__ALLOW_REMOTE_ENDPOINTS` | ✅ |

This is the first part of the repository whose technical content has checked out
against reality, and it is worth saying so plainly given the number of things that have
not.

## 4. Three of our own documents disagree about how many variables the CSCS swap needs

The claim appears in the pitch material with three different values:

| Document | Claim |
|---|---|
| `ARCHITECTURE.md` (principle 5) | "no code changes, **one** environment variable" |
| `CSCS-PROPOSAL.md` | "it's **three** environment variables, no code change" |
| `docs/INFERENCE.md` (closing section) | "swaps **three** environment variables" |
| `docs/INFERENCE.md` (the block itself) | lists **five** |

Verified against upstream, the real minimum for the `openai_compatible` swap is
**four** — backend, base URL, API key, model:

```
DOCMIND_LLM_BACKEND=openai_compatible
DOCMIND_OPENAI__BASE_URL=https://api.inference.cscs.ch/v1
DOCMIND_OPENAI__API_KEY=<key>
DOCMIND_LLM_REQUEST__MODEL=apertus-70b
```

`DOCMIND_SECURITY__ALLOW_REMOTE_ENDPOINTS` is a fifth variable that may or may not be
required: upstream's policy allows loopback always, and requires non-loopback hosts to
be allowlisted *and* to resolve to public addresses. `api.inference.cscs.ch` is public,
so it may pass with the default, but this has **not** been tested — no CSCS credentials
have been requested, and `docs/INFERENCE.md` itself commits us to not claiming we have.

**Action:** pick one number and make all three documents say it, then verify against a
real endpoint before the pitch repeats it. A number that differs between the proposal
and the architecture notes is exactly the kind of detail a reviewer spots.

## 5. Smaller things worth having written down

- **The UI is on port 8501** (Streamlit). Not stated anywhere in our documents.
- **Ingestion is UI- or CLI-driven.** DocMind is a Streamlit application; it does not
  expose a query API out of the box. Prompt 04 step 5 asks for it to be registered in
  MetaMCP as `query_docs`, so a wrapper will be needed, as the prompt itself anticipates
  ("if DocMind doesn't natively speak MCP, wrap its query API the same way
  `firecrest-mcp` wraps FirecREST's REST API").
- **Embedding and reranking run on CPU here, and that is the real risk.** The models
  themselves are not a runtime problem: the Dockerfile downloads every default
  retrieval model (`BAAI/bge-m3`, `BAAI/bge-reranker-v2-m3`, the sparse model) and the
  Docling parser bundle **during the build**, bakes them into the image under
  `HF_HUB_CACHE=/app/hf-models`, and then proves offline correctness inside the build
  with `RUN --network=none HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python ...`. So there
  are no surprise downloads during a live demo — the cost is paid once, at build time.
  What remains exposed is CPU inference latency for embedding and reranking, and that is
  the main risk to prompt 04's stated goal of ingesting in under five minutes.
- **`.env` does not reach the container.** Upstream's `app` service uses an inline
  `environment:` block and declares no `env_file`, so values in `.env` only substitute
  into the compose file. Following the README's `cp .env.example .env` and expecting the
  settings to take effect fails silently: the app starts on its defaults. Application
  configuration has to go in a compose override.
- **`DOCMIND_ENABLE_GPU_ACCELERATION=true` is the upstream default.** This host has no
  usable NVIDIA driver, so it must be set false or startup will probe for a GPU that
  cannot work.
- **Embedding and reranking are not small.** `bge-m3` plus `bge-reranker-v2-m3` are
  multi-gigabyte downloads and run on CPU here, which is the main risk to prompt 04's
  stated goal of ingesting in under five minutes.

## 6. What this means for the demo narrative

`docs/INFERENCE.md` ends by committing the project to demo against local Ollama and to
*not* claim untested access to `api.inference.cscs.ch`. That commitment is sound and
should hold — but it is currently undermined from two directions:

- the "one/three environment variables" claim, which is wrong and unverified (§4);
- the implication that laptop inference is whatever Ollama you have, when DocMind pins
  its own version and its container cannot currently pull a model at all (§1, §2).

Neither is fatal to the project. Both are the kind of thing that turns a good live demo
into an awkward question, so they are recorded here before prompt 04 is finished rather
than in a report afterwards.

## 7. Corrections to this document

Kept visible rather than silently edited, because the same mistake could easily be made
again by whoever reads this next.

**Originally written, and wrong:** that upstream's `HF_HUB_OFFLINE=1` /
`TRANSFORMERS_OFFLINE=1` would block first-run downloads of the embedding and reranker
models, presenting as a missing model.

**Actually the case:** there is no first-run download. The `Dockerfile` pulls every
default retrieval model and the Docling parser bundle during the build, bakes them into
the image at `HF_HUB_CACHE=/app/hf-models`, and then proves offline correctness within
the same build:

```dockerfile
RUN --network=none HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python - <<'PY'
```

The offline flags are therefore correct as shipped, and an override that cleared them
would let the application reach for the network against its own design. The local
override was corrected accordingly. The practical consequence is the opposite of the
original claim and better for the demo: **no multi-gigabyte download can surprise us
mid-presentation**, because that cost is paid at image build time.

The error came from reasoning about what `.env.example` implies instead of reading the
`Dockerfile`. The build log made it obvious — a step fetching twelve Hugging Face files
appeared partway through — and the next step was to verify against the file rather than
adjust the theory.

One real consequence remains and is unchanged: embedding and reranking still run on CPU
on this host, and that — not any download — is what threatens prompt 04's
under-five-minute ingestion target.

## 8. There is no supported headless ingestion path

Prompt 04 assumes the corpus can simply be ingested. Establishing how took longer than
expected, and the answer constrains both this prompt and the reproducibility story the
project tells CSCS.

**What exists.** Requirement FR-024, specified in `docs/specs/spec-026-ingestion-api-facade.md`
(status: Implemented), mandates "one canonical programmatic ingestion API for local
filesystem inputs". That API is `src/processing/ingestion_api.py`, and its public contract
is:

```python
collect_paths(root, *, recursive=True, extensions=None) -> list[Path]
load_documents(paths, *, doc_id=None, parsing_overrides=None) -> list[Document]
load_documents_from_inputs(inputs) -> list[Document]
generate_stable_id(file_path) -> str          # doc-<full lowercase sha256>
sanitize_document_metadata(meta, *, source_filename)
clear_ingestion_cache()
```

**What that API does and does not cover.** Read the spec's own summary of ownership: path
collection, stable identifier generation, document loading, metadata sanitation. It is a
**parsing facade**. It turns files into parsed `Document` objects. It does not index them.

**Where the rest lives.** Getting those documents into Qdrant and activating them is
handled by `src/ui/ingest_adapter.py` (`ingest_inputs`) plus a snapshot-transaction
sequence orchestrated inside `src/pages/02_documents.py`
(`_start_ingestion_job`: `begin_snapshot` → `_physical_collection_names(workspace)` →
`ingest_inputs` → manifest writes → `finalize_snapshot`). Both call sites are in the UI
layer — `src/ui/` and `src/pages/` — which is a deliberate boundary, not an accident.

**Consequence.** There is no CLI, no `[project.scripts]` entry point, and no documented
headless ingestion. The only supported end-to-end path is the Documents page in the
Streamlit UI. Something calling itself an integration must either drive that UI or
reimplement the snapshot transaction, including the physical collection naming, which is
derived from the snapshot workspace directory name:

```python
build_id = "".join(c for c in workspace.name.removeprefix("_tmp-") if c.isalnum())
```

That is replicable, but it means depending on an internal naming rule that upstream has not
committed to.

**Why this matters beyond prompt 04.** The project's pitch is that the build is
reproducible by anyone at CSCS. For the FirecREST side that holds — everything is a script
in this repository. For the DocMind side, corpus ingestion is a sequence of clicks unless
this is solved. That asymmetry is worth naming in the pitch rather than discovering live.

It also has a direct bearing on prompt 08: the failure-scenario demo requires a job log to
be ingested so DocMind can diagnose from it. If ingestion is click-driven, that step is
manual on demo day, and the rehearsed fallback transcript becomes more important, not less.

## 9. Two practical constraints found while actually ingesting

### 9.1 Two thirds of the required corpus has an unsupported extension

`src/processing/ingestion_api.py` accepts only:

```python
_DEFAULT_EXTENSIONS: set[str] = {".pdf", ".txt", ".md", ".markdown", ".rst"}
```

Prompt 04 asks for three corpora, and two of them arrive in formats DocMind will not
load:

| Required corpus | Natural extension | Accepted? |
|---|---|---|
| the hot cache markdown | `.md` | yes |
| the FirecREST OpenAPI spec | `.yaml` | **no** |
| job logs from `firecrest-mcp/logs/` | `.log` | **no** |

Nothing errors. The files are simply not ingested, and the index looks built. The corpus
has to be staged as `.txt` (or converted) before upload. Worth knowing for prompt 08,
where a job log is the entire point of the exercise.

### 9.2 `AppTest` cannot complete an asynchronous ingestion on its own

Streamlit's `AppTest` is the right way to drive this UI without a browser: it exposes the
real widgets (`file_uploader` labelled *Add files*, buttons *Ingest* and *Rebuild*, and a
*Build GraphRAG (beta)* checkbox that prompt 04 requires to stay off). The browser tool
cannot be used at all here — it refuses private and internal addresses, so
`localhost:8501` is unreachable from it.

But `AppTest` executes the app script and returns. It does not keep the process's
**JobManager** alive, and DocMind's ingestion is asynchronous by design (ADR-052,
process-owned job manager). The first attempt therefore looked like this:

```
clicking Ingest ...
  Acquired snapshot lock .lock
  info: Corpus change in progress: save · 0%
  Created snapshot workspace _tmp-dfd4dc6b3c44450db66df01cd853bae3
  LLM configured via factory: provider=ollama model=qwen3:4b-instruct
  WARNING Could not configure embeddings (EmbeddingModelInitializationError)
  Released snapshot lock .lock
```

and left `/app/data/uploads` **empty** — the upload never persisted.

The embedding warning is a red herring and worth calling out as such: calling
`_configure_embeddings()` directly succeeds, loads 391 weights and reports
`is_embedding_ready() == True`. The failure only appears when the job is torn down
mid-flight as the AppTest script exits. Chasing that warning first would send you into the
embedding stack for no reason; the real signal was the empty uploads directory.

Workaround being tried: keep the driving process alive and re-run the app script in a loop
so the process-owned job manager has time to work while the UI reflects progress.

