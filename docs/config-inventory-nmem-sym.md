## P0 inventory — nmem_sym (NMEM_SYM_)

**Totals:** 275 module symbols → **208 fields** · 56 constants · 10 dicts/sets · 1 dynamic

> Note: call-site counts are grepped from THIS repo's src/ only. A 0 does NOT mean dead — host-driven flags (BridgeConfig, drive_loop, prediction plugin) are read by the CONSUMER (michelle/DJ-AI), not by nmem-sym itself. Verify against consumers before treating as unwired.

- name mismatches (rename needed): **25**
- default-ON booleans: **24**
- chained-fallback aliases (collapse to one name): **1**
- derived-default fields (need model_validator): **4**
- unwired fields (0 call sites in src): **6**

### Fields

| line | canonical field | env today | ? | type | default | call sites | note |
|---|---|---|:--:|---|---|:--:|---|
| 10 | `db_dsn` | `NMEM_SYM_DB_DSN` | ✓ | str | `None` | 7 |  |
| 26 | `vllm_model` | `NMEM_SYM_VLLM_MODEL` | ✓ | str | `` | 10 |  |
| 29 | `embed_model` | `NMEM_SYM_EMBED_MODEL` | ✓ | str | `sentence-transformers/all-MiniLM-L6-v2` | 4 |  |
| 45 | `seed_candidate_mult` | `NMEM_SYM_SEED_CANDIDATE_MULT` | ✓ | int | `5` | 2 |  |
| 55 | `trace_persist_enabled` | `NMEM_SYM_TRACE_PERSIST` | ✗ | bool | `1` | 2 |  |
| 56 | `trace_sample_rate` | `NMEM_SYM_TRACE_SAMPLE_RATE` | ✓ | float | `1.0` | 2 |  |
| 74 | `normalize_v2` | `NMEM_SYM_NORMALIZE_V2` | ✓ | bool | `0` | 2 |  |
| 86 | `cluster_full_rescan_every` | `NMEM_SYM_CLUSTER_FULL_RESCAN_EVERY` | ✓ | int | `0` | 1 |  |
| 89 | `cluster_knn_k` | `NMEM_SYM_CLUSTER_KNN_K` | ✓ | int | `20` | 2 |  |
| 123 | `retention_enabled` | `NMEM_SYM_RETENTION_ENABLED` | ✓ | bool | `1` | 2 |  |
| 124 | `retention_batch` | `NMEM_SYM_RETENTION_BATCH` | ✓ | int | `5000` | 2 |  |
| 127 | `retention_speculative_ttl_days` | `NMEM_SYM_RETENTION_SPECULATIVE_TTL_DAYS` | ✓ | int | `21` | 1 |  |
| 128 | `retention_superseded_ttl_days` | `NMEM_SYM_RETENTION_SUPERSEDED_TTL_DAYS` | ✓ | int | `14` | 1 |  |
| 129 | `retention_grounded_ttl_days` | `NMEM_SYM_RETENTION_GROUNDED_TTL_DAYS` | ✓ | int | `90` | 1 |  |
| 133 | `retention_prediction_resolved_ttl_days` | `NMEM_SYM_RETENTION_PREDICTION_RESOLVED_TTL_DAYS` | ✓ | int | `21` | 1 |  |
| 134 | `retention_prediction_pending_grace_days` | `NMEM_SYM_RETENTION_PREDICTION_PENDING_GRACE_DAYS` | ✓ | int | `7` | 1 |  |
| 135 | `retention_prediction_pending_ttl_days` | `NMEM_SYM_RETENTION_PREDICTION_PENDING_TTL_DAYS` | ✓ | int | `30` | 1 |  |
| 138 | `retention_analogy_ttl_days` | `NMEM_SYM_RETENTION_ANALOGY_TTL_DAYS` | ✓ | int | `21` | 1 |  |
| 141 | `retention_coact_stale_days` | `NMEM_SYM_RETENTION_COACT_STALE_DAYS` | ✓ | int | `30` | 1 |  |
| 145 | `retention_pending_utterance_ttl_days` | `NMEM_SYM_RETENTION_PENDING_UTTERANCE_TTL_DAYS` | ✓ | int | `14` | 1 |  |
| 147 | `retention_pending_utterance_delivered_ttl_days` | `NMEM_SYM_RETENTION_PENDING_UTTERANCE_DELIVERED_TTL_DAYS` | ✓ | int | `30` | 1 |  |
| 168 | `hypothesis_min_hops` | `NMEM_SYM_HYPOTHESIS_MIN_HOPS` | ✓ | int | `3` | 3 |  |
| 179 | `hypothesis_auto_ground` | `NMEM_SYM_HYPOTHESIS_AUTO_GROUND` | ✓ | bool | `1` | 3 |  |
| 187 | `hypothesis_posterior_enabled` | `NMEM_SYM_HYPOTHESIS_POSTERIOR` | ✗ | bool | `0` | 3 |  |
| 190 | `hypothesis_posterior_temperature` | `NMEM_SYM_HYPOTHESIS_POSTERIOR_TEMPERATURE` | ✓ | float | `1.0` | 1 |  |
| 195 | `hypothesis_grounding_similarity` | `NMEM_SYM_HYPOTHESIS_GROUNDING_SIMILARITY` | ✓ | float | `0.6` | 2 |  |
| 200 | `hypothesis_grounding_min_age_hours` | `NMEM_SYM_HYPOTHESIS_GROUNDING_MIN_AGE_HOURS` | ✓ | int | `24` | 1 |  |
| 205 | `hypothesis_grounding_ttl_hours` | `NMEM_SYM_HYPOTHESIS_GROUNDING_TTL_HOURS` | ✓ | int | `168` | 1 |  |
| 213 | `hypothesis_hub_degree_percentile` | `NMEM_SYM_HYPOTHESIS_HUB_DEGREE_PERCENTILE` | ✓ | int | `99` | 4 |  |
| 219 | `hypothesis_hub_min_graph_size` | `NMEM_SYM_HYPOTHESIS_HUB_MIN_GRAPH_SIZE` | ✓ | int | `200` | 2 |  |
| 242 | `hypothesis_journal_dual_write` | `NMEM_SYM_HYPOTHESIS_JOURNAL_DUAL_WRITE` | ✓ | bool | `1` | 3 |  |
| 248 | `hypothesis_orphan_ttl_days` | `NMEM_SYM_HYPOTHESIS_ORPHAN_TTL_DAYS` | ✓ | int | `3` | 0 |  |
| 255 | `hypothesis_evidence_threshold` | `NMEM_SYM_HYPOTHESIS_EVIDENCE_THRESHOLD` | ✓ | int | `3` | 3 |  |
| 257 | `hypothesis_refutation_threshold` | `NMEM_SYM_HYPOTHESIS_REFUTATION_THRESHOLD` | ✓ | int | `3` | 2 |  |
| 264 | `hypothesis_abductive_enabled` | `NMEM_SYM_HYPOTHESIS_ABDUCTIVE_ENABLED` | ✓ | bool | `1` | 3 |  |
| 271 | `hypothesis_abductive_max_per_hour` | `NMEM_SYM_HYPOTHESIS_ABDUCTIVE_MAX_PER_HOUR` | ✓ | int | `20` | 3 |  |
| 277 | `hypothesis_abductive_max_hypotheses` | `NMEM_SYM_HYPOTHESIS_ABDUCTIVE_MAX_HYPOTHESES` | ✓ | int | `5` | 2 |  |
| 286 | `hypothesis_mechanistic_enabled` | `NMEM_SYM_HYPOTHESIS_MECHANISTIC_ENABLED` | ✓ | bool | `1` | 1 |  |
| 288 | `hypothesis_counterfactual_enabled` | `NMEM_SYM_HYPOTHESIS_COUNTERFACTUAL_ENABLED` | ✓ | bool | `0` | 1 |  |
| 290 | `hypothesis_exception_enabled` | `NMEM_SYM_HYPOTHESIS_EXCEPTION_ENABLED` | ✓ | bool | `1` | 0 |  |
| 292 | `hypothesis_analogical_completion_enabled` | `NMEM_SYM_HYPOTHESIS_ANALOGICAL_COMPLETION_ENABLED` | ✓ | bool | `1` | 0 |  |
| 302 | `hypothesis_competition_enabled` | `NMEM_SYM_HYPOTHESIS_COMPETITION_ENABLED` | ✓ | bool | `1` | 2 |  |
| 304 | `hypothesis_competition_jaccard` | `NMEM_SYM_HYPOTHESIS_COMPETITION_JACCARD` | ✓ | float | `0.7` | 1 |  |
| 316 | `prediction_min_confidence_to_store` | `NMEM_SYM_PREDICTION_MIN_CONFIDENCE_TO_STORE` | ✓ | float | `0.3` | 1 |  |
| 324 | `prediction_novelty_gate_enabled` | `NMEM_SYM_PREDICTION_NOVELTY_GATE` | ✗ | bool | `1` | 1 |  |
| 326 | `prediction_dreamstate_enabled` | `NMEM_SYM_PREDICTION_DREAMSTATE_ENABLED` | ✓ | bool | `1` | 1 |  |
| 332 | `prediction_adaptive_depth` | `NMEM_SYM_PREDICTION_ADAPTIVE_DEPTH` | ✓ | bool | `1` | 1 |  |
| 348 | `prediction_llm_reasoning_enabled` | `NMEM_SYM_PREDICTION_LLM_REASONING` | ✗ | bool | `0` | 1 |  |
| 349 | `prediction_llm_reasoning_threshold` | `NMEM_SYM_PREDICTION_LLM_REASONING_THRESHOLD` | ✓ | float | `0.5` | 1 |  |
| 350 | `prediction_llm_reasoning_timeout_ms` | `NMEM_SYM_PREDICTION_LLM_REASONING_TIMEOUT_MS` | ✓ | int | `5000` | 1 |  |
| 351 | `prediction_confounder_detection` | `NMEM_SYM_PREDICTION_CONFOUNDER_DETECTION` | ✓ | bool | `1` | 1 |  |
| 364 | `prediction_grounding_similarity` | `NMEM_SYM_PREDICTION_GROUNDING_SIMILARITY` | ✓ | float | `0.6` | 2 |  |
| 372 | `prediction_grounding_llm_enabled` | `NMEM_SYM_PREDICTION_GROUNDING_LLM` | ✗ | bool | `0` | 1 |  |
| 373 | `prediction_grounding_llm_candidate_similarity` | `NMEM_SYM_PREDICTION_GROUNDING_LLM_CANDIDATE_SIMILARITY` | ✓ | float | `0.25` | 1 |  |
| 381 | `sensory_db_dsn` | `NMEM_SYM_SENSORY_DB_DSN` | ✓ | str | `None` | 4 |  |
| 385 | `sensory_context_enabled` | `NMEM_SYM_SENSORY_CONTEXT` | ✗ | bool | `0` | 4 |  |
| 391 | `drives_enabled` | `NMEM_SYM_DRIVES_ENABLED` | ✓ | bool | `0` | 17 |  |
| 398 | `drives_wake_mode` | `NMEM_SYM_DRIVES_WAKE_MODE` | ✓ | str | `timer` | 4 |  |
| 409 | `drives_honest_discharge` | `NMEM_SYM_DRIVES_HONEST_DISCHARGE` | ✓ | bool | `0` | 3 |  |
| 417 | `drives_create_goals` | `NMEM_SYM_DRIVES_CREATE_GOALS` | ✓ | bool | `0` | 3 |  |
| 427 | `utility_plasticity_enabled` | `NMEM_SYM_UTILITY_PLASTICITY` | ✗ | bool | `0` | 6 |  |
| 429 | `utility_reward_alpha` | `NMEM_SYM_UTILITY_REWARD_ALPHA` | ✓ | float | `0.3` | 2 |  |
| 436 | `consolidation_enabled` | `NMEM_SYM_CONSOLIDATION_ENABLED` | ✓ | bool | `0` | 3 |  |
| 441 | `failure_memory_enabled` | `NMEM_SYM_FAILURE_MEMORY` | ✗ | bool | `0` | 5 |  |
| 446 | `self_capability_enabled` | `NMEM_SYM_SELF_CAPABILITY` | ✗ | bool | `0` | 6 |  |
| 451 | `strategy_memory_enabled` | `NMEM_SYM_STRATEGY_MEMORY` | ✗ | bool | `0` | 4 |  |
| 456 | `world_model_enabled` | `NMEM_SYM_WORLD_MODEL` | ✗ | bool | `0` | 4 |  |
| 461 | `dreamstate_gain_budget_enabled` | `NMEM_SYM_DREAMSTATE_GAIN_BUDGET` | ✗ | bool | `0` | 2 |  |
| 467 | `communication_drive_enabled` | `NMEM_SYM_COMMUNICATION_DRIVE` | ✗ | bool | `0` | 1 |  |
| 479 | `outcome_surprise_enabled` | `NMEM_SYM_OUTCOME_SURPRISE` | ✗ | bool | `0` | 4 |  |
| 482 | `outcome_surprise_alpha` | `NMEM_SYM_OUTCOME_SURPRISE_ALPHA` | ✓ | float | `0.3` | 1 |  |
| 486 | `outcome_surprise_min_samples` | `NMEM_SYM_OUTCOME_SURPRISE_MIN_SAMPLES` | ✓ | int | `3` | 1 |  |
| 490 | `outcome_surprise_floor` | `NMEM_SYM_OUTCOME_SURPRISE_FLOOR` | ✓ | float | `0.15` | 1 |  |
| 493 | `outcome_surprise_z` | `NMEM_SYM_OUTCOME_SURPRISE_Z` | ✓ | float | `2.0` | 1 |  |
| 503 | `pending_utterances_enabled` | `NMEM_SYM_PENDING_UTTERANCES` | ✗ | bool | `0` | 4 |  |
| 507 | `pending_utterance_min_relevance` | `NMEM_SYM_PENDING_UTTERANCE_MIN_RELEVANCE` | ✓ | float | `0.0` | 1 |  |
| 512 | `pending_utterance_llm_phrasing` | `NMEM_SYM_PENDING_UTTERANCE_LLM_PHRASING` | ✓ | bool | `0` | 2 |  |
| 514 | `pending_utterance_llm_timeout_ms` | `NMEM_SYM_PENDING_UTTERANCE_LLM_TIMEOUT_MS` | ✓ | int | `4000` | 1 |  |
| 526 | `recipient_id` | `NMEM_SYM_RECIPIENT_ID` | ✓ | str | `` | 4 |  |
| 527 | `recipient_name` | `NMEM_SYM_RECIPIENT_NAME` | ✓ | str | `` | 1 |  |
| 528 | `recipient_interests` | `NMEM_SYM_RECIPIENT_INTERESTS` | ✓ | list[str] | `` | 1 |  |
| 533 | `recipient_interrupt_cost` | `NMEM_SYM_RECIPIENT_INTERRUPT_COST` | ✓ | float | `0.5` | 1 |  |
| 538 | `pending_utterance_embedding_relevance` | `NMEM_SYM_PENDING_UTTERANCE_EMBEDDING_RELEVANCE` | ✓ | bool | `0` | 3 |  |
| 549 | `pending_utterance_worth_threshold` | `NMEM_SYM_PENDING_UTTERANCE_WORTH_THRESHOLD` | ✓ | float | `0.0` | 1 |  |
| 552 | `pending_dispatch_batch` | `NMEM_SYM_PENDING_DISPATCH_BATCH` | ✓ | int | `20` | 1 |  |
| 557 | `pending_utterance_context_boost` | `NMEM_SYM_PENDING_UTTERANCE_CONTEXT_BOOST` | ✓ | float | `1.0` | 2 |  |
| 569 | `pending_utterance_comms_value` | `NMEM_SYM_PENDING_UTTERANCE_COMMS_VALUE` | ✓ | bool | `0` | 1 |  |
| 571 | `pending_utterance_comms_value_min_samples` | `NMEM_SYM_PENDING_UTTERANCE_COMMS_VALUE_MIN_SAMPLES` | ✓ | int | `3` | 1 |  |
| 573 | `pending_utterance_comms_value_min` | `NMEM_SYM_PENDING_UTTERANCE_COMMS_VALUE_MIN` | ✓ | float | `0.5` | 1 |  |
| 575 | `pending_utterance_comms_value_max` | `NMEM_SYM_PENDING_UTTERANCE_COMMS_VALUE_MAX` | ✓ | float | `1.5` | 1 |  |
| 584 | `recall_drive_enabled` | `NMEM_SYM_RECALL_DRIVE` | ✗ | bool | `0` | 1 |  |
| 587 | `recall_agent_id` | `NMEM_SYM_RECALL_AGENT_ID` | ✓ | str | `default` | 1 |  |
| 595 | `concerns_enabled` | `NMEM_SYM_CONCERNS_ENABLED` | ✓ | bool | `0` | 2 |  |
| 597 | `concern_floor` | `NMEM_SYM_CONCERN_FLOOR` | ✓ | float | `0.05` | 1 |  |
| 599 | `concern_max_per_drive` | `NMEM_SYM_CONCERN_MAX_PER_DRIVE` | ✓ | int | `50` | 1 |  |
| 601 | `concern_decay` | `NMEM_SYM_CONCERN_DECAY` | ✓ | float | `0.003` | 1 |  |
| 605 | `curiosity_concerns_enabled` | `NMEM_SYM_CURIOSITY_CONCERNS` | ✗ | bool | `0` | 2 |  |
| 607 | `curiosity_concern_min_composite` | `NMEM_SYM_CURIOSITY_MIN_COMPOSITE` | ✗ | float | `0.5` | 1 |  |
| 609 | `curiosity_concern_scale` | `NMEM_SYM_CURIOSITY_SCALE` | ✗ | float | `1.0` | 1 |  |
| 611 | `curiosity_sync_interval` | `NMEM_SYM_CURIOSITY_SYNC_INTERVAL` | ✓ | float | `30.0` | 2 |  |
| 613 | `curiosity_sync_limit` | `NMEM_SYM_CURIOSITY_SYNC_LIMIT` | ✓ | int | `20` | 1 |  |
| 618 | `concern_persistence_enabled` | `NMEM_SYM_CONCERN_PERSISTENCE` | ✗ | bool | `0` | 3 |  |
| 621 | `concern_persist_interval` | `NMEM_SYM_CONCERN_PERSIST_INTERVAL` | ✓ | float | `60.0` | 2 |  |
| 626 | `concern_neglect_min_reinforced` | `NMEM_SYM_CONCERN_NEGLECT_MIN_REINFORCED` | ✓ | int | `8` | 1 |  |
| 627 | `concern_neglect_min_age_s` | `NMEM_SYM_CONCERN_NEGLECT_MIN_AGE_S` | ✓ | float | `300.0` | 1 |  |
| 628 | `concern_neglect_boost` | `NMEM_SYM_CONCERN_NEGLECT_BOOST` | ✓ | float | `0.2` | 1 |  |
| 635 | `obligations_enabled` | `NMEM_SYM_OBLIGATIONS_ENABLED` | ✓ | bool | `0` | 3 |  |
| 637 | `extrinsic_weight` | `NMEM_SYM_EXTRINSIC_WEIGHT` | ✓ | float | `1.0` | 1 |  |
| 639 | `obligation_base_pressure` | `NMEM_SYM_OBLIGATION_BASE_PRESSURE` | ✓ | float | `1.0` | 1 |  |
| 640 | `obligation_max_pressure` | `NMEM_SYM_OBLIGATION_MAX_PRESSURE` | ✓ | float | `3.0` | 1 |  |
| 643 | `obligation_tau_hours` | `NMEM_SYM_OBLIGATION_TAU_HOURS` | ✓ | float | `48.0` | 1 |  |
| 645 | `obligation_critical` | `NMEM_SYM_OBLIGATION_CRITICAL` | ✓ | float | `0.9` | 1 |  |
| 647 | `obligation_grace_hours` | `NMEM_SYM_OBLIGATION_GRACE_HOURS` | ✓ | float | `24.0` | 1 |  |
| 650 | `obligation_action_cooldown_s` | `NMEM_SYM_OBLIGATION_ACTION_COOLDOWN_S` | ✓ | float | `60.0` | 1 |  |
| 653 | `obligation_persistence_enabled` | `NMEM_SYM_OBLIGATION_PERSISTENCE` | ✗ | bool | `0` | 2 |  |
| 655 | `obligation_persist_interval` | `NMEM_SYM_OBLIGATION_PERSIST_INTERVAL` | ✓ | float | `60.0` | 1 |  |
| 658 | `obligation_impasse_boost` | `NMEM_SYM_OBLIGATION_IMPASSE_BOOST` | ✓ | float | `0.3` | 1 |  |
| 662 | `reliability_concern_threshold` | `NMEM_SYM_RELIABILITY_CONCERN_THRESHOLD` | ✓ | float | `0.6` | 1 |  |
| 663 | `reliability_min_observations` | `NMEM_SYM_RELIABILITY_MIN_OBSERVATIONS` | ✓ | int | `3` | 1 |  |
| 675 | `temperament` | `NMEM_SYM_TEMPERAMENT` | ✓ | str | `conscientious` | 1 |  |
| 685 | `conscientiousness` | `NMEM_SYM_CONSCIENTIOUSNESS` | ✓ | float | `preset[conscientiousness]` | 2 | ⚠ derived-default via _temperament_axis() — needs model_validator |
| 687 | `failure_valence_cost` | `NMEM_SYM_FAILURE_VALENCE_COST` | ✓ | float | `preset[failure_valence_cost]` | 1 | ⚠ derived-default via _temperament_axis() — needs model_validator |
| 689 | `failure_drive` | `NMEM_SYM_FAILURE_DRIVE` | ✓ | float | `preset[failure_drive]` | 1 | ⚠ derived-default via _temperament_axis() — needs model_validator |
| 691 | `self_care_floor` | `NMEM_SYM_SELF_CARE_FLOOR` | ✓ | float | `preset[self_care_floor]` | 1 | ⚠ derived-default via _temperament_axis() — needs model_validator |
| 698 | `hypotheses_enabled` | `NMEM_SYM_HYPOTHESES_ENABLED` | ✓ | bool | `1` | 1 |  |
| 702 | `schemas_enabled` | `NMEM_SYM_SCHEMAS_ENABLED` | ✓ | bool | `0` | 4 |  |
| 703 | `schema_min_instances` | `NMEM_SYM_SCHEMA_MIN_INSTANCES` | ✓ | int | `5` | 4 |  |
| 704 | `schema_min_confidence` | `NMEM_SYM_SCHEMA_MIN_CONFIDENCE` | ✓ | float | `0.3` | 1 |  |
| 705 | `schema_max_pattern_length` | `NMEM_SYM_SCHEMA_MAX_PATTERN_LENGTH` | ✓ | int | `5` | 2 |  |
| 706 | `schema_max_patterns_per_cycle` | `NMEM_SYM_SCHEMA_MAX_PATTERNS_PER_CYCLE` | ✓ | int | `20` | 2 |  |
| 710 | `analogy_enabled` | `NMEM_SYM_ANALOGY_ENABLED` | ✓ | bool | `0` | 3 |  |
| 711 | `analogy_min_score` | `NMEM_SYM_ANALOGY_MIN_SCORE` | ✓ | float | `0.6` | 1 |  |
| 712 | `analogy_max_per_cycle` | `NMEM_SYM_ANALOGY_MAX_PER_CYCLE` | ✓ | int | `10` | 1 |  |
| 721 | `analogy_max_aligned_sim` | `NMEM_SYM_ANALOGY_MAX_ALIGNED_SIM` | ✓ | float | `0.92` | 1 |  |
| 725 | `analogy_reject_alias_pairs` | `NMEM_SYM_ANALOGY_REJECT_ALIAS_PAIRS` | ✓ | bool | `1` | 1 |  |
| 731 | `self_model_enabled` | `NMEM_SYM_SELF_MODEL_ENABLED` | ✓ | bool | `0` | 3 |  |
| 732 | `self_model_min_evidence` | `NMEM_SYM_SELF_MODEL_MIN_EVIDENCE` | ✓ | int | `5` | 2 |  |
| 733 | `self_model_observation_promotion_count` | `NMEM_SYM_SELF_MODEL_PROMOTE_COUNT` | ✗ | int | `3` | 1 |  |
| 734 | `self_model_capability_threshold` | `NMEM_SYM_SELF_MODEL_CAPABILITY_THRESHOLD` | ✓ | float | `0.7` | 4 |  |
| 735 | `self_model_limitation_threshold` | `NMEM_SYM_SELF_MODEL_LIMITATION_THRESHOLD` | ✓ | float | `0.3` | 4 |  |
| 736 | `self_model_trend_window` | `NMEM_SYM_SELF_MODEL_TREND_WINDOW` | ✓ | int | `5` | 1 |  |
| 742 | `self_model_concept_clusters` | `NMEM_SYM_SELF_MODEL_CONCEPT_CLUSTERS` | ✓ | bool | `1` | 1 |  |
| 747 | `self_model_cluster_cutoff` | `NMEM_SYM_SELF_MODEL_CLUSTER_CUTOFF` | ✓ | float | `0.3` | 1 |  |
| 752 | `self_model_cluster_min_size` | `NMEM_SYM_SELF_MODEL_CLUSTER_MIN_SIZE` | ✓ | int | `3` | 1 |  |
| 765 | `self_model_cluster_min_outcome_nodes` | `NMEM_SYM_SELF_MODEL_CLUSTER_MIN_OUTCOME_NODES` | ✓ | int | `20` | 1 |  |
| 771 | `self_model_cluster_fallback_top_n` | `NMEM_SYM_SELF_MODEL_CLUSTER_FALLBACK_TOP_N` | ✓ | int | `200` | 1 |  |
| 778 | `self_model_path_observation` | `NMEM_SYM_SELF_MODEL_PATH_OBSERVATION` | ✓ | bool | `0` | 0 |  |
| 779 | `self_model_somatic_markers` | `NMEM_SYM_SELF_MODEL_SOMATIC_MARKERS` | ✓ | bool | `0` | 5 |  |
| 780 | `self_model_gap_awareness` | `NMEM_SYM_SELF_MODEL_GAP_AWARENESS` | ✓ | bool | `0` | 1 |  |
| 781 | `self_model_attention_patterns` | `NMEM_SYM_SELF_MODEL_ATTENTION_PATTERNS` | ✓ | bool | `0` | 2 |  |
| 782 | `self_model_agent_perception` | `NMEM_SYM_SELF_MODEL_AGENT_PERCEPTION` | ✓ | bool | `0` | 1 |  |
| 783 | `self_model_counterfactual` | `NMEM_SYM_SELF_MODEL_COUNTERFACTUAL` | ✓ | bool | `0` | 1 |  |
| 784 | `self_model_causal_self` | `NMEM_SYM_SELF_MODEL_CAUSAL_SELF` | ✓ | bool | `0` | 1 |  |
| 789 | `procedures_enabled` | `NMEM_SYM_PROCEDURES_ENABLED` | ✓ | bool | `0` | 6 |  |
| 790 | `procedure_min_instances` | `NMEM_SYM_PROCEDURE_MIN_INSTANCES` | ✓ | int | `3` | 2 |  |
| 791 | `procedure_min_confidence` | `NMEM_SYM_PROCEDURE_MIN_CONFIDENCE` | ✓ | float | `0.3` | 1 |  |
| 792 | `procedure_max_per_cycle` | `NMEM_SYM_PROCEDURE_MAX_PER_CYCLE` | ✓ | int | `10` | 1 |  |
| 793 | `procedure_trace_lookback_hours` | `NMEM_SYM_PROCEDURE_TRACE_LOOKBACK_HOURS` | ✓ | int | `48` | 2 |  |
| 794 | `procedure_similarity_threshold` | `NMEM_SYM_PROCEDURE_SIMILARITY_THRESHOLD` | ✓ | float | `0.5` | 2 |  |
| 795 | `procedure_llm_enrichment` | `NMEM_SYM_PROCEDURE_LLM_ENRICHMENT` | ✓ | bool | `0` | 1 |  |
| 796 | `procedure_llm_timeout_ms` | `NMEM_SYM_PROCEDURE_LLM_TIMEOUT_MS` | ✓ | int | `5000` | 1 |  |
| 797 | `procedure_max_depth` | `NMEM_SYM_PROCEDURE_MAX_DEPTH` | ✓ | int | `3` | 1 |  |
| 798 | `procedure_coverage_gap_min_edges` | `NMEM_SYM_PROCEDURE_COVERAGE_GAP_MIN_EDGES` | ✓ | int | `5` | 1 |  |
| 804 | `temporal_awareness_enabled` | `NMEM_SYM_TEMPORAL_AWARENESS_ENABLED` | ✓ | bool | `0` | 4 |  |
| 805 | `temporal_check_interval_s` | `NMEM_SYM_TEMPORAL_CHECK_INTERVAL_S` | ✓ | float | `30` | 3 |  |
| 806 | `temporal_silence_window_s` | `NMEM_SYM_TEMPORAL_SILENCE_WINDOW_S` | ✓ | float | `300` | 1 |  |
| 807 | `temporal_silence_threshold` | `NMEM_SYM_TEMPORAL_SILENCE_THRESHOLD` | ✓ | float | `0.5` | 1 |  |
| 808 | `temporal_staleness_hours` | `NMEM_SYM_TEMPORAL_STALENESS_HOURS` | ✓ | int | `48` | 1 |  |
| 809 | `temporal_staleness_min_groundedness` | `NMEM_SYM_TEMPORAL_STALENESS_MIN_GROUNDEDNESS` | ✓ | int | `3` | 1 |  |
| 810 | `temporal_backlog_pressure_per_entry` | `NMEM_SYM_TEMPORAL_BACKLOG_PRESSURE` | ✗ | float | `0.01` | 1 |  |
| 823 | `prediction_deadline_factor` | `NMEM_SYM_PREDICTION_DEADLINE_FACTOR` | ✓ | float | `2.0` | 3 | ⚠ chained-fallback (alias): ['NMEM_SYM_PREDICTION_DEADLINE_FACTOR', 'NMEM_SYM_TEMPORAL_PREDICTION_DEADLINE_FACTOR'] |
| 837 | `goals_enabled` | `NMEM_SYM_GOALS_ENABLED` | ✓ | bool | `0` | 7 |  |
| 838 | `goal_max_depth` | `NMEM_SYM_GOAL_MAX_DEPTH` | ✓ | int | `4` | 3 |  |
| 839 | `goal_impasse_threshold` | `NMEM_SYM_GOAL_IMPASSE_THRESHOLD` | ✓ | int | `3` | 3 |  |
| 840 | `goal_priority_abandon_threshold` | `NMEM_SYM_GOAL_PRIORITY_ABANDON_THRESHOLD` | ✓ | float | `0.2` | 2 |  |
| 841 | `goal_max_sub_goals` | `NMEM_SYM_GOAL_MAX_SUB_GOALS` | ✓ | int | `6` | 1 |  |
| 842 | `goal_decomposition_min_similarity` | `NMEM_SYM_GOAL_DECOMP_MIN_SIMILARITY` | ✗ | float | `0.4` | 1 |  |
| 849 | `emotion_enabled` | `NMEM_SYM_EMOTION_ENABLED` | ✓ | bool | `0` | 6 |  |
| 850 | `emotion_arousal_weight_drives` | `NMEM_SYM_EMOTION_AROUSAL_WEIGHT_DRIVES` | ✓ | float | `0.3` | 1 |  |
| 851 | `emotion_arousal_weight_errors` | `NMEM_SYM_EMOTION_AROUSAL_WEIGHT_ERRORS` | ✓ | float | `0.2` | 1 |  |
| 852 | `emotion_arousal_weight_dead_ends` | `NMEM_SYM_EMOTION_AROUSAL_WEIGHT_DEAD_ENDS` | ✓ | float | `0.15` | 1 |  |
| 853 | `emotion_arousal_weight_instability` | `NMEM_SYM_EMOTION_AROUSAL_WEIGHT_INSTABILITY` | ✓ | float | `0.35` | 1 |  |
| 856 | `emotion_arousal_weight_obligation` | `NMEM_SYM_EMOTION_AROUSAL_WEIGHT_OBLIGATION` | ✓ | float | `0.2` | 1 |  |
| 857 | `emotion_valence_weight_guilt` | `NMEM_SYM_EMOTION_VALENCE_WEIGHT_GUILT` | ✓ | float | `0.5` | 1 |  |
| 858 | `emotion_inertia` | `NMEM_SYM_EMOTION_INERTIA` | ✓ | float | `0.8` | 1 |  |
| 859 | `emotion_regulation_learning` | `NMEM_SYM_EMOTION_REGULATION_LEARNING` | ✓ | bool | `1` | 2 |  |
| 860 | `emotion_state_history_limit` | `NMEM_SYM_EMOTION_STATE_HISTORY` | ✗ | int | `1000` | 1 |  |
| 965 | `extraction_bias_causal` | `NMEM_SYM_EXTRACTION_BIAS_CAUSAL` | ✓ | bool | `1` | 4 |  |
| 971 | `extraction_require_mechanism` | `NMEM_SYM_EXTRACTION_REQUIRE_MECHANISM` | ✓ | bool | `1` | 4 |  |
| 976 | `extraction_pass2_enrich` | `NMEM_SYM_EXTRACTION_PASS2_ENRICH` | ✓ | bool | `0` | 3 |  |
| 986 | `extract_max_parallel` | `NMEM_SYM_EXTRACT_MAX_PARALLEL` | ✓ | int | `8` | 7 |  |
| 993 | `search_augmentation` | `NMEM_SYM_SEARCH_AUGMENTATION` | ✓ | bool | `1` | 1 |  |
| 994 | `extract_on_ltm_saved` | `NMEM_SYM_EXTRACT_ON_LTM_SAVED` | ✓ | bool | `1` | 1 |  |
| 995 | `cluster_on_full_cycle` | `NMEM_SYM_CLUSTER_ON_FULL_CYCLE` | ✓ | bool | `1` | 1 |  |
| 996 | `dreamstate_on_nightly` | `NMEM_SYM_DREAMSTATE_ON_NIGHTLY` | ✓ | bool | `1` | 1 |  |
| 997 | `emit_curiosity_for_hypotheses` | `NMEM_SYM_EMIT_CURIOSITY_FOR_HYPOTHESES` | ✓ | bool | `1` | 1 |  |
| 999 | `drive_tick_seconds` | `NMEM_SYM_DRIVE_TICK_SECONDS` | ✓ | float | `5.0` | 0 |  |
| 1002 | `prediction_enabled` | `NMEM_SYM_PREDICTION_ENABLED` | ✓ | bool | `0` | 0 |  |
| 1015 | `extract_max_tokens` | `NMEM_SYM_EXTRACT_MAX_TOKENS` | ✓ | int | `2048` | 2 |  |
| 1017 | `extract_enrich_max_tokens` | `NMEM_SYM_EXTRACT_ENRICH_MAX_TOKENS` | ✓ | int | `256` | 2 |  |
| 1019 | `prediction_max_tokens` | `NMEM_SYM_PREDICTION_MAX_TOKENS` | ✓ | int | `2048` | 1 |  |
| 1024 | `hypothesis_plausibility_max_tokens` | `NMEM_SYM_HYPOTHESIS_PLAUSIBILITY_MAX_TOKENS` | ✓ | int | `512` | 1 |  |
| 1026 | `hypothesis_abductive_max_tokens` | `NMEM_SYM_HYPOTHESIS_ABDUCTIVE_MAX_TOKENS` | ✓ | int | `1024` | 1 |  |
| 1028 | `hypothesis_shape_max_tokens` | `NMEM_SYM_HYPOTHESIS_SHAPE_MAX_TOKENS` | ✓ | int | `512` | 1 |  |
| 1030 | `procedural_enrich_max_tokens` | `NMEM_SYM_PROCEDURAL_ENRICH_MAX_TOKENS` | ✓ | int | `512` | 1 |  |
| 1045 | `extract_multi_turn_enabled` | `NMEM_SYM_EXTRACT_MULTI_TURN_ENABLED` | ✓ | bool | `0` | 2 |  |
| 1050 | `extract_chunk_threshold_chars` | `NMEM_SYM_EXTRACT_CHUNK_THRESHOLD_CHARS` | ✓ | int | `6000` | 3 |  |
| 1055 | `extract_chunk_max_chars` | `NMEM_SYM_EXTRACT_CHUNK_MAX_CHARS` | ✓ | int | `3000` | 3 |  |

### Dynamic sources (variable/indexed env — need a custom settings source)

- `VLLM_BACKENDS` (line 25) — ⚠ dynamic via _discover_backends() — custom source, not a scalar field

### Constants (stay module-level; NOT fields)

`EMBED_DIMENSIONS`, `MAX_ACTIVATED_NODES`, `MAX_HOPS`, `TIMEOUT_MS`, `DECAY_FACTOR`, `SEED_TOP_K`, `MIN_SEED_SIMILARITY`, `MERGE_THRESHOLD`, `ALIAS_THRESHOLD`, `LTP_INCREMENT`, `LTD_INCREMENT`, `MYELINATION_THRESHOLD`, `LTD_WEIGHT_FLOOR`, `HEBBIAN_MIN_COACTIVATIONS`, `RECORD_TRAVERSALS`, `EXPLORATION_MAX_HOPS`, `EXPLORATION_DECAY_FACTOR`, `EXPLORATION_MAX_NODES`, `EXPLORATION_TIMEOUT_MS`, `EXPLORATION_THRESHOLD_SCALE`, `DREAMSTATE_INTERVAL_HOURS`, `DREAMSTATE_HOUR_UTC`, `DREAMSTATE_MAX_SESSION_CYCLES`, `DREAMSTATE_CONVERGENCE_THRESHOLD`, `DREAMSTATE_EMPTY_CYCLE_PATIENCE`, `DREAMSTATE_SALIENCE_DECAY_FACTOR`, `DREAMSTATE_SALIENCE_FLOOR`, `DREAMSTATE_SALIENCE_STALE_DAYS`, `DREAMSTATE_HOLE_MIN_SIMILARITY`, `DREAMSTATE_HOLE_MAX_SIMILARITY`, `DREAMSTATE_HOLE_SAMPLE_MULTIPLIER`, `HYPOTHESIS_MIN_NOVELTY`, `HYPOTHESIS_MIN_PLAUSIBILITY`, `HYPOTHESIS_MAX_PER_ACTIVATION`, `PREDICTION_MAX_HOPS`, `PREDICTION_DECAY_FACTOR`, `PREDICTION_MAX_NODES`, `PREDICTION_TIMEOUT_MS`, `PREDICTION_THRESHOLD_SCALE`, `PREDICTION_UNCERTAIN_THRESHOLD`, `PREDICTION_MAX_RECENT_ACTIONS`, `PREDICTION_ACTION_LOOKBACK_HOURS`, `PREDICTION_GROUNDING_LOOKBACK_HOURS`, `PREDICTION_COARSE_MAX_HOPS`, `PREDICTION_COARSE_MAX_NODES`, `PREDICTION_COARSE_TIMEOUT_MS`, `PREDICTION_AMBIGUITY_THRESHOLD`, `PREDICTION_RISK_TRIGGERS_DEEP`, `PREDICTION_DELIBERATION_TTL_HOURS`, `PREDICTION_IMAGINE_LIMIT`, `PREDICTION_IMAGINE_MIN_CAUSAL_EDGES`, `PREDICTION_IMAGINE_MAX_ACTIVATION_COUNT`, `ANALOGY_STRUCTURAL_WEIGHT`, `ANALOGY_SEMANTIC_WEIGHT`, `ANALOGY_WEIGHT_WEIGHT`, `TEMPORAL_PREDICTION_DEADLINE_FACTOR`

### Dicts / sets / derived (stay module-level)

`HYPOTHESIS_EDGE_TYPE_WEIGHTS`, `CAUSAL_EDGE_TYPES`, `CONTRADICTING_EDGE_PAIRS`, `MECHANISM_EDGE_TYPES`, `STRUCTURAL_EDGE_TYPES`, `TEMPORAL_EDGE_TYPES`, `ASSOCIATIVE_EDGE_TYPES`, `SELF_MODEL_EDGE_TYPES`, `PROCEDURAL_EDGE_TYPES`, `DEFAULT_EDGE_TYPES`
