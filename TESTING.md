# Testing nmem

## Before pushing: run CI locally in a clean container

The canonical way to run tests matches exactly what GitHub Actions runs.
**Always run this before pushing** — it prevents "works on my machine" bugs
where a test dep is satisfied by your venv but missing from CI.

```bash
make ci-local          # Python 3.12 in python:3.12-slim container
make ci-local-all      # Full matrix: 3.11, 3.12, 3.13 (what CI runs)
```

The script at `scripts/ci-local.sh` spins up a `python:<ver>-slim` Docker
container, mounts the repo read-only, copies it to a writable `/build`,
runs the **exact install + pytest commands** from
`.github/workflows/ci.yml`, and exits non-zero on failure. No host Python,
no leftover site-packages, no drift.

## Quick: unit tests in your dev venv (faster, less isolated)

```bash
pip install -e ".[dev,sqlite]"
pytest tests/test_cli/ tests/test_importers/ tests/test_mcp/ tests/test_config.py tests/test_search.py tests/test_tiers/ -v
```

Use this for the iteration loop. Use `make ci-local` before you push.

## Integration: requires Docker PostgreSQL

```bash
docker compose up -d
pytest tests/ --ignore=tests/integration/test_real_models.py -v --timeout=30
```

## Full: requires PostgreSQL + sentence-transformers + vLLM backend

```bash
docker compose up -d
pytest tests/ -v --timeout=90
```

## E2E Manual QA Checklist

Run through this before each release:

### Zero-config path
- [ ] `python -m venv /tmp/nmem-test && . /tmp/nmem-test/bin/activate`
- [ ] `pip install nmem[cli,sqlite]`
- [ ] `nmem --version`: prints version
- [ ] `nmem demo`: runs full demo, shows search results + consolidation + prompt injection
- [ ] `nmem stats`: shows tier counts

### Import path
- [ ] `nmem init --sqlite`
- [ ] `nmem import claude-code`: imports ~/.claude/ memories, shows count
- [ ] `nmem search "any topic"`: finds imported entries
- [ ] `nmem stats`: shows per-agent breakdown with "claude-code" agent

### PostgreSQL path
- [ ] `docker compose up -d`
- [ ] `NMEM_DATABASE_URL="postgresql+asyncpg://nmem:nmem@localhost:5433/nmem" nmem init`
- [ ] `NMEM_DATABASE_URL=... nmem import claude-code`
- [ ] `NMEM_DATABASE_URL=... nmem search "deployment"`
- [ ] `NMEM_DATABASE_URL=... nmem consolidate`
- [ ] `NMEM_DATABASE_URL=... nmem stats`

### MCP server
- [ ] `echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' | NMEM_DATABASE_URL=... nmem-mcp`: responds with JSON
- [ ] `nmem setup --project-dir /tmp/test`: creates `.claude.json`, shows CLAUDE.md snippet

### Config
- [ ] Copy `nmem.example.toml` to `nmem.toml`, verify `nmem init` reads it
