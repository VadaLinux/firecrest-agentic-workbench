# Trust and data protection by design

> **Not legal advice.** This page describes engineering choices and how they relate
> to the principles of the GDPR, the Swiss FADP and the EU AI Act. It is not a legal
> opinion. Any production use needs a formal assessment by qualified counsel.

## Which rules apply to whom

- **CSCS is in Switzerland.** Personal data handled by CSCS falls under the Swiss
  Federal Act on Data Protection (FADP / nLPD), in force since 1 September 2023, not
  under the GDPR as such. [FADP on Fedlex](https://www.fedlex.admin.ch/eli/cc/2022/491/en);
  guidance from the [FDPIC / EDÖB / IFPDT](https://www.edoeb.admin.ch/en).
- **CRI.GA.MO. 3 is Italian.** The [GDPR (Reg. EU 2016/679)](https://eur-lex.europa.eu/eli/reg/2016/679/oj)
  applies to any personal data we process ourselves.
- **EU AI Act ([Reg. EU 2024/1689](https://eur-lex.europa.eu/eli/reg/2024/1689/oj)).**
  - Art. 2(6) and 2(8) exclude AI research and development before placing on the
    market or putting into service. The hackathon demo falls in that space.
  - The workbench is not one of the high-risk use cases listed in Annex III.
  - Art. 4 (AI literacy) applies since 2 February 2025.
  - Art. 50 (transparency towards people interacting with an AI system) applies
    since 2 August 2026, so it is already in force.
  - Obligations for general-purpose AI models (Chapter V) fall on the model provider,
    not on this repository.
- Switzerland has no AI Act equivalent. In 2025 the Federal Council chose to ratify
  the Council of Europe AI Convention and to adapt sector laws.

## What we claim

We **adopt these principles by design**, as good practice for a European HPC-AI
service. Using the workbench in production requires a formal assessment.

## Principle → workbench

| Principle | Source | Already in place | Gap / action |
|---|---|---|---|
| Human oversight | AI Act art. 14 | `fcagent approve` gate, Ed25519-signed, one-time consumption | Limit D2 is declared in `docs/reproducibility.md`; D1 (unprivileged agent, separate approver identity) is future work. Key rotation policy: none yet |
| Transparency | AI Act art. 13, 50 | cards, `next_step` messages | Standard AI notice (below); the card now records the declared AI client and model (`ai_agent`) |
| Robustness and security | AI Act art. 15, GDPR art. 32 | template allowlist, limits, system/workdir validation, fail-closed | Review of secrets in logs and a threat list are still open |
| Minimisation, privacy by design | GDPR art. 5, 25; FADP art. 6–7 | audit never stores parameters or scripts | The approver identity (OS user name) is personal data: see below |
| Data subject rights | GDPR art. 15–22 | none | Short procedure below |

Articles: [AI Act](https://eur-lex.europa.eu/eli/reg/2024/1689/oj) ·
[GDPR](https://eur-lex.europa.eu/eli/reg/2016/679/oj) ·
[FADP](https://www.fedlex.admin.ch/eli/cc/2022/491/en).

## Standard transparency sentence

Use this exact sentence in demos and in the handout:

> You are interacting with an AI agent; every job needs a human approval.

The demo profile's `SOUL.md` is updated separately by Claude Code.

## AI agent recorded in the card

`provenance.json` has an `ai_agent` object `{client, model}`. Values come only from
`FCAGENT_AI_CLIENT` and `FCAGENT_AI_MODEL`, declared by the operator; they are never
guessed and never read from API keys, URLs or headers. Each is trimmed, must be
printable and is cut at 128 characters. Unset means `null`; if neither is set the
whole field is `null`. Cards written before this field existed remain valid.

## Personal data in the workbench

| Where | What | Why | Retention |
|---|---|---|---|
| `logs/audit.jsonl` | OS user (`actor`, `approved_by`), timestamps | accountability of approvals | `FCAGENT_RETENTION_DAYS` (default 90) |
| `<state_dir>/prepared/*.json` | approver name, timestamps | approval gate | same |
| `<state_dir>/jobs/<id>/provenance.*` | approver name, job metadata | reproducibility | same |

Legal basis (to be confirmed by counsel): legitimate interest in the security and
traceability of job submission (GDPR art. 6(1)(f)). Pseudonymisation: run the
approving identity under a pseudonymous OS account if the real name is not needed.

**Data subject requests** (access, erasure): search the three locations above for the
user name (`grep -l '<user>' logs/audit.jsonl; grep -rl '<user>' <state_dir>`). Export by
copying the matching lines/files. Erasure is by editing or removing those lines/files
with the stack stopped; this conflicts with accountability, so the request needs a
human decision.

## Retention

Set `FCAGENT_RETENTION_DAYS` (positive integer; invalid values stop the script, there
is no silent fallback). Then:

```
python scripts/retention_cleanup.py            # dry-run: prints what would go
python scripts/retention_cleanup.py --apply    # deletes
```

Rules:

- **Prepared records**: deleted if `consumed`/`rejected` and older than the threshold,
  or `pending`/`approved` and already expired. A record that can still be consumed is
  never touched. Deletion takes the same `flock` on `<state_dir>/.lock` as the approval store.
- **Cards**: deleted only if `state` is terminal and `times.ended` is older than the
  threshold. Cards of non-terminal jobs are kept.
- **Audit**: lines older than the threshold are removed by writing a temporary file and
  `os.replace`. Unparseable lines are kept. `AuditLog.write` appends without a lock, so
  run `--apply` with the stack stopped; if the file changes during the run, the rewrite is skipped.
- Symlinks are never followed; only `jobs/<valid job id>` and the prepared directory are touched.
- No cron or systemd timer is installed. Scheduling is the operator's decision.

## What we do NOT claim

- We do not say the workbench is "compliant" with, or "certified" against, the GDPR,
  the FADP or the AI Act.
- We do not give legal advice or a legal classification beyond the pointers above.
- The approval gate is a process control, not protection against a malicious agent with
  shell and sudo on the same host (decision D2).
