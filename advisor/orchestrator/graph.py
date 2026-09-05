"""LangGraph pipeline giving the advisor an explicit, inspectable data flow.

    resolve -> parse -> gate -> dispatch -> advise -> record

Each node reads and writes one typed `AdvisorState`, so cross-cutting concerns are
declared once instead of being hand-written into every tool branch. The previous
ad-hoc dispatcher recorded conversation state in only a handful of its ~20 `return`
paths; here `record` runs on every route out of `dispatch`, so it cannot be missed.

`dispatch` delegates to the existing tool chain in `runner._orchestrate_core`, which
keeps the covered legality/costing logic intact while the graph owns the flow.
"""

from typing import Any, Dict, List, Optional, Tuple

from typing_extensions import TypedDict

from langgraph.graph import END, START, StateGraph

from advisor.audit.logger import StructuredLogger, append_audit_event
from advisor.data.repository import OpsRepository
from advisor.domain.state import OpsState
from advisor.llm.client import LLMClient
from advisor.llm.parser import QueryIntent
from advisor.llm.suggest import derive_suggestions
from advisor.orchestrator.chat_state import ConversationState

logger = StructuredLogger("advisor.orchestrator.graph")


class AdvisorState(TypedDict, total=False):
    """State flowing between graph nodes."""

    # Inputs
    query: str
    ops_state: Any
    repo: Any
    client: Any
    request_id: Optional[str]
    chat_state: Any

    # resolve
    clean_query: str
    pii_map: Dict[str, str]
    resolved_crew_ids: List[str]

    # parse
    intent: Optional[QueryIntent]

    # dispatch
    events: List[Tuple[str, Any]]
    impact: Any
    options: List[Any]
    prose: Optional[str]
    recommendation: Optional[str]
    outcome: str
    question_asked: Optional[str]

    # advise
    suggestions: List[Dict[str, str]]


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def node_resolve(state: AdvisorState) -> Dict[str, Any]:
    """De-identify the directive and resolve local entities before anything leaves."""
    from advisor.orchestrator.resolver import resolve_local_pii

    clean_query, pii_map, crew_ids = resolve_local_pii(state["query"], state["repo"])
    logger.debug("Graph node: resolve", resolved_crew=len(crew_ids))
    return {
        "clean_query": clean_query,
        "pii_map": pii_map,
        "resolved_crew_ids": crew_ids,
    }


def node_parse(state: AdvisorState) -> Dict[str, Any]:
    """Classify intent and extract operational entities."""
    from advisor.llm.parser import parse_intent

    intent = parse_intent(state["clean_query"], state.get("client"))
    logger.debug("Graph node: parse", intent=intent.intent)
    return {"intent": intent}


def node_dispatch(state: AdvisorState) -> Dict[str, Any]:
    """Run the tool chain and collect its event stream.

    Delegates to the existing dispatcher so tool behaviour stays exactly as tested;
    the graph's contribution is that everything after this point is uniform.
    """
    from advisor.orchestrator.runner import _orchestrate_core

    events: List[Tuple[str, Any]] = list(
        _orchestrate_core(
            state["query"],
            state["ops_state"],
            state["repo"],
            state.get("client"),
            state.get("request_id"),
            state.get("chat_state"),
            precomputed=(
                state["clean_query"],
                state["pii_map"],
                state["resolved_crew_ids"],
                state["intent"],
            ),
        )
    )

    impact = None
    options: List[Any] = []
    prose = None
    recommendation = None
    outcome = "answered"
    question_asked = None

    for kind, payload in events:
        if kind == "evidence" and isinstance(payload, dict):
            impact = payload.get("impact") or impact
        elif kind == "options":
            options = payload or []
        elif kind == "prose":
            prose = payload
        elif kind == "recommendation":
            recommendation = payload
        elif kind == "abstain":
            outcome = "abstained"
            question_asked = None
        elif kind == "clarify":
            outcome = "clarification_requested"
            question_asked = (payload or {}).get("message")

    logger.debug("Graph node: dispatch", outcome=outcome, option_count=len(options))
    return {
        "events": events,
        "impact": impact,
        "options": options,
        "prose": prose,
        "recommendation": recommendation,
        "outcome": outcome,
        "question_asked": question_asked,
    }


def node_advise(state: AdvisorState) -> Dict[str, Any]:
    """Derive question-aware follow-ups grounded in this exchange."""
    intent = state.get("intent")
    if intent is None:
        return {"suggestions": []}

    suggestions = derive_suggestions(
        intent,
        impact=state.get("impact"),
        options=state.get("options") or [],
        awaiting_clarification=state.get("outcome") != "answered",
    )
    logger.debug("Graph node: advise", suggestion_count=len(suggestions))
    return {"suggestions": suggestions}


def node_record(state: AdvisorState) -> Dict[str, Any]:
    """Write this turn into conversation state.

    Runs on every route out of dispatch, so no intent can be forgotten.
    """
    chat_state: Optional[ConversationState] = state.get("chat_state")
    intent = state.get("intent")
    if chat_state is None or intent is None:
        return {}

    entities = _turn_entities(state)
    chat_state.record_turn(
        query=state.get("clean_query", ""),
        intent=intent.intent,
        entities=entities,
        outcome=state.get("outcome", "answered"),
        summary=_turn_summary(state),
        recommendation=state.get("recommendation"),
        suggestions=state.get("suggestions") or [],
        question_asked=state.get("question_asked"),
    )
    logger.debug("Graph node: record", turn=chat_state.turn_count)
    return {}


# ---------------------------------------------------------------------------
# Turn summarisation
# ---------------------------------------------------------------------------

def _turn_entities(state: AdvisorState) -> Dict[str, Any]:
    """Entities worth carrying forward, merged from the intent and the evidence."""
    intent = state["intent"]
    ents = dict(intent.entities or {})

    out: Dict[str, Any] = {
        "station": ents.get("base") or ents.get("station") or _first(ents.get("stations")),
        "crew_id": _first(state.get("resolved_crew_ids")) or _first(ents.get("crew_ids")),
        "flight_ids": ents.get("flight_ids") or [],
        "pairing_id": ents.get("pairing_id"),
        "rank": ents.get("rank"),
        "date": ents.get("date") or (intent.time_scope or {}).get("raw"),
        "origin": ents.get("origin"),
        "destination": ents.get("destination"),
    }

    # Evidence beats the query: record what actually happened, not what was typed.
    impact = state.get("impact")
    if impact is not None:
        out["crew_id"] = impact.disrupted_crew_id or out["crew_id"]
        out["pairing_id"] = impact.broken_pairing_id or out["pairing_id"]
        if impact.uncrewed_flights:
            out["flight_ids"] = [f.flight_id for f in impact.uncrewed_flights]
            out["station"] = impact.uncrewed_flights[0].origin or out["station"]

    return out


def _turn_summary(state: AdvisorState) -> str:
    """One-line description of what this turn produced."""
    intent = state["intent"]
    outcome = state.get("outcome", "answered")

    if outcome == "abstained":
        return f"Abstained on '{state.get('clean_query', '')[:60]}'"
    if outcome == "clarification_requested":
        return "Asked the controller for a missing parameter"

    impact = state.get("impact")
    options = state.get("options") or []
    if impact is not None:
        top = next((o.crew_id for o in options if o.crew_id != "DO_NOTHING"), None)
        return (
            f"Simulated {impact.disrupted_crew_id} disruption — "
            f"{len(impact.uncrewed_flights)} leg(s) uncrewed, "
            f"top candidate {top or 'none'}"
        )

    prose = state.get("prose") or ""
    first_line = prose.strip().splitlines()[0] if prose.strip() else ""
    first_line = first_line.lstrip("#> ").strip()
    return first_line[:120] or f"Handled {intent.intent}"


def _first(seq: Optional[List[Any]]) -> Optional[Any]:
    return seq[0] if seq else None


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_advisor_graph():
    """Compiles the advisor pipeline.

    resolve -> parse -> dispatch -> advise -> record -> END
    """
    graph = StateGraph(AdvisorState)

    graph.add_node("resolve", node_resolve)
    graph.add_node("parse", node_parse)
    graph.add_node("dispatch", node_dispatch)
    graph.add_node("advise", node_advise)
    graph.add_node("record", node_record)

    graph.add_edge(START, "resolve")
    graph.add_edge("resolve", "parse")
    graph.add_edge("parse", "dispatch")
    graph.add_edge("dispatch", "advise")
    graph.add_edge("advise", "record")
    graph.add_edge("record", END)

    return graph.compile()


_COMPILED_GRAPH = None


def get_advisor_graph():
    """Returns the compiled pipeline, building it once per process."""
    global _COMPILED_GRAPH
    if _COMPILED_GRAPH is None:
        _COMPILED_GRAPH = build_advisor_graph()
        logger.info("Compiled advisor LangGraph pipeline")
    return _COMPILED_GRAPH


def run_advisor_graph(
    query: str,
    ops_state: OpsState,
    repo: OpsRepository,
    client: Optional[LLMClient] = None,
    request_id: Optional[str] = None,
    chat_state: Optional[ConversationState] = None,
) -> List[Tuple[str, Any]]:
    """Runs the pipeline and returns the ordered event stream for this turn."""
    result = get_advisor_graph().invoke(
        {
            "query": query,
            "ops_state": ops_state,
            "repo": repo,
            "client": client,
            "request_id": request_id,
            "chat_state": chat_state,
        }
    )

    events: List[Tuple[str, Any]] = list(result.get("events") or [])
    suggestions = result.get("suggestions") or []
    if suggestions:
        events.append(("suggestions", suggestions))
    return events
