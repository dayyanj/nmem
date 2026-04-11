#!/usr/bin/env bash
# Run the nmem CI workflow locally in a clean Docker container.
#
# Why this exists
# ---------------
# A venv on the dev machine is not a clean CI environment: it inherits
# the host Python, system packages, and often leftover installs from
# prior sessions. That causes false positives — tests pass locally and
# then fail on GitHub Actions because a dep was satisfied by something
# that wasn't declared.
#
# This script runs the exact install + test commands the GHA workflow
# runs, inside a python:<ver>-slim container. No host Python, no host
# site-packages, no surprises.
#
# Usage
# -----
#   scripts/ci-local.sh                # runs unit job on python 3.12
#   scripts/ci-local.sh 3.11           # runs unit job on python 3.11
#   scripts/ci-local.sh 3.13           # runs unit job on python 3.13
#   scripts/ci-local.sh all            # runs the matrix (3.11, 3.12, 3.13)
#
# Limitations
# -----------
# - Only runs the test-unit job. Integration tests need a pgvector
#   Postgres service which is already covered by `docker compose up`
#   + existing tests/integration pytest runs.
# - If you edit .github/workflows/ci.yml, the install/test commands
#   below must be kept in sync (they're duplicated here rather than
#   parsed from yaml, which would need a yaml parser in bash).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# These commands must match .github/workflows/ci.yml `test-unit` job.
# If you change the workflow, change them here too.
INSTALL_CMD='pip install -e ".[cli,sqlite,mcp-server,api]" "pytest>=8.0" "pytest-asyncio>=0.23" "pytest-cov>=5.0" "pytest-timeout>=2.0" "asgi-lifespan>=2.1"'
TEST_CMD='pytest tests/ --ignore=tests/integration -v --timeout=60'

run_one() {
    local pyver="$1"
    local image="python:${pyver}-slim"
    local tag="nmem-ci-py${pyver}"

    echo
    echo "=================================================================="
    echo "CI-local: Python ${pyver}  (image: ${image})"
    echo "=================================================================="

    # Bind-mount the source tree read-only into /work. Scratch /tmp is
    # tmpfs so the pip build dirs don't survive between runs.
    docker run \
        --rm \
        --name "$tag" \
        -v "$REPO_ROOT":/work:ro \
        -w /work \
        --tmpfs /tmp \
        "$image" \
        bash -c "
            set -euo pipefail
            # Copy to a writable location so pip install -e can drop a .egg-info
            cp -a /work /build
            cd /build
            pip install --upgrade pip --quiet
            eval $INSTALL_CMD
            $TEST_CMD
        "
}

case "${1:-3.12}" in
    all)
        run_one 3.11
        run_one 3.12
        run_one 3.13
        ;;
    3.11|3.12|3.13)
        run_one "$1"
        ;;
    *)
        echo "Usage: $0 [3.11|3.12|3.13|all]" >&2
        exit 1
        ;;
esac

echo
echo "=================================================================="
echo "CI-local: all requested Python versions passed"
echo "=================================================================="
