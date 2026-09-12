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

Targeted ingestion was attempted with this transcript as its only new source. It
did not begin indexing because DocMind could not acquire its snapshot lock:

```text
src.persistence.lockfile.SnapshotLockTimeoutError: Timed out acquiring snapshot lock
at /app/data/storage/.lock after 10.0s
```

The container process list at the time identified the lock holder as an already
running ingestion, not this job's ingestion attempt:

```text
/app/.venv/bin/python /app/docmind_ingest.py /app/data/vdlp5-corpus
```

Therefore no `query_docs` question or answer is represented as an end-to-end
verification for job 13. The missing item is explicit: after the existing ingestion
releases the snapshot lock, ingest this transcript and query for job `13` plus the
missing input path; preserve the returned citations and answer here before claiming
DocMind retrieved this evidence. The failure diagnosis above remains independently
checkable from the scheduler record and raw job stderr.
