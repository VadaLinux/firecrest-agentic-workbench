# Plan B

1. **Model fallback.** `demo/run-demo.sh check` probes the primary model (`OMNIROUTE_MODEL`)
   and then `DEMO_FALLBACK_MODEL` through `scripts/omniroute_check.py`. Both live only in
   gitignored `.env` files (see `demo/.env.example`).
2. **Everything down.** `demo/replay.sh` prints the transcript in `demo/prerecorded/`.
   It is a **template, not real output**: every line is prefixed `[SAMPLE]` and values
   that must come from a real run are `<PLACEHOLDER: ...>`. Replace them with output
   captured from the rehearsal on the VPS (after removing tokens, keys, key paths and
   Cloudflare headers) before relying on Plan B.
3. **Last resort.** Play the backup video from the laptop (`demo/VIDEO.md`).
