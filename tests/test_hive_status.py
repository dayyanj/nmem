"""Unit tests for studio_server.hive_status_payload — the read-only dashboard Hive card feed."""
import base64

import pytest

pytest.importorskip("fastapi")  # studio_server imports fastapi lazily but the module needs it present

from nmem.agent_core.studio_server import _fingerprint, hive_status_payload  # noqa: E402
from nmem.agent_core.hive_doctor import DoctorReport  # noqa: E402


def _pub(seed: bytes) -> str:
    return base64.b64encode(seed.ljust(32, b"\0")).decode()


def _cfg(**over):
    cfg = {
        "exchange": {
            "enabled": True,
            "identity": {"agent_id": "michelle"},
            "keyring": {
                "michelle": {"sign_pub": _pub(b"m-sign"), "box_pub": _pub(b"m-box")},
                "djai": {"sign_pub": _pub(b"d-sign"), "box_pub": _pub(b"d-box")},
            },
        },
        "delegation": {
            "enabled": True,
            "task_types": [{"type": "ask", "capability_class": "READ_ONLY"}],
            "peer_channels": {"djai": "dm:djai:michelle"},
        },
    }
    cfg.update(over)
    return cfg


def test_members_fingerprints_and_is_me():
    p = hive_status_payload(_cfg(), None)
    assert p["enabled"] is True
    assert p["identity"] == "michelle"
    ids = [m["agent_id"] for m in p["members"]]
    assert ids == ["djai", "michelle"]  # sorted
    me = next(m for m in p["members"] if m["agent_id"] == "michelle")
    assert me["is_me"] is True
    assert len(me["sign_fp"]) == 16 and me["sign_fp"] != "invalid"
    assert next(m for m in p["members"] if m["agent_id"] == "djai")["is_me"] is False
    assert p["accepts"] == ["ask"]
    assert p["peer_channels"] == {"djai": "dm:djai:michelle"}
    assert p["delegation_enabled"] is True


def test_delegation_enabled_reflects_flag():
    off = hive_status_payload(_cfg(delegation={"enabled": False, "task_types": []}), None)
    assert off["delegation_enabled"] is False
    assert off["enabled"] is True   # exchange still on → hive still enabled


def test_checks_serialized_from_report():
    rep = DoctorReport()
    rep.add("broker", True, "info", "broker reachable")
    rep.add("channels", False, "warn", "non-canonical name")
    p = hive_status_payload(_cfg(), rep)
    assert p["ok"] is True  # only warn → still ok
    assert p["solo"] is False
    names = {c["name"] for c in p["checks"]}
    assert names == {"broker", "channels"}


def test_error_check_marks_not_ok():
    rep = DoctorReport()
    rep.add("broker", False, "error", "broker unreachable")
    p = hive_status_payload(_cfg(), rep)
    assert p["ok"] is False


def test_disabled_when_no_exchange_or_delegation():
    p = hive_status_payload({}, None)
    assert p["enabled"] is False
    assert p["members"] == []


def test_fail_open_on_garbage_config():
    # non-dict fields must not raise — degrade to empties
    p = hive_status_payload({"exchange": "nope", "delegation": 5, "hive": None}, None)
    assert p["enabled"] is False
    assert p["members"] == [] and p["accepts"] == []


def test_fingerprint_invalid():
    assert _fingerprint("!!!not-base64") == "invalid"
    assert _fingerprint("") == "invalid"
