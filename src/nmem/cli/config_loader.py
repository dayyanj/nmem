"""
Configuration loader — resolves config from TOML files + env vars.

Priority (highest wins):
  1. Explicit overrides (from CLI flags)
  2. Environment variables (NMEM_DATABASE_URL, NMEM_EMBEDDING__PROVIDER, etc.)
  3. nmem.toml in current directory
  4. ~/.config/nmem/nmem.toml
  5. NmemConfig defaults
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from nmem.config import NmemConfig


def _find_toml() -> Path | None:
    """Find nmem.toml config file."""
    candidates = [
        Path.cwd() / "nmem.toml",
        Path.home() / ".config" / "nmem" / "nmem.toml",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def _read_toml(path: Path) -> dict[str, Any]:
    """Read a TOML file and return as a flat dict suitable for NmemConfig."""
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        try:
            import tomli as tomllib
        except ImportError:
            return {}

    with open(path, "rb") as f:
        data = tomllib.load(f)

    return data


def load_config(**overrides: Any) -> NmemConfig:
    """Load NmemConfig from TOML + env vars + explicit overrides.

    Args:
        **overrides: Explicit overrides (e.g., database_url="sqlite+aiosqlite:///nmem.db").
            None values are filtered out.

    Returns:
        Fully resolved NmemConfig instance.
    """
    kwargs: dict[str, Any] = {}

    # Layer 1: TOML file (lowest priority)
    toml_path = _find_toml()
    if toml_path:
        kwargs.update(_read_toml(toml_path))

    # Layer 2: Env vars are handled automatically by pydantic-settings

    # Layer 3: Explicit overrides (highest priority)
    for key, value in overrides.items():
        if value is not None:
            kwargs[key] = value

    return NmemConfig(**kwargs)
