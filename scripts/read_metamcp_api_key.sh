#!/usr/bin/env bash
# Read the bootstrapped MetaMCP API key out of Postgres and store it locally.
# The value is never echoed: it is only written to a gitignored file.
#
# The SQL goes through a file rather than `psql -c` because the query needs
# quoting that survives two shells (this script, then `sg docker -c ...`).
set -euo pipefail

REPO="${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SECRETS="${SECRETS:-${REPO}/.secrets}"
mkdir -p "$SECRETS"

SQL=/tmp/metamcp_keyquery.sql
cat > "$SQL" <<'END_SQL'
\pset tuples_only on
\pset format unaligned
select 'users=' || count(*) from users;
select 'namespaces=' || count(*) from namespaces;
select 'endpoints=' || count(*) from endpoints;
select 'api_keys=' || count(*) from api_keys;
select 'mcp_servers=' || count(*) from mcp_servers;
END_SQL

echo "=== bootstrap: conteggi ==="
sg docker -c "docker exec -i metamcp-pg psql -U metamcp_user -d metamcp_db -f -" < "$SQL"

cat > "$SQL" <<'END_SQL'
\pset tuples_only on
\pset format unaligned
select '  namespace: ' || name from namespaces order by name;
select '  endpoint : ' || name from endpoints order by name;
select '  api key  : ' || name || ' (public=' || is_public || ')' from api_keys order by name;
END_SQL

echo
echo "=== nomi (nessun segreto) ==="
sg docker -c "docker exec -i metamcp-pg psql -U metamcp_user -d metamcp_db -f -" < "$SQL"

cat > "$SQL" <<'END_SQL'
\pset tuples_only on
\pset format unaligned
select key from api_keys where name = 'firecrest-workbench' limit 1;
END_SQL

KEY=$(sg docker -c "docker exec -i metamcp-pg psql -tAq -U metamcp_user -d metamcp_db -f -" < "$SQL" 2>/dev/null | tr -d '\r\n')
rm -f "$SQL"

if [ -z "$KEY" ]; then
  echo
  echo "ERROR: no API key named 'firecrest-workbench' was bootstrapped" >&2
  exit 1
fi
printf '%s\n' "$KEY" > "$SECRETS/metamcp-api-key.txt"
chmod 600 "$SECRETS/metamcp-api-key.txt"
echo
echo "API key stored in $SECRETS/metamcp-api-key.txt (${#KEY} chars, value not printed)"
