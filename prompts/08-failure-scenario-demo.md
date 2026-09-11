# Prompt 08 — Rehearse a controlled failure and its diagnosis

The single most convincing moment of the demo is showing the agent explain *why* something broke, using real logs, not a canned answer. Your task:

1. Design a job script that fails deterministically and informatively — e.g. a script that references a missing input file, or requests a partition that doesn't exist in the dummy cluster. Avoid failures that are just "syntax error in the script," which don't showcase retrieval, use something where the *reason* requires reading FirecREST's response or Slurm's log.
2. Submit it through the same path as prompt 05 (via Hermes, through MetaMCP), let it fail, and pull the log with `get_job_log`.
3. Re-run DocMind ingestion (prompt 04, step 3c) so this specific failure's log is in the index.
4. Ask Hermes, through the same Telegram/Multica path as prompt 06/07: "why did my last job fail?" — confirm it retrieves the actual log content via `query_docs` and gives a correct, specific diagnosis (not a generic "check your script" non-answer).
5. Write the whole sequence — the failing script, the log, the question, and Hermes's answer — into `docs/failure-scenario-transcript.md`. This is your rehearsed fallback if the live version doesn't cooperate on the day.

## Definition of done

- The failure is deterministic and reproducible on demand.
- Hermes's diagnosis, retrieved live, correctly names the actual cause (not a hallucinated one) and references the log.
- `docs/failure-scenario-transcript.md` exists as a rehearsed, known-good fallback.
