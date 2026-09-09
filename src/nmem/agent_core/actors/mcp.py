"""MCP (Model Context Protocol) actuator adapter — register an MCP server's tools as
nmem-act Actions.

Connects to an MCP server over **Streamable HTTP** (the current remote transport) or **stdio**
(a local subprocess), lists its tools, and wraps each as a capability-classed ``Action`` whose
handler calls the tool. The session is held open for the agent's lifetime (via an
``AsyncExitStack``) and closed through the returned ``aclose`` — so tool calls reuse one live
session rather than reconnecting per call.

The official ``mcp`` SDK is imported lazily (an optional dependency): an agent that uses no MCP
server never needs it. Capability class is taken from the tool's ``readOnlyHint`` annotation when
present, else defaults to MUTATING (the safe posture — the autonomy gate then treats it as a
write that needs allowlisting for autonomous use).
"""
from __future__ import annotations

import logging

from nmem.agent_core.actors.webhook import _safe_name

log = logging.getLogger(__name__)


def _content_text(content) -> str:
    """Flatten an MCP CallToolResult.content (list of content blocks) to text."""
    parts = []
    for block in content or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
        elif isinstance(block, dict) and block.get("text"):
            parts.append(block["text"])
    return "\n".join(parts)


def _tool_action(session, tool, prefix: str):
    from nmem_act import Action, CapabilityClass
    from nmem_act.registry import ActionResult

    ann = getattr(tool, "annotations", None)
    # readOnlyHint (v1) / read_only_hint (v2) — either signals a read-only tool
    read_only = bool(getattr(ann, "read_only_hint", None) or getattr(ann, "readOnlyHint", None)) \
        if ann is not None else False
    cap = CapabilityClass.READ_ONLY if read_only else CapabilityClass.MUTATING

    async def handler(params: dict) -> ActionResult:
        try:
            res = await session.call_tool(tool.name, arguments=params or {})
        except Exception as e:  # noqa: BLE001
            return ActionResult(success=False, outcome=f"mcp call error: {type(e).__name__}: {e}",
                                task_success=0.0)
        text = _content_text(getattr(res, "content", None))
        is_err = bool(getattr(res, "isError", False))
        return ActionResult(success=not is_err, outcome=(text[:200] or ("error" if is_err else "ok")),
                            observations={"content": text[:4000]},
                            task_success=0.0 if is_err else 1.0)

    # input_schema (SDK v2, snake_case) / inputSchema (v1, camelCase)
    schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) \
        or {"type": "object", "properties": {}}
    return Action(name=_safe_name(f"{prefix}_{tool.name}"), handler=handler, capability_class=cap,
                  description=getattr(tool, "description", None) or f"MCP tool {tool.name}",
                  parameters=schema)


async def connect_mcp(server: dict):
    """Connect to an MCP server and return ``(actions, aclose)``.

    ``server``: ``{name, transport: 'http'|'stdio', url?, headers?, command?, args?, env?, only?}``.
    ``transport`` defaults to 'http' when a ``url`` is given, else 'stdio'. ``only`` restricts to a
    subset of tool names. ``aclose`` closes the live session (call it on shutdown)."""
    from contextlib import AsyncExitStack

    from mcp import ClientSession

    stack = AsyncExitStack()
    transport = (server.get("transport") or ("http" if server.get("url") else "stdio")).lower()
    try:
        if transport in ("http", "streamable-http", "streamable_http"):
            import mcp.client.streamable_http as _sh
            hdrs = server.get("headers") or None
            newer = getattr(_sh, "streamable_http_client", None)
            if newer is not None:
                # Newer SDK: streamable_http_client(url, *, http_client=…) — NO headers kwarg.
                # Auth headers must ride a pre-built client via create_mcp_http_client(headers=…).
                kwargs = {}
                if hdrs and hasattr(_sh, "create_mcp_http_client"):
                    kwargs["http_client"] = await stack.enter_async_context(
                        _sh.create_mcp_http_client(headers=hdrs))
                streams = await stack.enter_async_context(newer(server["url"], **kwargs))
            else:
                # Older SDK: streamablehttp_client(url, headers=…) directly.
                streams = await stack.enter_async_context(
                    _sh.streamablehttp_client(server["url"], headers=hdrs))
            read, write = streams[0], streams[1]      # (read, write, get_session_id) in newer SDKs
        else:
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client
            params = StdioServerParameters(command=server["command"], args=server.get("args") or [],
                                           env=server.get("env") or None)
            read, write = await stack.enter_async_context(stdio_client(params))

        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        listed = await session.list_tools()
    except Exception as e:  # noqa: BLE001 — a bad server must not kill the agent boot
        log.warning("[actors] MCP connect failed for %s: %s", server.get("name") or server, e, exc_info=True)
        await stack.aclose()
        return [], None

    only = set(server.get("only") or [])
    prefix = server.get("name") or "mcp"
    actions = [_tool_action(session, t, prefix) for t in listed.tools if not only or t.name in only]
    log.info("[actors] MCP '%s' (%s): %d tool(s) [%s]", prefix, transport, len(actions),
             ", ".join(t.name for t in listed.tools))

    async def aclose():
        await stack.aclose()

    return actions, aclose
