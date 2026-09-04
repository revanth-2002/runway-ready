"""Strict UTC date and time manipulation utilities."""

from datetime import datetime, timezone, timedelta
from typing import List, Optional
from advisor.domain.types import Duty, Flight


def parse_utc(iso_str: str) -> datetime:
    """Parses an ISO-8601 string into a strict timezone-aware UTC datetime."""
    clean_str = iso_str.strip()
    if clean_str.endswith("Z"):
        clean_str = clean_str[:-1] + "+00:00"
    dt = datetime.fromisoformat(clean_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_utc(dt: datetime) -> str:
    """Formats a datetime into a strict UTC ISO-8601 string ending with 'Z'."""
    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def format_display_time(iso_str: str) -> str:
    """Formats an ISO-8601 string into a presentation-friendly string (e.g. '14:15 UTC')."""
    try:
        dt = parse_utc(iso_str)
        return dt.strftime("%H:%M UTC")
    except Exception:
        return iso_str


def format_display_date_time(iso_str: str) -> str:
    """Formats an ISO-8601 string into '15 Sep 14:15 UTC'."""
    try:
        dt = parse_utc(iso_str)
        return dt.strftime("%d %b %H:%M UTC")
    except Exception:
        return iso_str


def interval_overlap_minutes(
    start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime
) -> float:
    """Calculates the minute overlap between two time intervals [start_a, end_a] and [start_b, end_b]."""
    overlap_start = max(start_a, start_b)
    overlap_end = min(end_a, end_b)
    if overlap_end <= overlap_start:
        return 0.0
    return (overlap_end - overlap_start).total_seconds() / 60.0


def rolling_window_duty_hours(
    historical_duties: List[Duty],
    window_end: datetime,
    window_days: int = 7,
) -> float:
    """Calculates cumulative duty hours overlapping the rolling window [window_end - days, window_end].
    Boundary-straddling duty periods are pro-rated by fractional minute overlap.
    """
    window_start = window_end - timedelta(days=window_days)
    total_overlap_minutes = 0.0

    for duty in historical_duties:
        d_start = parse_utc(duty.start_utc)
        d_end = parse_utc(duty.end_utc)
        overlap = interval_overlap_minutes(d_start, d_end, window_start, window_end)
        total_overlap_minutes += overlap

    return round(total_overlap_minutes / 60.0, 2)


def rolling_window_block_hours(
    historical_flights: List[Flight],
    window_end: datetime,
    window_days: int = 28,
) -> float:
    """Calculates cumulative flight block hours strictly within the 28-day rolling window.
    Deadhead flights are excluded.
    """
    window_start = window_end - timedelta(days=window_days)
    total_block_minutes = 0.0

    for flight in historical_flights:
        f_dep = parse_utc(flight.dep_utc)
        f_arr = parse_utc(flight.arr_utc)
        if f_arr > window_start and f_dep < window_end:
            overlap = interval_overlap_minutes(f_dep, f_arr, window_start, window_end)
            total_block_minutes += overlap

    return round(total_block_minutes / 60.0, 2)
