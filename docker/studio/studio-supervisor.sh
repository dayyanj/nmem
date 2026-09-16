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
# The embedded EXCHANGE broker is OFF by default: a solo agent needs no bus, and a hive JOINER
# connects to the hive's existing broker. Turn it ON for the ONE appliance that HOSTS the hive broker
# (the single-box on-ramp) — `NMEM_EMBEDDED_EXCHANGE=1`. Mirrors EMBED_PG/EMBED_VIZ.
EMBED_EXCHANGE="${NMEM_EMBEDDED_EXCHANGE:-0}"

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

if _on "$EMBED_EXCHANGE"; then
  # Host the hive's message broker (Redis) IN this appliance — the batteries-included single-box
  # on-ramp. The broker is UNTRUSTED (it only carries sealed envelopes); the password is a COARSE
  # membership gate, generated once + persisted so restarts + peers keep the same secret.
  BROKER_PORT="${NMEX_BROKER_PORT:-6390}"
  PASS_FILE="$DATA/exchange/broker.pass"
  mkdir -p "$DATA/exchange"
  if [ -n "${NMEX_BROKER_PASSWORD:-}" ]; then
    BROKER_PASS="$NMEX_BROKER_PASSWORD"                 # operator-provided
  elif [ -s "$PASS_FILE" ]; then
    BROKER_PASS="$(cat "$PASS_FILE")"                   # reuse the persisted one across restarts
  else
    # URL-safe (alphanumeric) so it drops cleanly into redis://:PASS@host — no %-encoding needed.
    BROKER_PASS="$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32)"
    printf '%s' "$BROKER_PASS" > "$PASS_FILE"; chmod 600 "$PASS_FILE"
  fi
  # A newline can't be represented in a redis quoted-string directive — it would split requirepass
  # across lines and the broker would fail to start (silently, while the studio looks healthy). Reject
  # it up front with a clear error rather than shipping a broken broker.
  case "$BROKER_PASS" in
    *$'\n'*) echo "[nmem-studio] ERROR: NMEX_BROKER_PASSWORD must not contain a newline"; exit 1 ;;
  esac
  # redis.conf (the secret lives in this file, NOT in the supervisord config). bind 0.0.0.0 so hive
  # peers on OTHER hosts can reach it (publish the port to expose it); requirepass gates access; the
  # bus is transient so persistence is off. Create it 0600 BEFORE writing the secret (a normal umask
  # would otherwise make it world-readable to other local users, e.g. the postgres user).
  REDIS_CONF=/tmp/nmem-broker.conf
  touch "$REDIS_CONF" && chmod 600 "$REDIS_CONF"
  # Escape a custom password for the QUOTED requirepass directive (backslash first, then double-quote)
  # so a password containing " or \ can't corrupt the config or change what Redis interprets.
  _CONF_PASS=${BROKER_PASS//\\/\\\\}
  _CONF_PASS=${_CONF_PASS//\"/\\\"}
  {
    echo "port $BROKER_PORT"
    echo "bind 0.0.0.0"
    echo "protected-mode no"          # a password IS set (requirepass); bind is intentional for peers
    echo "requirepass \"$_CONF_PASS\""
    echo 'save ""'                    # transient message bus — no RDB snapshots
    echo "appendonly no"
  } > "$REDIS_CONF"
  # Point THIS agent at its own embedded broker unless an explicit NMEX_REDIS_URL is already set
  # (e.g. the operator prefers an external one). Percent-encode the password for the URL userinfo so
  # a custom password with /?#@% survives redis-py's URL parser. Exported → the studio inherits it.
  if [ -z "${NMEX_REDIS_URL:-}" ]; then
    _URL_PASS="$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "$BROKER_PASS")"
    export NMEX_REDIS_URL="redis://:${_URL_PASS}@127.0.0.1:${BROKER_PORT}/0"
  fi
  # Persist the EFFECTIVE broker URL (0600) so an out-of-band `docker exec … hive doctor` — which does
  # NOT inherit supervisord's env — can still validate the live broker. hive_doctor._cli_env reads
  # this as the lowest-precedence default (an agent's own secrets.env NMEX_REDIS_URL still wins).
  BROKER_ENV="$DATA/exchange/broker.env"
  touch "$BROKER_ENV" && chmod 600 "$BROKER_ENV"
  printf "NMEX_REDIS_URL='%s'\n" "$NMEX_REDIS_URL" > "$BROKER_ENV"
  cat >> "$CONF" <<EXCH

[program:exchange]
command=redis-server $REDIS_CONF
priority=5
autorestart=true
startretries=1000000
startsecs=3
stdout_logfile=/dev/stdout
stderr_logfile=/dev/stderr
stdout_logfile_maxbytes=0
stderr_logfile_maxbytes=0
EXCH
  echo "[nmem-studio] embedded exchange broker ON :$BROKER_PORT — password persisted at $PASS_FILE"
  echo "[nmem-studio]   peers join with:  redis://:<password>@<this-host>:$BROKER_PORT/0  (publish the port)"
else
  # If the embedded broker was disabled after a prior embedded run, drop the persisted URL so the
  # doctor (and anything else) doesn't probe a broker this container no longer hosts.
  rm -f "$DATA/exchange/broker.env" 2>/dev/null || true
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

echo "[nmem-studio] starting (embedded postgres=$EMBED_PG, viz=$EMBED_VIZ, exchange=$EMBED_EXCHANGE) — open http://localhost:${STUDIO_PORT:-8080}"
exec supervisord -c "$CONF"
