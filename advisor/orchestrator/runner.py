"""In-process generator orchestrator yielding progressive execution stages."""

from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple
from advisor.audit.logger import StructuredLogger, append_audit_event
from advisor.data.repository import OpsRepository, DEFAULT_DB_PATH
from advisor.domain.evidence import LegalityLedger, RuleVerdict
from advisor.domain.state import OpsState, Overlay
from advisor.domain.types import DutyProposal
from advisor.llm.client import LLMClient, get_default_llm_client
from advisor.llm.parser import parse_intent
from advisor.llm.renderer import render_slotted_prose, substitute_slots
from advisor.orchestrator.abstain import should_abstain
from advisor.orchestrator.resolver import resolve_local_pii
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
) -> Generator[Tuple[str, Any], None, None]:
    """In-process generator yielding stages:
    1. ('status', '<message>')
    2. ('abstain', {'reason': '...', 'message': '...'}) -> halts
    3. ('evidence', {'impact': ..., 'ledger': ..., 'twin_view': ...})
    4. ('options', [<RecoveryOption>, ...])
    5. ('prose', '<briefing text>')
    """
    if repo is None:
        repo = OpsRepository(state.db_path)
    if client is None:
        client = get_default_llm_client()

    yield ("status", "Anonymizing query and resolving local entities...")
    clean_query, pii_map, crew_ids = resolve_local_pii(query, repo)

    yield ("status", "Parsing natural language intent...")
    intent_bundle = parse_intent(clean_query, client)

    # Abstention check
    abstention = should_abstain(intent_bundle, repo)
    if abstention:
        reason, message = abstention
        logger.info("Abstention triggered", reason=reason.value, detail=message)
        append_audit_event("ABSTENTION", {"reason": reason.value, "query": query, "message": message})
        yield ("abstain", {"reason": reason.value, "message": message})
        return

    # Handle Tier 1 Point Lookups (e.g. Reserve lookup)
    if intent_bundle.intent == "lookup_reserves":
        stations = intent_bundle.entities.get("stations", ["BLR"])
        station = stations[0] if stations else "BLR"
        yield ("status", f"Querying active reserves at station {station}...")
        reserves = repo.list_reserves(base=station)

        crew_items = []
        reserve_details = []
        for r in reserves:
            c = repo.get_crew(r.crew_id)
            ratings = repo.list_ratings(r.crew_id)
            clk = repo.get_duty_clock(r.crew_id)
            duty_7d = clk.duty_hours_7d if clk else 0.0
            crew_items.append(f"• **{c.crew_id} ({c.name})** — {c.rank}, On-Call: {r.oncall_start_utc[11:16]}-{r.oncall_end_utc[11:16]} UTC ({r.standby_status})")
            reserve_details.append({
                "crew_id": c.crew_id,
                "name": c.name,
                "rank": c.rank,
                "base": r.base,
                "ratings": ratings,
                "oncall_start_utc": r.oncall_start_utc,
                "oncall_end_utc": r.oncall_end_utc,
                "standby_status": r.standby_status,
                "reachability_minutes": c.reachability_minutes or 45,
                "duty_hours_7d": duty_7d,
            })

        prose = (
            f"**Active Reserves at {station} (2026-09-15):**\n"
            + ("\n".join(crew_items) if crew_items else f"No active reserves scheduled at {station}.")
        )
        yield ("evidence", {"reserves": reserves, "reserve_details": reserve_details, "station": station})
        yield ("prose", prose)
        append_audit_event("LOOKUP_RESERVES", {"station": station, "count": len(reserves)})
        return

    if intent_bundle.intent == "lookup_flights":
        orig = intent_bundle.entities.get("origin")
        dest = intent_bundle.entities.get("destination")
        dt = intent_bundle.entities.get("date")
        yield ("status", f"Querying flights from {orig}{' to ' + dest if dest else ''} on {dt}...")
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

    if intent_bundle.intent == "lookup_expiring_certs":
        days = intent_bundle.entities.get("within_days", 30)
        ref_date = intent_bundle.entities.get("reference_date", "2026-09-15")
        yield ("status", f"Querying certifications expiring within {days} days of {ref_date}...")
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

    if intent_bundle.intent == "lookup_pairing_crew":
        p_id = intent_bundle.entities.get("pairing_id", "P-2291")
        yield ("status", f"Querying crew assigned to pairing {p_id}...")
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

    if intent_bundle.intent == "lookup_nonstop_destinations":
        stn = intent_bundle.entities.get("station", "BLR")
        yield ("status", f"Querying nonstop destinations from {stn}...")
        dests = repo.list_nonstop_destinations(stn)
        prose = (
            f"**Nonstop Destinations Served from {stn}:**\n"
            + (", ".join(dests) if dests else f"No nonstop destinations found from {stn}.")
        )
        yield ("evidence", {"station": stn, "destinations": dests})
        yield ("prose", prose)
        append_audit_event("LOOKUP_NONSTOP_DESTINATIONS", {"station": stn, "destinations": dests})
        return

    if intent_bundle.intent == "lookup_closure_impact":
        stn = intent_bundle.entities.get("station", "BLR")
        start_utc = intent_bundle.entities.get("start_utc", "2026-09-17T08:00:00Z")
        end_utc = intent_bundle.entities.get("end_utc", "2026-09-17T14:00:00Z")
        yield ("status", f"Analyzing closure impact for {stn} ({start_utc} - {end_utc})...")
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


    # Tier 2 & 3: Disruption Simulation & Candidate Ranking
    disrupted_crew_id = None
    if crew_ids:
        disrupted_crew_id = crew_ids[0]
    elif intent_bundle.entities.get("crew_ids"):
        disrupted_crew_id = intent_bundle.entities["crew_ids"][0]
    elif intent_bundle.entities.get("flight_ids"):
        for fid in intent_bundle.entities["flight_ids"]:
            rostered_c = repo.get_crew_for_flight(fid, role="Captain")
            if rostered_c:
                disrupted_crew_id = rostered_c.crew_id
                break

    if not disrupted_crew_id:
        disrupted_crew_id = "C-1042"

    import uuid
    req_id = f"req-{uuid.uuid4().hex[:6]}"
    overlay_id = f"ov-sick-{disrupted_crew_id}-{req_id}"

    yield ("status", f"Forking Digital Twin shadow branch for crew {disrupted_crew_id} ({req_id})...")
    ov = Overlay(
        overlay_id=overlay_id,
        kind="sick",
        payload={"crew_id": disrupted_crew_id, "date": "2026-09-15"},
        label=f"Sick callout for {disrupted_crew_id}",
    )

    shadow_state = state.apply(ov)
    baseline_view = state.materialize()
    shadow_view = shadow_state.materialize()

    yield ("status", "Propagating physical tail delays and broken pairings...")
    impact = compute_twin_diff(baseline_view, shadow_view, ov, repo)

    # Evaluate broken pairing legality ledger
    pairing = repo.get_pairing_for_crew(disrupted_crew_id, at_utc="2026-09-15T00:00:00Z")
    if pairing:
        disrupted_crew = repo.get_crew(disrupted_crew_id)
        prop = DutyProposal(
            proposal_id=f"prop-{pairing.pairing_id}",
            pairing_id=pairing.pairing_id,
            flights=pairing.legs,
            start_utc=pairing.start_utc,
            end_utc=pairing.end_utc,
            sectors=len(pairing.legs),
            passengers=impact.passengers_affected,
        )
        ledger = evaluate_all(
            disrupted_crew,
            prop,
            {
                "ratings": repo.list_ratings(disrupted_crew.crew_id),
                "certifications": repo.list_certifications(disrupted_crew.crew_id),
                "duty_clock": repo.get_duty_clock(disrupted_crew.crew_id),
                "target_station": disrupted_crew.base,
            },
        )
    else:
        ledger = LegalityLedger(subject=disrupted_crew_id, context="no_pairing", verdicts=[])

    # Stream evidence frame
    yield (
        "evidence",
        {
            "impact": impact,
            "ledger": ledger,
            "twin_view": shadow_view,
            "disrupted_crew_id": disrupted_crew_id,
            "broken_pairing_id": pairing.pairing_id if pairing else None,
            "flight_ids": [f.flight_id for f in pairing.legs] if pairing else [],
            "disruption_overlay": ov,
            "request_id": req_id,
        },
    )

    yield ("status", "Searching legal reserve candidates and computing repair levers...")
    rates = repo.get_cost_rates()
    candidates = enumerate_candidates(impact, shadow_state, repo, rates)
    ranked_options = rank_recovery_options(candidates, impact, rates, repo)

    yield ("options", ranked_options)

    yield ("status", "Generating controller operational briefing...")
    raw_prose = render_slotted_prose(impact, ledger, ranked_options, client)
    final_prose = substitute_slots(raw_prose, impact, ledger, ranked_options, pii_map)

    yield ("prose", final_prose)

    # Audit log
    append_audit_event(
        "SIMULATION_COMPLETED",
        {
            "disrupted_crew": disrupted_crew_id,
            "broken_pairing": impact.broken_pairing_id,
            "uncrewed_count": len(impact.uncrewed_flights),
            "top_candidate": ranked_options[0].crew_id if ranked_options else None,
        },
    )
