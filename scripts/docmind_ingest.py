#!/usr/bin/env python3
"""Ingest a corpus into DocMind headlessly.

Why this exists
---------------
DocMind has no supported headless ingestion path. Requirement FR-024 / SPEC-026
provides a *parsing* facade (`src/processing/ingestion_api.py`) that turns files
into parsed `Document` objects, but indexing into Qdrant and activating the result
lives in `src/ui/ingest_adapter.py` plus a snapshot transaction orchestrated by
`src/pages/02_documents.py` — the UI layer, by design. The only supported
end-to-end path is the Documents page.

Streamlit's `AppTest` can drive that page's widgets but cannot complete an
asynchronous ingestion: it executes the app script and returns, without keeping the
process-owned JobManager alive (ADR-052), so the job is torn down mid-flight and the
upload never persists.

This script therefore calls the page's own helpers directly:

    _existing_corpus_inputs -> _IngestTransaction -> begin_snapshot
    -> _physical_collection_names -> _plan_pending_inputs -> ingest_inputs
    -> _activate_ingest_generation

Nothing is reimplemented. The page module is loaded with importlib under a
synthetic name; it is safe to import because its `main()` is guarded by
`if __name__ == "__main__"`.

The trade-off is explicit: this depends on private names in a UI page
(underscore-prefixed functions). If upstream renames them, this breaks loudly
rather than silently — the script checks for each one and names the missing symbol.

Usage (inside the app container):

    python docmind_ingest.py /app/corpus

Corpus preparation matters: the ingestion API accepts only
.pdf/.txt/.md/.markdown/.rst. A `.yaml` OpenAPI spec or a `.log` file is skipped
without any error, so stage them as `.txt`.
"""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
import sys
import time
import traceback
from pathlib import Path

PAGE_PATH = Path("src/pages/02_documents.py")
REQUIRED_HELPERS = (
    "_IngestTransaction",
    "_physical_collection_names",
    "_plan_pending_inputs",
    "_existing_corpus_inputs",
    "_activate_ingest_generation",
)
SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md", ".markdown", ".rst"}


def load_page_module():
    """Import the Documents page under a synthetic module name."""
    spec = importlib.util.spec_from_file_location("docmind_documents_page", PAGE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {PAGE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["docmind_documents_page"] = module
    spec.loader.exec_module(module)

    missing = [name for name in REQUIRED_HELPERS if not hasattr(module, name)]
    if missing:
        raise RuntimeError(
            "the Documents page no longer exposes: "
            + ", ".join(missing)
            + " — the headless ingestion path needs updating. Open the Documents "
            "page in the UI and ingest manually, then fix this script."
        )
    return module


def stage_corpus(source: Path, uploads_dir: Path) -> list[Path]:
    """Copy the corpus into the uploads directory.

    Two things this has to handle, both of which DocMind enforces rather than
    warns about:

    1. Unsupported extensions are skipped silently by the ingestion API, so
       anything outside .pdf/.txt/.md/.markdown/.rst is renamed to .txt instead of
       being dropped.
    2. Duplicate *content* is a hard error. Document ids are
       `doc-<sha256 of content>`, and `require_unique_document_ids` rejects the
       whole batch with "Duplicate document_id in ingestion batch". A corpus with
       two byte-identical files (here: three job logs all reading
       "hello-from-mcp") fails entirely. Duplicates are dropped, keeping the first.
    """
    uploads_dir.mkdir(parents=True, exist_ok=True)
    staged: list[Path] = []
    seen: dict[str, str] = {}
    for path in sorted(source.iterdir()):
        if not path.is_file() or path.stat().st_size == 0:
            continue

        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in seen:
            print(f"  {path.name}: duplicate content of {seen[digest]}, skipped")
            continue
        seen[digest] = path.name

        target = uploads_dir / path.name
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            target = uploads_dir / (path.stem + ".txt")
            print(f"  {path.name} -> {target.name}  (extension not supported, renamed)")
        shutil.copyfile(path, target)
        staged.append(target)
    return staged


def remove_duplicate_content(uploads_dir: Path) -> list[str]:
    """Delete files in uploads whose content already appears earlier.

    Staging alone is not enough: a previous run leaves its files behind, and the
    duplicate guard does not care where the second copy came from. Enforcing
    uniqueness across the whole directory is what the app requires.
    """
    seen: dict[str, Path] = {}
    removed: list[str] = []
    for path in sorted(uploads_dir.iterdir()):
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in seen:
            path.unlink()
            removed.append(f"{path.name} (duplicate of {seen[digest].name})")
        else:
            seen[digest] = path
    return removed


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: docmind_ingest.py <corpus-dir>")
        return 2
    corpus = Path(sys.argv[1])
    if not corpus.is_dir():
        print(f"not a directory: {corpus}")
        return 2

    started = time.monotonic()

    print("=== loading settings and integrations ===")
    from src.config import settings
    from src.config.integrations import setup_llamaindex
    from src.persistence.snapshot import SnapshotManager, recover_snapshot_transactions
    from src.ui.ingest_adapter import ingest_inputs

    data_dir = Path(settings.data_dir)
    print(f"  data_dir         : {data_dir}")
    print(f"  text collection  : {settings.database.qdrant_collection}")
    print(f"  image collection : {settings.database.qdrant_image_collection}")

    # Clear anything a previous interrupted attempt left behind. Do this before
    # taking the snapshot lock; it is what the app does at startup.
    print("\n=== recovering any interrupted snapshot transactions ===")
    try:
        recover_snapshot_transactions(data_dir / "storage")
        print("  recovery done")
    except Exception as exc:  # noqa: BLE001
        print(f"  recovery reported: {type(exc).__name__}: {exc}")

    setup_llamaindex(force_llm=True, force_embed=True)
    print("  llamaindex configured")

    print("\n=== staging corpus ===")
    uploads_dir = data_dir / "uploads"
    staged = stage_corpus(corpus, uploads_dir)
    print(f"  {len(staged)} files staged in {uploads_dir}")
    if not staged:
        print("nothing to ingest")
        return 1

    removed = remove_duplicate_content(uploads_dir)
    if removed:
        print(f"  removed {len(removed)} duplicate(s) already in uploads:")
        for item in removed:
            print(f"    - {item}")

    page = load_page_module()
    print("  Documents page helpers loaded")

    inputs = page._existing_corpus_inputs(uploads_dir, encrypt=False)
    print(f"  {len(inputs)} ingestion inputs")

    transaction = page._IngestTransaction(
        manager=SnapshotManager(data_dir / "storage"),
        owned_source_paths=(),
    )
    try:
        print("\n=== begin snapshot ===")
        workspace = transaction.manager.begin_snapshot()
        transaction.workspace = workspace
        collections = page._physical_collection_names(workspace)
        transaction.collections = collections
        print(f"  workspace : {workspace.name}")
        print(f"  text      : {collections['text']}")
        print(f"  image     : {collections['image']}")

        planned, promotion_moves = page._plan_pending_inputs(inputs)
        transaction.promotion_moves = promotion_moves
        print(f"  promotion moves: {len(promotion_moves)}")

        print("\n=== ingest ===")
        t0 = time.monotonic()
        ingest_result = ingest_inputs(
            planned,
            text_collection_name=collections["text"],
            image_collection_name=collections["image"],
            activation_path_aliases={
                source: destination for source, destination, _digest in promotion_moves
            },
            # Prompt 04 step 5: no GraphRAG, no multimodal image pipeline.
            enable_graphrag=False,
            encrypt_images=False,
            nlp_service=None,
        )
        print(f"  ingest_inputs returned in {time.monotonic() - t0:.1f}s")
        print(f"  keys: {sorted(ingest_result.keys())}")

        resource = ingest_result.get("vector_resource")
        if resource is None:
            print("  ERROR: ingest_inputs returned no vector_resource")
            return 1
        transaction.resource = resource

        print("\n=== activate generation ===")
        finalized = page._activate_ingest_generation(
            transaction,
            ingest_result,
            use_graphrag=False,
            quarantine_source=None,
        )
        print(f"  committed: {transaction.committed}")
        print(f"  finalised: {type(finalized).__name__}")
        manifest = getattr(finalized, "manifest", None)
        if manifest is not None:
            for field in ("payload_count", "corpus_hash", "config_hash"):
                print(f"  {field}: {getattr(manifest, field, '<absent>')}")
    except Exception:
        print("\n=== FAILED — rolling back ===")
        traceback.print_exc()
        try:
            transaction.cleanup()
            print("  rollback done")
        except Exception:  # noqa: BLE001
            print("  rollback itself raised:")
            traceback.print_exc()
        return 1

    print(f"\nOK: ingested in {time.monotonic() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
