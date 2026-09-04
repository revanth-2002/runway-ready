"""RULE-DUTY-02: Maximum 60 Duty Hours in 7 Days calculation."""

from datetime import datetime
from typing import Any, Dict
from advisor.domain.evidence import RuleVerdict
from advisor.domain.timeutil import parse_utc, rolling_window_duty_hours
from advisor.domain.types import Crew, DutyProposal


def evaluate_duty_02(
    crew: Crew, proposal: DutyProposal, context: Dict[str, Any]
) -> RuleVerdict:
    rule_cfg = context.get("rules_config", {}).get("RULE-DUTY-02", {})
    limit_7d = float(rule_cfg.get("limit_hours_7d", 60.0))

    p_start = parse_utc(proposal.start_utc) if proposal.start_utc else parse_utc("2026-09-15T09:30:00Z")
    if proposal.end_utc:
        p_end = parse_utc(proposal.end_utc)
    else:
        from datetime import timedelta
        p_end = p_start + timedelta(hours=8)
    proposed_hours = round((p_end - p_start).total_seconds() / 3600.0, 2)

    # Use historical duties if provided; fallback to duty_clock scalar
    historical_duties = context.get("historical_duties", [])
    clock = context.get("duty_clock")

    clock_mode = context.get("clock_mode", "reconciled")
    if clock_mode == "scalar_anchored" and clock and clock.duty_hours_7d is not None:
        accrued_hours = float(clock.duty_hours_7d)
        source_note = "duty_clock scalar"
    elif historical_duties:
        accrued_hours = rolling_window_duty_hours(historical_duties, p_end, window_days=7)
        source_note = "derived historical duty intervals"
    elif clock and clock.duty_hours_7d is not None:
        accrued_hours = float(clock.duty_hours_7d)
        source_note = "duty_clock fallback"
    else:
        accrued_hours = 0.0
        source_note = "zero baseline"

    total_hours = round(accrued_hours + proposed_hours, 2)
    margin = round(limit_7d - total_hours, 2)
    passed = margin >= 0.0

    if passed:
        arithmetic = f"{accrued_hours:.1f}h accrued + {proposed_hours:.1f}h proposed = {total_hours:.1f}h <= {limit_7d:.1f}h"
        headline = f"7-day duty legal with {margin:.1f}h buffer"
    else:
        arithmetic = f"{accrued_hours:.1f}h accrued + {proposed_hours:.1f}h proposed = {total_hours:.1f}h > {limit_7d:.1f}h"
        headline = f"Exceeds 60h/7d limit by {abs(margin):.1f}h"

    source_rows = [f"crew:{crew.crew_id}", f"duty_clock:{crew.crew_id}"]
    if proposal.pairing_id:
        source_rows.append(f"pairing:{proposal.pairing_id}")

    return RuleVerdict(
        rule_id="RULE-DUTY-02",
        passed=passed,
        headline=headline,
        arithmetic=arithmetic,
        inputs={
            "accrued_hours_7d": accrued_hours,
            "proposed_hours": proposed_hours,
            "total_hours": total_hours,
            "limit_7d": limit_7d,
            "source_mode": source_note,
        },
        margin=margin,
        source_rows=source_rows,
        assumption="Boundary-straddling duties pro-rated by fractional minute overlap",
    )
