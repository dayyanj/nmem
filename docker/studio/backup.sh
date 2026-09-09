#!/usr/bin/env bash
# Back up / restore an nmem-studio appliance's STATE — the two things that aren't in the image:
#   * the Postgres database (the agent's memory tiers + symbol graph), and
#   * the agent config volume (/data: agent.yaml, capabilities.env, persona.yaml, secrets.env).
#
# Run from this directory (docker/studio) so `docker compose` finds the stack. Postgres must be up.
#   ./backup.sh backup [out-dir]     # → <out-dir>/nmem-studio-<db>-<UTC-timestamp>.tgz  (default ./backups)
#   ./backup.sh restore <archive>    # stop agent → recreate DB from dump → replace /data → start agent
#
# The archive CONTAINS SECRETS (secrets.env), so it is created owner-only (umask 077) — keep it that way.
set -euo pipefail
umask 077                                           # secret-bearing archive + temp dirs are owner-only

MODE="${1:-}"
DB="${NMEM_AGENT_DB:-agent_nmem}"
DC() { docker compose "$@"; }                       # run against the compose project in this dir

_require_pg() {
  DC ps --status running --services 2>/dev/null | grep -qx postgres \
    || { echo "ERROR: the 'postgres' service isn't running — 'docker compose up -d' first." >&2; exit 1; }
}

case "$MODE" in
  backup)
    _require_pg
    OUT="${2:-./backups}"; mkdir -p "$OUT"
    TS="$(date -u +%Y%m%dT%H%M%SZ)"
    WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
    echo "==> dumping database '$DB'"                # pg_dump is MVCC-consistent, so the live agent may keep running
    DC exec -T postgres pg_dump -U nmem -Fc "$DB" > "$WORK/db.dump"
    echo "==> copying agent config (/data)"
    DC cp studio:/data "$WORK/data"                  # agent.yaml + capabilities.env + persona + secrets
    ARCHIVE="$OUT/nmem-studio-${DB}-${TS}.tgz"
    tar -C "$WORK" -czf "$ARCHIVE" db.dump data
    echo "done: $ARCHIVE  ($(du -h "$ARCHIVE" | cut -f1))"
    echo "NOTE: this archive contains secrets.env — keep it protected (it was written owner-only)."
    ;;

  restore)
    _require_pg
    ARCHIVE="${2:-}"
    [ -f "$ARCHIVE" ] || { echo "usage: ./backup.sh restore <archive.tgz>" >&2; exit 1; }
    printf 'This STOPS the agent and REPLACES database "%s" and /data from %s. Continue? [y/N] ' "$DB" "$ARCHIVE"
    read -r ans; [ "$ans" = "y" ] || [ "$ans" = "Y" ] || { echo "aborted."; exit 1; }
    WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
    tar -C "$WORK" -xzf "$ARCHIVE"
    [ -f "$WORK/db.dump" ] && [ -d "$WORK/data" ] || { echo "ERROR: archive missing db.dump/data" >&2; exit 1; }
    # Validate the dump is READABLE before any destructive step — an empty/corrupt/wrong-version dump
    # must fail HERE, while the live DB is still intact, not after we've already dropped it.
    echo "==> validating the database dump"
    DC exec -T postgres pg_restore --list < "$WORK/db.dump" >/dev/null 2>&1 \
      || { echo "ERROR: db.dump is not a readable pg_restore archive — aborting (nothing changed)." >&2; exit 1; }

    echo "==> stopping the agent (no writers touch the stores mid-restore)"
    DC stop studio
    echo "==> recreating database '$DB' from scratch (clean restore, not a merge)"
    # DROP + CREATE (not pg_restore --clean) so tables added AFTER the backup are also gone — a true
    # rollback. WITH (FORCE) severs any lingering connections; the agent is already stopped.
    DC exec -T postgres psql -U nmem -d postgres -v ON_ERROR_STOP=1 \
       -c "DROP DATABASE IF EXISTS \"$DB\" WITH (FORCE)" -c "CREATE DATABASE \"$DB\""
    echo "==> loading the database dump"
    DC exec -T postgres pg_restore -U nmem -d "$DB" --no-owner < "$WORK/db.dump"
    # Replace /data: clear via a throwaway container (mounts the volume), then push the backup in with
    # `docker compose cp`. No host bind mount → this is correct against a REMOTE Docker daemon too.
    echo "==> replacing /data with the backup (clear first, so a prior agent can't linger)"
    DC run --rm --no-deps --entrypoint sh studio -c 'rm -rf /data/* /data/.[!.]* 2>/dev/null || true'
    DC cp "$WORK/data/." studio:/data
    echo "==> starting the agent"
    DC start studio
    echo "done."
    ;;

  *)
    echo "usage: ./backup.sh {backup [out-dir] | restore <archive.tgz>}" >&2
    exit 1
    ;;
esac
