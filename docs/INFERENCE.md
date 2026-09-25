# Inference: laptop demo vs the version proposed to CSCS

## Two deployments, one config change

The whole point of building DocMind on a backend-agnostic config (it natively supports `ollama`, `vllm`, `lmstudio`, `llamacpp`, and `openai_compatible`) is that the laptop demo and the "real" proposed deployment differ by **environment variables, not code**.

### Laptop demo (what we bring to the hackathon)

```
DOCMIND_LLM_BACKEND=ollama
DOCMIND_OLLAMA_BASE_URL=http://localhost:11434
DOCMIND_LLM_REQUEST__MODEL=qwen3:4b-instruct
```

Small model, CPU-only, fully offline. Good enough to demonstrate the RAG/agent loop live; not representative of production quality or latency.

### Version proposed to CSCS

CSCS already runs its own **LLM Inference API Service** — an OpenAI/Anthropic-compatible endpoint (`https://api.inference.cscs.ch/v1`) serving **Apertus**, the fully-open LLM trained on Alps itself, plus other vetted open-weight models. CSCS operates the serving stack (deployment, patching, scaling); we don't need to run or maintain any inference infrastructure ourselves.

```
DOCMIND_LLM_BACKEND=openai_compatible
DOCMIND_OPENAI__BASE_URL=https://api.inference.cscs.ch/v1
DOCMIND_OPENAI__API_KEY=<CSCS_INFERENCE_API_KEY>
DOCMIND_LLM_REQUEST__MODEL=swiss-ai/Apertus-v1.5-70B
DOCMIND_SECURITY__ALLOW_REMOTE_ENDPOINTS=true
```

Model IDs are exact — they come from the service's own `/v1/models` listing, not from prose.
Verified against <https://docs.cscs.ch/services/inference/api/>:
`swiss-ai/Apertus-v1.5-70B`, `swiss-ai/Apertus-8B-Instruct-2509`,
`swiss-ai/Apertus-70B-Instruct-2509`, `google/gemma-4-31B-it`,
`moonshotai/Kimi-K2.7-Code`, `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-BF16`,
`zai-org/GLM-5.2`. An earlier draft of this document used a shorthand
`apertus-70b` that does not resolve to anything.

**Do not use the `-thinking` variants for anything that calls tools.** CSCS documents that
they are served with tool use disabled, and that attempting it produces
`"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set`.
DocMind's synthesis needs no tools, so it is unaffected — but the agent is not.

Same DocMind deployment, same agent, same MCP wrapper — the model call is the only thing that moves off the laptop.

### The agent's model, which is the larger consumer

DocMind's model synthesises answers. **Hermes's model decides what to do** — which tool to
call, how to read a job failure, when to ask. It is the larger consumer and the one whose
quality the demo actually rests on, so it swaps the same way, through a user-defined model
alias pointing at a `custom` provider:

```yaml
model_aliases:
  apertus:
    model: apertus-70b          # or the smaller Apertus-8B variant
    provider: custom
    base_url: "https://api.inference.cscs.ch/v1"
    key_env: CSCS_INFERENCE_API_KEY
```

then `/model apertus`. No code change, no fork, no adapter — the same property
`docs/MULTICA.md` argues is load-bearing for choosing Hermes as the Multica runtime at all.

**Status: written, not exercised.** We have no CSCS inference credentials, so neither this
alias nor the DocMind one has ever run against `api.inference.cscs.ch`. What can be verified
without their key is that the `custom` provider path itself works, by pointing it at an
OpenAI-compatible endpoint we control — worth doing before the 3rd, because an unexecuted
config line is the claim that fails in front of the person who built the endpoint.

## Why this matters for the pitch, specifically

- **Sovereignty / EU AI Act alignment**: Apertus is fully open (weights, training data, alignment documented) and explicitly positioned by CSCS as compliant with the EU AI Act. That's a stronger governance story than "we called OpenAI's API."
- **No inference infrastructure to operate**: the proposal doesn't ask CSCS to stand up anything new — it consumes a service they've already launched.
- **Same tokens, no re-training the agent**: because DocMind and Hermes both speak OpenAI-compatible chat completions, nothing about the MCP tools, the Multica orchestration, or the Telegram channel changes when we point at Apertus instead of Ollama. The demo we show live is architecturally identical to the deployment we're proposing — we are not hand-waving a "in production this would be different."

## What we will NOT claim in the demo

We will demo against the local Ollama backend on the laptop (no CSCS inference credentials needed for the hackathon itself) and state clearly, live, that the production version swaps three environment variables to run on CSCS's own Apertus endpoint instead — not claim we've already tested against `api.inference.cscs.ch` unless we've actually requested and verified access beforehand.

## OmniRoute Local Proxy Setup

To decouple local development from external keys, OmniRoute can optionally be used as a proxy.
OmniRoute intercepts OpenAI-compatible checks, acts as the central router, and validates requests.

Configure the integration by copying the example environment file from the root directory:

```bash
cp .env.example .env
```

This root `.env` provides the necessary fallback overrides without exposing secrets:
- `OMNIROUTE_DISCOVERY_URL` targets the host listener (e.g., `http://127.0.0.1:20128/v1`).
- `OMNIROUTE_BASE_URL` dictates what the internal Docker containers use (e.g., `http://host.docker.internal:20128/v1`).

**Critical Networking Requirement:**
For `host.docker.internal` to successfully traverse the bridge, the OmniRoute host instance **must bind to `0.0.0.0` or directly to the Docker bridge IP (e.g., `172.17.0.1`)**.
Binding OmniRoute only to `127.0.0.1` will block incoming traffic from the container with a `Connection Refused` error. Note that `0.0.0.0` opens the listener to all network interfaces, exposing OmniRoute — use specific IP binding or host firewall rules to restrict access when needed.

When you pass `DOCMIND_LLM_BACKEND_CHOICE=openai_compatible` to `scripts/workbench-up.sh`, the wrapper checks if OmniRoute is available and falls back gracefully.
