#!/bin/bash
#SBATCH --nodes={{nodes}}
#SBATCH --time={{time_minutes}}
#SBATCH --partition={{partition}}
#SBATCH --job-name={{job_name}}
echo "hello-from-fcagent {{message}}"
