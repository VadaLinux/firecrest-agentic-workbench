# Failure scenario transcript — job 13

## Purpose

This is an evidence-led rehearsal of an HPC batch-job failure.  It records one
submission, what the scheduler and the job itself reported, and the documentation
retrieval check.  It does not infer a cause from the intended script alone.

## Preconditions checked before submission

Tool: `list_files`

Path checked:

```text
/home/service-account-firecrest-sample/firecrest-demo-inputs/
```

Raw result:

```text
/utilities/ls failed (HTTP 400): {"description":"Error on ls operation","error":"ls: cannot access \"/home/service-account-firecrest-sample/firecrest-demo-inputs/\": No such file or directory"}
```

This establishes that the required input directory was absent immediately before
the single submission below.

## Submitted script

```bash
#!/bin/bash
#SBATCH --job-name=preprocess-dataset
#SBATCH --output=preprocess-dataset.out
#SBATCH --error=preprocess-dataset.err
#SBATCH --time=00:01:00
#SBATCH --partition=part01

echo "starting preprocessing at $(date)"
INPUT=/home/service-account-firecrest-sample/firecrest-demo-inputs/dataset.csv
if [ ! -f "$INPUT" ]; then
  echo "ERROR: required input not found: $INPUT" >&2
  exit 3
fi
wc -l "$INPUT"
```

`submit_job(system="cluster")` returned job id `13`, stdout path
`/home/service-account-firecrest-sample/firecrest/7646e410b4fe0f22c7c522f10a97d318/preprocess-dataset.out`,
and stderr path
`/home/service-account-firecrest-sample/firecrest/7646e410b4fe0f22c7c522f10a97d318/preprocess-dataset.err`.

## Scheduler evidence

Tool: `get_job_status(job_id="13")`

```json
{
  "jobid": "13",
  "name": "preprocess-dataset",
  "state": "FAILED",
  "partition": "part01",
  "exit_code": "3:0",
  "start_time": "2026-09-12T17:09:02",
  "termination_time": "2026-09-12T17:09:02"
}
```

## Job output evidence

Tool: `get_job_log(job_id="13")`

stdout:

```text
starting preprocessing at Sat Sep 12 17:09:02 UTC 2026
```

stderr:

```text
ERROR: required input not found: /home/service-account-firecrest-sample/firecrest-demo-inputs/dataset.csv
```

## Diagnosis

The job failed because the required file
`/home/service-account-firecrest-sample/firecrest-demo-inputs/dataset.csv` was not
found. This is supported directly by the stderr line above. Slurm independently
records `FAILED` with exit code `3:0`, matching the script's explicit `exit 3` path.
The stdout start line shows that the batch script started; it is not evidence of a
submission or script-parse failure.

No scheduler-specific failure reason was returned beyond state and exit code.
The diagnosis therefore does **not** attribute the failure to a node, partition,
or scheduler fault.

## DocMind retrieval check

At the time this transcript was first written, targeted ingestion could not begin
because DocMind could not acquire its snapshot lock:

```text
src.persistence.lockfile.SnapshotLockTimeoutError: Timed out acquiring snapshot lock
at /app/data/storage/.lock after 10.0s
```

The container process list at the time identified the lock holder as an already
running ingestion (`/app/.venv/bin/python /app/docmind_ingest.py /app/data/vdlp5-corpus`),
not this job's own attempt, so no verification was recorded and this section said so.

That lock has since cleared and the corpus was reindexed (snapshot
`20260913T035937-419b8dcf`). The parent issue's coordinating agent re-ran the check
directly against the live `query_docs` tool and the result is recorded here verbatim.

**Question asked:** "What caused job 13 preprocess-dataset to fail on part01?"

**Answer received:**

> The job failed because the required input file was missing. The script checked
> for `/home/service-account-firecrest-sample/firecrest-demo-inputs/dataset.csv` and,
> not finding it, printed `ERROR: required input not found: ...` and exited with
> code 3, which Slurm recorded as state FAILED with exit_code "3:0". No scheduler
> or node fault was indicated.

**Citation returned:** `13-failure-scenario-transcript.md`, page 1, score 0.6343
(`source_hash: 7ba9d753062ee3071b814236cd24d5866bfe9b44eb7bdfa1945ef39b8b572b97`).
The retrieved chunk is an earlier draft of this same file — captured before this
"DocMind retrieval check" section was filled in — containing the identical
preconditions check, script, `get_job_status` JSON, and stdout/stderr shown above.
Two lower-scoring, unrelated citations (a v2 UI use-case source file and an OpenMPI
troubleshooting doc, both score ≤0.574) were also returned; neither backs any claim
in the answer, and the answer does not draw on them.

**Check against the evidence:** the answer names the same cause as the diagnosis
above (missing `dataset.csv`), quotes the same stderr line and exit code, and
explicitly declines to attribute the failure to a node or scheduler fault, matching
`## Diagnosis`'s own restraint. It does not introduce any claim unsupported by the
retrieved chunk. This is the end-to-end verification: `query_docs` retrieved the
actual ingested evidence for job 13 (not a stale or unrelated log) and produced a
diagnosis checkable against it, with nothing asserted beyond what the citation
supports.
