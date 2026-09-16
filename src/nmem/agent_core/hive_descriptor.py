"""Hive descriptor — the single shareable source of truth for hive membership (P1).

P1 of the hive-membership-UX plan (``_design/hive-membership-ux-plan.md``). Today each agent's
``exchange:`` (keyring + per-pair channels) and ``delegation:`` (peer_channels) blocks are
hand-maintained on every host — O(N²) pasted public keys and two channel maps that must agree.
This module introduces a compact **hive descriptor** (name + broker + members' PUBLIC bundles) as
the one artifact exchanged between hosts, and :func:`expand` DERIVES the verbose ``exchange:``/
``delegation:`` config Piece A reads from it — so an operator maintains membership in ONE place and
the wiring is generated (never hand-copied).

The descriptor holds PUBLIC keys only (safe to share); each host keeps its own PRIVATE keyfile
locally (never in the descriptor). The broker secret stays in an env var (named by ``broker.url_env``,
default ``NMEX_REDIS_URL``), never on the descriptor.

Pure library: no I/O beyond optional yaml load/save, no env. The studio host wires
:func:`expand` at boot (``hive:`` block → effective config) — that's P1.2; the ``hive`` CLI
(create/join/add-member) is P1.3. :func:`expand`'s output is validated by :mod:`hive_doctor`.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field

# X25519 / Ed25519 public keys are both 32 bytes when base64-decoded (mirrors hive_doctor._valid_pub;
# kept local so this module has no dependency on the doctor).
_PUB_LEN = 32

# capability_class per accepted task type. Only `ask` (READ_ONLY, answered by AskExecutor) exists
# today; extend as new task executors graduate. Unknown types default to READ_ONLY (safe: the
# runtime gate fail-closes anything non-read-only without an approver anyway).
_ACCEPT_CAPABILITY = {"ask": "READ_ONLY"}


def _valid_pub(s) -> bool:
    if not isinstance(s, str) or not s:
        return False
    try:
        return len(base64.b64decode(s, validate=True)) == _PUB_LEN
    except Exception:  # noqa: BLE001
        return False


def canonical_dm(a: str, b: str) -> str:
    """The canonical sorted-pair DM channel name: ``dm:<lo>:<hi>``. Deterministic across hosts so
    both ends derive the SAME channel id from the descriptor without coordination."""
    lo, hi = sorted((a, b))
    return f"dm:{lo}:{hi}"


class HiveDescriptorError(ValueError):
    """A descriptor is structurally invalid (bad member shape, duplicate ids, missing name)."""


@dataclass
class Member:
    """One hive member's PUBLIC identity — safe to share in the descriptor."""
    agent_id: str
    sign_pub: str
    box_pub: str

    def validate(self) -> None:
        if not isinstance(self.agent_id, str) or not self.agent_id:
            raise HiveDescriptorError(f"member has an invalid agent_id: {self.agent_id!r}")
        if not _valid_pub(self.sign_pub) or not _valid_pub(self.box_pub):
            raise HiveDescriptorError(
                f"member {self.agent_id!r} has malformed public keys (want 32-byte base64).")

    def bundle(self) -> dict:
        return {"agent_id": self.agent_id, "sign_pub": self.sign_pub, "box_pub": self.box_pub}


@dataclass
class HiveDescriptor:
    """The shareable membership artifact. ``broker_url_env`` names the env var holding the broker
    URL (the secret stays in env, off-descriptor); ``ts_window_s`` is the freshness window all
    members agree on."""
    name: str
    broker_url_env: str = "NMEX_REDIS_URL"
    ts_window_s: float = 300.0
    members: list[Member] = field(default_factory=list)

    # ── (de)serialization ────────────────────────────────────────────────
    @classmethod
    def from_dict(cls, d: dict) -> "HiveDescriptor":
        """Parse a descriptor mapping. Accepts either the raw descriptor fields or a wrapper with a
        top-level ``hive:`` key (the on-disk file form)."""
        if not isinstance(d, dict):
            raise HiveDescriptorError(f"descriptor is not a mapping (got {type(d).__name__}).")
        if "hive" in d and isinstance(d.get("hive"), dict):
            d = d["hive"]
        name = d.get("name")
        if not isinstance(name, str) or not name:
            raise HiveDescriptorError("descriptor is missing a 'name'.")
        broker = d.get("broker")
        broker = broker if isinstance(broker, dict) else {}
        url_env = broker.get("url_env") or "NMEX_REDIS_URL"
        if not isinstance(url_env, str) or not url_env:
            raise HiveDescriptorError(
                f"descriptor.broker.url_env must be a non-empty string (got {url_env!r}).")
        raw_members = d.get("members")
        if raw_members is None:
            raw_members = []
        if not isinstance(raw_members, list):
            raise HiveDescriptorError(f"descriptor.members is not a list (got {type(raw_members).__name__}).")
        members: list[Member] = []
        for m in raw_members:
            if not isinstance(m, dict):
                raise HiveDescriptorError(f"descriptor member is not a mapping: {m!r}")
            members.append(Member(m.get("agent_id"), m.get("sign_pub"), m.get("box_pub")))
        desc = cls(
            name=name,
            broker_url_env=url_env,
            ts_window_s=_coerce_ts(broker.get("ts_window_s", 300.0)),
            members=members,
        )
        desc.validate()
        return desc

    def to_dict(self) -> dict:
        """The on-disk / shareable form (wrapped under a top-level ``hive:`` key)."""
        return {"hive": {
            "name": self.name,
            "broker": {"url_env": self.broker_url_env, "ts_window_s": self.ts_window_s},
            "members": [m.bundle() for m in self.members],
        }}

    @classmethod
    def load(cls, path: str) -> "HiveDescriptor":
        import yaml
        with open(path) as f:
            return cls.from_dict(yaml.safe_load(f) or {})

    def save(self, path: str) -> None:
        import yaml
        with open(path, "w") as f:
            yaml.safe_dump(self.to_dict(), f, sort_keys=False)

    # ── membership ops ────────────────────────────────────────────────────
    def validate(self) -> None:
        seen: set[str] = set()
        for m in self.members:
            m.validate()
            if m.agent_id in seen:
                raise HiveDescriptorError(f"duplicate member '{m.agent_id}' in descriptor.")
            seen.add(m.agent_id)

    def member(self, agent_id: str) -> "Member | None":
        return next((m for m in self.members if m.agent_id == agent_id), None)

    def agent_ids(self) -> list[str]:
        return [m.agent_id for m in self.members]

    def upsert_member(self, member: Member) -> None:
        """Add a member, or replace an existing one with the same agent_id (a re-keyed peer)."""
        member.validate()
        self.members = [m for m in self.members if m.agent_id != member.agent_id]
        self.members.append(member)


def _coerce_ts(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 300.0


# ── expand-at-load: descriptor → effective exchange:/delegation: config ─────────────

def expand(descriptor: HiveDescriptor, *, identity: str, keyfile: str,
           delegation: dict | None = None) -> dict:
    """Derive the verbose ``exchange:`` (and optional ``delegation:``) config for member ``identity``
    from the descriptor — the block Piece A's studio factories read. Kills the O(N²) hand-copied
    keyring, the hand-derived per-pair channels, and the duplicated ``delegation.peer_channels`` map.

    - keyring = every member's public bundle (incl self — the client wraps encrypted messages to self).
    - channels = one canonical encrypted ``dm:<lo>:<hi>`` per pair that includes me (my DMs).
    - transport = ``{kind: redis}``; the URL is injected at runtime from ``NMEX_REDIS_URL``
      (``peer._resolve``), so it isn't embedded here.
    - delegation (when ``delegation.enabled``): task_types from ``accepts`` (capability_class from the
      known table, default READ_ONLY) + peer_channels routing each peer over our shared DM.

    Raises :class:`HiveDescriptorError` if ``identity`` isn't a member (the caller — expand-at-load —
    catches this and leaves the agent solo rather than booting a broken hive)."""
    if descriptor.member(identity) is None:
        raise HiveDescriptorError(
            f"identity '{identity}' is not a member of hive '{descriptor.name}' "
            f"(members: {', '.join(descriptor.agent_ids()) or 'none'}).")
    if not keyfile:
        raise HiveDescriptorError(f"no keyfile path for identity '{identity}'.")

    keyring = {m.agent_id: {"sign_pub": m.sign_pub, "box_pub": m.box_pub} for m in descriptor.members}
    peers = [aid for aid in descriptor.agent_ids() if aid != identity]
    channels = {
        canonical_dm(identity, peer): {"policy": "encrypted", "roster": sorted([identity, peer])}
        for peer in peers
    }
    exchange = {
        "enabled": True,
        "identity": {"agent_id": identity, "keyfile": keyfile},
        "transport": {"kind": "redis"},           # url from NMEX_REDIS_URL at runtime
        "ts_window_s": descriptor.ts_window_s,
        "keyring": keyring,
        "channels": channels,
    }
    out: dict = {"exchange": exchange}

    delegation = delegation or {}
    if delegation.get("enabled"):
        accepts = delegation.get("accepts") or []
        # `accepts: ask` (a bare string) would iterate into task types 'a','s','k' — a config that
        # passes structural checks but rejects every real `ask`. Require an explicit list of
        # non-empty strings and fail loudly (expand_into_config's fail-open logs it).
        if not isinstance(accepts, list) or any(not isinstance(t, str) or not t for t in accepts):
            raise HiveDescriptorError(
                f"delegation.accepts must be a list of task-type strings (got {accepts!r}).")
        task_types = [
            {"type": t, "capability_class": _ACCEPT_CAPABILITY.get(t, "READ_ONLY"),
             "description": f"Delegated '{t}' task"}
            for t in accepts
        ]
        peer_channels = {peer: canonical_dm(identity, peer) for peer in peers}
        out["delegation"] = {"enabled": True, "task_types": task_types, "peer_channels": peer_channels}
    return out


def _canonicalize_dm_name(ch: str) -> str:
    """Rewrite a ``dm:a:b`` channel name to its canonical sorted form so it matches the channels
    :func:`expand` derives; leave any other channel name unchanged."""
    parts = ch.split(":")
    if len(parts) == 3 and parts[0] == "dm":
        return canonical_dm(parts[1], parts[2])
    return ch


def _preserve_legacy_channels(derived_channels: dict, legacy_channels, identity: str,
                              member_ids: set) -> None:
    """Add channels from a replaced hand-written exchange block that the canonical derivation renamed,
    so in-flight delegation tasks (whose ledger rows hold the OLD channel name) still route until they
    drain. Only channels I'm in whose roster ⊆ hive members are kept (else the doctor would flag an
    untrusted member). Mutates ``derived_channels`` in place."""
    if not isinstance(legacy_channels, dict):
        return
    for ch_id, ch in legacy_channels.items():
        if ch_id in derived_channels or not isinstance(ch, dict):
            continue
        roster = ch.get("roster")
        if not isinstance(roster, list):
            continue
        members = [str(m) for m in roster]
        if identity in members and set(members) <= member_ids:
            derived_channels[ch_id] = {"policy": "encrypted", "roster": sorted(members)}


def expand_into_config(config: dict, *, agent_dir: str, default_identity: str, env,
                       logger=None) -> bool:
    """Expand-at-load (shared by the studio host AND the ``hive doctor`` CLI): if ``config`` carries a
    ``hive:`` block with a ``descriptor``, DERIVE ``config['exchange']`` / ``config['delegation']`` in
    place and resolve the broker URL into ``env['NMEX_REDIS_URL']``. No-op (returns False) without
    ``hive.descriptor``, so a hand-written seed is byte-identical to today. Fail-OPEN: any error
    (missing/invalid descriptor, identity not a member) is logged and leaves ``config`` unchanged.

    ``env`` is mutated (the host passes ``os.environ`` so ``peer._resolve`` sees the URL; the CLI
    passes its reconstructed env dict so its broker probe does). Returns True iff expansion happened."""
    import os as _os

    hive = config.get("hive")
    if not isinstance(hive, dict) or not hive.get("descriptor"):
        return False
    # The descriptor is the SOURCE OF TRUTH — drop any hand-written exchange:/delegation: up front.
    # This also makes a FAILED expansion detectable: on failure no derived block is written, so a
    # stale hand-written exchange can't mask the failure from the doctor (which keys off exchange
    # presence). Capture the outgoing channels first (for in-flight migration, below).
    legacy_ex = config.get("exchange")
    legacy_channels = legacy_ex.get("channels") if isinstance(legacy_ex, dict) else None
    had_manual = bool(config.get("exchange") or config.get("delegation"))
    config.pop("exchange", None)
    config.pop("delegation", None)
    if had_manual and logger is not None:
        logger.warning("[hive] descriptor is set — DERIVING exchange:/delegation: and discarding the "
                       "hand-written block(s) in the seed")
    try:
        desc_path = hive["descriptor"]
        if not _os.path.isabs(desc_path):
            desc_path = _os.path.join(agent_dir, desc_path)
        descriptor = HiveDescriptor.load(desc_path)

        identity = hive.get("identity") or default_identity
        keyfile = hive.get("keyfile") or f"{identity}.key.json"
        if not _os.path.isabs(keyfile):
            keyfile = _os.path.join(agent_dir, keyfile)

        derived = expand(descriptor, identity=identity, keyfile=keyfile,
                         delegation=hive.get("delegation"))

        # Migration safety: if the seed being replaced named a channel differently from the canonical
        # one (e.g. live `dm:michelle:djai` vs canonical `dm:djai:michelle`), the delegation ledger
        # holds in-flight rows with the OLD request_channel/reply_to. Preserve those legacy channels
        # (as extra subscriptions) so pending tasks still route until they drain — but only when the
        # roster is a subset of hive members (else the doctor would flag an untrusted member). Once the
        # operator removes the hand-written block, only canonical channels remain.
        _preserve_legacy_channels(derived["exchange"]["channels"], legacy_channels,
                                  identity, set(descriptor.agent_ids()))

        config["exchange"] = derived["exchange"]
        if "delegation" in derived:
            config["delegation"] = derived["delegation"]

        # Canonicalize a comms.channel dm-pair so the communication drive speaks on a channel that
        # actually exists in the derived exchange (else PeerExchangeSink KeyErrors on every send).
        comms = config.get("comms")
        if isinstance(comms, dict) and isinstance(comms.get("channel"), str):
            comms["channel"] = _canonicalize_dm_name(comms["channel"])

        # Broker selection: the descriptor's named env var is the SOURCE OF TRUTH. When it names a
        # var other than NMEX_REDIS_URL, that var's value wins over any pre-existing NMEX_REDIS_URL
        # (a seed migrated from an old broker must follow its descriptor). And if the named var is
        # UNSET, we must NOT let a stale NMEX_REDIS_URL masquerade as the descriptor's broker — clear
        # it so the doctor reports "no broker" and the runtime fails clearly, rather than silently
        # connecting to a broker the descriptor didn't select. peer._resolve/the CLI probe read
        # NMEX_REDIS_URL.
        url_env = descriptor.broker_url_env or "NMEX_REDIS_URL"
        if url_env != "NMEX_REDIS_URL":
            selected = env.get(url_env)
            if selected:
                env["NMEX_REDIS_URL"] = selected
            else:
                env.pop("NMEX_REDIS_URL", None)

        if logger is not None:
            logger.info("[hive] '%s': expanded membership for '%s' (%d peers)",
                        descriptor.name, identity, max(0, len(descriptor.members) - 1))
        return True
    except Exception:  # noqa: BLE001 — fail-open; never crash-boot / crash the CLI on a bad descriptor
        if logger is not None:
            logger.warning("[hive] expand-at-load failed — leaving config as-is (agent may run solo)",
                           exc_info=True)
        return False
