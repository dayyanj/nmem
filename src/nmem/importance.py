"""
Built-in importance classifier for auto-captured and imported entries.

Assigns realistic importance scores based on entry_type and content keywords.
The benchmark showed that flat importance (everything at 7-8) breaks the
consolidation engine — this module provides the default distribution
(~70% at 3-4, ~20% at 5-6, ~10% at 7+).

Usage:
    from nmem.importance import classify_importance

    # Auto-classify from content and entry_type
    score = classify_importance(content="Fixed the auth bug", entry_type="observation")
    # Returns: 5 (matches "fixed" keyword)

    # Entry type takes precedence over keyword matching
    score = classify_importance(content="...", entry_type="deployment")
    # Returns: 7

Configurable via nmem.toml [importance] section or NmemConfig.importance_rules.
"""

from __future__ import annotations

import re
from typing import Any


# ── Default Rules ────────────────────────────────────────────────────────────

# Entry-type based rules (highest priority — if entry_type matches, use this)
ENTRY_TYPE_RULES: dict[str, int] = {
    "session_summary": 4,
    "file_read": 2,
    "file_edit": 5,
    "test_run": 3,
    "test_failure": 6,
    "deployment": 7,
    "incident": 8,
    "architecture_decision": 8,
    "convention_established": 9,
    "observation": 3,
    "note": 3,
    "decision": 6,
    "outcome": 5,
    "lesson_learned": 6,
    "imported": 3,
    "entity_reference": 3,
}

# Keyword patterns scanned in content (applied if entry_type has no specific rule)
KEYWORD_RULES: list[tuple[list[str], int]] = [
    # High importance keywords (7)
    (
        ["never ", "always ", "must ", "critical", "breaking",
         "security", "production", "incident", "decision:"],
        7,
    ),
    # Medium importance keywords (5)
    (
        ["fixed", "implemented", "configured", "deployed", "migration",
         "refactored", "resolved", "updated"],
        5,
    ),
    # Low importance keywords (2)
    (
        ["read ", "viewed", "checked", "looked at", "browsed"],
        2,
    ),
]

DEFAULT_IMPORTANCE = 3


def classify_importance(
    content: str,
    entry_type: str | None = None,
    *,
    rules: dict[str, Any] | None = None,
) -> int:
    """Classify the importance of a memory entry.

    Priority order:
    1. Entry-type specific rule (exact match)
    2. Content keyword matching (first match wins, highest-importance rules checked first)
    3. Default (3)

    Args:
        content: The entry content to classify.
        entry_type: The entry type (e.g., "deployment", "observation").
        rules: Optional custom rules dict to override defaults. Supports keys:
            - entry_type names with int values
            - "keywords_high", "keywords_medium" dicts with "patterns" and "importance"
            - "default" int

    Returns:
        Importance score 1-10.
    """
    # Merge custom rules with defaults
    type_rules = dict(ENTRY_TYPE_RULES)
    kw_rules = list(KEYWORD_RULES)
    default = DEFAULT_IMPORTANCE

    if rules:
        # Custom entry_type overrides
        for key, val in rules.items():
            if isinstance(val, int):
                if key == "default":
                    default = val
                else:
                    type_rules[key] = val
            elif isinstance(val, dict) and "patterns" in val and "importance" in val:
                kw_rules.insert(0, (val["patterns"], val["importance"]))

    # 1. Check entry_type
    if entry_type and entry_type in type_rules:
        return type_rules[entry_type]

    # 2. Check content keywords (highest importance first)
    content_lower = content.lower()
    sorted_kw_rules = sorted(kw_rules, key=lambda r: r[1], reverse=True)
    for patterns, importance in sorted_kw_rules:
        for pattern in patterns:
            if pattern.lower() in content_lower:
                return importance

    # 3. Default
    return default


def classify_tool_importance(
    tool_name: str,
    tool_input: dict[str, Any] | None = None,
) -> int | None:
    """Classify importance for a Claude Code tool call.

    Returns None if the tool call should be skipped (too noisy).

    Args:
        tool_name: The tool name (e.g., "Read", "Edit", "Bash").
        tool_input: The tool input parameters.

    Returns:
        Importance 1-10, or None to skip capture.
    """
    tool_lower = tool_name.lower()
    input_data = tool_input or {}

    # Skip reads by default (too noisy)
    if tool_lower in ("read", "glob", "grep"):
        return None

    # Edits to production code
    if tool_lower in ("edit", "write"):
        file_path = input_data.get("file_path", "")
        if any(p in file_path for p in ["test", "spec", "__test__"]):
            return 3  # test files
        if any(p in file_path for p in [".md", ".txt", ".json", ".toml"]):
            return 4  # config/docs
        return 5  # production code

    # Bash commands
    if tool_lower == "bash":
        cmd = input_data.get("command", "")
        cmd_lower = cmd.lower()

        # Deployment commands
        if any(kw in cmd_lower for kw in ["deploy", "docker", "systemctl", "rsync"]):
            return 7

        # Test runs
        if any(kw in cmd_lower for kw in ["pytest", "test", "npm test", "make test"]):
            return 3

        # Git operations
        if any(kw in cmd_lower for kw in ["git push", "git merge"]):
            return 6
        if "git" in cmd_lower:
            return 4

        # Package management
        if any(kw in cmd_lower for kw in ["pip install", "npm install", "apt"]):
            return 5

        # Skip trivial commands
        if any(kw in cmd_lower for kw in ["ls", "pwd", "cd ", "echo", "cat "]):
            return None

        return 4  # other bash commands

    return 3  # unknown tools
