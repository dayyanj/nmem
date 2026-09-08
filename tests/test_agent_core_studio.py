"""The studio router is the wizard backend: it must expose the capability map, refuse an
incomplete flag-set, write a correct-by-construction agent WITHOUT leaking the api_key, and
hand the secrets to the host by name only. LLM-touching endpoints (test-llm/list-models) are
exercised for their spec-validation + key-never-returned contract, not against a live model."""
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nmem.agent_core.studio import create_studio_app, make_studio_router


def _client(**kw):
    app = FastAPI()
    app.include_router(make_studio_router(**kw))
    return TestClient(app)


def test_studio_app_serves_wizard_and_router(tmp_path):
    # the docker image runs create_studio_app: the SPA at / and the router under /studio.
    c = TestClient(create_studio_app(config_dir=str(tmp_path)))
    home = c.get("/")
    assert home.status_code == 200 and "text/html" in home.headers["content-type"]
    body = home.text
    assert "nmem-studio" in body
    # the productionised SPA fetches the live catalog (no inlined data) and posts to the router
    assert "fetch('/studio/catalog')" in body
    for path in ("/studio/test-llm", "/studio/list-models", "/studio/create"):
        assert path in body
    assert c.get("/studio/catalog").status_code == 200


def test_catalog_exposes_map_presets_and_groups():
    r = _client().get("/studio/catalog")
    assert r.status_code == 200
    body = r.json()
    flags = {c["flag"] for c in body["capabilities"]}
    assert "NMEM_SYM_DRIVES_ENABLED" in flags
    # every capability carries what the pills need to enforce dependencies
    sample = body["capabilities"][0]
    assert {"flag", "group", "summary", "requires", "default", "enabled"} <= set(sample)
    assert set(body["presets"]) == {"memory", "reflective", "full_cognition"}
    # presets are dependency-complete as served (recall pulls in drives/concerns/autonomy)
    full = set(body["presets"]["full_cognition"]["flags"])
    assert {"NMEM_SYM_CONCERNS_ENABLED", "NMEM_AUTONOMY__ENABLED"} <= full
    assert "drives" in body["groups"]


def test_create_auto_completes_dependency_closure(tmp_path):
    # OUTWARD_ACTIONS requires DRIVES_ENABLED + HONEST_DISCHARGE; the user selected only the
    # leaf. The writer auto-completes (env is always dependency-complete), and create REPORTS
    # what it pulled in rather than silently expanding or rejecting.
    c = _client(config_dir=str(tmp_path))
    r = c.post("/studio/create", json={
        "agent_id": "digger",
        "enabled": ["NMEM_SYM_DRIVES_OUTWARD_ACTIONS", "bogus_flag"],
        "persona": {"agent_id": "digger", "objectives": ["x"]},
        "llm": {"provider": "openai", "base_url": "http://x/v1", "model": "m"},
    })
    body = r.json()
    assert body["ok"] is True
    assert {"NMEM_SYM_DRIVES_ENABLED", "NMEM_SYM_DRIVES_HONEST_DISCHARGE"} <= set(body["auto_enabled"])
    assert body["unknown_flags"] == ["bogus_flag"]   # unknown flags surfaced, not written
    env = (tmp_path / "digger" / "capabilities.env").read_text()
    assert "bogus_flag" not in env


def test_create_writes_secret_free_agent_and_returns_key_names(tmp_path):
    stored = {}
    c = _client(config_dir=str(tmp_path), store_secrets=stored.update)
    r = c.post("/studio/create", json={
        "agent_id": "scout",
        "enabled": ["NMEM_SYM_RECALL_DRIVE_ENABLED"],
        "persona": {"agent_id": "scout", "objectives": ["Learn."], "world_entities": "the domain"},
        "llm": {"provider": "openai", "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o", "api_key": "sk-SECRET", "api_key_env": "OPENAI_API_KEY"},
    })
    body = r.json()
    assert body["ok"] is True and body["agent_id"] == "scout"
    # secrets handed to the host by NAME only; value never in the response
    assert body["secret_keys"] == ["OPENAI_API_KEY"]
    assert "sk-SECRET" not in r.text
    assert stored == {"OPENAI_API_KEY": "sk-SECRET"}
    # files written, and the key is NOT in the on-disk agent.yaml
    agent_yaml = (tmp_path / "scout" / "agent.yaml").read_text()
    assert "sk-SECRET" not in agent_yaml
    env = (tmp_path / "scout" / "capabilities.env").read_text()
    # recall drive pulled its whole dependency closure in
    assert "NMEM_SYM_DRIVES_ENABLED=true" in env
    assert "NMEM_AUTONOMY__ENABLED=true" in env
    assert "NMEM_SYM_RECALL_AGENT_ID=scout" in env


def test_create_requires_agent_id():
    r = _client().post("/studio/create", json={"enabled": []})
    assert r.json()["ok"] is False


def test_test_llm_bad_spec_is_reported_not_raised():
    # no model → a clean ok:false, and nothing that looks like a key echoed back
    r = _client().post("/studio/test-llm", json={"provider": "openai", "api_key": "sk-XYZ"})
    body = r.json()
    assert body["ok"] is False and "sk-XYZ" not in r.text


def test_test_llm_unknown_provider():
    r = _client().post("/studio/test-llm", json={"provider": "mystery", "model": "m"})
    assert r.json()["ok"] is False


def test_agent_dashboard_html_wires_ops():
    # the agent-mode dashboard is a face over /health + /admin/* — assert it's wired to them
    # (served by studio_server.build_agent_app; no DB needed to check the asset itself).
    from nmem.agent_core.studio import agent_dashboard_html
    html = agent_dashboard_html()
    assert "<!DOCTYPE html>" in html and "nmem-studio" in html
    assert "/health" in html
    for path in ("/admin/consolidate", "/admin/nightly", "/admin/dreamstate",
                 "/admin/probe_recipes", "/admin/seed_recall"):
        assert path in html
    assert "setInterval(refresh" in html      # live auto-refresh of health
    assert "/chat" in html and "sendChat" in html   # the grounded chat panel (Step 5)
    assert "vizlink" in html                   # the nmem-viz "brain" link (Step 5b)


def test_init_viz_is_noop_without_config(monkeypatch):
    # the viz bridge is additive: with no NMEM_VIZ_INGEST_URL it never attaches (returns None)
    # before touching the runtime, so it's safe to call unconditionally at boot.
    from nmem.agent_core.viz import init_viz
    monkeypatch.delenv("NMEM_VIZ_INGEST_URL", raising=False)
    assert init_viz(object()) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
