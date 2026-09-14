#!/usr/bin/env bash
# nmem-identity sidecar entrypoint. One image, two roles (IDENTITY_ROLE):
#   matcher     — the /resolve + /enrol service (needs Postgres; port 9404). DEFAULT.
#   text-embed  — the LUAR /embed sidecar (loads the model; no DB; port 9406).
# The chat-style identity path in an nmem agent calls both over HTTP (NMEM_IDENTITY_MATCHER_URL /
# NMEM_IDENTITY_TEXT_EMBED_URL). Fail-hard here (set -e): a half-provisioned sidecar must not look up.
set -euo pipefail

SVC=/src/nmem-identity/services
ROLE="${IDENTITY_ROLE:-matcher}"

if [ "$ROLE" = "text-embed" ]; then
  # The LUAR model is baked into the image (HF cache); HF_HUB_OFFLINE keeps first boot network-free.
  exec python "$SVC/text_style_embed_server.py"
fi

# matcher: the schema is NOT applied on connect (unlike nmem-sym-sensor), so provision it here.
# Idempotent: create the identity DB if absent, then apply the (CREATE ... IF NOT EXISTS) schema.
python - <<'PY'
import asyncio, os, urllib.parse
import asyncpg

dsn = os.environ.get("NMEM_IDENTITY_DSN", "postgresql://nmem:nmem@postgres:5432/nmem_identity")
parsed = urllib.parse.urlparse(dsn)
dbname = parsed.path.lstrip("/") or "nmem_identity"
admin_dsn = dsn.replace("/" + dbname, "/postgres", 1)   # maintenance connection

async def ensure_db():
    conn = None
    for _ in range(60):                                  # Postgres may still be starting
        try:
            conn = await asyncpg.connect(admin_dsn)
            break
        except Exception:                                # noqa: BLE001
            await asyncio.sleep(2)
    if conn is None:
        raise SystemExit("[identity] Postgres unreachable — cannot provision the identity DB")
    exists = await conn.fetchval("select 1 from pg_database where datname=$1", dbname)
    if not exists:
        await conn.execute(f'CREATE DATABASE "{dbname}"')
        print(f"[identity] created database {dbname}")
    await conn.close()

asyncio.run(ensure_db())
PY

# apply_schema() is idempotent and installs the pgvector extension + identity_* tables (incl. the
# text_style columns/index the chat-style fusion path needs).
nmem-identity schema-apply

exec python "$SVC/matcher_server.py"
