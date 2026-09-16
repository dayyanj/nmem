"""hive CLI (P1.3) — create / join / add-member / members, + keygen-at-correct-perms.

The capstone drives the whole operator flow (create → join on two hosts → doctor) and asserts the
result is a doctor-clean hive — the "super user-friendly" promise, end to end.

Run: ~/sh/venv/bin/python -m pytest tests/test_hive_cli.py -q
"""
from __future__ import annotations

import json
import os
import stat

import pytest

crypto = pytest.importorskip("nmem_exchange.crypto")

from nmem.agent_core import hive_cli as cli
from nmem.agent_core.hive_descriptor import HiveDescriptor


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


# ── create ───────────────────────────────────────────────────────────────────────

def test_create_writes_empty_descriptor(tmp_path):
    out = str(tmp_path / "hive.yaml")
    cli.create_descriptor("spwig-fleet", out=out, broker_env="NMEX_REDIS_URL", ts_window_s=300)
    desc = HiveDescriptor.load(out)
    assert desc.name == "spwig-fleet" and desc.members == []
    assert desc.broker_url_env == "NMEX_REDIS_URL"


def test_create_refuses_clobber(tmp_path):
    out = str(tmp_path / "hive.yaml")
    cli.create_descriptor("x", out=out)
    with pytest.raises(SystemExit):
        cli.create_descriptor("y", out=out)


def test_create_in_nonexistent_dir(tmp_path):
    """create --out into a not-yet-existing dir must work — the parent (and lock file) are prepared
    before locking (codex P2 round 9)."""
    out = str(tmp_path / "new" / "subdir" / "hive.yaml")
    cli.create_descriptor("fleet", out=out, nfs=True)
    assert os.path.isfile(out)
    assert _mode(out) == 0o644


def test_lock_rejects_symlink(tmp_path):
    """A pre-planted symlink at <descriptor>.lock must NOT be followed (O_NOFOLLOW) — else an attacker
    could redirect our O_CREAT/chmod onto a private file (codex P1 round 10)."""
    private = tmp_path / "private"
    private.write_text("secret")
    os.chmod(str(private), 0o600)
    out = str(tmp_path / "hive.yaml")
    os.symlink(str(private), out + ".lock")     # attacker points the lock at a private file
    with pytest.raises(OSError):
        cli.create_descriptor("fleet", out=out)
    assert _mode(str(private)) == 0o600         # untouched — not followed, not chmod'd


def test_lock_file_is_group_writable(tmp_path):
    """The persistent lock must be openable O_RDWR by other authorized writers regardless of umask
    (codex P2 round 9)."""
    old = os.umask(0o077)
    try:
        out = str(tmp_path / "hive.yaml")
        cli.create_descriptor("fleet", out=out)
        assert _mode(out + ".lock") == 0o666
    finally:
        os.umask(old)


# ── keyfile generation + perms ─────────────────────────────────────────────────────

def test_generate_keyfile_default_0600(tmp_path):
    kf = str(tmp_path / "djai.key.json")
    idn = cli.generate_keyfile("djai", kf)
    assert _mode(kf) == 0o600
    # the keyfile round-trips to the same identity
    loaded = crypto.identity_from_secret(json.load(open(kf)))
    assert loaded.sign_pub == idn.sign_pub


def test_generate_keyfile_nfs_0644(tmp_path):
    kf = str(tmp_path / "djai.key.json")
    cli.generate_keyfile("djai", kf, mode=0o644)
    assert _mode(kf) == 0o644          # the NFS root-squash workaround, written centrally


def test_generate_keyfile_refuses_clobber(tmp_path):
    kf = str(tmp_path / "djai.key.json")
    cli.generate_keyfile("djai", kf)
    with pytest.raises(SystemExit):
        cli.generate_keyfile("djai", kf)   # must not silently destroy an existing identity


def test_generate_keyfile_cleans_up_on_write_failure(tmp_path, monkeypatch):
    """A write/chmod failure on the exclusively-created keyfile must remove it, so a corrected retry
    isn't blocked (codex P2 round 5)."""
    kf = str(tmp_path / "djai.key.json")
    monkeypatch.setattr(cli.os, "fchmod", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        cli.generate_keyfile("djai", kf)
    assert not os.path.exists(kf)          # cleaned up
    monkeypatch.undo()
    cli.generate_keyfile("djai", kf)       # retry now works
    assert os.path.exists(kf)


# ── join ───────────────────────────────────────────────────────────────────────────

def test_join_adds_self_and_writes_key(tmp_path):
    desc_path = str(tmp_path / "hive.yaml")
    cli.create_descriptor("fleet", out=desc_path)
    kf = str(tmp_path / "djai.key.json")
    res = cli.join_hive(desc_path, agent_id="djai", keyfile=kf)
    assert res["bundle"]["agent_id"] == "djai"
    # descriptor now lists me with the keyfile's PUBLIC keys
    desc = HiveDescriptor.load(desc_path)
    assert desc.member("djai").sign_pub == res["bundle"]["sign_pub"]
    assert _mode(kf) == 0o600


# ── add-member ─────────────────────────────────────────────────────────────────────

def test_add_member_merges_bundle(tmp_path):
    desc_path = str(tmp_path / "hive.yaml")
    cli.create_descriptor("fleet", out=desc_path)
    peer = crypto.generate_identity("michelle")
    cli.add_member(desc_path, peer.public_bundle())
    assert "michelle" in HiveDescriptor.load(desc_path).agent_ids()


def test_add_member_rejects_malformed_bundle(tmp_path):
    desc_path = str(tmp_path / "hive.yaml")
    cli.create_descriptor("fleet", out=desc_path)
    with pytest.raises(Exception):
        cli.add_member(desc_path, {"agent_id": "x", "sign_pub": "bad", "box_pub": "bad"})


# ── seed block emission ─────────────────────────────────────────────────────────────

def test_seed_block_paths_relative_to_seed_dir(tmp_path):
    """Files INSIDE the seed dir are emitted RELATIVE (portable across host→container bind-mounts);
    files outside fall back to absolute (codex P2 round 2)."""
    import yaml
    seed = str(tmp_path / "djai")
    block = yaml.safe_load(cli._seed_block(
        os.path.join(seed, "hive.yaml"), "djai", os.path.join(seed, "djai.key.json"), ["ask"], seed))
    assert block["hive"]["descriptor"] == "hive.yaml"       # basename — resolves under /data/djai
    assert block["hive"]["keyfile"] == "djai.key.json"
    # a descriptor outside the seed dir → absolute (no portable relative form)
    outside = yaml.safe_load(cli._seed_block("/shared/hive.yaml", "djai",
                                             os.path.join(seed, "djai.key.json"), None, seed))
    assert os.path.isabs(outside["hive"]["descriptor"])


def test_seed_block_requester_only_on_empty_accepts():
    """--accepts with no values (explicit []) → requester-only delegation ON; omission (None) → off
    (codex P2)."""
    import yaml
    on = yaml.safe_load(cli._seed_block("/d/hive.yaml", "djai", "/d/djai.key.json", [], "/d"))
    assert on["hive"]["delegation"] == {"enabled": True, "accepts": []}
    off = yaml.safe_load(cli._seed_block("/d/hive.yaml", "djai", "/d/djai.key.json", None, "/d"))
    assert "delegation" not in off["hive"]


def test_nfs_keyfile_creates_traversable_dir(tmp_path):
    """A 0644 (NFS) keyfile in a newly-created dir needs that dir world-traversable, else the
    root-squashed container can't reach it (codex P2 round 2)."""
    kf = str(tmp_path / "newdir" / "djai.key.json")
    cli.generate_keyfile("djai", kf, mode=0o644)
    assert _mode(kf) == 0o644
    assert _mode(os.path.dirname(kf)) & 0o001    # world-traversable


def test_nfs_keyfile_traverses_all_new_levels(tmp_path):
    """Multiple freshly-created dir levels must ALL be traversable under a tight umask (codex P2 r3)."""
    old = os.umask(0o077)
    try:
        kf = str(tmp_path / "a" / "b" / "c" / "djai.key.json")
        cli.generate_keyfile("djai", kf, mode=0o644)
        for level in (tmp_path / "a", tmp_path / "a" / "b", tmp_path / "a" / "b" / "c"):
            assert _mode(str(level)) & 0o001, level     # every created level world-traversable
    finally:
        os.umask(old)


def test_nfs_join_makes_descriptor_readable(tmp_path):
    """join --nfs must make the DESCRIPTOR container-readable too, not just the keyfile (codex P2 r3)."""
    old = os.umask(0o077)
    try:
        desc_path = str(tmp_path / "hive.yaml")
        cli.create_descriptor("fleet", out=desc_path, nfs=True)
        assert _mode(desc_path) == 0o644
        cli.join_hive(desc_path, agent_id="djai", keyfile=str(tmp_path / "djai.key.json"), nfs=True)
        assert _mode(desc_path) == 0o644            # still readable after the join re-saves it
    finally:
        os.umask(old)


def test_join_nfs_sets_keyfile_0644_via_engine(tmp_path):
    """join_hive(nfs=True) must itself set the keyfile 0644 — no need for callers to also pass mode
    (codex P2 round 4)."""
    desc_path = str(tmp_path / "hive.yaml")
    cli.create_descriptor("fleet", out=desc_path)
    kf = str(tmp_path / "djai.key.json")
    cli.join_hive(desc_path, agent_id="djai", keyfile=kf, nfs=True)
    assert _mode(kf) == 0o644


def test_join_rolls_back_keyfile_on_registration_failure(tmp_path):
    """If descriptor.save() fails after the key is written, the keyfile is rolled back so a corrected
    retry works (codex P2 round 4). A read-ONLY descriptor DIR makes the atomic save fail."""
    ro_dir = tmp_path / "ro"
    ro_dir.mkdir()
    desc_path = str(ro_dir / "hive.yaml")
    cli.create_descriptor("fleet", out=desc_path)
    kf = str(tmp_path / "keys" / "djai.key.json")     # keyfile in a WRITABLE location
    os.chmod(ro_dir, 0o500)                            # read-only dir → mkstemp/replace fails
    try:
        with pytest.raises(Exception):
            cli.join_hive(desc_path, agent_id="djai", keyfile=kf)
        assert not os.path.exists(kf)                  # rolled back
    finally:
        os.chmod(ro_dir, 0o755)
    res = cli.join_hive(desc_path, agent_id="djai", keyfile=kf)   # retry now works
    assert res["bundle"]["agent_id"] == "djai"


def test_descriptor_save_is_atomic_on_failure(tmp_path, monkeypatch):
    """A failed descriptor write must leave the ORIGINAL roster intact (codex P2 round 6) — else every
    member's expand-at-load breaks."""
    desc_path = str(tmp_path / "hive.yaml")
    cli.create_descriptor("fleet", out=desc_path)
    cli.add_member(desc_path, crypto.generate_identity("michelle").public_bundle())
    before = open(desc_path).read()
    # make the yaml serialization blow up mid-save
    import nmem.agent_core.hive_descriptor as hdsc
    monkeypatch.setattr(hdsc.HiveDescriptor, "to_dict",
                        lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        cli.add_member(desc_path, crypto.generate_identity("djai").public_bundle())
    monkeypatch.undo()
    assert open(desc_path).read() == before            # original descriptor untouched
    assert "michelle" in HiveDescriptor.load(desc_path).agent_ids()


def test_expand_requester_only_derives_routes_no_task_types():
    """expand() with enabled + empty accepts → peer routes but no worker task types (requester-only)."""
    from nmem.agent_core.hive_descriptor import HiveDescriptor, Member, expand
    a = crypto.generate_identity("djai")
    b = crypto.generate_identity("michelle")
    desc = HiveDescriptor(name="f", members=[Member("djai", a.sign_pub, a.box_pub),
                                             Member("michelle", b.sign_pub, b.box_pub)])
    cfg = expand(desc, identity="djai", keyfile="/k", delegation={"enabled": True, "accepts": []})
    assert cfg["delegation"]["task_types"] == []
    assert cfg["delegation"]["peer_channels"] == {"michelle": "dm:djai:michelle"}


def test_concurrent_joins_no_lost_update(tmp_path):
    """Four threads joining the same descriptor must ALL end up registered — the load→save lock
    prevents lost updates (codex P2 round 7)."""
    import threading
    desc_path = str(tmp_path / "hive.yaml")
    cli.create_descriptor("fleet", out=desc_path)

    def _join(aid):
        cli.join_hive(desc_path, agent_id=aid, keyfile=str(tmp_path / f"{aid}.key.json"))

    threads = [threading.Thread(target=_join, args=(a,)) for a in ("a", "b", "c", "d")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert set(HiveDescriptor.load(desc_path).agent_ids()) == {"a", "b", "c", "d"}


def test_save_through_symlink_updates_target(tmp_path):
    """A descriptor that's a symlink to a shared roster must update the TARGET, keeping the link
    (codex P2 round 7)."""
    (tmp_path / "shared").mkdir()
    (tmp_path / "agent").mkdir()
    real = str(tmp_path / "shared" / "hive.yaml")
    link = str(tmp_path / "agent" / "hive.yaml")
    cli.create_descriptor("fleet", out=real)
    os.symlink(real, link)
    cli.add_member(link, crypto.generate_identity("michelle").public_bundle())
    assert os.path.islink(link)                                   # link preserved
    assert "michelle" in HiveDescriptor.load(real).agent_ids()   # shared target updated


# ── capstone: full flow → doctor-clean hive ────────────────────────────────────────

def test_full_flow_two_hosts_is_doctor_clean(tmp_path):
    """create → djai joins → michelle joins → each host's expanded config passes hive_doctor.
    This is the whole 'establish a hive' UX, validated end to end."""
    from nmem.agent_core import hive_descriptor as hdsc
    from nmem.agent_core import hive_doctor as hd

    # A shared descriptor lives on each host; simulate one shared file both hosts edit in turn.
    desc_path = str(tmp_path / "hive.yaml")
    cli.create_descriptor("spwig-fleet", out=desc_path)

    djai_dir = tmp_path / "djai"; djai_dir.mkdir()
    mich_dir = tmp_path / "michelle"; mich_dir.mkdir()
    djai_kf = str(djai_dir / "djai.key.json")
    mich_kf = str(mich_dir / "michelle.key.json")

    cli.join_hive(desc_path, agent_id="djai", keyfile=djai_kf)
    cli.join_hive(desc_path, agent_id="michelle", keyfile=mich_kf)
    assert set(HiveDescriptor.load(desc_path).agent_ids()) == {"djai", "michelle"}

    # each host expands the (now-complete) descriptor for its own identity + validates
    env = {"NMEX_REDIS_URL": "redis://:pw@h:6390/0"}
    for identity, keyfile in (("djai", djai_kf), ("michelle", mich_kf)):
        config = {"db": {"config_key": identity}, "symbol_graph": {"enabled": True},
                  "hive": {"descriptor": desc_path, "identity": identity, "keyfile": keyfile,
                           "delegation": {"enabled": True, "accepts": ["ask"]}}}
        hdsc.expand_into_config(config, agent_dir=str(tmp_path), default_identity=identity, env=dict(env))
        rep = hd.static_report(config, env=env)
        assert rep.ok, (identity, [c.message for c in rep.checks if c.level == hd._ERROR])
        assert not rep.problems(), (identity, [c.message for c in rep.problems()])


# ── argparse smoke (dispatch wiring) ────────────────────────────────────────────────

def test_main_create_then_members(tmp_path, capsys):
    out = str(tmp_path / "hive.yaml")
    assert cli.main(["create", "fleet", "--out", out]) == 0
    peer = crypto.generate_identity("michelle")
    bundle_path = str(tmp_path / "m.json")
    with open(bundle_path, "w") as f:
        json.dump(peer.public_bundle(), f)
    assert cli.main(["add-member", out, "--bundle", bundle_path]) == 0
    assert cli.main(["members", out]) == 0
    assert "michelle" in capsys.readouterr().out
