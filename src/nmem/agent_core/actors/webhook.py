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
    # OpenAPI encodes each argument's location; these route named params to the query string or
    # request headers regardless of method (so a POST's required query/header param isn't buried
    # in the JSON body). Empty for a plain webhook → today's behavior.
    query_ps = set(spec.get("query_params") or [])
    header_ps = set(spec.get("header_params") or [])

    async def handler(params: dict) -> ActionResult:
        from urllib.parse import quote
        params = dict(params or {})
        target = url
        for k in [k for k in params if "{" + k + "}" in target]:   # path placeholders
            # URL-encode: a value with '/', '?', '#', or spaces would otherwise break the path
            # or inject extra path/query segments.
            target = target.replace("{" + k + "}", quote(str(params.pop(k)), safe=""))
        req_headers = dict(headers)
        for k in [k for k in params if k in header_ps]:            # in: header
            req_headers[k] = str(params.pop(k))
        # query: everything remaining for GET; the explicitly-tagged params for other methods.
        query = params if method == "GET" else {k: params.pop(k) for k in list(params) if k in query_ps}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if query:
                    # MERGE into the URL's existing query (httpx `params=` would REPLACE it,
                    # dropping a fixed ?k=v or a {placeholder} already substituted into the query).
                    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
                    u = urlsplit(target)
                    q = parse_qsl(u.query, keep_blank_values=True) + [(k, str(v)) for k, v in query.items()]
                    target = urlunsplit(u._replace(query=urlencode(q)))
                if method == "GET":
                    r = await client.get(target, headers=req_headers)
                else:
                    r = await client.request(method, target, json=params or None, headers=req_headers)
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


def _resolve_ref(node, doc: dict):
    """Resolve a local ``$ref`` (``#/components/schemas/Foo``) against the document. Non-local or
    absent refs return the node unchanged. Bounded depth so a cyclic ref can't loop forever."""
    for _ in range(8):
        if not (isinstance(node, dict) and isinstance(node.get("$ref"), str) and node["$ref"].startswith("#/")):
            return node
        cur = doc
        for part in node["$ref"][2:].split("/"):
            cur = (cur or {}).get(part) if isinstance(cur, dict) else None
        node = cur or {}
    return node


def _deep_resolve(node, doc: dict, depth: int = 6):
    """Recursively inline local ``$ref``s inside a schema — not just the top level. A FastAPI body
    with ``items: list[Item]`` nests ``items.$ref``; left unresolved the exported tool schema
    points at ``#/components/…`` that isn't shipped, so strict tool validators reject it. Walks
    properties / items / additionalProperties / allOf|anyOf|oneOf, depth-bounded for cyclic refs."""
    node = _resolve_ref(node, doc)
    if depth <= 0 or not isinstance(node, dict):
        return node
    out = {}
    for k, v in node.items():
        if k == "properties" and isinstance(v, dict):
            out[k] = {pk: _deep_resolve(pv, doc, depth - 1) for pk, pv in v.items()}
        elif k in ("items", "additionalProperties") and isinstance(v, dict):
            out[k] = _deep_resolve(v, doc, depth - 1)
        elif k in ("allOf", "anyOf", "oneOf") and isinstance(v, list):
            out[k] = [_deep_resolve(x, doc, depth - 1) for x in v]
        else:
            out[k] = v
    return out


def _openapi_params(op: dict, doc: dict, inherited: list | None = None) -> tuple[dict, list, list]:
    """Merge an operation's parameters (+ any inherited from the Path Item) + JSON requestBody into
    one JSON Schema, deep-resolving ``$ref``s. Returns ``(schema, query_param_names,
    header_param_names)`` so the caller routes each argument to its OpenAPI ``in:`` location."""
    props: dict = {}
    required: list = []
    query_names: list = []
    header_names: list = []
    # Path-Item-level parameters apply to every operation; operation-level ones override by (name,in).
    merged: dict = {}
    for p in (inherited or []) + (op.get("parameters", []) or []):
        p = _resolve_ref(p, doc)
        if isinstance(p, dict) and p.get("name"):
            merged[(p["name"], p.get("in"))] = p
    for p in merged.values():
        schema = _deep_resolve(p.get("schema") or {"type": "string"}, doc)
        props[p["name"]] = {**schema, "description": p.get("description", "")}
        if p.get("required"):
            required.append(p["name"])
        loc = p.get("in")
        if loc == "query":
            query_names.append(p["name"])
        elif loc == "header":
            header_names.append(p["name"])
        # in:path is carried by the URL's {name} placeholder (handled in the webhook handler)
    body = (((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema")
    body = _deep_resolve(body, doc) if body else None
    if isinstance(body, dict) and body.get("properties"):
        props.update(body["properties"])
        required += list(body.get("required", []))
    out = {"type": "object", "properties": props}
    if required:
        out["required"] = sorted(set(required))
    return out, query_names, header_names


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
        # Parameters declared on the Path Item apply to every operation under it (OpenAPI spec).
        path_params = (methods or {}).get("parameters", []) if isinstance(methods, dict) else []
        for method, op in (methods or {}).items():
            if method.upper() not in _METHODS or not isinstance(op, dict):
                continue
            op_id = op.get("operationId") or f"{method}_{path}"
            if include and op_id not in include and path not in include:
                continue
            schema, query_names, header_names = _openapi_params(op, doc, inherited=path_params)
            actions.append(webhook_action({
                "name": _safe_name(op_id),
                "url": base.rstrip("/") + path,
                "method": method,
                "headers": headers,
                "description": op.get("summary") or op.get("description") or f"{method.upper()} {path}",
                "parameters": schema,
                "query_params": query_names,      # route these to the query string, not the body
                "header_params": header_names,    # route these to request headers
            }))
    log.info("[actors] openapi import: %d operation(s) from %s", len(actions), spec.get("spec_url") or "inline")
    return actions
