## P0 inventory — server (NMEM_VIZ_)

**Totals:** 19 module symbols → **19 fields** · 0 constants · 0 dicts/sets · 0 dynamic

> Note: call-site counts are grepped from THIS repo's src/ only. A 0 does NOT mean dead — host-driven flags (BridgeConfig, drive_loop, prediction plugin) are read by the CONSUMER (michelle/DJ-AI), not by nmem-sym itself. Verify against consumers before treating as unwired.

- name mismatches (rename needed): **9**
- default-ON booleans: **1**
- chained-fallback aliases (collapse to one name): **0**
- derived-default fields (need model_validator): **0**
- unwired fields (0 call sites in src): **0**

### Fields

| line | canonical field | env today | ? | type | default | call sites | note |
|---|---|---|:--:|---|---|:--:|---|
| 14 | `database_url` | `NMEM_VIZ_DATABASE_URL` | ✓ | str | `postgresql://postgres:postgres@localhost:5432/nmem` | 11 |  |
| 22 | `sensory_database_url` | `NMEM_VIZ_SENSORY_DATABASE_URL` | ✓ | str | `` | 8 |  |
| 27 | `sensory_bridge_enabled` | `NMEM_VIZ_SENSORY_BRIDGE` | ✗ | bool | `1` | 4 |  |
| 28 | `sensory_bridge_poll_interval` | `NMEM_VIZ_BRIDGE_POLL_INTERVAL` | ✗ | int | `10` | 4 |  |
| 32 | `http_port` | `NMEM_VIZ_HTTP_PORT` | ✓ | int | `5174` | 3 |  |
| 37 | `upstream_ws` | `NMEM_VIZ_UPSTREAM_WS` | ✓ | str | `` | 6 |  |
| 41 | `agent_id` | `NMEM_VIZ_AGENT_ID` | ✓ | str | `agent` | 5 |  |
| 42 | `title` | `NMEM_VIZ_TITLE` | ✓ | str | `nmem-viz` | 2 |  |
| 45 | `static_dir` | `NMEM_VIZ_STATIC_DIR` | ✓ | str | `None` | 2 |  |
| 52 | `ingest_token` | `NMEM_VIZ_INGEST_TOKEN` | ✓ | str | `` | 3 |  |
| 57 | `tls_cert` | `NMEM_VIZ_TLS_CERT` | ✓ | str | `` | 3 |  |
| 58 | `tls_key` | `NMEM_VIZ_TLS_KEY` | ✓ | str | `` | 3 |  |
| 62 | `data_server_port` | `NMEM_VIZ_DATA_PORT` | ✗ | int | `5174` | 3 |  |
| 63 | `ws_bridge_port` | `NMEM_VIZ_WS_PORT` | ✗ | int | `5175` | 3 |  |
| 66 | `default_mem_limit` | `NMEM_VIZ_MEM_LIMIT` | ✗ | int | `1000` | 2 |  |
| 69 | `default_graph_node_limit` | `NMEM_VIZ_GRAPH_NODE_LIMIT` | ✗ | int | `4000` | 2 |  |
| 70 | `default_graph_edge_limit` | `NMEM_VIZ_GRAPH_EDGE_LIMIT` | ✗ | int | `8000` | 2 |  |
| 72 | `default_cognition_limit` | `NMEM_VIZ_COGNITION_LIMIT` | ✗ | int | `500` | 2 |  |
| 73 | `default_identity_limit` | `NMEM_VIZ_IDENTITY_LIMIT` | ✗ | int | `500` | 2 |  |

### Constants (stay module-level; NOT fields)

(none)

### Dicts / sets / derived (stay module-level)

(none)
