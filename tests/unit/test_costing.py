"""Unit tests for operational line-item costing."""

from advisor.domain.evidence import ImpactReport
from advisor.domain.types import Crew, DutyProposal, Flight
from advisor.reasoning.costing import compute_recovery_cost, compute_cancellation_benchmark


def test_compute_recovery_cost_on_base():
    crew = Crew(crew_id="C-3310", name="R. Sharma", rank="Captain", base="BLR")
    prop = DutyProposal(proposal_id="p-1", duty_minutes=480)  # 8 hours, no overtime
    rates = {
        "reserve_callout": 15000.0,
        "overtime_per_hour": 1400.0,
        "deadhead_base_fare": 12000.0,
        "delay_penalty_per_min": 1200.0,
    }
    cost = compute_recovery_cost(crew, prop, rates)
    assert cost.callout_fee == 15000.0
    assert cost.overtime_fee == 0.0
    assert cost.deadhead_fare == 0.0
    assert cost.total_inr == 15000.0
    assert len(cost.line_items) >= 1


def test_compute_recovery_cost_overtime_and_deadhead():
    crew = Crew(crew_id="C-4015", name="S. Sen", rank="Captain", base="DEL")
    prop = DutyProposal(proposal_id="p-2", duty_minutes=600)  # 10h -> 2h overtime @ 1400 = 2800
    rates = {
        "reserve_callout": 15000.0,
        "overtime_per_hour": 1400.0,
        "deadhead_base_fare": 12000.0,
        "deadhead_DEL_BLR": 22700.0,
        "delay_penalty_per_min": 1200.0,
    }
    cost = compute_recovery_cost(crew, prop, rates, is_deadhead=True, deadhead_fare=22700.0)
    assert cost.callout_fee == 15000.0
    assert cost.overtime_fee == 2800.0
    assert cost.deadhead_fare == 22700.0
    assert cost.total_inr == 15000.0 + 2800.0 + 22700.0


def test_compute_cancellation_benchmark():
    fl = Flight(
        flight_id="DX412",
        origin="BLR",
        destination="DEL",
        dep_utc="2026-09-15T10:30:00Z",
        arr_utc="2026-09-15T13:15:00Z",
        block_minutes=165,
        aircraft_type="A320",
        passengers=486,
    )
    impact = ImpactReport(
        disruption_id="d1",
        disrupted_crew_id="C-1042",
        broken_pairing_id="P-2291",
        uncrewed_flights=(fl,),
        delayed_rotations=(),
        stranded_companions=(),
        passengers_affected=486,
        source_rows=[],
    )
    rates = {
        "cancel_fixed_fee": 180000.0,
        "cancel_pax_comp": 3000.0,
    }
    cost = compute_cancellation_benchmark(impact, rates)
    assert cost.total_inr == 180000.0 + (486 * 3000.0)
    assert cost.total_inr == 1638000.0
