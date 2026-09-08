"""The actor seam turns declarative config into gated nmem-act Actions. These lock the
protocol-agnostic bits (capability mapping, OpenAPI param merge, config→registry) without a
network or an LLM — the live LLM-driven loop is validated against a real backend separately."""
import asyncio

from nmem.agent_core.actors import assemble_registry
from nmem.agent_core.actors.webhook import _openapi_params, _safe_name, webhook_action


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
    schema = _openapi_params(op)
    assert set(schema["properties"]) == {"id", "note"}
    assert set(schema["required"]) == {"id", "note"}


def test_safe_name():
    assert _safe_name("GET /v1/items/{id}") == "GET_v1_items_id"


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
