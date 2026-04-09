.PHONY: ci-local ci-unit ci-integration ci-lint clean-ci help

CI_VENV := /tmp/nmem-ci-venv
DB_URL := postgresql+asyncpg://nmem:nmem@localhost:5433/nmem

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

ci-local: ci-lint ci-unit ci-integration ## Run full CI locally (lint + unit + integration)
	@echo "\n\033[32m=== All CI checks passed ===\033[0m"

ci-lint: ## Check imports work
	@echo "\033[36m=== Lint ===\033[0m"
	@rm -rf $(CI_VENV)
	@python3 -m venv $(CI_VENV)
	@$(CI_VENV)/bin/pip install -q -e ".[cli,sqlite]"
	@$(CI_VENV)/bin/python -c "import nmem; print(f'nmem {nmem.__version__} imported OK')"

ci-unit: ## Run unit tests in clean venv (SQLite, no heavy deps)
	@echo "\033[36m=== Unit Tests (SQLite) ===\033[0m"
	@rm -rf $(CI_VENV)
	@python3 -m venv $(CI_VENV)
	@$(CI_VENV)/bin/pip install -q -e ".[cli,sqlite,mcp-server]" "pytest>=8.0" "pytest-asyncio>=0.23" "pytest-timeout>=2.0"
	@$(CI_VENV)/bin/python -m pytest tests/ --ignore=tests/integration -v --timeout=60

ci-integration: ## Run integration tests against PostgreSQL (requires docker compose up)
	@echo "\033[36m=== Integration Tests (PostgreSQL + pgvector) ===\033[0m"
	@docker compose ps --status running | grep -q nmem-db || (echo "Starting PostgreSQL..." && docker compose up -d --wait)
	@rm -rf $(CI_VENV)
	@python3 -m venv $(CI_VENV)
	@$(CI_VENV)/bin/pip install -q -e ".[cli,postgres]" "pytest>=8.0" "pytest-asyncio>=0.23" "pytest-timeout>=2.0"
	@$(CI_VENV)/bin/python -c "import asyncio,asyncpg;asyncio.run(asyncpg.connect('postgresql://nmem:nmem@localhost:5433/nmem'))" 2>/dev/null \
		|| (echo "\033[31mCannot connect to PostgreSQL on port 5433\033[0m" && exit 1)
	@$(CI_VENV)/bin/python scripts/setup_pgvector.py
	@NMEM_TEST_DSN=$(DB_URL) $(CI_VENV)/bin/python -m pytest tests/integration/test_postgres.py -v --timeout=30

clean-ci: ## Remove CI test venv and temp files
	@rm -rf $(CI_VENV) /tmp/nmem_benchmark.db /tmp/nmem_demo.db
	@echo "Cleaned CI artifacts"
