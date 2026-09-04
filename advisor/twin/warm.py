"""Operational Digital Twin Pre-Warming Engine.

Pre-materializes fleet rotations, crew clocks, and station reserves into memory
at server startup to guarantee zero cold-start latency for operations controllers.
"""

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from advisor.audit.logger import StructuredLogger
from advisor.data.ingest import build_database, DEFAULT_DB_PATH
from advisor.data.repository import OpsRepository
from advisor.domain.state import OpsState
from advisor.twin.view import DigitalTwinState

logger = StructuredLogger("advisor.twin.warm")

DEFAULT_STATIONS = ["BLR", "DEL", "BOM", "HYD", "MAA"]


def verify_database_health(db_path: Path) -> bool:
    """Checks if the SQLite operations database exists and satisfies schema integrity."""
    db_path = Path(db_path)
    if not db_path.exists() or db_path.stat().st_size == 0:
        logger.warning("Database file missing or zero-sized", db_path=str(db_path))
        return False

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # 1. SQLite low-level integrity verification
        cursor.execute("PRAGMA integrity_check;")
        integrity = cursor.fetchone()
        if not integrity or integrity[0] != "ok":
            logger.warning("Integrity check failed", db_path=str(db_path), result=integrity)
            conn.close()
            return False

        # 2. Verify all essential tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cursor.fetchall()}
        required_tables = {"crew", "flight", "pairing", "pairing_leg", "reserve", "duty_clock"}
        if not required_tables.issubset(tables):
            missing = required_tables - tables
            logger.warning("Database missing essential operational tables", missing=list(missing))
            conn.close()
            return False

        # 3. Verify non-zero flight and crew records
        cursor.execute("SELECT COUNT(*) FROM flight;")
        flight_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM crew;")
        crew_count = cursor.fetchone()[0]
        conn.close()

        if flight_count == 0 or crew_count == 0:
            logger.warning("Database has empty tables", flights=flight_count, crew=crew_count)
            return False

        return True

    except Exception as exc:
        logger.warning("Database health check raised exception", error=str(exc), db_path=str(db_path))
        return False


def warm_operational_digital_twin(
    db_path: Path = DEFAULT_DB_PATH,
    force_rebuild: bool = False,
    stations: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Warms the Operational Digital Twin on server startup.

    1. Validates SQLite database existence and referential integrity.
    2. Automatically builds or repairs the database if missing or corrupt.
    3. Initializes OpsRepository and OpsState.
    4. Pre-materializes DigitalTwinState (tails VT-DXA..VT-DXF, active flights,
       crew clocks, pairings, rotations, and ripple graph).
    5. Pre-warms reserve rosters and ratings across all hub stations.
    6. Returns operational handles and system health telemetry.
    """
    start_time = time.perf_counter()
    db_path = Path(db_path)
    logger.info("Warming Operational Digital Twin...", db_path=str(db_path), force_rebuild=force_rebuild)

    # 1. Health check and auto-repair
    if force_rebuild or not verify_database_health(db_path):
        logger.info("Initializing fresh operational database from dataset...", db_path=str(db_path))
        build_database(db_path=db_path)

    # 2. Instantiate repository and operational state
    repo = OpsRepository(db_path)
    state = OpsState(db_path=db_path)

    # 3. Materialize baseline digital twin view
    baseline_twin: DigitalTwinState = state.materialize()

    # 4. Pre-warm station reserve rosters
    target_stations = stations or DEFAULT_STATIONS
    station_reserves: Dict[str, List[Dict[str, Any]]] = {}
    total_reserves_warmed = 0

    for stn in target_stations:
        raw_reserves = repo.list_reserves(base=stn)
        stn_list: List[Dict[str, Any]] = []
        for r in raw_reserves:
            c = repo.get_crew(r.crew_id)
            ratings = repo.list_ratings(r.crew_id)
            clk = repo.get_duty_clock(r.crew_id)
            duty_7d = clk.duty_hours_7d if clk else 0.0
            stn_list.append({
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
        station_reserves[stn] = stn_list
        total_reserves_warmed += len(stn_list)

    # 5. Pre-load flights and all crew
    all_flights = repo.list_flights()
    all_crew = repo.list_all_crew()

    warm_latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
    logger.info(
        "Operational Digital Twin successfully warmed",
        warm_latency_ms=warm_latency_ms,
        tails=len(baseline_twin.tails),
        flights=len(all_flights),
        crew=len(all_crew),
        reserves_warmed=total_reserves_warmed,
    )

    return {
        "status": "WARMED",
        "db_path": db_path,
        "state": state,
        "repo": repo,
        "baseline_twin": baseline_twin,
        "tail_count": len(baseline_twin.tails),
        "flight_count": len(all_flights),
        "crew_count": len(all_crew),
        "pairing_count": len(baseline_twin.active_pairings),
        "station_reserves": station_reserves,
        "warmed_stations": target_stations,
        "warm_latency_ms": warm_latency_ms,
        "warmed_at_utc": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    result = warm_operational_digital_twin()
    print(
        f"✅ Digital Twin Warmed: {result['tail_count']} Tails, "
        f"{result['flight_count']} Flights, {result['crew_count']} Crew in {result['warm_latency_ms']}ms"
    )
