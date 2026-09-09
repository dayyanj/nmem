#!/usr/bin/env bash
# Back up / restore an nmem-studio appliance's STATE — the two things that aren't in the image:
#   * the Postgres database (the agent's memory tiers + symbol graph), and
#   * the agent config volume (/data: agent.yaml, capabilities.env, persona.yaml, secrets.env).
#
# Run from this directory (docker/studio) so `docker compose` finds the stack. The stack must be up.
#   ./backup.sh backup [out-dir]     # → <out-dir>/nmem-studio-<db>-<UTC-timestamp>.tgz  (default ./backups)
#   ./backup.sh restore <archive>    # DROP-and-restore the DB + overwrite /data from the archive
#
# The archive CONTAINS SECRETS (secrets.env) — store it somewhere at least as protected as the appliance.
set -euo pipefail

MODE="${1:-}"
DB="${NMEM_AGENT_DB:-agent_nmem}"
DC() { docker compose "$@"; }                       # run against the compose project in this dir

_require_up() {
  DC ps --status running --services 2>/dev/null | grep -qx postgres \
    || { echo "ERROR: the 'postgres' service isn't running — 'docker compose up -d' first." >&2; exit 1; }
}

case "$MODE" in
  backup)
    _require_up
    OUT="${2:-./backups}"; mkdir -p "$OUT"
    TS="$(date -u +%Y%m%dT%H%M%SZ)"
    WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
    echo "==> dumping database '$DB'"
    # -Fc = custom format → restore with pg_restore --clean (portable, compressed, selective)
    DC exec -T postgres pg_dump -U nmem -Fc "$DB" > "$WORK/db.dump"
    echo "==> copying agent config (/data)"
    DC cp studio:/data "$WORK/data"                 # agent.yaml + capabilities.env + persona + secrets
    ARCHIVE="$OUT/nmem-studio-${DB}-${TS}.tgz"
    tar -C "$WORK" -czf "$ARCHIVE" db.dump data
    echo "done: $ARCHIVE  ($(du -h "$ARCHIVE" | cut -f1))"
    echo "NOTE: this archive contains secrets.env — keep it protected."
    ;;

  restore)
    _require_up
    ARCHIVE="${2:-}"
    [ -f "$ARCHIVE" ] || { echo "usage: ./backup.sh restore <archive.tgz>" >&2; exit 1; }
    printf 'This OVERWRITES database "%s" and /data from %s. Continue? [y/N] ' "$DB" "$ARCHIVE"
    read -r ans; [ "$ans" = "y" ] || [ "$ans" = "Y" ] || { echo "aborted."; exit 1; }
    WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
    tar -C "$WORK" -xzf "$ARCHIVE"
    echo "==> restoring config (/data)"
    DC cp "$WORK/data/." studio:/data
    echo "==> restoring database '$DB' (drop existing objects, then load)"
    # --clean --if-exists drops objects first so the load is not additive; created DB if absent.
    DC exec -T postgres psql -U nmem -d postgres -c "CREATE DATABASE \"$DB\"" 2>/dev/null || true
    DC exec -T postgres pg_restore -U nmem -d "$DB" --clean --if-exists --no-owner < "$WORK/db.dump"
    echo "==> restarting the agent to pick up the restored config"
    DC restart studio
    echo "done."
    ;;

  *)
    echo "usage: ./backup.sh {backup [out-dir] | restore <archive.tgz>}" >&2
    exit 1
    ;;
esac
