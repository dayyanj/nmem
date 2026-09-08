"""UTCP (Universal Tool Calling Protocol) actuator adapter — register a UTCP manual's tools
as nmem-act Actions.

UTCP's premise (the lightweight MCP alternative): instead of running a wrapper server, an agent
reads a **manual** — a JSON document listing tools, each with a *call template* describing how to
hit the tool's NATIVE endpoint directly. For the HTTP provider (the dominant transport) a UTCP
tool is, in effect, a webhook with a declared input schema — so this adapter parses the manual and
builds each HTTP tool through the same ``webhook_action`` call path, landing them on the one gated,
outcome-recording executor like every other actuator.

Tolerant of UTCP's evolving field names (``tool_provider`` vs ``call_template`` vs ``provider``;
``inputs`` vs ``input_schema``; ``http_method`` vs ``method``). Non-HTTP providers (cli / graphql /
websocket / …) are skipped with a log — HTTP is what maps cleanly onto the actor seam today; the
rest can graduate onto this same adapter later.
"""
from __future__ import annotations

import logging

from nmem.agent_core.actors.webhook import _safe_name, webhook_action

log = logging.getLogger(__name__)

_TOOLS_KEYS = ("tools", "manual")
_PROVIDER_KEYS = ("tool_provider", "call_template", "provider")
_TYPE_KEYS = ("provider_type", "call_template_type", "type")
_METHOD_KEYS = ("http_method", "method", "http_verb")
_INPUTS_KEYS = ("inputs", "input_schema", "parameters")


def _first(d: dict, keys, default=None):
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return default


def _http_provider(tool: dict) -> dict | None:
    """The tool's HTTP call template, or None if it isn't an HTTP tool (skip those)."""
    prov = _first(tool, _PROVIDER_KEYS, {}) or {}
    ptype = str(_first(prov, _TYPE_KEYS, "") or "").lower()
    if ptype and ptype not in ("http", "https", "rest", "openapi"):
        return None                        # a non-HTTP transport — not handled here
    return prov if prov.get("url") else None


def _tool_spec(tool: dict, prefix: str) -> dict | None:
    prov = _http_provider(tool)
    if prov is None:
        return None
    name = tool.get("name") or "tool"
    return {
        "name": _safe_name(f"{prefix}_{name}" if prefix else name),
        "url": prov["url"],
        "method": str(_first(prov, _METHOD_KEYS, "GET")).upper(),
        "headers": prov.get("headers") or {},
        "description": tool.get("description") or f"UTCP tool {name}",
        "parameters": _first(tool, _INPUTS_KEYS, {"type": "object", "properties": {}}),
    }


async def utcp_actions(spec: dict) -> list:
    """Build Actions from a UTCP manual. ``spec``:
    ``{manual: {...}} | {manual_url|url: "…", headers?, name?(namespace), only?(tool names)}``.
    Fetches the manual if a URL is given, else uses the inline manual."""
    manual = spec.get("manual")
    if manual is None:
        import httpx
        url = spec.get("manual_url") or spec["url"]
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url, headers=spec.get("headers") or {})
            r.raise_for_status()
            manual = r.json()

    tools = _first(manual, _TOOLS_KEYS, []) or []
    prefix = spec.get("name") or ""
    only = set(spec.get("only") or [])
    actions = []
    skipped = 0
    for tool in tools:
        if only and tool.get("name") not in only:
            continue
        ts = _tool_spec(tool, prefix)
        if ts is None:
            skipped += 1
            continue
        actions.append(webhook_action(ts))
    log.info("[actors] UTCP manual '%s': %d HTTP tool(s), %d non-HTTP skipped",
             prefix or spec.get("manual_url") or "inline", len(actions), skipped)
    return actions
