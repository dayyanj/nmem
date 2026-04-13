"""
Hook configuration — loads from nmem.toml [hooks] section.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Default hook configuration
DEFAULT_HOOK_CONFIG = {
    "enabled": True,
    "capture_edits": True,
    "capture_bash": True,
    "capture_reads": False,
    "session_summary": True,
    "summary_llm": False,
    "importance_rules": "default",
    "filters": {
        "skip_paths": ["node_modules/", ".git/", "__pycache__/", ".venv/", "venv/"],
        "skip_commands": ["ls", "pwd", "cd", "echo", "cat"],
    },
}


def load_hook_config() -> dict:
    """Load hook configuration from nmem.toml or environment.

    Checks these locations in order:
    1. $NMEM_HOOKS_CONFIG (path to config file)
    2. ./nmem.toml [hooks] section
    3. ~/.config/nmem/hooks.toml
    4. Default config

    Returns:
        Dict with hook configuration.
    """
    import os

    # Check env var first
    config_path = os.environ.get("NMEM_HOOKS_CONFIG")
    if config_path:
        return _load_from_file(Path(config_path))

    # Check project-local nmem.toml
    cwd = Path(os.environ.get("CLAUDE_CWD", os.getcwd()))
    local_config = cwd / "nmem.toml"
    if local_config.exists():
        config = _load_from_file(local_config)
        if config:
            return config

    # Check user config
    user_config = Path.home() / ".config" / "nmem" / "hooks.toml"
    if user_config.exists():
        config = _load_from_file(user_config)
        if config:
            return config

    return dict(DEFAULT_HOOK_CONFIG)


def _load_from_file(path: Path) -> dict:
    """Load hook config from a TOML file."""
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            logger.debug("No TOML parser available, using defaults")
            return dict(DEFAULT_HOOK_CONFIG)

    try:
        data = tomllib.loads(path.read_text())
        hooks = data.get("hooks", {})
        # Merge with defaults
        merged = dict(DEFAULT_HOOK_CONFIG)
        merged.update(hooks)
        if "filters" in hooks:
            merged["filters"] = {**DEFAULT_HOOK_CONFIG["filters"], **hooks["filters"]}
        return merged
    except Exception as e:
        logger.debug("Failed to load hook config from %s: %s", path, e)
        return dict(DEFAULT_HOOK_CONFIG)
