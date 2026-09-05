"""Modular operational tool registry for the Autonomous Crew Ops AI Agent."""

import uuid
from typing import Any, Dict, List, Optional
from advisor.audit.logger import StructuredLogger, append_audit_event
from advisor.data.repository import OpsRepository
from advisor.domain.evidence import CostBreakdown, ImpactReport, LegalityLedger, RecoveryOption
from advisor.domain.state import OpsState, Overlay
from advisor.domain.types import DutyProposal, Flight
from advisor.reasoning.candidates import enumerate_candidates
from advisor.reasoning.costing import compute_station_cancellation_loss
from advisor.reasoning.ranker import rank_recovery_options
from advisor.rules.engine import evaluate_all
from advisor.twin.diff import compute_twin_diff

logger = StructuredLogger("advisor.orchestrator.tools")


def tool_simulate_station_cancellations(
    repo: OpsRepository,
    state: OpsState,
    station: str,
    date: Optional[str] = "2026-09-15",
) -> Dict[str, Any]:
    """Simulates mass flight cancellations departing a station and computes passenger & financial loss."""
    clean_stn = station.upper().strip()
    logger.info("Executing tool: simulate_station_cancellations", station=clean_stn, date=date)

    # Fetch departing flights from repository
    flights = repo.list_flights_by_station(origin=clean_stn, date=date)
    flight_objs: List[Flight] = []
    total_pax = 0
    unique_tails = set()

    for f_dict in flights:
        f_obj = Flight(
            flight_id=f_dict["flight_id"],
            origin=f_dict["origin"],
            destination=f_dict["destination"],
            dep_utc=f_dict["dep_utc"],
            arr_utc=f_dict["arr_utc"],
            block_minutes=f_dict.get("block_minutes") or 120,
            aircraft_type=f_dict.get("aircraft_type") or "A320",
            tail_id=f_dict.get("tail_id") or "VT-DXA",
            rotation_id=f_dict.get("rotation_id"),
            rotation_seq=f_dict.get("rotation_seq"),
            passengers=f_dict.get("passengers") or 0,
        )
        flight_objs.append(f_obj)
        total_pax += f_obj.passengers
        if f_obj.tail_id:
            unique_tails.add(f_obj.tail_id)

    rates = repo.get_cost_rates()
    cost_breakdown = compute_station_cancellation_loss(
        flights=flight_objs,
        passengers=total_pax,
        rates=rates,
        unique_tails=list(unique_tails),
    )

    # Create cancellation overlays in digital twin shadow branch
    overlays = []
    for f in flight_objs:
        overlays.append(
            Overlay(
                overlay_id=f"ov-cancel-{f.flight_id}-{uuid.uuid4().hex[:4]}",
                kind="cancel",
                payload={"flight_id": f.flight_id, "station": clean_stn, "date": date},
                label=f"Mass cancellation of {f.flight_id} departing {clean_stn}",
            )
        )

    shadow_state = state
    for ov in overlays:
        shadow_state = shadow_state.apply(ov)

    shadow_view = shadow_state.materialize()

    append_audit_event(
        "MASS_CANCELLATION_SIMULATED",
        {
            "station": clean_stn,
            "date": date,
            "flight_count": len(flight_objs),
            "passengers": total_pax,
            "total_loss_inr": cost_breakdown.total_inr,
        },
    )

    return {
        "station": clean_stn,
        "date": date or "2026-09-15",
        "flights": flight_objs,
        "flight_count": len(flight_objs),
        "passengers_affected": total_pax,
        "grounded_tails": sorted(list(unique_tails)),
        "cost_breakdown": cost_breakdown,
        "twin_view": shadow_view,
        "overlays": overlays,
    }


def tool_simulate_crew_disruption(
    repo: OpsRepository,
    state: OpsState,
    crew_id: str,
    date: str = "2026-09-15",
) -> Dict[str, Any]:
    """Simulates a crew member disruption, evaluates broken pairings, and ranks legal reserves."""
    req_id = f"req-{uuid.uuid4().hex[:6]}"
    overlay_id = f"ov-sick-{crew_id}-{req_id}"

    logger.info("Executing tool: simulate_crew_disruption", crew_id=crew_id, date=date, req_id=req_id)
    ov = Overlay(
        overlay_id=overlay_id,
        kind="sick",
        payload={"crew_id": crew_id, "date": date},
        label=f"Sick callout for {crew_id}",
    )

    shadow_state = state.apply(ov)
    baseline_view = state.materialize()
    shadow_view = shadow_state.materialize()

    impact = compute_twin_diff(baseline_view, shadow_view, ov, repo)

    # Evaluate broken pairing legality ledger
    pairing = repo.get_pairing_for_crew(crew_id, at_utc=f"{date}T00:00:00Z")
    if pairing:
        disrupted_crew = repo.get_crew(crew_id)
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
        ledger = LegalityLedger(subject=crew_id, context="no_pairing", verdicts=[])

    rates = repo.get_cost_rates()
    candidates = enumerate_candidates(impact, shadow_state, repo, rates)
    ranked_options = rank_recovery_options(candidates, impact, rates, repo)

    return {
        "impact": impact,
        "ledger": ledger,
        "twin_view": shadow_view,
        "disrupted_crew_id": crew_id,
        "broken_pairing_id": pairing.pairing_id if pairing else None,
        "flight_ids": [f.flight_id for f in pairing.legs] if pairing else [],
        "disruption_overlay": ov,
        "request_id": req_id,
        "candidates": candidates,
        "ranked_options": ranked_options,
    }


def tool_commit_crew_reassignment(
    state: OpsState,
    pairing_id: str,
    disrupted_crew_id: str,
    replacement_crew_id: str,
    delay_minutes: int = 0,
) -> OpsState:
    """Commits a reassignment overlay to the Digital Twin, advancing clocks and preventing work collisions."""
    req_id = f"reassign-{uuid.uuid4().hex[:6]}"
    overlay = Overlay(
        overlay_id=req_id,
        kind="reassign",
        payload={
            "pairing_id": pairing_id,
            "disrupted_crew_id": disrupted_crew_id,
            "replacement_crew_id": replacement_crew_id,
            "delay_minutes": delay_minutes,
        },
        label=f"Reassign {pairing_id} to {replacement_crew_id}",
    )
    new_state = state.apply(overlay)
    logger.info(
        "Committed crew reassignment to Digital Twin",
        pairing_id=pairing_id,
        replacement=replacement_crew_id,
        disrupted=disrupted_crew_id,
    )
    append_audit_event(
        "REASSIGNMENT_COMMITTED",
        {
            "pairing_id": pairing_id,
            "disrupted_crew": disrupted_crew_id,
            "replacement_crew": replacement_crew_id,
            "delay_minutes": delay_minutes,
        },
    )
    return new_state


def tool_lookup_reserves(
    repo: OpsRepository,
    state: OpsState,
    station: str = "BLR",
    date: Optional[str] = "2026-09-15",
) -> Dict[str, Any]:
    """Queries reserves while validating their live availability against the Digital Twin state."""
    clean_stn = station.upper().strip()
    raw_reserves = repo.list_reserves(base=clean_stn, date=date)
    if not raw_reserves:
        raw_reserves = repo.list_reserves(base=clean_stn, distinct_crew=True)
    twin_view = state.materialize()

    crew_items = []
    reserve_details = []
    seen_cids = set()
    distinct_raw = []

    for r in raw_reserves:
        if r.crew_id in seen_cids:
            continue
        seen_cids.add(r.crew_id)
        distinct_raw.append(r)

        c = repo.get_crew(r.crew_id)
        ratings = repo.list_ratings(r.crew_id)
        clk = repo.get_duty_clock(r.crew_id)
        duty_7d = clk.duty_hours_7d if clk else 0.0

        # Check live twin state for assignment collisions
        twin_crew = twin_view.crew.get(r.crew_id)
        live_status = r.standby_status
        if twin_crew:
            if twin_crew.is_incapacitated:
                live_status = "INCAPACITATED"
            elif twin_crew.on_call_status == "CALLED" or twin_crew.assigned_pairing_id:
                live_status = f"CALLED ({twin_crew.assigned_pairing_id})"

        crew_items.append(
            f"• **{c.crew_id} ({c.name})** — {c.rank}, On-Call: {r.oncall_start_utc[11:16]}-{r.oncall_end_utc[11:16]} UTC ({live_status})"
        )
        reserve_details.append(
            {
                "crew_id": c.crew_id,
                "name": c.name,
                "rank": c.rank,
                "base": r.base,
                "ratings": ratings,
                "oncall_start_utc": r.oncall_start_utc,
                "oncall_end_utc": r.oncall_end_utc,
                "standby_status": live_status,
                "reachability_minutes": c.reachability_minutes or 45,
                "duty_hours_7d": duty_7d,
            }
        )

    return {
        "station": clean_stn,
        "date": date,
        "reserves": distinct_raw,
        "reserve_details": reserve_details,
        "crew_items": crew_items,
    }


def tool_evaluate_crew_move(
    repo: OpsRepository,
    state: OpsState,
    crew_id: str,
    flight_or_pairing_id: Optional[str] = None,
    specified_role: Optional[str] = None,
    date: Optional[str] = "2026-09-15",
) -> Dict[str, Any]:
    """Evaluates the DGCA legality and operational impact of moving a crew member onto a flight or pairing."""
    clean_cid = crew_id.upper().strip()
    crew = repo.find_crew(clean_cid)
    if not crew:
        return {
            "status": "crew_not_found",
            "crew_id": clean_cid,
            "available_crew": [c.crew_id for c in repo.list_crew_by_base(base="BLR")[:5]],
        }

    ratings = repo.list_ratings(clean_cid)
    certs = repo.list_certifications(clean_cid)
    clk = repo.get_duty_clock(clean_cid)

    # Rank mismatch detection (e.g. user calls Captain C-2087 an FO)
    rank_mismatch_note = None
    if specified_role and specified_role.lower() != crew.rank.lower():
        rank_mismatch_note = f"Note: Crew member {crew.crew_id} is registered as {crew.rank} {crew.name} (Base: {crew.base}), not a {specified_role}."

    # Pairing & Flight resolution
    pairing = None
    flight_id = None
    if flight_or_pairing_id:
        target_clean = flight_or_pairing_id.upper().strip()
        if target_clean.startswith("P-"):
            try:
                pairing = repo.get_pairing(target_clean)
                flight_id = pairing.legs[0].flight_id if pairing and pairing.legs else target_clean
            except Exception:
                return {"status": "pairing_not_found", "pairing_id": target_clean}
        else:
            conn = repo._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT pairing_id, flight_id FROM pairing_leg WHERE flight_id LIKE ? ORDER BY leg_seq ASC", (f"%{target_clean}%",))
            row = cursor.fetchone()
            if row:
                pairing_id = row["pairing_id"]
                flight_id = row["flight_id"]
                pairing = repo.get_pairing(pairing_id)
            else:
                # Check if flight exists at all
                fl = repo.find_flight(target_clean)
                if not fl:
                    return {"status": "flight_not_found", "flight_id": target_clean}
                flight_id = fl.flight_id

    if not pairing:
        pairing = repo.get_pairing("P-2291")
        if not flight_id:
            flight_id = pairing.legs[0].flight_id if pairing.legs else "DX412-2026-09-15"

    # Identify currently assigned and displaced crew
    assigns = repo.get_pairing_assignments(pairing.pairing_id)
    displaced_crew = None
    companion_crew = []
    target_role = crew.rank if crew.rank in ("Captain", "First Officer") else (specified_role or "Captain")
    for a in assigns:
        a_role = a.get("role") or a.get("rank", "Captain")
        if a_role.lower() == target_role.lower() and not displaced_crew:
            displaced_crew = a
        else:
            companion_crew.append(a)

    # Evaluate all 7 DGCA rules
    proposal = DutyProposal(
        proposal_id=f"prop-eval-{crew.crew_id}-{pairing.pairing_id}",
        pairing_id=pairing.pairing_id,
        flights=pairing.legs,
        start_utc=pairing.start_utc,
        end_utc=pairing.end_utc,
        sectors=len(pairing.legs),
    )
    context = {
        "ratings": ratings,
        "certifications": certs,
        "duty_clock": clk,
        "target_station": crew.base,
    }
    ledger = evaluate_all(crew, proposal, context)

    append_audit_event(
        "CREW_MOVE_EVALUATED",
        {
            "crew_id": clean_cid,
            "flight_id": flight_id,
            "pairing_id": pairing.pairing_id,
            "legal": ledger.legal,
            "breach_count": len(ledger.breaches),
            "displaced_crew": displaced_crew.get("crew_id") if displaced_crew else None,
        },
    )

    return {
        "status": "success",
        "crew": crew,
        "pairing": pairing,
        "flight_id": flight_id,
        "ratings": ratings,
        "duty_clock": clk,
        "rank_mismatch_note": rank_mismatch_note,
        "ledger": ledger,
        "legal": ledger.legal,
        "breaches": ledger.breaches,
        "displaced_crew": displaced_crew,
        "companion_crew": companion_crew,
    }


def tool_evaluate_flight_crew(
    repo: OpsRepository,
    flight_id: str,
) -> Dict[str, Any]:
    """Retrieves all crew assigned to a flight's pairing, explaining roles and potential displacement impacts."""
    clean_fid = flight_id.upper().strip()
    flight = repo.find_flight(clean_fid)
    if not flight:
        return {"status": "flight_not_found", "flight_id": clean_fid}

    conn = repo._get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT pairing_id FROM pairing_leg WHERE flight_id LIKE ? LIMIT 1", (f"%{clean_fid}%",))
    row = cursor.fetchone()
    if not row:
        return {"status": "no_pairing", "flight": flight}

    pairing_id = row["pairing_id"]
    pairing = repo.get_pairing(pairing_id)
    assigns = repo.get_pairing_assignments(pairing_id)

    return {
        "status": "success",
        "flight": flight,
        "pairing": pairing,
        "assignments": assigns,
    }


def tool_lookup_crew_info(
    repo: OpsRepository,
    crew_id: str,
) -> Dict[str, Any]:
    """Retrieves complete factual profile for a crew member."""
    clean_cid = crew_id.upper().strip()
    crew = repo.find_crew(clean_cid)
    if not crew:
        return {"status": "not_found", "crew_id": clean_cid}

    ratings = repo.list_ratings(clean_cid)
    certs = repo.list_certifications(clean_cid)
    clk = repo.get_duty_clock(clean_cid)

    conn = repo._get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT pairing_id, role FROM assignment WHERE crew_id = ?", (clean_cid,))
    assignments = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT * FROM reserve WHERE crew_id = ? AND standby_status = 'STANDBY'", (clean_cid,))
    reserve_rows = cursor.fetchall()

    return {
        "status": "found",
        "crew": crew,
        "ratings": ratings,
        "certifications": certs,
        "duty_clock": clk,
        "assignments": assignments,
        "is_reserve": len(reserve_rows) > 0,
        "reserve_shift": dict(reserve_rows[0]) if reserve_rows else None,
    }

