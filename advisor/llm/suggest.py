"""Question-aware follow-up suggestions for the controller console.

Suggestions are derived from the parsed intent and the entities the controller
actually named, so a follow-up never proposes a station or flight that was not part
of the conversation. This is deliberately deterministic — a wrong suggestion costs a
controller a wasted query, and there is no upside to generating them with an LLM.
"""

from typing import Any, Dict, List, Optional

from advisor.audit.logger import StructuredLogger
from advisor.domain.evidence import ImpactReport, RecoveryOption
from advisor.llm.parser import QueryIntent

logger = StructuredLogger("advisor.llm.suggest")

MAX_SUGGESTIONS = 3


def _first(seq: Optional[List[Any]]) -> Optional[Any]:
    return seq[0] if seq else None


def _entity_context(intent: QueryIntent, impact: Optional[ImpactReport]) -> Dict[str, Any]:
    """Pulls the concrete entities a follow-up is allowed to reference."""
    ents = intent.entities or {}
    station = (
        ents.get("base")
        or _first(ents.get("stations"))
        or ents.get("origin")
        or ents.get("station")
    )
    flight_id = _first(ents.get("flight_ids"))
    crew_id = _first(ents.get("crew_ids"))
    pairing_id = ents.get("pairing_id")
    date = ents.get("date") or (intent.time_scope or {}).get("raw")

    # Evidence beats the query: if a simulation ran, follow-ups should point at what
    # it actually found rather than at what the controller happened to type.
    if impact is not None:
        crew_id = impact.disrupted_crew_id or crew_id
        pairing_id = impact.broken_pairing_id or pairing_id
        if impact.uncrewed_flights:
            flight_id = impact.uncrewed_flights[0].flight_id
            station = impact.uncrewed_flights[0].origin or station

    return {
        "station": station,
        "flight_id": flight_id,
        "crew_id": crew_id,
        "pairing_id": pairing_id,
        "date": date,
    }


def derive_suggestions(
    intent: QueryIntent,
    impact: Optional[ImpactReport] = None,
    options: Optional[List[RecoveryOption]] = None,
    awaiting_clarification: bool = False,
) -> List[Dict[str, str]]:
    """Returns up to MAX_SUGGESTIONS follow-up actions grounded in this exchange.

    Each suggestion is a {"label", "query"} pair; the query is a directive the
    orchestrator can execute verbatim.
    """
    # While the advisor is asking the controller a question, competing chips just
    # pull them away from answering it.
    if awaiting_clarification:
        return []

    ctx = _entity_context(intent, impact)
    station = ctx["station"]
    flight_id = ctx["flight_id"]
    crew_id = ctx["crew_id"]
    pairing_id = ctx["pairing_id"]
    options = options or []

    out: List[Dict[str, str]] = []

    def add(label: str, query: str) -> None:
        if len(out) < MAX_SUGGESTIONS and not any(s["query"] == query for s in out):
            out.append({"label": label, "query": query})

    name = intent.intent

    if name in ("simulate_sick", "request_recovery_options"):
        legal = [o for o in options if o.ledger.legal and o.crew_id != "DO_NOTHING"]
        top = _first(legal) or _first([o for o in options if o.crew_id != "DO_NOTHING"])
        if top and pairing_id:
            add(
                f"🚀 Adopt {top.crew_id} for {pairing_id}",
                f"Reassign {top.crew_id} to pairing {pairing_id}",
            )
        blocked = [o for o in options if not o.ledger.legal and o.repair]
        if blocked:
            b = blocked[0]
            add(
                f"🔧 What clears {b.crew_id}?",
                f"What is the minimal repair to make {b.crew_id} legal for pairing {pairing_id}?",
            )
        if station:
            add(
                f"🔍 Standby strength at {station}",
                f"Who is on reserve at {station}?",
            )
        if flight_id:
            add(
                f"👥 Crew assigned to {flight_id}",
                f"Which crew are assigned to flight {flight_id}?",
            )

    elif name in ("evaluate_crew_move", "check_legality"):
        if flight_id:
            add(
                f"⚡ Recovery options for {flight_id}",
                f"Produce recovery options for {flight_id}",
            )
            add(
                f"👥 Crew assigned to {flight_id}",
                f"Which crew are assigned to flight {flight_id}?",
            )
        if crew_id:
            add(f"📋 Profile for {crew_id}", f"Show the crew profile for {crew_id}")

    elif name == "lookup_reserves":
        if station:
            add(
                f"✈️ Departures from {station}",
                f"Which flights depart {station}?",
            )
            add(
                f"⏱️ High duty hours at {station}",
                f"Which crew at {station} have 45 or more duty hours in 7 days?",
            )

    elif name == "lookup_crew_by_base":
        if station:
            add(f"🔍 Standby strength at {station}", f"Who is on reserve at {station}?")
            add(
                f"📜 Certs expiring at {station}",
                f"List crew whose licence expires in the next 30 days for {station}",
            )

    elif name == "lookup_crew_info":
        if crew_id:
            add(
                f"⚖️ Duty limits for {crew_id}",
                f"Does {crew_id} breach any duty limit if assigned tomorrow?",
            )
            if pairing_id:
                add(
                    f"👥 Companions on {pairing_id}",
                    f"Which crew are assigned to pairing {pairing_id}?",
                )

    elif name in ("lookup_flights", "lookup_pairing_crew", "lookup_flight_crew"):
        if flight_id:
            add(
                f"⚡ Recovery options for {flight_id}",
                f"Produce recovery options for {flight_id}",
            )
        if station:
            add(f"🔍 Standby strength at {station}", f"Who is on reserve at {station}?")

    elif name == "cancel_station_departures":
        if station:
            add(
                f"🔍 Reserves available at {station}",
                f"Who is on reserve at {station}?",
            )
            add(
                f"✈️ Rotations affected at {station}",
                f"Which flights depart {station}?",
            )

    elif name == "lookup_closure_impact":
        if station:
            add(
                f"⚡ Recovery plan for {station}",
                f"Produce recovery options for the {station} closure",
            )
            add(f"🔍 Reserves at {station}", f"Who is on reserve at {station}?")

    elif name == "lookup_expiring_certs":
        add(
            "⏱️ Crew near duty limits",
            "Which crew have 45 or more duty hours in the last 7 days?",
        )
        if station:
            add(f"🔍 Standby strength at {station}", f"Who is on reserve at {station}?")

    # Generic tail: only ever references entities that appeared in this exchange.
    if not out:
        if flight_id:
            add(
                f"⚡ Recovery options for {flight_id}",
                f"Produce recovery options for {flight_id}",
            )
        if station:
            add(f"🔍 Standby strength at {station}", f"Who is on reserve at {station}?")

    logger.debug(
        "Derived follow-up suggestions",
        intent=name,
        count=len(out),
        station=station,
        flight_id=flight_id,
    )
    return out
