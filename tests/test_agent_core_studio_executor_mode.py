"""studio appliance executor-MODE switch (build_agent_app, §33.3).

The appliance picks its executor from config: a top-level ``computer_use:`` block ⇒ the DIRECT
verifier-enforced research runner (never the selector — an LLM Done(True) must not override the
sandbox Verifier); an ``actors:`` block ⇒ the gated selector; neither ⇒ a pure thinker (None).

We fake ``create_agent_app`` to CAPTURE the hooks build_agent_app wires, then drive those real
closures with a stub ctx — so the actual decision logic is exercised with no DB/boot/LLM."""
import os
import types

import pytest

pytest.importorskip("nmem_act")

from nmem.agent_core import studio_server


def _write_agent_dir(tmp_path, agent_yaml: str):
    d = tmp_path / "agent1"
    d.mkdir()
    (d / "capabilities.env").write_text("# test\n")          # what find_agent_dir keys on
    (d / "agent.yaml").write_text(agent_yaml)
    return d


def _capture_hooks(tmp_path, monkeypatch, agent_yaml: str):
    _write_agent_dir(tmp_path, agent_yaml)
    monkeypatch.setattr(studio_server, "DATA_DIR", str(tmp_path))

    captured = {}

    class _StubApp:
        def include_router(self, *a, **k):
            return None

        def get(self, *a, **k):                 # studio_server registers GET /edit on the app
            return lambda fn: fn

    def _fake_create(config, persona, **kw):
        captured["config"] = config
        captured.update(kw)
        return _StubApp(), types.SimpleNamespace(state={}, runtime=None)

    monkeypatch.setattr("nmem.agent_core.host.create_agent_app", _fake_create)
    monkeypatch.setattr("nmem.agent_core.auth.install_session_auth", lambda *a, **k: None)

    studio_server.build_agent_app()
    return captured


def _runtime_stub():
    return types.SimpleNamespace(mem=object(), backend=object(), agent_id="agent1",
                                 bridge=None, hive=None)


class _RouteApp:
    """Captures the handlers extra_routes registers, so a route can be called directly."""

    def __init__(self):
        self.routes = {}

    def get(self, path, **kw):
        def deco(fn):
            self.routes[("GET", path)] = fn
            return fn
        return deco

    def post(self, path, **kw):
        def deco(fn):
            self.routes[("POST", path)] = fn
            return fn
        return deco


_CU_YAML = """
db: {config_key: agent1}
computer_use: {enabled: true, url: "http://sandbox:8080"}
pursuit: {action_type: pursue_knowledge, tool_tag: "research"}
"""

_CU_NO_PURSUIT_YAML = """
db: {config_key: agent1}
computer_use: {enabled: true, url: "http://sandbox:8080"}
"""

_CU_DISABLED_YAML = """
db: {config_key: agent1}
computer_use: {enabled: false, url: "http://sandbox:8080"}
"""

_CU_NULL_PURSUIT_YAML = """
db: {config_key: agent1}
computer_use: {enabled: true, url: "http://sandbox:8080"}
pursuit:
"""

_CU_EMPTY_WITH_ACTORS_YAML = """
db: {config_key: agent1}
computer_use: {}
actors: {webhooks: [{name: ping, url: "http://svc/ping", description: "ping"}]}
"""

_THINKER_YAML = """
db: {config_key: agent1}
"""


def test_null_pursuit_section_does_not_crash_and_is_pinned(tmp_path, monkeypatch):
    # A `pursuit:` YAML section with an empty body parses to None; build_agent_app must normalize it
    # to a dict before pinning action_type (else AttributeError blocks the whole app from starting).
    hooks = _capture_hooks(tmp_path, monkeypatch, _CU_NULL_PURSUIT_YAML)
    assert hooks["config"]["pursuit"]["action_type"] == "pursue_knowledge"


def test_explicit_empty_computer_use_block_counts_as_present(tmp_path, monkeypatch):
    # `computer_use: {}` is an explicit (disabled) research declaration: it suppresses the selector
    # even though actors: is present, and holds a client so a stale capability row can be superseded.
    hooks = _capture_hooks(tmp_path, monkeypatch, _CU_EMPTY_WITH_ACTORS_YAML)
    ctx = types.SimpleNamespace(state={}, runtime=_runtime_stub())
    import asyncio
    asyncio.run(hooks["on_resources_ready"](ctx))
    assert ctx.state.get("sandbox") is not None              # present ⇒ client held
    asyncio.run(hooks["pre_start"](ctx))                     # research agent ⇒ selector NOT assembled
    assert ctx.state.get("reg") is None
    assert hooks["build_executor"](ctx, bridge=None) is None  # disabled ⇒ pure thinker (not selector)


def test_computer_use_config_selects_the_direct_research_runner(tmp_path, monkeypatch):
    hooks = _capture_hooks(tmp_path, monkeypatch, _CU_YAML)
    ctx = types.SimpleNamespace(state={}, runtime=_runtime_stub())

    import asyncio
    asyncio.run(hooks["on_resources_ready"](ctx))            # research mode owns a SandboxClient
    assert ctx.state.get("sandbox") is not None
    assert type(ctx.state["sandbox"]).__name__ == "SandboxClient"

    executor = hooks["build_executor"](ctx, bridge=None)
    assert type(executor).__name__ == "ReferenceRunner"      # the verifier-enforced direct path


def test_omitted_action_type_is_pinned_to_the_research_default(tmp_path, monkeypatch):
    # P1: computer_use with no pursuit.action_type must still dispatch on the SAME name the runtime's
    # auto-default proposal builder uses — build_agent_app pins it in config before the runtime boots.
    hooks = _capture_hooks(tmp_path, monkeypatch, _CU_NO_PURSUIT_YAML)
    assert hooks["config"]["pursuit"]["action_type"] == "pursue_knowledge"
    ctx = types.SimpleNamespace(state={}, runtime=_runtime_stub())
    import asyncio
    asyncio.run(hooks["on_resources_ready"](ctx))
    assert type(hooks["build_executor"](ctx, bridge=None)).__name__ == "ReferenceRunner"


def test_disabled_computer_use_builds_the_client_but_is_a_pure_thinker(tmp_path, monkeypatch):
    # P2: a disabled block still builds the client (so _on_started can register UNAVAILABLE and
    # supersede a stale 'available' row), but selects NO executor — the agent is a pure thinker.
    hooks = _capture_hooks(tmp_path, monkeypatch, _CU_DISABLED_YAML)
    ctx = types.SimpleNamespace(state={}, runtime=_runtime_stub())
    import asyncio
    asyncio.run(hooks["on_resources_ready"](ctx))
    assert ctx.state.get("sandbox") is not None              # client held for capability registration
    assert ctx.state["sandbox"].is_enabled() is False
    asyncio.run(hooks["pre_start"](ctx))
    assert hooks["build_executor"](ctx, bridge=None) is None  # disabled sandbox → pure thinker


def test_tools_reports_research_actuator_so_dashboard_shows_the_act_card(tmp_path, monkeypatch):
    # In research mode the sandbox actuator is NOT in the selector registry (§33.3), so /tools must
    # report it explicitly — else the dashboard hides the Act card and the working /act is unreachable.
    hooks = _capture_hooks(tmp_path, monkeypatch, _CU_YAML)
    rt = _runtime_stub()
    rt._runner = object()                                    # runtime built an executor
    ctx = types.SimpleNamespace(state={}, runtime=rt)
    import asyncio
    asyncio.run(hooks["on_resources_ready"](ctx))
    route_app = _RouteApp()
    hooks["extra_routes"](route_app, ctx)
    res = asyncio.run(route_app.routes[("GET", "/tools")]())
    assert "pursue_knowledge" in [t["name"] for t in res["tools"]]  # non-empty ⇒ card stays visible
    assert res["has_executor"] is True


def test_no_actuator_config_is_a_pure_thinker(tmp_path, monkeypatch):
    hooks = _capture_hooks(tmp_path, monkeypatch, _THINKER_YAML)
    ctx = types.SimpleNamespace(state={}, runtime=_runtime_stub())

    import asyncio
    asyncio.run(hooks["on_resources_ready"](ctx))            # no computer_use → no sandbox built
    assert ctx.state.get("sandbox") is None
    asyncio.run(hooks["pre_start"](ctx))                     # no actors → no registry
    assert hooks["build_executor"](ctx, bridge=None) is None  # pure thinker


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
