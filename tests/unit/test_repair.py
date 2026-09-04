"""Unit tests for minimal actionable repair lever calculation."""

from advisor.domain.evidence import LegalityLedger, RuleVerdict
from advisor.domain.types import DutyProposal
from advisor.reasoning.repair import compute_minimal_repair


def test_repair_duty_02_breach():
    verdict_duty = RuleVerdict(
        rule_id="RULE-DUTY-02",
        passed=False,
        headline="Exceeds 60h/7d limit by 1.5h",
        arithmetic="54.0h accrued + 7.5h proposed = 61.5h > 60.0h",
        inputs={},
        margin=-1.5,
        source_rows=[],
    )
    ledger = LegalityLedger(subject="C-2087", context="P-2291", verdicts=[verdict_duty])
    prop = DutyProposal(proposal_id="p-1", passengers=162)

    repair = compute_minimal_repair(ledger, prop)
    assert repair is not None
    assert repair.lever == "delay_departure"
    # -1.5h -> 90 minutes + 1 = 91 minutes
    assert repair.magnitude_minutes == 91
    assert repair.repaired_rule == "RULE-DUTY-02"
    assert repair.clears_binding is True
    assert "91m" in repair.side_effects


def test_repair_rest_04_breach():
    verdict_rest = RuleVerdict(
        rule_id="RULE-REST-04",
        passed=False,
        headline="Breaches 12h rest limit by 2.0h",
        arithmetic="10.0h rest < 12.0h required",
        inputs={},
        margin=-2.0,
        source_rows=[],
    )
    ledger = LegalityLedger(subject="C-2087", context="P-2291", verdicts=[verdict_rest])
    prop = DutyProposal(proposal_id="p-2")

    repair = compute_minimal_repair(ledger, prop)
    assert repair is not None
    assert repair.lever == "delay_departure"
    assert repair.magnitude_minutes == 121  # 2.0 * 60 + 1
    assert repair.repaired_rule == "RULE-REST-04"


def test_repair_passing_ledger():
    verdict_pass = RuleVerdict(
        rule_id="RULE-FDP-01",
        passed=True,
        headline="Legal",
        arithmetic="OK",
        inputs={},
        margin=2.0,
        source_rows=[],
    )
    ledger = LegalityLedger(subject="C-3310", context="P-2291", verdicts=[verdict_pass])
    assert compute_minimal_repair(ledger, DutyProposal(proposal_id="p-3")) is None
