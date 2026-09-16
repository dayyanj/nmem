"""Hive descriptor (P1.1) — model + expand-at-load derivation.

Pure unit tests over synthetic descriptors + real generated keypairs. The capstone test proves
:func:`expand`'s output is a VALID exchange/delegation config by running it through hive_doctor.

Run: ~/sh/venv/bin/python -m pytest tests/test_hive_descriptor.py -q
"""
from __future__ import annotations

import json
import os

import pytest

from nmem.agent_core import hive_descriptor as hdsc
from nmem.agent_core.hive_descriptor import HiveDescriptor, HiveDescriptorError, Member, expand

crypto = pytest.importorskip("nmem_exchange.crypto")


def _members(*agent_ids):
    """(descriptor-ready Members, {agent_id: Identity})."""
    idns = {a: crypto.generate_identity(a) for a in agent_ids}
    members = [Member(a, idns[a].sign_pub, idns[a].box_pub) for a in agent_ids]
    return members, idns


def _descriptor(*agent_ids, name="spwig-fleet"):
    members, idns = _members(*agent_ids)
    return HiveDescriptor(name=name, members=members), idns


# ── (de)serialization ──────────────────────────────────────────────────────────────

def test_roundtrip_dict():
    desc, _ = _descriptor("michelle", "djai")
    again = HiveDescriptor.from_dict(desc.to_dict())
    assert again.name == "spwig-fleet"
    assert again.agent_ids() == ["michelle", "djai"]
    assert again.broker_url_env == "NMEX_REDIS_URL"
    assert again.ts_window_s == 300.0


def test_from_dict_accepts_unwrapped_and_wrapped():
    desc, _ = _descriptor("a", "b")
    wrapped = desc.to_dict()                      # {"hive": {...}}
    unwrapped = wrapped["hive"]
    assert HiveDescriptor.from_dict(wrapped).name == HiveDescriptor.from_dict(unwrapped).name


def test_load_save_roundtrip(tmp_path):
    desc, _ = _descriptor("michelle", "djai")
    path = str(tmp_path / "hive.yaml")
    desc.save(path)
    loaded = HiveDescriptor.load(path)
    assert loaded.agent_ids() == ["michelle", "djai"]


# ── validation ───────────────────────────────────────────────────────────────────

def test_missing_name_errors():
    with pytest.raises(HiveDescriptorError):
        HiveDescriptor.from_dict({"members": []})


def test_duplicate_member_errors():
    members, _ = _members("michelle")
    dup = HiveDescriptor(name="x", members=members + members)
    with pytest.raises(HiveDescriptorError):
        dup.validate()


def test_malformed_pub_errors():
    with pytest.raises(HiveDescriptorError):
        Member("michelle", "not-b64!!", "also-bad").validate()


def test_members_not_a_list_errors():
    with pytest.raises(HiveDescriptorError):
        HiveDescriptor.from_dict({"name": "x", "members": "nope"})


def test_malformed_broker_url_env_errors():
    """broker.url_env must be a string — a list crashes env.get() mid-expansion (codex P2 round 3)."""
    with pytest.raises(HiveDescriptorError):
        HiveDescriptor.from_dict({"name": "x", "broker": {"url_env": ["FLEET_URL"]}, "members": []})


# ── membership ops ───────────────────────────────────────────────────────────────

def test_upsert_replaces_rekeyed_member():
    desc, _ = _descriptor("michelle", "djai")
    new = crypto.generate_identity("djai")
    desc.upsert_member(Member("djai", new.sign_pub, new.box_pub))
    assert len(desc.members) == 2                 # replaced, not appended
    assert desc.member("djai").sign_pub == new.sign_pub


def test_remove_member_drops_and_is_idempotent():
    desc, _ = _descriptor("michelle", "djai")
    assert desc.remove_member("djai") is True
    assert desc.agent_ids() == ["michelle"]
    assert desc.remove_member("djai") is False    # already gone → no-op, no error


# ── expand ───────────────────────────────────────────────────────────────────────

def test_expand_derives_keyring_and_channels():
    desc, _ = _descriptor("michelle", "djai")
    cfg = expand(desc, identity="michelle", keyfile="/data/michelle/michelle.key.json")
    ex = cfg["exchange"]
    assert ex["enabled"] and ex["identity"] == {"agent_id": "michelle",
                                                "keyfile": "/data/michelle/michelle.key.json"}
    assert set(ex["keyring"]) == {"michelle", "djai"}     # keyring incl self
    # one canonical DM per peer-pair including me
    assert set(ex["channels"]) == {"dm:djai:michelle"}
    assert ex["channels"]["dm:djai:michelle"] == {"policy": "encrypted",
                                                  "roster": ["djai", "michelle"]}
    assert "delegation" not in cfg                        # no delegation block requested


def test_expand_three_members_only_my_dms():
    desc, _ = _descriptor("a", "b", "c")
    cfg = expand(desc, identity="b", keyfile="/k")
    assert set(cfg["exchange"]["channels"]) == {"dm:a:b", "dm:b:c"}   # b's DMs only, not a↔c


def test_expand_derives_delegation():
    desc, _ = _descriptor("michelle", "djai")
    cfg = expand(desc, identity="djai", keyfile="/k",
                 delegation={"enabled": True, "accepts": ["ask"]})
    d = cfg["delegation"]
    assert d["enabled"]
    assert d["task_types"] == [{"type": "ask", "capability_class": "READ_ONLY",
                                "description": "Delegated 'ask' task"}]
    assert d["peer_channels"] == {"michelle": "dm:djai:michelle"}


def test_unexpanded_descriptor_config_is_error_not_solo():
    """hive.descriptor set but no exchange derived (expand-at-load failed) → error, not 'solo'
    (codex P2 round 2)."""
    from nmem.agent_core import hive_doctor as hd
    rep = hd.static_report({"hive": {"descriptor": "hive.yaml", "identity": "x"}}, env={})
    assert not rep.ok
    assert any("no exchange config was derived" in c.message for c in rep.checks)
    # not reported as a benign solo agent
    assert not any("Solo agent" in c.message for c in rep.checks)


def test_expand_scalar_accepts_raises():
    """`accepts: ask` (a bare string) must fail, not register task types a/s/k (codex P2 round 4)."""
    desc, _ = _descriptor("michelle", "djai")
    with pytest.raises(HiveDescriptorError):
        expand(desc, identity="michelle", keyfile="/k",
               delegation={"enabled": True, "accepts": "ask"})


def test_expand_identity_not_member_raises():
    desc, _ = _descriptor("michelle", "djai")
    with pytest.raises(HiveDescriptorError):
        expand(desc, identity="stranger", keyfile="/k")


def test_expand_requires_keyfile():
    desc, _ = _descriptor("michelle", "djai")
    with pytest.raises(HiveDescriptorError):
        expand(desc, identity="michelle", keyfile="")


# ── capstone: expanded config passes hive_doctor ───────────────────────────────────

def test_expanded_config_passes_doctor(tmp_path):
    """The whole point: a descriptor + expand yields a config the doctor certifies healthy — no
    hand-derivation, no silent misconfig. Uses a real keyfile so identity/keyring cross-checks pass."""
    from nmem.agent_core import hive_doctor as hd

    members, idns = _members("michelle", "djai")
    desc = HiveDescriptor(name="spwig-fleet", members=members)
    # write michelle's real private keyfile where expand will point
    keyfile = str(tmp_path / "michelle.key.json")
    with open(keyfile, "w") as f:
        json.dump(idns["michelle"].export_secret(), f)
    os.chmod(keyfile, 0o600)                       # else the doctor's world-readable advisory warns

    cfg = expand(desc, identity="michelle", keyfile=keyfile,
                 delegation={"enabled": True, "accepts": ["ask"]})
    cfg["symbol_graph"] = {"enabled": True}       # delegation needs a graph pool

    rep = hd.static_report(cfg, env={"NMEX_REDIS_URL": "redis://:pw@h:6390/0"})
    assert rep.ok, [c.message for c in rep.checks if c.level == hd._ERROR]
    assert not rep.problems()                     # not even a canonical-naming warning
