"""build_research_runner — the graduated DIRECT (verifier-enforced) knowledge-seeking executor.

The load-bearing property (§33.3): the ``ReferenceRunner`` ENFORCES the research Action's intrinsic
``Verifier`` after the handler, so the actuator CANNOT self-certify — a substantial finding from a
completed sandbox session verifies; a too-short one does NOT, even though the handler returned
``success=True``. That is exactly why computer-use uses this direct path and not the selector.

Uses a fake ``SandboxClient`` + stub bridge/mem; the experiential+reflective sink is best-effort
(every write is wrapped fail-open), so minimal stubs suffice and no DB/LLM/boot is needed. The
sandbox poll ``asyncio.sleep`` is patched out so the test is instant."""
import asyncio

import pytest

pytest.importorskip("nmem_act")

from nmem.agent_core.actors.computer_use import build_research_runner


class _FakeClient:
    """Stands in for SandboxClient: enabled, one session, returns a fixed done result."""

    def __init__(self, result: str):
        self._result = result
        self.aborted = False

    def is_enabled(self):
        return True

    async def start_session(self, task, mode="read_only", max_steps=None):
        return "sid-1"

    async def get_session(self, sid):
        return {"status": "done", "result": self._result, "steps": [{"action": "read"}]}

    async def abort_session(self, sid):
        self.aborted = True
        return True


class _StubBridge:
    async def record_action_outcome(self, **kw):
        return None

    async def discharge_drive(self, *a, **k):
        return None


class _StubMem:
    class _Skills:
        async def record(self, what, **kw):
            return None

    class _Journal:
        async def add(self, **kw):
            return None

    skills = _Skills()
    journal = _Journal()


def _run_one(result: str, monkeypatch):
    async def _no_sleep(_):
        return None

    monkeypatch.setattr("nmem.agent_core.actors.computer_use.asyncio.sleep", _no_sleep)
    runner = build_research_runner(mem=_StubMem(), backend=object(), agent_id="a",
                                   bridge=_StubBridge(), client=_FakeClient(result),
                                   reflect_enabled=False)
    from nmem_act import ActionProposal, CapabilityClass
    p = ActionProposal(action_type="pursue_knowledge", id="pursue:1", rationale="find X",
                       capability_class=CapabilityClass.READ_ONLY,
                       params={"objective": "find X", "goal_id": 1})
    return asyncio.run(runner.execute(p))


def test_substantial_finding_verifies(monkeypatch):
    out = _run_one("Spwig supports 3D product configurators via model-viewer.", monkeypatch)
    assert out.observations["verified"] is True
    assert out.observations["verification_status"] == "verified"


def test_insubstantial_finding_is_rejected_the_handler_cannot_self_certify(monkeypatch):
    # The handler returns success=(status=="achieved")=True, but the runner-enforced Verifier sees
    # a finding of "[done] x" (8 chars < the research action's min_chars=16) and OVERRIDES it to
    # not-verified. This is the honest-failure floor the direct path exists for.
    out = _run_one("x", monkeypatch)
    assert out.observations["verified"] is False
    assert out.observations["verification_status"] == "failed"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
