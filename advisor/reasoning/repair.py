from typing import Optional
from advisor.audit.logger import StructuredLogger
from advisor.domain.evidence import LegalityLedger, RepairOption
from advisor.domain.types import DutyProposal

logger = StructuredLogger("advisor.reasoning.repair")



def compute_minimal_repair(
    ledger: LegalityLedger, proposal: DutyProposal
) -> Optional[RepairOption]:
    """Inverts the single binding regulatory constraint to calculate the minimal operational lever.
    For example: delaying departure by N minutes to satisfy mandatory rest or clear rolling duty.
    """
    breach = ledger.binding_breach
    if not breach:
        return None

    res = None
    if breach.rule_id == "RULE-DUTY-02":
        # Over 60h duty limit by N hours
        over_minutes = int(abs(breach.margin) * 60) + 1
        pax_count = proposal.passengers or 162
        res = RepairOption(
            lever="delay_departure",
            magnitude_minutes=over_minutes,
            repaired_rule="RULE-DUTY-02",
            clears_binding=True,
            side_effects=f"Delays departure by {over_minutes}m; impacts {pax_count} passengers",
        )

    elif breach.rule_id == "RULE-REST-04":
        # Rest shortfall by N hours
        shortfall_minutes = int(abs(breach.margin) * 60) + 1
        res = RepairOption(
            lever="delay_departure",
            magnitude_minutes=shortfall_minutes,
            repaired_rule="RULE-REST-04",
            clears_binding=True,
            side_effects=f"Pushes report back by {shortfall_minutes}m to satisfy mandatory 12h rest",
        )

    if res:
        logger.info(
            "Computed minimal actionable repair lever",
            crew_id=ledger.subject,
            lever=res.lever,
            magnitude_minutes=res.magnitude_minutes,
            repaired_rule=res.repaired_rule,
        )
    return res
