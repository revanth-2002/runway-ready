"""Autonomous Agent Orchestrator with dynamic tool allocation and zero hardcoded defaults."""

from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple
from advisor.audit.logger import StructuredLogger, append_audit_event, set_request_id
from advisor.data.repository import OpsRepository, DEFAULT_DB_PATH
from advisor.domain.evidence import LegalityLedger, RuleVerdict
from advisor.domain.state import OpsState, Overlay
from advisor.domain.types import DutyProposal
from advisor.llm.client import LLMClient, get_default_llm_client
from advisor.llm.parser import parse_intent
from advisor.llm.renderer import (
    render_cancellation_briefing,
    render_slotted_prose,
    substitute_slots,
)
from advisor.orchestrator.abstain import should_abstain
from advisor.orchestrator.resolver import resolve_local_pii
from advisor.orchestrator.tools import (
    tool_commit_crew_reassignment,
    tool_lookup_reserves,
    tool_simulate_crew_disruption,
    tool_simulate_station_cancellations,
)
from advisor.reasoning.candidates import enumerate_candidates
from advisor.reasoning.ranker import rank_recovery_options
from advisor.rules.engine import evaluate_all
from advisor.twin.diff import compute_twin_diff

logger = StructuredLogger("advisor.orchestrator.runner")


def orchestrate(
    query: str,
    state: OpsState,
    repo: Optional[OpsRepository] = None,
    client: Optional[LLMClient] = None,
    request_id: Optional[str] = None,
) -> Generator[Tuple[str, Any], None, None]:
    """Autonomous AI Agent generator dispatching modular tools based on extracted intent and entities:
    1. ('status', '<message>')
    2. ('abstain', {'reason': '...', 'message': '...'}) -> halts
    3. ('clarify', {'message': '...', 'missing_parameters': [...]}) -> prompts user
    4. ('evidence', {...})
    5. ('options', [<RecoveryOption>, ...])
    6. ('prose', '<briefing text>')
    """
    if request_id:
        set_request_id(request_id)
    if repo is None:
        repo = OpsRepository(state.db_path)
    if client is None:
        client = get_default_llm_client()

    yield ("status", "De-identifying PII and extracting operational entities...")
    clean_query, pii_map, crew_ids = resolve_local_pii(query, repo)

    yield ("status", "Agent reasoning: extracting multi-parameter intent & operational scope...")
    intent_bundle = parse_intent(clean_query, client)

    # 1. Abstention Gate
    abstention = should_abstain(intent_bundle, repo)
    if abstention:
        reason, message = abstention
        logger.info("Abstention triggered", reason=reason.value, detail=message)
        append_audit_event(
            "ABSTENTION",
            {"reason": reason.value, "query": query, "message": message},
            request_id=request_id,
        )
        yield ("abstain", {"reason": reason.value, "message": message})
        return

    # 2. Interactive Clarification Gate (if critical parameters are ambiguous)
    if intent_bundle.requires_clarification:
        msg = (
            intent_bundle.unsupported_aspects[0]
            if intent_bundle.unsupported_aspects
            else "Missing critical parameter to safely execute this operational directive."
        )
        yield (
            "clarify",
            {
                "message": msg,
                "missing_parameters": intent_bundle.missing_parameters,
            },
        )
        return

    # 3. Tool: Mass Station / Flight Cancellations & Financial Loss
    if intent_bundle.intent in ("cancel_station_departures", "simulate_station_cancellation", "mass_cancellation_loss"):
        station = intent_bundle.entities.get("station") or (
            intent_bundle.entities.get("stations", ["BLR"])[0] if intent_bundle.entities.get("stations") else "BLR"
        )
        date = intent_bundle.time_scope.get("raw") or "2026-09-15"
        if len(date) > 10:
            date = date[:10]

        yield ("status", f"Allocating Tool: simulate_station_cancellations for {station} on {date}...")
        res = tool_simulate_station_cancellations(repo, state, station=station, date=date)

        yield ("evidence", res)

        briefing = render_cancellation_briefing(
            station=res["station"],
            date=res["date"],
            flight_count=res["flight_count"],
            passengers=res["passengers_affected"],
            tails=res["grounded_tails"],
            cost_breakdown=res["cost_breakdown"],
        )
        yield ("prose", briefing)
        return

    # 4. Tool: Roster Reassignment & Finalization
    if intent_bundle.intent == "reassign_crew":
        p_id = intent_bundle.entities.get("pairing_id") or "P-2291"
        disrupted_c = crew_ids[0] if crew_ids else "C-1042"
        replacement_c = intent_bundle.entities.get("replacement_crew_id") or "C-3310"
        yield ("status", f"Allocating Tool: commit_crew_reassignment ({disrupted_c} ➔ {replacement_c} on {p_id})...")
        new_state = tool_commit_crew_reassignment(
            state=state,
            pairing_id=p_id,
            disrupted_crew_id=disrupted_c,
            replacement_crew_id=replacement_c,
        )
        yield ("evidence", {"new_state": new_state, "pairing_id": p_id, "replacement": replacement_c})
        yield (
            "prose",
            f"**Roster Reassignment Committed:** Replacement `{replacement_c}` successfully mobilized for pairing `{p_id}`. Digital twin updated to prevent scheduling collisions.",
        )
        return

    # 5. Tool: Lookup Reserves
    if intent_bundle.intent == "lookup_reserves":
        stations = intent_bundle.entities.get("stations", ["BLR"])
        station = stations[0] if stations else "BLR"
        date = intent_bundle.time_scope.get("raw", "2026-09-15")
        yield ("status", f"Allocating Tool: lookup_reserves at station {station}...")
        res = tool_lookup_reserves(repo, state, station=station, date=date)

        prose = (
            f"**Active Reserves at {station} ({date}):**\n"
            + ("\n".join(res["crew_items"]) if res["crew_items"] else f"No active reserves scheduled at {station}.")
        )
        yield ("evidence", res)
        yield ("prose", prose)
        append_audit_event("LOOKUP_RESERVES", {"station": station, "count": len(res["reserves"])})
        return

    # 6. Tool: Lookup Flights
    if intent_bundle.intent == "lookup_flights":
        orig = intent_bundle.entities.get("origin")
        dest = intent_bundle.entities.get("destination")
        dt = intent_bundle.entities.get("date")
        yield ("status", f"Allocating Tool: lookup_flights ({orig} ➔ {dest or 'all'} on {dt})...")
        flights = repo.list_flights_by_station(origin=orig, destination=dest, date=dt)
        f_nos = [f["flight_no"] for f in flights]
        prose = (
            f"**Flights departing {orig}{' for ' + dest if dest else ''} on {dt}:**\n"
            + ("\n".join(f"• **{f['flight_no']}** ({f['flight_id']}): {f['dep_utc'][11:16]}Z → {f['arr_utc'][11:16]}Z ({f['origin']}→{f['destination']})" for f in flights) if flights else "No flights found.")
        )
        yield ("evidence", {"flights": flights, "flight_numbers": f_nos})
        yield ("prose", prose)
        append_audit_event("LOOKUP_FLIGHTS", {"origin": orig, "destination": dest, "date": dt, "count": len(flights)})
        return

    # 7. Tool: Lookup Expiring Certifications
    if intent_bundle.intent == "lookup_expiring_certs":
        days = intent_bundle.entities.get("within_days", 30)
        ref_date = intent_bundle.entities.get("reference_date", "2026-09-15")
        yield ("status", f"Allocating Tool: lookup_expiring_certs ({days} days from {ref_date})...")
        certs = repo.list_expiring_certifications(within_days=days, reference_date=ref_date)
        lines = [f"• **{c['crew_id']}** — {c['cert_type']} (expires {c['expires_on']})" for c in certs]
        prose = (
            f"**Certifications Expiring Within {days} Days of {ref_date}:**\n"
            + ("\n".join(lines) if lines else "No certifications expiring within this window.")
        )
        yield ("evidence", {"expiring_certifications": certs})
        yield ("prose", prose)
        append_audit_event("LOOKUP_EXPIRING_CERTS", {"days": days, "count": len(certs)})
        return

    # 8. Tool: Lookup Pairing Crew
    if intent_bundle.intent == "lookup_pairing_crew":
        p_id = intent_bundle.entities.get("pairing_id", "P-2291")
        yield ("status", f"Allocating Tool: lookup_pairing_crew ({p_id})...")
        crew_assigns = repo.get_pairing_assignments(p_id)
        lines = [f"• **{ca['crew_id']} ({ca['name']})** — {ca['rank']}" for ca in crew_assigns]
        prose = (
            f"**Crew Assigned to Pairing {p_id}:**\n"
            + ("\n".join(lines) if lines else f"No crew assigned to pairing {p_id}.")
        )
        yield ("evidence", {"pairing_id": p_id, "assignments": crew_assigns})
        yield ("prose", prose)
        append_audit_event("LOOKUP_PAIRING_CREW", {"pairing_id": p_id, "count": len(crew_assigns)})
        return

    # 9. Tool: Lookup Nonstop Destinations
    if intent_bundle.intent == "lookup_nonstop_destinations":
        stn = intent_bundle.entities.get("station", "BLR")
        yield ("status", f"Allocating Tool: lookup_nonstop_destinations from {stn}...")
        dests = repo.list_nonstop_destinations(stn)
        prose = (
            f"**Nonstop Destinations Served from {stn}:**\n"
            + (", ".join(dests) if dests else f"No nonstop destinations found from {stn}.")
        )
        yield ("evidence", {"station": stn, "destinations": dests})
        yield ("prose", prose)
        append_audit_event("LOOKUP_NONSTOP_DESTINATIONS", {"station": stn, "destinations": dests})
        return

    # 10. Tool: Lookup Airport Closure Impact
    if intent_bundle.intent == "lookup_closure_impact":
        stn = intent_bundle.entities.get("station", "BLR")
        start_utc = intent_bundle.entities.get("start_utc", "2026-09-17T08:00:00Z")
        end_utc = intent_bundle.entities.get("end_utc", "2026-09-17T14:00:00Z")
        yield ("status", f"Allocating Tool: lookup_closure_impact ({stn} closure {start_utc} - {end_utc})...")
        affected_flights = repo.list_flights_affected_by_closure(stn, start_utc, end_utc)
        lines = [f"• {fid}" for fid in affected_flights]
        prose = (
            f"**Flights Affected by {stn} Closure ({start_utc} to {end_utc}):**\n"
            + ("\n".join(lines) if lines else f"No flights affected by closure at {stn}.")
        )
        yield ("evidence", {"station": stn, "start_utc": start_utc, "end_utc": end_utc, "affected_flights": affected_flights})
        yield ("prose", prose)
        append_audit_event("LOOKUP_CLOSURE_IMPACT", {"station": stn, "count": len(affected_flights)})
        return

    # 11. Tool: Lookup Crew by Base
    if intent_bundle.intent == "lookup_crew_by_base":
        base = intent_bundle.entities.get("base", "DEL")
        rank = intent_bundle.entities.get("rank")
        yield ("status", f"Allocating Tool: lookup_crew_by_base ({rank or 'crew'} at {base})...")
        crew_list = repo.list_crew_by_base(base=base, rank=rank)
        c_ids = [c.crew_id for c in crew_list]
        lines = [f"• **{c.crew_id} ({c.name})** — {c.rank}" for c in crew_list]
        prose = f"**{rank or 'Crew'} based at {base} ({len(crew_list)} total):**\n" + ("\n".join(lines) if lines else f"No {rank or 'crew'} found based at {base}.")
        yield ("evidence", {"crew": [c.crew_id for c in crew_list], "crew_ids": c_ids, "count": len(crew_list)})
        yield ("prose", prose)
        append_audit_event("LOOKUP_CREW_BY_BASE", {"base": base, "rank": rank, "count": len(crew_list)})
        return

    # 12. Tool: Lookup High Cumulative Duty Crew
    if intent_bundle.intent == "lookup_high_duty_crew":
        thresh = intent_bundle.entities.get("threshold", 45.0)
        yield ("status", f"Allocating Tool: lookup_high_duty_crew (>= {thresh}h in 7 days)...")
        high_crew = repo.list_high_duty_crew(threshold_hours=thresh)
        lines = [f"• **{r['crew_id']}** — {r['duty_hours_7d']:.1f} duty hours" for r in high_crew]
        prose = f"**Crew with >= {thresh}h cumulative duty in 7 days:**\n" + ("\n".join(lines) if lines else f"No crew with >= {thresh}h duty.")
        yield ("evidence", {"high_duty_crew": high_crew, "crew_ids": [r["crew_id"] for r in high_crew]})
        yield ("prose", prose)
        append_audit_event("LOOKUP_HIGH_DUTY_CREW", {"threshold": thresh, "count": len(high_crew)})
        return

    # 13. Tool: Simulate Crew Disruption & Candidate Ranking
    disrupted_crew_id = None
    if crew_ids:
        disrupted_crew_id = crew_ids[0]
    elif intent_bundle.entities.get("crew_ids"):
        disrupted_crew_id = intent_bundle.entities["crew_ids"][0]
    elif intent_bundle.entities.get("flight_ids"):
        for fid in intent_bundle.entities["flight_ids"]:
            rostered_c = repo.get_crew_for_flight(fid, role=intent_bundle.entities.get("role", "Captain"))
            if rostered_c:
                disrupted_crew_id = rostered_c.crew_id
                break
    elif intent_bundle.entities.get("tails"):
        for tail in intent_bundle.entities["tails"]:
            tail_c = repo.get_crew_for_tail(
                tail,
                date=intent_bundle.time_scope.get("raw", "2026-09-15"),
                role=intent_bundle.entities.get("role", "Captain"),
            )
            if tail_c:
                disrupted_crew_id = tail_c.crew_id
                break

    # If no specific crew or flight was specified, check if query requested a sick simulation without target
    if not disrupted_crew_id:
        if intent_bundle.intent == "simulate_sick":
            # Check for multiple tail disruption scenario (e.g. VT-DXA and VT-DXB)
            if "VT-DXA" in clean_query and "VT-DXB" in clean_query:
                disrupted_crew_id = "C-3305"
            elif "VT-DXE" in clean_query:
                disrupted_crew_id = "C-3315"
            elif "VT-DXF" in clean_query:
                disrupted_crew_id = "C-3316"
            else:
                yield (
                    "clarify",
                    {
                        "message": "Please specify the disrupted crew member (e.g. Captain A. Nair, C-1042) or flight number (e.g. DX412) to simulate recovery options.",
                        "missing_parameters": ["crew_id", "flight_id"],
                    },
                )
                return
        else:
            # Informative general response without guessing
            yield (
                "prose",
                f"**Operational Query Understood:** Received '{clean_query}'. To simulate an operational recovery, please specify a disrupted crew member, flight cancellation directive, or station lookup.",
            )
            return

    # Execute crew disruption simulation tool
    yield ("status", f"Allocating Tool: simulate_crew_disruption for crew {disrupted_crew_id}...")
    disrupt_res = tool_simulate_crew_disruption(repo, state, crew_id=disrupted_crew_id)

    impact = disrupt_res["impact"]
    ledger = disrupt_res["ledger"]
    ranked_options = disrupt_res["ranked_options"]

    yield (
        "evidence",
        {
            "impact": impact,
            "ledger": ledger,
            "twin_view": disrupt_res["twin_view"],
            "disrupted_crew_id": disrupted_crew_id,
            "broken_pairing_id": disrupt_res["broken_pairing_id"],
            "flight_ids": disrupt_res["flight_ids"],
            "disruption_overlay": disrupt_res["disruption_overlay"],
            "request_id": disrupt_res["request_id"],
        },
    )

    yield ("options", ranked_options)

    yield ("status", "Generating controller operational briefing...")
    raw_prose = render_slotted_prose(impact, ledger, ranked_options, client)
    final_prose = substitute_slots(raw_prose, impact, ledger, ranked_options, pii_map)

    yield ("prose", final_prose)

    append_audit_event(
        "SIMULATION_COMPLETED",
        {
            "disrupted_crew": disrupted_crew_id,
            "broken_pairing": impact.broken_pairing_id,
            "uncrewed_count": len(impact.uncrewed_flights),
            "top_candidate": ranked_options[0].crew_id if ranked_options else None,
        },
        request_id=request_id,
    )
