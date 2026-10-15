#!/bin/bash
#SBATCH --nodes={{nodes}}
#SBATCH --time={{time_minutes}}
#SBATCH --partition={{partition}}
#SBATCH --job-name={{job_name}}
#SBATCH --output=slurm-%j.out
#SBATCH --error=slurm-%j.err
# Small dense matrix multiplication (pure awk, no dependencies), size {{size}} x {{size}}.
awk -v n={{size}} 'BEGIN {
  for (i = 0; i < n; i++) for (j = 0; j < n; j++) { a[i,j] = (i + j) % 7; b[i,j] = (i * j) % 5 }
  s = 0
  for (i = 0; i < n; i++) for (j = 0; j < n; j++) { c = 0; for (k = 0; k < n; k++) c += a[i,k] * b[k,j]; s += c }
  print "matrix-benchmark size=" n " checksum=" s
}'
