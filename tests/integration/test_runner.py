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
