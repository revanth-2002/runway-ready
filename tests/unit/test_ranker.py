"""Unit tests for lexicographic ranker."""

import pytest
from advisor.data.repository import OpsRepository, DEFAULT_DB_PATH
from advisor.domain.state import OpsState, Overlay
from advisor.reasoning.candidates import enumerate_candidates
from advisor.reasoning.ranker import rank_recovery_options
from advisor.twin.diff import compute_twin_diff


@pytest.fixture
def repo():
    return OpsRepository()


@pytest.fixture
def base_state():
    return OpsState(db_path=DEFAULT_DB_PATH)


def test_rank_recovery_options(base_state, repo):
    ov = Overlay(
        overlay_id="ov-sick-test",
        kind="sick",
        payload={"crew_id": "C-1042"},
        label="Capt Nair sick",
    )
    shadow = base_state.apply(ov)
    shadow_view = shadow.materialize()
    baseline_view = base_state.materialize()

    impact = compute_twin_diff(baseline_view, shadow_view, ov, repo)
    rates = repo.get_cost_rates()
    candidates = enumerate_candidates(impact, shadow, repo, rates)
    ranked = rank_recovery_options(candidates, impact, rates, repo)

    assert len(ranked) >= 2
    # First option should be legal
    assert ranked[0].ledger.legal is True
    # The top recommended candidate should be C-3310 (legal on-base reserve with lowest INR cost)
    assert ranked[0].crew_id == "C-3310"

    # Last option must be DO_NOTHING benchmark card
    assert ranked[-1].crew_id == "DO_NOTHING"
    assert ranked[-1].candidate_type == "do_nothing"
    assert ranked[-1].cost.total_inr > ranked[0].cost.total_inr
