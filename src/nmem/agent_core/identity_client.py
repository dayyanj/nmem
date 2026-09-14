"""Thin, fail-open client to the nmem-identity sidecars — the ONLY coupling between the chat path
and the (separate, optional) identity service.

The chat path stays agent-agnostic and identity-optional: ``agent_core.speaker`` resolves who is
speaking, and *when* a text-style author signal is wanted it calls this client to (1) embed the
accumulated authored chat text (LUAR sidecar ``/embed``) and (2) fuse that vote with contextual
priors into a calibrated identity (matcher ``/resolve``). Both live in nmem-identity, reached over
HTTP so the library carries no torch / asyncpg / model weights.

Everything here is **fail-open and off by default**: the endpoints are read from env
(``NMEM_IDENTITY_TEXT_EMBED_URL`` / ``NMEM_IDENTITY_MATCHER_URL``); if either is unset, or httpx is
absent, or the call errors/times out, the helper returns ``None`` and the caller falls back to the
Phase-2 local resolution — identity is a soft prior, never on the turn's critical path. httpx is
imported lazily (as elsewhere in agent_core) so importing this module never requires the dep.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

log = logging.getLogger(__name__)

# Short timeout: identity is a soft prior gathered on a cadence, never worth stalling a turn for.
_TIMEOUT_S = float(os.getenv("NMEM_IDENTITY_HTTP_TIMEOUT", "2.0") or "2.0")


def _embed_url() -> Optional[str]:
    return (os.getenv("NMEM_IDENTITY_TEXT_EMBED_URL", "").strip() or None)


def _matcher_url() -> Optional[str]:
    return (os.getenv("NMEM_IDENTITY_MATCHER_URL", "").strip() or None)


def endpoints_configured() -> bool:
    """True only when BOTH the embed and matcher endpoints are set. The text-identity path needs the
    two in sequence (embed → resolve), so the caller checks this before accumulating or making any
    request — else a half-configured deployment would buffer + embed text it can never resolve."""
    return _embed_url() is not None and _matcher_url() is not None


def _client(timeout: float):
    """A lazily-constructed httpx.AsyncClient, or None if httpx isn't installed (fail-open)."""
    try:
        import httpx
    except Exception:                                # pragma: no cover - httpx absent → feature off
        log.debug("[identity] httpx not installed; text-identity disabled")
        return None
    return httpx.AsyncClient(timeout=timeout)


async def _post(base: str, path: str, body: dict) -> Optional[dict]:
    client = _client(_TIMEOUT_S)
    if client is None:
        return None
    url = base.rstrip("/") + path
    try:
        async with client:
            resp = await client.post(url, json=body)
            if resp.status_code != 200:
                log.debug("[identity] %s -> HTTP %s", path, resp.status_code)
                return None
            data = resp.json()
            return data if isinstance(data, dict) else None
    except Exception:                           # noqa: BLE001 - any transport error → soft None
        log.debug("[identity] POST %s failed (non-fatal)", url, exc_info=True)
        return None


async def embed_documents(documents: list[str], *, pasted_ratio: Optional[float] = None
                          ) -> Optional[dict]:
    """Embed an episode of authored chat turns via the LUAR sidecar ``/embed``. Returns the sidecar
    payload (``embedding``, ``authored_chars``, ``quality``, ``regime``, ...) or None. A
    ``regime='text_insufficient'`` response has NO ``embedding`` — the caller treats that as "no
    signal yet" (below the authored-length floor)."""
    base = _embed_url()
    if not base or not documents:
        return None
    body: dict[str, Any] = {"documents": documents}
    if pasted_ratio is not None:
        body["pasted_ratio"] = pasted_ratio
    return await _post(base, "/embed", body)


async def resolve(*, text_embedding: Optional[list[float]] = None,
                  text_length: Optional[int] = None, text_quality: Optional[float] = None,
                  text_learn: bool = False, session_id: Optional[str] = None,
                  context: Optional[list[dict]] = None) -> Optional[dict]:
    """Fuse the author vote + contextual priors into a calibrated identity via the matcher
    ``/resolve``. Returns the fusion payload (``speaker``, ``grounding``, ``authorize_ok``,
    ``ask_identity``, ...) or None on any failure. ``context`` is a list of
    ``{kind, name|candidate, log_odds, strong}`` prior signals."""
    base = _matcher_url()
    if not base:
        return None
    body: dict[str, Any] = {"session_id": session_id, "context": context or []}
    if text_embedding is not None:
        body["text"] = {"embedding": text_embedding, "length": text_length,
                        "quality": text_quality, "learn": bool(text_learn)}
    if not body.get("text") and not body["context"]:
        return None                                  # nothing to resolve
    return await _post(base, "/resolve", body)
