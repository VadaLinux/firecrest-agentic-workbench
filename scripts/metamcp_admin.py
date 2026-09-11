#!/usr/bin/env python3
"""Headless administration of MetaMCP through its own admin MCP tools.

MetaMCP exposes its administrative operations as MCP tools on the same endpoints
it serves, prefixed `metamcp-admin__` (see apps/backend/src/lib/admin-mcp/
tools-registry.ts upstream). That means the whole prompt 03 configuration —
registering the FirecREST MCP server, attaching it to the `firecrest-workbench`
namespace, exposing an endpoint — can be done without the web UI, and therefore
without a human click and without a screenshot to prove it happened.

Usage:
    metamcp_admin.py tools                     # tool names on the endpoint
    metamcp_admin.py schema <tool>             # input schema of one tool
    metamcp_admin.py servers | namespaces | endpoints
    metamcp_admin.py call <tool> '<json args>'
    metamcp_admin.py add-firecrest [--url URL] # the prompt 03 configuration

The API key is read from .secrets/metamcp-api-key.txt (gitignored) and never
printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

REPO = Path(__file__).resolve().parent.parent
KEY_FILE = REPO / ".secrets" / "metamcp-api-key.txt"
ENDPOINT = "http://localhost:12008/metamcp/firecrest-workbench/mcp"
NAMESPACE = "firecrest-workbench"
ADMIN_PREFIX = "metamcp-admin__"


def api_key() -> str:
    if not KEY_FILE.exists():
        sys.exit(
            f"missing {KEY_FILE} — run scripts/read_metamcp_api_key.sh first"
        )
    key = KEY_FILE.read_text().strip()
    if not key:
        sys.exit(f"{KEY_FILE} is empty")
    return key


def parse_result(result) -> dict:
    """Unwrap an MCP tool result into a plain object."""
    if getattr(result, "structured_content", None):
        return result.structured_content
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
    return {}


class Admin:
    # Read timeout for admin tool calls. The 60s this used to be hardcoded to was the
    # cause of a long false trail: it truncated `docmind__query_docs` at 60s, which
    # looks identical to a slow or broken tool. Aggregated tools can legitimately take
    # minutes when the backend infers on CPU, so the default is generous and callers
    # can override it with --timeout.
    DEFAULT_TIMEOUT_S = 900.0

    def __init__(self, url: str, key: str, timeout_s: float = DEFAULT_TIMEOUT_S):
        self.url = url
        self.key = key
        self.timeout_s = timeout_s

    async def __aenter__(self):
        self._http = httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {self.key}"},
            timeout=httpx2.Timeout(self.timeout_s),
            follow_redirects=True,
        )
        self._ctx = streamable_http_client(self.url, http_client=self._http)
        read, write = await self._ctx.__aenter__()
        self._session = ClientSession(read, write)
        await self._session.__aenter__()
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc):
        await self._session.__aexit__(*exc)
        await self._ctx.__aexit__(*exc)
        await self._http.aclose()

    async def tools(self):
        return (await self._session.list_tools()).tools

    async def call(self, name: str, args: dict | None = None):
        result = await self._session.call_tool(name, args or {})
        return parse_result(result)


def pretty(obj) -> str:
    return json.dumps(obj, indent=2, default=str)


async def run(args) -> int:
    async with Admin(args.endpoint, api_key(), timeout_s=args.timeout) as admin:
        if args.command == "tools":
            names = sorted(t.name for t in await admin.tools())
            for n in names:
                print(n)
            return 0

        if args.command == "schema":
            for t in await admin.tools():
                if t.name == args.tool or t.name == ADMIN_PREFIX + args.tool:
                    print(pretty({"name": t.name, "description": t.description,
                                  "inputSchema": t.input_schema}))
                    return 0
            print(f"no such tool: {args.tool}", file=sys.stderr)
            return 1

        if args.command in ("servers", "namespaces", "endpoints"):
            tool = {
                "servers": "metamcp_list_mcp_servers",
                "namespaces": "metamcp_list_namespaces",
                "endpoints": "metamcp_list_endpoints",
            }[args.command]
            print(pretty(await admin.call(ADMIN_PREFIX + tool)))
            return 0

        if args.command == "call":
            payload = json.loads(args.json) if args.json else {}
            print(pretty(await admin.call(args.tool, payload)))
            return 0

        if args.command == "add-firecrest":
            return await add_firecrest(admin, args)

        if args.command == "add-server":
            return await add_server(admin, args)

    return 1


async def add_server(admin: Admin, args) -> int:
    """Register an HTTP MCP server and attach it to a namespace.

    Field names are camelCase: the admin tools take the tRPC input shapes, not the
    database column names. `metamcp_refresh_namespace_tools` is deliberately not
    used here — it expects the caller to pass the tool list it has already
    discovered, which is the web UI's job, not an administrator's.

    Generic over the server because prompt 04 adds a second one (DocMind's RAG) next
    to the first (FirecREST); duplicating this per server would mean two places to fix
    whenever the admin tool shapes change.
    """
    created = await admin.call(ADMIN_PREFIX + "metamcp_create_mcp_server", {
        "name": args.name,
        "description": args.description,
        "type": "STREAMABLE_HTTP",
        "url": args.url,
    })
    print("=== create_mcp_server ===")
    print(pretty(created))

    payload = created.get("data", created)
    server_uuid = None
    if isinstance(payload, dict):
        server_uuid = payload.get("uuid") or (payload.get("server") or {}).get("uuid")
    if not server_uuid:
        print("could not determine the new server uuid", file=sys.stderr)
        return 1
    print(f"\nserver uuid: {server_uuid}")

    attached = await admin.call(
        ADMIN_PREFIX + "metamcp_update_namespace_server_status", {
            "namespaceUuid": args.namespace_uuid,
            "serverUuid": server_uuid,
            "status": "ACTIVE",
        })
    print("\n=== update_namespace_server_status ===")
    print(pretty(attached))

    tools = await admin.call(
        ADMIN_PREFIX + "metamcp_get_namespace_tools",
        {"namespaceUuid": args.namespace_uuid})
    print("\n=== get_namespace_tools ===")
    print(pretty(tools))

    names = sorted(t.name for t in await admin.tools())
    exposed = [t for t in names if not t.startswith(ADMIN_PREFIX)]
    print("\n=== tools on the endpoint (excluding admin) ===")
    print(pretty(exposed))
    return 0


async def add_firecrest(admin: Admin, args) -> int:
    """Register firecrest-mcp, preserving the prompt 03 invocation."""
    args.name = "firecrest"
    args.description = "FirecREST HPC job submission, status, logs and files"
    return await add_server(admin, args)


def main() -> int:
    p = argparse.ArgumentParser(description="Headless MetaMCP administration via its admin MCP tools.")
    p.add_argument("--endpoint", default=ENDPOINT)
    p.add_argument(
        "--timeout",
        type=float,
        default=Admin.DEFAULT_TIMEOUT_S,
        help="read timeout in seconds for a tool call (default: %(default)s)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("tools")
    sp = sub.add_parser("schema")
    sp.add_argument("tool")
    sub.add_parser("servers")
    sub.add_parser("namespaces")
    sub.add_parser("endpoints")
    sc = sub.add_parser("call")
    sc.add_argument("tool")
    sc.add_argument("json", nargs="?")

    sa = sub.add_parser("add-firecrest")
    sa.add_argument("--url", default="http://host.docker.internal:8765/mcp")
    sa.add_argument("--namespace-uuid", required=True)

    sg = sub.add_parser("add-server")
    sg.add_argument("--name", required=True)
    sg.add_argument("--description", required=True)
    sg.add_argument("--url", required=True)
    sg.add_argument("--namespace-uuid", required=True)

    args = p.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
