"""hive doctor (P0) — read-only preflight for hive membership config.

Pure unit tests over synthetic ``exchange:``/``delegation:`` configs + a real generated keyfile in
tmp_path. No Postgres, no broker (the live broker probe is exercised only via its no-network exits).

Run: ~/sh/venv/bin/python -m pytest tests/test_hive_doctor.py -q
"""
from __future__ import annotations

import json
import os

import pytest

from nmem.agent_core import hive_doctor as hd

# nmem_exchange is an OPTIONAL dependency (not in the CI `.[cli,mcp-server,api]` extras); these tests
# generate real keypairs with it, so skip the whole module cleanly when it isn't installed rather
# than failing collection for the entire suite.
crypto = pytest.importorskip("nmem_exchange.crypto")


# ── fixtures / builders ──────────────────────────────────────────────────────────

def _write_keyfile(dirpath, agent_id, *, mode=0o600):
    idn = crypto.generate_identity(agent_id)
    path = os.path.join(str(dirpath), f"{agent_id}.key.json")
    with open(path, "w") as f:
        json.dump(idn.export_secret(), f)
    os.chmod(path, mode)
    return path, idn


def _cfg(tmp_path, *, self_id="michelle", peer_id="djai", enabled=True, deleg=True):
    """A well-formed two-member hive config for `self_id`, with a real keyfile + both pubs in the
    keyring + a canonical encrypted DM channel + an `ask` delegation route to the peer."""
    keyfile, me = _write_keyfile(tmp_path, self_id)
    peer = crypto.generate_identity(peer_id)
    ch = hd._canonical_dm(self_id, peer_id)
    config = {
        # Delegation needs a graph DB pool; real delegation seeds (michelle, djai) carry this.
        "symbol_graph": {"enabled": True},
        "exchange": {
            "enabled": enabled,
            "identity": {"agent_id": self_id, "keyfile": keyfile},
            "transport": {"kind": "redis"},
            "ts_window_s": 300,
            "keyring": {
                self_id: {"sign_pub": me.sign_pub, "box_pub": me.box_pub},
                peer_id: {"sign_pub": peer.sign_pub, "box_pub": peer.box_pub},
            },
            "channels": {ch: {"policy": "encrypted", "roster": [peer_id, self_id]}},
        },
        "delegation": {
            "enabled": deleg,
            "task_types": [{"type": "ask", "capability_class": "READ_ONLY", "description": "q"}],
            "peer_channels": {peer_id: ch},
        },
    }
    return config, me, ch


_ENV = {"NMEX_REDIS_URL": "redis://:pw@host:6390/0"}


def _by_name(rep, name):
    return [c for c in rep.checks if c.name == name]


def _errors(rep):
    return [c for c in rep.checks if c.level == hd._ERROR]


# ── happy path ───────────────────────────────────────────────────────────────────

def test_healthy_config_passes(tmp_path):
    config, _me, _ch = _cfg(tmp_path)
    rep = hd.static_report(config, env=_ENV)
    assert rep.ok, [c.message for c in _errors(rep)]
    assert rep.enabled
    assert not rep.problems()               # no warnings or errors on a clean config
    # each family reported an info
    assert {c.name for c in rep.checks} >= {"identity", "keyring", "channels", "delegation"}


# ── enablement / mode ──────────────────────────────────────────────────────────────

def test_solo_agent_is_info_only(tmp_path):
    config, _me, _ch = _cfg(tmp_path, enabled=False, deleg=False)
    rep = hd.static_report(config, env=_ENV)
    assert rep.ok
    assert not rep.enabled                   # nothing to check
    assert len(rep.checks) == 1 and rep.checks[0].name == "hive"


@pytest.mark.parametrize("field,value,check", [
    ("exchange", ["alice"], "exchange"),
    ("delegation", "on", "delegation"),
])
def test_malformed_toplevel_container_reports_not_crashes(tmp_path, field, value, check):
    """A non-mapping top-level block must be a finding, not an AttributeError that aborts the whole
    report (and discards every hive readiness item) — codex P2, round 5."""
    config, _me, _ch = _cfg(tmp_path)
    config[field] = value
    rep = hd.static_report(config, env=_ENV)   # must not raise
    assert not rep.ok
    assert any(c.level == hd._ERROR for c in _by_name(rep, check))


@pytest.mark.parametrize("field", ["keyring", "channels", "identity", "transport"])
def test_malformed_exchange_subcontainer_reports_not_crashes(tmp_path, field):
    config, _me, _ch = _cfg(tmp_path)
    config["exchange"][field] = ["not", "a", "mapping"]
    rep = hd.static_report(config, env=_ENV)   # must not raise
    assert not rep.ok


@pytest.mark.parametrize("field", ["keyring", "channels", "identity"])
def test_explicit_null_exchange_block_errors(tmp_path, field):
    """`keyring:`/`channels:`/`identity:` with no value crashes build_exchange/start — must be an
    error, distinct from an absent key (codex P2, round 11)."""
    config, _me, _ch = _cfg(tmp_path)
    config["exchange"][field] = None
    rep = hd.static_report(config, env=_ENV)
    assert not rep.ok
    assert any("present but null" in c.message for c in _by_name(rep, field))


@pytest.mark.parametrize("field", ["task_types", "peer_channels"])
def test_malformed_delegation_subcontainer_reports_not_crashes(tmp_path, field):
    config, _me, _ch = _cfg(tmp_path)
    config["delegation"][field] = "oops"
    rep = hd.static_report(config, env=_ENV)   # must not raise
    assert not rep.ok
    assert any("not a" in c.message for c in _by_name(rep, "delegation"))


def test_delegation_without_exchange_is_error(tmp_path):
    config, _me, _ch = _cfg(tmp_path, enabled=False, deleg=True)
    rep = hd.static_report(config, env=_ENV)
    assert not rep.ok
    assert any("rides the exchange bus" in c.message for c in _errors(rep))


# ── identity / keyfile ─────────────────────────────────────────────────────────────

def test_missing_keyfile_errors(tmp_path):
    config, _me, _ch = _cfg(tmp_path)
    config["exchange"]["identity"]["keyfile"] = os.path.join(str(tmp_path), "nope.json")
    rep = hd.static_report(config, env=_ENV)
    assert not rep.ok
    assert any("keyfile not found" in c.message for c in _errors(rep))


def test_keyfile_pub_mismatch_with_roster_errors(tmp_path):
    """My keyring entry advertises a DIFFERENT key than my keyfile holds → peers reject me."""
    config, _me, _ch = _cfg(tmp_path)
    other = crypto.generate_identity("michelle")
    config["exchange"]["keyring"]["michelle"] = {"sign_pub": other.sign_pub, "box_pub": other.box_pub}
    rep = hd.static_report(config, env=_ENV)
    assert not rep.ok
    assert any("do NOT match keyring" in c.message for c in _errors(rep))


def test_corrupt_keyfile_errors(tmp_path):
    config, _me, _ch = _cfg(tmp_path)
    with open(config["exchange"]["identity"]["keyfile"], "w") as f:
        f.write("{not json")
    rep = hd.static_report(config, env=_ENV)
    assert not rep.ok
    assert any("not valid JSON" in c.message for c in _by_name(rep, "identity"))


def test_world_readable_keyfile_warns_not_errors(tmp_path):
    config, _me, _ch = _cfg(tmp_path)
    os.chmod(config["exchange"]["identity"]["keyfile"], 0o644)
    rep = hd.static_report(config, env=_ENV)
    assert rep.ok                             # 0644 is the NFS workaround — not fatal
    assert any(c.level == hd._WARN and "group/other-readable" in c.message
               for c in _by_name(rep, "identity"))


# ── keyring ─────────────────────────────────────────────────────────────────────

def test_missing_self_in_keyring_errors(tmp_path):
    config, _me, _ch = _cfg(tmp_path)
    del config["exchange"]["keyring"]["michelle"]
    rep = hd.static_report(config, env=_ENV)
    assert not rep.ok
    assert any("missing my own entry" in c.message for c in _errors(rep))


def test_malformed_keyring_pub_errors(tmp_path):
    config, _me, _ch = _cfg(tmp_path)
    config["exchange"]["keyring"]["djai"]["box_pub"] = "not-base64!!"
    rep = hd.static_report(config, env=_ENV)
    assert not rep.ok
    assert any("malformed public keys" in c.message for c in _by_name(rep, "keyring"))


def test_nonmapping_keyring_entry_reports_not_crashes(tmp_path):
    """A keyring entry that's a bare string (not a {sign_pub, box_pub} mapping) must be a finding,
    not an AttributeError — including the self-entry read by _check_identity (codex P2, round 3)."""
    config, _me, _ch = _cfg(tmp_path)
    config["exchange"]["keyring"]["djai"] = "oops-a-string"
    rep = hd.static_report(config, env=_ENV)   # must not raise
    assert not rep.ok
    assert any("malformed public keys" in c.message for c in _by_name(rep, "keyring"))
    # and a malformed SELF entry must also not crash _check_identity
    config2, _me2, _ch2 = _cfg(tmp_path)
    config2["exchange"]["keyring"]["michelle"] = ["not", "a", "mapping"]
    rep2 = hd.static_report(config2, env=_ENV)  # must not raise
    assert not rep2.ok


# ── channels / roster symmetry ─────────────────────────────────────────────────────

def test_untrusted_roster_member_errors(tmp_path):
    """A channel lists a peer that's not in my keyring — I can't encrypt to / verify them."""
    config, _me, ch = _cfg(tmp_path)
    config["exchange"]["channels"][ch]["roster"] = ["michelle", "stranger"]
    rep = hd.static_report(config, env=_ENV)
    assert not rep.ok
    assert any("not in my keyring" in c.message for c in _by_name(rep, "channels"))


def test_noncanonical_dm_name_warns(tmp_path):
    config, _me, ch = _cfg(tmp_path)
    ex = config["exchange"]
    ex["channels"]["dm:weird"] = ex["channels"].pop(ch)
    config["delegation"]["peer_channels"]["djai"] = "dm:weird"
    rep = hd.static_report(config, env=_ENV)
    assert rep.ok                             # only a convention warning
    assert any("not named canonically" in c.message for c in _by_name(rep, "channels"))


def test_nonmapping_channel_reports_not_crashes(tmp_path):
    """`channels: {dm:a:b: encrypted}` makes the channel a bare string — a finding, not a crash
    (codex P2, round 4)."""
    config, _me, ch = _cfg(tmp_path)
    config["exchange"]["channels"][ch] = "encrypted"
    rep = hd.static_report(config, env=_ENV)   # must not raise
    assert not rep.ok
    assert any("not a mapping" in c.message for c in _by_name(rep, "channels"))


def test_nonlist_roster_reports_not_crashes(tmp_path):
    config, _me, ch = _cfg(tmp_path)
    config["exchange"]["channels"][ch]["roster"] = "michelle"   # a string, not a list
    rep = hd.static_report(config, env=_ENV)   # must not raise
    assert not rep.ok
    assert any("roster is not a list" in c.message for c in _by_name(rep, "channels"))


def test_not_in_any_channel_warns(tmp_path):
    config, _me, ch = _cfg(tmp_path)
    config["exchange"]["channels"][ch]["roster"] = ["djai", "someoneelse"]
    config["exchange"]["keyring"]["someoneelse"] = config["exchange"]["keyring"]["djai"]
    # remove delegation route so the only signal is the channel membership check
    config["delegation"]["enabled"] = False
    rep = hd.static_report(config, env=_ENV)
    assert any("not in any channel roster" in c.message for c in _by_name(rep, "channels"))


# ── delegation ─────────────────────────────────────────────────────────────────────

def test_invalid_capability_class_errors(tmp_path):
    config, _me, _ch = _cfg(tmp_path)
    config["delegation"]["task_types"][0]["capability_class"] = "SUPERPOWER"
    rep = hd.static_report(config, env=_ENV)
    assert not rep.ok
    assert any("invalid capability_class" in c.message for c in _by_name(rep, "delegation"))


def test_lowercase_capability_class_errors(tmp_path):
    """The runtime gate is case-sensitive: 'read_only' is NOT READ_ONLY and gets denied. The doctor
    must flag it, not silently normalize it (codex P2)."""
    config, _me, _ch = _cfg(tmp_path)
    config["delegation"]["task_types"][0]["capability_class"] = "read_only"
    rep = hd.static_report(config, env=_ENV)
    assert not rep.ok
    assert any("CASE-SENSITIVE" in c.message for c in _by_name(rep, "delegation"))


def test_absent_capability_class_ok(tmp_path):
    """An absent key defaults to READ_ONLY in the studio factory — so the doctor must NOT flag it."""
    config, _me, _ch = _cfg(tmp_path)
    del config["delegation"]["task_types"][0]["capability_class"]
    rep = hd.static_report(config, env=_ENV)
    assert rep.ok, [c.message for c in _errors(rep)]


def test_mutating_without_approver_warns(tmp_path):
    config, _me, _ch = _cfg(tmp_path)
    config["delegation"]["task_types"][0]["capability_class"] = "MUTATING"
    rep = hd.static_report(config, env=_ENV)
    assert rep.ok
    assert any("fail-closed" in c.message for c in _by_name(rep, "delegation"))


def test_delegation_route_to_multimember_channel_errors(tmp_path):
    """A route over a 3-member channel double-executes (DelegationClient sends no target field) —
    the doctor must require exactly [self, target] (codex P2, round 6)."""
    config, _me, ch = _cfg(tmp_path)
    config["exchange"]["channels"][ch]["roster"] = ["michelle", "djai", "eve"]
    config["exchange"]["keyring"]["eve"] = config["exchange"]["keyring"]["djai"]
    rep = hd.static_report(config, env=_ENV)
    assert not rep.ok
    assert any("2-member channel of" in c.message for c in _by_name(rep, "delegation"))


def test_nonstring_roster_member_reports_not_crashes(tmp_path):
    """A roster like [michelle, 123] must be a finding, not a TypeError on sort/join/lookup
    (codex P2, round 6)."""
    config, _me, ch = _cfg(tmp_path)
    config["exchange"]["channels"][ch]["roster"] = ["michelle", 123]
    rep = hd.static_report(config, env=_ENV)   # must not raise
    assert not rep.ok
    assert any("non-string members" in c.message for c in _by_name(rep, "channels"))


def test_peer_channel_to_missing_channel_errors(tmp_path):
    config, _me, _ch = _cfg(tmp_path)
    config["delegation"]["peer_channels"]["djai"] = "dm:does:not:exist"
    rep = hd.static_report(config, env=_ENV)
    assert not rep.ok
    assert any("not a defined exchange channel" in c.message for c in _by_name(rep, "delegation"))


def test_delegation_enabled_but_empty_warns(tmp_path):
    config, _me, _ch = _cfg(tmp_path)
    config["delegation"]["task_types"] = []
    config["delegation"]["peer_channels"] = {}
    rep = hd.static_report(config, env=_ENV)
    assert any("it does nothing" in c.message for c in _by_name(rep, "delegation"))


def test_delegation_under_shared_world_errors(tmp_path):
    """AgentRuntime._wire_delegation() refuses delegation on a shared graph DB (codex P2)."""
    config, _me, _ch = _cfg(tmp_path)
    config["hive"] = {"mode": "shared_world"}
    rep = hd.static_report(config, env=_ENV)
    assert not rep.ok
    assert any("shared_world" in c.message for c in _by_name(rep, "delegation"))


def test_delegation_without_graph_pool_errors(tmp_path):
    """No graph pool (symbol_graph.enabled=false) → the runtime skips delegation (codex P2)."""
    config, _me, _ch = _cfg(tmp_path)
    config["symbol_graph"] = {"enabled": False}
    rep = hd.static_report(config, env=_ENV)
    assert not rep.ok
    assert any("symbol graph is off" in c.message for c in _by_name(rep, "delegation"))


def test_delegation_absent_graph_block_errors(tmp_path):
    """build_symbol_graph() defaults enabled=False → no pool. An ABSENT symbol_graph block therefore
    means delegation won't wire, and the doctor must flag it (codex P2, round 3)."""
    config, _me, _ch = _cfg(tmp_path)
    del config["symbol_graph"]
    rep = hd.static_report(config, env=_ENV)
    assert not rep.ok
    assert any("symbol graph is off" in c.message for c in _by_name(rep, "delegation"))


@pytest.mark.parametrize("block", ["hive", "symbol_graph"])
def test_malformed_delegation_prereq_block_reports_not_crashes(tmp_path, block):
    """`hive: [isolated]` / `symbol_graph: [enabled]` must be findings, not AttributeErrors (codex
    P2, round 8)."""
    config, _me, _ch = _cfg(tmp_path)
    config[block] = ["oops"]
    rep = hd.static_report(config, env=_ENV)   # must not raise
    assert not rep.ok


def test_nonstring_task_type_name_errors(tmp_path):
    """`type: [ask]` is unhashable → TaskTypeRegistry construction crashes; require a string
    (codex P2, round 8)."""
    config, _me, _ch = _cfg(tmp_path)
    config["delegation"]["task_types"][0]["type"] = ["ask"]
    rep = hd.static_report(config, env=_ENV)   # must not raise
    assert not rep.ok
    assert any("invalid 'type'" in c.message for c in _by_name(rep, "delegation"))


def test_malformed_task_entry_reports_not_crashes(tmp_path):
    """`task_types: [ask]` (a bare string, not a mapping) must be a finding, not an AttributeError."""
    config, _me, _ch = _cfg(tmp_path)
    config["delegation"]["task_types"] = ["ask"]
    rep = hd.static_report(config, env=_ENV)   # must not raise
    assert not rep.ok
    assert any("not a mapping" in c.message for c in _by_name(rep, "delegation"))


# ── broker config (static) ─────────────────────────────────────────────────────────

def test_redis_transport_without_url_errors(tmp_path):
    config, _me, _ch = _cfg(tmp_path)
    rep = hd.static_report(config, env={})   # NMEX_REDIS_URL unset
    assert not rep.ok
    assert any("NMEX_REDIS_URL is unset" in c.message for c in _by_name(rep, "broker"))


def test_nonpositive_ts_window_warns(tmp_path):
    config, _me, _ch = _cfg(tmp_path)
    config["exchange"]["ts_window_s"] = 0
    rep = hd.static_report(config, env=_ENV)
    assert any(c.name == "skew" and c.level == hd._WARN for c in rep.checks)


def test_unparseable_ts_window_errors(tmp_path):
    """A non-number ts_window_s crashes build_exchange() — must be an error, not a warning
    (codex P2, round 7)."""
    config, _me, _ch = _cfg(tmp_path)
    config["exchange"]["ts_window_s"] = "oops"
    rep = hd.static_report(config, env=_ENV)
    assert not rep.ok
    assert any(c.name == "skew" and c.level == hd._ERROR for c in rep.checks)


def test_nonstring_route_value_reports_not_crashes(tmp_path):
    """peer_channels: {djai: ['dm:…']} — a non-string route value must be a finding, not a TypeError
    on the channels.get() lookup (codex P2, round 7)."""
    config, _me, ch = _cfg(tmp_path)
    config["delegation"]["peer_channels"]["djai"] = [ch]
    rep = hd.static_report(config, env=_ENV)   # must not raise
    assert not rep.ok
    assert any("not a channel-id string" in c.message for c in _by_name(rep, "delegation"))


@pytest.mark.asyncio
async def test_explicit_null_transport_errors_and_not_overwritten(tmp_path):
    """`transport: null` crashes peer._resolve() at start — must be an error, AND the live probe must
    not overwrite that error with a 'reachable' result (codex P2, round 10)."""
    config, _me, _ch = _cfg(tmp_path)
    config["exchange"]["transport"] = None
    rep = hd.static_report(config, env=_ENV)
    assert not rep.ok
    assert any("present but null" in c.message for c in _by_name(rep, "broker"))
    # live path preserves the error (check_broker returns None → no overwrite)
    assert await hd.check_broker(config, env=_ENV) is None
    rep2 = await hd.run(config, env=_ENV)
    assert any("present but null" in c.message for c in _by_name(rep2, "broker"))


def test_unknown_transport_kind_errors(tmp_path):
    config, _me, _ch = _cfg(tmp_path)
    config["exchange"]["transport"]["kind"] = "redsi"   # typo build_transport() would reject
    rep = hd.static_report(config, env=_ENV)
    assert not rep.ok
    assert any("unknown exchange transport kind" in c.message for c in _by_name(rep, "broker"))


def test_directory_keyfile_reports_not_crashes(tmp_path):
    """A directory at the keyfile path passes exists() but open() raises IsADirectoryError — it must
    become a finding, not an uncaught traceback (codex P2)."""
    config, _me, _ch = _cfg(tmp_path)
    config["exchange"]["identity"]["keyfile"] = str(tmp_path)   # a directory
    rep = hd.static_report(config, env=_ENV)      # must not raise
    assert not rep.ok
    assert any("could not be read" in c.message for c in _by_name(rep, "identity"))


# ── live probe exits (no network) ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_broker_none_when_exchange_off(tmp_path):
    config, _me, _ch = _cfg(tmp_path, enabled=False, deleg=False)
    assert await hd.check_broker(config, env=_ENV) is None


@pytest.mark.asyncio
async def test_check_broker_error_when_url_unset(tmp_path):
    config, _me, _ch = _cfg(tmp_path)
    c = await hd.check_broker(config, env={})
    assert c is not None and not c.ok and "unset" in c.message


@pytest.mark.asyncio
async def test_check_broker_env_url_wins_over_yaml(tmp_path):
    """Runtime precedence: NMEX_REDIS_URL overrides transport.url, so the probe must use the env one
    (codex P2)."""
    config, _me, _ch = _cfg(tmp_path)
    config["exchange"]["transport"]["url"] = "redis://yaml-host:6390/0"
    seen = {}

    class _FakeRedis:
        async def ping(self):
            return True

        async def aclose(self):
            pass

    def _from_url(url, **kw):
        seen["url"] = url
        return _FakeRedis()

    import redis.asyncio as aioredis
    orig = aioredis.from_url
    aioredis.from_url = _from_url
    try:
        c = await hd.check_broker(config, env={"NMEX_REDIS_URL": "redis://env-host:6390/0"})
    finally:
        aioredis.from_url = orig
    assert c is not None and c.ok
    assert seen["url"] == "redis://env-host:6390/0"


@pytest.mark.asyncio
async def test_check_broker_error_does_not_leak_credentials(tmp_path):
    """A parse error must not echo str(e) — it can carry the password (codex P2). Only the class."""
    config, _me, _ch = _cfg(tmp_path)

    def _boom(url, **kw):
        raise ValueError("Port could not be cast to integer value as 'supersecret'")

    import redis.asyncio as aioredis
    orig = aioredis.from_url
    aioredis.from_url = _boom
    try:
        c = await hd.check_broker(config, env={"NMEX_REDIS_URL": "redis://:supersecret#1@h:6390/0"})
    finally:
        aioredis.from_url = orig
    assert c is not None and not c.ok
    assert "supersecret" not in c.message
    assert "ValueError" in c.message


@pytest.mark.asyncio
async def test_run_static_only_skips_probe(tmp_path):
    config, _me, _ch = _cfg(tmp_path)
    rep = await hd.run(config, env=_ENV, live=False)
    # broker check stays the STATIC url-presence one (info), not a live reachability result
    broker = _by_name(rep, "broker")
    assert len(broker) == 1 and "configured" in broker[0].message
    assert rep.ok


# ── readiness shaping ──────────────────────────────────────────────────────────────

def test_cli_env_loads_secrets_env(tmp_path):
    """A standalone CLI run must pick up NMEX_REDIS_URL from the agent dir's secrets.env — a
    docker exec / separate shell doesn't inherit the entrypoint's sourced env (codex P2, round 4)."""
    import shlex
    agent_dir = tmp_path / "michelle"
    agent_dir.mkdir()
    yaml_path = str(agent_dir / "agent.yaml")
    open(yaml_path, "w").close()
    with open(agent_dir / "secrets.env", "w") as f:
        f.write(f"NMEX_REDIS_URL={shlex.quote('redis://:pw with space@h:6390/0')}\n")
    env = hd._cli_env(yaml_path)
    assert env["NMEX_REDIS_URL"] == "redis://:pw with space@h:6390/0"


def test_cli_env_strips_export_prefix(tmp_path):
    """`export KEY=VALUE` is valid in a sourced env file — the loader must strip `export ` (codex P2,
    round 12)."""
    agent_dir = tmp_path / "djai"
    agent_dir.mkdir()
    yaml_path = str(agent_dir / "agent.yaml")
    open(yaml_path, "w").close()
    with open(agent_dir / "secrets.env", "w") as f:
        f.write("export NMEX_REDIS_URL=redis://broker:6379/0\n")
    env = hd._cli_env(yaml_path)
    assert env["NMEX_REDIS_URL"] == "redis://broker:6379/0"
    assert not any(k.startswith("export ") for k in env)


def test_backstop_never_raises_on_exotic_shape(tmp_path):
    """The never-raises contract is structural: even a shape no targeted check anticipates yields a
    report with a 'config' error rather than a traceback."""
    # config itself not a mapping
    rep = hd.static_report(["not", "a", "dict"], env=_ENV)   # type: ignore[arg-type]
    assert not rep.ok
    assert any(c.name == "config" for c in rep.checks)


@pytest.mark.asyncio
async def test_run_on_nonmapping_config_preserves_static_findings(tmp_path):
    """The live path (run → check_broker) must also not crash on a non-mapping config (codex P2,
    round 9)."""
    rep = await hd.run(["invalid"], env={})   # type: ignore[arg-type] — must not raise
    assert not rep.ok
    assert any(c.name == "config" for c in rep.checks)


def test_readiness_items_only_problems(tmp_path):
    config, _me, _ch = _cfg(tmp_path)
    config["exchange"]["identity"]["keyfile"] = os.path.join(str(tmp_path), "gone.json")
    rep = hd.static_report(config, env=_ENV)
    items = rep.readiness_items()
    assert items and all(i["level"] in (hd._WARN, hd._ERROR) for i in items)
    assert all(i["capability"].startswith("hive: ") for i in items)
