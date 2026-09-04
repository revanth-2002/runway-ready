"""Evidence models, ledgers, verdicts, and recovery options."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple
from advisor.domain.types import Crew, Flight


@dataclass(frozen=True)
class RuleVerdict:
    rule_id: str
    passed: bool
    headline: str
    arithmetic: str
    inputs: Dict[str, Any]
    margin: float  # Signed margin (+: buffer in hours, -: breach in hours)
    source_rows: List[str]
    assumption: Optional[str] = None


@dataclass(frozen=True)
class LegalityLedger:
    subject: str  # crew_id
    context: str  # proposed duty / pairing / flight
    verdicts: List[RuleVerdict]

    @property
    def legal(self) -> bool:
        return all(v.passed for v in self.verdicts)

    @property
    def breaches(self) -> List[RuleVerdict]:
        return [v for v in self.verdicts if not v.passed]

    @property
    def binding_breach(self) -> Optional[RuleVerdict]:
        """Returns the breach with the largest negative margin (most severe constraint)."""
        failing = self.breaches
        if not failing:
            return None
        return min(failing, key=lambda v: v.margin)


@dataclass(frozen=True)
class CostBreakdown:
    callout_fee: float
    overtime_fee: float
    deadhead_fare: float
    delay_penalty: float
    total_inr: float
    line_items: List[str]


@dataclass(frozen=True)
class RepairOption:
    lever: Literal["delay_departure", "advance_deadhead"]
    magnitude_minutes: int
    repaired_rule: str
    clears_binding: bool
    side_effects: str


@dataclass(frozen=True)
class RecoveryOption:
    crew_id: str
    candidate_type: str  # "on_base_reserve", "off_base_deadhead", "rest_day_swap", "do_nothing"
    base: str
    ledger: LegalityLedger
    cost: CostBreakdown
    repair: Optional[RepairOption] = None
    deadhead_flight_id: Optional[str] = None
    expiry_utc: Optional[str] = None
    source_rows: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ImpactReport:
    disruption_id: str
    disrupted_crew_id: str
    broken_pairing_id: str
    uncrewed_flights: Tuple[Flight, ...]
    delayed_rotations: Tuple[Dict[str, Any], ...]
    stranded_companions: Tuple[Crew, ...]
    passengers_affected: int
    source_rows: List[str]
    confidence: str = "HIGH"


@dataclass(frozen=True)
class EvidenceBundle:
    impact: ImpactReport
    ledger: LegalityLedger
    options: Tuple[RecoveryOption, ...]
    source_rows: List[str]
