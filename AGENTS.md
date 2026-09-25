# fcagent conventions

## Purpose
`fcagent` is the Python scaffold for agentic orchestration around FirecREST.
No FirecREST client logic belongs here; calls remain in `firecrest-mcp/client*.py`.

## Structure
- `core/`: shared orchestration primitives
- `mcp_server/`: MCP server boundary
- `ci/`: CI boundary
- `argo/`: Argo workflow boundary

## Commands
```bash
make install
make lint
make test
```

## Constraints
- Use OmniRoute only for LLM routing.
- Keep shared behavior in `core`.
- Keep CI deterministic with pinned dependencies.
- Never put secrets in code, fixtures, commits, or logs.
