"""Unit tests for Operational Digital Twin pre-warming engine."""

import sqlite3
from pathlib import Path
import pytest

from advisor.data.repository import DEFAULT_DB_PATH
from advisor.twin.warm import (
    DEFAULT_STATIONS,
    verify_database_health,
    warm_operational_digital_twin,
)


def test_verify_database_health_valid():
    """Verify health check returns True on existing populated database."""
    assert verify_database_health(DEFAULT_DB_PATH) is True


def test_verify_database_health_missing(tmp_path):
    """Verify health check returns False when database file does not exist."""
    missing_db = tmp_path / "nonexistent.db"
    assert verify_database_health(missing_db) is False


def test_verify_database_health_corrupted(tmp_path):
    """Verify health check returns False when database is corrupted or lacks schema."""
    corrupted_db = tmp_path / "corrupted.db"
    conn = sqlite3.connect(str(corrupted_db))
    conn.execute("CREATE TABLE dummy (id INT);")
    conn.commit()
    conn.close()

    assert verify_database_health(corrupted_db) is False


def test_warm_operational_digital_twin_baseline():
    """Verify baseline twin pre-materialization and multi-station reserve warming."""
    result = warm_operational_digital_twin(db_path=DEFAULT_DB_PATH)

    assert result["status"] == "WARMED"
    assert result["tail_count"] == 6
    assert result["flight_count"] >= 100
    assert result["crew_count"] >= 100
    assert result["pairing_count"] > 0
    assert result["warm_latency_ms"] < 2000.0

    # Verify all 6 aircraft tails materialized
    expected_tails = {"VT-DXA", "VT-DXB", "VT-DXC", "VT-DXD", "VT-DXE", "VT-DXF"}
    assert set(result["baseline_twin"].tails.keys()) == expected_tails

    # Verify all 5 hub stations are warmed
    for stn in DEFAULT_STATIONS:
        assert stn in result["station_reserves"]
        reserves = result["station_reserves"][stn]
        assert isinstance(reserves, list)
        if reserves:
            first = reserves[0]
            assert "crew_id" in first
            assert "ratings" in first
            assert "duty_hours_7d" in first
            assert first["base"] == stn


def test_warm_auto_rebuild_on_missing_db(tmp_path):
    """Verify that warming a non-existent database automatically triggers build_database."""
    target_db = tmp_path / "auto_rebuilt.db"
    assert not target_db.exists()

    result = warm_operational_digital_twin(db_path=target_db)

    assert target_db.exists()
    assert result["status"] == "WARMED"
    assert result["tail_count"] == 6
    assert result["flight_count"] > 0
    assert verify_database_health(target_db) is True
