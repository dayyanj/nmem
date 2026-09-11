"""agent_core.proposal_source — the drive->ActionProposal.source derivation, graduated out of
individual agents (agent-a used to hardcode ``drive:novelty`` for every drive_intent goal because
the originating drive wasn't threaded through PursuitGoal → every drive success wrongly discharged
novelty). Pure function; no DB.
"""
from nmem.agent_core.goal_store import proposal_source
from nmem_act import PursuitGoal


def _pg(source_type=None, source_ref=None):
    return PursuitGoal(id=1, objective="o", source_type=source_type, source_ref=source_ref)


def test_drive_intent_uses_its_real_drive():
    assert proposal_source(_pg("drive_intent", {"drive": "recall"})) == "drive:recall"
    assert proposal_source(_pg("drive_intent", {"drive": "uncertainty"})) == "drive:uncertainty"
    assert proposal_source(_pg("drive_intent", {"drive": "novelty"})) == "drive:novelty"


def test_drive_intent_without_recorded_drive_does_not_mis_credit():
    # no drive in source_ref → a 'goal:' source, so a success discharges NO drive (better than
    # crediting a fixed one — the old bug).
    assert proposal_source(_pg("drive_intent", {})) == "goal:drive_intent"
    assert proposal_source(_pg("drive_intent", None)) == "goal:drive_intent"


def test_non_drive_origins_use_goal_source():
    assert proposal_source(_pg("decomposition", {"drive": "novelty"})) == "goal:decomposition"
    assert proposal_source(_pg("external")) == "goal:external"
    assert proposal_source(_pg(None)) == "goal:objective"


def test_accepts_row_like_dict():
    assert proposal_source({"source_type": "drive_intent",
                            "source_ref": {"drive": "coherence"}}) == "drive:coherence"
    assert proposal_source({"source_type": "decomposition"}) == "goal:decomposition"


def test_pursuit_goal_carries_source_ref():
    g = PursuitGoal(id=7, objective="o", source_type="drive_intent",
                    source_ref={"drive": "recall", "target_key": "recall:q=x"})
    assert g.source_ref["drive"] == "recall"
    assert proposal_source(g) == "drive:recall"
