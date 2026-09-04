"""Pytest configuration and shared test fixtures."""

import os
import sqlite3
import pytest
from pathlib import Path

# Ensure root directory is on python path
ROOT_DIR = Path(__file__).resolve().parent.parent
os.environ["PYTHONPATH"] = str(ROOT_DIR)


@pytest.fixture
def in_memory_db():
    """Provides an isolated in-memory SQLite database connection."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    yield conn
    conn.close()
