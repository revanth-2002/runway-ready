from datetime import datetime, timezone, timedelta
from typing import List, Optional
from advisor.audit.logger import StructuredLogger
from advisor.data.repository import OpsRepository
from advisor.domain.timeutil import parse_utc
from advisor.domain.types import Flight

logger = StructuredLogger("advisor.reasoning.deadhead")



def find_feasible_deadheads(
    repo: OpsRepository,
    from_base: str,
    to_station: str,
    latest_arrival_utc: str,
    earliest_dep_utc: str,
    buffer_minutes: int = 30,
) -> List[Flight]:
    """Finds scheduled passenger flights connecting from_base to to_station.
    Must depart after earliest_dep_utc and arrive at least buffer_minutes before latest_arrival_utc.
    """
    candidates = repo.list_flights(origin=from_base, destination=to_station)
    feasible = []

    earliest_dt = parse_utc(earliest_dep_utc)
    latest_dt = parse_utc(latest_arrival_utc)

    for fl in candidates:
        dep_dt = parse_utc(fl.dep_utc)
        arr_dt = parse_utc(fl.arr_utc)

        # Check departure after crew readiness
        if dep_dt < earliest_dt:
            continue

        # Check arrival + turnaround buffer before report
        if (arr_dt + timedelta(minutes=buffer_minutes)) <= latest_dt:
            feasible.append(fl)

    # Sort by arrival time (closest to report time is best)
    feasible.sort(key=lambda f: f.arr_utc, reverse=True)
    logger.debug(
        "Searched feasible deadheads",
        from_base=from_base,
        to_station=to_station,
        feasible_count=len(feasible),
    )
    return feasible

