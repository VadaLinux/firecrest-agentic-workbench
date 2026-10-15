#!/usr/bin/env bash
# Build docs/handout.pdf from docs/handout.md. Installs nothing: if a tool is missing
# it says which one and exits non-zero (no placeholder PDF is ever produced).
set -eu
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
URL="https://github.com/VadaLinux/firecrest-agentic-workbench"
missing=()
for t in pandoc qrencode; do command -v "$t" >/dev/null 2>&1 || missing+=("$t"); done
engine=""
for e in xelatex weasyprint wkhtmltopdf; do
  command -v "$e" >/dev/null 2>&1 && { engine="$e"; break; }
done
[ -n "$engine" ] || missing+=("PDF engine (xelatex|weasyprint|wkhtmltopdf)")
if [ "${#missing[@]}" -gt 0 ]; then
  echo "handout: missing tools: ${missing[*]}" >&2
  exit 3
fi
mkdir -p "$ROOT/docs/assets"
qrencode -o "$ROOT/docs/assets/qr-repo.png" -s 8 "$URL"
cd "$ROOT/docs"
if [ "$engine" = xelatex ]; then
  pandoc handout.md -o handout.pdf --pdf-engine=xelatex -V papersize=a4 -V geometry:margin=1.8cm -V fontsize=10pt
else
  pandoc handout.md -o handout.pdf --pdf-engine="$engine" --css=handout.css -V papersize=a4
fi
echo "wrote docs/handout.pdf"
