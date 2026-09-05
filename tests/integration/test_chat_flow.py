"""Multi-turn conversation: chat state as ground truth across turns."""

import pytest

from advisor.data.repository import OpsRepository, DEFAULT_DB_PATH
from advisor.domain.state import OpsState
from advisor.orchestrator.chat_state import ConversationState
from advisor.orchestrator.runner import orchestrate


@pytest.fixture
def repo():
    return OpsRepository()


@pytest.fixture
def base_state():
    return OpsState(db_path=DEFAULT_DB_PATH)


def _events(query, state, repo, chat):
    return list(orchestrate(query, state, repo, chat_state=chat))


def _get(events, kind):
    return next((p for k, p in events if k == kind), None)


@pytest.fixture
def chat():
    return ConversationState()


def test_disruption_opens_context_and_recommends_next_step(base_state, repo, chat):
    events = _events("Captain C-1042 is sick tomorrow", base_state, repo, chat)

    # The recommendation is its own event, so the UI can place it after the option
    # cards rather than mid-response.
    assert _get(events, "recommendation")
    assert "recommendation" not in (_get(events, "prose") or "").lower()

    ctx = chat.open_disruption_context()
    assert ctx is not None
    assert ctx.crew_id == "C-1042"
    assert ctx.uncrewed_flight_ids


def test_flight_follow_up_scopes_to_uncrewed_legs(base_state, repo, chat):
    _events("Captain C-1042 is sick tomorrow", base_state, repo, chat)
    ctx = chat.open_disruption_context()

    events = _events("which flights are affected?", base_state, repo, chat)
    prose = _get(events, "prose")

    assert "Uncrewed Flights" in prose
    for fid in ctx.uncrewed_flight_ids:
        assert fid in prose
    # And it offers the next step rather than dumping options unasked.
    assert "recovery options" in (_get(events, "recommendation") or "").lower()


def test_explicit_schedule_lookup_still_returns_the_full_schedule(base_state, repo, chat):
    _events("Captain C-1042 is sick tomorrow", base_state, repo, chat)

    events = _events("Which flights depart DEL on 2026-09-15?", base_state, repo, chat)
    prose = _get(events, "prose")

    assert "Flight Schedule" in prose
    assert "Uncrewed Flights" not in prose


def test_flight_question_without_a_disruption_is_unscoped(base_state, repo, chat):
    events = _events("which flights are affected?", base_state, repo, chat)
    assert "Uncrewed Flights" not in (_get(events, "prose") or "")


def test_clearing_chat_stops_the_scoping(base_state, repo, chat):
    _events("Captain C-1042 is sick tomorrow", base_state, repo, chat)
    assert chat.open_disruption_context() is not None

    chat.clear()
    assert chat.turn_count == 0

    events = _events("which flights are affected?", base_state, repo, chat)
    assert "Uncrewed Flights" not in (_get(events, "prose") or "")
    # The post-clear turn is itself recorded, starting a fresh conversation.
    assert chat.turn_count == 1


def test_committing_a_reassignment_closes_the_disruption(base_state, repo, chat):
    _events("Captain C-1042 is sick tomorrow", base_state, repo, chat)

    _events("Reassign C-3310 to pairing P-2291", base_state, repo, chat)

    assert chat.open_disruption_context() is None
    assert chat.active_disruption.resolved_by == "C-3310"


def test_orchestrate_without_chat_state_still_works(base_state, repo):
    """Chat state is optional — the eval harness and API callers may omit it."""
    events = list(orchestrate("Who is on reserve at BLR?", base_state, repo))
    assert _get(events, "prose")


def test_crew_id_in_query_is_never_asked_for_again(base_state, repo, chat):
    """'Captain C-1042 is out - what should I do?' used to ask for the crew ID."""
    events = _events("Captain C-1042 is out - what should I do?", base_state, repo, chat)
    prose = _get(events, "prose") or ""

    assert "Disruption Impact" in prose
    assert "C-1042" in prose
    assert "specify" not in prose.lower()
    assert _get(events, "options")


def test_advice_phrasing_with_a_crew_id_routes_to_recovery(base_state, repo, chat):
    events = _events("Captain C-1042 - what are my options?", base_state, repo, chat)
    assert _get(events, "options")
    assert "specify" not in (_get(events, "prose") or "").lower()


def test_rank_comes_from_the_roster_not_the_query(base_state, repo, chat):
    """C-1313 is a First Officer; the briefing used to call everyone Captain."""
    events = _events("C-1313 is unavailable", base_state, repo, chat)
    prose = _get(events, "prose") or ""
    assert "First Officer" in prose
    assert "Captain K. Chandra" not in prose


def test_no_cover_recommended_when_nothing_is_uncrewed(base_state, repo, chat):
    events = _events("C-2087 is unavailable", base_state, repo, chat)
    rec = _get(events, "recommendation") or ""
    assert "no cover is required" in rec
    assert "Best way to cover" not in rec
