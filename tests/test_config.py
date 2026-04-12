"""Tests for nmem configuration."""

from __future__ import annotations

import pytest

from nmem import NmemConfig


def test_default_config() -> None:
    """Default config loads without errors."""
    config = NmemConfig()
    assert config.embedding.provider == "noop"
    assert config.llm.provider == "noop"
    assert config.journal.default_expiry_days == 30
    assert config.consolidation.enabled is True


def test_custom_config() -> None:
    """Custom values override defaults."""
    config = NmemConfig(
        database_url="sqlite+aiosqlite:///:memory:",
        embedding={"provider": "sentence-transformers", "model": "all-MiniLM-L6-v2"},
        journal={"default_expiry_days": 60},
    )
    assert config.database_url == "sqlite+aiosqlite:///:memory:"
    assert config.embedding.provider == "sentence-transformers"
    assert config.journal.default_expiry_days == 60


def test_policy_permissions() -> None:
    """Policy writers and proposers are configured correctly."""
    config = NmemConfig(
        policy={"writers": {"admin", "system"}, "proposers": {"agent1"}},
    )
    assert "admin" in config.policy.writers
    assert "agent1" in config.policy.proposers


# ── Profile tests ────────────────────────────────────────────────────────


def test_from_profile_neutral_matches_bare_defaults() -> None:
    """Neutral profile produces the same config as NmemConfig()."""
    bare = NmemConfig()
    profiled = NmemConfig.from_profile("neutral")
    # Spot-check key fields
    assert profiled.consolidation.nightly_synthesis_min_entries == bare.consolidation.nightly_synthesis_min_entries
    assert profiled.belief.agent_trust == bare.belief.agent_trust
    assert profiled.retrospective.lookback_days == bare.retrospective.lookback_days


def test_from_profile_refinery_seeds_agent_trust() -> None:
    """Refinery profile pre-seeds agent trust for known roles."""
    config = NmemConfig.from_profile("refinery")
    trust = config.belief.agent_trust
    assert "orchestrator" in trust
    assert "critic" in trust
    assert trust["orchestrator"] > trust["writer"]  # orchestrator trusted more


def test_from_profile_refinery_nightly_min_entries() -> None:
    """Refinery profile raises nightly_synthesis_min_entries."""
    config = NmemConfig.from_profile("refinery")
    assert config.consolidation.nightly_synthesis_min_entries == 10


def test_from_profile_user_override_wins() -> None:
    """Explicit kwargs override profile defaults."""
    config = NmemConfig.from_profile(
        "refinery",
        consolidation={"nightly_synthesis_min_entries": 42},
    )
    assert config.consolidation.nightly_synthesis_min_entries == 42


def test_from_profile_user_override_deep_merges() -> None:
    """User overrides merge with profile section, not replace entirely."""
    config = NmemConfig.from_profile(
        "refinery",
        belief={"agent_trust": {"my_agent": 0.99}},
    )
    # User's agent_trust replaces the profile's entirely (dict merge)
    assert config.belief.agent_trust == {"my_agent": 0.99}
    # But other belief fields come from profile (= defaults since refinery
    # doesn't override enabled/grounding_priority)
    assert config.belief.enabled is True


def test_from_profile_unknown_falls_back_to_neutral() -> None:
    """Unknown profile name falls back to neutral (no crash)."""
    config = NmemConfig.from_profile("nonexistent_profile")
    bare = NmemConfig()
    assert config.consolidation.nightly_synthesis_min_entries == bare.consolidation.nightly_synthesis_min_entries


def test_register_custom_profile() -> None:
    """Custom profiles can be registered and used."""
    from nmem import register_profile

    register_profile("test_custom", {
        "journal": {"default_expiry_days": 7},
    })
    config = NmemConfig.from_profile("test_custom")
    assert config.journal.default_expiry_days == 7


def test_list_profiles_includes_builtins() -> None:
    """list_profiles returns at least neutral and refinery."""
    from nmem import list_profiles
    profiles = list_profiles()
    assert "neutral" in profiles
    assert "refinery" in profiles
