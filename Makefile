.PHONY: ci-local ci-local-all ci-integration clean-ci help

DB_URL := postgresql+asyncpg://nmem:nmem@localhost:5433/nmem

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

ci-local: ## Run the unit test CI job in a clean Docker container (Python 3.12)
	@scripts/ci-local.sh 3.12

ci-local-all: ## Run the full CI matrix (Python 3.11, 3.12, 3.13) in clean containers
	@scripts/ci-local.sh all

ci-integration: ## Run integration tests against local PostgreSQL (needs `docker compose up`)
	@echo "\033[36m=== Integration Tests (PostgreSQL + pgvector) ===\033[0m"
	@docker compose ps --status running | grep -q nmem-db || (echo "Starting PostgreSQL..." && docker compose up -d --wait)
	@rm -rf /tmp/nmem-ci-int-venv
	@python3 -m venv /tmp/nmem-ci-int-venv
	@/tmp/nmem-ci-int-venv/bin/pip install -q -e ".[cli,postgres]" "pytest>=8.0" "pytest-asyncio>=0.23" "pytest-timeout>=2.0"
	@/tmp/nmem-ci-int-venv/bin/python -c "import asyncio,asyncpg;asyncio.run(asyncpg.connect('postgresql://nmem:nmem@localhost:5433/nmem'))" 2>/dev/null \
		|| (echo "\033[31mCannot connect to PostgreSQL on port 5433\033[0m" && exit 1)
	@/tmp/nmem-ci-int-venv/bin/python scripts/setup_pgvector.py
	@NMEM_TEST_DSN=$(DB_URL) /tmp/nmem-ci-int-venv/bin/python -m pytest tests/integration/test_postgres.py -v --timeout=30

clean-ci: ## Remove CI test artifacts
	@rm -rf /tmp/nmem-ci-int-venv /tmp/nmem_benchmark.db /tmp/nmem_demo.db
	@docker rmi -f python:3.11-slim python:3.12-slim python:3.13-slim 2>/dev/null || true
	@echo "Cleaned CI artifacts"
