# The platform behind it: a home-built AgentOS

> Not part of the 5-minute demo core: this is the "where it comes from / where it can go"
> companion to [`product.md`](./product.md).

The FirecREST workbench is not a one-off hackathon prototype. It is one workload running on an
**AgentOS** that I operate every day for my company, CRI.GA.MO. 3 SRLS: a self-hosted stack
built on **three cores** that, together, turn AI agents into reliable colleagues.

**To be clear about authorship: I did not build these three cores.** They are open-source
projects by other teams. I selected them, self-host them, integrate them with each other and
with my own tools, and run them in production every day. My work is the integration, the
configuration, the operating rules and the agents built on top.

| Core | Role | Project | Author | License |
|---|---|---|---|---|
| **Multica** | Orchestrator — *who does what, when, with which approval* | [multica-ai/multica](https://github.com/multica-ai/multica) | Multica team | see repository |
| **Hermes** | Agent core — *the colleague that reasons and acts* | [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) | Nous Research | MIT |
| **OmniRoute** | Inference router — *which model answers, at what cost* | [diegosouzapw/OmniRoute](https://github.com/diegosouzapw/OmniRoute) | diegosouzapw and contributors | MIT |

```
   People: web · desktop app · mobile app · Telegram
                       │  assign work, follow, approve
                       ▼
 ┌───────────────────────────────────────────────────────┐
 │  MULTICA — orchestrator                               │
 │  board · agents as teammates · review gate ·          │
 │  schedules & wake-ups · runtimes on any machine       │
 └──────────────────────────┬────────────────────────────┘
                            │  runs agents (one daemon per machine)
                            ▼
 ┌───────────────────────────────────────────────────────┐
 │  HERMES — agent core                                  │
 │  memory · skills · MCP tools · channels · profiles    │
 └──────────────────────────┬────────────────────────────┘
                            │  one OpenAI-compatible endpoint
                            ▼
 ┌───────────────────────────────────────────────────────┐
 │  OMNIROUTE — inference router                         │
 │  combos & strategies · fallback · cache · MCP · A2A   │
 └──────┬──────────────────┬──────────────────┬──────────┘
        ▼                  ▼                  ▼
  paid APIs / OpenRouter   local compute      CSCS Inference API (Apertus)
                                              + FirecREST for HPC jobs
```

The three cores are deliberately **independent and replaceable**: Multica can drive other
agents, Hermes can talk to any OpenAI-compatible endpoint, OmniRoute can serve any client.
Together they give three properties no single tool gives: **governance** (Multica),
**capability** (Hermes) and **economics** (OmniRoute).

---

## Core 1 — Multica, the orchestrator

### What it is
An open-source platform where humans and AI agents work on the same board. An agent is a
teammate: it is assigned issues, comments while it works, and hands work back for review.
Multica does not ship a model or an agent: it drives the agent CLIs you already run.

### Why I chose it
- **Agents as teammates, not scripts.** Work is assigned, not hard-coded: the same mental model
  a team already uses for tickets and code review.
- **Human review gate.** Agent work lands in *review*, never directly in *done*. This is the
  control an HPC centre asks for when agents can spend node hours.
- **Self-hostable.** Code, prompts and data stay on machines I control (Docker: web, API,
  PostgreSQL; a Helm chart exists for Kubernetes).
- **Runtimes anywhere.** A small daemon on each machine; each agent is bound to the machine
  that fits its job.
- **Native Hermes support.** Hermes is one of the agent runtimes Multica drives out of the
  box — no adapter to maintain.

### How I run it
- Self-hosted on an office server, published through a Cloudflare tunnel; e-mail through an
  SMTP relay.
- **4 machines as runtimes:** office server, marketing workstation, two cloud VPS.
- **9 agents**, among them: a chief-of-staff agent, a job-market radar, a tailored-CV writer,
  prospecting, travel planning, CRM management, a nightly backup runner, and a DevOps pair —
  an *Expert* who proposes every state-changing operation and a *Controller* who must approve
  it first.
- **5 autopilots** (scheduled runs): mailbox triage every few hours, daily job radar, CV
  checks, weekly prospecting, nightly router backup.
- **Conditional wake-ups:** "start when the previous task is in review", "run Monday 09:00".
- **Channels and mobility:** web, Multica's desktop app, Telegram and Multica's mobile app,
  which I use on my phone against my self-hosted server.
- **Since 22 September 2026:** 135 issues, ~900 agent runs (95% completed), ~470 comments.

### What you can do with it
Run a small company's back office with agents; split one project across several specialised
agents with explicit hand-offs; enforce "propose → approve → execute" for anything risky;
schedule recurring agent work; follow and approve everything from a phone.

### Honest status — what operating it taught me
It works, but a multi-machine agent fleet is real operations work. In three weeks I fixed:
an environment variable on one runtime that overrode the per-task token (agents could not
read their own issues); an upstream Hermes bug that rewrote launchers to a temporary path
(I run a guard service until it is fixed upstream); tool connectors configured on one
machine but not on the others after agents were moved. Each fix is logged in an
infrastructure diary.

### Link to FirecREST / CSCS
The review gate is the answer to CSCS's own stated risk that *"coding agents may
inadvertently submit many jobs, consume node hours, or modify files in unintended ways"*:
nothing an agent proposes reaches the cluster without a human approval on the board.

---

## Core 2 — Hermes, the agent core

### What it is
An open-source agent by Nous Research: the component that actually reasons, plans and calls
tools. Every agent on my board is a Hermes instance.

### Why I chose it
"Provider-agnostic" is not the reason — many agent CLIs are. What matters in daily use:

| Property | Why it matters |
|---|---|
| **Persistent memory across sessions** | Agents remember the environment, decisions and past mistakes; a reboot or a context compaction does not reset them. |
| **Self-improving skills** | A problem solved once becomes a stored procedure the next session loads automatically. |
| **Native MCP** | Every tool plugs in through the same protocol. |
| **Multi-platform gateway** | Telegram built in: the same agent answers on the board and on the phone. |
| **Profiles** | Several isolated agents on one machine, each with its own config, skills and permissions. |
| **Custom OpenAI-compatible provider** | Pointing an agent at a new model — including CSCS's Inference API — is configuration, not code. |

### How I run it
- Installed on all 4 machines, driven by Multica through its agent protocol.
- **Models through OmniRoute**, with three agent-specific combos: `hermes-fast` for quick
  answers, `hermes-agent` for normal tool-using work, `hermes-deep` for hard reasoning.
- **Tools via MCP:** the OmniRoute router itself, Gmail / LinkedIn / GitHub (through
  Composio), web scraping (Apify), transactional e-mail (Brevo), the Odoo CRM, and the office
  NAS (read-only for agents).
- **Telegram gateway** for the chief-of-staff agent, including **voice messages**: speech is
  transcribed locally (faster-whisper) and the agent answers with text and a synthesized voice.

### What you can do with it
An agent that remembers your context, learns procedures, uses your tools and talks to you
where you are — from a chat on the phone to a long multi-step task on a server.

### Honest status
Hermes moves fast (canary builds); I pin and test versions per machine. Tool connectors are
per-machine configuration, so moving an agent to another machine means moving its tools too.

### Link to FirecREST / CSCS
In the workbench, FirecREST is just **one more MCP tool** for Hermes: submit, monitor,
download, read logs — with authentication handled inside the tool, never by the agent. CSCS's
own guide *"Using coding agents on Alps"* lists *running the agent locally and submitting
jobs via FirecREST* as one of three recommended patterns: this is that pattern.

---

## Core 3 — OmniRoute, the inference router

### What it is
An open-source AI gateway: one OpenAI-compatible endpoint in front of many model providers,
with routing, fallback, caching, an MCP server and A2A support.

### Why I chose it
- **Strategy becomes configuration.** Routing logic lives in named *combos*, not in agent
  code.
- **Resilience.** Automatic fallback: when a model is rate-limited or down, the next one
  answers and the agent never sees the outage.
- **Economics.** Routine work never touches an expensive model.
- **Agent-manageable.** The router exposes itself as an MCP tool, so an agent can operate it.

### How I run it
- Self-hosted on the office server; machines on the LAN use it directly, cloud machines
  through a Cloudflare endpoint.
- **11 combos** — `free`, `cheap`, `balanced`, `premium` (and v2 variants) plus `hermes-fast`,
  `hermes-agent`, `hermes-deep` — each with the strategy that fits it: `priority` for free
  tiers, `cost-optimized` for routine work, `cache-optimized` for long agent sessions (repeat
  requests go back to the connection holding the cached prefix), `lkgp` (last-known-good
  provider) for premium.
- OmniRoute offers **19 strategies** plus zero-config `auto/*` variants that score every
  connection live on health, quota, cost and latency, with optional exploration to discover
  better models.
- Semantic cache and prompt compression on top; ~2,000 calls logged so far.

### An agent that manages the strategies — via MCP
OmniRoute ships a built-in **MCP server**. In my setup an agent already uses it to read cost
and usage reports and to create combos (160 cost-report and 3 combo-creation calls logged).
The direction I am taking: let that agent **learn from the routing data** — success rates,
latency, cost per task type — and adjust combo order and strategy itself, under the same
Multica review gate as every other change.

### Agents calling agents — A2A
OmniRoute also speaks the **A2A (agent-to-agent) protocol**: an external agent can delegate a
task to the fleet through the gateway, with cost estimate, routing explanation and policy
verdict recorded per task. It is installed but **not yet in production use** in my setup; it is
the natural way to expose a FirecREST agent to other teams' agents.

### Where the capacity comes from — honestly
Technically, a gateway like OmniRoute can also route through personal AI subscriptions
(OAuth logins) and unofficial free web endpoints. **That works, but it is generally not
permitted by the providers' terms.** The legitimate options are paid API keys and services like
**OpenRouter**, which offers limited free use of selected models; together with local compute,
those are the sources a proper deployment should use.

### Link to FirecREST / CSCS
CSCS has its own HPC and its own models — and that is exactly the point: the **CSCS Inference
API** (OpenAI-compatible, serving Apertus and other open-weight models) slots in as **one more
provider in a combo**. A sovereign tier that any agent — including the FirecREST agent — uses
by changing a combo, not code.

---

## Why this matters for CSCS

The workbench shows one workflow: scientist → agent → FirecREST → reproducible result.
The AgentOS shows how that pattern scales:

- **Multica** keeps it governed — every agent action is assigned, logged, reviewed, approved.
- **Hermes** keeps it capable — memory, skills and tools, reachable from a phone.
- **OmniRoute** keeps it sovereign and affordable — the same agents *reason* on CSCS models
  and *compute* on CSCS HPC through FirecREST, by configuration.

All three are open source and self-hostable: nothing here requires CSCS to adopt a vendor.
