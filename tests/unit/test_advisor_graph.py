"""Tests for the LangGraph pipeline and the richer conversation state."""

import pytest

from advisor.llm.parser import QueryIntent
from advisor.orchestrator.chat_state import ConversationState
from advisor.orchestrator.graph import (
    AdvisorState,
    build_advisor_graph,
    node_advise,
    node_record,
    _turn_entities,
    _turn_summary,
)


def _intent(name="lookup_reserves", **entities):
    return QueryIntent(intent=name, entities=entities, time_scope={}, confidence=0.95)


# --------------------------------------------------------------------------
# Graph structure
# --------------------------------------------------------------------------

def test_graph_declares_the_expected_pipeline():
    graph = build_advisor_graph()
    nodes = set(graph.get_graph().nodes)
    for expected in ("resolve", "parse", "dispatch", "advise", "record"):
        assert expected in nodes


def test_graph_edges_run_record_after_dispatch():
    """Recording must sit downstream of dispatch so no route can skip it."""
    graph = build_advisor_graph()
    edges = {(e.source, e.target) for e in graph.get_graph().edges}
    assert ("dispatch", "advise") in edges
    assert ("advise", "record") in edges


# --------------------------------------------------------------------------
# record node
# --------------------------------------------------------------------------

def test_record_node_writes_a_turn():
    cs = ConversationState()
    state: AdvisorState = {
        "clean_query": "who is on reserve at BLR",
        "intent": _intent("lookup_reserves", base="BLR"),
        "chat_state": cs,
        "outcome": "answered",
        "prose": "### Active Standby Reserves: BLR",
        "recommendation": "Check duty hours next?",
        "suggestions": [{"label": "x", "query": "y"}],
        "resolved_crew_ids": [],
    }
    node_record(state)

    assert cs.turn_count == 1
    turn = cs.last_turn()
    assert turn.intent == "lookup_reserves"
    assert turn.objective == "Assess standby strength"
    assert turn.recommendation == "Check duty hours next?"
    assert turn.suggestions == [{"label": "x", "query": "y"}]
    assert cs.last_entity("station") == "BLR"


def test_record_node_captures_a_clarifying_question():
    cs = ConversationState()
    node_record({
        "clean_query": "who can fly later",
        "intent": _intent("ambiguous_time"),
        "chat_state": cs,
        "outcome": "clarification_requested",
        "question_asked": "Which UTC window?",
        "resolved_crew_ids": [],
    })

    pending = cs.pending_question()
    assert pending is not None
    assert pending["question"] == "Which UTC window?"
    assert cs.last_turn().outcome == "clarification_requested"


def test_answering_closes_the_pending_question():
    cs = ConversationState()
    cs.record_turn("q", "ambiguous_time", question_asked="Which UTC window?",
                   outcome="clarification_requested")
    assert cs.pending_question() is not None

    cs.record_turn("12:00-14:00", "lookup_flights", outcome="answered")
    assert cs.pending_question() is None


def test_record_node_is_a_noop_without_chat_state():
    assert node_record({"intent": _intent(), "chat_state": None}) == {}


# --------------------------------------------------------------------------
# Turn summarisation
# --------------------------------------------------------------------------

def test_entities_prefer_evidence_over_the_typed_query():
    class _Flight:
        flight_id = "DX702"
        origin = "DEL"

    class _Impact:
        disrupted_crew_id = "C-1042"
        broken_pairing_id = "P-2291"
        uncrewed_flights = (_Flight(),)

    ents = _turn_entities({
        "intent": _intent("simulate_sick", base="BLR", crew_ids=["C-9999"]),
        "impact": _Impact(),
        "resolved_crew_ids": [],
    })
    assert ents["crew_id"] == "C-1042"
    assert ents["pairing_id"] == "P-2291"
    assert ents["flight_ids"] == ["DX702"]
    assert ents["station"] == "DEL"


def test_summary_reports_abstention():
    out = _turn_summary({
        "intent": _intent("out_of_scope"),
        "outcome": "abstained",
        "clean_query": "book me a hotel",
    })
    assert "Abstained" in out


def test_summary_falls_back_to_the_prose_headline():
    out = _turn_summary({
        "intent": _intent("lookup_reserves"),
        "outcome": "answered",
        "prose": "### 👥 Active Standby Reserves: BLR\n\n| a | b |",
    })
    assert out.startswith("👥 Active Standby Reserves")


# --------------------------------------------------------------------------
# Derived state: objective, priorities, recommendations
# --------------------------------------------------------------------------

def test_unresolved_disruption_is_the_top_priority():
    cs = ConversationState()
    cs.record_turn("q", "lookup_reserves", entities={"station": "BLR"})
    cs.open_disruption("C-1042", "P-2291", ["DX412"], station="BLR")

    prios = cs.priorities()
    assert prios[0]["kind"] == "unresolved_disruption"
    assert cs.objective() == "Restore legal cover for the disrupted pairing"


def test_pending_question_outranks_a_plain_lookup():
    cs = ConversationState()
    cs.record_turn("q", "ambiguous_time", question_asked="Which UTC window?",
                   outcome="clarification_requested")
    assert cs.priorities()[0]["kind"] == "awaiting_controller_answer"
    assert cs.objective() == "Awaiting controller decision"


def test_priorities_fall_back_to_the_last_enquiry():
    cs = ConversationState()
    cs.record_turn("q", "lookup_reserves", entities={"station": "BLR"})
    assert cs.priorities()[0]["kind"] == "last_enquiry"


def test_recommendations_accumulate_most_recent_first():
    cs = ConversationState()
    cs.record_turn("q1", "simulate_sick", recommendation="Assign C-3310")
    cs.record_turn("q2", "lookup_flights", recommendation="Produce recovery options")

    recs = cs.recommendations()
    assert [r["recommendation"] for r in recs] == [
        "Produce recovery options",
        "Assign C-3310",
    ]


def test_entities_carry_forward_across_turns():
    cs = ConversationState()
    cs.record_turn("q1", "lookup_reserves", entities={"station": "MAA"})
    cs.record_turn("q2", "lookup_crew_info", entities={"crew_id": "C-2087"})

    # Station survives a turn that never mentioned it.
    assert cs.last_entity("station") == "MAA"
    assert cs.last_entity("crew_id") == "C-2087"


def test_brief_summarises_the_conversation_for_the_llm():
    cs = ConversationState()
    cs.record_turn("q", "simulate_sick", summary="Simulated C-1042 disruption")
    cs.open_disruption("C-1042", "P-2291", ["DX412"], station="BLR")

    brief = cs.brief()
    assert "Objective:" in brief
    assert "Open disruption: C-1042 on pairing P-2291" in brief
    assert "Simulated C-1042 disruption" in brief


def test_brief_is_empty_for_a_fresh_conversation():
    assert "No prior turns" in ConversationState().brief()


def test_clear_resets_every_derived_field():
    cs = ConversationState()
    cs.record_turn("q", "simulate_sick", entities={"station": "BLR"},
                   recommendation="Assign C-3310", question_asked="Confirm?")
    cs.open_disruption("C-1042", "P-2291", ["DX412"])

    cs.clear()

    assert cs.turns == []
    assert cs.entities == {}
    assert cs.open_questions == []
    assert cs.recommendations() == []
    assert cs.priorities() == []
    assert cs.objective() is None
    assert cs.active_disruption is None
    assert "No prior turns" in cs.brief()
