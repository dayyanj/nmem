"""OpenAICompatibleBackend._apply_family — the per-family reasoning dialect.

Pure unit tests (no HTTP): the Qwen binary reasoning collapse (abstract reasoning_effort
level → enable_thinking on/off + token-budget floor) and the invariant that the default
path (no reasoning_effort/enable_thinking) is byte-identical to before the actuator.
"""
from nmem.agent_core.backend import OpenAICompatibleBackend, _QWEN_THINK_MIN_TOKENS


def _qwen():
    return OpenAICompatibleBackend(base_url="http://x/v1", model="Qwen3-x", family="qwen")


def _body(max_tokens=700):
    return {"model": "Qwen3-x", "messages": [], "max_tokens": max_tokens}


# ── the invariant: default path = no thinking, untouched budget ──

def test_qwen_default_no_extra_keeps_thinking_off():
    b = _body()
    _qwen()._apply_family(b, {})
    assert b["chat_template_kwargs"] == {"enable_thinking": False}
    assert b["max_tokens"] == 700                 # untouched
    assert "reasoning_effort" not in b            # not forwarded when unrequested


# ── binary collapse of the abstract level ──

def test_qwen_high_enables_thinking_and_floors_tokens():
    b = _body(max_tokens=300)
    _qwen()._apply_family(b, {"reasoning_effort": "high"})
    assert b["chat_template_kwargs"] == {"enable_thinking": True}
    assert b["max_tokens"] == _QWEN_THINK_MIN_TOKENS   # floored up from 300
    assert b["reasoning_effort"] == "high"             # forwarded when requested


def test_qwen_medium_enables_thinking():
    b = _body()
    _qwen()._apply_family(b, {"reasoning_effort": "medium"})
    assert b["chat_template_kwargs"]["enable_thinking"] is True


def test_qwen_low_keeps_thinking_off():
    b = _body()
    _qwen()._apply_family(b, {"reasoning_effort": "low"})
    assert b["chat_template_kwargs"]["enable_thinking"] is False
    assert b["max_tokens"] == 700                 # no floor when not thinking


def test_qwen_high_does_not_lower_a_large_budget():
    b = _body(max_tokens=4096)
    _qwen()._apply_family(b, {"reasoning_effort": "high"})
    assert b["max_tokens"] == 4096                # floor only raises, never lowers


def test_qwen_explicit_enable_thinking_wins_over_effort():
    b = _body()
    _qwen()._apply_family(b, {"reasoning_effort": "high", "enable_thinking": False})
    assert b["chat_template_kwargs"]["enable_thinking"] is False   # explicit wins


# ── gemma / generic: no dialect ──

def test_gemma_family_is_noop():
    b = _body()
    OpenAICompatibleBackend(base_url="http://x/v1", model="g", family="gemma")._apply_family(
        b, {"reasoning_effort": "high"})
    assert "chat_template_kwargs" not in b
    assert "reasoning_effort" not in b
    assert b["max_tokens"] == 700


# ── thinking-truncated-to-empty → bounded non-thinking retry (codex P2) ──

import pytest  # noqa: E402


@pytest.mark.asyncio
async def test_qwen_empty_thinking_truncation_retries_without_thinking(monkeypatch):
    b = _qwen()
    calls = []

    async def fake_post(body):
        calls.append(dict(body.get("chat_template_kwargs") or {}))
        if body.get("chat_template_kwargs", {}).get("enable_thinking"):
            # first call: thinking on, truncated to empty
            return {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
        # retry: thinking off, real answer
        return {"choices": [{"message": {"content": "the answer"}, "finish_reason": "stop"}]}

    monkeypatch.setattr(b, "_post", fake_post)
    reply = await b.chat([{"role": "user", "content": "hard"}], reasoning_effort="high")
    assert reply == "the answer"
    assert calls == [{"enable_thinking": True}, {"enable_thinking": False}]   # exactly one retry


@pytest.mark.asyncio
async def test_qwen_normal_reply_no_retry(monkeypatch):
    b = _qwen()
    n = {"count": 0}

    async def fake_post(body):
        n["count"] += 1
        return {"choices": [{"message": {"content": "fine"}, "finish_reason": "stop"}]}

    monkeypatch.setattr(b, "_post", fake_post)
    reply = await b.chat([{"role": "user", "content": "hi"}], reasoning_effort="high")
    assert reply == "fine" and n["count"] == 1        # no retry on a clean reply


# ── usage_sink: eval-only realized-cost accounting (codex P1-2/P1-3) ──

@pytest.mark.asyncio
async def test_usage_sink_records_both_attempts_on_truncation_retry(monkeypatch):
    b = _qwen()

    async def fake_post(body):
        if body.get("chat_template_kwargs", {}).get("enable_thinking"):
            return {"choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                    "usage": {"completion_tokens": 1024}}
        return {"choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}],
                "usage": {"completion_tokens": 30}}

    monkeypatch.setattr(b, "_post", fake_post)
    sink = []
    reply = await b.chat([{"role": "user", "content": "hard"}],
                         reasoning_effort="high", max_tokens=700, usage_sink=sink)
    assert reply == "answer"
    # BOTH backend calls recorded — so 'equal compute' can be MEASURED, not assumed.
    assert [c["attempt"] for c in sink] == [1, 2]
    assert sink[0]["enable_thinking"] is True and sink[0]["max_tokens"] == _QWEN_THINK_MIN_TOKENS
    assert sink[1]["enable_thinking"] is False
    assert sink[0]["usage"]["completion_tokens"] == 1024
    assert sink[1]["usage"]["completion_tokens"] == 30


@pytest.mark.asyncio
async def test_usage_sink_none_is_untouched(monkeypatch):
    b = _qwen()

    async def fake_post(body):
        return {"choices": [{"message": {"content": "fine"}, "finish_reason": "stop"}],
                "usage": {"completion_tokens": 10}}

    monkeypatch.setattr(b, "_post", fake_post)
    # production path: no usage_sink → no error, plain reply (byte-identical)
    reply = await b.chat([{"role": "user", "content": "hi"}], reasoning_effort="high")
    assert reply == "fine"
