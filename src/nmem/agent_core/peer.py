"""Peer exchange — agent-to-agent messaging over the nmem-exchange bus.

An agent sends and receives peer messages (challenges + replies) over a secured bus;
inbound challenges run through the agent's OWN cognition (in character) and every
exchange is logged into its own nmem. Isolation is preserved: the bus carries only the
sealed envelope; each agent's memory of the conversation is entirely its own.

This is the reusable glue graduated from the reference agent's ``service/peer.py``. The generic
parts live here — the exchange lifecycle, ephemeral-channel handling, inbound routing
(log → correlate a comms reply → answer a challenge → reply threaded → log), and the
:class:`PeerExchangeSink` that lets the communication drive speak over the bus. The two
agent-specific things are INJECTED: ``on_challenge`` (the agent's cognition) and ``mem``
+ ``agent_id`` (whose memory to log into). Config-gated by ``exchange.enabled``.
"""
from __future__ import annotations

import inspect
import logging
import os
import time
from collections.abc import Awaitable, Callable

from nmem.agent_core.continuity import continuity_block, record_turn_checkpoint

log = logging.getLogger(__name__)

# (sender, text) -> the agent's in-character reply to a peer challenge. A handler MAY take a
# third argument, ``continuity`` (the living wake snapshot), to ground its reply in where the
# agent currently is; PeerExchange passes it only when the handler's signature accepts it.
OnChallenge = Callable[..., Awaitable[str]]


def _resolve(ex_cfg: dict) -> dict:
    """Inject the Redis URL from env (NMEX_REDIS_URL) so the secret isn't in yaml."""
    tr = ex_cfg.setdefault("transport", {})
    if os.environ.get("NMEX_REDIS_URL"):
        tr["url"] = os.environ["NMEX_REDIS_URL"]
    return ex_cfg


class PeerExchange:
    """An agent's connection to the nmem-exchange bus.

    Args:
        config: the agent config dict (reads ``config['exchange']``).
        mem: the agent's MemorySystem — inbound/outbound messages are journalled here.
        agent_id: author id for those journal entries.
        on_challenge: the agent's cognition — answers an inbound peer challenge.
        comms_channel: the channel the communication drive speaks on (via `comms_sink`);
            "" disables the comms sink.
        ephemeral_prefixes: channels with these prefixes respond live but are NOT
            persisted (test/dev). Defaults from PEER_EPHEMERAL_PREFIXES env.
    """

    def __init__(self, config: dict, *, mem, agent_id: str, on_challenge: OnChallenge,
                 comms_channel: str = "", ephemeral_prefixes: tuple[str, ...] | None = None,
                 continuity: bool = True) -> None:
        self._config = config
        self._mem = mem
        self._agent_id = agent_id
        self._on_challenge = on_challenge
        self._continuity = continuity
        # Does the handler accept the optional 3rd ``continuity`` arg? Detected once, so a
        # 2-arg handler (sender, text) keeps working unchanged while a 3-arg one is grounded.
        self._on_challenge_wants_continuity = self._accepts_continuity(on_challenge)
        self._ex = None
        self._ephemeral = ephemeral_prefixes or tuple(
            p.strip() for p in os.environ.get("PEER_EPHEMERAL_PREFIXES", "test:,dev:").split(",")
            if p.strip())
        # The sink exists even before start() (delivery no-ops until started), so a host
        # can wire the comms loop unconditionally — matching pre-graduation behaviour.
        self._comms_sink = PeerExchangeSink(self, comms_channel) if comms_channel else None

    @staticmethod
    def _accepts_continuity(fn) -> bool:
        """True if the on_challenge handler takes a 3rd positional/keyword arg (continuity).
        Fail-safe: assume it does NOT on any introspection failure, so we never pass an arg a
        2-arg handler can't accept."""
        try:
            params = [p for p in inspect.signature(fn).parameters.values()
                      if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
            return len(params) >= 3 or any(
                p.kind is p.VAR_POSITIONAL for p in inspect.signature(fn).parameters.values())
        except (TypeError, ValueError):
            return False

    @property
    def comms_sink(self):
        return self._comms_sink

    @property
    def started(self) -> bool:
        return self._ex is not None

    # ── lifecycle ─────────────────────────────────────────────────
    async def start(self):
        """Build + start the Exchange if enabled. Returns it, or None when gated off."""
        ex_cfg = self._config.get("exchange") or {}
        if not ex_cfg.get("enabled", False):
            log.info("[peer] exchange gated OFF (exchange.enabled=false)")
            return None
        from nmem_exchange.client import build_exchange
        ex = build_exchange(_resolve(ex_cfg))
        try:
            await ex.start(self._handle)   # publish to state ONLY once fully started
        except Exception:
            await ex.close()
            raise
        self._ex = ex
        log.info("[peer] exchange live as %s on channels %s", ex.id.agent_id, list(ex.channels))
        return ex

    async def close(self) -> None:
        if self._ex is None:
            return
        try:
            await self._ex.close()
        finally:
            self._ex = None

    async def send(self, channel: str, kind: str, text: str, **kw):
        return await self._ex.send(channel, kind, text, **kw) if self._ex else None

    async def send_challenge(self, channel: str, prompt: str):
        """Outbound: challenge a peer on a channel."""
        if self._ex is None:
            raise RuntimeError("exchange not started")
        return await self._ex.send(channel, "challenge", prompt)

    # ── inbound routing ───────────────────────────────────────────
    async def _handle(self, meta, body) -> None:
        """Handle one accepted (verified + decrypted) inbound peer message."""
        text = body.decode("utf-8", errors="replace")
        sender, channel = meta["from"], meta["channel"]
        kind, msg_id = meta["kind"], meta["msg_id"]
        ephemeral = channel.startswith(self._ephemeral)
        if ephemeral:
            log.info("[peer] ephemeral channel %s — responding without persisting", channel)

        if not ephemeral:
            await self._log("peer_msg", f"{kind} from {sender}", f"[{channel}] {text}",
                            importance=6, record_type="evidence")

        # Is this a reply to an utterance the comms drive DELIVERED? If so, correlate it
        # → comms assessment/learning, and don't treat it as an inbound challenge.
        irt = meta.get("in_reply_to")
        if irt and self._comms_sink is not None:
            try:
                if await self._comms_sink.correlate_reply(irt, text):
                    log.info("[peer] correlated reply to delivered utterance (in_reply_to=%s)", irt)
                    return
            except Exception:  # noqa: BLE001
                log.warning("[peer] comms reply correlation failed", exc_info=True)

        if kind != "challenge":
            log.info("[peer] %s from %s logged", kind, sender)
            return

        # Answering a peer challenge IS a reasoning turn — ground it in the agent's living
        # continuity (where it is, its open loops / drives / narrative) when the handler
        # accepts it. NOT on ephemeral channels: a query-driven wake runs search(), whose
        # entity auto-journaling would persist activity — breaking the ephemeral channel's
        # no-persistence guarantee. Fail-open: a continuity hiccup never blocks the reply.
        cont = ""
        if self._continuity and self._on_challenge_wants_continuity and not ephemeral:
            cont = await continuity_block(self._mem, self._agent_id, query=text)
        reply = "(unable to respond)"
        try:
            if self._on_challenge_wants_continuity:
                reply = await self._on_challenge(sender, text, cont)
            else:
                reply = await self._on_challenge(sender, text)
        except Exception as e:  # noqa: BLE001
            log.warning("[peer] on_challenge failed: %s", e)

        if self._ex is not None:
            try:
                await self._ex.send(channel, "response", reply, in_reply_to=msg_id)
            except Exception as e:  # noqa: BLE001
                log.warning("[peer] send response failed: %s", e)

        if not ephemeral:
            await self._log("peer_msg", f"response to {sender}", f"[{channel}] {reply}",
                            importance=6, record_type="judgment")
            # Advance the continuity checkpoint: this peer turn is now "where I left off".
            if self._continuity:
                await record_turn_checkpoint(self._mem, self._agent_id, text, reply)
        log.info("[peer] answered %s's challenge (%d chars)", sender, len(reply))

    async def _log(self, entry_type: str, title: str, content: str, **kw) -> None:
        try:
            await self._mem.journal.add(agent_id=self._agent_id, entry_type=entry_type,
                                        title=title, content=content, **kw)
        except Exception:  # noqa: BLE001
            pass


class PeerExchangeSink:
    """A communication-drive ``ChannelSink`` that speaks over the peer exchange.

    The agent delivers a worth-saying utterance to its peer as a ``challenge`` (the kind
    a peer's handler responds to), tracks the msg_id, and when the peer's threaded
    ``response`` arrives (correlated in ``PeerExchange._handle``) routes the raw reply
    back to the utterance for assessment/learning. Channel-specific code lives ONLY here."""

    def __init__(self, peer: PeerExchange, channel: str) -> None:
        self._peer = peer
        self._channel = channel
        self._awaiting: dict[str, tuple] = {}   # msg_id -> (Utterance, sent_monotonic)

    async def deliver(self, utterance) -> bool:
        if not self._peer.started:
            return False
        try:
            msg_id = await self._peer.send(self._channel, "challenge", utterance.text)
            if msg_id:
                self._awaiting[str(msg_id)] = (utterance, time.monotonic())
            return True
        except Exception:  # noqa: BLE001
            log.warning("[peer] utterance deliver failed", exc_info=True)
            return False

    async def correlate_reply(self, in_reply_to, text: str) -> bool:
        entry = self._awaiting.pop(str(in_reply_to), None)
        if entry is None:
            return False
        utt, sent = entry
        await utt.record_response(text, latency_s=time.monotonic() - sent)
        return True
