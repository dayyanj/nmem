"""The actor seam turns declarative config into gated nmem-act Actions. These lock the
protocol-agnostic bits (capability mapping, OpenAPI param merge, config→registry) without a
network or an LLM — the live LLM-driven loop is validated against a real backend separately."""
import asyncio

import pytest

# The actor layer builds nmem-act Actions; nmem-act is an OPTIONAL sibling (the actor feature's
# dep), not installed in nmem's own CI extras. Skip the whole file when it's absent rather than
# erroring the run — the live/full actor coverage runs where nmem-act is installed.
pytest.importorskip("nmem_act")

from nmem.agent_core.actors import assemble_registry  # noqa: E402
from nmem.agent_core.actors.webhook import (  # noqa: E402
    _openapi_params, _safe_name, openapi_actions, webhook_action)


def test_webhook_capability_defaults_by_method():
    from nmem_act import CapabilityClass
    get = webhook_action({"name": "look", "url": "http://x/a", "method": "GET"})
    post = webhook_action({"name": "do it!", "url": "http://x/a", "method": "POST"})
    forced = webhook_action({"name": "z", "url": "http://x", "method": "GET",
                             "capability_class": "high_risk"})
    assert get.capability_class == CapabilityClass.READ_ONLY      # GET observes → read_only
    assert post.capability_class == CapabilityClass.MUTATING      # write verb → mutating
    assert forced.capability_class == CapabilityClass.HIGH_RISK   # explicit override wins
    assert post.name == "do_it"                                   # name sanitized for tool schemas


def test_openapi_params_merge_path_and_body():
    op = {
        "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
        "requestBody": {"content": {"application/json": {"schema": {
            "type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"]}}}},
    }
    schema, query, header = _openapi_params(op, {})
    assert set(schema["properties"]) == {"id", "note"}
    assert set(schema["required"]) == {"id", "note"}
    assert query == [] and header == []          # id is in:path (URL placeholder), note is body


def test_openapi_params_resolves_refs_and_locations():
    # $ref request body (as FastAPI emits) + a required query param + a header param.
    doc = {"components": {"schemas": {"Note": {
        "type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}}}
    op = {
        "parameters": [
            {"name": "q", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "X-Token", "in": "header", "schema": {"type": "string"}}],
        "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Note"}}}},
    }
    schema, query, header = _openapi_params(op, doc)
    assert "text" in schema["properties"]        # $ref resolved → body field present (was empty before)
    assert query == ["q"] and header == ["X-Token"]   # locations retained for correct routing


def test_safe_name():
    assert _safe_name("GET /v1/items/{id}") == "GET_v1_items_id"


def test_mcp_connect_failure_is_graceful():
    # a bad MCP server must not kill agent boot: connect_mcp returns ([], None), not an exception.
    import pytest
    pytest.importorskip("mcp")
    from nmem.agent_core.actors.mcp import connect_mcp

    async def go():
        actions, aclose = await connect_mcp(
            {"name": "nope", "transport": "stdio", "command": "definitely-not-a-real-binary-xyz"})
        assert actions == [] and aclose is None
    asyncio.run(go())


def test_plugin_mount_registers_and_skips_broken(tmp_path):
    # a good plugin registers its Action; a broken one is skipped, not fatal.
    (tmp_path / "good.py").write_text(
        "from nmem_act import Action, CapabilityClass\n"
        "from nmem_act.registry import ActionResult\n"
        "async def _run(p):\n"
        "    return ActionResult(success=True, outcome='ok', observations={'echo': p})\n"
        "def register(reg):\n"
        "    reg.register(Action(name='echo', handler=_run,\n"
        "        capability_class=CapabilityClass.READ_ONLY, description='echo',\n"
        "        parameters={'type':'object','properties':{'x':{'type':'string'}}}))\n")
    (tmp_path / "broken.py").write_text("this is not valid python :(\n")
    (tmp_path / "_ignored.py").write_text("raise RuntimeError('should never import')\n")

    from nmem_act import ActionRegistry
    from nmem.agent_core.actors.plugins import load_plugins
    reg = ActionRegistry()
    loaded = load_plugins(str(tmp_path), reg)
    assert reg.names() == ["echo"]                    # good registered; broken + _ignored skipped
    assert any("good.py" in p for p in loaded) and len(loaded) == 1
    assert reg.get("echo").parameters["properties"]["x"]["type"] == "string"


def test_webhook_get_preserves_url_query_string():
    # regression: an empty params dict must NOT wipe a query string already on the URL (httpx
    # replaces the query when given params) — a {placeholder} in the query, once filled, was lost.
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        from nmem.agent_core.actors.webhook import webhook_action

        async def go():
            qp = webhook_action({"name": "q", "url": f"http://127.0.0.1:{port}/time?city={{city}}", "method": "GET"})
            await qp.handler({"city": "Auckland"})              # query placeholder → filled + kept
            fq = webhook_action({"name": "f", "url": f"http://127.0.0.1:{port}/s?key=abc", "method": "GET"})
            await fq.handler({})                                 # fixed query, no params → kept
        asyncio.run(go())
        assert seen == ["/time?city=Auckland", "/s?key=abc"]
    finally:
        srv.shutdown()


def test_openapi_inherits_path_item_params_and_resolves_nested_refs():
    # codex re-review: Path-Item-level params apply to every operation, and $refs nested in arrays
    # must be inlined (else the exported tool schema references components it doesn't ship).
    doc = {
        "components": {"schemas": {
            "Item": {"type": "object", "properties": {"sku": {"type": "string"}}},
            "Order": {"type": "object", "required": ["lines"], "properties": {
                "lines": {"type": "array", "items": {"$ref": "#/components/schemas/Item"}}}}}},
        "paths": {"/orders/{oid}": {
            "parameters": [{"name": "oid", "in": "path", "required": True, "schema": {"type": "string"}}],
            "post": {"operationId": "create_order", "requestBody": {"content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/Order"}}}}}}},
    }

    async def go():
        actions = await openapi_actions({"spec": doc, "base_url": "http://svc"})
        props = actions[0].parameters["properties"]
        assert "oid" in props                                  # inherited path-item param
        assert "lines" in props                                # body $ref resolved
        assert props["lines"]["items"]["properties"]["sku"]["type"] == "string"  # NESTED ref inlined
        assert "$ref" not in str(props["lines"])
    asyncio.run(go())


def test_webhook_url_encodes_path_placeholder_values():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{}')

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        async def go():
            a = webhook_action({"name": "g", "url": f"http://127.0.0.1:{port}/items/{{id}}", "method": "GET"})
            await a.handler({"id": "a/b c"})       # slash + space must be percent-encoded, not injected
        asyncio.run(go())
        assert seen == ["/items/a%2Fb%20c"]
    finally:
        srv.shutdown()


def test_webhook_routes_query_and_header_params_by_location():
    # codex P2: a POST's query/header params must NOT be buried in the JSON body.
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
            seen["path"] = self.path
            seen["token"] = self.headers.get("X-Token")
            seen["body"] = json.loads(body) if body else {}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        from nmem.agent_core.actors.webhook import webhook_action

        async def go():
            a = webhook_action({"name": "mk", "url": f"http://127.0.0.1:{port}/items", "method": "POST",
                                "query_params": ["q"], "header_params": ["X-Token"]})
            await a.handler({"q": "hello", "X-Token": "sekret", "text": "body-field"})
        asyncio.run(go())
        assert seen["path"] == "/items?q=hello"      # query param → query string
        assert seen["token"] == "sekret"             # header param → header
        assert seen["body"] == {"text": "body-field"}  # only the real body field remains
    finally:
        srv.shutdown()


def test_a2a_failed_task_state_is_not_success():
    # codex P2: a JSON-RPC result carrying status.state=failed must NOT be rewarded as success.
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def _send(self, obj):
            b = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if "agent-card.json" in self.path:
                self._send({"name": "flaky", "url": f"http://127.0.0.1:{self.server.server_address[1]}/rpc"})
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            self._send({"jsonrpc": "2.0", "id": req["id"], "result": {
                "status": {"state": "failed", "message": {"parts": [{"kind": "text", "text": "nope"}]}}}})

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        from nmem.agent_core.actors.a2a import a2a_action

        async def go():
            action, _ = await a2a_action({"card_url": f"http://127.0.0.1:{srv.server_address[1]}"})
            res = await action.handler({"task": "do it"})
            assert res.success is False and res.task_success == 0.0   # failed task ≠ success
        asyncio.run(go())
    finally:
        srv.shutdown()


def test_utcp_manual_import_is_field_tolerant_and_skips_non_http():
    # inline manual with three tools: two HTTP (different field-name conventions), one CLI (skipped).
    async def go():
        manual = {"utcp_version": "1.0", "tools": [
            {"name": "get_weather", "description": "weather",
             "inputs": {"type": "object", "properties": {"city": {"type": "string"}}},
             "tool_provider": {"provider_type": "http", "http_method": "GET",
                               "url": "https://api.example/weather?city={city}"}},
            {"name": "post_note", "description": "note",
             "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
             "call_template": {"call_template_type": "http", "method": "POST", "url": "https://api.example/notes"}},
            {"name": "run_local", "description": "cli",
             "tool_provider": {"provider_type": "cli", "command": "ls"}},   # non-HTTP → skipped
        ]}
        from nmem.agent_core.actors.utcp import utcp_actions
        from nmem_act import CapabilityClass
        actions = await utcp_actions({"manual": manual, "name": "wx"})
        by = {a.name: a for a in actions}
        assert set(by) == {"wx_get_weather", "wx_post_note"}          # cli tool skipped
        assert by["wx_get_weather"].capability_class == CapabilityClass.READ_ONLY   # GET
        assert by["wx_post_note"].capability_class == CapabilityClass.MUTATING       # POST
        assert by["wx_get_weather"].parameters["properties"]["city"]["type"] == "string"
    asyncio.run(go())


def test_assemble_registry_wires_utcp():
    async def go():
        manual = {"tools": [{"name": "ping", "tool_provider": {"type": "http", "url": "http://svc/ping"}}]}
        reg, aclose = await assemble_registry({"utcp": [{"manual": manual, "name": "svc"}]})
        assert reg.names() == ["svc_ping"]
        await aclose()
    asyncio.run(go())


def test_a2a_delegate_with_tasks_send_fallback():
    # a remote agent that only speaks early A2A (tasks/send) → the adapter must fall back from
    # message/send and still get the reply. Also exercises Agent Card fetch + text extraction.
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    holder = {}

    class H(BaseHTTPRequestHandler):
        def _send(self, obj):
            b = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if "agent-card.json" in self.path:
                self._send({"name": "old-agent", "description": "legacy",
                            "url": f"http://127.0.0.1:{holder['port']}/rpc"})
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            if req["method"] == "message/send":       # legacy agent doesn't support it
                self._send({"jsonrpc": "2.0", "id": req["id"],
                            "error": {"code": -32601, "message": "method not found"}})
            else:                                       # tasks/send → a Task with a status message
                instr = req["params"]["message"]["parts"][0]["text"]
                self._send({"jsonrpc": "2.0", "id": req["id"], "result": {
                    "status": {"message": {"parts": [{"kind": "text", "text": f"did: {instr}"}]}}}})

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    holder["port"] = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        from nmem.agent_core.actors.a2a import a2a_action

        async def go():
            action, _ = await a2a_action({"card_url": f"http://127.0.0.1:{holder['port']}"})
            assert action.name == "a2a_old_agent"
            res = await action.handler({"task": "reticulate splines"})
            assert res.success and res.observations["reply"] == "did: reticulate splines"
        asyncio.run(go())
    finally:
        srv.shutdown()


def test_assemble_registry_from_webhooks():
    async def go():
        reg, aclose = await assemble_registry({"webhooks": [
            {"name": "weather", "url": "http://svc/weather", "method": "GET",
             "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}},
            {"name": "post_note", "url": "http://svc/notes", "method": "POST"},
        ]})
        assert set(reg.names()) == {"weather", "post_note"}
        weather = reg.get("weather")
        assert weather.parameters["properties"]["city"]["type"] == "string"
        await aclose()   # no live sessions → no-op, but must not raise
    asyncio.run(go())
