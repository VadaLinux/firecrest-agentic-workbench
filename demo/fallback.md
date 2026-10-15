# Plan B

1. **Model fallback.** `demo/run-demo.sh check` probes the primary model (`OMNIROUTE_MODEL`)
   and then `DEMO_FALLBACK_MODEL` through `scripts/omniroute_check.py`. Both live only in
   gitignored `.env` files (see `demo/.env.example`).
2. **Everything down.** `demo/replay.sh` prints the transcript in `demo/prerecorded/`.
   The files are **real output, excerpted; annotations marked**: captured on
   2026-10-15 from the VPS service (FirecREST 2.6.1; jobs 11 and 12, `matrix`, 1 node,
   `checksum=38402000`, plus the 4-node refusal). Reduced responses are marked
   `[excerpt]` and comments start with `# note:`; no value was changed or invented. The outputs contain no
   tokens, keys, key paths or Cloudflare headers. Every printed line is prefixed
   `[REPLAY]` and the banner says it is recorded and not live: say so on stage.
   If the flow, the templates or the tool output change, capture again.
3. **Last resort.** Play the backup video from the laptop (`demo/VIDEO.md`).
