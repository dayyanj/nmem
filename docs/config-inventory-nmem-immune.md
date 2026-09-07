## P0 inventory — nmem_immune (NMEM_IMMUNE_)

**Totals:** 20 module symbols → **18 fields** · 0 constants · 2 dicts/sets · 0 dynamic

> Note: call-site counts are grepped from THIS repo's src/ only. A 0 does NOT mean dead — host-driven flags (BridgeConfig, drive_loop, prediction plugin) are read by the CONSUMER (michelle/DJ-AI), not by nmem-sym itself. Verify against consumers before treating as unwired.

- name mismatches (rename needed): **10**
- default-ON booleans: **0**
- chained-fallback aliases (collapse to one name): **0**
- derived-default fields (need model_validator): **0**
- unwired fields (0 call sites in src): **5**

### Fields

| line | canonical field | env today | ? | type | default | call sites | note |
|---|---|---|:--:|---|---|:--:|---|
| 33 | `db_dsn` | `NMEM_IMMUNE_DB_DSN` | ✓ | str | `None` | 0 |  |
| 37 | `skeptic_enabled` | `NMEM_IMMUNE_SKEPTIC_ENABLED` | ✓ | bool | `None` | 1 |  |
| 38 | `skeptic_text_overlap_threshold` | `NMEM_IMMUNE_TEXT_OVERLAP_THRESHOLD` | ✗ | float | `0.7` | 1 |  |
| 39 | `skeptic_vector_divergence_threshold` | `NMEM_IMMUNE_VECTOR_DIVERGENCE_THRESHOLD` | ✗ | float | `0.4` | 1 |  |
| 40 | `skeptic_trust_delta_threshold` | `NMEM_IMMUNE_TRUST_DELTA` | ✗ | float | `0.3` | 1 |  |
| 41 | `skeptic_poison_pattern_threshold` | `NMEM_IMMUNE_POISON_THRESHOLD` | ✗ | float | `0.6` | 1 |  |
| 42 | `skeptic_scan_limit` | `NMEM_IMMUNE_SKEPTIC_SCAN_LIMIT` | ✓ | int | `20` | 1 | ⚠ clamp min(200) — preserve via field_validator |
| 46 | `quarantine_aging_days` | `NMEM_IMMUNE_QUARANTINE_AGING_DAYS` | ✓ | int | `7` | 2 | invariant ge=1 |
| 47 | `quarantine_corroboration_min_sources` | `NMEM_IMMUNE_CORROBORATION_MIN` | ✗ | int | `2` | 0 | invariant ge=1 |
| 48 | `quarantine_expiry_days` | `NMEM_IMMUNE_QUARANTINE_EXPIRY_DAYS` | ✓ | int | `90` | 2 | invariant ge=1 |
| 52 | `drift_sample_size` | `NMEM_IMMUNE_DRIFT_SAMPLE_SIZE` | ✓ | int | `10` | 1 | ⚠ clamp min(100) — preserve via field_validator |
| 53 | `drift_stale_days` | `NMEM_IMMUNE_DRIFT_STALE_DAYS` | ✓ | int | `30` | 0 |  |
| 54 | `drift_compatibility_threshold` | `NMEM_IMMUNE_DRIFT_COMPAT_THRESHOLD` | ✗ | float | `0.6` | 1 |  |
| 58 | `antidote_max_depth` | `NMEM_IMMUNE_ANTIDOTE_MAX_DEPTH` | ✓ | int | `5` | 1 |  |
| 59 | `antidote_confidence_slash` | `NMEM_IMMUNE_CONFIDENCE_SLASH` | ✗ | float | `0.5` | 0 |  |
| 63 | `immunity_decay_rate` | `NMEM_IMMUNE_DECAY_RATE` | ✗ | float | `0.05` | 0 |  |
| 64 | `immunity_min_confidence` | `NMEM_IMMUNE_MIN_CONFIDENCE` | ✗ | float | `0.1` | 1 |  |
| 65 | `immunity_retirement_threshold` | `NMEM_IMMUNE_RETIREMENT_THRESHOLD` | ✗ | float | `0.1` | 1 |  |

### Constants (stay module-level; NOT fields)

(none)

### Dicts / sets / derived (stay module-level)

`GROUNDING_TRUST`, `SOURCE_TRUST`
