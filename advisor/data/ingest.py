"""Ingest engine for Crew Ops Advisor.
Builds SQLite ops.db from raw JSON files with soft dual-clock reconciliation.
Supports both official dCortex dataset and synthetic baseline datasets.
"""

import json
import sqlite3
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from advisor.audit.logger import StructuredLogger
from advisor.domain.exceptions import DataIngestionError

logger = StructuredLogger("advisor.data.ingest")

OFFICIAL_RAW_DIR = Path(__file__).resolve().parent.parent.parent / "crew-ops-advisor-dataset" / "data"
DEFAULT_RAW_DIR = OFFICIAL_RAW_DIR
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "ops.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"



def load_json(filepath: Path) -> Any:
    if not filepath.exists():
        raise DataIngestionError(f"Raw data file missing: {filepath}")
    with filepath.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_database(
    raw_dir: Path = DEFAULT_RAW_DIR,
    db_path: Path = DEFAULT_DB_PATH,
    schema_path: Path = SCHEMA_PATH,
) -> Tuple[sqlite3.Connection, str]:
    """Rebuilds the SQLite database from raw JSON files with referential validation."""
    raw_dir = Path(raw_dir)
    db_path = Path(db_path)
    schema_path = Path(schema_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")

    # Apply schema
    with schema_path.open("r", encoding="utf-8") as f:
        conn.executescript(f.read())

    logger.info("Database schema applied successfully", db_path=str(db_path))

    if (raw_dir / "rosters.json").exists():
        _ingest_official_dataset(conn, raw_dir)
    else:
        _ingest_synthetic_dataset(conn, raw_dir)

    # Validate rotation continuity
    validate_rotation_continuity(conn)

    # Soft reconciliation
    clock_mode = reconcile_duty_clocks(conn)

    conn.commit()
    logger.info("Database ingestion complete", tables_loaded=12, clock_mode=clock_mode)
    return conn, clock_mode


def _ingest_official_dataset(conn: sqlite3.Connection, raw_dir: Path) -> None:
    """Ingests the official dCortex 150-crew / 147-flight dataset."""
    # 1. Crew & Ratings
    crew_list = load_json(raw_dir / "crew.json")
    for c in crew_list:
        conn.execute(
            """INSERT INTO crew (crew_id, name, rank, base, seniority, reachability_minutes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                c["crew_id"],
                c["name"],
                c["rank"],
                c["base"],
                c.get("seniority"),
                c.get("reachability_minutes"),
            ),
        )
        for rating in c.get("ratings", []):
            conn.execute(
                "INSERT OR IGNORE INTO crew_rating (crew_id, aircraft_type) VALUES (?, ?)",
                (c["crew_id"], rating),
            )

    # 2. Flights
    flights = load_json(raw_dir / "flights.json")
    fby: Dict[str, Dict[str, Any]] = {}
    for fl in flights:
        fby[fl["flight_id"]] = fl
        block_min = int(round(fl["block_hours"] * 60)) if "block_hours" in fl else fl.get("block_minutes", 0)
        conn.execute(
            """INSERT INTO flight (flight_id, flight_no, origin, destination, dep_utc, arr_utc,
                                  block_minutes, aircraft_type, tail_id, rotation_id,
                                  rotation_seq, passengers)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fl["flight_id"],
                fl.get("flight_no"),
                fl.get("dep_station") or fl.get("origin"),
                fl.get("arr_station") or fl.get("destination"),
                fl["dep_utc"],
                fl["arr_utc"],
                block_min,
                fl["aircraft_type"],
                fl.get("aircraft") or fl.get("tail_id"),
                fl.get("aircraft") or fl.get("rotation_id"),
                None,
                fl.get("seats") or fl.get("passengers", 162),
            ),
        )

    # 3. Rosters, Pairings, Assignments & Duties
    rosters = load_json(raw_dir / "rosters.json")
    for p in rosters.get("pairings", []):
        pid = p["pairing_id"]
        days = p.get("days", [])
        crew_members = p.get("crew", [])

        # Start and end
        rep_utcs = [d["report_utc"] for d in days if "report_utc" in d]
        rel_utcs = [d["release_utc"] for d in days if "release_utc" in d]
        start_utc = min(rep_utcs) if rep_utcs else ""
        end_utc = max(rel_utcs) if rel_utcs else ""

        first_leg_origin = "BLR"
        if days and days[0].get("flights"):
            first_fid = days[0]["flights"][0]
            if first_fid in fby:
                first_leg_origin = fby[first_fid].get("dep_station", "BLR")

        conn.execute(
            "INSERT INTO pairing (pairing_id, base, start_utc, end_utc) VALUES (?, ?, ?, ?)",
            (pid, first_leg_origin, start_utc, end_utc),
        )

        leg_seq = 1
        for day_idx, day in enumerate(days):
            day_tag = f"D{day_idx + 1}"
            duty_tag = f"{pid}-{day_tag}"

            rep_utc = day["report_utc"]
            rel_utc = day["release_utc"]
            dt_start = datetime.fromisoformat(rep_utc.replace("Z", "+00:00"))
            dt_end = datetime.fromisoformat(rel_utc.replace("Z", "+00:00"))
            duty_mins = int((dt_end - dt_start).total_seconds() // 60)
            day_flights = day.get("flights", [])
            sectors = len(day_flights)
            block_mins = sum(
                int(round(fby[fid]["block_hours"] * 60))
                for fid in day_flights
                if fid in fby and "block_hours" in fby[fid]
            )

            for fid in day_flights:
                conn.execute(
                    """INSERT INTO pairing_leg (pairing_id, leg_seq, flight_id, duty_id)
                       VALUES (?, ?, ?, ?)""",
                    (pid, leg_seq, fid, duty_tag),
                )
                leg_seq += 1

            for cm in crew_members:
                cid = cm["crew_id"]
                duty_id = f"{cid}-{pid}-{day_tag}"
                conn.execute(
                    """INSERT OR REPLACE INTO duty (duty_id, pairing_id, crew_id, start_utc,
                                                   end_utc, duty_minutes, block_minutes, sectors)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (duty_id, pid, cid, rep_utc, rel_utc, duty_mins, block_mins, sectors),
                )

        for cm in crew_members:
            conn.execute(
                "INSERT OR IGNORE INTO assignment (crew_id, pairing_id, role) VALUES (?, ?, ?)",
                (cm["crew_id"], pid, cm["role"]),
            )

    # 4. Duty Clocks
    clock_file = raw_dir / "duty_clocks.json"
    if clock_file.exists():
        clocks = load_json(clock_file)
        for dc in clocks:
            history_json = json.dumps(dc.get("daily_history", []))
            conn.execute(
                """INSERT INTO duty_clock (crew_id, duty_hours_7d, flight_hours_28d, last_rest_ended, daily_history)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    dc["crew_id"],
                    dc.get("duty_hours_7d"),
                    dc.get("flight_hours_28d"),
                    dc.get("last_rest_ended"),
                    history_json,
                ),
            )

    # 5. Reserve Pool
    reserve_file = raw_dir / "reserve_pool.json"
    if reserve_file.exists():
        reserves = load_json(reserve_file)
        for res in reserves:
            cid = res["crew_id"]
            base = res["base"]
            win = res.get("oncall_window_utc", {"start": "00:00", "end": "23:59"})
            w_start = win.get("start", "00:00")
            w_end = win.get("end", "23:59")
            for dt in res.get("dates", []):
                start_iso = f"{dt}T{w_start}:00Z"
                end_iso = f"{dt}T{w_end}:00Z"
                conn.execute(
                    """INSERT OR REPLACE INTO reserve (crew_id, base, oncall_start_utc, oncall_end_utc, standby_status)
                       VALUES (?, ?, ?, ?, ?)""",
                    (cid, base, start_iso, end_iso, "STANDBY"),
                )

    # 6. Certifications
    certs = load_json(raw_dir / "certifications.json")
    for cert in certs:
        v_to = cert.get("valid_to") or cert.get("expires_on")
        conn.execute(
            """INSERT OR REPLACE INTO certification (crew_id, cert_type, valid_from, expires_on)
               VALUES (?, ?, ?, ?)""",
            (cert["crew_id"], cert["cert_type"], cert.get("valid_from"), v_to),
        )

    # 7. Costs
    costs_raw = load_json(raw_dir / "costs.json")
    if isinstance(costs_raw, dict):
        currency = costs_raw.get("currency", "INR")
        for k, v in costs_raw.items():
            if isinstance(v, (int, float)):
                conn.execute(
                    "INSERT OR REPLACE INTO cost_rate (key, value, unit) VALUES (?, ?, ?)",
                    (k, float(v), currency),
                )
        aliases = {
            "reserve_callout": float(costs_raw.get("reserve_callout_pilot", 18500.0)),
            "deadhead_base_fare": float(costs_raw.get("deadhead_positioning", 6500.0)),
            "cancel_fixed_fee": float(costs_raw.get("cancellation_per_flight", 250000.0)),
            "delay_penalty_per_min": float(costs_raw.get("delay_cost_per_duty_hour", 5400.0)) / 60.0,
            "deadhead_DEL_BLR": float(costs_raw.get("deadhead_positioning", 6500.0)),
        }
        for ak, av in aliases.items():
            conn.execute(
                "INSERT OR IGNORE INTO cost_rate (key, value, unit) VALUES (?, ?, ?)",
                (ak, av, currency),
            )
    elif isinstance(costs_raw, list):
        for c in costs_raw:
            conn.execute(
                "INSERT OR REPLACE INTO cost_rate (key, value, unit) VALUES (?, ?, ?)",
                (c["key"], c["value"], c["unit"]),
            )

    # 8. Risk Signals
    if (raw_dir / "risk_signals.json").exists():
        risks = load_json(raw_dir / "risk_signals.json")
        for r in risks:
            score = r.get("disruption_risk_score", r.get("score", 0.0))
            factors = json.dumps(r.get("drivers", r.get("factors", [])))
            conn.execute(
                "INSERT OR REPLACE INTO risk_signal (crew_id, score, factors) VALUES (?, ?, ?)",
                (r["crew_id"], score, factors),
            )


def _ingest_synthetic_dataset(conn: sqlite3.Connection, raw_dir: Path) -> None:
    """Ingests the synthetic fallback baseline dataset."""
    # 1. Crew
    crew_list = load_json(raw_dir / "crew.json")
    for c in crew_list:
        conn.execute(
            """INSERT INTO crew (crew_id, name, rank, base, seniority, reachability_minutes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                c["crew_id"],
                c["name"],
                c["rank"],
                c["base"],
                c.get("seniority"),
                c.get("reachability_minutes"),
            ),
        )

    # 2. Crew Ratings
    if (raw_dir / "crew_rating.json").exists():
        ratings = load_json(raw_dir / "crew_rating.json")
        for r in ratings:
            conn.execute(
                "INSERT INTO crew_rating (crew_id, aircraft_type) VALUES (?, ?)",
                (r["crew_id"], r["aircraft_type"]),
            )

    # 3. Flights
    flights = load_json(raw_dir / "flights.json")
    for fl in flights:
        conn.execute(
            """INSERT INTO flight (flight_id, flight_no, origin, destination, dep_utc, arr_utc,
                                  block_minutes, aircraft_type, tail_id, rotation_id,
                                  rotation_seq, passengers)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fl["flight_id"],
                fl.get("flight_no"),
                fl.get("origin") or fl.get("dep_station"),
                fl.get("destination") or fl.get("arr_station"),
                fl["dep_utc"],
                fl["arr_utc"],
                fl["block_minutes"],
                fl["aircraft_type"],
                fl.get("tail_id") or fl.get("aircraft"),
                fl.get("rotation_id"),
                fl.get("rotation_seq"),
                fl.get("passengers"),
            ),
        )

    # 4. Pairings
    pairings = load_json(raw_dir / "pairings.json")
    for p in pairings:
        conn.execute(
            "INSERT INTO pairing (pairing_id, base, start_utc, end_utc) VALUES (?, ?, ?, ?)",
            (p["pairing_id"], p.get("base"), p["start_utc"], p["end_utc"]),
        )

    # 5. Pairing Legs
    pairing_legs = load_json(raw_dir / "pairing_legs.json")
    for pl in pairing_legs:
        conn.execute(
            """INSERT INTO pairing_leg (pairing_id, leg_seq, flight_id, duty_id)
               VALUES (?, ?, ?, ?)""",
            (pl["pairing_id"], pl["leg_seq"], pl["flight_id"], pl.get("duty_id")),
        )

    # 6. Assignments
    assignments = load_json(raw_dir / "assignments.json")
    for a in assignments:
        conn.execute(
            "INSERT INTO assignment (crew_id, pairing_id, role) VALUES (?, ?, ?)",
            (a["crew_id"], a["pairing_id"], a["role"]),
        )

    # 7. Duty Clocks
    clocks = load_json(raw_dir / "duty_clock.json")
    for dc in clocks:
        conn.execute(
            """INSERT INTO duty_clock (crew_id, duty_hours_7d, flight_hours_28d, last_rest_ended)
               VALUES (?, ?, ?, ?)""",
            (
                dc["crew_id"],
                dc.get("duty_hours_7d"),
                dc.get("flight_hours_28d"),
                dc.get("last_rest_ended"),
            ),
        )

    # 8. Certifications
    certs = load_json(raw_dir / "certifications.json")
    for cert in certs:
        conn.execute(
            """INSERT INTO certification (crew_id, cert_type, valid_from, expires_on)
               VALUES (?, ?, ?, ?)""",
            (cert["crew_id"], cert["cert_type"], cert.get("valid_from"), cert.get("expires_on") or cert.get("valid_to")),
        )

    # 9. Reserve
    reserves = load_json(raw_dir / "reserve.json")
    for res in reserves:
        conn.execute(
            """INSERT INTO reserve (crew_id, base, oncall_start_utc, oncall_end_utc, standby_status)
               VALUES (?, ?, ?, ?, ?)""",
            (
                res["crew_id"],
                res["base"],
                res["oncall_start_utc"],
                res["oncall_end_utc"],
                res["standby_status"],
            ),
        )

    # 10. Costs
    costs = load_json(raw_dir / "costs.json")
    for c in costs:
        conn.execute(
            "INSERT INTO cost_rate (key, value, unit) VALUES (?, ?, ?)",
            (c["key"], c["value"], c["unit"]),
        )

    # 11. Risk Signals
    risk_signals = load_json(raw_dir / "risk_signals.json")
    for rs in risk_signals:
        conn.execute(
            "INSERT INTO risk_signal (crew_id, score, factors) VALUES (?, ?, ?)",
            (rs["crew_id"], rs["score"], rs.get("factors")),
        )

    # Derive Duty Timeline
    derive_duty_timeline(conn)


def derive_duty_timeline(conn: sqlite3.Connection) -> None:
    """Derives discrete duty records for each crew member from pairings and assignments."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT a.crew_id, p.pairing_id, pl.duty_id, pl.leg_seq,
               f.flight_id, f.dep_utc, f.arr_utc, f.block_minutes
        FROM assignment a
        JOIN pairing p ON a.pairing_id = p.pairing_id
        JOIN pairing_leg pl ON p.pairing_id = pl.pairing_id
        JOIN flight f ON pl.flight_id = f.flight_id
        ORDER BY a.crew_id, p.pairing_id, pl.leg_seq
    """)
    rows = cursor.fetchall()

    grouped_duties: Dict[Tuple[str, str, str], List[sqlite3.Row]] = {}
    for r in rows:
        duty_tag = r["duty_id"] or f"{r['pairing_id']}-D1"
        key = (r["crew_id"], r["pairing_id"], duty_tag)
        grouped_duties.setdefault(key, []).append(r)

    for (crew_id, pairing_id, duty_tag), legs in grouped_duties.items():
        first_dep = min(datetime.fromisoformat(l["dep_utc"].replace("Z", "+00:00")) for l in legs)
        last_arr = max(datetime.fromisoformat(l["arr_utc"].replace("Z", "+00:00")) for l in legs)

        # Duty starts 1h before first departure, ends 30m after last arrival
        duty_start = first_dep - timedelta(hours=1)
        duty_end = last_arr + timedelta(minutes=30)
        duty_minutes = int((duty_end - duty_start).total_seconds() // 60)
        block_minutes = sum(l["block_minutes"] for l in legs)
        sectors = len(legs)

        duty_id = f"{crew_id}-{pairing_id}-{duty_tag}"
        conn.execute(
            """INSERT OR REPLACE INTO duty (duty_id, pairing_id, crew_id, start_utc,
                                           end_utc, duty_minutes, block_minutes, sectors)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                duty_id,
                pairing_id,
                crew_id,
                duty_start.isoformat().replace("+00:00", "Z"),
                duty_end.isoformat().replace("+00:00", "Z"),
                duty_minutes,
                block_minutes,
                sectors,
            ),
        )


def validate_rotation_continuity(conn: sqlite3.Connection) -> None:
    """Validates that for every rotation, arrival station of leg N matches departure of leg N+1."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COALESCE(rotation_id, tail_id) as rot_id, origin, destination, flight_id, dep_utc
        FROM flight
        WHERE rotation_id IS NOT NULL OR tail_id IS NOT NULL
        ORDER BY rot_id, dep_utc
    """)
    rotations: Dict[str, List[sqlite3.Row]] = {}
    for r in cursor.fetchall():
        if r["rot_id"]:
            rotations.setdefault(r["rot_id"], []).append(r)

    for rot_id, legs in rotations.items():
        for i in range(len(legs) - 1):
            if legs[i]["destination"] != legs[i + 1]["origin"]:
                logger.warning(
                    "Rotation continuity break detected",
                    rotation_id=rot_id,
                    leg_n=legs[i]["flight_id"],
                    dest_n=legs[i]["destination"],
                    leg_next=legs[i + 1]["flight_id"],
                    orig_next=legs[i + 1]["origin"],
                )


def reconcile_duty_clocks(conn: sqlite3.Connection) -> str:
    """Soft reconciliation between derived 7-day duty hours and duty_clock.duty_hours_7d."""
    cursor = conn.cursor()
    anchor_utc = datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc)
    seven_days_ago = anchor_utc - timedelta(days=7)

    cursor.execute("SELECT crew_id, duty_hours_7d, daily_history FROM duty_clock")
    clocks = cursor.fetchall()

    discrepancy_found = False
    for clk in clocks:
        crew_id = clk["crew_id"]
        scalar_7d = clk["duty_hours_7d"] or 0.0
        daily_history_raw = clk["daily_history"]

        if daily_history_raw:
            # Dataset provides daily history list: sum for Sep 8-14
            try:
                history_list = json.loads(daily_history_raw)
                hist_duties = {
                    date.fromisoformat(x["date"]): x.get("duty_hours", 0.0)
                    for x in history_list
                }
                # Check Sep 8-14 history plus any duty on Sep 14
                sep8 = date(2026, 9, 8)
                sep14 = date(2026, 9, 14)
                d7_hist = sum(v for d, v in hist_duties.items() if sep8 <= d <= sep14)
                cursor.execute(
                    "SELECT SUM(duty_minutes) as dm FROM duty WHERE crew_id = ? AND start_utc LIKE '2026-09-14%'",
                    (crew_id,),
                )
                r_row = cursor.fetchone()
                d7_roster = (r_row["dm"] or 0) / 60.0 if r_row else 0.0
                d7 = round(d7_hist + d7_roster, 2)
                diff = abs(d7 - round(scalar_7d, 2))
                if diff > 0.2:
                    discrepancy_found = True
                    logger.warning(
                        "Duty clock soft-reconciliation delta exceeded tolerance (history)",
                        crew_id=crew_id,
                        scalar_hours=scalar_7d,
                        derived_hours=d7,
                        delta=diff,
                    )
                continue
            except Exception:
                pass

        # Fallback to summing derived duty periods in duty table
        cursor.execute(
            """SELECT start_utc, end_utc, duty_minutes FROM duty
               WHERE crew_id = ? AND start_utc < ? AND end_utc > ?""",
            (crew_id, anchor_utc.isoformat(), seven_days_ago.isoformat()),
        )
        duties = cursor.fetchall()
        total_minutes = 0.0
        for d in duties:
            s_iso = d["start_utc"].replace("Z", "+00:00")
            e_iso = d["end_utc"].replace("Z", "+00:00")
            d_start = max(datetime.fromisoformat(s_iso), seven_days_ago)
            d_end = min(datetime.fromisoformat(e_iso), anchor_utc)
            if d_end > d_start:
                total_minutes += (d_end - d_start).total_seconds() / 60.0

        derived_7d = round(total_minutes / 60.0, 2)
        diff = abs(derived_7d - scalar_7d)

        if diff > 0.2:
            discrepancy_found = True
            logger.warning(
                "Duty clock soft-reconciliation delta exceeded tolerance",
                crew_id=crew_id,
                scalar_hours=scalar_7d,
                derived_hours=derived_7d,
                delta=diff,
            )

    return "scalar_anchored" if discrepancy_found else "reconciled"


if __name__ == "__main__":
    build_database()
