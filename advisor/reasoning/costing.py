from typing import Dict, List, Optional
from advisor.audit.logger import StructuredLogger
from advisor.domain.evidence import CostBreakdown, ImpactReport
from advisor.domain.types import Crew, DutyProposal

logger = StructuredLogger("advisor.reasoning.costing")



def compute_recovery_cost(
    candidate: Crew,
    proposal: DutyProposal,
    rates: Dict[str, float],
    is_deadhead: bool = False,
    deadhead_fare: float = 0.0,
    delay_minutes: int = 0,
    is_dayoff: bool = False,
) -> CostBreakdown:
    """Calculates explicit line-item costs for assigning a candidate."""
    line_items: List[str] = []

    # 1. Base callout fee
    is_pilot = candidate.rank in ("Captain", "First Officer")
    if is_dayoff:
        callout_fee = (
            rates.get("dayoff_callout_pilot", 24000.0)
            if is_pilot
            else rates.get("dayoff_callout_cabin", 12500.0)
        )
        line_items.append(f"Day-off callout fee: ₹{int(callout_fee):,} (dayoff_callout)")
    else:
        if is_pilot:
            callout_fee = rates.get("reserve_callout_pilot", rates.get("reserve_callout", 18500.0))
        else:
            callout_fee = rates.get("reserve_callout_cabin", rates.get("reserve_callout", 9500.0))
        line_items.append(f"Base callout fee: ₹{int(callout_fee):,} (reserve_callout)")

    # 2. Overtime fee (for duty exceeding 8 hours if overtime rate is defined)
    duty_hours = (proposal.duty_minutes or 450) / 60.0
    overtime_fee = 0.0
    ot_rate = rates.get("overtime_per_hour", 0.0)
    if duty_hours > 8.0 and ot_rate > 0.0:
        ot_hours = duty_hours - 8.0
        overtime_fee = round(ot_hours * ot_rate, 2)
        line_items.append(f"Overtime ({ot_hours:.1f}h @ ₹{int(ot_rate):,}/h): ₹{int(overtime_fee):,}")

    # 3. Deadhead fare
    actual_deadhead_fare = 0.0
    if is_deadhead:
        actual_deadhead_fare = (
            deadhead_fare
            if deadhead_fare > 0
            else rates.get("deadhead_positioning", rates.get("deadhead_base_fare", 6500.0))
        )
        line_items.append(f"Deadhead positioning fare: ₹{int(actual_deadhead_fare):,}")

    # 4. Delay penalty
    delay_penalty = 0.0
    if delay_minutes > 0:
        if "delay_cost_per_duty_hour" in rates:
            penalty_rate = rates["delay_cost_per_duty_hour"] / 60.0
        else:
            penalty_rate = rates.get("delay_penalty_per_min", 90.0)
        delay_penalty = round(delay_minutes * penalty_rate, 2)
        line_items.append(f"Delay penalty ({delay_minutes}m @ ₹{int(penalty_rate):,}/m): ₹{int(delay_penalty):,}")

    total_inr = callout_fee + overtime_fee + actual_deadhead_fare + delay_penalty
    breakdown = CostBreakdown(
        callout_fee=callout_fee,
        overtime_fee=overtime_fee,
        deadhead_fare=actual_deadhead_fare,
        delay_penalty=delay_penalty,
        total_inr=total_inr,
        line_items=line_items,
    )
    logger.debug(
        "Computed recovery cost breakdown",
        crew_id=candidate.crew_id,
        total_inr=total_inr,
        callout_fee=callout_fee,
        is_dayoff=is_dayoff,
        is_deadhead=is_deadhead,
    )
    return breakdown



def compute_cancellation_benchmark(
    impact: ImpactReport,
    rates: Dict[str, float],
) -> CostBreakdown:
    """Calculates the Do-Nothing / Cancellation baseline cost."""
    line_items: List[str] = []
    per_flight_fee = rates.get("cancellation_per_flight", rates.get("cancel_fixed_fee", 250000.0))
    flight_count = len(impact.uncrewed_flights) or (1 if impact.broken_pairing_id else 1)
    cancel_fixed_fee = per_flight_fee * flight_count
    line_items.append(f"Base cancellation & disruption penalty ({flight_count} legs @ ₹{int(per_flight_fee):,}): ₹{int(cancel_fixed_fee):,}")

    pax_comp_rate = rates.get("cancel_pax_comp", 0.0)
    pax_count = impact.passengers_affected or 0
    total_pax_comp = pax_count * pax_comp_rate
    if total_pax_comp > 0:
        line_items.append(f"Passenger compensation ({pax_count} pax @ ₹{int(pax_comp_rate):,}/pax): ₹{int(total_pax_comp):,}")

    total_inr = cancel_fixed_fee + total_pax_comp
    return CostBreakdown(
        callout_fee=0.0,
        overtime_fee=0.0,
        deadhead_fare=0.0,
        delay_penalty=0.0,
        total_inr=total_inr,
        line_items=line_items,
    )
