.PHONY: install lint test test-local env-up env-down env-status

install:
	uv sync --all-groups

lint:
	uv run ruff check .
	uv run ruff format --check src tests

test:
	uv run pytest tests

# End-to-end v2 verification. Requires `make env-up`, a configured
# firecrest-mcp/.env, and runs one disposable hello job.
test-local:
	FIRECREST_API_VERSION=v2 firecrest-mcp/.venv/bin/python firecrest-mcp/scripts/mcp_smoke_test.py

# Local FirecREST v2 development environment (CRIGAMO-21). Clones
# eth-cscs/firecrest-v2 on first run (see scripts/env_v2.sh and
# docs/local-env.md), then starts firecrest + slurm + keycloak.
env-up:
	scripts/env_v2.sh

env-down:
	scripts/env_v2.sh down

env-status:
	scripts/env_v2.sh --status
