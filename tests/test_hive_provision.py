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

from nmem.agent_core.studio_server import _provision_hive  # noqa: E402
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
