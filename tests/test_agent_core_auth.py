"""Session-cookie auth for the studio admin surface. It gates a surface that can create + run agents
and register tools that execute in the container, so the contract matters: OFF by default (local use
unchanged); when a password is set, default-deny with a login → HttpOnly/SameSite cookie session and a
CSRF token required on mutations. Tested against create_studio_app (real wiring) + SessionAuth directly."""
import time

import pytest
from fastapi.testclient import TestClient

from nmem.agent_core.auth import SessionAuth, hash_password
from nmem.agent_core.studio import create_studio_app


def _app(tmp_path, monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return TestClient(create_studio_app(config_dir=str(tmp_path)))


def test_disabled_by_default_is_open(tmp_path, monkeypatch):
    # no password configured → no gate (the previous loopback-only behaviour), catalog is reachable
    monkeypatch.delenv("STUDIO_AUTH_PASSWORD", raising=False)
    c = _app(tmp_path, monkeypatch)
    assert c.get("/studio/catalog").status_code == 200
    st = c.get("/auth/status").json()
    assert st["enabled"] is False and st["authenticated"] is True     # open ⇒ treated as authed


def test_enabled_gate_blocks_then_login_grants(tmp_path, monkeypatch):
    c = _app(tmp_path, monkeypatch, STUDIO_AUTH_PASSWORD="s3cret", STUDIO_AUTH_USER="admin")
    # default-deny: a protected GET is 401 before login
    assert c.get("/studio/catalog").status_code == 401
    assert c.get("/auth/status").json() == {"enabled": True, "authenticated": False}
    # wrong credentials rejected
    assert c.post("/auth/login", json={"user": "admin", "password": "nope"}).status_code == 401
    # correct login → cookie set (HttpOnly, SameSite=Strict) + a csrf token returned
    r = c.post("/auth/login", json={"user": "admin", "password": "s3cret"})
    assert r.status_code == 200 and r.json()["ok"] is True
    setc = r.headers.get("set-cookie", "").lower()
    assert "httponly" in setc and "samesite=strict" in setc
    assert "nmem_studio_session=" in setc
    # now the session cookie (auto-carried by the client) unlocks protected GETs
    assert c.get("/studio/catalog").status_code == 200
    # logout clears the session → back to 401
    assert c.post("/auth/logout").status_code == 200
    assert c.get("/studio/catalog").status_code == 401


def test_open_paths_never_gated(tmp_path, monkeypatch):
    c = _app(tmp_path, monkeypatch, STUDIO_AUTH_PASSWORD="s3cret")
    assert c.get("/").status_code == 200            # the SPA shell must load to show its login screen
    # /health stays open for the compose healthcheck even with auth on
    # (health lives on the agent-mode app; here we assert the SPA shell + auth routes are open)
    assert c.get("/auth/status").status_code == 200


def test_mutation_requires_csrf_token(tmp_path, monkeypatch):
    c = _app(tmp_path, monkeypatch, STUDIO_AUTH_PASSWORD="s3cret")
    csrf = c.post("/auth/login", json={"user": "admin", "password": "s3cret"}).json()["csrf"]
    # a mutating request with a valid session but NO csrf header is refused (double-submit defence)
    assert c.post("/studio/create", json={"agent_id": "x"}).status_code == 403
    # with the csrf header it passes the GATE (a downstream ok:false for a thin spec is 200, not 403/401)
    ok = c.post("/studio/create", json={"agent_id": "x"}, headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 200
    # a WRONG csrf token is refused
    assert c.post("/studio/create", json={"agent_id": "x"},
                  headers={"X-CSRF-Token": "wrong"}).status_code == 403


def test_password_hash_path(tmp_path, monkeypatch):
    monkeypatch.delenv("STUDIO_AUTH_PASSWORD", raising=False)
    enc = hash_password("hunter2")
    c = _app(tmp_path, monkeypatch, STUDIO_AUTH_PASSWORD_HASH=enc)
    assert c.post("/auth/login", json={"user": "admin", "password": "wrong"}).status_code == 401
    assert c.post("/auth/login", json={"user": "admin", "password": "hunter2"}).status_code == 200


def test_sessionauth_unit_verify_and_expiry(monkeypatch):
    monkeypatch.setenv("STUDIO_AUTH_PASSWORD", "pw")
    monkeypatch.setenv("STUDIO_AUTH_USER", "admin")
    a = SessionAuth()
    assert a.enabled is True
    assert a.verify("admin", "pw") is True
    assert a.verify("admin", "bad") is False
    assert a.verify("root", "pw") is False           # wrong user also fails (constant-time)
    sid, csrf = a.login("admin", "pw")
    assert a.session(sid) is not None and a.session(sid).csrf == csrf
    a._sessions[sid].expiry = time.time() - 1         # force-expire
    assert a.session(sid) is None                     # expired sessions are rejected + reaped
    assert a.session("never-issued") is None


def test_hash_verify_roundtrip():
    from nmem.agent_core.auth import _verify_hash
    enc = hash_password("correct horse")
    assert _verify_hash("correct horse", enc) is True
    assert _verify_hash("wrong", enc) is False
    assert _verify_hash("x", "garbage$not$a$hash") is False    # malformed hash is never valid


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
