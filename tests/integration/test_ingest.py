"""Integration tests for database ingestion and reconciliation."""

import sqlite3
import pytest
from advisor.data.ingest import build_database, DEFAULT_RAW_DIR, SCHEMA_PATH


def test_build_database_temp(tmp_path):
    temp_db = tmp_path / "test_ops.db"
    conn, clock_mode = build_database(
        raw_dir=DEFAULT_RAW_DIR,
        db_path=temp_db,
        schema_path=SCHEMA_PATH,
    )
    assert temp_db.exists()
    assert clock_mode in ("reconciled", "scalar_anchored")

    # Verify foreign key constraint enforcement
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO pairing_leg (pairing_id, leg_seq, flight_id) VALUES ('P-NONEXISTENT', 1, 'DX412')")

    # Verify duty periods derived
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM duty")
    row = cursor.fetchone()
    assert row["cnt"] > 0

    conn.close()
