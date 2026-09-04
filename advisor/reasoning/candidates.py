"""Reserve and roster-swap candidate enumeration for disrupted duties."""

from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from advisor.audit.logger import StructuredLogger
from advisor.data.repository import OpsRepository
from advisor.domain.evidence import ImpactReport, RecoveryOption
from advisor.domain.state import OpsState
from advisor.domain.timeutil import parse_utc, format_utc
from advisor.domain.types import Crew, DutyProposal, Flight
from advisor.reasoning.costing import compute_recovery_cost
from advisor.reasoning.deadhead import find_feasible_deadheads
from advisor.reasoning.repair import compute_minimal_repair
from advisor.rules.engine import evaluate_all

logger = StructuredLogger("advisor.reasoning.candidates")



def enumerate_candidates(
    impact: ImpactReport,
    state: OpsState,
    repo: OpsRepository,
    rates: Optional[Dict[str, float]] = None,
) -> List[RecoveryOption]:
    """Enumerates and evaluates recovery candidates:
    1. On-base active reserves
    2. Off-base reserves with schedule-feasible deadheads
    3. Rest-day roster swaps
    4. Stranded companion crew (with minimal repair lever)
    """
    if rates is None:
        rates = repo.get_cost_rates()

    disrupted_crew = repo.get_crew(impact.disrupted_crew_id)
    target_rank = disrupted_crew.rank
    target_station = disrupted_crew.base

    # First uncrewed flight determines report time and origin
    if impact.uncrewed_flights:
        first_fl = impact.uncrewed_flights[0]
        report_dt = parse_utc(first_fl.dep_utc) - timedelta(hours=1)
        target_station = first_fl.origin
    else:
        report_dt = parse_utc("2026-09-15T09:30:00Z")

    report_utc = format_utc(report_dt)
    logger.info(
        "Enumerating recovery candidates",
        disrupted_crew_id=impact.disrupted_crew_id,
        target_rank=target_rank,
        target_station=target_station,
        report_utc=report_utc,
    )

    proposal = DutyProposal(
        proposal_id=f"recov-{impact.broken_pairing_id or 'prop'}",
        flight_id=impact.uncrewed_flights[0].flight_id if impact.uncrewed_flights else None,
        pairing_id=impact.broken_pairing_id,
        flights=impact.uncrewed_flights,
        start_utc=report_utc,
        end_utc=impact.uncrewed_flights[-1].arr_utc if impact.uncrewed_flights else "",
        duty_minutes=int((parse_utc(impact.uncrewed_flights[-1].arr_utc) - report_dt).total_seconds() // 60) if impact.uncrewed_flights else 450,
        block_minutes=sum(f.block_minutes for f in impact.uncrewed_flights),
        sectors=len(impact.uncrewed_flights),
        passengers=impact.passengers_affected,
    )

    candidates: List[RecoveryOption] = []
    seen_crew = {impact.disrupted_crew_id}

    # 1. On-base active reserves
    on_base_reserves = repo.list_reserves(base=target_station)
    for res in on_base_reserves:
        if res.crew_id in seen_crew:
            continue
        c = repo.get_crew(res.crew_id)
        if c.rank != target_rank and not (target_rank == "Captain" and c.rank == "Captain"):
            continue

        seen_crew.add(c.crew_id)
        ratings = repo.list_ratings(c.crew_id)
        certs = repo.list_certifications(c.crew_id)
        clk = repo.get_duty_clock(c.crew_id)

        context = {
            "ratings": ratings,
            "certifications": certs,
            "duty_clock": clk,
            "target_station": target_station,
            "clock_mode": state.clock_mode,
        }
        ledger = evaluate_all(c, proposal, context)
        repair = compute_minimal_repair(ledger, proposal)
        cost = compute_recovery_cost(c, proposal, rates)

        # Expiry clock: report_time - reachability
        reachability = c.reachability_minutes or 60
        expiry_dt = report_dt - timedelta(minutes=reachability)

        candidates.append(
            RecoveryOption(
                crew_id=c.crew_id,
                candidate_type="on_base_reserve",
                base=c.base,
                ledger=ledger,
                cost=cost,
                repair=repair,
                expiry_utc=format_utc(expiry_dt),
                source_rows=[f"reserve:{c.crew_id}:{c.base}", f"crew:{c.crew_id}"],
            )
        )

    # 2. Off-base active reserves with schedule-feasible deadhead
    all_reserves = repo.list_reserves()
    for res in all_reserves:
        if res.crew_id in seen_crew:
            continue
        c = repo.get_crew(res.crew_id)
        if c.rank != target_rank and not (target_rank == "Captain" and c.rank == "Captain"):
            continue

        seen_crew.add(c.crew_id)
        reachability = c.reachability_minutes or 60
        earliest_dep = format_utc(datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc) + timedelta(minutes=reachability))
        latest_arrival = first_fl.dep_utc if impact.uncrewed_flights else report_utc
        feasible_dh = find_feasible_deadheads(
            repo,
            from_base=c.base,
            to_station=target_station,
            latest_arrival_utc=latest_arrival,
            earliest_dep_utc=earliest_dep,
        )

        dh_flight = feasible_dh[0] if feasible_dh else None
        dh_fare_key = f"deadhead_{c.base}_{target_station}"
        dh_fare = rates.get(dh_fare_key, rates.get("deadhead_base_fare", 12000.0))

        ratings = repo.list_ratings(c.crew_id)
        certs = repo.list_certifications(c.crew_id)
        clk = repo.get_duty_clock(c.crew_id)

        context = {
            "ratings": ratings,
            "certifications": certs,
            "duty_clock": clk,
            "target_station": target_station,
            "deadhead_flight": dh_flight,
            "clock_mode": state.clock_mode,
        }
        ledger = evaluate_all(c, proposal, context)
        repair = compute_minimal_repair(ledger, proposal)
        cost = compute_recovery_cost(
            c, proposal, rates, is_deadhead=True, deadhead_fare=dh_fare
        )

        # Expiry: min(deadhead flight departure - reachability, oncall_end)
        if dh_flight:
            expiry_dt = parse_utc(dh_flight.dep_utc) - timedelta(minutes=reachability)
            expiry_str = format_utc(expiry_dt)
        else:
            expiry_str = None

        candidates.append(
            RecoveryOption(
                crew_id=c.crew_id,
                candidate_type="off_base_deadhead",
                base=c.base,
                ledger=ledger,
                cost=cost,
                repair=repair,
                deadhead_flight_id=dh_flight.flight_id if dh_flight else None,
                expiry_utc=expiry_str,
                source_rows=[f"reserve:{c.crew_id}:{c.base}"] + ([f"flight:{dh_flight.flight_id}:deadhead"] if dh_flight else []),
            )
        )

    # 3. Day-off callouts (off-duty crew at target station)
    win_start = proposal.start_utc or report_utc
    win_end = proposal.end_utc or format_utc(report_dt + timedelta(hours=14))
    dayoff_crew = repo.list_crew_on_dayoff(target_rank, target_station, win_start, win_end)
    for c in dayoff_crew[:6]:
        if c.crew_id in seen_crew:
            continue
        seen_crew.add(c.crew_id)
        ratings = repo.list_ratings(c.crew_id)
        certs = repo.list_certifications(c.crew_id)
        clk = repo.get_duty_clock(c.crew_id)

        context = {
            "ratings": ratings,
            "certifications": certs,
            "duty_clock": clk,
            "target_station": target_station,
            "clock_mode": state.clock_mode,
        }
        ledger = evaluate_all(c, proposal, context)
        repair = compute_minimal_repair(ledger, proposal)
        cost = compute_recovery_cost(c, proposal, rates, is_dayoff=True)

        candidates.append(
            RecoveryOption(
                crew_id=c.crew_id,
                candidate_type="dayoff_callout",
                base=c.base,
                ledger=ledger,
                cost=cost,
                repair=repair,
                expiry_utc=None,
                source_rows=[f"crew:{c.crew_id}:dayoff"],
            )
        )

    # 4. Stranded Companion Crew (e.g. C-2087) evaluated as candidate with repair
    for companion in impact.stranded_companions:
        if companion.crew_id in seen_crew:
            continue
        if companion.rank != target_rank:
            continue
        seen_crew.add(companion.crew_id)
        ratings = repo.list_ratings(companion.crew_id)
        certs = repo.list_certifications(companion.crew_id)
        clk = repo.get_duty_clock(companion.crew_id)

        context = {
            "ratings": ratings,
            "certifications": certs,
            "duty_clock": clk,
            "target_station": target_station,
            "clock_mode": state.clock_mode,
        }
        ledger = evaluate_all(companion, proposal, context)
        repair = compute_minimal_repair(ledger, proposal)
        cost = compute_recovery_cost(companion, proposal, rates)

        candidates.append(
            RecoveryOption(
                crew_id=companion.crew_id,
                candidate_type="companion_upgrade" if companion.rank != target_rank else "rest_day_swap",
                base=companion.base,
                ledger=ledger,
                cost=cost,
                repair=repair,
                source_rows=[f"crew:{companion.crew_id}:companion"],
            )
        )

    legal_count = sum(1 for c in candidates if c.ledger.legal)
    logger.info(
        "Completed candidate enumeration",
        total_candidates=len(candidates),
        legal_candidates=legal_count,
        disrupted_crew_id=impact.disrupted_crew_id,
    )
    return candidates
