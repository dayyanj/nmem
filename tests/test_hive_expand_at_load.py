"""Expand-at-load (P1.2) — studio_server._expand_hive_config.

A `hive:` descriptor block in the seed is expanded IN PLACE into the verbose exchange:/delegation:
config the comms/delegation factories read. Fail-open: a bad descriptor leaves config untouched.

Run: ~/sh/venv/bin/python -m pytest tests/test_hive_expand_at_load.py -q
"""
from __future__ import annotations

import json
import os

import pytest

crypto = pytest.importorskip("nmem_exchange.crypto")

from nmem.agent_core.hive_descriptor import HiveDescriptor, Member
from nmem.agent_core.studio_server import _expand_hive_config


def _seed(tmp_path, *, identity="michelle", peers=("djai",), delegation=None,
          descriptor_name="hive.yaml", write_keyfile=True, broker_env="NMEX_REDIS_URL"):
    """Write a descriptor + (optionally) the identity's keyfile into an agent dir; return
    (agent_dir, config-with-hive-block)."""
    agent_dir = tmp_path / identity
    agent_dir.mkdir(exist_ok=True)
    idns = {a: crypto.generate_identity(a) for a in (identity, *peers)}
    desc = HiveDescriptor(name="spwig-fleet", broker_url_env=broker_env,
                          members=[Member(a, idns[a].sign_pub, idns[a].box_pub)
                                   for a in (identity, *peers)])
    desc.save(str(agent_dir / descriptor_name))
    if write_keyfile:
        kf = agent_dir / f"{identity}.key.json"
        with open(kf, "w") as f:
            json.dump(idns[identity].export_secret(), f)
        os.chmod(kf, 0o600)
    hive = {"descriptor": descriptor_name, "identity": identity}
    if delegation is not None:
        hive["delegation"] = delegation
    return str(agent_dir), {"hive": hive}


def test_noop_without_descriptor(tmp_path):
    config = {"hive": {"mode": "isolated"}}   # HiveConfig-only block, no descriptor
    before = json.dumps(config, sort_keys=True)
    _expand_hive_config(config, str(tmp_path), "michelle")
    assert json.dumps(config, sort_keys=True) == before   # untouched
    assert "exchange" not in config


def test_noop_without_hive_block(tmp_path):
    config = {"backends": {}}
    _expand_hive_config(config, str(tmp_path), "michelle")
    assert "exchange" not in config


def test_expands_exchange_and_delegation(tmp_path):
    agent_dir, config = _seed(tmp_path, delegation={"enabled": True, "accepts": ["ask"]})
    _expand_hive_config(config, agent_dir, "michelle")
    ex = config["exchange"]
    assert ex["enabled"] and ex["identity"]["agent_id"] == "michelle"
    assert ex["identity"]["keyfile"] == os.path.join(agent_dir, "michelle.key.json")
    assert set(ex["keyring"]) == {"michelle", "djai"}
    assert set(ex["channels"]) == {"dm:djai:michelle"}
    assert config["delegation"]["peer_channels"] == {"djai": "dm:djai:michelle"}


def test_expanded_config_passes_doctor(tmp_path):
    from nmem.agent_core import hive_doctor as hd
    agent_dir, config = _seed(tmp_path, delegation={"enabled": True, "accepts": ["ask"]})
    config["symbol_graph"] = {"enabled": True}
    _expand_hive_config(config, agent_dir, "michelle")
    rep = hd.static_report(config, env={"NMEX_REDIS_URL": "redis://:pw@h:6390/0"})
    assert rep.ok, [c.message for c in rep.checks if c.level == hd._ERROR]
    assert not rep.problems()


def test_descriptor_overrides_handwritten_blocks(tmp_path):
    agent_dir, config = _seed(tmp_path)
    config["exchange"] = {"enabled": True, "keyring": {"stale": {}}}   # hand-written; must be replaced
    _expand_hive_config(config, agent_dir, "michelle")
    assert "stale" not in config["exchange"]["keyring"]
    assert set(config["exchange"]["keyring"]) == {"michelle", "djai"}


def test_delegation_off_clears_stale_handwritten(tmp_path):
    agent_dir, config = _seed(tmp_path)     # no delegation in the hive block
    config["delegation"] = {"enabled": True, "task_types": [{"type": "ask"}]}   # stale hand-written
    _expand_hive_config(config, agent_dir, "michelle")
    # descriptor has no delegation → the stale block is dropped entirely (absent == off)
    assert not config.get("delegation")


def test_broker_env_aliasing(tmp_path, monkeypatch):
    agent_dir, config = _seed(tmp_path, broker_env="SPWIG_BROKER_URL")
    monkeypatch.delenv("NMEX_REDIS_URL", raising=False)
    monkeypatch.setenv("SPWIG_BROKER_URL", "redis://:pw@broker:6390/0")
    _expand_hive_config(config, agent_dir, "michelle")
    assert os.environ["NMEX_REDIS_URL"] == "redis://:pw@broker:6390/0"


def test_descriptor_broker_wins_over_existing_nmex_url(tmp_path, monkeypatch):
    """A seed migrated from an old broker must follow its descriptor, not a stale NMEX_REDIS_URL
    (codex P2)."""
    agent_dir, config = _seed(tmp_path, broker_env="SPWIG_BROKER_URL")
    monkeypatch.setenv("NMEX_REDIS_URL", "redis://stale-old-broker:6390/0")
    monkeypatch.setenv("SPWIG_BROKER_URL", "redis://new-broker:6390/0")
    _expand_hive_config(config, agent_dir, "michelle")
    assert os.environ["NMEX_REDIS_URL"] == "redis://new-broker:6390/0"


def test_descriptor_broker_var_unset_clears_stale_nmex_url(tmp_path, monkeypatch):
    """If the descriptor selects FLEET_URL but it's unset, a stale NMEX_REDIS_URL must be CLEARED
    (don't connect to a broker the descriptor didn't select) — codex P2 round 2."""
    agent_dir, config = _seed(tmp_path, broker_env="FLEET_URL")
    monkeypatch.setenv("NMEX_REDIS_URL", "redis://stale:6390/0")
    monkeypatch.delenv("FLEET_URL", raising=False)
    _expand_hive_config(config, agent_dir, "michelle")
    assert "NMEX_REDIS_URL" not in os.environ   # cleared → doctor will report "no broker"


def test_legacy_channel_preserved_for_inflight_migration(tmp_path):
    """Migrating a live pair whose channel was named non-canonically (dm:michelle:djai) must keep
    that channel alongside the canonical one, so in-flight delegation tasks drain (codex P2 round 4)."""
    agent_dir, config = _seed(tmp_path)
    # simulate the live hand-written exchange with the non-canonical channel name
    config["exchange"] = {"enabled": True, "channels": {
        "dm:michelle:djai": {"policy": "encrypted", "roster": ["michelle", "djai"]}}}
    _expand_hive_config(config, agent_dir, "michelle")
    chans = config["exchange"]["channels"]
    assert "dm:djai:michelle" in chans       # canonical (new routes)
    assert "dm:michelle:djai" in chans       # legacy preserved (in-flight drains)


def test_legacy_channel_with_nonmember_not_preserved(tmp_path):
    """A legacy channel referencing a non-member must NOT be re-added (would fail the doctor)."""
    agent_dir, config = _seed(tmp_path)
    config["exchange"] = {"enabled": True, "channels": {
        "dm:michelle:stranger": {"policy": "encrypted", "roster": ["michelle", "stranger"]}}}
    _expand_hive_config(config, agent_dir, "michelle")
    assert "dm:michelle:stranger" not in config["exchange"]["channels"]


def test_comms_channel_canonicalized(tmp_path):
    """A stale non-canonical comms.channel must be rewritten so PeerExchangeSink finds it in the
    derived channels (codex P2)."""
    agent_dir, config = _seed(tmp_path)
    config["comms"] = {"channel": "dm:michelle:djai"}   # non-canonical (canonical is dm:djai:michelle)
    _expand_hive_config(config, agent_dir, "michelle")
    assert config["comms"]["channel"] == "dm:djai:michelle"
    assert config["comms"]["channel"] in config["exchange"]["channels"]


def test_fail_open_on_missing_descriptor(tmp_path):
    config = {"hive": {"descriptor": "nonexistent.yaml", "identity": "michelle"}}
    _expand_hive_config(config, str(tmp_path), "michelle")   # must not raise
    assert "exchange" not in config       # left as-is → agent runs solo


def test_failed_expansion_drops_stale_exchange(tmp_path):
    """A descriptor is authoritative: on FAILED expansion, any stale hand-written exchange must be
    dropped so the failure isn't masked (codex P2 round 3). The doctor then flags it."""
    from nmem.agent_core import hive_doctor as hd
    config = {"hive": {"descriptor": "nonexistent.yaml", "identity": "michelle"},
              "exchange": {"enabled": False}}          # stale hand-written block
    _expand_hive_config(config, str(tmp_path), "michelle")
    assert "exchange" not in config                    # dropped up front
    rep = hd.static_report(config, env={})
    assert not rep.ok
    assert any("no exchange config was derived" in c.message for c in rep.checks)


def test_fail_open_on_identity_not_member(tmp_path):
    agent_dir, config = _seed(tmp_path)
    config["hive"]["identity"] = "stranger"
    _expand_hive_config(config, agent_dir, "stranger")   # must not raise
    assert "exchange" not in config


def test_identity_defaults_to_agent_id(tmp_path):
    agent_dir, config = _seed(tmp_path)
    del config["hive"]["identity"]         # rely on the default_identity arg
    _expand_hive_config(config, agent_dir, "michelle")
    assert config["exchange"]["identity"]["agent_id"] == "michelle"
