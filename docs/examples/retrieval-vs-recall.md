# Retrieval vs recall: one question, two answers

A recorded case of an agent answering a question from memory when the authoritative
document was sitting on the same machine. Kept because it is the cleanest available
argument for the retrieval layer, and because it happened on its own rather than being
constructed for a slide.

## The setup

Multica's built-in agent, Mika, was asked what it knew about Apertus and tool calling. Mika
is configured as the workspace's Chief of Staff — goals into issues, coordination — and has
**no MCP servers attached** (`mcp_config` is empty). It therefore answered from parametric
memory, with no access to the CSCS documentation.

The documentation was present locally at that moment, as part of the corpus prepared for
DocMind. The answer was three hundred characters away from where Mika was looking, and
unreachable to it.

## What Mika answered

> "On Apertus (the Swiss sovereign open-LLM family from EPFL/ETH Zürich/CSCS): it ships in
> two sizes, 8B and 70B, each as a base checkpoint and an Instruct checkpoint. The base
> variants (Apertus-8B, Apertus-70B — no -Instruct suffix) are the ones served without tool
> support: they only went through pretraining, never the instruction/tool-use tuning stage,
> so they do raw text completion and have no chat template that emits structured
> function-call output. The -Instruct checkpoints are the tool-capable ones."

It then offered to open an issue to *"evaluate Apertus-70B-Instruct as a tool-calling
backend"*, and to *"spec out a fallback/parsing layer if you ever need to drive a base model
in an agent loop"*.

## What the source says

From CSCS's own inference documentation, `docs/services/inference/api.md`:

> "Note particularly that the **-thinking variants** of the Apertus models are served with
> tool use disabled. If you attempt to use them you may see errors such as `"auto" tool
> choice requires --enable-auto-tool-choice and --tool-call-parser to be set`."

And the catalogue the service actually returns from `/v1/models`:

```
swiss-ai/Apertus-8B-Instruct-2509
swiss-ai/Apertus-70B-Instruct-2509
swiss-ai/Apertus-v1.5-8B
swiss-ai/Apertus-v1.5-8B-thinking        <- tool use disabled
swiss-ai/Apertus-v1.5-70B                <- tool use available
swiss-ai/Apertus-v1.5-70B-thinking       <- tool use disabled
```

## The difference

| | |
|---|---|
| Mika said the discriminator is | base checkpoint vs `-Instruct` checkpoint |
| The source says it is | the `-thinking` suffix |
| Mika said the served models include | `Apertus-8B`, `Apertus-70B` (no suffix, no tool support) |
| The service actually serves | no unsuffixed base checkpoint at all |
| Mika's actionable conclusion | use `-Instruct`; a plain model cannot call tools |
| Consequence | `swiss-ai/Apertus-v1.5-70B`, which has no suffix and **does** support tool calling, would be ruled out |

Mika's *general* principle is sound: a base model does raw completion, has no chat template,
and cannot emit a structured function call, so an agent loop needs an instructed checkpoint.
That is correct as general knowledge about language models.

It is wrong as an answer about Apertus, because it was applied to a catalogue that does not
contain the distinction it describes. The result is a confident paragraph whose conclusion
would rule out a model that works, and whose proposed follow-up issue would start from the
wrong premise.

## Why this is the argument, not an embarrassment

The failure mode here is exactly the one written into the Operator agent's instructions
before this happened:

> "The retrieval layer can produce a fluent sentence that no source supports; noticing that
> is part of the job."

The sentence was produced without the retrieval layer. The risk is not created by RAG — it
is the default state of a language model asked a factual question, and RAG is one of the
things that mitigates it. What RAG adds is not correctness by itself but **checkability**:
an answer that arrives with citations can be verified in seconds, and an answer that arrives
with none can only be believed or disbelieved.

Three things follow.

1. **Mika needs `query_docs`, read-only.** Not the FirecREST job tools — an agent that
   converses with humans has no business submitting work to a cluster — but the ability to
   read the source before answering. This exchange is the evidence for that change; before
   it, the case was a matter of principle.
2. **The read path and the write path are different problems.** Reading wants retrieval over
   the documentation. Writing wants a small, audited set of job tools. Collapsing them into
   one tool surface gives a conversational agent the power to submit jobs.
3. **Citation discipline is a correctness mechanism, not a formatting preference.** A claim
   with no citation behind it is unverified. Saying so is cheap; the alternative is a
   plausible paragraph that quietly rules out the correct configuration.

## Reproducing it

The documentation used to check the answer is generated from the `eth-cscs/cscs-docs`
repository (CC0), and the specific page is `docs/services/inference/api.md`. The
`/v1/models` listing is quoted from CSCS's own example response on that page; the live
endpoint requires an API key we do not have.
