#!/bin/bash
#SBATCH --nodes={{nodes}}
#SBATCH --time={{time_minutes}}
#SBATCH --partition={{partition}}
#SBATCH --job-name={{job_name}}
#SBATCH --output=slurm-%j.out
#SBATCH --error=slurm-%j.err
echo "hello-from-fcagent {{message}}"
