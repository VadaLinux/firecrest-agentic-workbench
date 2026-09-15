# Reproducing the local workbench environment

This is the environment that produced this repository, measured on 2026-09-15. It is a reproducible development and demo environment, not a claim of minimum hardware requirements.

## Measured capacity, not minimums

The build host is an HP ZBook 17 G3 with an Intel i7-6820HQ (4 cores / 8 threads, 2.70 GHz), 31 GiB RAM and a 464 GB NVMe. With all four compose projects active, `free -h` reported 13 GiB used, 17 GiB available and no swap in use; `df -h /` reported 93 GiB used and 370 GiB available. Those are observations on this host, not evidence that 16 GiB or any other lower figure is sufficient.

The NVIDIA Quadro M3000M has 4 GiB VRAM (4096 MiB), driver 580.178.04 and CUDA 13.0; its Maxwell compute capability is 5.2. A GPU is optional for the workbench. Its useful local role is embedding or a small fallback model, not large-model inference: the 4 GiB VRAM cannot hold both the documented 2.9 GiB local Ollama model footprint and the roughly 2.3 GiB BGE-M3 embedding footprint. See [GPU.md](GPU.md) for the measured behaviour and commands.

## openSUSE Leap 16 preparation

The measured system is openSUSE Leap 16.0, kernel `6.12.0`, Docker `29.4.0-ce` and Compose `2.33.1`. Install Docker and its Compose plugin using the distribution packages, enable the Docker service, and make the intended non-root user a member of the `docker` group:

```bash
sudo zypper install docker docker-compose
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

For NVIDIA containers, use the verified repository, package and runtime configuration in [GPU.md](GPU.md). It installs `nvidia-container-toolkit` and configures Docker's `nvidia` runtime; restarting Docker stops containers without restart policies, so do it before bringing up the stacks.

SELinux remained enforcing for the entire build. Check it with `sestatus` and inspect a bind-mounted source with `ls -Z`. On this host, a path under the user home had label `user_home_t`, which confined `container_t` services could not read. The verified fix for the FirecREST demo is to relabel only its bind-mounted paths as `container_file_t`:

```bash
sudo chcon -R -t container_file_t <firecrest-clone>/deploy/demo \
  <firecrest-clone>/deploy/test-build/environment/keys \
  <firecrest-clone>/doc/openapi
```

No SELinux boolean was needed and SELinux was not made permissive. If you maintain a compose file yourself, use `:z` for a shared bind mount or `:Z` for a private one instead of disabling enforcement. `chcon` labels survive reboot but not `restorecon` or a full relabel; the diagnosis and original evidence are in [the demo-stack report](reports/01-firecrest-demo-stack.md#5-blocker-encountered-and-resolved).

## Start stacks in dependency order

1. Clone the official FirecREST repository and start `deploy/demo` with `docker compose up -d`. Its first image build compiles Slurm from source and was observed to take 10–20 minutes; it is the longest first-run step.
2. Create the wrapper environment in `firecrest-mcp/` using the commands in `AGENTS.md`, then start the MCP server with its local `.env` (never commit that file).
3. Start MetaMCP from its own checkout and configure its endpoint to reach the wrapper; the exact local configuration is in [METAMCP.md](METAMCP.md).
4. Start DocMind from its own checkout, including its documented compose override and corpus preparation. Its embeddings run on CPU in the recorded configuration and the initial ingestion took 771 seconds for four files; see [the corpus report](reports/04-docmind-corpus.md).
5. Start Multica from its own compose checkout last; it supplies the board and agent execution records. Its local setup is described in [MULTICA.md](MULTICA.md).

At measurement, `docker ps --format '{{.Label "com.docker.compose.project"}}' | sort | uniq -c` returned 15 `demo`, 4 `docmind-ai-llm`, 3 `multica` and 2 `metamcp`: 24 active containers. This corrects the otherwise easy-to-assume total of 25.

## End-to-end checks

With the FirecREST demo running, an unauthenticated Kong request must return `401`, which confirms the gateway is reachable and enforcing authentication:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://localhost:8000/
```

The wrapper unit suite is intentionally independent of the demo stack. Create its development virtual environment as described in `AGENTS.md`, stop the demo stack, then run:

```bash
cd firecrest-mcp
.venv/bin/python -m pytest
```

The suite contains 44 tests in this revision. The required acceptance result is `44 passed` with the demo stopped; this documentation update did not stop or restart the already-running demo stack, so that stopped-stack result was not re-measured on 2026-09-15. Finally, repeat the compose-project count above and compare it with the expected 24 active containers.

## Traps already paid for

- The original NVIDIA note was wrong: the driver works, but the NVIDIA Container Toolkit was missing. The current Docker runtime is configured but GPU acceleration is not enabled for the containers; [GPU.md](GPU.md) records both the correction and the VRAM limit.
- The FirecREST demo has no restart policies. Docker can return after reboot while the 15 demo services remain down; run its compose `up -d` before a rehearsal. The `f7t-base` build-stage container exiting with status 0 is normal.
- DocMind's Ollama container is on an internal-only network by default, so it cannot pull a model from the registry. The upstream `.env` also only substitutes compose values; it is not automatically passed to the app. Both findings and the supported workaround choices are in [the DocMind reconnaissance](reports/04-docmind-recon.md).
- DocMind silently skips `.yaml` and `.log` inputs, and duplicate content aborts an ingestion batch. Stage those as supported text files and deduplicate before ingestion; see [the corpus report](reports/04-docmind-corpus.md).

## Replacing local inference

The development environment can point DocMind and the agent at CSCS's inference service without changing the wrapper. Follow [INFERENCE.md](INFERENCE.md) for the exact environment variables, model identifiers and the explicitly unverified credential-dependent path; do not duplicate secrets in this repository.
