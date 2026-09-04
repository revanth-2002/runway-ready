"""Unit tests for candidate enumeration and deadhead search."""

import pytest
from advisor.data.repository import OpsRepository, DEFAULT_DB_PATH
from advisor.domain.state import OpsState, Overlay
from advisor.reasoning.candidates import enumerate_candidates
from advisor.reasoning.deadhead import find_feasible_deadheads
from advisor.twin.diff import compute_twin_diff


@pytest.fixture
def repo():
    return OpsRepository()


@pytest.fixture
def base_state():
    return OpsState(db_path=DEFAULT_DB_PATH)


def test_find_feasible_deadheads(repo):
    # From DEL to BLR, report time is 10:30 UTC
    feasible = find_feasible_deadheads(
        repo,
        from_base="DEL",
        to_station="BLR",
        latest_arrival_utc="2026-09-15T10:30:00Z",  # flight departure 10:30 UTC
        earliest_dep_utc="2026-09-15T06:00:00Z",
    )
    assert len(feasible) > 0
    flight_ids = [f.flight_id for f in feasible]
    assert any("DX" in fid for fid in flight_ids)


def test_enumerate_candidates(base_state, repo):
    ov = Overlay(
        overlay_id="ov-sick",
        kind="sick",
        payload={"crew_id": "C-1042"},
        label="Capt Nair sick",
    )
    shadow = base_state.apply(ov)
    shadow_view = shadow.materialize()
    baseline_view = base_state.materialize()

    impact = compute_twin_diff(baseline_view, shadow_view, ov, repo)
    candidates = enumerate_candidates(impact, shadow, repo)

    candidate_ids = [c.crew_id for c in candidates]
    # C-3310 (on-base reserve) must be present
    assert "C-3310" in candidate_ids
    assert any(c.candidate_type == "off_base_deadhead" for c in candidates)

    # Find C-3310 option: should be legal
    opt_c3310 = next(c for c in candidates if c.crew_id == "C-3310")
    assert opt_c3310.ledger.legal is True
    assert opt_c3310.candidate_type == "on_base_reserve"

    # Find C-2087 option: companion evaluated with repair
    opt_c2087 = next((c for c in candidates if c.crew_id == "C-2087"), None)
    if opt_c2087:
        assert opt_c2087.repair is not None
        assert opt_c2087.repair.lever == "delay_departure"
