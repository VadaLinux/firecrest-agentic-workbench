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
DOCMIND_LLM_REQUEST__MODEL=apertus-70b   # or the smaller Apertus-8B variant
DOCMIND_SECURITY__ALLOW_REMOTE_ENDPOINTS=true
```

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
