#!/usr/bin/env python3
"""Build the DocMind ingestion corpus from source trees.

DocMind's ingestion takes a *flat* directory: `docmind_ingest.py` iterates the top
level and writes by basename. The source trees here are nested and contain many
identically-named files (`index.md`, `README.md`), so flattening naively collides and
DocMind rejects the batch with "Duplicate document_id in ingestion batch".

Each file is therefore renamed to a path-encoded, unique name:

    docs/services/inference/api.md   ->  services__inference__api.md

Path separators become double underscores, so the hierarchy stays readable in
citations while the name is unique.

Extensions DocMind does not accept are **renamed to `.txt`, never dropped**. This is
the part an earlier hand-rolled flatten got wrong: it skipped files whose name had no
suffix at all, which silently discarded real content — the Containerfiles under
`docs/software/communication/dockerfiles/` and the environment-variable lists
`nccl_env_vars` and `torch_distributed_env_vars`. `docmind_ingest.py`'s own
`stage_corpus` renames unsupported extensions for exactly this reason; doing it here
means the two agree instead of one undoing the other.

Content is deduplicated by SHA-256, because DocMind hard-errors on duplicate document
ids rather than warning.

Usage:
    build_corpus.py --out <staging-dir> <source-tree>[:<prefix>] ...

Example:
    build_corpus.py --out /tmp/corpus \
        ~/Sviluppo/cscs-docs/docs:docs \
        /tmp/f7t-v2-src/docs:v2docs
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md", ".markdown", ".rst"}

# Files that are pure repository metadata and carry no user-facing content.
SKIP_NAMES = {"CNAME", "patchref", ".gitignore", ".dockerignore"}

# Binary formats that must never be ingested, whatever their extension. Note `.svg`:
# it is text (XML), but it is a *diagram* — its content is markup, not prose, so
# feeding it in adds noise rather than knowledge.
SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp", ".bmp", ".tiff",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".zip", ".gz", ".tar", ".whl",
    ".pyc", ".so", ".dll", ".exe", ".bin",
}


def is_binary(path: Path, probe: int = 8192) -> bool:
    """True when the first chunk looks like binary (NUL byte present).

    Used instead of trusting the extension, so a mislabelled file cannot slip a
    blob into the corpus as text.
    """
    try:
        with path.open("rb") as handle:
            return b"\x00" in handle.read(probe)
    except OSError:
        return True


def flatten(source: Path, prefix: str, out_dir: Path, seen: dict[str, str]) -> list[Path]:
    staged: list[Path] = []
    renamed: list[str] = []
    skipped_binary: list[str] = []

    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.stat().st_size == 0:
            continue
        if path.name in SKIP_NAMES or path.suffix.lower() in SKIP_EXTENSIONS:
            continue

        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in seen:
            print(f"  dup: {path} == {seen[digest]}")
            continue
        seen[digest] = str(path)

        rel = path.relative_to(source)
        flat = "__".join((prefix,) + rel.parts) if prefix else "__".join(rel.parts)

        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            # Rename rather than drop — but only if it is really text. A binary with
            # an unsupported extension is skipped, not turned into a .txt blob.
            if is_binary(path):
                skipped_binary.append(flat)
                continue
            new_flat = (flat + ".txt") if not path.suffix else (flat[: -len(path.suffix)] + ".txt")
            renamed.append(f"{flat} -> {new_flat}")
            flat = new_flat

        # Disambiguate rather than abort: two source files can legitimately map to the
        # same flat name (e.g. `x` and `x.bak` both become `x.txt`).
        target = out_dir / flat
        if target.exists():
            stem, dot, ext = flat.rpartition(".")
            n = 2
            while target.exists():
                flat = f"{stem}-{n}{dot}{ext}"
                target = out_dir / flat
                n += 1
            renamed.append(f"collision resolved -> {flat}")
        shutil.copyfile(path, target)
        staged.append(target)

    if renamed:
        print(f"  {len(renamed)} rinominati:")
        for item in renamed:
            print(f"    {item}")
    if skipped_binary:
        print(f"  {len(skipped_binary)} binari scartati (est. non supportata e contenuto non testuale)")
    return staged


def main(argv: list[str] | None = None) -> int:
    doc = (__doc__ or "").split("\n")[0]
    parser = argparse.ArgumentParser(description=doc)
    parser.add_argument("--out", required=True, help="staging directory to write")
    parser.add_argument(
        "sources",
        nargs="+",
        help="source tree, optionally suffixed with :<prefix> to namespace its names",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    seen: dict[str, str] = {}
    total: list[Path] = []
    for spec in args.sources:
        source_str, _, prefix = spec.partition(":")
        source = Path(source_str).expanduser()
        if not source.is_dir():
            print(f"not a directory: {source}")
            return 2
        print(f"=== {source}  (prefix {prefix or '-'})")
        total += flatten(source, prefix, out_dir, seen)

    names = [p.name for p in total]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        print(f"FATAL: non-unique names remain: {sorted(dupes)}")
        return 1

    size = sum(p.stat().st_size for p in total)
    print(f"\nstaged : {len(total)} files  ({size / 1024:.0f} KB)  -> {out_dir}")
    print(f"dupes  : {len(seen) - len(total)} dropped by content")
    print("names  : all unique")
    return 0


if __name__ == "__main__":
    sys.exit(main())
