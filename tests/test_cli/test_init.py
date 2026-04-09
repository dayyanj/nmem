"""Tests for nmem init command."""

from nmem.cli.main import app


def test_init_sqlite(runner, cli_env):
    result = runner.invoke(app, ["init", "--sqlite"])
    assert result.exit_code == 0
    assert "initialized" in result.output.lower()


def test_init_shows_tier_counts(runner, cli_env):
    result = runner.invoke(app, ["init", "--sqlite"])
    assert "Working" in result.output
    assert "Journal" in result.output
    assert "LTM" in result.output
    assert "Shared" in result.output


def test_init_shows_next_steps(runner, cli_env):
    result = runner.invoke(app, ["init", "--sqlite"])
    assert "nmem demo" in result.output


def test_init_idempotent(runner, cli_env):
    result1 = runner.invoke(app, ["init", "--sqlite"])
    assert result1.exit_code == 0
    result2 = runner.invoke(app, ["init", "--sqlite"])
    assert result2.exit_code == 0


def test_version(runner):
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "nmem" in result.output
    assert "0.1.0" in result.output
