#!/usr/bin/env python3
"""MCP server exposing DocMind's RAG corpus as a `query_docs` tool.

Prompt 04 asks for a `query_docs` tool returning three fields — `answer`,
`citations`, `retrieved_chunks`. This is that tool.

Run it (inside a container built from the DocMind image, which is where DocMind's
code and its snapshot data live):

    python docmind_mcp.py --transport http --host 0.0.0.0 --port 8770
    python docmind_mcp.py --dry-run          # announce the tool surface and exit

Why a separate process rather than a DocMind endpoint: DocMind exposes no retrieval
API. Its retrieval stack is reachable only through the Streamlit Chat page — see
docmind_query.py for the full explanation. This server reuses that module's runtime
loader, retrieval and synthesis so there is exactly one implementation of the query
path, not two that can drift.

The tool docstring below is written for the *agent* as its reader. The MCP server
publishes it as the tool description, so it is the agent's only guidance on when the
tool applies — it is not developer documentation.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time

from mcp.server.mcpserver import MCPServer

import docmind_query

logger = logging.getLogger("docmind-mcp")

server = MCPServer(
    name="docmind-rag",
    title="DocMind RAG",
    instructions=(
        "Semantic search over the FirecREST documentation corpus, answered in natural "
        "language with citations. Use this when you need to know how the FirecREST API "
        "works — which endpoint to call, which header to set — and you do not already "
        "have the answer. Prefer it over guessing. Answers are synthesised from "
        "retrieved passages, so they can be confidently wrong: check the citations, and "
        "treat a claim with no corresponding citation as unverified. For the handful of "
        "calls this project uses most, docs/hot-cache.md in the repository is verified "
        "and takes precedence over anything returned here."
    ),
)

# One runtime per process, rebuilt only when the active snapshot changes. Building it
# loads BGE-M3 and opens the vector store, which is far too expensive per call.
_runtime_lock = threading.Lock()
_runtime: tuple[str, object, object] | None = None


def _get_runtime():
    """Return a router bound to the currently active snapshot."""
    global _runtime
    with _runtime_lock:
        current = docmind_query.current_snapshot_id()
        if _runtime is not None and _runtime[0] == current:
            return _runtime
        if _runtime is not None:
            logger.info("active snapshot changed to %s; rebuilding router", current)
            _runtime[1].close()
            _runtime = None
        snapshot_id, resource, router = docmind_query.build_runtime()
        logger.info("router ready for snapshot %s", snapshot_id)
        _runtime = (snapshot_id, resource, router)
        return _runtime


@server.tool()
def query_docs(question: str) -> dict:
    """Search the FirecREST documentation and answer a question about it.

    Answers "how do I use the FirecREST API" style questions — authentication, job
    submission, job status, file transfer, machine selection — from an indexed corpus
    of the FirecREST OpenAPI specification and a curated hot cache of verified calls.

    Returns three fields:
      answer            the synthesised answer, as prose
      citations         one entry per retrieved passage, with the source file, page,
                        relevance score and a content hash. Cite these when you relay
                        the answer.
      retrieved_chunks  the raw passages the answer was built from, so a caller can
                        verify any claim against its source

    The answer is generated from retrieved passages, not from the API itself, and may
    contain an endpoint that looks plausible but does not exist. `citations` is what
    makes a claim checkable: if a claim has no citation behind it, it is unverified.
    """
    started = time.monotonic()
    snapshot_id, _resource, router = _get_runtime()

    chunks = [
        docmind_query.node_to_chunk(node_with_score)
        for node_with_score in docmind_query.retrieve(router, question)
    ]
    answer = docmind_query.synthesise(question, chunks)

    return {
        "answer": answer,
        "citations": [
            {
                "source": chunk["source"],
                "page": chunk["page"],
                "score": chunk["score"],
                "document_id": chunk["document_id"],
                "source_hash": chunk["source_hash"],
            }
            for chunk in chunks
        ],
        "retrieved_chunks": chunks,
        "snapshot": snapshot_id,
        "elapsed_s": round(time.monotonic() - started, 1),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="MCP server exposing DocMind's RAG corpus as query_docs."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="announce the tool surface and exit without serving",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
        help="stdio (default, used by MetaMCP over a command) or Streamable HTTP",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host")
    parser.add_argument("--port", type=int, default=8770, help="HTTP bind port")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if args.dry_run:
        logger.info(
            "tool surface: query_docs — active snapshot: %s",
            docmind_query.current_snapshot_id(),
        )
        return 0

    if args.transport == "http":
        logger.info("serving Streamable HTTP on %s:%d", args.host, args.port)
        server.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        logger.info("serving stdio")
        server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
