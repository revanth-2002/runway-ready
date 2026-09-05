"""REST API Route handlers for Crew Ops Advisor (/api/v1/...)."""

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, HTTPException, Path as FastApiPath, Query, status

from advisor.api.schemas import (
    CandidateOptionSchema,
    CostBreakdownSchema,
    DisruptionSimulateRequest,
    DisruptionSimulateResponse,
    FinalizeRecommendationRequest,
    FinalizeRecommendationResponse,
    FleetRotationsResponse,
    FlightManifestItem,
    HealthResponse,
    NetworkKPIs,
    NetworkOverviewResponse,
    OverlaySummary,
    RepairOptionSchema,
    ReserveItemSchema,
    ReserveListResponse,
    StationDetailResponse,
    StationFlightMovement,
    StationSummary,
    TailRotation,
    TwinActionResponse,
    TwinStateResponse,
    WeatherCondition,
    WeatherForecastPeriod,
)
from advisor.audit.logger import StructuredLogger, append_audit_event
from advisor.data.repository import OpsRepository, DEFAULT_DB_PATH
from advisor.domain.state import OpsState, Overlay
from advisor.llm.client import get_active_llm_info, StubClient
from advisor.orchestrator.runner import orchestrate
from advisor.twin.warm import warm_operational_digital_twin, DEFAULT_STATIONS

logger = StructuredLogger("advisor.api.routes")

router = APIRouter(prefix="/api/v1", tags=["Operational Digital Twin"])


# -------------------------------------------------------------------------
# In-Memory Operational State Management
# -------------------------------------------------------------------------

class TwinManager:
    """Singleton holding the server's live operational digital twin state."""

    def __init__(self, db_path=DEFAULT_DB_PATH):
        self.db_path = db_path
        self.warmed = warm_operational_digital_twin(db_path=self.db_path)
        self.state: OpsState = self.warmed["state"]
        self.repo: OpsRepository = self.warmed["repo"]
        self.action_history: List[Dict[str, Any]] = []

    def reset(self, force_rebuild: bool = False):
        if hasattr(self, "repo") and getattr(self.repo, "_conn", None):
            try:
                self.repo._conn.close()
                self.repo._conn = None
            except Exception:
                pass
        self.warmed = warm_operational_digital_twin(db_path=self.db_path, force_rebuild=force_rebuild)
        self.state = self.warmed["state"]
        self.repo = self.warmed["repo"]
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
        self.action_history.insert(0, {
            "timestamp": now_str,
            "action": "RESET_BASELINE",
            "description": "Purged all overlays and re-materialized clean 06:00Z baseline",
        })

    def undo(self) -> Optional[Overlay]:
        if not self.state.overlays:
            return None
        popped_overlay = self.state.overlays[-1]
        self.state = self.state.pop()
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
        self.action_history.insert(0, {
            "timestamp": now_str,
            "action": "UNDO",
            "description": f"Reverted overlay {popped_overlay.label}",
        })
        return popped_overlay


# Global instance
twin_manager = TwinManager()


# -------------------------------------------------------------------------
# Health Endpoint
# -------------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    """Service health and LLM provider status check."""
    llm_info = get_active_llm_info()
    return HealthResponse(
        status="healthy",
        version="1.0.0",
        twin_warmed=True,
        llm_mode="gemini_live" if llm_info.get("configured") else "deterministic_stub",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# -------------------------------------------------------------------------
# Workspace 1: Network Overview & Station Health
# -------------------------------------------------------------------------

@router.get("/network/overview", response_model=NetworkOverviewResponse)
def get_network_overview() -> NetworkOverviewResponse:
    """Returns high-level situational awareness metrics and station summaries for 5 hub bases."""
    twin_view = twin_manager.state.materialize()
    flights = twin_manager.repo.list_flights()

    total_flights = len(flights)
    statuses = twin_view.flight_statuses

    uncrewed_count = sum(1 for s in statuses.values() if s == "UNCREWED")
    delayed_count = sum(1 for s in statuses.values() if s == "DELAYED")
    on_time_count = sum(1 for s in statuses.values() if s == "ON_TIME")

    punctuality_rate = round((on_time_count / total_flights * 100) if total_flights > 0 else 100.0, 1)

    seats_at_risk = sum(
        f.passengers or 0
        for f in flights
        if statuses.get(f.flight_id) in ("UNCREWED", "DELAYED", "CANCELLED")
    )

    # Station Summaries
    station_advisories = {
        "BLR": ("Active Runway Maintenance 08:00Z-11:00Z; Turnarounds nominal", None),
        "DEL": ("Peak slot congestion in Sector 3; Standbys alerted", None),
        "BOM": (None, "Coastal crosswinds 18kts; All gates operational"),
        "HYD": ("Nominal operations; High standby availability", None),
        "MAA": ("Nominal operations; All rotations on time", None),
    }

    station_summaries = []
    total_avail_reserves = 0

    for stn in DEFAULT_STATIONS:
        raw_reserves = twin_manager.repo.list_reserves(base=stn, distinct_crew=True)
        avail = 0
        for r in raw_reserves:
            # Check if reserve crew is affected by overlays
            crew_state = twin_view.crew.get(r.crew_id)
            if crew_state and not crew_state.is_incapacitated and not crew_state.assigned_pairing_id:
                avail += 1

        total_avail_reserves += avail
        dept_count = sum(1 for f in flights if f.origin == stn)
        adv, maint = station_advisories.get(stn, (None, None))

        station_summaries.append(
            StationSummary(
                station=stn,
                total_reserves=len(raw_reserves),
                available_reserves=avail,
                scheduled_departures=dept_count,
                weather_advisory=adv,
                maintenance_notice=maint,
            )
        )

    kpis = NetworkKPIs(
        total_active_tails=len(twin_view.tails),
        scheduled_flights=total_flights,
        on_time_rate_pct=punctuality_rate,
        disruption_alerts_count=uncrewed_count + len(twin_manager.state.overlays),
        passenger_seats_at_risk=seats_at_risk,
        total_available_reserves=total_avail_reserves,
    )

    return NetworkOverviewResponse(
        kpis=kpis,
        stations=station_summaries,
        active_overlays_count=len(twin_manager.state.overlays),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# -------------------------------------------------------------------------
# Airport Station Operations & Weather Deep-Dive (BLR, DEL, BOM, HYD, MAA)
# -------------------------------------------------------------------------

WEATHER_DATA_PATHS = [
    Path(__file__).resolve().parent.parent.parent / "crew-ops-advisor-dataset" / "data" / "weather.json",
    Path(__file__).resolve().parent.parent.parent / "data" / "weather.json",
]


def load_airport_metadata() -> Dict[str, Any]:
    """Loads hub airport metadata & METAR/TAF weather observations from weather.json."""
    for p in WEATHER_DATA_PATHS:
        if p.exists():
            try:
                with p.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and data:
                        return data
            except Exception as e:
                logger.warning(f"Failed loading weather data from {p}: {e}")
    logger.warning("No weather.json found in dataset directories; station weather will be empty.")
    return {}


AIRPORT_METADATA: Dict[str, Any] = load_airport_metadata()


@router.get("/stations/{station_code}", response_model=StationDetailResponse)
def get_station_details(
    station_code: str = FastApiPath(
        ...,
        pattern="^[A-Za-z]{3}$",
        description="3-letter IATA station code (e.g. BLR, DEL, BOM, HYD, MAA)",
        examples=["BLR"],
    )
) -> StationDetailResponse:
    """Returns deep-dive operational status for an airport hub: weather, forecasts, arrivals, departures."""
    code = station_code.upper()
    meta_all = load_airport_metadata() or AIRPORT_METADATA
    if code not in meta_all:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Station '{station_code}' not recognized.",
        )

    meta = meta_all[code]
    w_raw = meta["weather"]

    weather = WeatherCondition(
        metar_raw=w_raw["metar_raw"],
        taf_raw=w_raw["taf_raw"],
        flight_category=w_raw["flight_category"],
        temperature_c=w_raw["temperature_c"],
        dewpoint_c=w_raw["dewpoint_c"],
        wind_direction_deg=w_raw["wind_direction_deg"],
        wind_speed_kts=w_raw["wind_speed_kts"],
        wind_gusts_kts=w_raw["wind_gusts_kts"],
        visibility_m=w_raw["visibility_m"],
        altimeter_hpa=w_raw["altimeter_hpa"],
        clouds=w_raw["clouds"],
        runway_in_use=w_raw["runway_in_use"],
        crosswind_component_kts=w_raw["crosswind_component_kts"],
        advisory=w_raw["advisory"],
        forecast_periods=[WeatherForecastPeriod(**p) for p in w_raw["forecast_periods"]],
    )

    twin_view = twin_manager.state.materialize()
    flights = twin_manager.repo.list_flights()
    statuses = twin_view.flight_statuses
    deps = twin_view.flight_estimated_deps

    from datetime import timedelta
    from advisor.domain.timeutil import parse_utc

    departures: List[StationFlightMovement] = []
    arrivals: List[StationFlightMovement] = []
    total_pax = 0

    for fl in sorted(flights, key=lambda f: f.dep_utc):
        f_status = statuses.get(fl.flight_id, "ON_TIME")
        est_dep = deps.get(fl.flight_id, fl.dep_utc)

        gate_num = (abs(hash(fl.flight_id)) % 24) + 1
        gate_str = f"Gate {gate_num:02d}" if fl.aircraft_type == "A320" else f"Stand {gate_num:02d}"

        # Departure
        if fl.origin == code:
            delay_mins = 0
            if est_dep > fl.dep_utc:
                delay_mins = int((parse_utc(est_dep) - parse_utc(fl.dep_utc)).total_seconds() // 60)

            pax = fl.passengers or 0
            total_pax += pax

            departures.append(
                StationFlightMovement(
                    flight_id=fl.flight_id,
                    movement_type="DEPARTURE",
                    origin=fl.origin,
                    destination=fl.destination,
                    route=f"{fl.origin} ➔ {fl.destination}",
                    scheduled_utc=fl.dep_utc,
                    estimated_utc=est_dep,
                    status=f_status,
                    delay_minutes=delay_mins,
                    tail_id=fl.tail_id,
                    aircraft_type=fl.aircraft_type,
                    passengers=pax,
                    gate=gate_str,
                )
            )

        # Arrival
        if fl.destination == code:
            est_dep_dt = parse_utc(est_dep)
            est_arr_dt = est_dep_dt + timedelta(minutes=fl.block_minutes)
            est_arr_str = est_arr_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            delay_mins = 0
            if est_arr_str > fl.arr_utc:
                delay_mins = int((est_arr_dt - parse_utc(fl.arr_utc)).total_seconds() // 60)

            pax = fl.passengers or 0
            total_pax += pax

            arrivals.append(
                StationFlightMovement(
                    flight_id=fl.flight_id,
                    movement_type="ARRIVAL",
                    origin=fl.origin,
                    destination=fl.destination,
                    route=f"{fl.origin} ➔ {fl.destination}",
                    scheduled_utc=fl.arr_utc,
                    estimated_utc=est_arr_str,
                    status=f_status,
                    delay_minutes=delay_mins,
                    tail_id=fl.tail_id,
                    aircraft_type=fl.aircraft_type,
                    passengers=pax,
                    gate=gate_str,
                )
            )

    all_movements = departures + arrivals
    total_movements_cnt = len(all_movements)
    on_time_cnt = sum(1 for m in all_movements if m.status == "ON_TIME")
    on_time_pct = round((on_time_cnt / total_movements_cnt * 100) if total_movements_cnt > 0 else 100.0, 1)

    raw_reserves = twin_manager.repo.list_reserves(base=code, distinct_crew=True)

    return StationDetailResponse(
        station_code=code,
        airport_name=meta["airport_name"],
        city=meta["city"],
        icao_code=meta["icao"],
        elevation_ft=meta["elevation_ft"],
        runways=meta["runways"],
        weather=weather,
        total_movements=total_movements_cnt,
        departure_count=len(departures),
        arrival_count=len(arrivals),
        on_time_rate_pct=on_time_pct,
        total_passengers=total_pax,
        departures=departures,
        arrivals=arrivals,
        standby_reserves_count=len(raw_reserves),
    )


# -------------------------------------------------------------------------
# Workspace 2: Disruption Simulation & Recovery Options
# -------------------------------------------------------------------------

@router.post("/disruptions/simulate", response_model=DisruptionSimulateResponse)
async def simulate_disruption(req: DisruptionSimulateRequest) -> DisruptionSimulateResponse:
    """Executes the operational pipeline to simulate disruptions, check legality, and rank options."""
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Directive query cannot be empty.")

    request_id = str(uuid.uuid4())
    llm_client = StubClient() if req.offline_mode else None
    events = await asyncio.to_thread(
        lambda: list(
            orchestrate(
                query,
                twin_manager.state,
                twin_manager.repo,
                client=llm_client,
                request_id=request_id,
            )
        )
    )

    abstain_event = next((p for s, p in events if s == "abstain"), None)
    clarify_event = next((p for s, p in events if s == "clarify"), None)
    evidence_payload = next((p for s, p in events if s == "evidence"), {})
    options_payload = next((p for s, p in events if s == "options"), None)
    prose_payload = next((p for s, p in events if s == "prose"), None)

    if clarify_event:
        return DisruptionSimulateResponse(
            request_id=request_id,
            status="clarification_needed",
            query=query,
            abstained=True,
            abstain_reason="CLARIFICATION_REQUIRED",
            abstain_message=clarify_event.get("message", "Please clarify operational parameters."),
            prose_summary=f"❓ **Clarification Needed:** {clarify_event.get('message')}",
        )

    if abstain_event:
        return DisruptionSimulateResponse(
            request_id=request_id,
            status="abstained",
            query=query,
            abstained=True,
            abstain_reason=abstain_event.get("reason"),
            abstain_message=abstain_event.get("message"),
        )

    # Format options
    formatted_options: List[CandidateOptionSchema] = []
    if options_payload:
        for opt in options_payload:
            repair_schema = None
            if opt.repair:
                repair_schema = RepairOptionSchema(
                    lever=opt.repair.lever,
                    magnitude_minutes=opt.repair.magnitude_minutes,
                    repaired_rule=opt.repair.repaired_rule,
                    clears_binding=opt.repair.clears_binding,
                    side_effects=opt.repair.side_effects,
                )

            formatted_options.append(
                CandidateOptionSchema(
                    crew_id=opt.crew_id,
                    candidate_type=opt.candidate_type,
                    base=opt.base,
                    legal=opt.ledger.legal if opt.ledger else True,
                    cost=CostBreakdownSchema(
                        total_inr=opt.cost.total_inr,
                        line_items=opt.cost.line_items,
                    ),
                    repair=repair_schema,
                    deadhead_flight_id=opt.deadhead_flight_id,
                    expiry_utc=opt.expiry_utc,
                    source_rows=opt.source_rows,
                )
            )

    # Serialize twin view summary if present
    twin_view_dict = None
    if "twin_view" in evidence_payload:
        tv = evidence_payload["twin_view"]
        twin_view_dict = {
            "timestamp_utc": tv.timestamp_utc,
            "flight_statuses": tv.flight_statuses,
            "flight_estimated_deps": tv.flight_estimated_deps,
            "active_flights_count": len(tv.active_flights),
            "active_flights": {
                f_id: {
                    "flight_id": fl.flight_id,
                    "origin": fl.origin,
                    "destination": fl.destination,
                    "dep_utc": fl.dep_utc,
                    "arr_utc": fl.arr_utc,
                    "block_minutes": fl.block_minutes,
                    "aircraft_type": fl.aircraft_type,
                    "tail_id": fl.tail_id,
                    "rotation_id": fl.rotation_id,
                    "rotation_seq": fl.rotation_seq,
                    "passengers": fl.passengers,
                }
                for f_id, fl in tv.active_flights.items()
            },
        }

    # Format ledger dict if present
    ledger_dict = None
    if "ledger" in evidence_payload:
        led = evidence_payload["ledger"]
        ledger_dict = {
            "subject": led.subject,
            "legal": led.legal,
            "context": led.context,
            "breaches_count": len(led.breaches),
            "verdicts": [
                {
                    "rule_id": v.rule_id,
                    "headline": v.headline,
                    "passed": v.passed,
                    "margin": v.margin,
                    "arithmetic": v.arithmetic,
                    "inputs": v.inputs,
                    "source_rows": v.source_rows,
                    "assumption": v.assumption,
                }
                for v in led.verdicts
            ],
        }

    impact = evidence_payload.get("impact")
    uncrewed_ids = [f.flight_id for f in impact.uncrewed_flights] if impact else []

    return DisruptionSimulateResponse(
        request_id=request_id,
        status="success",
        query=query,
        abstained=False,
        disrupted_crew_id=evidence_payload.get("disrupted_crew_id"),
        broken_pairing_id=evidence_payload.get("broken_pairing_id"),
        uncrewed_flight_ids=uncrewed_ids,
        options=formatted_options,
        ledger=ledger_dict,
        twin_view=twin_view_dict,
        prose_summary=prose_payload,
    )


@router.post("/recommendations/finalize", response_model=FinalizeRecommendationResponse)
def finalize_recommendation(req: FinalizeRecommendationRequest) -> FinalizeRecommendationResponse:
    """Applies reassign overlay to operational digital twin and updates reserve status."""
    disrupted_id = req.disrupted_crew_id or "C-1042"
    overlay_ids = {o.overlay_id for o in twin_manager.state.overlays}

    curr_state = twin_manager.state

    # 1. Ensure sick overlay is present for disrupted crew
    if not any(o.kind == "sick" and o.payload.get("crew_id") == disrupted_id for o in curr_state.overlays):
        sick_ov = Overlay(
            overlay_id=f"ov-sick-{disrupted_id}-{uuid.uuid4().hex[:6]}",
            kind="sick",
            payload={"crew_id": disrupted_id, "date": "2026-09-15"},
            label=f"Sick Callout: {disrupted_id}",
        )
        curr_state = curr_state.apply(sick_ov)

    # 2. Add reassign overlay
    reassign_ov_id = f"ov-reassign-{req.crew_id}-{uuid.uuid4().hex[:6]}"
    reassign_ov = Overlay(
        overlay_id=reassign_ov_id,
        kind="reassign",
        payload={
            "replacement_crew_id": req.crew_id,
            "disrupted_crew_id": disrupted_id,
            "pairing_id": req.pairing_id,
            "flight_ids": req.flight_ids,
            "cost_inr": req.cost_inr,
            "candidate_type": req.candidate_type,
            "delay_minutes": req.delay_minutes,
            "delayed_flight_id": req.delayed_flight_id,
        },
        label=f"Reassigned {req.crew_id} to {req.pairing_id} (Cost: ₹{int(req.cost_inr):,})",
    )
    curr_state = curr_state.apply(reassign_ov)
    twin_manager.state = curr_state

    now_str = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    twin_manager.action_history.insert(0, {
        "timestamp": now_str,
        "action": "FINALIZED_RECOMMENDATION",
        "description": f"Adopted {req.crew_id} for Pairing {req.pairing_id} (Cost: ₹{int(req.cost_inr):,})",
    })

    request_id = str(uuid.uuid4())
    append_audit_event(
        "REASSIGNMENT_COMMITTED",
        {
            "crew_id": req.crew_id,
            "pairing_id": req.pairing_id,
            "cost_inr": req.cost_inr,
            "overlay_id": reassign_ov_id,
        },
        request_id=request_id,
    )

    return FinalizeRecommendationResponse(
        request_id=request_id,
        success=True,
        message=f"Successfully finalized recommendation for {req.crew_id} on pairing {req.pairing_id}",
        finalized_overlay_id=reassign_ov_id,
        active_overlays_count=len(curr_state.overlays),
        dispatched_crew_id=req.crew_id,
        pairing_id=req.pairing_id,
        timestamp=now_str,
    )


# -------------------------------------------------------------------------
# Workspace 3: Standby & Reserves Roster
# -------------------------------------------------------------------------

@router.get("/reserves", response_model=ReserveListResponse)
def list_reserves(
    station: str = Query("BLR", description="Hub base airport code (BLR, DEL, BOM, HYD, MAA)"),
    rank: Optional[str] = Query(None, description="Filter by rank: Captain or First Officer"),
    available_only: bool = Query(False, description="Filter only currently available standby crew"),
) -> ReserveListResponse:
    """Returns standby crew members at a given station reflecting live twin overlay states."""
    twin_view = twin_manager.state.materialize()
    raw_reserves = twin_manager.repo.list_reserves(base=station.upper(), date="2026-09-15")
    if not raw_reserves:
        raw_reserves = twin_manager.repo.list_reserves(base=station.upper(), distinct_crew=True)

    results: List[ReserveItemSchema] = []
    available_count = 0
    seen_cids = set()

    for r in raw_reserves:
        if r.crew_id in seen_cids:
            continue
        seen_cids.add(r.crew_id)

        c = twin_manager.repo.get_crew(r.crew_id)
        if not c:
            continue

        if rank and c.rank.lower() != rank.lower():
            continue

        ratings = twin_manager.repo.list_ratings(r.crew_id)
        clk = twin_manager.repo.get_duty_clock(r.crew_id)
        duty_7d = clk.duty_hours_7d if clk else 0.0

        # Live overlay reflection
        twin_crew = twin_view.crew.get(r.crew_id)
        current_status = r.standby_status
        if twin_crew:
            if twin_crew.is_incapacitated:
                current_status = "INCAPACITATED"
            elif twin_crew.assigned_pairing_id:
                current_status = f"CALLED ({twin_crew.assigned_pairing_id})"

        is_avail = current_status == "AVAILABLE"
        if is_avail:
            available_count += 1

        if available_only and not is_avail:
            continue

        results.append(
            ReserveItemSchema(
                crew_id=c.crew_id,
                name=c.name,
                rank=c.rank,
                base=r.base,
                ratings=ratings,
                oncall_start_utc=r.oncall_start_utc,
                oncall_end_utc=r.oncall_end_utc,
                standby_status=current_status,
                reachability_minutes=c.reachability_minutes or 45,
                duty_hours_7d=duty_7d,
            )
        )

    return ReserveListResponse(
        station=station.upper(),
        total_count=len(results),
        available_count=available_count,
        reserves=results,
    )


# -------------------------------------------------------------------------
# Workspace 4: Fleet Rotations & Manifest
# -------------------------------------------------------------------------

@router.get("/fleet/rotations", response_model=FleetRotationsResponse)
def get_fleet_rotations() -> FleetRotationsResponse:
    """Returns aircraft tail rotations and complete flight manifest with live statuses."""
    twin_view = twin_manager.state.materialize()
    flights = twin_manager.repo.list_flights()
    statuses = twin_view.flight_statuses
    deps = twin_view.flight_estimated_deps

    tail_groups: Dict[str, List[FlightManifestItem]] = {}
    manifest_items: List[FlightManifestItem] = []

    for fl in sorted(flights, key=lambda f: f.dep_utc):
        tail_id = fl.tail_id or "UNASSIGNED"
        est_dep = deps.get(fl.flight_id, fl.dep_utc)
        curr_status = statuses.get(fl.flight_id, "ON_TIME")

        item = FlightManifestItem(
            flight_id=fl.flight_id,
            origin=fl.origin,
            destination=fl.destination,
            dep_utc=est_dep,
            arr_utc=fl.arr_utc,
            block_minutes=fl.block_minutes,
            aircraft_type=fl.aircraft_type,
            tail_id=tail_id,
            rotation_id=fl.rotation_id,
            rotation_seq=fl.rotation_seq,
            passengers=fl.passengers,
            status=curr_status,
        )
        manifest_items.append(item)

        if tail_id not in tail_groups:
            tail_groups[tail_id] = []
        tail_groups[tail_id].append(item)

    tail_rotations: List[TailRotation] = [
        TailRotation(
            tail_id=t_id,
            aircraft_type=fl_list[0].aircraft_type if fl_list else "A320",
            flight_count=len(fl_list),
            flights=fl_list,
        )
        for t_id, fl_list in sorted(tail_groups.items())
    ]

    return FleetRotationsResponse(
        active_tails_count=len(tail_rotations),
        total_flights_count=len(manifest_items),
        tails=tail_rotations,
        manifest=manifest_items,
    )


# -------------------------------------------------------------------------
# Digital Twin State & History Controls
# -------------------------------------------------------------------------

@router.get("/twin/state", response_model=TwinStateResponse)
def get_twin_state() -> TwinStateResponse:
    """Returns active overlays and last actions recorded in memory."""
    summaries = [
        OverlaySummary(
            overlay_id=ov.overlay_id,
            kind=ov.kind,
            label=ov.label,
            created_utc=ov.created_utc,
            payload=ov.payload,
        )
        for ov in twin_manager.state.overlays
    ]
    last_act = twin_manager.action_history[0]["description"] if twin_manager.action_history else None
    return TwinStateResponse(
        active_overlays_count=len(summaries),
        overlays=summaries,
        last_action=last_act,
    )


@router.post("/twin/undo", response_model=TwinActionResponse)
def undo_overlay() -> TwinActionResponse:
    """Pops the top overlay from the digital twin stack."""
    popped = twin_manager.undo()
    now_str = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    if not popped:
        return TwinActionResponse(
            success=False,
            action="UNDO",
            message="Overlay stack is already at clean baseline. Nothing to undo.",
            active_overlays_count=0,
            timestamp=now_str,
        )

    return TwinActionResponse(
        success=True,
        action="UNDO",
        message=f"Reverted top overlay: {popped.label}",
        active_overlays_count=len(twin_manager.state.overlays),
        timestamp=now_str,
    )


@router.post("/twin/reset", response_model=TwinActionResponse)
def reset_baseline() -> TwinActionResponse:
    """Resets digital twin baseline by purging all overlays and re-materializing 06:00Z state."""
    twin_manager.reset(force_rebuild=False)
    now_str = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    return TwinActionResponse(
        success=True,
        action="RESET",
        message="Digital twin baseline successfully re-materialized from repository.",
        active_overlays_count=0,
        timestamp=now_str,
    )


# -------------------------------------------------------------------------
# Voice Agent (Sarvam AI STT & TTS)
# -------------------------------------------------------------------------

from pydantic import BaseModel


class VoiceSynthesizeRequest(BaseModel):
    text: str
    speaker: str = "meera"
    language_code: str = "en-IN"


class VoiceSynthesizeResponse(BaseModel):
    success: bool
    audio_base64: str
    speaker: str
    message: str


@router.post("/voice/synthesize", response_model=VoiceSynthesizeResponse)
def synthesize_voice(req: VoiceSynthesizeRequest) -> VoiceSynthesizeResponse:
    """Converts operational text into natural spoken audio using Sarvam AI Bulbul TTS."""
    import base64
    from advisor.voice.sarvam import get_sarvam_client

    client = get_sarvam_client()
    if not client.is_configured():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sarvam AI API key is not configured. Please set SARVAM_API_KEY.",
        )
    try:
        wav_bytes = client.synthesize(
            req.text, speaker=req.speaker, target_language_code=req.language_code
        )
        b64_str = base64.b64encode(wav_bytes).decode("utf-8")
        return VoiceSynthesizeResponse(
            success=True,
            audio_base64=b64_str,
            speaker=req.speaker,
            message="Speech synthesized successfully via Sarvam AI.",
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

