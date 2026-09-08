#!/usr/bin/env bash
# nmem-studio appliance entrypoint. Picks the mode BEFORE Python starts, because nmem /
# nmem-sym read their capability + DSN settings from the environment at import time — so the
# env MUST be in place before the interpreter loads them (agent mode). See studio_server.py.
set -euo pipefail

DATA="${STUDIO_DATA_DIR:-/data}"
# The wizard writes the agent to $DATA/<agent_id>/. Discover the one that exists (single-agent
# appliance) — the same scan studio_server.find_agent_dir() does — and source its env.
CAP="$(ls "$DATA"/*/capabilities.env 2>/dev/null | head -1 || true)"

if [ -n "$CAP" ]; then
  AGENT_DIR="$(dirname "$CAP")"
  echo "[entrypoint] agent config found at $CAP — sourcing env, booting AGENT mode"
  set -a
  # shellcheck disable=SC1090
  . "$CAP"
  [ -f "$AGENT_DIR/secrets.env" ] && . "$AGENT_DIR/secrets.env"
  set +a
else
  echo "[entrypoint] no agent yet — serving the setup WIZARD"
fi

exec python -m nmem.agent_core.studio_server
