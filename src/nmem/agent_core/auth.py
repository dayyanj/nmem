"""Session-cookie auth for the studio appliance's admin surface (the wizard, /studio/*, /admin/*,
/act, /chat, /tools). The whole surface can create + run agents and register tools that execute in
the container, so exposing it beyond localhost demands real auth.

Design (deliberately minimal + correct for a single-admin appliance):
  * **Opt-in.** Enabled only when a password is configured (``STUDIO_AUTH_PASSWORD`` or
    ``STUDIO_AUTH_PASSWORD_HASH``). With none set, the appliance runs UNAUTHENTICATED (the previous
    behaviour, loopback-only) and logs a loud warning — so local ``docker compose up`` still just works.
  * **Login → server-side session.** ``POST /auth/login`` (user+password, both compared in
    constant time) mints a random session id held in-process (a restart logs everyone out — fine for
    an appliance) with a TTL, and sets it as an **HttpOnly, SameSite=Strict** cookie (so client JS/XSS
    can't read it and a cross-site request won't carry it).
  * **CSRF double-submit.** The session also carries a CSRF token, returned to the SPA at login; every
    **mutating** request (POST/PUT/PATCH/DELETE) must echo it in ``X-CSRF-Token`` — defence-in-depth on
    top of SameSite. Safe methods (GET/HEAD) don't need it.
  * **Default-deny.** The middleware gates EVERYTHING except a small open allowlist (the SPA shell at
    ``/``, ``/health`` for compose checks, and ``/auth/*``), so a newly-added route is protected by
    default rather than accidentally exposed.

Password at rest: ``STUDIO_AUTH_PASSWORD`` is a plaintext env secret (kept in secrets.env at 0600, the
same model as the DB/LLM secrets). For a hash instead, set ``STUDIO_AUTH_PASSWORD_HASH`` to a
``pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>`` string (see ``hash_password``).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time

# FastAPI is imported at module level here (not lazily) because the route handlers' `Request`/
# `Response` annotations must resolve in THIS module's globals for FastAPI's injection to work — a
# local import leaves them out of __globals__ and FastAPI would treat them as query params. auth.py
# is a web-only module (only the studio FastAPI apps import it), so the dependency is appropriate.
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

log = logging.getLogger("nmem.studio.auth")

# Open (no session required). The SPA shell must load so it can render its own login screen; /health
# is the compose healthcheck; /auth/* is how you log in. Everything else is default-denied.
_OPEN_PATHS = frozenset({"/", "/health", "/favicon.ico",
                         "/auth/login", "/auth/logout", "/auth/status"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def hash_password(password: str, *, iterations: int = 200_000) -> str:
    """Produce a ``pbkdf2_sha256$iterations$salt$hash`` string for STUDIO_AUTH_PASSWORD_HASH."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def _verify_hash(password: str, encoded: str) -> bool:
    try:
        scheme, iters, salt_hex, hash_hex = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:  # noqa: BLE001 — a malformed hash env is never a valid credential
        return False


class _Session:
    __slots__ = ("csrf", "expiry")

    def __init__(self, csrf: str, expiry: float):
        self.csrf = csrf
        self.expiry = expiry


class SessionAuth:
    """In-process session store + credential check, configured from env. One instance per app."""

    def __init__(self, env: dict | None = None):
        e = env if env is not None else os.environ
        self.user = e.get("STUDIO_AUTH_USER", "admin")
        self._pw = e.get("STUDIO_AUTH_PASSWORD", "")
        self._pw_hash = e.get("STUDIO_AUTH_PASSWORD_HASH", "")
        self.cookie = e.get("STUDIO_AUTH_COOKIE", "nmem_studio_session")
        self.secure = str(e.get("STUDIO_COOKIE_SECURE", "")).lower() in ("1", "true", "yes")
        try:
            self.ttl = max(60, int(e.get("STUDIO_AUTH_TTL", "43200")))   # default 12h, floor 1m
        except ValueError:
            self.ttl = 43200
        self._sessions: dict[str, _Session] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._pw or self._pw_hash)

    def _password_ok(self, pw: str) -> bool:
        if self._pw_hash:
            return _verify_hash(pw, self._pw_hash)
        # constant-time compare on UTF-8 bytes (compare_digest raises TypeError on non-ASCII str, so a
        # unicode password would otherwise 500 instead of authenticating). Empty pw never authenticates.
        return bool(self._pw) and hmac.compare_digest(pw.encode("utf-8"), self._pw.encode("utf-8"))

    def verify(self, user: str, pw: str) -> bool:
        # check BOTH sides in constant time so a wrong username isn't faster to reject than a wrong
        # password (no user-enumeration timing signal). `&` not `and` — evaluate both unconditionally.
        u_ok = hmac.compare_digest((user or "").encode("utf-8"), self.user.encode("utf-8"))
        p_ok = self._password_ok(pw or "")
        return bool(u_ok & p_ok)

    def login(self, user: str, pw: str) -> tuple[str, str] | None:
        """Verify credentials → (session_id, csrf_token), or None on failure."""
        if not self.verify(user, pw):
            return None
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self._sessions[sid] = _Session(csrf, time.time() + self.ttl)
        return sid, csrf

    def session(self, sid: str | None) -> _Session | None:
        """The live session for ``sid`` (None if absent or expired; expired ones are reaped)."""
        if not sid:
            return None
        s = self._sessions.get(sid)
        if s is None:
            return None
        if s.expiry < time.time():
            self._sessions.pop(sid, None)
            return None
        return s

    def logout(self, sid: str | None) -> None:
        if sid:
            self._sessions.pop(sid, None)


def make_auth_router(auth: SessionAuth):
    """The /auth/login · /auth/logout · /auth/status routes (always mounted; no-ops sensibly when
    auth is disabled so the SPA can ask /auth/status and skip its login screen)."""
    r = APIRouter()

    @r.post("/auth/login")
    async def login(request: Request, response: Response):
        if not auth.enabled:
            return JSONResponse({"ok": False, "error": "auth is not enabled"}, status_code=400)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        out = auth.login(str(body.get("user", "")), str(body.get("password", "")))
        if out is None:
            return JSONResponse({"ok": False, "error": "invalid credentials"}, status_code=401)
        sid, csrf = out
        response.set_cookie(auth.cookie, sid, max_age=auth.ttl, httponly=True,
                            samesite="strict", secure=auth.secure, path="/")
        return {"ok": True, "csrf": csrf}      # SPA keeps csrf in memory, echoes it on mutations

    @r.post("/auth/logout")
    async def logout(request: Request, response: Response):
        auth.logout(request.cookies.get(auth.cookie))
        response.delete_cookie(auth.cookie, path="/")
        return {"ok": True}

    @r.get("/auth/status")
    async def status(request: Request):
        if not auth.enabled:
            return {"enabled": False, "authenticated": True}   # open appliance → treat as authed
        return {"enabled": True, "authenticated": auth.session(request.cookies.get(auth.cookie)) is not None}

    return r


def install_session_auth(app, auth: SessionAuth) -> None:
    """Gate ``app`` behind ``auth`` (default-deny except the open allowlist; CSRF on mutations).
    A no-op with a loud warning when auth is disabled, so the appliance still runs unauthenticated
    on loopback for local use."""
    if not auth.enabled:
        log.warning("[studio] ADMIN SURFACE IS UNAUTHENTICATED — set STUDIO_AUTH_PASSWORD to require "
                    "login before exposing the studio beyond 127.0.0.1")
        return

    @app.middleware("http")
    async def _gate(request, call_next):
        path = request.url.path
        if path in _OPEN_PATHS:
            return await call_next(request)
        sess = auth.session(request.cookies.get(auth.cookie))
        if sess is None:
            return JSONResponse({"ok": False, "error": "authentication required"}, status_code=401)
        if request.method not in _SAFE_METHODS:
            token = request.headers.get("x-csrf-token", "")
            if not hmac.compare_digest(token.encode("utf-8"), sess.csrf.encode("utf-8")):
                return JSONResponse({"ok": False, "error": "invalid or missing CSRF token"},
                                    status_code=403)
        return await call_next(request)

    log.info("[studio] session auth ENABLED (user=%s, ttl=%ss) — admin surface requires login", auth.user, auth.ttl)
