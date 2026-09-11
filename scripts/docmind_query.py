#!/usr/bin/env python3
"""Query DocMind's active snapshot headlessly. Core of the `query_docs` MCP tool.

Companion to docmind_ingest.py. Replicates the retrieval half of what the Chat page
does when it hydrates from the activated snapshot:

    load_manifest -> load_vector_index -> VectorIndexResource
    -> load_property_graph_index -> build_router_engine

Two findings shape this script, both verified against the source rather than assumed:

1. The router does NOT synthesise. `build_router_engine` builds its vector tool with
   `ResponseMode.NO_TEXT` (src/retrieval/router_factory.py), so `router.query()`
   returns retrieved nodes and an empty `.response`. The synthesis lives one layer up,
   in the agent (src/agents/tools/retrieval.py consumes the nodes as "documents" and
   the MultiAgentCoordinator composes the answer). A script that reads `.response`
   gets an empty string and looks broken when it is not. So this script synthesises
   explicitly, which is what the agent layer does and what prompt 04 asks for:

       answer            the synthesised text
       citations         one entry per retrieved chunk: source file, page, score
       retrieved_chunks  the raw chunks with scores

2. Citations come from node metadata key `source_filename`. The intuitive names
   (file_name, filename, source, file_path) are all absent — the metadata carries
   `source_filename`, `document_id`, `source_hash`, `page_number`, `page_id`.
   Guessing here would have produced silent `<unknown>` citations.

Usage (inside the app container):

    python docmind_query.py                       # JSON, default questions
    python docmind_query.py "question"            # JSON, one question
    python docmind_query.py --pretty              # human-readable
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

DEFAULT_QUESTIONS = (
    "How do I obtain an authentication token from FirecREST's Keycloak?",
    "Which HTTP header selects the machine when calling the FirecREST API?",
    "How do I submit a job to FirecREST?",
)

SYNTHESIS_PROMPT = """\
You are answering questions about the FirecREST API using only the context below.
Answer concisely and concretely. Quote exact endpoints, headers and payloads when the
context contains them. If the context does not contain the answer, say so plainly
instead of guessing.

Context:
{context}

Question: {question}

Answer:"""


def current_snapshot_id() -> str:
    """Return the id of the snapshot `CURRENT` points at, or raise if none."""
    from src.config import settings

    pointer = Path(settings.data_dir) / "storage" / "CURRENT"
    if not pointer.is_file():
        raise RuntimeError(f"no active snapshot: {pointer} does not exist")
    return pointer.read_text().strip()


def build_runtime():
    """Load the active snapshot and build the router the Chat page would build."""
    from src.config import settings
    from src.config.integrations import setup_llamaindex
    from src.persistence.snapshot import (
        load_manifest,
        load_property_graph_index,
        load_vector_index,
    )
    from src.retrieval.router_factory import build_router_engine
    from src.ui.vector_session import VectorIndexResource

    storage = Path(settings.data_dir) / "storage"
    pointer = storage / "CURRENT"
    if not pointer.is_file():
        raise RuntimeError(f"no active snapshot: {pointer} does not exist")
    snapshot_id = pointer.read_text().strip()
    snapshot_dir = storage / snapshot_id

    setup_llamaindex(force_llm=True, force_embed=True)

    manifest = load_manifest(snapshot_dir) or {}
    collections = manifest.get("collections")
    if not isinstance(collections, dict):
        raise RuntimeError("active snapshot exposes no collection identities")

    vector_index = load_vector_index(snapshot_dir)
    if vector_index is None:
        raise RuntimeError("active snapshot has no vector index")
    resource = VectorIndexResource.from_vector_store(
        vector_index, getattr(vector_index, "vector_store", None)
    )
    router = build_router_engine(
        vector_index,
        load_property_graph_index(snapshot_dir),
        settings,
        text_collection=collections.get("text"),
        image_collection=collections.get("image"),
    )
    return snapshot_id, resource, router


def retrieve(router, question: str):
    """Retrieve nodes. `.response` is intentionally empty — see the module docstring."""
    response = router.query(question)
    return list(getattr(response, "source_nodes", None) or [])


def node_to_chunk(node_with_score) -> dict:
    """Flatten a NodeWithScore into the shape the tool contract promises."""
    node = getattr(node_with_score, "node", node_with_score)
    metadata = getattr(node, "metadata", None) or {}
    score = getattr(node_with_score, "score", None)
    return {
        "text": (getattr(node, "text", "") or "").strip(),
        "source": metadata.get("source_filename"),
        "page": metadata.get("page_number"),
        "document_id": metadata.get("document_id"),
        "source_hash": metadata.get("source_hash"),
        "score": round(float(score), 4) if isinstance(score, (int, float)) else None,
    }


def synthesise(question: str, chunks: list[dict]) -> str:
    """Compose the answer from the retrieved chunks, the way the agent layer does."""
    from llama_index.core import Settings

    llm = getattr(Settings, "llm", None)
    if llm is None:
        return ""
    context = "\n\n---\n\n".join(
        f"[{chunk['source']} p.{chunk['page']}]\n{chunk['text']}" for chunk in chunks
    )
    prompt = SYNTHESIS_PROMPT.format(context=context, question=question)
    try:
        answer = str(llm.complete(prompt) or "").strip()
    except Exception as exc:  # noqa: BLE001
        return f"<synthesis failed: {type(exc).__name__}: {exc}>"

    if answer:
        return answer

    # The LLM is configured with streaming=True. A non-streaming completion against a
    # streaming LLM can legitimately come back empty, so retry once with streaming off
    # rather than reporting an empty answer as a retrieval failure.
    try:
        previous = getattr(llm, "streaming", None)
        llm.streaming = False
        try:
            answer = str(llm.complete(prompt) or "").strip()
        finally:
            llm.streaming = previous
        if answer:
            return answer + "\n\n[synthesised with streaming disabled]"
    except Exception as exc:  # noqa: BLE001
        return f"<synthesis failed (non-streaming retry): {type(exc).__name__}: {exc}>"
    return "<empty answer: the model returned no text>"


def main() -> int:
    arguments = [a for a in sys.argv[1:] if not a.startswith("--")]
    pretty = "--pretty" in sys.argv
    questions = tuple(arguments) or DEFAULT_QUESTIONS

    started = time.monotonic()
    snapshot_id, resource, router = build_runtime()
    try:
        results = []
        for question in questions:
            query_started = time.monotonic()
            # Replace any None source so the JSON stays readable downstream.
            chunks = [c for c in (node_to_chunk(n) for n in retrieve(router, question))]
            answer = synthesise(question, chunks)
            results.append(
                {
                    "question": question,
                    "answer": answer,
                    "citations": [
                        {
                            "source": c["source"],
                            "page": c["page"],
                            "score": c["score"],
                            "document_id": c["document_id"],
                        }
                        for c in chunks
                    ],
                    "retrieved_chunks": chunks,
                    "elapsed_s": round(time.monotonic() - query_started, 1),
                }
            )
    finally:
        resource.close()

    payload = {
        "snapshot": snapshot_id,
        "query_count": len(results),
        "elapsed_s": round(time.monotonic() - started, 1),
        "results": results,
    }

    if pretty:
        for index, result in enumerate(results, 1):
            print(f"\n=== [{index}] {result['question']}")
            print(f"    risposta in {result['elapsed_s']}s")
            print(f"    {result['answer'][:600]}")
            print(f"    citazioni ({len(result['citations'])}):")
            for citation in result["citations"][:5]:
                print(
                    f"      - {citation['source']} p.{citation['page']}"
                    f"  score={citation['score']}"
                )
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
