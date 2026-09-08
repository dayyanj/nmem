"""Webhook / OpenAPI actuator adapter — register HTTP endpoints as nmem-act Actions.

The no-code default: point the agent at a URL (or an OpenAPI spec) and it becomes a
capability-classed tool the LLM can call, gated + recorded like any other Action. A GET is
READ_ONLY by default; anything else is MUTATING (so the autonomy gate treats it as a write) —
override per tool with ``capability_class``.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def _cap(method: str, declared):
    from nmem_act import CapabilityClass
    if declared:
        return declared if isinstance(declared, CapabilityClass) else CapabilityClass(declared)
    return CapabilityClass.READ_ONLY if method.upper() == "GET" else CapabilityClass.MUTATING


def _safe_name(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", s).strip("_")[:64] or "tool"


def webhook_action(spec: dict):
    """Build an ``Action`` from a webhook spec:
    ``{name, url, method=GET, description, parameters(JSON Schema), capability_class?, headers?, timeout?}``.
    The tool's params become the request — query string for GET, JSON body otherwise — and any
    ``{placeholder}`` in the URL is filled from (and removed from) the params."""
    from nmem_act import Action
    from nmem_act.registry import ActionResult
    import httpx

    name = _safe_name(spec["name"])
    url = spec["url"]
    method = (spec.get("method") or "GET").upper()
    headers = spec.get("headers") or {}
    timeout = float(spec.get("timeout") or 20.0)
    cap = _cap(method, spec.get("capability_class"))

    async def handler(params: dict) -> ActionResult:
        params = dict(params or {})
        target = url
        for k in [k for k in params if "{" + k + "}" in target]:
            target = target.replace("{" + k + "}", str(params.pop(k)))
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if method == "GET":
                    # `params or None`: an EMPTY dict makes httpx replace (drop) any query string
                    # already on the URL — so a URL whose query held a {placeholder} (now filled)
                    # would lose it. None leaves the URL's own query intact.
                    r = await client.get(target, params=params or None, headers=headers)
                else:
                    r = await client.request(method, target, json=params or None, headers=headers)
            try:
                data = r.json()
            except Exception:  # noqa: BLE001
                data = {"body": r.text[:4000]}
            return ActionResult(success=r.is_success, outcome=f"HTTP {r.status_code}",
                                observations={"status": r.status_code, "data": data},
                                task_success=1.0 if r.is_success else 0.0)
        except Exception as e:  # noqa: BLE001
            return ActionResult(success=False, outcome=f"request error: {type(e).__name__}: {e}",
                                task_success=0.0)

    return Action(name=name, handler=handler, capability_class=cap,
                  description=spec.get("description") or f"{method} {url}",
                  parameters=spec.get("parameters") or {"type": "object", "properties": {}})


# ── OpenAPI import ───────────────────────────────────────────────────────────────
def _openapi_base(doc: dict, spec_url: str | None) -> str:
    servers = doc.get("servers") or []
    if servers and servers[0].get("url"):
        base = servers[0]["url"]
        if base.startswith("http"):
            return base
        if spec_url:  # relative server URL → resolve against the spec's origin
            from urllib.parse import urljoin
            return urljoin(spec_url, base)
        return base
    if spec_url:
        from urllib.parse import urlparse
        p = urlparse(spec_url)
        return f"{p.scheme}://{p.netloc}"
    return ""


def _openapi_params(op: dict) -> dict:
    """Merge an operation's path/query parameters + JSON requestBody into one JSON Schema
    object (what the LLM sees as the tool's arguments)."""
    props: dict = {}
    required: list = []
    for p in op.get("parameters", []) or []:
        schema = p.get("schema") or {"type": "string"}
        props[p["name"]] = {**schema, "description": p.get("description", "")}
        if p.get("required"):
            required.append(p["name"])
    body = (((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema")
    if isinstance(body, dict) and body.get("properties"):
        props.update(body["properties"])
        required += list(body.get("required", []))
    out = {"type": "object", "properties": props}
    if required:
        out["required"] = sorted(set(required))
    return out


async def openapi_actions(spec: dict) -> list:
    """Import an OpenAPI spec and produce a webhook ``Action`` per operation.
    ``spec``: ``{spec_url|spec, base_url?, include?(operationIds/paths), headers?}``."""
    import httpx

    doc = spec.get("spec")
    if doc is None:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(spec["spec_url"])
            r.raise_for_status()
            doc = r.json()
    base = spec.get("base_url") or _openapi_base(doc, spec.get("spec_url"))
    include = set(spec.get("include") or [])
    headers = spec.get("headers") or {}
    actions = []
    for path, methods in (doc.get("paths") or {}).items():
        for method, op in (methods or {}).items():
            if method.upper() not in _METHODS or not isinstance(op, dict):
                continue
            op_id = op.get("operationId") or f"{method}_{path}"
            if include and op_id not in include and path not in include:
                continue
            actions.append(webhook_action({
                "name": _safe_name(op_id),
                "url": base.rstrip("/") + path,
                "method": method,
                "headers": headers,
                "description": op.get("summary") or op.get("description") or f"{method.upper()} {path}",
                "parameters": _openapi_params(op),
            }))
    log.info("[actors] openapi import: %d operation(s) from %s", len(actions), spec.get("spec_url") or "inline")
    return actions
