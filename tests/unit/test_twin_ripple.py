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


def test_reassign_overlay_and_recovery(base_state, repo):
    """Verify that applying a reassign overlay resolves broken pairings and marks reserve crew as CALLED."""
    # 1. Sick disruption
    ov_sick = Overlay(
        overlay_id="ov-sick-c1042",
        kind="sick",
        payload={"crew_id": "C-1042", "date": "2026-09-15"},
        label="Captain Nair Sick",
    )
    disrupted_state = base_state.apply(ov_sick)
    disrupted_view = disrupted_state.materialize()
    first_flight = next(fid for fid in disrupted_view.flight_statuses if "DX412" in fid)
    assert disrupted_view.flight_statuses[first_flight] == "UNCREWED"
    assert disrupted_view.crew["C-3310"].assigned_pairing_id is None

    # 2. Reassign overlay (finalizing recommendation)
    ov_reassign = Overlay(
        overlay_id="ov-reassign-c3310",
        kind="reassign",
        payload={
            "replacement_crew_id": "C-3310",
            "disrupted_crew_id": "C-1042",
            "pairing_id": "P-2291",
            "flight_ids": ["DX412"],
            "cost_inr": 18500.0,
        },
        label="Reassigned C-3310 to P-2291",
    )
    recovered_state = disrupted_state.apply(ov_reassign)
    recovered_view = recovered_state.materialize()

    # 3. Verify digital twin state holds the changes
    assert recovered_view.crew["C-3310"].assigned_pairing_id == "P-2291"
    assert recovered_view.crew["C-3310"].on_call_status == "CALLED"
    assert recovered_view.crew["C-1042"].is_incapacitated is True
    assert recovered_view.crew["C-1042"].assigned_pairing_id is None

    # 4. Verify uncrewed flight is restored to ON_TIME
    assert recovered_view.flight_statuses[first_flight] == "ON_TIME"
