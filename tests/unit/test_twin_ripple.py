"""Unit tests for Operational Digital Twin ripple and diff engine."""

import pytest
from advisor.data.repository import OpsRepository, DEFAULT_DB_PATH
from advisor.domain.state import OpsState, Overlay
from advisor.twin.diff import compute_twin_diff


@pytest.fixture
def repo():
    return OpsRepository()


@pytest.fixture
def base_state():
    return OpsState(db_path=DEFAULT_DB_PATH)


def test_ops_state_immutability(base_state):
    assert len(base_state.overlays) == 0

    ov = Overlay(
        overlay_id="ov-1",
        kind="sick",
        payload={"crew_id": "C-1042"},
        label="Capt Nair sick",
    )
    shadow = base_state.apply(ov)
    assert len(shadow.overlays) == 1
    assert len(base_state.overlays) == 0

    popped = shadow.pop()
    assert len(popped.overlays) == 0


def test_twin_sick_crew_propagation(base_state, repo):
    # Materialize baseline
    baseline_view = base_state.materialize()
    first_flight = next(fid for fid in baseline_view.flight_statuses if "DX412" in fid)
    assert baseline_view.flight_statuses[first_flight] == "ON_TIME"
    assert baseline_view.crew["C-1042"].is_incapacitated is False

    # Inject sick callout for C-1042
    ov = Overlay(
        overlay_id="ov-sick-c1042",
        kind="sick",
        payload={"crew_id": "C-1042", "date": "2026-09-15"},
        label="Captain Nair Sick Callout",
    )
    shadow_state = base_state.apply(ov)
    shadow_view = shadow_state.materialize()

    # Verify crew status
    assert shadow_view.crew["C-1042"].is_incapacitated is True

    # Verify uncrewed flight along pairing P-2291
    assert shadow_view.flight_statuses[first_flight] == "UNCREWED"

    # Compute diff
    impact = compute_twin_diff(baseline_view, shadow_view, ov, repo)
    assert impact.disrupted_crew_id == "C-1042"
    assert impact.broken_pairing_id == "P-2291"
    assert len(impact.uncrewed_flights) in (3, 6)
    assert [f.flight_id.split("-")[0] for f in impact.uncrewed_flights[:3]] == ["DX412", "DX413", "DX588"]

    # Verify companion crew is stranded
    companion_ids = [c.crew_id for c in impact.stranded_companions]
    assert len(companion_ids) > 0

    # Verify passenger impact
    assert impact.passengers_affected > 0
    assert any("crew:C-1042" in s for s in impact.source_rows)
    assert any("DX412" in s for s in impact.source_rows)
