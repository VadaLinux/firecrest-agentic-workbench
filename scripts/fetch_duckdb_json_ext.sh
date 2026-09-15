#!/usr/bin/env bash
# Fetch the DuckDB "json" extension onto the host, so the DocMind app container
# can find it locally instead of downloading it from extensions.duckdb.org.
#
# Why this is needed: DuckDB auto-installs the json extension on first use by
# fetching it from extensions.duckdb.org. That host serves IPv6 (AAAA) records,
# and the Docker network this app runs on is IPv4-only, so the container cannot
# reach it and ingestion dies with:
#   IO Error: Failed to download extension "json" at URL
#     "http://extensions.duckdb.org/v1.3.2/linux_amd64/json.duckdb_extension.gz"
# The same download works fine from the host, so we fetch it here and let the
# compose override bind it read-only into the container.
#
# Usage:
#   scripts/fetch_duckdb_json_ext.sh
#
# Writes:
#   $HOME/Sviluppo/duckdb-extensions/json.duckdb_extension
# and relabels it for SELinux (container_file_t) so the bind mount is readable.

set -euo pipefail

DEST_DIR="${DUCKDB_EXT_DIR:-${WORKSPACE_ROOT:-${HOME}/Sviluppo}/duckdb-extensions}"
URL="${DUCKDB_EXT_URL:-https://extensions.duckdb.org/v1.3.2/linux_amd64/json.duckdb_extension.gz}"
EXPECTED_SHA256="ebdced42fff071e7a9ac904347466dafb6e45c489453b0543ca6f150581fa167"

mkdir -p "$DEST_DIR"
gz="$(mktemp --suffix=.gz)"
trap 'rm -f "$gz"' EXIT

echo "downloading $URL"
curl -fsSL -o "$gz" "$URL"
gunzip -f "$gz" -c > "$DEST_DIR/json.duckdb_extension"

actual="$(sha256sum "$DEST_DIR/json.duckdb_extension" | awk '{print $1}')"
if [ "$actual" != "$EXPECTED_SHA256" ]; then
    echo "ERROR: sha256 mismatch" >&2
    echo "  expected $EXPECTED_SHA256" >&2
    echo "  got      $actual" >&2
    rm -f "$DEST_DIR/json.duckdb_extension"
    exit 1
fi

chmod 644 "$DEST_DIR/json.duckdb_extension"
# SELinux: make the bind mount readable by the container.
chcon -t container_file_t "$DEST_DIR/json.duckdb_extension" 2>/dev/null || true

echo "OK: $(ls -la "$DEST_DIR/json.duckdb_extension")"
echo "verified sha256 $actual"
