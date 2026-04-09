"""Tests for nmem consolidate command."""

from nmem.cli.main import app


def test_consolidate_runs(runner, cli_env):
    runner.invoke(app, ["init", "--sqlite"])
    result = runner.invoke(app, ["consolidate"])
    assert result.exit_code == 0
    assert "Consolidation Results" in result.output


def test_consolidate_nightly(runner, cli_env):
    runner.invoke(app, ["init", "--sqlite"])
    result = runner.invoke(app, ["consolidate", "--nightly"])
    assert result.exit_code == 0
