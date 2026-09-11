# Prompt 03 — Configure MetaMCP as the single gateway

Your task:

1. Run MetaMCP locally via its published Docker image (`ghcr.io/metatool-ai/metamcp:latest`), following its own README for first-run setup (it needs an OpenAI-compatible key for some internal features — for the laptop demo, point this at the same local Ollama instance DocMind will use, not a paid API).
2. In MetaMCP's UI, register the FirecREST MCP server from prompt 02 as a source server (STDIO or HTTP transport, whichever `firecrest-mcp/server.py` exposes).
3. Create a namespace called `firecrest-workbench` containing only the FirecREST tool for now (DocMind gets added in prompt 04, in the same namespace).
4. Host that namespace as a public endpoint (Streamable HTTP) protected with an API key. Write the endpoint URL and how to obtain/rotate the key into `docs/METAMCP.md` (create it) — do not put the actual key in any committed file.
5. Verify with MetaMCP's built-in inspector that all five FirecREST tools are visible and callable through the aggregated endpoint, not just directly against `firecrest-mcp`.

## Definition of done

- MetaMCP container is healthy and reachable at `localhost:3000` (or documented alternate port).
- The `firecrest-workbench` namespace exposes exactly the 5 FirecREST tools.
- A call to `submit_job` through the MetaMCP endpoint (not directly to `firecrest-mcp`) succeeds against the demo stack.
- `docs/METAMCP.md` documents the endpoint URL, namespace name, and key-rotation procedure, with no real key committed.
