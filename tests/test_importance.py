"""Tests for §3: Importance classifier."""

from __future__ import annotations

from nmem.importance import classify_importance, classify_tool_importance


class TestClassifyImportance:
    """Tests for content/entry_type importance classification."""

    def test_entry_type_deployment(self) -> None:
        """Deployment entry_type gets high importance."""
        assert classify_importance("anything", entry_type="deployment") == 7

    def test_entry_type_incident(self) -> None:
        """Incident entry_type gets very high importance."""
        assert classify_importance("anything", entry_type="incident") == 8

    def test_entry_type_session_summary(self) -> None:
        """Session summary entry_type gets medium-low importance."""
        assert classify_importance("anything", entry_type="session_summary") == 4

    def test_entry_type_observation(self) -> None:
        """Observation entry_type gets low importance."""
        assert classify_importance("anything", entry_type="observation") == 3

    def test_keyword_critical(self) -> None:
        """Content with 'critical' keyword gets high importance."""
        assert classify_importance("This is a critical security issue") == 7

    def test_keyword_fixed(self) -> None:
        """Content with 'fixed' keyword gets medium importance."""
        assert classify_importance("Fixed the authentication bug") == 5

    def test_keyword_never(self) -> None:
        """Content with 'never' keyword gets high importance."""
        assert classify_importance("Never deploy on Fridays") == 7

    def test_no_keywords_default(self) -> None:
        """Content with no matching keywords gets default importance."""
        assert classify_importance("Regular log message with nothing special") == 3

    def test_entry_type_takes_precedence(self) -> None:
        """Entry type rule takes precedence over keyword matching."""
        # "critical" keyword = 7, but "session_summary" type = 4
        assert classify_importance("critical issue", entry_type="session_summary") == 4

    def test_custom_rules_override(self) -> None:
        """Custom rules override defaults."""
        custom = {"deployment": 10, "default": 1}
        assert classify_importance("anything", entry_type="deployment", rules=custom) == 10

    def test_custom_keyword_rules(self) -> None:
        """Custom keyword rules are checked."""
        custom = {
            "custom_keywords": {
                "patterns": ["urgent", "asap"],
                "importance": 9,
            }
        }
        assert classify_importance("This is urgent", rules=custom) == 9

    def test_case_insensitive_keywords(self) -> None:
        """Keyword matching is case-insensitive."""
        assert classify_importance("FIXED the bug") == 5
        assert classify_importance("Critical update") == 7

    def test_highest_keyword_wins(self) -> None:
        """When multiple keywords match, highest importance wins."""
        # Both "critical" (7) and "fixed" (5) match — 7 wins
        assert classify_importance("Fixed a critical security issue") == 7


class TestClassifyToolImportance:
    """Tests for tool-based importance classification."""

    def test_read_returns_none(self) -> None:
        """Read tool returns None (skip capture)."""
        assert classify_tool_importance("Read") is None

    def test_grep_returns_none(self) -> None:
        """Grep tool returns None (skip capture)."""
        assert classify_tool_importance("Grep") is None

    def test_edit_production_code(self) -> None:
        """Edit to production code gets importance 5."""
        result = classify_tool_importance("Edit", {"file_path": "src/app/main.py"})
        assert result == 5

    def test_edit_test_file(self) -> None:
        """Edit to test file gets importance 3."""
        result = classify_tool_importance("Edit", {"file_path": "tests/test_main.py"})
        assert result == 3

    def test_edit_config_file(self) -> None:
        """Edit to config file gets importance 4."""
        result = classify_tool_importance("Edit", {"file_path": "config.json"})
        assert result == 4

    def test_bash_deploy(self) -> None:
        """Deployment bash command gets importance 7."""
        result = classify_tool_importance("Bash", {"command": "docker compose up -d"})
        assert result == 7

    def test_bash_test(self) -> None:
        """Test bash command gets importance 3."""
        result = classify_tool_importance("Bash", {"command": "pytest tests/"})
        assert result == 3

    def test_bash_git_push(self) -> None:
        """Git push gets importance 6."""
        result = classify_tool_importance("Bash", {"command": "git push origin main"})
        assert result == 6

    def test_bash_trivial_returns_none(self) -> None:
        """Trivial commands like ls return None (skip)."""
        assert classify_tool_importance("Bash", {"command": "ls -la"}) is None
        assert classify_tool_importance("Bash", {"command": "pwd"}) is None

    def test_unknown_tool(self) -> None:
        """Unknown tools get default importance 3."""
        assert classify_tool_importance("SomeTool") == 3
