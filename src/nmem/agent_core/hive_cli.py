"""``hive`` — the operator CLI for establishing/joining a hive (P1.3).

The engine the studio wizard + dashboard (P2) call into. Turns "set up a hive" from hand-editing
seeds + cross-pasting base64 keys into a few guided commands:

    python -m nmem.agent_core.hive_cli create <name> --out hive.yaml
    python -m nmem.agent_core.hive_cli join   hive.yaml --as djai --keyfile /data/djai/djai.key.json
    python -m nmem.agent_core.hive_cli add-member hive.yaml --bundle michelle.json
    python -m nmem.agent_core.hive_cli announce  hive.yaml --as djai        # TOFU convenience
    python -m nmem.agent_core.hive_cli discover  hive.yaml --as djai --pin  # collect + trust peers
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

import base64
import contextlib
import json
import os
import tempfile

from nmem.agent_core.hive_descriptor import HiveDescriptor, Member

# TOFU bootstrap (P3b): the OPEN channel members announce their signed public bundle on. The broker
# password (whoever can reach the broker at all) is the coarse admission gate; collectors pin what
# they see (trust-on-first-use). Deliberately NOT a persistent registry — announcements are just
# transient stream entries; nothing here reads an authoritative directory.
ROSTER_CHANNEL = "hive:roster"


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


def _pin_new_member(descriptor_path: str, bundle: dict, *, nfs: bool = False) -> str:
    """Atomically PIN a discovered peer under the descriptor lock (TOFU first-use). Returns ``'pinned'``
    (new id added), ``'exists'`` (already present with the SAME keys — no-op), or ``'conflict'`` (present
    with DIFFERENT keys — REFUSED, not overwritten). The load→compare→save runs inside ONE lock so a
    concurrent join/add-member/discover can't slip a new pin past the first-use check (a plain
    load-then-add_member would TOCTOU: another writer could add the id in the gap and be overwritten)."""
    member = Member(bundle.get("agent_id"), bundle.get("sign_pub"), bundle.get("box_pub"))
    member.validate()                # reject a malformed bundle before touching the descriptor
    with _descriptor_lock(descriptor_path):
        descriptor = HiveDescriptor.load(descriptor_path)
        existing = descriptor.member(member.agent_id)
        if existing is not None:
            if existing.sign_pub == member.sign_pub and existing.box_pub == member.box_pub:
                return "exists"      # already pinned with the same keys — idempotent no-op
            return "conflict"        # known id, DIFFERENT keys — takeover attempt, do NOT overwrite
        descriptor.upsert_member(member)
        descriptor.save(descriptor_path)
        if nfs:                      # inside the lock (a concurrent normal save must not re-tighten it)
            _make_readable(descriptor_path)
    return "pinned"


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


def _replace_keyfile(keyfile: str, idn, mode: int) -> None:
    """Atomically REPLACE an existing private keyfile with a new identity (key rotation): serialize the
    new secret to a sibling temp file with the right perms, then os.replace() over the keyfile — so a
    failed write leaves the OLD key intact rather than truncating the only copy of the agent's identity.
    fchmod acts on the fd we created (a path-based chmod could follow a symlink swapped in after
    creation); mkstemp gives a unique 0600 temp that O_EXCL-creates (no symlink-follow, no collision)."""
    d = os.path.dirname(os.path.abspath(keyfile)) or "."
    prev = os.stat(keyfile) if os.path.exists(keyfile) else None
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".key-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(idn.export_secret(), f, indent=2)
            f.flush()
            os.fchmod(f.fileno(), mode)
        # Preserve the existing keyfile's owner/group so a rotation run by a DIFFERENT user (e.g. root
        # doing maintenance on a runtime-owned key) doesn't leave the new key unreadable to the runtime.
        # Best-effort: setting uid needs privilege; fall back to chgrp-only (mirrors descriptor save()).
        if prev is not None and hasattr(os, "chown"):   # POSIX-only
            try:
                os.chown(tmp, prev.st_uid, prev.st_gid)
            except OSError:
                try:
                    os.chown(tmp, -1, prev.st_gid)
                except OSError:
                    pass
        os.replace(tmp, keyfile)         # atomic on the same filesystem
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def rotate_key(descriptor_path: str, *, agent_id: str, keyfile: str, nfs: bool = False,
               mode: int | None = None) -> dict:
    """Generate a FRESH keypair for an EXISTING member, publish its new PUBLIC bundle to the descriptor,
    and atomically swap in the matching PRIVATE keyfile. Returns ``{bundle, descriptor, keyfile}`` — the
    operator re-shares the descriptor (or the new bundle) so peers pick up the new key. Unlike ``join``
    this expects the member + keyfile to already exist (rotation, not first join). ``mode`` (if given)
    sets the new keyfile's exact permission bits — used by callers that PRESERVE the old keyfile's mode
    rather than forcing 0600/0644; otherwise ``nfs`` selects 0644 (root-squash) vs 0600.

    Consistency invariant: the keyfile's private key and the descriptor's advertised public key MUST
    agree, or this agent can neither be decrypted by peers nor decrypt their replies. The WHOLE
    rotation (descriptor save + keyfile swap + any rollback) runs under ONE descriptor lock so two
    concurrent rotations of the same identity can't interleave into a pub/priv mismatch (save A, save B,
    then replace-key B, replace-key A). We save the descriptor first, then swap the local keyfile; if
    that rare local write fails we ROLL THE DESCRIPTOR BACK to the old bundle — still holding the lock —
    so pub and priv stay in agreement and the agent keeps working."""
    if not isinstance(agent_id, str) or not agent_id:
        raise SystemExit("agent_id to rotate must be a non-empty string")
    from nmem_exchange import crypto

    # Guard against a mis-pointed --keyfile: verify the EXISTING keyfile is actually this agent's before
    # we overwrite it, else rotating 'a' with b.key.json would destroy B's identity (and leave A's real
    # keyfile out of sync with its updated descriptor entry). _load_identity raises if the file is
    # missing/unreadable (rotation needs an existing key — use `join` first).
    existing = _load_identity(keyfile)
    if existing.agent_id != agent_id:
        raise SystemExit(f"keyfile {keyfile} belongs to '{existing.agent_id}', not '{agent_id}' — "
                         "refusing to overwrite a different member's identity")
    keymode = mode if mode is not None else (0o644 if nfs else 0o600)
    idn = crypto.generate_identity(agent_id)     # new keypair in memory; nothing written yet
    with _descriptor_lock(descriptor_path):      # one lock spans the ENTIRE rotation (no interleaving)
        descriptor = HiveDescriptor.load(descriptor_path)
        old_member = descriptor.member(agent_id)
        if old_member is None:                   # rotate is for members; adding is join/add-member
            raise SystemExit(
                f"'{agent_id}' is not a member of {descriptor_path} — use `join`/`add-member` to add it")
        descriptor.upsert_member(Member(agent_id, idn.sign_pub, idn.box_pub))
        descriptor.save(descriptor_path)
        if nfs:                                  # inside the lock (a concurrent save must not re-tighten)
            _make_readable(descriptor_path)
        # descriptor advertises the NEW pub; swap in the matching private key WHILE STILL LOCKED. This
        # local write rarely fails, but if it does the descriptor is ahead of the keyfile — roll it
        # back to the old bundle (same lock) so they agree again.
        try:
            _replace_keyfile(keyfile, idn, mode=keymode)
        except Exception:
            try:
                descriptor.upsert_member(old_member)   # restore the old PUBLIC bundle (matches old key)
                descriptor.save(descriptor_path)
                if nfs:
                    _make_readable(descriptor_path)
            except Exception:                          # noqa: BLE001 — best-effort; original error matters
                pass
            raise
    return {"bundle": idn.public_bundle(), "descriptor": descriptor_path, "keyfile": keyfile}


# ── TOFU bootstrap: announce / discover over the OPEN roster channel (P3b) ───────
#
# The trust chain is deliberately shallow and HONEST (see the plan's "trust bootstrap" section):
#   • The broker is UNTRUSTED — it only fans out blobs. The broker PASSWORD is the coarse membership
#     gate: whoever can reach the broker at all can announce and be discovered.
#   • Each announcement is a SIGNED-but-open envelope (crypto.seal_open) carrying the announcer's own
#     PUBLIC bundle. The signature proves the announcer HOLDS the private key for the advertised
#     sign_pub (self-attesting) — it does NOT establish that they are who they claim (that's what
#     "trust-on-first-use" means: you accept the identity you first see under the broker password).
#   • `discover` COLLECTS + shows; PINNING (writing into the descriptor) is the explicit trust act.
# This is a CONVENIENCE for single-broker setups; the out-of-band descriptor exchange (join/add-member)
# remains the default, verifiable path. There is intentionally no central key registry.


def _resolve_broker_url(descriptor: HiveDescriptor) -> str:
    """The broker URL lives in the env var the descriptor NAMES (never on the descriptor itself — the
    secret stays in env). Fail loudly if it isn't set: TOFU needs a reachable broker."""
    url_env = descriptor.broker_url_env or "NMEX_REDIS_URL"
    url = os.environ.get(url_env)
    if not url:
        raise SystemExit(
            f"broker URL not set — export {url_env}=redis://:<password>@host:port before announce/discover")
    return url


def _load_identity(keyfile: str):
    from nmem_exchange import crypto
    if not os.path.exists(keyfile):
        raise SystemExit(f"no keyfile at {keyfile} — run `join` (or `rotate-key`) first")
    with open(keyfile) as f:
        return crypto.identity_from_secret(json.load(f))


def _parse_announcement(blob, expected_hive: str | None) -> dict | None:
    """Decode + AUTHENTICATE one roster blob into ``{bundle, hive, verified}`` (or None if it isn't a
    well-formed, self-consistent announcement). ``verified`` is True only when the envelope signature
    checks out against the sign_pub the announcement itself advertises AND ``from`` matches the bundle's
    agent_id — i.e. the sender demonstrably holds that private key. Announcements for a DIFFERENT hive
    name are dropped (a shared broker can carry several hives)."""
    from nmem_exchange import crypto
    try:
        env = json.loads(blob)
    except (ValueError, TypeError):
        return None
    if not isinstance(env, dict) or env.get("kind") != "hive.announce" or env.get("enc") != "none":
        return None
    try:
        payload = json.loads(base64.b64decode(env["pt"]))
        bundle = payload["bundle"]
        # Reject an INCOMPLETE/malformed bundle here (missing box_pub, bad base64 keys) so one bad
        # announcement can't later crash `discover` printing or abort `add_member` — and, because the
        # stream replays history, block discovery of legitimate peers on every subsequent run.
        Member(bundle["agent_id"], bundle["sign_pub"], bundle["box_pub"]).validate()
        aid, sign_pub = bundle["agent_id"], bundle["sign_pub"]
    except (KeyError, ValueError, TypeError):   # incl. HiveDescriptorError (a ValueError)
        return None
    hive = payload.get("hive")
    if expected_hive and hive and hive != expected_hive:
        return None                          # a different hive sharing the same broker
    # self-attestation: `from` must be the claimed id AND the signature must verify under the
    # advertised sign_pub (proves possession of the matching private key).
    verified = env.get("from") == aid and crypto.verify_sig(env, sign_pub)
    return {"bundle": bundle, "hive": hive, "verified": verified}


async def announce_identity(descriptor_path: str, *, keyfile: str, transport=None,
                            msg_id: str | None = None, ts: float | None = None) -> dict:
    """Publish THIS agent's signed public bundle to the open roster channel so peers can discover +
    pin it. Reads the private key from ``keyfile`` (to sign); only PUBLIC keys go on the wire. Builds a
    RedisTransport from the descriptor's broker env var unless a ``transport`` is injected (tests)."""
    import time
    import uuid
    from nmem_exchange import crypto, transport as tmod

    descriptor = HiveDescriptor.load(descriptor_path)
    idn = _load_identity(keyfile)
    payload = json.dumps({"hive": descriptor.name, "bundle": idn.public_bundle()}).encode()
    env = crypto.seal_open(idn, payload, channel=ROSTER_CHANNEL, kind="hive.announce",
                           msg_id=msg_id or uuid.uuid4().hex,
                           ts=ts if ts is not None else time.time())
    owns = transport is None
    if owns:
        transport = tmod.RedisTransport(_resolve_broker_url(descriptor))
    try:
        await transport.publish(ROSTER_CHANNEL, json.dumps(env).encode())
    finally:
        if owns:
            await transport.close()
    return {"bundle": idn.public_bundle(), "channel": ROSTER_CHANNEL, "hive": descriptor.name}


async def _destroy_consumer_group(transport, channel: str, group: str) -> None:
    """Best-effort XGROUP DESTROY of a temporary discovery group so per-run groups don't pile up on the
    broker. Redis-only + private-attr access, guarded by type + hasattr; a no-op for InMemoryTransport
    (no groups) or any error (the group is disposable — the stream is maxlen-capped regardless)."""
    from nmem_exchange import transport as tmod
    if not isinstance(transport, tmod.RedisTransport):
        return
    try:
        r = await transport._client()
        await r.xgroup_destroy(tmod.STREAM_PREFIX + channel, group)
    except Exception:      # noqa: BLE001 — cleanup is best-effort; never fail discovery over it
        pass


async def discover_members(descriptor_path: str, *, self_id: str | None = None, timeout: float = 5.0,
                           pin: bool = False, nfs: bool = False, transport=None,
                           group: str | None = None) -> dict:
    """Subscribe to the open roster channel, collect signed announcements for ``timeout`` seconds, and
    return ``{collected, pinned, channel}``. ``collected`` is one record per agent_id (latest wins,
    self excluded) with its verified flag; when ``pin`` is set, VERIFIED peers are written into the
    descriptor via the same locked ``add_member`` path (trust-on-first-use). Uses a FRESH consumer
    group each run so the redis stream replays recent history; tests inject an InMemoryTransport."""
    import asyncio
    import uuid
    from nmem_exchange import transport as tmod

    descriptor = HiveDescriptor.load(descriptor_path)
    collected: dict[str, dict] = {}

    async def _cb(blob):
        rec = _parse_announcement(blob, descriptor.name)
        if rec is None:
            return
        aid = rec["bundle"]["agent_id"]
        if self_id and aid == self_id:
            return                            # don't rediscover myself
        collected[aid] = rec                  # latest announcement wins (a re-keyed peer)

    grp = group or f"discover-{uuid.uuid4().hex[:8]}"
    owns = transport is None
    if owns:
        transport = tmod.RedisTransport(_resolve_broker_url(descriptor))
    try:
        await transport.subscribe(ROSTER_CHANNEL, grp, _cb)
        await asyncio.sleep(timeout)
    finally:
        if owns:
            # A fresh consumer group per run makes the redis stream replay recent history — but
            # transport.close() only cancels tasks + closes the connection, it never destroys the group,
            # so repeated discovery would accumulate groups on the broker. Destroy it before closing
            # (best-effort, redis-only; InMemoryTransport has no groups).
            await _destroy_consumer_group(transport, ROSTER_CHANNEL, grp)
            await transport.close()

    pinned: list[str] = []
    conflicts: list[str] = []
    if pin:
        # trust-on-FIRST-use: pin only agent_ids we don't already know. A self-signed announcement
        # proves POSSESSION of a key, NOT continuity of identity — so an announcement re-claiming an
        # EXISTING member id with DIFFERENT keys is a takeover attempt (anyone who can reach the broker
        # could publish it). Refuse it; the operator must rotate-key + re-share (or remove-member first)
        # to intentionally re-key a peer. Re-announcing the SAME keys is a harmless idempotent no-op.
        # The check+insert is atomic (per-member descriptor lock) so a concurrent writer can't slip a
        # new pin past the first-use check.
        for aid, rec in collected.items():
            if not rec["verified"]:
                continue                      # never pin an unverifiable bundle
            status = _pin_new_member(descriptor_path, rec["bundle"], nfs=nfs)
            if status == "pinned":
                pinned.append(aid)
            elif status == "conflict":
                conflicts.append(aid)         # known id, different keys → refused (not first-use)
    return {"collected": collected, "pinned": pinned, "conflicts": conflicts,
            "channel": ROSTER_CHANNEL}


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

    pk = sub.add_parser("rotate-key", help="generate a fresh keypair for an existing member")
    pk.add_argument("descriptor")
    pk.add_argument("--as", dest="agent_id", required=True, help="the member whose key to rotate")
    pk.add_argument("--keyfile", help="private keyfile path (default: <descriptor dir>/<id>.key.json)")
    pk.add_argument("--nfs", action="store_true",
                    help="write the new keyfile 0644 for an NFS root-squash bind-mount (else 0600)")

    pan = sub.add_parser("announce",
                         help="publish my signed public bundle to the broker roster (TOFU convenience)")
    pan.add_argument("descriptor")
    pan.add_argument("--as", dest="agent_id", required=True, help="the identity whose key signs the announcement")
    pan.add_argument("--keyfile", help="private keyfile path (default: <descriptor dir>/<id>.key.json)")

    pdisc = sub.add_parser("discover",
                           help="collect peers' announcements from the broker roster; --pin to trust them")
    pdisc.add_argument("descriptor")
    pdisc.add_argument("--as", dest="agent_id", default=None,
                       help="my identity (excluded from results); optional")
    pdisc.add_argument("--timeout", type=float, default=5.0, help="seconds to listen (default 5)")
    pdisc.add_argument("--pin", action="store_true",
                       help="ADD verified discovered peers to the descriptor (trust-on-first-use)")
    pdisc.add_argument("--nfs", action="store_true", help="keep the descriptor world-readable (root-squash)")

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

    if args.cmd == "rotate-key":
        keyfile = args.keyfile or os.path.join(
            os.path.dirname(os.path.abspath(args.descriptor)), f"{args.agent_id}.key.json")
        res = rotate_key(args.descriptor, agent_id=args.agent_id, keyfile=keyfile, nfs=args.nfs)
        print(f"rotated key for '{args.agent_id}' — new keyfile {res['keyfile']} "
              f"({'0644 NFS' if args.nfs else '0600'}) and updated {res['descriptor']}\n")
        print("PUBLIC bundle (re-share with peers, or send them the updated descriptor):")
        print(json.dumps(res["bundle"], indent=2))
        print("\nnote: peers must pick up the new descriptor/bundle or they'll reject your messages.")
        return 0

    if args.cmd == "announce":
        import asyncio
        keyfile = args.keyfile or os.path.join(
            os.path.dirname(os.path.abspath(args.descriptor)), f"{args.agent_id}.key.json")
        res = asyncio.run(announce_identity(args.descriptor, keyfile=keyfile))
        print(f"announced '{res['bundle']['agent_id']}' to '{res['channel']}' on hive "
              f"'{res['hive']}' — peers can now `discover` and pin you.")
        print("note: TOFU — anyone who can reach the broker sees this; the broker password is the "
              "only admission gate. Prefer out-of-band descriptor exchange for stricter trust.")
        return 0

    if args.cmd == "discover":
        import asyncio
        res = asyncio.run(discover_members(args.descriptor, self_id=args.agent_id,
                                           timeout=args.timeout, pin=args.pin, nfs=args.nfs))
        collected = res["collected"]
        if not collected:
            print(f"no announcements on '{res['channel']}' within {args.timeout:g}s "
                  "(are peers running `announce`? is the broker reachable?)")
            return 0
        print(f"discovered {len(collected)} peer(s) on '{res['channel']}':")
        for aid, rec in sorted(collected.items()):
            b = rec["bundle"]
            mark = "✓ verified" if rec["verified"] else "✗ UNVERIFIED (bad signature — will NOT pin)"
            print(f"  {aid}  sign={b['sign_pub'][:12]}…  box={b['box_pub'][:12]}…  [{mark}]")
        if args.pin:
            if res["pinned"]:
                print(f"\npinned {len(res['pinned'])} verified peer(s) into {args.descriptor}: "
                      f"{', '.join(sorted(res['pinned']))}")
                print("trust-on-first-use: verify these fingerprints out-of-band if the broker isn't trusted.")
            else:
                print("\nnothing pinned (no NEW verified peers).")
            if res.get("conflicts"):
                print(f"\nREFUSED {len(res['conflicts'])} takeover attempt(s): "
                      f"{', '.join(sorted(res['conflicts']))} already exist with DIFFERENT keys and were "
                      "NOT replaced. To intentionally re-key a peer, use `rotate-key` + re-share, or "
                      "`remove-member` first.")
        else:
            print("\n(review-only — re-run with --pin to add verified peers to the descriptor)")
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
