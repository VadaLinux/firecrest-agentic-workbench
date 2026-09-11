# Prompt 01 — Stand up the FirecREST demo stack

You are working in this repo. Your task:

1. Clone `https://github.com/eth-cscs/firecrest` into a sibling directory (not inside this repo — add the path to `.gitignore` if it ends up nested).
2. Follow `deploy/demo/README.md` exactly: set the SSH key permissions to `400` before starting anything, then bring up the stack with Docker Compose (fall back to Podman Compose if Docker isn't available).
3. The first build compiles Slurm from source inside the "cluster" container — this can take several minutes. Wait it out rather than assuming it hung.
4. Once up, verify against the bundled demo client: submit a trivial job (e.g. `echo hello`) through the demo client's own workflow and confirm it completes and its output is retrievable.
5. Write down, in `docs/hot-cache.md` (create it), the exact endpoints and payloads you used to do step 4 — this becomes the seed of the "hot cache" DocMind will use later.
6. Do not modify anything inside the cloned `firecrest` repo itself.

## Definition of done

- `docker compose ps` (or podman equivalent) shows all demo-stack services healthy.
- A job submitted through the demo client reaches `COMPLETED` and its stdout is downloadable.
- `docs/hot-cache.md` exists with at least the auth flow (token request) and one job-submission example, both as raw HTTP or `curl`, not paraphrased.
- Nothing outside `docs/hot-cache.md` and `.gitignore` was changed in this repo.
