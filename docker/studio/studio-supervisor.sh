#!/usr/bin/env bash
# nmem-studio container entrypoint. ONE image, two shapes chosen by env:
#   default (docker run)         → embedded Postgres + nmem-viz + the studio, all in this container.
#   compose (NMEM_EMBEDDED_PG=0, → just the studio; Postgres + viz are separate compose services.
#            NMEM_EMBEDDED_VIZ=0)
# supervisord is PID 1 (reaps children; restarts the studio on its create→boot self-SIGTERM, so the
# seam works whether or not `docker run --restart` is set). All state lives on /data.
set -euo pipefail

DATA="${STUDIO_DATA_DIR:-/data}"
mkdir -p "$DATA"

_on() { case "${1:-1}" in 0|false|no|off|"") return 1 ;; *) return 0 ;; esac; }
EMBED_PG="${NMEM_EMBEDDED_PG:-1}"
EMBED_VIZ="${NMEM_EMBEDDED_VIZ:-1}"

CONF=/tmp/nmem-supervisord.conf
cat > "$CONF" <<'HDR'
[supervisord]
nodaemon=true
user=root
pidfile=/tmp/supervisord.pid
logfile=/dev/null
logfile_maxbytes=0
HDR

if _on "$EMBED_PG"; then
  if [ ! -s "$PGDATA/PG_VERSION" ]; then
    echo "[nmem-studio] first boot — initialising embedded Postgres at $PGDATA"
    mkdir -p "$PGDATA"; chown postgres:postgres "$PGDATA"
    # loopback trust only (Postgres is never exposed outside this container)
    gosu postgres initdb -D "$PGDATA" -U "$POSTGRES_USER" --auth-local=trust --auth-host=trust >/dev/null
    {
      echo "listen_addresses = '127.0.0.1'"
      echo "unix_socket_directories = '/var/run/postgresql, /tmp'"
    } >> "$PGDATA/postgresql.conf"
    echo "host all all 127.0.0.1/32 trust" >> "$PGDATA/pg_hba.conf"
  fi
  chown -R postgres:postgres "$PGDATA"
  mkdir -p /var/run/postgresql && chown postgres:postgres /var/run/postgresql
  cat >> "$CONF" <<'PG'

[program:postgres]
command=gosu postgres /usr/lib/postgresql/16/bin/postgres -D /data/pgdata
priority=10
autorestart=true
startretries=1000000
startsecs=3
stdout_logfile=/dev/stdout
stderr_logfile=/dev/stderr
stdout_logfile_maxbytes=0
stderr_logfile_maxbytes=0
PG
fi

if _on "$EMBED_VIZ"; then
  # EXPORT the viz env in the shell (the value is a plain string here — safe with any special char)
  # so the program inherits it. Never interpolate the DB password into the supervisord config file:
  # an unescaped %/"/newline would corrupt config parsing (codex).
  export NMEM_VIZ_DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${NMEM_AGENT_DB}"
  export NMEM_VIZ_AGENT_ID="appliance" NMEM_VIZ_TITLE="nmem agent · brain" NMEM_VIZ_HTTP_PORT="5174"
  cat >> "$CONF" <<'VIZ'

[program:viz]
command=/opt/venv/bin/python server/viz_server.py
directory=/opt/viz
priority=20
autorestart=true
startretries=1000000
startsecs=3
stdout_logfile=/dev/stdout
stderr_logfile=/dev/stderr
stdout_logfile_maxbytes=0
stderr_logfile_maxbytes=0
VIZ
fi

# startretries very high so supervisord NEVER gives up (FATAL) on the studio — the create→boot seam
# relies on it always relaunching, and a transiently-crashing agent must keep retrying like the
# compose `restart: unless-stopped` would (codex). Same for postgres above via its own high retries.
cat >> "$CONF" <<'STUDIO'

[program:studio]
command=/studio-service.sh
priority=30
autorestart=true
startretries=1000000
startsecs=3
stdout_logfile=/dev/stdout
stderr_logfile=/dev/stderr
stdout_logfile_maxbytes=0
stderr_logfile_maxbytes=0
STUDIO

echo "[nmem-studio] starting (embedded postgres=$EMBED_PG, viz=$EMBED_VIZ) — open http://localhost:${STUDIO_PORT:-8080}"
exec supervisord -c "$CONF"
