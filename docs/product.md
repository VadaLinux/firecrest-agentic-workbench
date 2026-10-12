# FirecREST Agentic Workbench — product

> Scope for the FirecREST Hackathon, CSCS / ETH Zürich, 3 November 2026.
> **What is not in this page is not built before the event.**

## 1. Problem

FirecREST makes HPC easy for a *developer*: a clean REST API for jobs, files and systems.
It is still not easy for the *scientist* who writes the simulation. They have to know the API,
the parameters, the partitions and the failure modes — or ask someone who does.

## 2. Product in one sentence

> **An HPC teammate for scientists: ask in plain language, it runs your job safely through
> FirecREST, and every result is reproducible — on CSCS's own models.**

## 3. What it does (the core)

```
 "Run the matrix benchmark on 4 nodes for 30 minutes and tell me when it's done"
        │
        ▼
 1. Agent understands the request and picks an APPROVED TEMPLATE
        │   only allowlisted templates; typed parameters with limits (nodes 1–64, time ≤ 24 h,
        │   partition from a fixed list) — anything else is refused before FirecREST is called
        ▼
 2. HUMAN APPROVAL — the agent shows what it will submit and waits for "yes"
        ▼
 3. Job submitted through FirecREST v2 (2.6.1) — the agent never sees a token
        ▼
 4. Monitoring → completion → output and logs retrieved
        ▼
 5. PROVENANCE RECORD saved — and the job can be re-run from it, hashes verified first
```

| Capability | Status |
|---|---|
| MCP tools over FirecREST v2: `submit_job`, `get_job_status`, `cancel_job`, `list_files`, `download_file`, `get_job_log` | ✅ exists |
| OAuth2 client-credentials handled inside the tool (the agent never holds a token) | ✅ exists |
| Approved templates with typed, bounded parameters; refusal before any remote call | ✅ exists |
| Audit log (JSON Lines): who, what, which template, parameters **hash** (values never logged), job id, outcome | ✅ exists |
| Aligned to FirecREST **2.6.1** (24 h job look-back, 502/504 errors, `time_window`) | 🔧 CRIGAMO-138 |
| Explicit human approval step before submission (within the MCP tool boundary) | 🔧 CRIGAMO-139 |
| Provenance record + verified re-run | 🔧 CRIGAMO-139 |
| Always-on deployment on a cloud VPS, reachable through Cloudflare | 🔧 CRIGAMO-142 |

## 4. What it does NOT do (yet)

Out of the core, shown only as **next steps** — they exist in my daily environment, not in
the demo path:

- **Multica board** (agents assigned work like teammates, review gate, schedules);
- **Telegram** and mobile access to the same agent;
- **DocMind RAG** over FirecREST docs and job logs;
- **A2A** delegation from other teams' agents.

See [`agentos.md`](./agentos.md) — *The platform behind it: a home-built AgentOS*.

## 5. Architecture

```
 Laptop (client only: browser, SSH, PDF, backup video)
        │
        ▼  Cloudflare tunnel (no open ports)
 ┌──────────────────── Cloud VPS (always on) ─────────────────────┐
 │  Hermes agent ──MCP──► firecrest-mcp ──► FirecREST v2.6.1      │
 │      │                 (templates,        demo stack           │
 │      │                  audit, auth)      (Keycloak, Slurm)    │
 │      ▼                                                         │
 │  provenance records · audit log                                │
 └──────┬─────────────────────────────────────────────────────────┘
        ▼  one OpenAI-compatible endpoint (OmniRoute combos)
  paid API / OpenRouter · local compute · CSCS Inference API (Apertus)
```

- **Agent:** Hermes Agent (Nous Research, MIT) — memory, skills, native MCP.
- **Tool boundary:** every FirecREST call goes through `firecrest-mcp`; nothing else talks to
  the API.
- **Models:** through OmniRoute (open source, MIT). Moving the agent onto CSCS's Inference API
  is a configuration change, not a code change.
- **Why a VPS and not the laptop:** the demo must not depend on one laptop, one home power
  line or one Wi-Fi network.

## 6. Safety

What the agent **cannot** do:

- submit anything that is not an approved template;
- exceed parameter limits (nodes, time, partitions);
- hold or see FirecREST credentials;
- submit without a human "yes".

What is **recorded**: every request and refusal in the audit log; every executed job in a
provenance record. This answers CSCS's own concern that *"coding agents may inadvertently
submit many jobs, consume node hours, or modify files in unintended ways"* (CSCS guide,
*Using coding agents on Alps*).

## 7. Demo script — 5 minutes

| Time | Step |
|---|---|
| 0:00 | The problem in one sentence; architecture in one picture. |
| 0:45 | **Request** in plain language → agent picks the template and shows the plan. |
| 1:30 | **Approval** → submission through FirecREST → live status. |
| 2:30 | **Result**: output and log retrieved; **provenance record** shown. |
| 3:15 | **Re-run** from the provenance record, hashes verified. |
| 3:45 | **Guardrail**: an out-of-policy request ("use 500 nodes for a week") is refused, and the refusal is in the audit log. |
| 4:15 | Next steps: CSCS models via one config change; the AgentOS behind it (one slide). |

Fallbacks: model fallback through OmniRoute; if the network fails, the **2–3 minute recorded
video** of the same script, stored on the laptop.

## 8. Asks for CSCS

1. **Access to a real test system** during the day, to point the workbench at real FirecREST
   instead of the demo stack.
2. **Access to the CSCS Inference API** (Apertus and the code-oriented open models) to run the
   agent on CSCS models.
3. Feedback on a small upstream contribution we are preparing for `eth-cscs/firecrest-v2`
   (default `size` of `ops/view` exceeding `max_ops_file_size`).

## 9. About

**Gabriele Vadalà** — AI Solution Architect & DevOps, CRI.GA.MO. 3 SRLS (Italy).
26 years of enterprise infrastructure (Linux, cloud, DevOps); agentic AI, multi-agent
orchestration and RAG in production.

- Repository: <https://github.com/VadaLinux/firecrest-agentic-workbench>
- Client laptop: openSUSE Leap 16.0

---

*Built on open source: FirecREST and pyfirecrest (CSCS), Hermes Agent (Nous Research),
OmniRoute (diegosouzapw), Multica (Multica team). We did not build these; we integrate them.*
