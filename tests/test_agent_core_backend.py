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
