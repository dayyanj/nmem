"""Tests for §1: Auto-capture hooks."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


class TestHookHandler:
    """Tests for the hook handler module."""

    def test_append_and_read_observations(self) -> None:
        """Observations can be appended and read back."""
        from nmem.hooks.handler import append_observation, read_observations, get_session_file

        with patch.dict("os.environ", {"CLAUDE_SESSION_ID": "test_session_123"}):
            session_file = get_session_file()
            # Clean up before test
            session_file.unlink(missing_ok=True)

            try:
                append_observation({"tool_name": "Edit", "summary": "Edited main.py"})
                append_observation({"tool_name": "Bash", "summary": "Ran tests"})

                observations = read_observations()
                assert len(observations) == 2
                assert observations[0]["tool_name"] == "Edit"
                assert observations[1]["tool_name"] == "Bash"
            finally:
                session_file.unlink(missing_ok=True)

    def test_read_observations_empty_file(self) -> None:
        """Reading observations from nonexistent file returns empty list."""
        from nmem.hooks.handler import read_observations

        with patch.dict("os.environ", {"CLAUDE_SESSION_ID": "nonexistent_session"}):
            observations = read_observations()
            assert observations == []

    def test_cleanup_session_file(self) -> None:
        """Cleanup removes the session file."""
        from nmem.hooks.handler import (
            append_observation, cleanup_session_file, get_session_file,
        )

        with patch.dict("os.environ", {"CLAUDE_SESSION_ID": "cleanup_test"}):
            session_file = get_session_file()
            append_observation({"test": True})
            assert session_file.exists()

            cleanup_session_file()
            assert not session_file.exists()

    def test_handle_post_tool_use_skips_reads(self) -> None:
        """PostToolUse hook skips read operations."""
        from nmem.hooks.handler import handle_post_tool_use, read_observations

        with patch.dict("os.environ", {"CLAUDE_SESSION_ID": "skip_read_test"}):
            from nmem.hooks.handler import get_session_file
            get_session_file().unlink(missing_ok=True)

            try:
                handle_post_tool_use({
                    "tool_name": "Read",
                    "tool_input": {"file_path": "/some/file.py"},
                })

                observations = read_observations()
                assert len(observations) == 0
            finally:
                get_session_file().unlink(missing_ok=True)

    def test_handle_post_tool_use_captures_edits(self) -> None:
        """PostToolUse hook captures edit operations."""
        from nmem.hooks.handler import handle_post_tool_use, read_observations

        with patch.dict("os.environ", {"CLAUDE_SESSION_ID": "capture_edit_test"}):
            from nmem.hooks.handler import get_session_file
            get_session_file().unlink(missing_ok=True)

            try:
                handle_post_tool_use({
                    "tool_name": "Edit",
                    "tool_input": {"file_path": "/src/app/main.py"},
                })

                observations = read_observations()
                assert len(observations) == 1
                assert observations[0]["tool_name"] == "Edit"
                assert observations[0]["importance"] == 5
                assert "main.py" in observations[0].get("summary", "")
            finally:
                get_session_file().unlink(missing_ok=True)

    def test_handle_post_tool_use_skips_filtered_paths(self) -> None:
        """PostToolUse hook skips paths matching filter patterns."""
        from nmem.hooks.handler import handle_post_tool_use, read_observations

        with patch.dict("os.environ", {"CLAUDE_SESSION_ID": "filter_path_test"}):
            from nmem.hooks.handler import get_session_file
            get_session_file().unlink(missing_ok=True)

            try:
                handle_post_tool_use({
                    "tool_name": "Edit",
                    "tool_input": {"file_path": "/project/node_modules/pkg/index.js"},
                })

                observations = read_observations()
                assert len(observations) == 0
            finally:
                get_session_file().unlink(missing_ok=True)

    def test_handle_post_tool_use_captures_deployment_bash(self) -> None:
        """PostToolUse hook captures deployment commands with high importance."""
        from nmem.hooks.handler import handle_post_tool_use, read_observations

        with patch.dict("os.environ", {"CLAUDE_SESSION_ID": "deploy_test"}):
            from nmem.hooks.handler import get_session_file
            get_session_file().unlink(missing_ok=True)

            try:
                handle_post_tool_use({
                    "tool_name": "Bash",
                    "tool_input": {"command": "docker compose up -d production"},
                    "tool_output": "Container started",
                })

                observations = read_observations()
                assert len(observations) == 1
                assert observations[0]["importance"] == 7
            finally:
                get_session_file().unlink(missing_ok=True)

    def test_handle_post_tool_use_respects_capture_edits_toggle(self) -> None:
        """PostToolUse hook skips edits when capture_edits=False in config."""
        from nmem.hooks.handler import handle_post_tool_use, read_observations

        with patch.dict("os.environ", {"CLAUDE_SESSION_ID": "toggle_edit_test"}):
            from nmem.hooks.handler import get_session_file
            get_session_file().unlink(missing_ok=True)

            try:
                # Patch config loader at its source module
                with patch("nmem.hooks.config.load_hook_config", return_value={
                    "enabled": True,
                    "capture_edits": False,
                    "capture_bash": True,
                    "capture_reads": False,
                    "filters": {"skip_paths": [], "skip_commands": []},
                }):
                    handle_post_tool_use({
                        "tool_name": "Edit",
                        "tool_input": {"file_path": "/src/main.py"},
                    })

                observations = read_observations()
                assert len(observations) == 0
            finally:
                get_session_file().unlink(missing_ok=True)

    def test_handle_post_tool_use_skips_reads_by_default_config(self) -> None:
        """PostToolUse hook skips reads because capture_reads defaults to False."""
        from nmem.hooks.handler import handle_post_tool_use, read_observations

        with patch.dict("os.environ", {"CLAUDE_SESSION_ID": "toggle_read_test"}):
            from nmem.hooks.handler import get_session_file
            get_session_file().unlink(missing_ok=True)

            try:
                handle_post_tool_use({
                    "tool_name": "Read",
                    "tool_input": {"file_path": "/src/main.py"},
                })

                # Skipped because default config has capture_reads=False
                observations = read_observations()
                assert len(observations) == 0
            finally:
                get_session_file().unlink(missing_ok=True)

    def test_build_summary_title_with_edits(self) -> None:
        """Summary title includes edited file names."""
        from nmem.hooks.handler import _build_summary_title

        observations = [
            {"tool_name": "Edit", "file_path": "/src/main.py"},
            {"tool_name": "Edit", "file_path": "/src/config.py"},
            {"tool_name": "Bash", "command": "pytest"},
        ]
        title = _build_summary_title(observations)
        assert "main.py" in title or "config.py" in title
        assert "3 ops" in title

    def test_build_summary_title_no_edits(self) -> None:
        """Summary title for read-only session."""
        from nmem.hooks.handler import _build_summary_title

        observations = [
            {"tool_name": "Bash", "command": "git status"},
            {"tool_name": "Bash", "command": "pytest"},
        ]
        title = _build_summary_title(observations)
        assert "2 operations" in title


class TestHookEntryPoint:
    """Tests for the hook module entry point (__main__.py)."""

    def test_unknown_hook_exits_with_error(self) -> None:
        """Running an unknown hook name exits with code 1."""
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-m", "nmem.hooks", "nonexistent"],
            capture_output=True, text=True,
            cwd="/mnt/nas_projects/apps/nmem",
        )
        assert result.returncode == 1
        assert "Unknown hook" in result.stderr

    def test_no_args_exits_with_error(self) -> None:
        """Running with no args exits with code 1."""
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-m", "nmem.hooks"],
            capture_output=True, text=True,
            cwd="/mnt/nas_projects/apps/nmem",
        )
        assert result.returncode == 1
        assert "Usage" in result.stderr


class TestHookConfig:
    """Tests for hook configuration."""

    def test_default_config(self) -> None:
        """Default config has expected structure."""
        from nmem.hooks.config import DEFAULT_HOOK_CONFIG

        assert DEFAULT_HOOK_CONFIG["enabled"] is True
        assert DEFAULT_HOOK_CONFIG["capture_edits"] is True
        assert DEFAULT_HOOK_CONFIG["capture_reads"] is False
        assert "skip_paths" in DEFAULT_HOOK_CONFIG["filters"]

    def test_load_config_defaults(self) -> None:
        """Loading config without a file returns defaults."""
        from nmem.hooks.config import load_hook_config

        with patch.dict("os.environ", {}, clear=True):
            # Use a temp dir that won't have nmem.toml
            with patch.dict("os.environ", {"CLAUDE_CWD": tempfile.gettempdir()}):
                config = load_hook_config()
                assert config["enabled"] is True
