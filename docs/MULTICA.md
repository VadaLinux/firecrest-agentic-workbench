# Multica: the orchestration board

Where this fits: agents get work assigned like teammates, on a board with an execution log
and a human review gate. This document records the self-host decision, why Hermes is the
runtime, and how the whole stack lands on CSCS's own inference service.

## Decision: self-hosted, not cloud

Self-host, on the laptop, via Docker Compose.

The reasoning is not "we prefer local" — it is the same reasoning that runs through the
whole project:

- **Code and prompts never leave the machine.** Multica's own framing is that a *runtime*
  is a machine you control and "code never leaves it". Consuming their cloud board would
  put an orchestration layer over a project whose entire argument is verifiability and
  sovereignty. It would also undercut the pitch we make to CSCS two paragraphs later.
- **No third-party dependency in the demo path.** A hosted board is another account, another
  network dependency, and another thing that can be unavailable at 10:30 on the day.
- **It is cheap here.** Multica adds a Next.js frontend, a Go backend and PostgreSQL 17.
  This laptop currently runs 21 containers — 15 FirecREST demo services, four DocMind, two
  MetaMCP — with 23 GiB of 31 GiB RAM available, 380 GiB of disk free, and a load average
  under 0.4. Headroom is not the constraint.

Multica also ships a Helm chart, so the deployment we run locally is the same shape as one
CSCS could run on their own Kubernetes — worth saying out loud, because "it only runs on
his laptop" is the obvious objection.

## Hermes is the runtime

Multica does not ship a model, and it does not ship an agent. It drives agent CLIs that you
already have installed and authenticated — 26 of them at the time of writing. **Hermes is
one of them**, listed under its own name in their provider table. Nothing had to be adapted,
wrapped or forked for this project's agent to appear on the board.

That matters more than it sounds: the alternative is writing an adapter between our agent
and their orchestration protocol, which is exactly the kind of glue that rots.

### Why Hermes, stated so it survives the question

The tempting one-line answer — "because it is provider-agnostic" — does not survive contact
with a knowledgeable reviewer, because **OpenCode is also provider-agnostic and is also in
Multica's list of 26**. Saying it invites the reply "so is OpenCode", and then the
justification has to be rebuilt in front of the person you are trying to convince.

Agnosticism is necessary, not sufficient. What is actually load-bearing for *this* project:

| Property | Why it matters here |
|---|---|
| **Provider-agnostic, with a `custom` provider** | The only way to run the agent itself on CSCS's Apertus endpoint without a code change. Necessary, and shared with OpenCode. |
| **Persistent memory across sessions** | An orchestrator routing work to several agents needs context that outlives a session. Everything this project learned about DocMind, MetaMCP and FirecREST survived a reboot and a context compaction as a result — the `HANDOVER.md` and the remembered environment facts are that property doing real work. |
| **Self-improving skills** | A solved problem becomes a reusable procedure instead of being re-derived. Every operational finding from prompts 01–04 is stored where the next session loads it automatically. |
| **Native multi-platform gateway** | Telegram is built in, which removes prompt 07 as a component to build — and Multica supports Telegram as a channel, so the two compose instead of competing. |
| **Native MCP** | The architecture is MCP end to end: MetaMCP, `firecrest-mcp`, `query_docs`. |
| **Profiles** | Several independent Hermes instances, isolated config and skills — what "several agents on one project" actually needs. |

The first row is shared with OpenCode. Rows two, three and four are not.

## Apertus: the agent's model, not just the RAG's

`docs/INFERENCE.md` documents the swap for DocMind's model. **The agent is the larger
consumer**, and it swaps the same way — a user-defined alias pointing at a `custom` provider:

```yaml
model_aliases:
  apertus:
    model: swiss-ai/Apertus-v1.5-70B   # exact ID from the service's /v1/models
    provider: custom
    base_url: "https://api.inference.cscs.ch/v1"
    key_env: CSCS_INFERENCE_API_KEY
```

`/model apertus` and the agent reasons on CSCS's own open model. No code change, no fork, no
adapter.

**Two corrections to how this was first written.** The model ID must be exact —
`swiss-ai/Apertus-v1.5-70B`, or `swiss-ai/Apertus-70B-Instruct-2509`; an earlier draft used a
shorthand `apertus-70b` that resolves to nothing. And the `-thinking` variants are served
**with tool use disabled**, which is disqualifying for an agent whose entire job is calling
MCP tools. DocMind's synthesis needs no tools and is unaffected.

For the agent specifically, CSCS's own coding-agent examples point at
`moonshotai/Kimi-K2.7-Code`, and their inference docs note Apertus is "optimized for general
use rather than programming tasks specifically". That is a real signal, not a footnote: the
sovereignty argument survives either way — both are open-weight models served inside CSCS —
but the honest configuration is likely **Apertus for retrieval synthesis, a code-specialised
open model for the agent**, rather than one model stretched across two different jobs.

## CSCS already documents this architecture, and it is one of the three they recommend

The single strongest thing we can say about this design is not ours to claim. CSCS maintains
a page, [Using coding agents on
Alps](https://docs.cscs.ch/guides/coding-agents/), which lists three ways to run an agent
against Alps. The third:

> running the agent locally and submitting jobs over SSH and Slurm or **via FirecREST**

That is our architecture, named by CSCS, with FirecREST called out. The same page recommends
against running agents on login nodes and suggests *"running agents either on compute nodes
or on your local computer with limited Slurm or FirecREST jobs allowed"* — again, the pattern
this project uses.

Three further points from that page, each of which we should address directly rather than
let a reviewer raise first:

- **They name the open problem we are working on.** *"Sandboxing agents is effective for the
  agent process and its tool calls on the particular node... When an agent submits a Slurm
  job, the restrictions of the sandbox are typically not propagated to the compute nodes.
  **We are investigating alternatives for improving the integration with Slurm.**"* A local
  agent driving FirecREST has no sandbox expectations to break, because nothing of the
  agent's runs on the cluster. That is a genuine, quotable answer to a stated CSCS concern.
- **They name the governance risk.** Compute spent by agent-launched jobs is billed to the
  project, and *"coding agents may inadvertently submit many jobs, consume node hours, or
  modify files in unintended ways"*, with CSCS taking no responsibility. **Multica's review
  gate is the answer to exactly this**: work lands in review rather than in `main`, and a
  human approves before it ships. The orchestration layer is not decoration — it is the
  control CSCS is asking for.
- **They invite contributions.** *"We encourage and welcome contributions from the CSCS user
  community on best practices on using coding agents... contribute directly to the
  documentation."* That is a concrete, low-cost way for this project to give something back,
  independent of whether it ships: the operational findings from prompts 01–04 — the
  FirecREST v1/v2 spec discrepancy, the ingestion constraints, the inference budget that
  silently clamps — belong on that page.

Also worth knowing before the day: the inference service is *"currently offered from a single
infrastructure"* and *"interruptions of the service should be expected"*, and it does not
distinguish cached from uncached input tokens, with an explicit warning to *"beware the costs
when performing typical agentic usage."* Both are reasons the local-first fallback stays in
the design rather than being replaced by the CSCS endpoint outright.

Why this is the strongest single argument in the project:

- **We ask CSCS for nothing new.** Apertus is already served — they run the deployment,
  patching and scaling. We consume a service that exists.
- **The chain becomes open end to end**: open agent framework, open orchestration, open
  retrieval, open model, all auditable. The alternative is routing researcher queries
  through an opaque third-party model, which is the thing CSCS's inference service exists to
  avoid.
- **EU AI Act alignment stops being a slogan** and becomes a deployment fact, because the
  model is the one CSCS positions for exactly that.
- **It fixes a measured problem.** On CPU, one answered question took 45 seconds and
  ingesting 170 KB took thirteen minutes — we measured both. The same stack against an
  inference endpoint turns that into seconds, which is the difference between a system that
  can be demonstrated live and one that cannot.

**Status, stated honestly: the configuration is written, not yet exercised.** We have no
CSCS inference credentials, so this has never been run against
`api.inference.cscs.ch`. What we *can* verify without their key is that the `custom`
provider path itself works, by pointing it at an OpenAI-compatible endpoint we control. That
verification is worth doing before the 3rd, because "a config line we never executed" is
exactly the claim that falls apart in front of the person who built the endpoint.

## The stack, end to end

```
Multica  (board: issues, execution log, review gate)
   └─→ Hermes  (runtime on the laptop; model = Apertus at CSCS)
         └─→ MetaMCP  (single gateway, one key)
               ├─→ firecrest-mcp → FirecREST → the cluster
               └─→ docmind query_docs → local-first RAG, citations
```

Two boundaries carry the design:

- **Between Multica and Hermes**: work is described in plain language as an issue and comes
  back for review. Multica never needs to know what FirecREST is.
- **Between Hermes and the world**: every capability arrives as an MCP tool through one
  gateway. Hermes never needs to know how FirecREST authenticates.

Everything the agent can do is therefore visible in one place — the aggregated tool surface —
which is also what makes it auditable rather than a black box that "just did something".

## What "several agents in synchrony" means concretely

The intent is several agents working the same project. Two things about that are worth
fixing in advance rather than discovering:

**Parallel agents on one codebase contradict each other.** This is not theoretical: the
browser-harness documentation warns about two agents racing on the same tab, and that is a
much smaller shared resource than a git working tree. Multica provides the right mechanisms —
a review gate so work lands in review rather than in `main`, and squads with a lead that
routes work — but the coordination still has to be designed.

**The workable shape here is division by workstream, not by volume.** Two agents with
disjoint responsibilities, not five generic ones: one on the FirecREST MCP wrapper (its
tests, its tool contracts), one on the retrieval layer (corpus, citations, query
behaviour). They touch different files, and their outputs meet at the review gate rather
than in the working tree. On 8 cores, more agents would mostly contend.

## If the self-host path blocks

Multica's README carries the warning we have already been burned by once:

> if the selected GHCR tag has not been published yet, fall back to `make selfhost-build`
> from a checkout

That is the same failure class as MetaMCP, where the published image was behind `main` and
lacked the entire declarative configuration mechanism — it started cleanly and silently
created nothing. The mitigation is the same and is applied up front: **check the published
tag before installing, and prefer building from a checkout.** It also keeps the install
auditable, which is the point of the exercise; a `curl | bash` installer would be an odd
choice for a project arguing that infrastructure should be verifiable.

## Open items

- The published GHCR tag has not been checked yet. Do that before choosing an install path.
- The `custom` provider path has not been exercised — see the status note above.
- The self-host install has not been run. This document records the decision and the
  intended path, not a completed deployment; it should be updated with what actually
  happened rather than left reading as though it worked first time.
