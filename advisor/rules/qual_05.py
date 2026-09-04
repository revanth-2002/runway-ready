"""RULE-QUAL-05: Aircraft Type Rating verification."""

from typing import Any, Dict
from advisor.domain.evidence import RuleVerdict
from advisor.domain.types import Crew, DutyProposal


def evaluate_qual_05(
    crew: Crew, proposal: DutyProposal, context: Dict[str, Any]
) -> RuleVerdict:
    crew_ratings = context.get("ratings", [])

    # Check aircraft types across all proposed flights
    required_types = set()
    if proposal.flights:
        for f in proposal.flights:
            required_types.add(f.aircraft_type)
    else:
        req_type = context.get("required_aircraft_type", "A320")
        required_types.add(req_type)

    missing_ratings = [t for t in required_types if t not in crew_ratings]
    passed = len(missing_ratings) == 0

    if passed:
        arithmetic = f"Rated for {', '.join(required_types)} in {crew_ratings}"
        headline = f"Qualified on aircraft type: {', '.join(required_types)}"
        margin = 1.0
    else:
        arithmetic = f"Missing ratings: {missing_ratings}; crew holds: {crew_ratings}"
        headline = f"Unqualified: lacks type rating for {', '.join(missing_ratings)}"
        margin = -1.0

    source_rows = [f"crew_rating:{crew.crew_id}"]
    for t in required_types:
        source_rows.append(f"fleet_type:{t}")

    return RuleVerdict(
        rule_id="RULE-QUAL-05",
        passed=passed,
        headline=headline,
        arithmetic=arithmetic,
        inputs={
            "required_types": list(required_types),
            "crew_ratings": crew_ratings,
            "missing_ratings": missing_ratings,
        },
        margin=margin,
        source_rows=source_rows,
        assumption="Exact string match on aircraft family type rating",
    )
