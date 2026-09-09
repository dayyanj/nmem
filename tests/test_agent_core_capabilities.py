"""Pure tests for the machine-readable capability dependency map + validator."""
from nmem.agent_core.capabilities import (
    CAPABILITIES,
    check_env,
    enabled_flags,
    requires_closure,
    validate,
)


def test_complete_set_has_no_issues():
    # recall drive with all its hard deps satisfied → clean
    enabled = {
        "NMEM_SYM_DRIVES_ENABLED", "NMEM_SYM_CONCERNS_ENABLED",
        "NMEM_AUTONOMY__ENABLED", "NMEM_SYM_RECALL_DRIVE_ENABLED",
    }
    assert validate(enabled) == []


def test_missing_dep_is_flagged():
    # recall drive ON, autonomy OFF → one issue naming the missing flag
    enabled = {"NMEM_SYM_DRIVES_ENABLED", "NMEM_SYM_CONCERNS_ENABLED",
               "NMEM_SYM_RECALL_DRIVE_ENABLED"}
    issues = validate(enabled)
    assert len(issues) == 1
    assert issues[0].flag == "NMEM_SYM_RECALL_DRIVE_ENABLED"
    assert "NMEM_AUTONOMY__ENABLED" in issues[0].missing
    assert "silently no-op" in issues[0].message


def test_transitive_chain():
    # goal-enrich → create-goals → (drives + goals). Enrich alone flags create-goals.
    issues = validate({"NMEM_SYM_DRIVES_GOAL_LLM_ENRICH"})
    assert issues and issues[0].missing == ("NMEM_SYM_DRIVES_CREATE_GOALS",)


def test_requires_closure_is_transitive():
    closure = requires_closure("NMEM_SYM_DRIVES_GOAL_LLM_ENRICH")
    assert {"NMEM_SYM_DRIVES_CREATE_GOALS", "NMEM_SYM_DRIVES_ENABLED",
            "NMEM_SYM_GOALS_ENABLED"} <= closure


def test_enabled_flags_reads_env_truthily():
    env = {"NMEM_SYM_DRIVES_ENABLED": "true", "NMEM_SYM_RECALL_DRIVE_ENABLED": "1",
           "NMEM_SYM_CONCERNS_ENABLED": "false", "NMEM_SYM_DRIVES_OUTWARD_ACTIONS": "explore",
           "UNKNOWN_FLAG": "true"}
    on = enabled_flags(env)
    assert "NMEM_SYM_DRIVES_ENABLED" in on
    assert "NMEM_SYM_RECALL_DRIVE_ENABLED" in on          # "1"
    assert "NMEM_SYM_DRIVES_OUTWARD_ACTIONS" in on        # CSV non-empty
    assert "NMEM_SYM_CONCERNS_ENABLED" not in on          # "false"
    assert "UNKNOWN_FLAG" not in on                       # not a known capability


def test_check_env_end_to_end():
    env = {"NMEM_SYM_RECALL_DRIVE_ENABLED": "1"}   # everything else off
    issues = check_env(env)
    assert any(i.flag == "NMEM_SYM_RECALL_DRIVE_ENABLED" for i in issues)


def test_every_requires_target_is_a_known_capability():
    # guard against a typo'd dep that could never be satisfied
    for cap in CAPABILITIES.values():
        for req in cap.requires:
            assert req in CAPABILITIES, f"{cap.flag} requires unknown flag {req}"


def test_surfaced_curated_toggles_are_present_and_valid():
    # the user-facing default-OFF flags added in the end-state sweep are curated in (a future removal
    # or a broken requires is caught here). LLM causal reasoning depends on the prediction plugin.
    for f in ("NMEM_SYM_PREDICTION_LLM_REASONING_ENABLED",
              "NMEM_SYM_EXTRACT_MULTI_TURN_ENABLED",
              "NMEM_SYM_DREAMSTATE_GAIN_BUDGET_ENABLED"):
        assert f in CAPABILITIES, f"{f} should be surfaced in the wizard catalog"
    assert "NMEM_SYM_PREDICTION_ENABLED" in requires_closure("NMEM_SYM_PREDICTION_LLM_REASONING_ENABLED")
