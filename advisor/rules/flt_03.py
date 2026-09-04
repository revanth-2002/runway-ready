"""RULE-FLT-03: Maximum 100 Flight Hours in 28 Days calculation."""

from datetime import datetime
from typing import Any, Dict
from advisor.domain.evidence import RuleVerdict
from advisor.domain.timeutil import parse_utc, rolling_window_block_hours
from advisor.domain.types import Crew, DutyProposal


def evaluate_flt_03(
    crew: Crew, proposal: DutyProposal, context: Dict[str, Any]
) -> RuleVerdict:
    rule_cfg = context.get("rules_config", {}).get("RULE-FLT-03", {})
    limit_28d = float(rule_cfg.get("limit_hours_28d", 100.0))

    # Deadhead legs contribute 0.0 block hours
    if proposal.is_deadhead:
        proposed_block_hours = 0.0
    else:
        proposed_block_hours = round(proposal.block_minutes / 60.0, 2)

    clock = context.get("duty_clock")
    historical_flights = context.get("historical_flights", [])

    if clock and clock.flight_hours_28d is not None:
        accrued_block_hours = float(clock.flight_hours_28d)
    elif historical_flights:
        p_end = parse_utc(proposal.end_utc)
        accrued_block_hours = rolling_window_block_hours(historical_flights, p_end, window_days=28)
    else:
        accrued_block_hours = 0.0

    total_block_hours = round(accrued_block_hours + proposed_block_hours, 2)
    margin = round(limit_28d - total_block_hours, 2)
    passed = margin >= 0.0

    if passed:
        arithmetic = f"{accrued_block_hours:.1f}h accrued + {proposed_block_hours:.1f}h proposed = {total_block_hours:.1f}h <= {limit_28d:.1f}h"
        headline = f"28-day flight hours legal with {margin:.1f}h buffer"
    else:
        arithmetic = f"{accrued_block_hours:.1f}h accrued + {proposed_block_hours:.1f}h proposed = {total_block_hours:.1f}h > {limit_28d:.1f}h"
        headline = f"Exceeds 100h/28d flight limit by {abs(margin):.1f}h"

    source_rows = [f"crew:{crew.crew_id}", f"duty_clock:{crew.crew_id}"]
    if proposal.flight_id:
        source_rows.append(f"flight:{proposal.flight_id}")

    return RuleVerdict(
        rule_id="RULE-FLT-03",
        passed=passed,
        headline=headline,
        arithmetic=arithmetic,
        inputs={
            "accrued_block_hours_28d": accrued_block_hours,
            "proposed_block_hours": proposed_block_hours,
            "total_block_hours": total_block_hours,
            "limit_28d": limit_28d,
            "is_deadhead": proposal.is_deadhead,
        },
        margin=margin,
        source_rows=source_rows,
        assumption="Deadhead positioning block time is excluded from flight limits",
    )
