.PHONY: install lint test env-up env-down env-status

install:
	uv sync --all-groups

lint:
	uv run ruff check .
	uv run ruff format --check src tests

test:
	uv run pytest tests

# Local FirecREST v2 development environment (CRIGAMO-21). Clones
# eth-cscs/firecrest-v2 on first run (see scripts/env_v2.sh and
# docs/local-env.md), then starts firecrest + slurm + keycloak.
env-up:
	scripts/env_v2.sh

env-down:
	scripts/env_v2.sh down

env-status:
	scripts/env_v2.sh --status
