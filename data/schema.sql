PRAGMA foreign_keys = ON;

-- Core Crew Records
CREATE TABLE IF NOT EXISTS crew (
  crew_id              TEXT PRIMARY KEY,
  name                 TEXT NOT NULL,
  rank                 TEXT NOT NULL,          -- Permissive: handles "Captain", "FO", "First Officer"
  base                 TEXT NOT NULL,
  seniority            INTEGER,
  reachability_minutes INTEGER
);

CREATE TABLE IF NOT EXISTS crew_rating (
  crew_id       TEXT NOT NULL REFERENCES crew(crew_id) ON DELETE CASCADE,
  aircraft_type TEXT NOT NULL,
  PRIMARY KEY (crew_id, aircraft_type)
);

-- Schedules & Rotations
CREATE TABLE IF NOT EXISTS flight (
  flight_id       TEXT PRIMARY KEY,
  flight_no       TEXT,
  origin          TEXT NOT NULL,
  destination     TEXT NOT NULL,
  dep_utc         TEXT NOT NULL,
  arr_utc         TEXT NOT NULL,
  block_minutes   INTEGER NOT NULL,
  aircraft_type   TEXT NOT NULL,
  tail_id         TEXT,                        -- Permissive: may be NULL in synthetic data
  rotation_id     TEXT,                        -- Permissive: may be NULL
  rotation_seq    INTEGER,
  passengers      INTEGER                      -- Permissive: may be NULL
);
CREATE INDEX IF NOT EXISTS idx_flight_dep ON flight(dep_utc);
CREATE INDEX IF NOT EXISTS idx_flight_org_dep ON flight(origin, dep_utc);
CREATE INDEX IF NOT EXISTS idx_flight_rotation ON flight(rotation_id, rotation_seq);

-- Pairings & Legs
CREATE TABLE IF NOT EXISTS pairing (
  pairing_id  TEXT PRIMARY KEY,
  base        TEXT,
  start_utc   TEXT NOT NULL,
  end_utc     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pairing_leg (
  pairing_id TEXT NOT NULL REFERENCES pairing(pairing_id),
  leg_seq    INTEGER NOT NULL,
  flight_id  TEXT NOT NULL REFERENCES flight(flight_id),
  duty_id    TEXT,                             -- Groups legs into specific duty periods
  PRIMARY KEY (pairing_id, leg_seq)
);
CREATE INDEX IF NOT EXISTS idx_pairing_leg_flight ON pairing_leg(flight_id);

CREATE TABLE IF NOT EXISTS assignment (
  crew_id    TEXT NOT NULL REFERENCES crew(crew_id),
  pairing_id TEXT NOT NULL REFERENCES pairing(pairing_id),
  role       TEXT NOT NULL,
  PRIMARY KEY (crew_id, pairing_id)
);
CREATE INDEX IF NOT EXISTS idx_assign_pairing ON assignment(pairing_id);

-- Derived Duty Timeline (D2 Modeling)
CREATE TABLE IF NOT EXISTS duty (
  duty_id       TEXT PRIMARY KEY,
  pairing_id    TEXT NOT NULL REFERENCES pairing(pairing_id),
  crew_id       TEXT NOT NULL REFERENCES crew(crew_id),
  start_utc     TEXT NOT NULL,
  end_utc       TEXT NOT NULL,
  duty_minutes  INTEGER NOT NULL,
  block_minutes INTEGER NOT NULL,
  sectors       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_duty_crew_time ON duty(crew_id, start_utc);

-- Snapshots & Supporting Records
CREATE TABLE IF NOT EXISTS duty_clock (
  crew_id          TEXT PRIMARY KEY REFERENCES crew(crew_id),
  duty_hours_7d    REAL,
  flight_hours_28d REAL,
  last_rest_ended  TEXT,
  daily_history    TEXT
);

CREATE TABLE IF NOT EXISTS certification (
  crew_id    TEXT NOT NULL REFERENCES crew(crew_id),
  cert_type  TEXT NOT NULL,
  valid_from TEXT,
  expires_on TEXT NOT NULL,
  PRIMARY KEY (crew_id, cert_type)
);

CREATE TABLE IF NOT EXISTS reserve (
  crew_id          TEXT NOT NULL REFERENCES crew(crew_id),
  base             TEXT NOT NULL,
  oncall_start_utc TEXT NOT NULL,
  oncall_end_utc   TEXT NOT NULL,
  standby_status   TEXT NOT NULL,
  PRIMARY KEY (crew_id, oncall_start_utc)
);
CREATE INDEX IF NOT EXISTS idx_reserve_base_time ON reserve(base, oncall_start_utc, oncall_end_utc);

CREATE TABLE IF NOT EXISTS cost_rate (
  key   TEXT PRIMARY KEY,
  value REAL NOT NULL,
  unit  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_signal (
  crew_id TEXT PRIMARY KEY REFERENCES crew(crew_id),
  score   REAL NOT NULL,
  factors TEXT
);
