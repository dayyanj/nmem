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
