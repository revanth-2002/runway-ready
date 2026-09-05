"""Parameterized SQL repository for Crew Ops Advisor."""

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from advisor.audit.logger import StructuredLogger
from advisor.domain.exceptions import EntityNotFoundError
from advisor.domain.types import (
    Assignment,
    Certification,
    Crew,
    Duty,
    DutyClock,
    Flight,
    Pairing,
    Reserve,
)

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "ops.db"
logger = StructuredLogger("advisor.data.repository")


class OpsRepository:
    """Encapsulates parameterized SQL queries over the SQLite operations database."""

    def __init__(self, db_source: Union[Path, str, sqlite3.Connection] = DEFAULT_DB_PATH):
        if isinstance(db_source, sqlite3.Connection):
            self._conn = db_source
            self._close_on_exit = False
        else:
            self._db_path = Path(db_source)
            self._conn = None
            self._close_on_exit = True

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def get_crew(self, crew_id: str) -> Crew:
        """Retrieves a single crew member by ID. Raises EntityNotFoundError if missing."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT crew_id, name, rank, base, seniority, reachability_minutes
               FROM crew WHERE crew_id = ?""",
            (crew_id,),
        )
        row = cursor.fetchone()
        if not row:
            logger.warning("Crew not found in database", crew_id=crew_id)
            raise EntityNotFoundError(f"Crew {crew_id} not found in roster records.")
        logger.debug("Retrieved crew record", crew_id=crew_id, base=row["base"], rank=row["rank"])
        return Crew(
            crew_id=row["crew_id"],
            name=row["name"],
            rank=row["rank"],
            base=row["base"],
            seniority=row["seniority"],
            reachability_minutes=row["reachability_minutes"],
        )

    def find_crew(self, crew_id: str) -> Optional[Crew]:
        """Retrieves a single crew member by ID, returning None if missing."""
        try:
            return self.get_crew(crew_id)
        except EntityNotFoundError:
            return None

    def list_all_crew(self) -> List[Crew]:
        """Lists all crew in the database."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT crew_id, name, rank, base, seniority, reachability_minutes FROM crew")
        return [
            Crew(
                crew_id=r["crew_id"],
                name=r["name"],
                rank=r["rank"],
                base=r["base"],
                seniority=r["seniority"],
                reachability_minutes=r["reachability_minutes"],
            )
            for r in cursor.fetchall()
        ]

    def list_crew_by_base(self, base: str, rank: Optional[str] = None) -> List[Crew]:
        """Lists crew based at a specific station, optionally filtered by rank."""
        conn = self._get_connection()
        cursor = conn.cursor()
        if rank:
            cursor.execute(
                "SELECT crew_id, name, rank, base, seniority, reachability_minutes FROM crew WHERE base = ? AND rank = ? ORDER BY seniority DESC",
                (base, rank),
            )
        else:
            cursor.execute(
                "SELECT crew_id, name, rank, base, seniority, reachability_minutes FROM crew WHERE base = ? ORDER BY seniority DESC",
                (base,),
            )
        return [
            Crew(
                crew_id=r["crew_id"],
                name=r["name"],
                rank=r["rank"],
                base=r["base"],
                seniority=r["seniority"],
                reachability_minutes=r["reachability_minutes"],
            )
            for r in cursor.fetchall()
        ]

    def list_high_duty_crew(self, threshold_hours: float = 45.0) -> List[Dict[str, Any]]:
        """Lists crew whose 7-day cumulative duty hours meet or exceed threshold."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT crew_id, duty_hours_7d, flight_hours_28d FROM duty_clock WHERE duty_hours_7d >= ? ORDER BY duty_hours_7d DESC",
            (threshold_hours,),
        )
        return [dict(r) for r in cursor.fetchall()]

    def get_crew_for_tail(
        self, tail_id: str, date: Optional[str] = "2026-09-15", role: str = "Captain"
    ) -> Optional[Crew]:
        """Retrieves the crew member rostered on a specific aircraft tail."""
        conn = self._get_connection()
        cursor = conn.cursor()
        query = """
            SELECT c.crew_id, c.name, c.rank, c.base, c.seniority, c.reachability_minutes
            FROM flight f
            JOIN pairing_leg pl ON f.flight_id = pl.flight_id
            JOIN assignment a ON pl.pairing_id = a.pairing_id
            JOIN crew c ON a.crew_id = c.crew_id
            WHERE f.tail_id = ?
        """
        params: List[Any] = [tail_id]
        if date:
            query += " AND f.dep_utc LIKE ?"
            params.append(f"{date}%")
        if role:
            query += " AND a.role LIKE ?"
            params.append(f"%{role}%")
        query += " ORDER BY f.dep_utc ASC LIMIT 1"
        cursor.execute(query, tuple(params))
        row = cursor.fetchone()
        if row:
            return Crew(
                crew_id=row["crew_id"],
                name=row["name"],
                rank=row["rank"],
                base=row["base"],
                seniority=row["seniority"],
                reachability_minutes=row["reachability_minutes"],
            )
        return None

    def get_flight(self, flight_id: str) -> Flight:
        """Retrieves a flight by ID or flight number. Raises EntityNotFoundError if missing."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT flight_id, origin, destination, dep_utc, arr_utc, block_minutes,
                      aircraft_type, tail_id, rotation_id, rotation_seq, passengers
               FROM flight WHERE flight_id = ? OR flight_no = ?
               ORDER BY CASE WHEN flight_id = ? THEN 0 ELSE 1 END LIMIT 1""",
            (flight_id, flight_id, flight_id),
        )
        row = cursor.fetchone()
        if not row:
            logger.warning("Flight not found in database", flight_id=flight_id)
            raise EntityNotFoundError(f"Flight {flight_id} not found.")
        logger.debug("Retrieved flight record", flight_id=flight_id, origin=row["origin"], destination=row["destination"])
        return Flight(
            flight_id=row["flight_id"],
            origin=row["origin"],
            destination=row["destination"],
            dep_utc=row["dep_utc"],
            arr_utc=row["arr_utc"],
            block_minutes=row["block_minutes"],
            aircraft_type=row["aircraft_type"],
            tail_id=row["tail_id"],
            rotation_id=row["rotation_id"],
            rotation_seq=row["rotation_seq"],
            passengers=row["passengers"],
        )

    def list_crew_on_dayoff(
        self, rank: str, base: str, window_start_utc: str, window_end_utc: str
    ) -> List[Crew]:
        """Finds crew of given rank and base who are off-duty and not on reserve during the window."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT c.crew_id, c.name, c.rank, c.base, c.seniority, c.reachability_minutes
               FROM crew c
               WHERE c.rank = ? AND c.base = ?
                 AND c.crew_id NOT IN (
                     SELECT d.crew_id FROM duty d
                     WHERE d.start_utc < ? AND d.end_utc > ?
                 )
                 AND c.crew_id NOT IN (
                     SELECT r.crew_id FROM reserve r
                     WHERE r.oncall_start_utc < ? AND r.oncall_end_utc > ?
                 )
               ORDER BY c.seniority DESC""",
            (rank, base, window_end_utc, window_start_utc, window_end_utc, window_start_utc),
        )
        return [
            Crew(
                crew_id=r["crew_id"],
                name=r["name"],
                rank=r["rank"],
                base=r["base"],
                seniority=r["seniority"],
                reachability_minutes=r["reachability_minutes"],
            )
            for r in cursor.fetchall()
        ]

    def find_flight(self, flight_id: str) -> Optional[Flight]:
        try:
            return self.get_flight(flight_id)
        except EntityNotFoundError:
            return None

    def list_flights(
        self,
        origin: Optional[str] = None,
        destination: Optional[str] = None,
        after_utc: Optional[str] = None,
        before_utc: Optional[str] = None,
        tail_id: Optional[str] = None,
        rotation_id: Optional[str] = None,
    ) -> List[Flight]:
        """Lists flights matching parameterized criteria."""
        conn = self._get_connection()
        cursor = conn.cursor()
        query = """SELECT flight_id, origin, destination, dep_utc, arr_utc, block_minutes,
                          aircraft_type, tail_id, rotation_id, rotation_seq, passengers
                   FROM flight WHERE 1=1"""
        params: List[Any] = []
        if origin:
            query += " AND origin = ?"
            params.append(origin)
        if destination:
            query += " AND destination = ?"
            params.append(destination)
        if after_utc:
            query += " AND dep_utc >= ?"
            params.append(after_utc)
        if before_utc:
            query += " AND dep_utc <= ?"
            params.append(before_utc)
        if tail_id:
            query += " AND tail_id = ?"
            params.append(tail_id)
        if rotation_id:
            query += " AND rotation_id = ?"
            params.append(rotation_id)

        query += " ORDER BY dep_utc"
        cursor.execute(query, tuple(params))
        return [
            Flight(
                flight_id=r["flight_id"],
                origin=r["origin"],
                destination=r["destination"],
                dep_utc=r["dep_utc"],
                arr_utc=r["arr_utc"],
                block_minutes=r["block_minutes"],
                aircraft_type=r["aircraft_type"],
                tail_id=r["tail_id"],
                rotation_id=r["rotation_id"],
                rotation_seq=r["rotation_seq"],
                passengers=r["passengers"],
            )
            for r in cursor.fetchall()
        ]

    def get_pairing(self, pairing_id: str) -> Pairing:
        """Retrieves a pairing with all its flight legs ordered by sequence."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT pairing_id, base, start_utc, end_utc FROM pairing WHERE pairing_id = ?", (pairing_id,))
        p_row = cursor.fetchone()
        if not p_row:
            logger.warning("Pairing not found in database", pairing_id=pairing_id)
            raise EntityNotFoundError(f"Pairing {pairing_id} not found.")
        logger.debug("Retrieved pairing record", pairing_id=pairing_id, base=p_row["base"])

        cursor.execute(
            """SELECT f.flight_id, f.origin, f.destination, f.dep_utc, f.arr_utc,
                      f.block_minutes, f.aircraft_type, f.tail_id, f.rotation_id,
                      f.rotation_seq, f.passengers
               FROM pairing_leg pl
               JOIN flight f ON pl.flight_id = f.flight_id
               WHERE pl.pairing_id = ?
               ORDER BY pl.leg_seq""",
            (pairing_id,),
        )
        legs = tuple(
            Flight(
                flight_id=r["flight_id"],
                origin=r["origin"],
                destination=r["destination"],
                dep_utc=r["dep_utc"],
                arr_utc=r["arr_utc"],
                block_minutes=r["block_minutes"],
                aircraft_type=r["aircraft_type"],
                tail_id=r["tail_id"],
                rotation_id=r["rotation_id"],
                rotation_seq=r["rotation_seq"],
                passengers=r["passengers"],
            )
            for r in cursor.fetchall()
        )
        return Pairing(
            pairing_id=p_row["pairing_id"],
            base=p_row["base"],
            start_utc=p_row["start_utc"],
            end_utc=p_row["end_utc"],
            legs=legs,
        )

    def get_pairing_for_crew(self, crew_id: str, at_utc: Optional[str] = None) -> Optional[Pairing]:
        """Finds the pairing assigned to a crew member covering or after a specific timestamp."""
        conn = self._get_connection()
        cursor = conn.cursor()
        query = """SELECT p.pairing_id
                   FROM assignment a
                   JOIN pairing p ON a.pairing_id = p.pairing_id
                   WHERE a.crew_id = ?"""
        params: List[Any] = [crew_id]
        if at_utc:
            query += " AND p.end_utc >= ? ORDER BY p.start_utc LIMIT 1"
            params.append(at_utc)
        else:
            query += " ORDER BY p.start_utc DESC LIMIT 1"

        cursor.execute(query, tuple(params))
        row = cursor.fetchone()
        if not row:
            return None
        return self.get_pairing(row["pairing_id"])

    def get_companion_crew(self, pairing_id: str, exclude_crew_id: str) -> List[Crew]:
        """Finds other crew members assigned to the same pairing."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT c.crew_id, c.name, c.rank, c.base, c.seniority, c.reachability_minutes
               FROM assignment a
               JOIN crew c ON a.crew_id = c.crew_id
               WHERE a.pairing_id = ? AND a.crew_id != ?""",
            (pairing_id, exclude_crew_id),
        )
        return [
            Crew(
                crew_id=r["crew_id"],
                name=r["name"],
                rank=r["rank"],
                base=r["base"],
                seniority=r["seniority"],
                reachability_minutes=r["reachability_minutes"],
            )
            for r in cursor.fetchall()
        ]

    def list_reserves(
        self,
        base: Optional[str] = None,
        report_time_utc: Optional[str] = None,
        date: Optional[str] = None,
        distinct_crew: bool = False,
        *args: Any,
        **kwargs: Any,
    ) -> List[Reserve]:
        """Lists active reserves optionally filtered by station, date, and coverage window."""
        date = date or kwargs.get("date")
        distinct_crew = distinct_crew or kwargs.get("distinct_crew", False)
        conn = self._get_connection()
        cursor = conn.cursor()
        query = "SELECT crew_id, base, oncall_start_utc, oncall_end_utc, standby_status FROM reserve WHERE 1=1"
        params: List[Any] = []
        if base:
            query += " AND base = ?"
            params.append(base)
        if report_time_utc:
            query += " AND oncall_start_utc <= ? AND oncall_end_utc >= ?"
            params.extend([report_time_utc, report_time_utc])
        elif date:
            query += " AND oncall_start_utc LIKE ?"
            params.append(f"{date}%")
        elif distinct_crew:
            query += " GROUP BY crew_id"

        cursor.execute(query, tuple(params))
        return [
            Reserve(
                crew_id=r["crew_id"],
                base=r["base"],
                oncall_start_utc=r["oncall_start_utc"],
                oncall_end_utc=r["oncall_end_utc"],
                standby_status=r["standby_status"],
            )
            for r in cursor.fetchall()
        ]

    def list_duty_intervals(
        self, crew_id: str, window_start_utc: str, window_end_utc: str
    ) -> List[Duty]:
        """Retrieves historical duty periods for a crew member overlapping a window."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT duty_id, pairing_id, crew_id, start_utc, end_utc,
                      duty_minutes, block_minutes, sectors
               FROM duty
               WHERE crew_id = ? AND start_utc < ? AND end_utc > ?
               ORDER BY start_utc""",
            (crew_id, window_end_utc, window_start_utc),
        )
        return [
            Duty(
                duty_id=r["duty_id"],
                pairing_id=r["pairing_id"],
                crew_id=r["crew_id"],
                start_utc=r["start_utc"],
                end_utc=r["end_utc"],
                duty_minutes=r["duty_minutes"],
                block_minutes=r["block_minutes"],
                sectors=r["sectors"],
            )
            for r in cursor.fetchall()
        ]

    def get_duty_clock(self, crew_id: str) -> Optional[DutyClock]:
        """Retrieves pre-computed scalar clocks for a crew member."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT crew_id, duty_hours_7d, flight_hours_28d, last_rest_ended FROM duty_clock WHERE crew_id = ?",
            (crew_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return DutyClock(
            crew_id=row["crew_id"],
            duty_hours_7d=row["duty_hours_7d"] or 0.0,
            flight_hours_28d=row["flight_hours_28d"] or 0.0,
            last_rest_ended=row["last_rest_ended"],
        )

    def list_certifications(self, crew_id: str) -> List[Certification]:
        """Lists certifications for a crew member."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT crew_id, cert_type, valid_from, expires_on FROM certification WHERE crew_id = ?",
            (crew_id,),
        )
        return [
            Certification(
                crew_id=r["crew_id"],
                cert_type=r["cert_type"],
                valid_from=r["valid_from"],
                expires_on=r["expires_on"],
            )
            for r in cursor.fetchall()
        ]

    def list_ratings(self, crew_id: str) -> List[str]:
        """Lists aircraft type ratings for a crew member."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT aircraft_type FROM crew_rating WHERE crew_id = ?", (crew_id,))
        return [r["aircraft_type"] for r in cursor.fetchall()]

    def get_cost_rates(self) -> Dict[str, float]:
        """Loads operational cost rates from cost_rate table."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM cost_rate")
        return {r["key"]: float(r["value"]) for r in cursor.fetchall()}

    def get_last_duty_end(self, crew_id: str, before_utc: str) -> Optional[str]:
        """Finds the completion timestamp of the last duty prior to a given time."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT end_utc FROM duty WHERE crew_id = ? AND end_utc <= ? ORDER BY end_utc DESC LIMIT 1",
            (crew_id, before_utc),
        )
        row = cursor.fetchone()
        if row:
            return row["end_utc"]
        # Fallback to duty_clock snapshot
        clk = self.get_duty_clock(crew_id)
        return clk.last_rest_ended if clk else None

    def list_flights_by_station(
        self, origin: Optional[str] = None, destination: Optional[str] = None, date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Lists flights filtered by origin, destination, and/or date."""
        conn = self._get_connection()
        cursor = conn.cursor()
        query = "SELECT flight_id, flight_no, origin, destination, dep_utc, arr_utc, aircraft_type, tail_id, passengers FROM flight WHERE 1=1"
        params: List[Any] = []
        if origin:
            query += " AND origin = ?"
            params.append(origin)
        if destination:
            query += " AND destination = ?"
            params.append(destination)
        if date:
            query += " AND dep_utc LIKE ?"
            params.append(f"{date}%")
        query += " ORDER BY dep_utc"
        cursor.execute(query, tuple(params))
        return [dict(r) for r in cursor.fetchall()]

    def list_expiring_certifications(
        self, within_days: int = 30, reference_date: str = "2026-09-15"
    ) -> List[Dict[str, Any]]:
        """Lists crew certifications expiring within given days of reference date."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT crew_id, cert_type, expires_on
               FROM certification
               WHERE expires_on >= ? AND expires_on <= date(?, ? || ' days')
               ORDER BY expires_on""",
            (reference_date, reference_date, str(within_days)),
        )
        return [dict(r) for r in cursor.fetchall()]

    def get_pairing_assignments(self, pairing_id: str) -> List[Dict[str, str]]:
        """Lists all crew assigned to a pairing with their rank/role."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT a.crew_id, c.name, c.rank, a.role
               FROM assignment a
               JOIN crew c ON a.crew_id = c.crew_id
               WHERE a.pairing_id = ?
               ORDER BY c.rank""",
            (pairing_id,),
        )
        return [dict(r) for r in cursor.fetchall()]

    def list_nonstop_destinations(self, origin: str) -> List[str]:
        """Lists all nonstop destination stations served from an origin."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT destination FROM flight WHERE origin = ? ORDER BY destination",
            (origin,),
        )
        return [r["destination"] for r in cursor.fetchall()]

    def list_flights_affected_by_closure(
        self, station: str, start_utc: str, end_utc: str
    ) -> List[str]:
        """Lists flight_ids arriving or departing a station during a closure window."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT flight_id FROM flight
               WHERE (origin = ? AND dep_utc >= ? AND dep_utc <= ?)
                  OR (destination = ? AND arr_utc >= ? AND arr_utc <= ?)
               ORDER BY dep_utc""",
            (station, start_utc, end_utc, station, start_utc, end_utc),
        )
        return [r["flight_id"] for r in cursor.fetchall()]

    def get_crew_for_flight(self, flight_id: str, role: Optional[str] = "Captain") -> Optional[Crew]:
        """Retrieves the crew member rostered on the pairing leg for a given flight number or flight_id."""
        conn = self._get_connection()
        cursor = conn.cursor()
        query = """
            SELECT c.crew_id, c.name, c.rank, c.base, c.seniority, c.reachability_minutes
            FROM pairing_leg pl
            JOIN flight f ON pl.flight_id = f.flight_id
            JOIN assignment a ON pl.pairing_id = a.pairing_id
            JOIN crew c ON a.crew_id = c.crew_id
            WHERE (pl.flight_id = ? OR f.flight_no = ? OR pl.flight_id LIKE ?)
        """
        params: List[Any] = [flight_id, flight_id, f"{flight_id}%"]
        if role:
            query += " AND a.role LIKE ?"
            params.append(f"%{role}%")
        query += " ORDER BY f.dep_utc ASC LIMIT 1"
        cursor.execute(query, tuple(params))
        row = cursor.fetchone()
        if not row:
            return None
        return Crew(
            crew_id=row["crew_id"],
            name=row["name"],
            rank=row["rank"],
            base=row["base"],
            seniority=row["seniority"],
            reachability_minutes=row["reachability_minutes"],
        )

