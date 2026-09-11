# How to use these prompts

Each file in this folder is a self-contained task prompt, meant to be given to Hermes one at a time, in order, either:

- pasted directly into a Hermes session, or
- filed as a Multica issue and assigned to the Hermes agent (recommended — this is what we demo: the whole build documented as a trail of issues, not a black box).

Every prompt assumes `README.md`, `ARCHITECTURE.md` and `AGENTS.md` at the repo root are already in context (Hermes/Multica read `AGENTS.md` automatically; point it at the other two explicitly if pasting manually).

Order matters for 01 → 05 (each depends on the previous one being live). 06, 07 and 08 can happen in parallel once 05 is done.

| # | Prompt | Produces |
|---|---|---|
| 01 | firecrest-demo-stack | a running local FirecREST (Keycloak + Kong + dummy Slurm) |
| 02 | firecrest-mcp-wrapper | `firecrest-mcp/`, the MCP server |
| 03 | metamcp-setup | MetaMCP container, FirecREST tool registered |
| 04 | docmind-corpus | DocMind ingested with FirecREST docs + hot cache |
| 05 | hermes-metamcp-integration | Hermes configured against the MetaMCP endpoint, end-to-end test passing |
| 06 | multica-orchestration | Multica board with Hermes as an agent, issue → PR flow working |
| 07 | telegram-channel | Telegram bot wired to the Multica workspace |
| 08 | failure-scenario-demo | a rehearsed, reproducible job failure + DocMind diagnosis, for the live demo |

Each prompt below ends with an explicit "Definition of done" — use it as the review checklist before marking the corresponding issue closed.
