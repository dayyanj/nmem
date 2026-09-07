## P0 inventory — nmem_identity (NMEM_IDENTITY_)  [dict-map idiom]

**Totals:** 15 keys (all fields) — no constants/dynamic in this idiom.

> Precedence today: built-in defaults < TOML (`$NMEM_IDENTITY_CONFIG`/`./nmem-identity.toml`) < env. Under pydantic-settings this needs a custom TOML source between defaults and env via `settings_customise_sources`. Access is `config.get("key")` → migrate to `settings.key`.

- name mismatches (rename needed): **4**
- default-ON booleans: **0** (no bools in this config)
- TOML source to preserve: **yes** (custom settings source)

### Fields

| canonical field | env today | ? | type | default | call sites | canonical env |
|---|---|:--:|---|---|:--:|---|
| `dsn` | `NMEM_IDENTITY_DSN` | ✓ | str | `'postgresql://nmem:nmem@localhost:5432/nmem_identity'` | 0 | `NMEM_IDENTITY_DSN` |
| `blob_dir` | `NMEM_IDENTITY_BLOB_DIR` | ✓ | str | `'~/.local/share/nmem-identity/blobs'` | 0 | `NMEM_IDENTITY_BLOB_DIR` |
| `matcher_port` | `NMEM_IDENTITY_MATCHER_PORT` | ✓ | int | `9404` | 0 | `NMEM_IDENTITY_MATCHER_PORT` |
| `face_embed_port` | `NMEM_IDENTITY_FACE_PORT` | ✗ | int | `9405` | 0 | `NMEM_IDENTITY_FACE_EMBED_PORT` |
| `voice_embed_port` | `NMEM_IDENTITY_VOICE_PORT` | ✗ | int | `9402` | 0 | `NMEM_IDENTITY_VOICE_EMBED_PORT` |
| `face_model_pack` | `NMEM_IDENTITY_FACE_PACK` | ✗ | str | `'buffalo_s'` | 0 | `NMEM_IDENTITY_FACE_MODEL_PACK` |
| `face_det_size` | `NMEM_IDENTITY_FACE_DET_SIZE` | ✓ | int | `640` | 0 | `NMEM_IDENTITY_FACE_DET_SIZE` |
| `tau_match` | `NMEM_IDENTITY_TAU_MATCH` | ✓ | float | `0.5` | 0 | `NMEM_IDENTITY_TAU_MATCH` |
| `margin` | `NMEM_IDENTITY_MARGIN` | ✓ | float | `0.1` | 0 | `NMEM_IDENTITY_MARGIN` |
| `min_quality` | `NMEM_IDENTITY_MIN_QUALITY` | ✓ | float | `1.5` | 0 | `NMEM_IDENTITY_MIN_QUALITY` |
| `face_tau_match` | `NMEM_IDENTITY_FACE_TAU` | ✗ | float | `0.3` | 0 | `NMEM_IDENTITY_FACE_TAU_MATCH` |
| `face_margin` | `NMEM_IDENTITY_FACE_MARGIN` | ✓ | float | `0.1` | 0 | `NMEM_IDENTITY_FACE_MARGIN` |
| `face_min_quality` | `NMEM_IDENTITY_FACE_MIN_QUALITY` | ✓ | float | `0.45` | 0 | `NMEM_IDENTITY_FACE_MIN_QUALITY` |
| `ask_recurrence` | `NMEM_IDENTITY_ASK_RECURRENCE` | ✓ | int | `3` | 0 | `NMEM_IDENTITY_ASK_RECURRENCE` |
| `viz_url` | `NMEM_IDENTITY_VIZ_URL` | ✓ | str|None | `None` | 0 | `NMEM_IDENTITY_VIZ_URL` |
