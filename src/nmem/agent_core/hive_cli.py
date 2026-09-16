"""``hive`` — the operator CLI for establishing/joining a hive (P1.3).

The engine the studio wizard + dashboard (P2) call into. Turns "set up a hive" from hand-editing
seeds + cross-pasting base64 keys into a few guided commands:

    python -m nmem.agent_core.hive_cli create <name> --out hive.yaml
    python -m nmem.agent_core.hive_cli join   hive.yaml --as djai --keyfile /data/djai/djai.key.json
    python -m nmem.agent_core.hive_cli add-member hive.yaml --bundle michelle.json
    python -m nmem.agent_core.hive_cli members hive.yaml
    python -m nmem.agent_core.hive_cli doctor  /data/djai/agent.yaml

``join`` OWNS the keyfile lifecycle (the plan's requirement #2): it generates the keypair and writes
the PRIVATE keyfile atomically with a chosen mode — solving the NFS root-squash trap centrally
(``--nfs`` writes 0644 so a container whose root maps to ``nobody`` can still read it; the default
0600 is used everywhere else). Only PUBLIC bundles ever touch the descriptor; the private key stays
in the keyfile, and the broker secret stays in its env var (the descriptor names the var, not the URL).

Pure-ish: the command functions take explicit args and are unit-tested directly; ``main`` is a thin
argparse dispatcher over them.
"""
from __future__ import annotations

import contextlib
import json
import os

from nmem.agent_core.hive_descriptor import HiveDescriptor, Member


@contextlib.contextmanager
def _descriptor_lock(descriptor_path: str):
    """Serialize the load→modify→save transaction on a shared descriptor so two concurrent joins
    (or a join + add-member) can't lose each other's member (atomic replace prevents partial writes,
    NOT lost updates). An exclusive flock on a sibling ``.lock`` file, keyed on the RESOLVED path so a
    symlink and its target share one lock. No-op where fcntl is unavailable (non-POSIX)."""
    try:
        import fcntl
    except ImportError:
        yield
        return
    lock_path = os.path.realpath(descriptor_path) + ".lock"
    # Open O_RDWR: on NFS, flock is emulated via a byte-range lock that REQUIRES a write-capable fd
    # (O_RDONLY → EBADF). For a group-shared descriptor every authorized writer must be able to open
    # it O_RDWR, so make the lock file 0666 explicitly (best-effort, owner-only) — defeating a tight
    # umask that would otherwise leave it 0600 and lock out other users. An empty lock file being
    # world-writable is acceptable (holding it requires local access to the shared descriptor anyway).
    # O_NOFOLLOW refuses a pre-planted symlink at the predictable `<descriptor>.lock` path (an
    # attacker in a group-writable dir could otherwise point it at a private file and have our
    # O_CREAT/chmod follow it, exposing the target). fchmod acts on the verified fd, never a path.
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o666)
    try:
        os.fchmod(fd, 0o666)
    except OSError:
        pass
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


# ── command implementations (testable; no argparse) ─────────────────────────────

def _prepare_dir(target_file: str, *, world: bool) -> None:
    """Create the parent dirs of ``target_file``. When ``world`` (the file will be other-readable, the
    NFS root-squash case), make every NEWLY-created dir level 0755 so the container (mapped to
    ``nobody``) can traverse them — os.makedirs would otherwise create them 0700 under a tight umask.
    A pre-existing ancestor that isn't world-traversable is WARNED about, never modified (it may be
    someone else's directory)."""
    d = os.path.dirname(os.path.abspath(target_file))
    # collect the missing levels (deepest → shallowest), and the nearest existing ancestor
    missing: list[str] = []
    p = d
    while p and not os.path.isdir(p):
        missing.append(p)
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    os.makedirs(d, exist_ok=True)
    if not world:
        return
    for level in missing:
        try:
            os.chmod(level, 0o755)
        except OSError:
            pass
    existing = p if os.path.isdir(p) else d      # nearest pre-existing ancestor
    try:
        if not (os.stat(existing).st_mode & 0o001):
            print(f"warning: {existing} is not world-traversable — a root-squashed container may fail "
                  f"to reach {os.path.basename(target_file)} (chmod o+x {existing}).")
    except OSError:
        pass


def _make_readable(path: str) -> None:
    """Make a shared artifact (the descriptor) world-readable + its dirs traversable, for the NFS
    root-squash bind-mount case. No-op-safe."""
    _prepare_dir(path, world=True)
    try:
        os.chmod(path, 0o644)
    except OSError:
        pass


def create_descriptor(name: str, *, out: str, broker_env: str = "NMEX_REDIS_URL",
                      ts_window_s: float = 300.0, nfs: bool = False) -> str:
    """Write a new descriptor with an EMPTY roster. ``broker_env`` is the NAME of the env var each
    host reads the broker URL from (the secret stays in env, off the descriptor). Refuses to
    clobber an existing file. ``nfs`` makes it world-readable (root-squash bind-mount)."""
    # Ensure the parent dir exists BEFORE the lock (the lock file is a sibling of `out` — locking
    # would FileNotFoundError in a not-yet-created dir). world= reflects the NFS root-squash case.
    _prepare_dir(out, world=nfs)
    with _descriptor_lock(out):      # serialize with concurrent create/join; re-check inside the lock
        if os.path.exists(out):
            raise SystemExit(f"refusing to overwrite existing descriptor: {out}")
        HiveDescriptor(name=name, broker_url_env=broker_env, ts_window_s=float(ts_window_s)).save(out)
        if nfs:
            _make_readable(out)
    return out


def generate_keyfile(agent_id: str, keyfile: str, *, mode: int = 0o600):
    """Generate a fresh identity and write its PRIVATE keyfile ATOMICALLY (O_EXCL refuses to clobber,
    O_NOFOLLOW refuses a symlink). ``mode`` defaults to 0600; pass 0644 for an NFS root-squash
    bind-mount where the container's runtime user (mapped to ``nobody``) must still read it. Returns
    the Identity (its public bundle goes into the descriptor)."""
    from nmem_exchange import crypto

    idn = crypto.generate_identity(agent_id)
    # NFS root-squash: a 0644 keyfile is useless if the container can't TRAVERSE its (possibly
    # multi-level, freshly-created) directory. _prepare_dir makes new levels 0755 + warns on tight
    # pre-existing ancestors.
    _prepare_dir(keyfile, world=bool(mode & 0o044))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(keyfile, flags, mode)
    except FileExistsError:
        raise SystemExit(f"refusing to overwrite existing keyfile: {keyfile} "
                         f"(delete it first to regenerate — this DESTROYS the old identity)")
    # We EXCLUSIVELY created this file — if the write/chmod fails (disk full, etc.) clean it up so a
    # corrected retry isn't blocked by a half-written keyfile. (A pre-existing keyfile can't reach
    # here — O_EXCL raised above — so this never deletes someone else's key.)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(idn.export_secret(), f, indent=2)
            f.flush()
            # fchmod on the OPEN fd (not a path): the O_EXCL create mode is umask-masked, but a
            # path-based chmod could follow a symlink swapped in after creation and expose an
            # unrelated file. fchmod acts on the file we exclusively created.
            os.fchmod(f.fileno(), mode)
    except Exception:
        try:
            os.remove(keyfile)
        except OSError:
            pass
        raise
    return idn


def join_hive(descriptor_path: str, *, agent_id: str, keyfile: str, nfs: bool = False) -> dict:
    """Generate this host's keypair (owning the keyfile + perms), add its PUBLIC bundle to the
    descriptor, and save. Returns ``{bundle, descriptor, keyfile}`` — the operator passes the bundle
    (or the updated descriptor) to the other hosts. ``nfs`` writes BOTH the keyfile and the descriptor
    world-readable (root-squash bind-mount); otherwise the keyfile is 0600. Refuses to clobber an
    existing keyfile (that would orphan the identity). If registration fails AFTER the key is written
    (e.g. a read-only descriptor), the just-created keyfile is ROLLED BACK so a corrected retry works."""
    idn = generate_keyfile(agent_id, keyfile, mode=0o644 if nfs else 0o600)
    try:
        with _descriptor_lock(descriptor_path):      # serialize concurrent joins (no lost updates)
            descriptor = HiveDescriptor.load(descriptor_path)   # (re)load INSIDE the lock
            descriptor.upsert_member(Member(agent_id, idn.sign_pub, idn.box_pub))
            descriptor.save(descriptor_path)
            if nfs:                  # inside the lock: else a concurrent normal save could re-tighten it
                _make_readable(descriptor_path)
    except Exception:
        try:
            os.remove(keyfile)       # roll back the fresh key so retrying `join` isn't blocked by it
        except OSError:
            pass
        raise
    return {"bundle": idn.public_bundle(), "descriptor": descriptor_path, "keyfile": keyfile}


def add_member(descriptor_path: str, bundle: dict, *, nfs: bool = False) -> dict:
    """Merge a peer's PUBLIC bundle (``{agent_id, sign_pub, box_pub}``) into the descriptor and save.
    Replaces an existing entry with the same agent_id (a re-keyed peer). Returns the bundle added."""
    if not isinstance(bundle, dict):
        raise SystemExit("bundle must be a JSON object {agent_id, sign_pub, box_pub}")
    member = Member(bundle.get("agent_id"), bundle.get("sign_pub"), bundle.get("box_pub"))
    member.validate()                # reject a malformed bundle before touching the descriptor
    with _descriptor_lock(descriptor_path):          # serialize with concurrent join/add-member
        descriptor = HiveDescriptor.load(descriptor_path)
        descriptor.upsert_member(member)
        descriptor.save(descriptor_path)
        if nfs:                      # inside the lock (a concurrent normal save must not re-tighten it)
            _make_readable(descriptor_path)
    return member.bundle()


def remove_member(descriptor_path: str, agent_id: str, *, nfs: bool = False) -> bool:
    """Drop a member from the descriptor and save. Returns True if a member was removed, False if
    none matched (idempotent). Serialized with concurrent join/add-member so a removal can't lose a
    concurrent join. Only mutates PUBLIC roster data — never touches anyone's private keyfile."""
    if not isinstance(agent_id, str) or not agent_id:
        raise SystemExit("agent_id to remove must be a non-empty string")
    with _descriptor_lock(descriptor_path):          # serialize with concurrent join/add-member
        descriptor = HiveDescriptor.load(descriptor_path)
        removed = descriptor.remove_member(agent_id)
        if removed:
            descriptor.save(descriptor_path)
            if nfs:                  # inside the lock (a concurrent normal save must not re-tighten it)
                _make_readable(descriptor_path)
    return removed


def list_members(descriptor_path: str) -> list[dict]:
    return [m.bundle() for m in HiveDescriptor.load(descriptor_path).members]


def _load_bundle(bundle_arg: str) -> dict:
    """A bundle given as a path to a JSON file, or a literal JSON string."""
    if os.path.isfile(bundle_arg):
        with open(bundle_arg) as f:
            return json.load(f)
    return json.loads(bundle_arg)


# ── argparse dispatcher ─────────────────────────────────────────────────────────

def _portable_path(path: str, seed_dir: str) -> str:
    """A seed path that survives a host→container bind-mount. expand_into_config resolves relative
    paths against the agent DIR, so a file INSIDE seed_dir is emitted RELATIVE (a basename that's
    identical whether the dir lives at /mnt/nas/... on the host or /data/<id> in the container).
    Only a file OUTSIDE seed_dir falls back to an absolute path (no portable relative form)."""
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(seed_dir))
    return rel if not rel.startswith("..") else os.path.abspath(path)


def _seed_block(descriptor_path: str, agent_id: str, keyfile: str, delegation_accepts,
                seed_dir: str) -> str:
    """The `hive:` block to paste into the agent's seed (agent.yaml). Printed by ``join`` so the
    operator wires the seed without hand-deriving anything (the wizard automates this in P2).
    ``seed_dir`` is where agent.yaml lives (the agent dir) — paths are emitted relative to it so they
    resolve correctly in the container regardless of the host path the operator ran ``join`` from."""
    import yaml
    block = {"hive": {"descriptor": _portable_path(descriptor_path, seed_dir), "identity": agent_id,
                      "keyfile": _portable_path(keyfile, seed_dir)}}
    # `accepts is not None` (incl. an explicit empty list) → delegation ON. An empty accepts means a
    # REQUESTER-ONLY agent (no worker task types, but peer routes still derived); omission = off.
    if delegation_accepts is not None:
        block["hive"]["delegation"] = {"enabled": True, "accepts": list(delegation_accepts)}
    return yaml.safe_dump(block, sort_keys=False).rstrip()


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    argv = sys.argv[1:] if argv is None else argv
    p = argparse.ArgumentParser(prog="hive", description="Establish or join an nmem hive.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("create", help="create a new (empty) hive descriptor")
    pc.add_argument("name")
    pc.add_argument("--out", default="hive.yaml", help="descriptor path to write (default: hive.yaml)")
    pc.add_argument("--broker-env", default="NMEX_REDIS_URL",
                    help="env var each host reads the broker URL from (default: NMEX_REDIS_URL)")
    pc.add_argument("--ts-window", type=float, default=300.0, help="freshness window seconds (default 300)")
    pc.add_argument("--nfs", action="store_true", help="world-readable descriptor for an NFS root-squash bind-mount")

    pj = sub.add_parser("join", help="generate this host's key + add it to the descriptor")
    pj.add_argument("descriptor")
    pj.add_argument("--as", dest="agent_id", required=True, help="this host's agent id")
    pj.add_argument("--keyfile", help="private keyfile path (default: <descriptor dir>/<id>.key.json)")
    pj.add_argument("--nfs", action="store_true",
                    help="write the keyfile 0644 for an NFS root-squash bind-mount (else 0600)")
    pj.add_argument("--seed-dir", default=None,
                    help="dir where the agent's agent.yaml lives (default: the keyfile's dir); the "
                         "printed seed block's paths are emitted relative to it so they resolve in "
                         "the container")
    pj.add_argument("--accepts", nargs="*", default=None,
                    help="delegation task types this host serves (--accepts ask; --accepts with no "
                         "values = requester-only)")

    pa = sub.add_parser("add-member", help="merge a peer's public bundle into the descriptor")
    pa.add_argument("descriptor")
    pa.add_argument("--bundle", required=True, help="peer bundle: a JSON file path or a JSON string")
    pa.add_argument("--nfs", action="store_true", help="keep the descriptor world-readable (root-squash)")

    pr = sub.add_parser("remove-member", help="drop a member from the descriptor")
    pr.add_argument("descriptor")
    pr.add_argument("--as", dest="agent_id", required=True, help="the member's agent id to remove")
    pr.add_argument("--nfs", action="store_true", help="keep the descriptor world-readable (root-squash)")

    pm = sub.add_parser("members", help="list the descriptor's members")
    pm.add_argument("descriptor")

    pd = sub.add_parser("doctor", help="validate an agent's effective hive config")
    pd.add_argument("target", nargs="?", help="agent dir or agent.yaml (default: discover)")

    args = p.parse_args(argv)

    if args.cmd == "create":
        out = create_descriptor(args.name, out=args.out, broker_env=args.broker_env,
                                ts_window_s=args.ts_window, nfs=args.nfs)
        print(f"created hive descriptor '{args.name}' → {out}")
        print("next: on each host run  hive join", out, "--as <agent_id>")
        return 0

    if args.cmd == "join":
        keyfile = args.keyfile or os.path.join(
            os.path.dirname(os.path.abspath(args.descriptor)), f"{args.agent_id}.key.json")
        res = join_hive(args.descriptor, agent_id=args.agent_id, keyfile=keyfile, nfs=args.nfs)
        print(f"joined as '{args.agent_id}' — wrote keyfile {res['keyfile']} "
              f"({'0644 NFS' if args.nfs else '0600'}) and added me to {res['descriptor']}\n")
        print("PUBLIC bundle (share with peers, or send them the updated descriptor):")
        print(json.dumps(res["bundle"], indent=2))
        seed_dir = args.seed_dir or os.path.dirname(os.path.abspath(keyfile))
        print("\nadd this to the agent's seed (agent.yaml):\n")
        print(_seed_block(args.descriptor, args.agent_id, keyfile, args.accepts, seed_dir))
        return 0

    if args.cmd == "add-member":
        added = add_member(args.descriptor, _load_bundle(args.bundle), nfs=args.nfs)
        print(f"added/updated member '{added['agent_id']}' in {args.descriptor}")
        return 0

    if args.cmd == "remove-member":
        if remove_member(args.descriptor, args.agent_id, nfs=args.nfs):
            print(f"removed member '{args.agent_id}' from {args.descriptor}")
        else:
            print(f"no member '{args.agent_id}' in {args.descriptor} (nothing to remove)")
        return 0

    if args.cmd == "members":
        members = list_members(args.descriptor)
        if not members:
            print("(no members yet)")
        for m in members:
            print(f"  {m['agent_id']}  sign={m['sign_pub'][:12]}…  box={m['box_pub'][:12]}…")
        return 0

    if args.cmd == "doctor":
        from nmem.agent_core.hive_doctor import main as doctor_main
        return doctor_main([args.target] if args.target else [])

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
