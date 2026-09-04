"""RULE-FDP-01: Maximum Flight Duty Period calculation."""

from datetime import datetime
from typing import Any, Dict, List, Tuple
from advisor.domain.evidence import RuleVerdict
from advisor.domain.timeutil import parse_utc
from advisor.domain.types import Crew, DutyProposal


def _split_into_duty_periods(proposal: DutyProposal) -> List[Tuple[datetime, datetime, int]]:
    """Splits proposal flights into distinct duty periods separated by >=8h ground rest."""
    if not proposal.flights:
        p_start = parse_utc(proposal.start_utc) if proposal.start_utc else parse_utc("2026-09-15T09:30:00Z")
        from datetime import timedelta
        p_end = parse_utc(proposal.end_utc) if proposal.end_utc else (p_start + timedelta(hours=8))
        return [(p_start, p_end, proposal.sectors)]

    periods = []
    curr_legs = [proposal.flights[0]]
    from datetime import timedelta
    for next_fl in proposal.flights[1:]:
        prev_arr = parse_utc(curr_legs[-1].arr_utc)
        next_dep = parse_utc(next_fl.dep_utc)
        if (next_dep - prev_arr).total_seconds() >= 8 * 3600:
            d_start = parse_utc(curr_legs[0].dep_utc) - timedelta(hours=1)
            d_end = parse_utc(curr_legs[-1].arr_utc) + timedelta(minutes=30)
            periods.append((d_start, d_end, len(curr_legs)))
            curr_legs = [next_fl]
        else:
            curr_legs.append(next_fl)

    d_start = parse_utc(curr_legs[0].dep_utc) - timedelta(hours=1)
    d_end = parse_utc(curr_legs[-1].arr_utc) + timedelta(minutes=30)
    periods.append((d_start, d_end, len(curr_legs)))
    return periods


def evaluate_fdp_01(
    crew: Crew, proposal: DutyProposal, context: Dict[str, Any]
) -> RuleVerdict:
    rule_cfg = context.get("rules_config", {}).get("RULE-FDP-01", {})
    base_limit = float(rule_cfg.get("base_limit_hours", 13.0))
    sector_penalty = float(rule_cfg.get("sector_penalty_hours", 0.5))
    night_penalty = float(rule_cfg.get("night_penalty_hours", 1.0))
    max_sectors_base = int(rule_cfg.get("max_sectors_base", 2))

    duty_periods = _split_into_duty_periods(proposal)
    worst_margin = 99999.0
    worst_period = None
    all_passed = True
    penalties_applied = []

    for p_start, p_end, sectors in duty_periods:
        if proposal.is_deadhead:
            sectors = 0
        duty_hours = round((p_end - p_start).total_seconds() / 3600.0, 2)
        effective_limit = base_limit
        period_penalties = []
        if sectors > max_sectors_base:
            extra_sectors = sectors - max_sectors_base
            deduction = extra_sectors * sector_penalty
            effective_limit -= deduction
            period_penalties.append(f"-{deduction:.1f}h for {extra_sectors} extra sector(s)")
        if 0 <= p_start.hour < 6:
            effective_limit -= night_penalty
            period_penalties.append(f"-{night_penalty:.1f}h for night report ({p_start.hour:02d}:{p_start.minute:02d} UTC)")

        margin = round(effective_limit - duty_hours, 2)
        if margin < worst_margin:
            worst_margin = margin
            worst_period = (duty_hours, effective_limit, margin, sectors, p_start.hour, period_penalties)
        if margin < 0:
            all_passed = False

    passed = all_passed
    duty_hours, effective_limit, margin, sectors, report_hour, penalties_applied = (
        worst_period or (0.0, base_limit, base_limit, 0, 0, [])
    )

    arithmetic = f"{duty_hours:.2f}h proposed FDP <= {effective_limit:.2f}h limit"
    if not passed:
        arithmetic = f"{duty_hours:.2f}h proposed FDP > {effective_limit:.2f}h limit"

    headline = (
        f"FDP legal with {margin:.2f}h buffer"
        if passed
        else f"Exceeds max FDP by {abs(margin):.2f}h"
    )

    source_rows = [f"crew:{crew.crew_id}"]
    if proposal.flight_id:
        source_rows.append(f"flight:{proposal.flight_id}")
    if proposal.pairing_id:
        source_rows.append(f"pairing:{proposal.pairing_id}")

    return RuleVerdict(
        rule_id="RULE-FDP-01",
        passed=passed,
        headline=headline,
        arithmetic=arithmetic,
        inputs={
            "duty_hours": duty_hours,
            "base_limit": base_limit,
            "effective_limit": effective_limit,
            "sectors": sectors,
            "report_hour_utc": report_hour,
            "penalties": penalties_applied,
        },
        margin=margin,
        source_rows=source_rows,
        assumption="Deadhead positioning does not count as operating sector" if proposal.is_deadhead else None,
    )
