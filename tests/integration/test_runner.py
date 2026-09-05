"""Integration tests for the in-process generator orchestrator."""

import pytest
from advisor.data.repository import OpsRepository, DEFAULT_DB_PATH
from advisor.domain.state import OpsState
from advisor.orchestrator.runner import orchestrate


@pytest.fixture
def repo():
    return OpsRepository()


@pytest.fixture
def base_state():
    return OpsState(db_path=DEFAULT_DB_PATH)


def test_orchestrate_lookup_reserves(base_state, repo):
    events = list(orchestrate("Who is on reserve at BLR tomorrow?", base_state, repo))
    event_types = [e[0] for e in events]
    assert "status" in event_types
    assert "evidence" in event_types
    assert "prose" in event_types
    assert "abstain" not in event_types

    prose_event = next(e for e in events if e[0] == "prose")
    assert "C-3310" in prose_event[1]


def test_orchestrate_sick_callout(base_state, repo):
    events = list(orchestrate("Captain A. Nair is sick tomorrow. Recommend replacement.", base_state, repo))
    event_types = [e[0] for e in events]
    assert "status" in event_types
    assert "evidence" in event_types
    assert "options" in event_types
    assert "prose" in event_types

    options_event = next(e for e in events if e[0] == "options")
    options = options_event[1]
    assert len(options) >= 2
    # Top option should be C-3310
    assert options[0].crew_id == "C-3310"
    assert options[0].ledger.legal is True


def test_orchestrate_abstention(base_state, repo):
    events = list(orchestrate("Is Captain C-9999 available to fly DX412?", base_state, repo))
    event_types = [e[0] for e in events]
    assert "abstain" in event_types

    abstain_event = next(e for e in events if e[0] == "abstain")
    assert abstain_event[1]["reason"] == "UNKNOWN_ENTITY"
    assert "C-9999" in abstain_event[1]["message"]


def test_orchestrate_mass_cancellation_financial_loss(base_state, repo):
    query = "If I cancel all the flights departing from blr what will be the total cost loss"
    events = list(orchestrate(query, base_state, repo))
    event_types = [e[0] for e in events]

    assert "evidence" in event_types
    assert "prose" in event_types
    assert "abstain" not in event_types

    evidence_event = next(e for e in events if e[0] == "evidence")
    data = evidence_event[1]
    assert data["station"] == "BLR"
    assert data["flight_count"] > 0
    assert data["passengers_affected"] > 0
    assert data["cost_breakdown"].total_inr > 0

    prose_event = next(e for e in events if e[0] == "prose")
    prose = prose_event[1]
    assert "Mass Flight Cancellation Simulation" in prose
    assert "BLR" in prose
    assert "Total Estimated Financial Loss" in prose
    # Must NOT hallucinate Captain A. Nair or single-crew pairing slot
    assert "Captain A. Nair is incapacitated" not in prose


def test_orchestrate_reassignment_and_collision_prevention(base_state, repo):
    # 1. Commit reassignment of C-3310 to pairing P-2291
    from advisor.orchestrator.tools import tool_commit_crew_reassignment, tool_lookup_reserves
    from advisor.reasoning.candidates import enumerate_candidates
    from advisor.domain.evidence import ImpactReport

    updated_state = tool_commit_crew_reassignment(
        state=base_state,
        pairing_id="P-2291",
        disrupted_crew_id="C-1042",
        replacement_crew_id="C-3310",
    )
    assert len(updated_state.overlays) == 1

    # 2. Check reserves - C-3310 should now show CALLED
    res_data = tool_lookup_reserves(repo, updated_state, station="BLR")
    c3310_item = next(item for item in res_data["reserve_details"] if item["crew_id"] == "C-3310")
    assert "CALLED" in c3310_item["standby_status"]

    # 3. Simulate subsequent disruption - C-3310 must NOT be suggested again (collision prevented)
    mock_impact = ImpactReport(
        disruption_id="disp-002",
        disrupted_crew_id="C-5837",
        broken_pairing_id="P-9999",
        uncrewed_flights=(),
        delayed_rotations=(),
        stranded_companions=(),
        passengers_affected=150,
        source_rows=[],
    )
    rates = repo.get_cost_rates()
    cands = enumerate_candidates(mock_impact, updated_state, repo, rates)
    cand_ids = [c.crew_id for c in cands]
    assert "C-3310" not in cand_ids


def test_orchestrate_what_if_crew_move_duty_breach(base_state, repo):
    query = "If I move FO C-2087 onto DX412, does anyone breach a duty limit?"
    events = list(orchestrate(query, base_state, repo))
    event_types = [e[0] for e in events]
    assert "status" in event_types
    assert "evidence" in event_types
    assert "prose" in event_types
    assert "abstain" not in event_types

    evidence_event = next(e for e in events if e[0] == "evidence")
    evidence = evidence_event[1]
    assert evidence["legal"] is False
    assert any(b.rule_id == "RULE-DUTY-02" for b in evidence["breaches"])
    assert evidence["crew"].crew_id == "C-2087"
    assert "Captain" in evidence["crew"].rank
    assert evidence["displaced_crew"]["crew_id"] == "C-1042"

    prose_event = next(e for e in events if e[0] == "prose")
    prose = prose_event[1]
    assert "Duty Limits Breached" in prose
    assert "RULE-DUTY-02" in prose
    assert "Captain R. Iyer" in prose
    assert "C-1042" in prose


def test_orchestrate_what_if_legal_crew_move(base_state, repo):
    query = "If I move Captain C-3310 onto DX412, does anyone breach a duty limit?"
    events = list(orchestrate(query, base_state, repo))
    event_types = [e[0] for e in events]
    assert "evidence" in event_types
    assert "prose" in event_types

    evidence_event = next(e for e in events if e[0] == "evidence")
    assert evidence_event[1]["legal"] is True

    prose_event = next(e for e in events if e[0] == "prose")
    assert "Fully Legal & Compliant" in prose_event[1]
    assert "C-3310" in prose_event[1]


def test_orchestrate_crew_profile_lookup(base_state, repo):
    query = "Why is Captain C-2087 not in the standby roster?"
    events = list(orchestrate(query, base_state, repo))
    event_types = [e[0] for e in events]
    assert "evidence" in event_types
    assert "prose" in event_types

    evidence_event = next(e for e in events if e[0] == "evidence")
    assert evidence_event[1]["crew"].crew_id == "C-2087"
    assert evidence_event[1]["is_reserve"] is False

    prose_event = next(e for e in events if e[0] == "prose")
    assert "Captain R. Iyer" in prose_event[1]
    assert "Off-Duty" in prose_event[1] or "Not on standby" in prose_event[1]


