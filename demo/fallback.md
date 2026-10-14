# Plan B

1. **Model fallback.** `demo/run-demo.sh check` probes the primary model (`OMNIROUTE_MODEL`)
   and then `DEMO_FALLBACK_MODEL` through `scripts/omniroute_check.py`. Both are configured only in
   gitignored `.env` files (see `demo/.env.example`). If the fallback answers, set
   `OMNIROUTE_MODEL` to it and restart the agent.
2. **Everything down.** Run `demo/replay.sh`: it prints the pre-recorded transcript in
   `demo/prerecorded/` (illustrative samples, not live output). Narrate over it.
3. **Last resort.** Play the backup video from the laptop (`demo/VIDEO.md`).
