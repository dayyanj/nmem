"""Unit tests for the wizard hive-membership materialization (P2b).

``studio_server._provision_hive`` turns a wizard {action:create|join, …} intent into a working
peering config: it generates the keyfile, creates/accepts a descriptor, adds this agent, and patches
agent.yaml's compact ``hive:`` block — the same engine the CLI + dashboard use. Plus the
config_writer guarantee that the transient ``setup`` intent is never persisted.
"""
import yaml

import pytest

pytest.importorskip("fastapi")          # studio_server imports fastapi at module load
pytest.importorskip("nmem_exchange")    # keyfile generation needs the exchange crypto

from nmem.agent_core.studio_server import _provision_hive, _patch_agent_yaml_hive  # noqa: E402
from nmem.agent_core.hive_descriptor import HiveDescriptor, Member  # noqa: E402
from nmem.agent_core.config_writer import render_agent_yaml  # noqa: E402


def _agent_dir(tmp_path, name="djai", agent_yaml=None):
    d = tmp_path / name
    d.mkdir()
    (d / "agent.yaml").write_text(yaml.safe_dump(agent_yaml if agent_yaml is not None else {}))
    return d


def test_provision_create_wires_agent_yaml_and_merges_axes(tmp_path):
    d = _agent_dir(tmp_path, agent_yaml={"db": {"config_key": "djai"},
                                         "hive": {"mode": "shared_world", "graph_role": "keeper"}})
    res = _provision_hive(str(d), "djai", {"action": "create", "name": "fleet", "accepts": ["ask"]})
    assert res["ok"] is True and res["action"] == "create"
    assert (d / "hive.yaml").exists() and (d / "djai.key.json").exists()
    hv = yaml.safe_load((d / "agent.yaml").read_text())["hive"]
    assert hv["descriptor"] == "hive.yaml" and hv["identity"] == "djai"
    assert hv["keyfile"] == "djai.key.json"
    assert hv["delegation"] == {"enabled": True, "accepts": ["ask"]}
    # the peering axis merges alongside the graph-sharing axis — neither clobbers the other
    assert hv["mode"] == "shared_world" and hv["graph_role"] == "keeper"
    assert HiveDescriptor.load(str(d / "hive.yaml")).agent_ids() == ["djai"]  # I'm in the roster


def test_provision_create_default_name_and_no_delegation(tmp_path):
    d = _agent_dir(tmp_path, "solo")
    res = _provision_hive(str(d), "solo", {"action": "create"})  # no name, no accepts
    assert res["ok"] is True
    hv = yaml.safe_load((d / "agent.yaml").read_text())["hive"]
    assert "delegation" not in hv                       # accepts omitted → requester side only
    assert HiveDescriptor.load(str(d / "hive.yaml")).name == "solo-hive"  # derived default name


def test_provision_create_requester_only_when_accepts_empty(tmp_path):
    """An explicit empty accepts (the wizard's 'blank = requester-only') enables delegation with no
    worker task types, so requester routing is installed. (Codex P2 repro: blank must not silently
    disable delegation.)"""
    d = _agent_dir(tmp_path, "req")
    res = _provision_hive(str(d), "req", {"action": "create", "accepts": []})
    assert res["ok"] is True
    hv = yaml.safe_load((d / "agent.yaml").read_text())["hive"]
    assert hv["delegation"] == {"enabled": True, "accepts": []}


def test_provision_join_accepts_pasted_descriptor(tmp_path):
    from nmem_exchange import crypto
    peer = crypto.generate_identity("michelle")
    desc = HiveDescriptor(name="fleet")
    desc.upsert_member(Member("michelle", peer.sign_pub, peer.box_pub))
    text = yaml.safe_dump(desc.to_dict())
    d = _agent_dir(tmp_path)
    res = _provision_hive(str(d), "djai", {"action": "join", "descriptor_text": text})
    assert res["ok"] is True and res["bundle"]["agent_id"] == "djai"
    assert set(HiveDescriptor.load(str(d / "hive.yaml")).agent_ids()) == {"michelle", "djai"}


def test_provision_join_rejects_invalid_descriptor_and_leaves_no_key(tmp_path):
    d = _agent_dir(tmp_path)
    res = _provision_hive(str(d), "djai", {"action": "join", "descriptor_text": "foo: bar"})
    assert res["ok"] is False
    assert not (d / "djai.key.json").exists()           # nothing half-written on a bad paste


def test_provision_join_needs_descriptor_text(tmp_path):
    d = _agent_dir(tmp_path)
    assert _provision_hive(str(d), "djai", {"action": "join"})["ok"] is False


def test_provision_unknown_action(tmp_path):
    assert _provision_hive(str(tmp_path), "djai", {"action": "solo"})["ok"] is False
    assert _provision_hive(str(tmp_path), "djai", {})["ok"] is False


# ── delegation toggle patches ONLY the hive block (P1: no lossy full-config rewrite) ──

def test_patch_agent_yaml_hive_preserves_unrelated_config(tmp_path):
    """The delegation route patches hive.delegation in place, leaving comms / backends.brain.
    reasoning_effort / a custom db.env_key untouched — unlike a write_agent rebuild from _current_spec()
    which can't represent them and would drop them. (Codex round-2 P1 repro.)"""
    d = _agent_dir(tmp_path, agent_yaml={
        "comms": {"channel": "dm:djai:peer"},
        "backends": {"brain": {"provider": "openai", "url": "http://x/v1", "model": "m",
                               "reasoning_effort": "high"}},
        "db": {"config_key": "djai", "env_key": "CUSTOM_DB"},
        "hive": {"descriptor": "hive.yaml", "identity": "djai"}})
    _patch_agent_yaml_hive(str(d), {"enabled": True, "accepts": []})   # requester-only
    doc = yaml.safe_load((d / "agent.yaml").read_text())
    assert doc["hive"]["delegation"] == {"enabled": True, "accepts": []}
    assert doc["hive"]["descriptor"] == "hive.yaml" and doc["hive"]["identity"] == "djai"
    assert doc["comms"] == {"channel": "dm:djai:peer"}                 # NOT dropped
    assert doc["backends"]["brain"]["reasoning_effort"] == "high"      # NOT dropped
    assert doc["db"]["env_key"] == "CUSTOM_DB"                         # NOT replaced


def test_patch_agent_yaml_hive_preserves_file_mode(tmp_path):
    """An in-place delegation edit keeps agent.yaml's existing mode (a group-readable 0640 config isn't
    tightened to the server user's 0600). (Codex round-3 repro.)"""
    import os
    import stat
    d = _agent_dir(tmp_path, agent_yaml={"hive": {"descriptor": "hive.yaml"}})
    yp = d / "agent.yaml"
    os.chmod(yp, 0o640)
    _patch_agent_yaml_hive(str(d), {"enabled": True, "accepts": []})
    assert stat.S_IMODE(os.stat(yp).st_mode) == 0o640


def test_patch_agent_yaml_hive_removes_delegation_when_none(tmp_path):
    d = _agent_dir(tmp_path, agent_yaml={"hive": {"descriptor": "hive.yaml",
                                                  "delegation": {"enabled": True, "accepts": ["ask"]}}})
    _patch_agent_yaml_hive(str(d), None)
    assert "delegation" not in yaml.safe_load((d / "agent.yaml").read_text())["hive"]


# ── config_writer never persists the transient wizard intent ─────────────────────────

def _llm():
    return {"provider": "openai", "base_url": "http://x/v1", "model": "m"}


def test_config_writer_strips_hive_setup_keeps_durable():
    doc = yaml.safe_load(render_agent_yaml(
        agent_id="a", llm=_llm(),
        hive={"mode": "shared_world", "graph_role": "keeper", "setup": {"action": "create"}}))
    assert "setup" not in doc["hive"]
    assert doc["hive"]["mode"] == "shared_world" and doc["hive"]["graph_role"] == "keeper"


def test_config_writer_drops_hive_when_only_setup():
    doc = yaml.safe_load(render_agent_yaml(
        agent_id="a", llm=_llm(), hive={"setup": {"action": "join"}}))
    assert "hive" not in doc                             # only-transient intent → no hive block at all
