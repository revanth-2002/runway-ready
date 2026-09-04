from typing import Any, Dict, List, Optional
from advisor.audit.logger import StructuredLogger
from advisor.data.repository import OpsRepository
from advisor.domain.evidence import ImpactReport
from advisor.domain.state import Overlay
from advisor.domain.types import Crew, Flight
from advisor.twin.view import DigitalTwinState

logger = StructuredLogger("advisor.twin.diff")



def compute_twin_diff(
    baseline: DigitalTwinState,
    shadow: DigitalTwinState,
    overlay: Overlay,
    repo: OpsRepository,
) -> ImpactReport:
    """Compares baseline and shadow twin views to calculate operational consequences."""
    disrupted_crew_id = overlay.payload.get("crew_id", "UNKNOWN")
    disruption_id = overlay.overlay_id

    # 1. Broken pairing
    broken_pairing_id = ""
    disrupted_crew_state = shadow.crew.get(disrupted_crew_id)
    if disrupted_crew_state and disrupted_crew_state.assigned_pairing_id:
        broken_pairing_id = disrupted_crew_state.assigned_pairing_id
    elif "pairing_id" in overlay.payload:
        broken_pairing_id = overlay.payload["pairing_id"]

    # 2. Uncrewed flights
    uncrewed_flights: List[Flight] = []
    for f_id, status in shadow.flight_statuses.items():
        if status == "UNCREWED" and baseline.flight_statuses.get(f_id) != "UNCREWED":
            fl = shadow.active_flights.get(f_id)
            if fl:
                uncrewed_flights.append(fl)

    # Sort uncrewed flights chronologically
    uncrewed_flights.sort(key=lambda f: f.dep_utc)

    # 3. Delayed rotations
    delayed_rotations: List[Dict[str, Any]] = []
    for f_id, shadow_dep in shadow.flight_estimated_deps.items():
        base_dep = baseline.flight_estimated_deps.get(f_id)
        if base_dep and shadow_dep > base_dep:
            fl = shadow.active_flights.get(f_id)
            delayed_rotations.append({
                "flight_id": f_id,
                "scheduled_dep_utc": base_dep,
                "estimated_dep_utc": shadow_dep,
                "tail_id": fl.tail_id if fl else None,
                "rotation_id": fl.rotation_id if fl else None,
            })

    # 4. Stranded companion crew
    stranded_companions: List[Crew] = []
    if broken_pairing_id:
        companions = repo.get_companion_crew(broken_pairing_id, exclude_crew_id=disrupted_crew_id)
        stranded_companions.extend(companions)

    # 5. Passengers affected
    total_pax = sum(f.passengers or 0 for f in uncrewed_flights)
    for d in delayed_rotations:
        fl = shadow.active_flights.get(d["flight_id"])
        if fl and fl not in uncrewed_flights:
            total_pax += (fl.passengers or 0)

    # 6. Source rows for auditability
    source_rows = [f"crew:{disrupted_crew_id}"]
    if broken_pairing_id:
        source_rows.append(f"pairing:{broken_pairing_id}")
    for f in uncrewed_flights:
        source_rows.append(f"flight:{f.flight_id}")
    for c in stranded_companions:
        source_rows.append(f"crew:{c.crew_id}:companion")

    report = ImpactReport(
        disruption_id=disruption_id,
        disrupted_crew_id=disrupted_crew_id,
        broken_pairing_id=broken_pairing_id,
        uncrewed_flights=tuple(uncrewed_flights),
        delayed_rotations=tuple(delayed_rotations),
        stranded_companions=tuple(stranded_companions),
        passengers_affected=total_pax,
        source_rows=source_rows,
        confidence="HIGH",
    )
    logger.info(
        "Computed operational twin diff impact",
        disruption_id=disruption_id,
        disrupted_crew_id=disrupted_crew_id,
        uncrewed_count=len(uncrewed_flights),
        passengers_affected=total_pax,
        broken_pairing_id=broken_pairing_id,
    )
    return report
