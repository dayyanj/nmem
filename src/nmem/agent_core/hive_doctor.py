"""``hive doctor`` — read-only preflight for an agent's hive membership.

P0 of the hive-membership-UX plan (``_design/hive-membership-ux-plan.md``). Establishing a
hive today is delivered by HAND-editing the ``exchange:`` / ``delegation:`` blocks + cross-pasting
public keys, and every misconfiguration surfaces as a *silent runtime failure* (a decrypt
rejection, a roster mismatch, an unreachable broker, a keyfile the container can't read) rather
than a config-time error. This module turns those into an actionable report — no behaviour change,
nothing written, purely validating what's already on disk + reachable.

It validates the CURRENT hand-written config (Piece A's ``exchange:``/``delegation:`` blocks). The
later phases (a ``hive:`` descriptor + expand-at-load) will GENERATE those blocks; this doctor keeps
validating whatever they expand to, so it stays useful across phases.

Two entry points, sharing the same checks:
  * :func:`static_report` — sync, no I/O beyond reading the local keyfile. Safe to call from a
    synchronous ``/health`` path.
  * :func:`run` — async; runs the static checks PLUS the live broker reachability probe. Used by the
    CLI and by the studio host's cached readiness refresher.

CLI::

    python -m nmem.agent_core.hive_doctor [AGENT_DIR|agent.yaml]

Discovers the appliance's single agent dir when no path is given (same scan as the studio host),
prints a readable report, and exits non-zero if any check is at ``error`` level.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Levels, worst-first for sorting/summary. "error" = broken (peering/delegation cannot work);
# "warn" = enabled-but-degraded or a convention drift; "info" = healthy / nothing to do.
_ERROR, _WARN, _INFO = "error", "warn", "info"
_LEVEL_RANK = {_ERROR: 0, _WARN: 1, _INFO: 2}

# The capability classes the delegation approval gate understands (delegation.TaskTypeSpec).
_CAP_CLASSES = {"READ_ONLY", "MUTATING", "HIGH_RISK"}
# X25519 / Ed25519 public keys are both 32 bytes when base64-decoded.
_PUB_LEN = 32


@dataclass
class Check:
    """One doctor finding. ``ok`` is the pass/fail; ``level`` drives how it surfaces (error/warn/
    info). ``name`` is the check family (broker/identity/roster/…); ``detail`` is optional context."""
    name: str
    ok: bool
    level: str
    message: str
    detail: dict | None = None

    def as_readiness(self) -> dict:
        """Shape matching studio_server ``_readiness`` items so the dashboard renders it uniformly."""
        return {"level": self.level, "capability": f"hive: {self.name}", "message": self.message}


@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, level: str, message: str, detail: dict | None = None) -> Check:
        c = Check(name, ok, level, message, detail)
        self.checks.append(c)
        return c

    @property
    def ok(self) -> bool:
        """No check at ``error`` level. Warnings are degraded-but-running, not a failure."""
        return not any(c.level == _ERROR for c in self.checks)

    @property
    def enabled(self) -> bool:
        """Was there a hive to check at all? (exchange or delegation on)."""
        return not any(c.name == "hive" and c.detail and c.detail.get("solo") for c in self.checks)

    def problems(self) -> list[Check]:
        """Only the actionable (non-info) findings, worst-first — what the dashboard should surface."""
        return sorted((c for c in self.checks if c.level != _INFO),
                      key=lambda c: _LEVEL_RANK[c.level])

    def readiness_items(self) -> list[dict]:
        return [c.as_readiness() for c in self.problems()]


# ── small helpers ────────────────────────────────────────────────────────────────

def _valid_pub(s) -> bool:
    """A base64 public key that decodes to exactly 32 bytes (a typo'd/truncated key fails here
    instead of as an opaque decrypt rejection at runtime)."""
    if not isinstance(s, str) or not s:
        return False
    try:
        return len(base64.b64decode(s, validate=True)) == _PUB_LEN
    except Exception:  # noqa: BLE001
        return False


def _canonical_dm(a: str, b: str) -> str:
    """The sorted-pair DM channel convention: ``dm:<lo>:<hi>``."""
    lo, hi = sorted((a, b))
    return f"dm:{lo}:{hi}"


def _as_mapping(rep: "DoctorReport", value, name: str, check: str) -> dict:
    """Coerce a config value to a dict, recording an error for a wrong shape (e.g. a hand-written
    ``keyring: [alice]``) instead of letting ``.get()`` raise AttributeError and abort the report."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        rep.add(check, False, _ERROR, f"{name} is not a mapping (got {type(value).__name__}).")
        return {}
    return value


def _as_list(rep: "DoctorReport", value, name: str, check: str) -> list:
    """Coerce a config value to a list, recording an error for a wrong shape."""
    if value is None:
        return []
    if not isinstance(value, list):
        rep.add(check, False, _ERROR, f"{name} is not a list (got {type(value).__name__}).")
        return []
    return value


def _coerce_block(rep: "DoctorReport", container: dict, key: str, check: str) -> "tuple[dict, bool]":
    """Coerce ``container[key]`` to a dict, returning ``(mapping, usable)``. For the exchange blocks
    that ``build_exchange`` dereferences (identity/keyring/channels), an ABSENT key is optional
    (``({}, True)`` — the deep check runs and reports what's missing), but an EXPLICIT NULL (``key:``
    with no value) or a wrong shape is a hard startup crash — ``build_exchange`` uses ``.get(key, {})``
    which keeps a present-``None``, so ``Exchange.start()`` then dereferences it. Report those as errors
    and return ``usable=False`` so the caller SKIPS the block's deep check (the error is the finding)."""
    if key not in container:
        return {}, True
    val = container[key]
    if val is None:
        rep.add(check, False, _ERROR,
                f"exchange.{key} is present but null — the exchange crashes at start. Remove the key "
                f"to accept the default, or give it a value.")
        return {}, False
    if not isinstance(val, dict):
        rep.add(check, False, _ERROR, f"exchange.{key} is not a mapping (got {type(val).__name__}).")
        return {}, False
    return val, True


# ── static checks (no network; reads only the local keyfile) ──────────────────────

def static_report(config: dict, *, env: dict | None = None) -> DoctorReport:
    """Validate the ``exchange:`` + ``delegation:`` config + local keyfile. NEVER raises — every
    problem becomes a :class:`Check`, and a catch-all backstop turns any unforeseen malformed-config
    shape into an error finding rather than a traceback (the CLI must print its report and the studio
    readiness refresher must keep its findings). No network I/O (the broker probe is in :func:`run`)."""
    env = env if env is not None else os.environ
    rep = DoctorReport()
    try:
        _collect_static(rep, config, env)
    except Exception as e:  # noqa: BLE001 — the never-raises contract is structural, not per-case
        # Report only the exception class, not str(e): this Check surfaces on the unauthenticated
        # /health endpoint, and a value-bearing message could echo config content. The targeted
        # checks above give actionable messages; this only catches a shape none of them anticipated.
        rep.add("config", False, _ERROR,
                f"hive config could not be fully validated ({type(e).__name__}) — it is malformed. "
                f"Check the exchange/delegation blocks against the docs.")
    return rep


def _collect_static(rep: DoctorReport, config: dict, env: dict) -> None:
    """The static-check body (see :func:`static_report`, which wraps this in the never-raises
    backstop). Appends findings to ``rep``."""
    if not isinstance(config, dict):
        rep.add("config", False, _ERROR, f"agent config is not a mapping (got {type(config).__name__}).")
        return
    # Coerce the top-level containers before reading them — a malformed hand-written shape (e.g.
    # ``exchange: [foo]``) must become an actionable error, not an AttributeError that aborts the
    # whole report (and, via _refresh_readiness, discards every hive finding).
    if config.get("exchange") is not None and not isinstance(config.get("exchange"), dict):
        rep.add("exchange", False, _ERROR,
                f"exchange: config is not a mapping (got {type(config.get('exchange')).__name__}).")
        return
    if config.get("delegation") is not None and not isinstance(config.get("delegation"), dict):
        rep.add("delegation", False, _ERROR,
                f"delegation: config is not a mapping (got {type(config.get('delegation')).__name__}).")
        return
    # A descriptor-backed seed must have been EXPANDED (at boot by the studio host, or by the CLI
    # before this runs) into an exchange block. If hive.descriptor is set but no exchange was derived,
    # expand-at-load failed (missing/invalid descriptor, or this agent isn't a member) → the agent
    # runs SOLO. Report that as an error, not a benign "solo" — the seed clearly INTENDS a hive.
    hive = config.get("hive")
    if isinstance(hive, dict) and hive.get("descriptor") and not config.get("exchange"):
        rep.add("hive", False, _ERROR,
                "hive.descriptor is set but no exchange config was derived — expand-at-load failed "
                "(missing/invalid descriptor, or this agent isn't a member). The agent runs SOLO. "
                "Check the descriptor path and that this agent is listed as a member.")
        return

    excfg = config.get("exchange") or {}
    dcfg = config.get("delegation") or {}
    ex_on = bool(excfg.get("enabled", False))
    deleg_on = bool(dcfg.get("enabled", False))

    # ── enablement / mode ──────────────────────────────────────────────
    if not ex_on and not deleg_on:
        rep.add("hive", True, _INFO, "Solo agent — no hive (exchange + delegation are off).",
                {"solo": True})
        return
    if deleg_on and not ex_on:
        rep.add("hive", False, _ERROR,
                "delegation.enabled is on but exchange.enabled is off — delegation rides the "
                "exchange bus, so it can never send or receive. Enable exchange or disable delegation.")
        return

    # These three are dereferenced by build_exchange/Exchange.start — an explicit null crashes at
    # start (not merely "empty"), so _coerce_block reports null/malformed as an error and signals
    # (usable=False) to skip the deeper check for that block.
    identity, ok_id = _coerce_block(rep, excfg, "identity", "identity")
    keyring, ok_kr = _coerce_block(rep, excfg, "keyring", "keyring")
    channels, ok_ch = _coerce_block(rep, excfg, "channels", "channels")
    self_id = identity.get("agent_id") or ""

    # ── identity + keyfile (the in-container readability + pub-match check) ─────────
    derived_pub = _check_identity(rep, identity, self_id, keyring) if ok_id else None

    # ── keyring well-formedness ────────────────────────────────────────
    if ok_kr:
        _check_keyring(rep, keyring, self_id, derived_pub)

    # ── channels + roster symmetry ─────────────────────────────────────
    if ok_ch:
        _check_channels(rep, channels, keyring, self_id)

    # ── delegation routing ─────────────────────────────────────────────
    if deleg_on:
        _check_delegation(rep, config, dcfg, channels, self_id)

    # ── broker URL presence + ts window (reachability is the live probe) ───────────
    _check_broker_config(rep, excfg, env)

    return


def _check_identity(rep: DoctorReport, identity: dict, self_id: str, keyring: dict) -> str | None:
    """Load the local keyfile as THIS process (== the runtime user in-container), verify it parses,
    and derive its public bundle. Returns the derived sign_pub (for the keyring cross-check) or None.
    The read is itself the NFS root-squash perms check — if the agent can't read it, neither can we.
    ``identity`` is the already-coerced ``exchange.identity`` mapping."""
    keyfile = identity.get("keyfile") or ""
    if not self_id:
        rep.add("identity", False, _ERROR, "exchange.identity.agent_id is missing.")
    if not keyfile:
        rep.add("identity", False, _ERROR, "exchange.identity.keyfile is missing — no private key path.")
        return None
    if not os.path.exists(keyfile):
        rep.add("identity", False, _ERROR,
                f"keyfile not found at {keyfile}. Generate one: python -m nmem_exchange.keygen "
                f"{self_id or '<agent_id>'} {keyfile}")
        return None
    try:
        with open(keyfile) as f:
            secret = json.load(f)
    except PermissionError:
        # The exact P1g-b footgun: over NFS root-squash a 0600 keyfile is unreadable by the
        # container's runtime user (mapped to nobody). The agent would crash-loop on boot.
        rep.add("identity", False, _ERROR,
                f"keyfile {keyfile} exists but is not readable by this user — the agent will fail to "
                f"start. Over NFS root-squash a 0600 key maps to 'nobody'; make it readable "
                f"(chmod 644 for a bind-mounted keyfile).")
        return None
    except (json.JSONDecodeError, ValueError) as e:
        rep.add("identity", False, _ERROR, f"keyfile {keyfile} is not valid JSON: {e}")
        return None
    except OSError as e:
        # A directory at the keyfile path (IsADirectoryError), a file that vanished between the
        # exists() check and open() (a TOCTOU race), or any other filesystem error — report it as a
        # finding rather than letting it abort the CLI / discard all hive readiness items.
        rep.add("identity", False, _ERROR, f"keyfile {keyfile} could not be read: {e}")
        return None

    try:
        from nmem_exchange import crypto
        idn = crypto.identity_from_secret(secret)
    except Exception as e:  # noqa: BLE001 — malformed/short seed, missing fields
        rep.add("identity", False, _ERROR,
                f"keyfile {keyfile} is not a valid exchange identity: {e}")
        return None

    if self_id and idn.agent_id != self_id:
        rep.add("identity", False, _ERROR,
                f"keyfile identity is '{idn.agent_id}' but exchange.identity.agent_id is '{self_id}' "
                f"— mismatched keyfile.")
    # Cross-check: the pub I hold must equal what MY keyring entry advertises (what peers trust).
    # A malformed (non-mapping) self-entry is reported by _check_keyring; skip the cross-check here
    # rather than raising on mine.get().
    mine = keyring.get(self_id)
    if isinstance(mine, dict) and mine:
        if mine.get("sign_pub") != idn.sign_pub or mine.get("box_pub") != idn.box_pub:
            rep.add("identity", False, _ERROR,
                    f"my keyfile's public keys do NOT match keyring['{self_id}'] — peers trust a "
                    f"different key than I hold, so every message I send is rejected. Re-copy my "
                    f"public bundle into the roster.")
        else:
            rep.add("identity", True, _INFO, f"identity '{idn.agent_id}' loaded; keyfile matches roster.")
    else:
        # keyring self-presence is separately reported by _check_keyring; here just confirm the load.
        rep.add("identity", True, _INFO, f"identity '{idn.agent_id}' loaded from keyfile.")

    # Security advisory (not fatal): a world/group-readable private key. 0644 is the NFS workaround,
    # so warn softly rather than error — the operator may have chosen it deliberately.
    try:
        mode = os.stat(keyfile).st_mode & 0o077
        if mode:
            rep.add("identity", True, _WARN,
                    f"keyfile {keyfile} is group/other-readable (mode …{oct(mode)[2:]}). Fine for an "
                    f"NFS bind-mount workaround, but keep it off shared hosts where others can read it.")
    except OSError:
        pass
    return idn.sign_pub


def _check_keyring(rep: DoctorReport, keyring: dict, self_id: str, derived_pub: str | None) -> None:
    """Every roster entry must carry well-formed sign_pub + box_pub; and I must be in my own keyring."""
    if not keyring:
        rep.add("keyring", False, _ERROR, "exchange.keyring is empty — no trusted peers (not even self).")
        return
    if self_id and self_id not in keyring:
        rep.add("keyring", False, _ERROR,
                f"keyring is missing my own entry '{self_id}' — the client wraps encrypted messages to "
                f"self too; add my public bundle to the roster.")
    bad = []
    for aid, keys in keyring.items():
        # A hand-written entry may be a bare string/list instead of a {sign_pub, box_pub} mapping —
        # treat any non-mapping (or one with malformed keys) as bad rather than raising AttributeError.
        if not isinstance(keys, dict) or not _valid_pub(keys.get("sign_pub")) \
                or not _valid_pub(keys.get("box_pub")):
            bad.append(aid)
    if bad:
        rep.add("keyring", False, _ERROR,
                f"keyring entries have malformed public keys (not 32-byte base64): {', '.join(sorted(bad))}. "
                f"A typo'd key fails as an opaque decrypt/verify rejection at runtime.")
    if not bad and (not self_id or self_id in keyring):
        rep.add("keyring", True, _INFO, f"keyring: {len(keyring)} trusted peers, all keys well-formed.")


def _check_channels(rep: DoctorReport, channels: dict, keyring: dict, self_id: str) -> None:
    """Roster symmetry: for each channel I'm in, every member is trusted (in my keyring); policy is
    valid; DM channels follow the canonical sorted-pair name so routing maps agree elsewhere."""
    if not channels:
        rep.add("channels", False, _WARN,
                "exchange.channels is empty — the peer is up but talks on no channels.")
        return
    my_channels = 0
    for ch_id, ch in channels.items():
        # A hand-written `channels: {dm:a:b: encrypted}` makes ch a bare string, not a
        # {policy, roster} mapping — report it rather than raising AttributeError on ch.get().
        if not isinstance(ch, dict):
            rep.add("channels", False, _ERROR,
                    f"channel '{ch_id}' is not a mapping: {ch!r} (want {{policy: …, roster: […]}}).")
            continue
        roster = ch.get("roster") or []
        policy = ch.get("policy")
        if not isinstance(roster, list):
            rep.add("channels", False, _ERROR,
                    f"channel '{ch_id}' roster is not a list: {roster!r}.")
            continue
        if not roster:
            rep.add("channels", False, _ERROR, f"channel '{ch_id}' has an empty roster.")
            continue
        # Roster members must be agent-id strings — a non-string (int, mapping) would raise on the
        # keyring lookup / sort / join below, aborting the report. Report and drop them.
        nonstr = [m for m in roster if not isinstance(m, str)]
        if nonstr:
            rep.add("channels", False, _ERROR,
                    f"channel '{ch_id}' roster has non-string members: {nonstr!r} — want agent-id strings.")
            roster = [m for m in roster if isinstance(m, str)]
            if not roster:
                continue
        if policy not in ("encrypted", "open"):
            rep.add("channels", False, _ERROR,
                    f"channel '{ch_id}' has invalid policy {policy!r} (want 'encrypted' or 'open').")
        if self_id and self_id not in roster:
            continue  # not my channel — nothing to validate from my side
        my_channels += 1
        missing = [m for m in roster if m not in keyring]
        if missing:
            rep.add("channels", False, _ERROR,
                    f"channel '{ch_id}': roster members {', '.join(sorted(missing))} are not in my "
                    f"keyring — I can't encrypt to / verify them, so messages are dropped.")
        if policy == "open":
            rep.add("channels", True, _WARN,
                    f"channel '{ch_id}' is OPEN (signed but not encrypted). Fine for public group chat; "
                    f"use 'encrypted' for direct/delegation traffic.")
        # Canonical DM naming: a 2-member channel should be dm:<sorted pair>, so the implicit
        # convention and delegation.peer_channels can be derived consistently.
        if len(roster) == 2:
            want = _canonical_dm(roster[0], roster[1])
            if ch_id != want:
                rep.add("channels", True, _WARN,
                        f"channel '{ch_id}' is a 2-member DM but not named canonically "
                        f"('{want}'); tooling that derives channel names may not find it.")
    if self_id and my_channels == 0:
        rep.add("channels", False, _WARN,
                f"I ('{self_id}') am not in any channel roster — the peer is up but reaches no one.")
    elif my_channels:
        rep.add("channels", True, _INFO, f"channels: member of {my_channels} channel(s), rosters trusted.")


def _check_delegation(rep: DoctorReport, config: dict, dcfg: dict, channels: dict, self_id: str) -> None:
    """Task types are well-formed; peer_channels reference real channels I'm in; MUTATING/HIGH_RISK
    without an approver will fail-closed (advisory, matches Piece A's approve=None). Also enforces the
    runtime's own wire prerequisites (AgentRuntime._wire_delegation): an isolated graph DB + a graph
    pool — both silently skip delegation if unmet."""
    errs_before = sum(1 for c in rep.checks if c.name == "delegation" and c.level == _ERROR)
    # Runtime prerequisites (config-detectable): _wire_delegation() refuses delegation under a
    # shared_world hive (shared ledgers = cross-agent task theft) and needs a graph DB pool (the
    # ledgers live there). Flag these as errors — otherwise the doctor greenlights delegation the
    # runtime will silently drop.
    hive_cfg = _as_mapping(rep, config.get("hive"), "hive", "delegation")
    mode = hive_cfg.get("mode")
    if isinstance(mode, str) and mode.lower() == "shared_world":
        rep.add("delegation", False, _ERROR,
                "delegation.enabled with hive.mode=shared_world — the runtime REFUSES delegation "
                "(the ledgers are unscoped tables in a shared graph DB; a shared inbox would let one "
                "agent run another's task). Delegation needs an isolated DB.")
    sg = _as_mapping(rep, config.get("symbol_graph"), "symbol_graph", "delegation")
    if not sg.get("enabled", False):   # build_symbol_graph() defaults enabled=False → returns None (no pool)
        rep.add("delegation", False, _ERROR,
                "delegation.enabled but the symbol graph is off (symbol_graph.enabled is not true) — "
                "the delegation ledgers live in the graph DB pool, so with no graph the runtime skips "
                "delegation. Set symbol_graph.enabled: true.")

    task_types = _as_list(rep, dcfg.get("task_types"), "delegation.task_types", "delegation")
    peer_channels = _as_mapping(rep, dcfg.get("peer_channels"), "delegation.peer_channels", "delegation")
    if not task_types and not peer_channels:
        rep.add("delegation", False, _WARN,
                "delegation.enabled is on but declares neither task_types (worker) nor peer_channels "
                "(requester) — it does nothing.")
        return
    # Worker: task types.
    bad_cap = []
    for t in task_types:
        if not isinstance(t, dict):
            # A hand-written `task_types: [ask]` yields a bare string — report it, don't AttributeError.
            rep.add("delegation", False, _ERROR,
                    f"a delegation.task_types entry is not a mapping: {t!r} (want "
                    f"{{type: …, capability_class: …}}).")
            continue
        ttype = t.get("type")
        if not isinstance(ttype, str) or not ttype:
            # TaskTypeRegistry keys by task_type; a non-string (unhashable list) crashes its
            # construction, and a numeric name won't match the str-converted incoming type.
            rep.add("delegation", False, _ERROR,
                    f"a delegation.task_types entry has an invalid 'type': {ttype!r} "
                    f"(want a non-empty string).")
            continue
        # Validate the EXACT string the runtime uses — the studio factory passes capability_class
        # verbatim into TaskTypeSpec and DelegationInbox._gate() compares it case-sensitively to
        # "READ_ONLY". So a lowercase/typo'd/empty value (e.g. "read_only") is NOT read-only at the
        # gate — it falls through to "requires approver", and with Piece A's approve=None it's
        # silently DENIED. An ABSENT key is fine (the factory defaults it to READ_ONLY), so mirror
        # that default here and only flag a present-but-wrong value.
        cap = t.get("capability_class", "READ_ONLY")
        if not isinstance(cap, str) or cap not in _CAP_CLASSES:
            bad_cap.append(f"{ttype}={cap!r}")
        elif cap in ("MUTATING", "HIGH_RISK"):
            rep.add("delegation", True, _WARN,
                    f"task type '{ttype}' is {cap} but no approver is wired (Piece A approve=None) "
                    f"— delegated {cap} work will fail-closed (denied) until an approval gate is added.")
    if bad_cap:
        rep.add("delegation", False, _ERROR,
                f"invalid capability_class on task types: {', '.join(bad_cap)} — the runtime gate is "
                f"CASE-SENSITIVE; want exactly one of {', '.join(sorted(_CAP_CLASSES))}. A wrong value "
                f"is treated as non-read-only and denied (no approver).")
    # Requester: peer channels must exist and I must be a member.
    for target, ch_id in peer_channels.items():
        if not isinstance(ch_id, str):
            rep.add("delegation", False, _ERROR,
                    f"delegation.peer_channels['{target}'] is not a channel-id string: {ch_id!r}.")
            continue
        ch = channels.get(ch_id)
        if ch is None:
            rep.add("delegation", False, _ERROR,
                    f"delegation.peer_channels['{target}'] → '{ch_id}' is not a defined exchange "
                    f"channel — requests to '{target}' can't be sent.")
            continue
        if not isinstance(ch, dict):
            continue   # malformed channel already reported by _check_channels; don't raise here
        roster = ch.get("roster")
        members = [m for m in roster if isinstance(m, str)] if isinstance(roster, list) else []
        # A delegation route MUST be a 2-member channel of EXACTLY {self, target}. DelegationClient
        # sends requests with no target field and DelegationInbox.on_request accepts on every worker
        # rostered on the channel — so a 3rd member double-executes the task, and another worker's
        # result can complete the request. (Malformed roster shapes are reported by _check_channels.)
        if self_id and self_id not in members:
            rep.add("delegation", False, _ERROR,
                    f"I'm not in the roster of '{ch_id}' (routing to '{target}') — can't send there.")
        elif self_id and (len(members) != 2 or set(members) != {self_id, target}):
            rep.add("delegation", False, _ERROR,
                    f"delegation route to '{target}' via '{ch_id}' must be a 2-member channel of "
                    f"exactly [{self_id}, {target}] (got {members}) — an extra member causes duplicate "
                    f"execution and lets another worker's result complete the request.")
    errs_after = sum(1 for c in rep.checks if c.name == "delegation" and c.level == _ERROR)
    if errs_after == errs_before:
        rep.add("delegation", True, _INFO,
                f"delegation: {len(task_types)} task type(s), {len(peer_channels)} route(s).")


def _check_broker_config(rep: DoctorReport, excfg: dict, env: dict) -> None:
    """Static broker config: a redis transport needs NMEX_REDIS_URL (the secret stays in env, off the
    yaml). Reachability is the LIVE probe in :func:`check_broker`. Also sanity-checks ts_window_s."""
    # An ABSENT transport key defaults fine; an EXPLICITLY NULL one (``transport:`` / ``transport: null``)
    # does NOT — peer._resolve() does setdefault("transport", {}) which keeps the None, then crashes
    # assigning tr["url"], and build_transport(None) dereferences it too. Distinguish the two.
    if "transport" in excfg and excfg.get("transport") is None:
        rep.add("broker", False, _ERROR,
                "exchange.transport is present but null — the exchange crashes at start. Remove the "
                "key to accept the redis default, or set 'transport: {kind: redis}'.")
        return
    raw_transport = excfg.get("transport")
    if raw_transport is not None and not isinstance(raw_transport, dict):
        rep.add("broker", False, _ERROR,
                f"exchange.transport is not a mapping (got {type(raw_transport).__name__}) — "
                f"the exchange would crash at start.")
        return
    transport = raw_transport or {}
    kind = transport.get("kind", "redis")
    if kind == "redis":
        if not (env.get("NMEX_REDIS_URL") or transport.get("url")):
            rep.add("broker", False, _ERROR,
                    "exchange transport is redis but NMEX_REDIS_URL is unset — no broker to connect "
                    "to. Set it in the agent's secrets.env.")
        else:
            rep.add("broker", True, _INFO,
                    "broker URL configured (run the live probe to test reachability).")
    elif kind == "memory":
        rep.add("broker", True, _INFO, "in-process 'memory' transport (tests/single-process only).")
    else:
        # build_transport() raises ValueError on anything but redis/memory — a typo here (e.g.
        # 'redsi') would crash the exchange at start, so surface it as a config-time error.
        rep.add("broker", False, _ERROR,
                f"unknown exchange transport kind {kind!r} — want 'redis' or 'memory'.")
    ts = excfg.get("ts_window_s", 300.0)
    try:
        ts = float(ts)
        if ts <= 0:
            rep.add("skew", False, _WARN, f"exchange.ts_window_s={ts} is non-positive — every message "
                                          f"will be dropped as stale. Use e.g. 300.")
    except (TypeError, ValueError):
        # build_exchange() does float(ts_window_s) and raises on a non-number — the exchange won't
        # start at all, so this is an error, not a warning.
        rep.add("skew", False, _ERROR,
                f"exchange.ts_window_s={excfg.get('ts_window_s')!r} is not a number — the exchange "
                f"will fail to start. Use e.g. 300.")


# ── live check: broker reachability + auth ────────────────────────────────────────

async def check_broker(config: dict, *, env: dict | None = None, timeout: float = 3.0) -> Check | None:
    """Live probe: can I reach the broker and AUTH? Returns None when there's nothing to probe
    (exchange off, or a non-redis transport). Never raises — a failure becomes an ``error`` Check."""
    env = env if env is not None else os.environ
    if not isinstance(config, dict):
        return None   # a non-mapping config already errored in static_report; nothing to probe
    excfg = config.get("exchange")
    excfg = excfg if isinstance(excfg, dict) else {}
    if not excfg.get("enabled", False):
        return None
    # An explicitly-null or malformed transport is a static-only config error (the exchange won't
    # start); don't probe — a live "reachable" result would overwrite that error in run().
    if "transport" in excfg and excfg.get("transport") is None:
        return None
    transport = excfg.get("transport")
    if transport is not None and not isinstance(transport, dict):
        return None
    transport = transport or {}
    if transport.get("kind", "redis") != "redis":
        return None
    # Match the runtime's precedence: peer._resolve() OVERRIDES transport.url with NMEX_REDIS_URL
    # when set, so probe the same broker the exchange will actually connect to — else we could pass
    # against an unused yaml URL while the live connection uses (and fails on) the env one.
    url = env.get("NMEX_REDIS_URL") or transport.get("url")
    if not url:
        return Check("broker", False, _ERROR,
                     "NMEX_REDIS_URL is unset — no broker to reach.")
    try:
        import redis.asyncio as aioredis
    except Exception:  # noqa: BLE001 — redis client not installed
        return Check("broker", True, _WARN, "redis client not installed — can't probe the broker.")
    client = None
    try:
        client = aioredis.from_url(url, socket_connect_timeout=timeout, socket_timeout=timeout)
        pong = await client.ping()
        if pong:
            return Check("broker", True, _INFO, "broker reachable and authenticated.")
        return Check("broker", False, _ERROR, "broker PING returned no response.")
    except Exception as e:  # noqa: BLE001 — connection refused, auth error, timeout, DNS…
        # Report ONLY the exception class, never str(e): a malformed URL surfaces the offending
        # token in the message (e.g. a password mis-parsed as a port → "…cast to integer value as
        # 'mysecret'"), and this Check is exposed through the unauthenticated /health endpoint. The
        # class name (ConnectionError / AuthenticationError / TimeoutError / ValueError) is enough to
        # act on without leaking the credential.
        return Check("broker", False, _ERROR,
                     f"broker unreachable or misconfigured ({type(e).__name__}) — check the host, "
                     f"port, and password in NMEX_REDIS_URL.")
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass


async def run(config: dict, *, env: dict | None = None, live: bool = True) -> DoctorReport:
    """Full doctor: static checks + (when ``live``) the broker reachability probe. The live probe
    REPLACES the static broker-URL check's result when it runs (reachability supersedes presence)."""
    rep = static_report(config, env=env)
    if not live:
        return rep
    # Only probe if the static pass didn't already conclude "solo" (no exchange).
    if not rep.enabled:
        return rep
    probe = await check_broker(config, env=env)
    if probe is not None:
        # Drop the static broker-URL-presence check in favour of the live reachability result.
        rep.checks = [c for c in rep.checks if c.name != "broker"]
        rep.checks.append(probe)
    return rep


# ── CLI ────────────────────────────────────────────────────────────────────────

def _load_config(path: str | None) -> tuple[dict, str]:
    """Resolve a config path (agent dir, an agent.yaml, or None → discover the appliance's single
    agent) and load it. Returns (config, resolved_yaml_path)."""
    import yaml

    if path and os.path.isfile(path):
        yaml_path = path
    else:
        agent_dir = path
        if not agent_dir:
            from nmem.agent_core.studio_server import find_agent_dir
            agent_dir = find_agent_dir()
        if not agent_dir:
            raise SystemExit("no agent config found — pass an agent dir or agent.yaml path")
        yaml_path = os.path.join(agent_dir, "agent.yaml")
    if not os.path.isfile(yaml_path):
        raise SystemExit(f"agent.yaml not found at {yaml_path}")
    with open(yaml_path) as f:
        return yaml.safe_load(f) or {}, yaml_path


def _load_env_file(path: str) -> dict:
    """Parse a KEY=VALUE env file (shell-quoted the way studio_server._merge_env_file writes it, so
    values are shlex-unquoted). Returns {} for a missing/unreadable file — never raises."""
    import shlex

    env: dict = {}
    try:
        with open(path) as fh:
            for line in fh:
                line = line.rstrip("\n")
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                if k.startswith("export "):
                    k = k[len("export "):].strip()   # the entrypoint SOURCEs this as shell; `export KEY=…` is valid
                try:
                    parts = shlex.split(v)
                except ValueError:
                    parts = [v]
                env[k] = parts[0] if parts else ""
    except OSError:
        return {}
    return env


def _default_identity(agent_dir: str, config: dict) -> str:
    """The identity expand-at-load defaults to when ``hive.identity`` is omitted — MUST match
    build_agent_app EXACTLY: persona.yaml's agent_id if present, else the db config_key, else the
    literal ``"agent"`` (build_agent_app's Persona default). Otherwise the doctor would validate a
    different member/keyfile than the running agent uses."""
    persona_yaml = os.path.join(agent_dir, "persona.yaml")
    if os.path.exists(persona_yaml):
        try:
            import yaml
            from nmem.agent_core.persona import Persona
            with open(persona_yaml) as f:
                return Persona.from_dict(yaml.safe_load(f)).agent_id
        except Exception:  # noqa: BLE001 — fall back to the db key
            pass
    db = config.get("db")
    db = db if isinstance(db, dict) else {}
    return db.get("config_key") or "agent"


def _cli_env(yaml_path: str) -> dict:
    """The environment a standalone CLI run should see. entrypoint.sh SOURCES the agent's
    capabilities.env + secrets.env into the studio process, but a ``docker exec`` / separate shell
    does NOT inherit them — so NMEX_REDIS_URL (stored in secrets.env) would look unset and the broker
    check would wrongly fail. Reconstruct the agent's effective env by layering those files (as the
    entrypoint does: capabilities then secrets) over the current environment, so the doctor probes
    what the running agent actually uses."""
    agent_dir = os.path.dirname(os.path.abspath(yaml_path))
    # The embedded-exchange broker (NMEM_EMBEDDED_EXCHANGE) persists the EFFECTIVE broker URL to
    # <data>/exchange/broker.env — load it as the LOWEST-precedence default so a docker-exec doctor
    # can validate the embedded broker even though the URL lives only in supervisord's env otherwise.
    # ONLY when the embedded broker is currently ON: a stale broker.env left on the volume from a
    # prior embedded run must NOT override a now-external broker (the flag is a container-level env
    # var, so docker exec sees it too).
    embedded_on = str(os.environ.get("NMEM_EMBEDDED_EXCHANGE", "")).strip().lower() \
        in ("1", "true", "yes", "on")
    data_dir = os.environ.get("STUDIO_DATA_DIR") or os.path.dirname(agent_dir)
    merged = dict(_load_env_file(os.path.join(data_dir, "exchange", "broker.env"))) if embedded_on else {}
    # A real env var wins over the persisted default — EXCEPT an empty NMEX_REDIS_URL, which the
    # supervisor treats as unset (it generates the embedded URL), so it must not clobber the default.
    for k, v in os.environ.items():
        if k == "NMEX_REDIS_URL" and not v:
            continue
        merged[k] = v
    merged.update(_load_env_file(os.path.join(agent_dir, "capabilities.env")))
    merged.update(_load_env_file(os.path.join(agent_dir, "secrets.env")))
    return merged


_ICON = {_ERROR: "✗", _WARN: "!", _INFO: "✓"}


def _print_report(rep: DoctorReport, yaml_path: str) -> None:
    print(f"hive doctor — {yaml_path}\n")
    for c in sorted(rep.checks, key=lambda c: (_LEVEL_RANK[c.level], c.name)):
        print(f"  {_ICON[c.level]} [{c.name}] {c.message}")
    errors = sum(1 for c in rep.checks if c.level == _ERROR)
    warns = sum(1 for c in rep.checks if c.level == _WARN)
    print()
    if not rep.enabled:
        print("hive: not configured (solo agent).")
    elif errors:
        print(f"RESULT: {errors} error(s), {warns} warning(s) — hive will NOT work until fixed.")
    elif warns:
        print(f"RESULT: OK with {warns} warning(s).")
    else:
        print("RESULT: all checks passing.")


def main(argv: list[str] | None = None) -> int:
    import asyncio
    import sys

    logging.basicConfig(level=logging.WARNING)
    argv = sys.argv[1:] if argv is None else argv
    path = argv[0] if argv else None
    config, yaml_path = _load_config(path)
    env = _cli_env(yaml_path)
    # A descriptor-backed seed carries only a compact hive: block — expand it (the same path the
    # studio host runs at boot) so the doctor validates the EFFECTIVE exchange/delegation/broker,
    # not the raw seed (which would look "solo"). Mutates config + env in place; fail-open no-op
    # for a hand-written seed. default_identity falls back to the db config_key (the agent id).
    if isinstance(config, dict):   # a non-mapping seed is diagnosed by run()'s backstop, not here
        from nmem.agent_core.hive_descriptor import expand_into_config
        agent_dir = os.path.dirname(os.path.abspath(yaml_path))
        expand_into_config(config, agent_dir=agent_dir,
                           default_identity=_default_identity(agent_dir, config), env=env, logger=log)
    rep = asyncio.run(run(config, env=env))
    _print_report(rep, yaml_path)
    return 1 if not rep.ok else 0


if __name__ == "__main__":
    raise SystemExit(main())
