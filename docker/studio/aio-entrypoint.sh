#!/usr/bin/env bash
# nmem-studio all-in-one entrypoint: initialise the bundled Postgres cluster ON THE /data VOLUME
# (so the database persists alongside the agent config), then hand off to supervisord which runs
# postgres + nmem-viz + the studio. PID 1 = supervisord (reaps children; restarts the studio on the
# create→boot self-SIGTERM seam without needing `docker run --restart`).
set -euo pipefail

DATA="${STUDIO_DATA_DIR:-/data}"
mkdir -p "$DATA"

if [ ! -s "$PGDATA/PG_VERSION" ]; then
  echo "[aio] first boot — initialising Postgres cluster at $PGDATA"
  mkdir -p "$PGDATA"
  chown postgres:postgres "$PGDATA"
  # trust auth on the loopback only (Postgres is never exposed outside this container); the studio
  # connects as $POSTGRES_USER over 127.0.0.1, so no password handling is needed.
  gosu postgres initdb -D "$PGDATA" -U "$POSTGRES_USER" --auth-local=trust --auth-host=trust >/dev/null
  {
    echo "listen_addresses = '127.0.0.1'"
    echo "unix_socket_directories = '/var/run/postgresql, /tmp'"
  } >> "$PGDATA/postgresql.conf"
  echo "host all all 127.0.0.1/32 trust" >> "$PGDATA/pg_hba.conf"
fi
chown -R postgres:postgres "$PGDATA"
mkdir -p /var/run/postgresql && chown postgres:postgres /var/run/postgresql

echo "[aio] starting Postgres + nmem-viz + studio (open http://localhost:${STUDIO_PORT:-8080})"
exec supervisord -c /etc/supervisor/conf.d/nmem.conf
