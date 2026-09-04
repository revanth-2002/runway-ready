"""Unit tests for the Abstention Gate."""

import pytest
from advisor.data.repository import OpsRepository
from advisor.llm.parser import QueryIntent
from advisor.orchestrator.abstain import should_abstain, AbstainReason


@pytest.fixture
def repo():
    return OpsRepository()


def test_abstain_unknown_crew(repo):
    intent = QueryIntent(
        intent="check_status",
        entities={"crew_ids": ["C-9999"]},
        time_scope={},
        confidence=0.9,
    )
    result = should_abstain(intent, repo)
    assert result is not None
    reason, message = result
    assert reason == AbstainReason.UNKNOWN_ENTITY
    assert "C-9999" in message


def test_abstain_out_of_scope(repo):
    intent = QueryIntent(
        intent="out_of_scope",
        entities={},
        time_scope={},
        confidence=0.99,
        unsupported_aspects=["hotel bookings for passengers"],
    )
    result = should_abstain(intent, repo)
    assert result is not None
    reason, message = result
    assert reason == AbstainReason.OUT_OF_SCOPE


def test_abstain_ambiguous_time(repo):
    intent = QueryIntent(
        intent="ambiguous_time",
        entities={},
        time_scope={"raw": "afternoon"},
        confidence=0.55,
    )
    result = should_abstain(intent, repo)
    assert result is not None
    reason, message = result
    assert reason == AbstainReason.AMBIGUOUS_TIME


def test_no_abstain_valid_query(repo):
    intent = QueryIntent(
        intent="simulate_sick",
        entities={"crew_ids": ["C-1042"]},
        time_scope={"raw": "2026-09-15"},
        confidence=0.95,
    )
    result = should_abstain(intent, repo)
    assert result is None
