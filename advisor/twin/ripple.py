"""Forward ripple event propagation engine for Operational Digital Twin."""

from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple, TYPE_CHECKING
from advisor.audit.logger import StructuredLogger
from advisor.domain.state import Overlay
from advisor.domain.timeutil import parse_utc, format_utc

if TYPE_CHECKING:
    from advisor.data.repository import OpsRepository
    from advisor.twin.view import DigitalTwinState

logger = StructuredLogger("advisor.twin.ripple")



def propagate_ripples(
    state: "DigitalTwinState",
    overlays: Tuple[Overlay, ...],
    repo: "OpsRepository",
) -> "DigitalTwinState":
    """Propagates pairing breakage, companion stranding, and tail delay cascades."""
    from advisor.twin.view import DigitalTwinState, AircraftTailState

    flight_statuses = dict(state.flight_statuses)
    flight_estimated_deps = dict(state.flight_estimated_deps)
    tail_map = dict(state.tails)

    # 1. Crew Disruption Ripple: Broken pairings and uncrewed legs
    broken_pairings = set()
    for crew_id, crew_state in state.crew.items():
        if crew_state.is_incapacitated and crew_state.assigned_pairing_id:
            broken_pairings.add(crew_state.assigned_pairing_id)

    uncrewed_flight_ids = set()
    for p_id in broken_pairings:
        pairing = state.active_pairings.get(p_id)
        if pairing:
            for leg in pairing.legs:
                flight_statuses[leg.flight_id] = "UNCREWED"
                uncrewed_flight_ids.add(leg.flight_id)

    logger.info(
        "Propagated twin ripples",
        broken_pairings_count=len(broken_pairings),
        uncrewed_flights_count=len(uncrewed_flight_ids),
    )

    # 2. Tail Rotation Delay Propagation
    # Sort flights by rotation_id and rotation_seq
    rotation_flights: Dict[str, List] = {}
    for f in state.active_flights.values():
        if f.rotation_id:
            rotation_flights.setdefault(f.rotation_id, []).append(f)

    for rot_id, legs in rotation_flights.items():
        sorted_legs = sorted(legs, key=lambda l: l.rotation_seq or 0)
        for i in range(len(sorted_legs) - 1):
            curr_leg = sorted_legs[i]
            next_leg = sorted_legs[i + 1]

            # If current leg is delayed, propagate to next leg
            if flight_statuses.get(curr_leg.flight_id) in ("DELAYED", "UNCREWED"):
                curr_dep = parse_utc(flight_estimated_deps[curr_leg.flight_id])
                curr_arr = curr_dep + timedelta(minutes=curr_leg.block_minutes)

                # 30-minute standard turnaround buffer
                min_next_dep = curr_arr + timedelta(minutes=30)
                sched_next_dep = parse_utc(next_leg.dep_utc)

                if min_next_dep > sched_next_dep:
                    flight_estimated_deps[next_leg.flight_id] = format_utc(min_next_dep)
                    if flight_statuses.get(next_leg.flight_id) == "ON_TIME":
                        flight_statuses[next_leg.flight_id] = "DELAYED"

                # Update physical tail availability
                if curr_leg.tail_id and curr_leg.tail_id in tail_map:
                    tail_map[curr_leg.tail_id] = AircraftTailState(
                        tail_id=curr_leg.tail_id,
                        current_station=curr_leg.destination,
                        available_at_utc=format_utc(min_next_dep),
                        active_rotation_id=rot_id,
                        rotation_sequence=(
                            next_leg.rotation_seq
                            or ((curr_leg.rotation_seq + 1) if curr_leg.rotation_seq is not None else (i + 2))
                        ),
                    )

    return DigitalTwinState(
        timestamp_utc=state.timestamp_utc,
        tails=tail_map,
        crew=state.crew,
        active_flights=state.active_flights,
        active_pairings=state.active_pairings,
        flight_statuses=flight_statuses,
        flight_estimated_deps=flight_estimated_deps,
    )
