from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from advisor.audit.logger import StructuredLogger
from advisor.data.repository import OpsRepository
from advisor.domain.state import Overlay
from advisor.domain.types import Crew, Flight, Pairing

logger = StructuredLogger("advisor.twin.view")



@dataclass(frozen=True)
class AircraftTailState:
    tail_id: str
    current_station: str
    available_at_utc: str
    active_rotation_id: Optional[str]
    rotation_sequence: int


@dataclass(frozen=True)
class CrewMemberState:
    crew_id: str
    base: str
    rank: str
    current_station: str
    last_duty_end_utc: Optional[str]
    cumulative_duty_7d: float
    cumulative_flight_28d: float
    on_call_status: Optional[str]
    assigned_pairing_id: Optional[str]
    is_incapacitated: bool = False


@dataclass(frozen=True)
class DigitalTwinState:
    timestamp_utc: str
    tails: Dict[str, AircraftTailState]
    crew: Dict[str, CrewMemberState]
    active_flights: Dict[str, Flight]
    active_pairings: Dict[str, Pairing]
    flight_statuses: Dict[str, str]  # "ON_TIME", "UNCREWED", "DELAYED", "CANCELLED"
    flight_estimated_deps: Dict[str, str]


def build_digital_twin_view(
    db_path: Path, overlays: Tuple[Overlay, ...]
) -> DigitalTwinState:
    """Projects base SQLite records through active overlays into an in-memory twin view."""
    repo = OpsRepository(db_path)
    all_crew = repo.list_all_crew()
    all_flights = repo.list_flights()
    logger.debug(
        "Building digital twin view",
        crew_count=len(all_crew),
        flight_count=len(all_flights),
        overlay_count=len(overlays),
    )


    # Base crew map
    crew_map: Dict[str, CrewMemberState] = {}
    for c in all_crew:
        clk = repo.get_duty_clock(c.crew_id)
        assigned_pairing = repo.get_pairing_for_crew(c.crew_id, at_utc="2026-09-15T00:00:00Z")
        crew_map[c.crew_id] = CrewMemberState(
            crew_id=c.crew_id,
            base=c.base,
            rank=c.rank,
            current_station=c.base,
            last_duty_end_utc=clk.last_rest_ended if clk else None,
            cumulative_duty_7d=clk.duty_hours_7d if clk else 0.0,
            cumulative_flight_28d=clk.flight_hours_28d if clk else 0.0,
            on_call_status=None,
            assigned_pairing_id=assigned_pairing.pairing_id if assigned_pairing else None,
            is_incapacitated=False,
        )

    # Base tails map
    tail_map: Dict[str, AircraftTailState] = {}
    flight_map: Dict[str, Flight] = {f.flight_id: f for f in all_flights}
    pairing_map: Dict[str, Pairing] = {}
    flight_statuses: Dict[str, str] = {f.flight_id: "ON_TIME" for f in all_flights}
    flight_estimated_deps: Dict[str, str] = {f.flight_id: f.dep_utc for f in all_flights}

    for f in all_flights:
        if f.tail_id and f.tail_id not in tail_map:
            tail_map[f.tail_id] = AircraftTailState(
                tail_id=f.tail_id,
                current_station=f.origin,
                available_at_utc=f.dep_utc,
                active_rotation_id=f.rotation_id,
                rotation_sequence=f.rotation_seq or 1,
            )

    # Active pairings
    for c in all_crew:
        p = repo.get_pairing_for_crew(c.crew_id, at_utc="2026-09-15T00:00:00Z")
        if p and p.pairing_id not in pairing_map:
            pairing_map[p.pairing_id] = p

    # Apply overlays
    for ov in overlays:
        if ov.kind == "sick":
            crew_id = ov.payload.get("crew_id")
            if crew_id in crew_map:
                cur = crew_map[crew_id]
                crew_map[crew_id] = CrewMemberState(
                    crew_id=cur.crew_id,
                    base=cur.base,
                    rank=cur.rank,
                    current_station=cur.current_station,
                    last_duty_end_utc=cur.last_duty_end_utc,
                    cumulative_duty_7d=cur.cumulative_duty_7d,
                    cumulative_flight_28d=cur.cumulative_flight_28d,
                    on_call_status=cur.on_call_status,
                    assigned_pairing_id=cur.assigned_pairing_id,
                    is_incapacitated=True,
                )
        elif ov.kind == "delay":
            flight_id = ov.payload.get("flight_id")
            new_dep = ov.payload.get("new_dep_utc")
            if flight_id in flight_map and new_dep:
                flight_statuses[flight_id] = "DELAYED"
                flight_estimated_deps[flight_id] = new_dep
        elif ov.kind == "cancel":
            flight_id = ov.payload.get("flight_id")
            if flight_id in flight_map:
                flight_statuses[flight_id] = "CANCELLED"

    from advisor.twin.ripple import propagate_ripples
    return propagate_ripples(
        DigitalTwinState(
            timestamp_utc="2026-09-15T06:00:00Z",
            tails=tail_map,
            crew=crew_map,
            active_flights=flight_map,
            active_pairings=pairing_map,
            flight_statuses=flight_statuses,
            flight_estimated_deps=flight_estimated_deps,
        ),
        overlays,
        repo,
    )
