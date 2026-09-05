"""Tests for conversational memory, recommendation placement, and disruption scoping."""

from advisor.orchestrator.chat_state import MAX_ACTIONS, ConversationState
from advisor.orchestrator.runner import _names_explicit_schedule


# --------------------------------------------------------------------------
# Recording and retrieval
# --------------------------------------------------------------------------

def test_records_actions_in_order_with_turn_numbers():
    cs = ConversationState()
    cs.record("lookup_reserves", "who is on reserve at BLR", "Listed 2 reserves", {"station": "BLR"})
    cs.record("lookup_flights", "flights from DEL", "Listed 8 flights", {"station": "DEL"})

    assert cs.turn_count == 2
    assert [a.turn for a in cs.actions] == [1, 2]
    assert cs.last_action().kind == "lookup_flights"
    assert cs.last_action("lookup_reserves").entities["station"] == "BLR"


def test_last_entity_walks_backwards_to_most_recent_value():
    cs = ConversationState()
    cs.record("lookup_reserves", "q", "s", {"station": "BLR"})
    cs.record("lookup_crew_info", "q", "s", {"crew_id": "C-1042"})

    assert cs.last_entity("station") == "BLR"
    assert cs.last_entity("crew_id") == "C-1042"
    assert cs.last_entity("pairing_id") is None


def test_history_is_bounded():
    cs = ConversationState()
    for i in range(MAX_ACTIONS + 20):
        cs.record("lookup_reserves", f"q{i}", "s")
    assert len(cs.actions) == MAX_ACTIONS
    # Turn numbering keeps counting even after the window slides.
    assert cs.actions[-1].turn == MAX_ACTIONS + 20


# --------------------------------------------------------------------------
# Active disruption lifecycle
# --------------------------------------------------------------------------

def test_open_disruption_becomes_the_active_context():
    cs = ConversationState()
    cs.open_disruption("C-1042", "P-2291", ["DX412", "DX413"], station="BLR")

    ctx = cs.open_disruption_context()
    assert ctx is not None
    assert ctx.pairing_id == "P-2291"
    assert ctx.uncrewed_flight_ids == ["DX412", "DX413"]
    assert ctx.station == "BLR"


def test_committed_reassignment_closes_the_disruption():
    cs = ConversationState()
    cs.open_disruption("C-1042", "P-2291", ["DX412"])
    cs.resolve_disruption("C-3310")

    # No longer the open context, but still inspectable for audit.
    assert cs.open_disruption_context() is None
    assert cs.active_disruption.resolved is True
    assert cs.active_disruption.resolved_by == "C-3310"


def test_resolve_without_an_open_disruption_is_a_noop():
    cs = ConversationState()
    cs.resolve_disruption("C-3310")
    assert cs.active_disruption is None


# --------------------------------------------------------------------------
# Clearing
# --------------------------------------------------------------------------

def test_clear_drops_actions_and_active_disruption():
    cs = ConversationState()
    cs.record("simulate_disruption", "q", "s", {"crew_id": "C-1042"})
    cs.open_disruption("C-1042", "P-2291", ["DX412"])

    cs.clear()

    assert cs.actions == []
    assert cs.active_disruption is None
    assert cs.open_disruption_context() is None
    assert cs.turn_count == 0
    assert cs.last_entity("crew_id") is None


def test_state_is_reusable_after_clear():
    cs = ConversationState()
    cs.record("lookup_reserves", "q", "s")
    cs.clear()
    cs.record("lookup_flights", "q2", "s2")

    assert cs.turn_count == 1
    assert cs.actions[0].turn == 1


def test_to_dict_is_serialisable():
    cs = ConversationState()
    cs.record("simulate_disruption", "q", "s", {"crew_id": "C-1042"})
    cs.open_disruption("C-1042", "P-2291", ["DX412"], station="BLR")

    d = cs.to_dict()
    assert d["turn_count"] == 1
    assert d["actions"][0]["kind"] == "simulate_disruption"
    assert d["active_disruption"]["uncrewed_flight_ids"] == ["DX412"]


# --------------------------------------------------------------------------
# Flight-question scoping
# --------------------------------------------------------------------------

def test_bare_follow_up_is_not_an_explicit_schedule_lookup():
    for q in ["which flights are affected?", "show me the flights", "what flights?"]:
        assert _names_explicit_schedule(q) is False


def test_named_station_route_or_date_is_an_explicit_lookup():
    for q in [
        "Which flights depart DEL?",
        "flights from BLR to BOM",
        "schedule on 2026-09-15",
        "what about DX412",
    ]:
        assert _names_explicit_schedule(q) is True, q


def test_scoping_reads_the_query_not_the_parsed_entities():
    """The parser back-fills origin=DEL even for a bare follow-up, so the text decides."""
    assert _names_explicit_schedule("which flights are affected?") is False
    assert _names_explicit_schedule("which flights are affected at DEL?") is True
