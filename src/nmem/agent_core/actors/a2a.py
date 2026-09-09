"""A2A (Agent2Agent) actuator adapter — let this agent delegate a task to ANOTHER agent.

A2A (Google, 2025 → Linux Foundation) is agent-to-agent, not tool-calling: a remote agent
publishes an **Agent Card** (``/.well-known/agent-card.json``) describing itself and a JSON-RPC
endpoint that accepts a **task/message** and returns a result. This adapter fetches the card and
registers ONE ``Action`` per remote agent — "delegate a task to <name>" — so from the local
agent's point of view another whole agent is just one more capability-classed tool, gated and
recorded like everything else.

Deliberately a small, dependency-free JSON-RPC client (httpx only), tolerant of the spec's
evolution: it tries ``message/send`` (current) and falls back to ``tasks/send`` (early A2A), and
extracts the reply text from whichever result shape comes back. Delegating to an agent that can
itself act is treated as MUTATING (the safe posture). This ties into the hive (Step 7); UTCP —
a direct-call variant — would slot onto the same seam later.
"""
from __future__ import annotations

import logging
import uuid

from nmem.agent_core.actors.webhook import _safe_name

log = logging.getLogger(__name__)


async def _fetch_card(url: str, headers: dict) -> dict | None:
    """Best-effort Agent Card fetch. `url` may be the card itself or an agent base URL (then try
    the well-known paths, current spec first)."""
    import httpx
    candidates = [url]
    if not url.rstrip("/").endswith(".json"):
        base = url.rstrip("/")
        candidates = [f"{base}/.well-known/agent-card.json", f"{base}/.well-known/agent.json", url]
    async with httpx.AsyncClient(timeout=15.0) as client:
        for c in candidates:
            try:
                r = await client.get(c, headers=headers)
                if r.is_success:
                    return r.json()
            except Exception:  # noqa: BLE001
                continue
    return None


_A2A_DONE = {"completed"}   # the only terminal state that means the delegated work succeeded


def _result_ok(result) -> bool:
    """Whether an A2A ``result`` represents completed work. A Task carries ``status.state``
    (completed / working / failed / canceled / rejected / …); only ``completed`` is success.
    A plain Message result has no lifecycle state → treat as ok (the agent just answered)."""
    if not isinstance(result, dict):
        return True
    state = str((result.get("status") or {}).get("state") or result.get("state") or "").strip().lower()
    return not state or state in _A2A_DONE


def _extract_text(result) -> str:
    """Pull the reply text out of an A2A result, tolerant of Task / Message / artifact shapes."""
    if result is None:
        return ""
    if isinstance(result, str):
        return result

    def parts_text(parts):
        out = []
        for p in parts or []:
            if isinstance(p, dict):
                out.append(p.get("text") or (p.get("root") or {}).get("text") or "")
        return "\n".join(t for t in out if t)

    if isinstance(result, dict):
        # a Message with parts
        if result.get("parts"):
            t = parts_text(result["parts"])
            if t:
                return t
        # a status message (Task.status.message.parts)
        msg = (result.get("status") or {}).get("message") or {}
        if msg.get("parts"):
            t = parts_text(msg["parts"])
            if t:
                return t
        # artifacts (Task.artifacts[].parts)
        for art in result.get("artifacts") or []:
            t = parts_text(art.get("parts"))
            if t:
                return t
        for key in ("message", "text", "output"):
            if isinstance(result.get(key), str):
                return result[key]
    return str(result)[:2000]


async def _delegate(endpoint: str, instruction: str, headers: dict) -> tuple[bool, str]:
    """Send `instruction` to a remote A2A agent; return (ok, reply_text). Tries the current
    ``message/send`` method, falls back to early A2A ``tasks/send`` on method-not-found."""
    import httpx

    def envelope(method: str) -> dict:
        message = {"role": "user", "messageId": uuid.uuid4().hex,
                   "parts": [{"kind": "text", "text": instruction}]}
        params = {"message": message} if method == "message/send" \
            else {"id": uuid.uuid4().hex, "message": message}
        return {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params}

    async with httpx.AsyncClient(timeout=120.0) as client:
        for method in ("message/send", "tasks/send"):
            try:
                r = await client.post(endpoint, json=envelope(method), headers=headers)
            except Exception as e:  # noqa: BLE001
                return False, f"a2a request error: {type(e).__name__}: {e}"
            try:
                data = r.json()
            except Exception:  # noqa: BLE001
                return r.is_success, r.text[:2000]
            err = data.get("error")
            if err:
                # -32601 = method not found → try the older method name
                if err.get("code") == -32601 and method == "message/send":
                    continue
                return False, f"a2a error {err.get('code')}: {err.get('message')}"
            # A JSON-RPC "result" is not automatically success: a Task can carry
            # status.state=failed/canceled, and an HTTP 4xx/5xx can still return JSON. Reward
            # only a genuinely completed task (or a plain Message result with no lifecycle).
            result = data.get("result")
            return (r.is_success and _result_ok(result)), _extract_text(result)
    return False, "a2a: no supported send method"


async def a2a_action(remote: dict):
    """Register a remote A2A agent as a delegate-a-task ``Action``. Returns ``(action, None)``
    (stateless JSON-RPC per call — no persistent session to close).
    ``remote``: ``{name?, card_url|url, description?, endpoint?, headers?}``."""
    from nmem_act import Action, CapabilityClass
    from nmem_act.registry import ActionResult

    url = remote.get("card_url") or remote["url"]
    headers = remote.get("headers") or {}
    card = await _fetch_card(url, headers)
    name = remote.get("name") or (card or {}).get("name") or "agent"
    endpoint = remote.get("endpoint") or (card or {}).get("url") or url
    desc = remote.get("description") or (card or {}).get("description") \
        or f"Delegate a task to the '{name}' agent and return its reply."
    if card is None:
        log.warning("[actors] a2a: no Agent Card at %s — registering the delegate tool anyway", url)

    async def handler(params: dict) -> ActionResult:
        instruction = (params or {}).get("task") or (params or {}).get("instruction") \
            or (params or {}).get("message") or ""
        if not instruction:
            return ActionResult(success=False, outcome="a2a: no task given", task_success=0.0)
        ok, text = await _delegate(endpoint, instruction, headers)
        return ActionResult(success=ok, outcome=(text[:200] or ("ok" if ok else "error")),
                            observations={"reply": text[:4000]}, task_success=1.0 if ok else 0.0)

    action = Action(
        name=_safe_name(f"a2a_{name}"), handler=handler, capability_class=CapabilityClass.MUTATING,
        description=desc,
        parameters={"type": "object", "properties": {
            "task": {"type": "string", "description": "What to ask the remote agent to do."}},
            "required": ["task"]})
    log.info("[actors] a2a: registered delegate tool for '%s' (endpoint=%s)", name, endpoint)
    return action, None
