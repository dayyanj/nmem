"""AgentRuntime thin-host auto-defaults: the runtime fills in graduated defaults so a host passes as
little as possible. Here: the skill.chronic handler (default_skill_chronic) is auto-wired when the host
doesn't supply one, and a host-supplied handler still wins."""
import types

import pytest

pytest.importorskip("nmem_act")

from nmem.agent_core.runtime import AgentRuntime


class _RecordingMem:
    """Records .on(event) subscriptions without needing a real MemorySystem."""
    def __init__(self):
        self.subs = {}

    def on(self, event):
        def deco(fn):
            self.subs[event] = fn
            return fn
        return deco


def _rt(skill_chronic):
    rt = AgentRuntime.__new__(AgentRuntime)          # bypass __init__: _wire_skill_chronic reads only these
    rt._skill_chronic = skill_chronic
    rt._persona = types.SimpleNamespace(agent_id="scout")
    return rt


def test_skill_chronic_is_auto_defaulted_when_absent():
    rt, mem = _rt(None), _RecordingMem()
    rt._wire_skill_chronic(mem)
    assert "skill.chronic" in mem.subs and callable(mem.subs["skill.chronic"])   # default wired for free


def test_host_skill_chronic_overrides_the_default():
    async def _mine(data):
        return None
    rt, mem = _rt(_mine), _RecordingMem()
    rt._wire_skill_chronic(mem)
    assert mem.subs["skill.chronic"] is _mine        # host handler wins over the default


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
