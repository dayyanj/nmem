"""Tests for nmem demo command."""

from nmem.cli.main import app


def test_demo_runs(runner, cli_env):
    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 0
    assert "Demo complete" in result.output
    assert "Acme Corp" in result.output


def test_demo_idempotent(runner, cli_env):
    """Running demo twice doesn't fail or duplicate excessively."""
    result1 = runner.invoke(app, ["demo"])
    assert result1.exit_code == 0
    result2 = runner.invoke(app, ["demo"])
    assert result2.exit_code == 0
    assert "Demo complete" in result2.output
