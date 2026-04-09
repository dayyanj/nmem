"""Shared fixtures for CLI tests — SQLite + noop providers, no external deps."""

import os
import pytest
from typer.testing import CliRunner


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Set environment for CLI tests: SQLite + noop providers."""
    db_path = tmp_path / "nmem_test.db"
    monkeypatch.setenv("NMEM_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("NMEM_EMBEDDING__PROVIDER", "noop")
    monkeypatch.setenv("NMEM_LLM__PROVIDER", "noop")
    # Change to tmp_path so any file creation doesn't pollute project
    monkeypatch.chdir(tmp_path)
    return tmp_path
