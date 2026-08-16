"""Tests for nmem init command."""

from nmem.cli.main import app


def test_init(runner, cli_env):
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert "initialized" in result.output.lower()


def test_init_shows_tier_counts(runner, cli_env):
    result = runner.invoke(app, ["init"])
    assert "Working" in result.output
    assert "Journal" in result.output
    assert "LTM" in result.output
    assert "Shared" in result.output


def test_init_shows_next_steps(runner, cli_env):
    result = runner.invoke(app, ["init"])
    assert "nmem demo" in result.output


def test_init_idempotent(runner, cli_env):
    result1 = runner.invoke(app, ["init"])
    assert result1.exit_code == 0
    result2 = runner.invoke(app, ["init"])
    assert result2.exit_code == 0


def test_version(runner):
    from nmem import __version__

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "nmem" in result.output
    assert __version__ in result.output
