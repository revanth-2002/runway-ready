"""RULE-BASE-07: Reserve Base Alignment & Deadhead Feasibility calculation."""

from datetime import datetime
from typing import Any, Dict
from advisor.domain.evidence import RuleVerdict
from advisor.domain.timeutil import parse_utc
from advisor.domain.types import Crew, DutyProposal, Flight


def evaluate_base_07(
    crew: Crew, proposal: DutyProposal, context: Dict[str, Any]
) -> RuleVerdict:
    rule_cfg = context.get("rules_config", {}).get("RULE-BASE-07", {})
    deadhead_buffer_minutes = int(rule_cfg.get("deadhead_buffer_minutes", 30))

    # Duty station
    origin_station = (
        proposal.flights[0].origin
        if proposal.flights
        else context.get("target_station", crew.base)
    )

    report_dt = parse_utc(proposal.start_utc) if proposal.start_utc else parse_utc("2026-09-15T09:30:00Z")

    # Check 1: On-base alignment
    if crew.base == origin_station:
        reachability = crew.reachability_minutes or 60
        margin = float(reachability) / 60.0
        return RuleVerdict(
            rule_id="RULE-BASE-07",
            passed=True,
            headline=f"On-base reserve at {origin_station}",
            arithmetic=f"Base {crew.base} matches flight origin {origin_station}",
            inputs={
                "base": crew.base,
                "origin": origin_station,
                "reachability_minutes": reachability,
            },
            margin=margin,
            source_rows=[f"crew:{crew.crew_id}", f"station:{origin_station}"],
            assumption="Local on-base reserve with callout reachability buffer",
        )

    # Check 2: Off-base with feasible scheduled deadhead
    deadhead_flight: Flight = context.get("deadhead_flight")
    if deadhead_flight:
        f_arr = parse_utc(deadhead_flight.arr_utc)
        earliest_available = f_arr.timestamp() + (deadhead_buffer_minutes * 60)
        report_timestamp = report_dt.timestamp()
        slack_seconds = report_timestamp - earliest_available
        margin_hours = round(slack_seconds / 3600.0, 2)
        passed = slack_seconds >= 0

        if passed:
            arithmetic = f"Deadhead {deadhead_flight.flight_id} arrives {deadhead_flight.arr_utc} + {deadhead_buffer_minutes}m <= report {proposal.start_utc}"
            headline = f"Feasible deadhead on {deadhead_flight.flight_id} ({margin_hours:.1f}h buffer)"
        else:
            arithmetic = f"Deadhead {deadhead_flight.flight_id} arrives {deadhead_flight.arr_utc} + {deadhead_buffer_minutes}m > report {proposal.start_utc}"
            headline = f"Infeasible deadhead: arrives {abs(margin_hours):.1f}h too late for report"

        return RuleVerdict(
            rule_id="RULE-BASE-07",
            passed=passed,
            headline=headline,
            arithmetic=arithmetic,
            inputs={
                "base": crew.base,
                "origin": origin_station,
                "deadhead_flight": deadhead_flight.flight_id,
                "arr_utc": deadhead_flight.arr_utc,
                "buffer_minutes": deadhead_buffer_minutes,
                "report_utc": proposal.start_utc,
            },
            margin=margin_hours,
            source_rows=[
                f"crew:{crew.crew_id}",
                f"flight:{deadhead_flight.flight_id}",
                f"station:{origin_station}",
            ],
            assumption=f"Requires {deadhead_buffer_minutes}-minute turnaround buffer after deadhead arrival",
        )

    # No deadhead found for off-base crew
    return RuleVerdict(
        rule_id="RULE-BASE-07",
        passed=False,
        headline=f"Off-base at {crew.base} with no connecting flight to {origin_station}",
        arithmetic=f"Base {crew.base} != origin {origin_station}, no scheduled deadhead available",
        inputs={"base": crew.base, "origin": origin_station},
        margin=-99.0,
        source_rows=[f"crew:{crew.crew_id}", f"station:{origin_station}"],
        assumption="Off-base reserve requires scheduled passenger flight connecting to base",
    )
