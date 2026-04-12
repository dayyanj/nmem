"""
Configuration profiles for common nmem deployment scenarios.

Profiles are named collections of config overrides. The ``neutral``
profile IS the bare defaults — it exists as a no-op so callers can
pass ``profile="neutral"`` explicitly without special-casing.

Usage::

    from nmem import NmemConfig

    # Neutral (default) — generic, no domain assumptions
    config = NmemConfig.from_profile("neutral", database_url="...")

    # Refinery — tuned for the Spwig multi-agent system
    config = NmemConfig.from_profile("refinery", database_url="...")

    # Custom — use any profile as a starting point, then override
    config = NmemConfig.from_profile("refinery",
        database_url="...",
        consolidation={"nightly_synthesis_hour_utc": 4},
    )
"""

from __future__ import annotations

from typing import Any


def _refinery_overrides() -> dict[str, Any]:
    """Overrides tuned for the Spwig refinery multi-agent system.

    These settings were extracted from ~6 months of production usage
    with 6-8 agents (orchestrator, researcher, writer, critic, coder,
    sales head) running against vLLM / llama.cpp backends.
    """
    return {
        "consolidation": {
            "nightly_synthesis_min_entries": 10,
        },
        "journal": {
            "default_expiry_days": 30,
            "auto_promote_importance": 7,
        },
        "ltm": {
            "staleness_days": 90,
            "shared_promote_min_agents": 2,
        },
        "belief": {
            "agent_trust": {
                "orchestrator": 0.8,
                "researcher": 0.7,
                "writer": 0.6,
                "critic": 0.7,
                "coder": 0.7,
                "sales_head": 0.6,
            },
        },
        "retrospective": {
            "lookback_days": 14,
            "min_lessons": 3,
        },
    }


# ── Profile registry ────────────────────────────────────────────────────────

_PROFILES: dict[str, dict[str, Any]] = {
    "neutral": {},       # bare defaults — generic, no domain assumptions
    "refinery": _refinery_overrides(),
}


def list_profiles() -> list[str]:
    """Return available profile names."""
    return list(_PROFILES.keys())


def get_profile_overrides(name: str) -> dict[str, Any]:
    """Return the raw override dict for a named profile.

    Returns an empty dict for unknown names (falls back to neutral).
    """
    return _PROFILES.get(name, {}).copy()


def register_profile(name: str, overrides: dict[str, Any]) -> None:
    """Register a custom profile at runtime.

    Useful for applications that want to ship their own preset::

        from nmem.profiles import register_profile
        register_profile("my_app", {
            "journal": {"default_expiry_days": 14},
            "belief": {"agent_trust": {"my_bot": 0.9}},
        })
    """
    _PROFILES[name] = overrides
