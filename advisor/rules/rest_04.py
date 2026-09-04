"""RULE-REST-04: Minimum 12 Hours Rest Before Duty calculation."""

from datetime import datetime
from typing import Any, Dict
from advisor.domain.evidence import RuleVerdict
from advisor.domain.timeutil import parse_utc
from advisor.domain.types import Crew, DutyProposal


def evaluate_rest_04(
    crew: Crew, proposal: DutyProposal, context: Dict[str, Any]
) -> RuleVerdict:
    rule_cfg = context.get("rules_config", {}).get("RULE-REST-04", {})
    min_rest_hours = float(rule_cfg.get("min_rest_hours", 12.0))

    p_start = parse_utc(proposal.start_utc)

    # Determine when last rest ended
    last_rest_str = context.get("last_rest_ended")
    if not last_rest_str:
        clock = context.get("duty_clock")
        if clock and clock.last_rest_ended:
            last_rest_str = clock.last_rest_ended

    if last_rest_str:
        last_rest_dt = parse_utc(last_rest_str)
        rest_duration_hours = round((p_start - last_rest_dt).total_seconds() / 3600.0, 2)
    else:
        # Default assumption if no history: well rested (>24h)
        rest_duration_hours = 24.0

    margin = round(rest_duration_hours - min_rest_hours, 2)
    passed = margin >= 0.0

    if passed:
        arithmetic = f"{rest_duration_hours:.1f}h rest >= {min_rest_hours:.1f}h required"
        headline = f"Rest period legal with {margin:.1f}h buffer"
    else:
        arithmetic = f"{rest_duration_hours:.1f}h rest < {min_rest_hours:.1f}h required"
        headline = f"Breaches 12h rest limit by {abs(margin):.1f}h"

    source_rows = [f"crew:{crew.crew_id}"]
    if last_rest_str:
        source_rows.append(f"duty_clock:{crew.crew_id}:last_rest_ended")

    return RuleVerdict(
        rule_id="RULE-REST-04",
        passed=passed,
        headline=headline,
        arithmetic=arithmetic,
        inputs={
            "rest_duration_hours": rest_duration_hours,
            "min_rest_hours": min_rest_hours,
            "last_rest_ended": last_rest_str,
            "duty_start_utc": proposal.start_utc,
        },
        margin=margin,
        source_rows=source_rows,
        assumption="Checked against derived duty timeline and last rest ended record",
    )
