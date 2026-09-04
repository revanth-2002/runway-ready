"""Lexicographic option ranker with Do-Nothing cancellation benchmark."""

from typing import Dict, List, Optional
from advisor.audit.logger import StructuredLogger
from advisor.data.repository import OpsRepository
from advisor.domain.evidence import ImpactReport, LegalityLedger, RecoveryOption
from advisor.reasoning.costing import compute_cancellation_benchmark

logger = StructuredLogger("advisor.reasoning.ranker")



def rank_recovery_options(
    candidates: List[RecoveryOption],
    impact: ImpactReport,
    rates: Dict[str, float],
    repo: Optional[OpsRepository] = None,
) -> List[RecoveryOption]:
    """Ranks options by lexicographic priority:
    1. Legal before illegal
    2. Lowest total INR cost
    3. Seniority (if available)
    Appends the Do-Nothing benchmark card at the end.
    """
    def sort_key(opt: RecoveryOption):
        # 1. Legal first (0 = legal, 1 = illegal)
        is_legal = 0 if opt.ledger.legal else 1
        # 2. Total cost
        cost = opt.cost.total_inr
        # 3. Seniority (higher seniority preferred, so negative seniority)
        seniority = 0
        if repo:
            try:
                c = repo.get_crew(opt.crew_id)
                seniority = -(c.seniority or 0)
            except Exception:
                pass
        return (is_legal, cost, seniority)

    ranked = sorted(candidates, key=sort_key)

    # Append Do-Nothing / Cancellation benchmark card
    cancel_cost = compute_cancellation_benchmark(impact, rates)
    do_nothing_ledger = LegalityLedger(
        subject="OPERATIONS",
        context="DO_NOTHING_BENCHMARK",
        verdicts=[],
    )
    do_nothing_option = RecoveryOption(
        crew_id="DO_NOTHING",
        candidate_type="do_nothing",
        base="SYSTEM",
        ledger=do_nothing_ledger,
        cost=cancel_cost,
        repair=None,
        deadhead_flight_id=None,
        expiry_utc=None,
        source_rows=["system:cancellation_benchmark"],
    )

    ranked.append(do_nothing_option)
    logger.info(
        "Ranked recovery options",
        candidate_count=len(candidates),
        top_candidate=ranked[0].crew_id if ranked else None,
        top_cost=ranked[0].cost.total_inr if ranked else None,
    )
    return ranked
