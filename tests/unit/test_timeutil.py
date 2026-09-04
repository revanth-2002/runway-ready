"""Unit tests for timeutil."""

from datetime import datetime, timezone
import pytest
from advisor.domain.timeutil import (
    parse_utc,
    format_utc,
    format_display_time,
    interval_overlap_minutes,
    rolling_window_duty_hours,
    rolling_window_block_hours,
)
from advisor.domain.types import Duty, Flight


def test_parse_utc():
    dt = parse_utc("2026-09-15T10:30:00Z")
    assert dt.tzinfo == timezone.utc
    assert dt.hour == 10
    assert dt.minute == 30

    # Test offset normalized
    dt2 = parse_utc("2026-09-15T16:00:00+05:30")
    assert dt2.tzinfo == timezone.utc
    assert dt2.hour == 10
    assert dt2.minute == 30


def test_format_display_time():
    assert format_display_time("2026-09-15T14:15:00Z") == "14:15 UTC"


def test_interval_overlap_minutes():
    # Disjoint
    s1, e1 = datetime(2026, 9, 15, 8, 0, tzinfo=timezone.utc), datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
    s2, e2 = datetime(2026, 9, 15, 11, 0, tzinfo=timezone.utc), datetime(2026, 9, 15, 13, 0, tzinfo=timezone.utc)
    assert interval_overlap_minutes(s1, e1, s2, e2) == 0.0

    # Overlapping 60 minutes
    s3, e3 = datetime(2026, 9, 15, 9, 0, tzinfo=timezone.utc), datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    assert interval_overlap_minutes(s1, e1, s3, e3) == 60.0


def test_rolling_window_duty_hours():
    # 2 historical duties of 9 hours each
    duties = [
        Duty(
            duty_id="d1",
            pairing_id="p1",
            crew_id="C-1",
            start_utc="2026-09-14T08:00:00Z",
            end_utc="2026-09-14T17:00:00Z",
            duty_minutes=540,
            block_minutes=300,
            sectors=2,
        ),
        Duty(
            duty_id="d2",
            pairing_id="p2",
            crew_id="C-1",
            start_utc="2026-09-13T08:00:00Z",
            end_utc="2026-09-13T17:00:00Z",
            duty_minutes=540,
            block_minutes=300,
            sectors=2,
        ),
    ]
    window_end = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    hours = rolling_window_duty_hours(duties, window_end, window_days=7)
    assert hours == 18.0
