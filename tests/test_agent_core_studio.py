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


def test_wizard_spa_authors_hive_membership(tmp_path):
    # Step 05 (shared_world first-class): the served wizard offers hive membership — mode + graph role
    # — and posts it as body.hive to /studio/create. (DSN is a deploy concern, not a wizard field.)
    c = TestClient(create_studio_app(config_dir=str(tmp_path)))
    body = c.get("/").text
    assert 'id="hive_mode"' in body and 'value="shared_world"' in body
    assert 'id="hive_role"' in body and 'value="keeper"' in body and 'value="contributor"' in body
    assert "function collectHive" in body and "body.hive=hive" in body   # collected + posted
    assert "onHiveMode" in body                                          # role field show/hide wired


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


def test_create_authors_shared_world_hive_member(tmp_path):
    # shared_world is a FIRST-CLASS create option: a valid member writes a self-contained hive block
    # (mode + role + its own owner agent_id) into agent.yaml; a bad mode/role is a clean ok:false.
    import yaml
    c = _client(config_dir=str(tmp_path))
    ok = c.post("/studio/create", json={
        "agent_id": "scout", "enabled": [],
        "llm": {"provider": "openai", "base_url": "http://x/v1", "model": "m"},
        "hive": {"mode": "shared_world", "graph_role": "keeper"},
    }).json()
    assert ok["ok"] is True
    doc = yaml.safe_load((tmp_path / "scout" / "agent.yaml").read_text())
    assert doc["hive"]["mode"] == "shared_world"
    assert doc["hive"]["graph_role"] == "keeper"
    assert doc["hive"]["agent_id"] == "scout"        # owner identity stamped in → self-contained

    bad_mode = c.post("/studio/create", json={
        "agent_id": "s2", "llm": {"provider": "openai", "base_url": "http://x/v1", "model": "m"},
        "hive": {"mode": "swarm"}}).json()
    assert bad_mode["ok"] is False and "hive.mode" in bad_mode["error"]

    bad_role = c.post("/studio/create", json={
        "agent_id": "s3", "llm": {"provider": "openai", "base_url": "http://x/v1", "model": "m"},
        "hive": {"mode": "shared_world", "graph_role": "overlord"}}).json()
    assert bad_role["ok"] is False and "graph_role" in bad_role["error"]

    # codex: a truthy NON-object hive must be a clean ok:false, not an AttributeError → HTTP 500
    non_obj = c.post("/studio/create", json={
        "agent_id": "s4", "llm": {"provider": "openai", "base_url": "http://x/v1", "model": "m"},
        "hive": "shared_world"})
    assert non_obj.status_code == 200 and non_obj.json()["ok"] is False


def test_create_reports_failure_when_start_agent_raises(tmp_path):
    # codex re-review: if start_agent (provisioning) fails + tears down the config, create must
    # report ok:false — not ok:true/started:false while announcing files that were just deleted.
    async def boom(spec, agent_dir):
        import shutil
        shutil.rmtree(agent_dir, ignore_errors=True)      # mimic the appliance's cleanup-on-failure
        raise RuntimeError("DB unreachable")

    c = _client(config_dir=str(tmp_path), start_agent=boom)
    r = c.post("/studio/create", json={"agent_id": "scout", "enabled": [],
                                       "persona": {"agent_id": "scout", "objectives": ["x"]},
                                       "llm": {"provider": "openai", "base_url": "http://x/v1", "model": "m"}})
    body = r.json()
    assert body["ok"] is False and "DB unreachable" in body["error"]
    assert not (tmp_path / "scout").exists()              # torn down, not left half-created


def test_create_aborts_and_rolls_back_when_store_secrets_fails(tmp_path):
    # codex round-3: a secret-store failure must also abort + roll back (committing to agent mode
    # without the credentials would boot a keyless, broken agent) — the sibling of start_agent.
    def bad_store(secrets):
        raise RuntimeError("vault down")

    c = _client(config_dir=str(tmp_path), store_secrets=bad_store)
    r = c.post("/studio/create", json={
        "agent_id": "scout", "enabled": [], "persona": {"agent_id": "scout", "objectives": ["x"]},
        "llm": {"provider": "openai", "base_url": "http://x/v1", "model": "m",
                "api_key": "sk-x", "api_key_env": "OPENAI_API_KEY"}})
    body = r.json()
    assert body["ok"] is False and "vault down" in body["error"]
    assert not (tmp_path / "scout").exists()              # rolled back, not left keyless


def test_create_rejects_existing_agent_and_never_deletes_it(tmp_path):
    # codex round-4: a create over an EXISTING agent must be rejected (not overwritten), and the
    # failure-rollback must NEVER delete a pre-existing agent's files.
    agent = tmp_path / "scout"
    agent.mkdir()
    (agent / "secrets.env").write_text("PRECIOUS=keep-me\n")

    def bad_store(secrets):
        raise RuntimeError("vault down")

    c = _client(config_dir=str(tmp_path), store_secrets=bad_store)
    r = c.post("/studio/create", json={"agent_id": "scout", "enabled": [],
                                       "llm": {"provider": "openai", "base_url": "http://x/v1", "model": "m"}}).json()
    assert r["ok"] is False and "already exists" in r["error"]
    assert (agent / "secrets.env").read_text() == "PRECIOUS=keep-me\n"   # untouched, not wiped


def test_create_rejects_brain_without_model_or_base_url(tmp_path):
    # codex round-3: validate the brain before committing agent mode.
    c = _client(config_dir=str(tmp_path))
    no_model = c.post("/studio/create", json={"agent_id": "scout", "enabled": [],
                                              "llm": {"provider": "openai", "base_url": "http://x/v1"}}).json()
    no_url = c.post("/studio/create", json={"agent_id": "scout", "enabled": [],
                                            "llm": {"provider": "openai", "model": "m"}}).json()
    assert no_model["ok"] is False and "model" in no_model["error"]
    assert no_url["ok"] is False and "base_url" in no_url["error"]


def test_create_rejects_path_traversal_agent_ids(tmp_path):
    # codex P1: agent_id becomes a filesystem path — ../, absolute, slashes, dots must be refused
    # BEFORE any write, so a request can't escape config_dir.
    c = _client(config_dir=str(tmp_path))
    # "my-agent" (hyphen) + "1scout" (leading digit) both yield INVALID shell var names in the
    # derived <AGENT>_DB_DSN_ASYNC, which _merge_env_file would skip → missing DSN → boot loop.
    for bad in ["../evil", "/etc/passwd", "a/b", "..", ".", "has space", "x;rm", "..\\win",
                "my-agent", "1scout"]:
        r = c.post("/studio/create", json={"agent_id": bad, "enabled": [],
                                           "llm": {"provider": "openai", "model": "m"}})
        assert r.json()["ok"] is False, f"{bad!r} should be rejected"
    # nothing was written outside a valid slug dir
    assert not any(p.name in ("etc", "..") for p in tmp_path.iterdir())


def test_secrets_env_is_shell_safe(tmp_path, monkeypatch):
    # codex P1: secrets.env is SOURCED by entrypoint.sh — a value with shell metacharacters must
    # be quoted so it can't execute, and must round-trip back to the original string.
    monkeypatch.setattr("nmem.agent_core.studio_server.DATA_DIR", str(tmp_path))
    from nmem.agent_core import studio_server as S
    agent = tmp_path / "scout"
    agent.mkdir()
    (agent / "capabilities.env").write_text("")          # makes find_agent_dir() see it
    S.store_secrets({"OPENAI_API_KEY": "$(touch /tmp/pwned)", "OTHER": "has 'quotes' and spaces"})
    text = (agent / "secrets.env").read_text()
    assert "$(touch" not in text.replace("'$(touch", "")   # the raw substitution isn't left unquoted
    # round-trips: a second merge reads the quoted values back intact
    import shlex
    env = {k: shlex.split(v)[0] for k, v in
           (ln.split("=", 1) for ln in text.splitlines() if "=" in ln and not ln.startswith("#"))}
    assert env["OPENAI_API_KEY"] == "$(touch /tmp/pwned)"
    assert env["OTHER"] == "has 'quotes' and spaces"


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
    assert "/tools" in html and "/act" in html and "sendAct" in html   # the Act panel (Step 6)


def test_init_viz_is_noop_without_config(monkeypatch):
    # the viz bridge is additive: with no NMEM_VIZ_INGEST_URL it never attaches (returns None)
    # before touching the runtime, so it's safe to call unconditionally at boot.
    from nmem.agent_core.viz import init_viz
    monkeypatch.delenv("NMEM_VIZ_INGEST_URL", raising=False)
    assert init_viz(object()) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
