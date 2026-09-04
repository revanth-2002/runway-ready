"""Pure rules engine orchestrating evaluation of all 7 regulations."""

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from advisor.audit.logger import StructuredLogger
from advisor.domain.evidence import LegalityLedger, RuleVerdict
from advisor.domain.types import Crew, DutyProposal
from advisor.rules.base_07 import evaluate_base_07
from advisor.rules.cert_06 import evaluate_cert_06
from advisor.rules.duty_02 import evaluate_duty_02
from advisor.rules.fdp_01 import evaluate_fdp_01
from advisor.rules.flt_03 import evaluate_flt_03
from advisor.rules.qual_05 import evaluate_qual_05
from advisor.rules.rest_04 import evaluate_rest_04

DEFAULT_RULES_FILE = Path(__file__).resolve().parent.parent.parent / "crew-ops-advisor-dataset" / "data" / "rules.json"
logger = StructuredLogger("advisor.rules.engine")


RULE_REGISTRY: List[Callable[[Crew, DutyProposal, Dict[str, Any]], RuleVerdict]] = [
    evaluate_fdp_01,
    evaluate_duty_02,
    evaluate_flt_03,
    evaluate_rest_04,
    evaluate_qual_05,
    evaluate_cert_06,
    evaluate_base_07,
]


def load_rules_config(rules_path: Path = DEFAULT_RULES_FILE) -> Dict[str, Any]:
    """Loads rule threshold configurations from rules.json."""
    if rules_path.exists():
        with rules_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def evaluate_all(
    crew: Crew,
    proposal: DutyProposal,
    context: Dict[str, Any],
    rules_config: Optional[Dict[str, Any]] = None,
) -> LegalityLedger:
    """Evaluates all 7 regulatory rules against a crew member and duty proposal."""
    if rules_config is None:
        rules_config = load_rules_config()

    full_context = dict(context)
    full_context["rules_config"] = rules_config

    verdicts: List[RuleVerdict] = []
    for rule_fn in RULE_REGISTRY:
        verdict = rule_fn(crew, proposal, full_context)
        logger.debug(
            "Rule evaluated",
            rule_id=verdict.rule_id,
            passed=verdict.passed,
            crew_id=crew.crew_id,
            headline=verdict.headline,
        )
        verdicts.append(verdict)


    failed = [v.rule_id for v in verdicts if not v.passed]
    if failed:
        logger.warning(
            "Rule breaches detected during evaluation",
            crew_id=crew.crew_id,
            proposal_id=proposal.proposal_id or proposal.pairing_id,
            breached_rules=failed,
        )
    else:
        logger.debug(
            "All regulatory rules passed",
            crew_id=crew.crew_id,
            proposal_id=proposal.proposal_id or proposal.pairing_id,
        )

    return LegalityLedger(
        subject=crew.crew_id,
        context=proposal.proposal_id or proposal.pairing_id or proposal.flight_id or "duty",
        verdicts=verdicts,
    )
