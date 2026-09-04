"""Unit tests for slot-token substitution and anti-hallucination guard."""

from advisor.domain.evidence import (
    CostBreakdown,
    ImpactReport,
    LegalityLedger,
    RecoveryOption,
    RepairOption,
    RuleVerdict,
)
from advisor.domain.types import Flight
from advisor.llm.renderer import substitute_slots


def test_substitute_slots_happy_path():
    fl = Flight(
        flight_id="DX412",
        origin="BLR",
        destination="DEL",
        dep_utc="2026-09-15T10:30:00Z",
        arr_utc="2026-09-15T13:15:00Z",
        block_minutes=165,
        aircraft_type="A320",
        passengers=162,
    )
    impact = ImpactReport(
        disruption_id="d-1",
        disrupted_crew_id="C-1042",
        broken_pairing_id="P-2291",
        uncrewed_flights=(fl,),
        delayed_rotations=(),
        stranded_companions=(),
        passengers_affected=162,
        source_rows=[],
    )
    ledger = LegalityLedger(subject="C-1042", context="P-2291", verdicts=[])

    opt0 = RecoveryOption(
        crew_id="C-3310",
        candidate_type="on_base_reserve",
        base="BLR",
        ledger=ledger,
        cost=CostBreakdown(15000.0, 0.0, 0.0, 0.0, 15000.0, []),
    )
    opt1 = RecoveryOption(
        crew_id="C-2087",
        candidate_type="companion_upgrade",
        base="BLR",
        ledger=LegalityLedger(
            subject="C-2087",
            context="P-2291",
            verdicts=[
                RuleVerdict(
                    rule_id="RULE-DUTY-02",
                    passed=False,
                    headline="Breach",
                    arithmetic="",
                    inputs={},
                    margin=-1.5,
                    source_rows=[],
                )
            ],
        ),
        cost=CostBreakdown(15000.0, 0.0, 0.0, 0.0, 15000.0, []),
        repair=RepairOption("delay_departure", 91, "RULE-DUTY-02", True, "Delays DX412 by 91m"),
    )

    template = (
        "Captain {{impact.crew_id}} is incapacitated. "
        "Breaks {{impact.pairing_id}}, leaving {{impact.uncrewed_count}} flight uncrewed. "
        "Assign reserve {{options.0.crew_id}} at ₹{{options.0.cost_inr}}. "
        "Backup {{options.1.crew_id}} breaches by {{options.1.ledger.duty_02.margin}} — {{options.1.repair.text}}."
    )

    pii_map = {"C-1042": "Captain A. Nair", "C-3310": "Captain R. Sharma", "C-2087": "First Officer V. Patel"}
    result = substitute_slots(template, impact, ledger, [opt0, opt1], pii_map)

    assert "Captain A. Nair is incapacitated" in result
    assert "Breaks P-2291, leaving 1 flight uncrewed" in result
    assert "₹15,000" in result
    assert "First Officer V. Patel breaches by 1.5h" in result
    assert "delay departure by 91m" in result
    assert "{{" not in result


def test_substitute_slots_unknown_token_fallback():
    fl = Flight(
        flight_id="DX412",
        origin="BLR",
        destination="DEL",
        dep_utc="2026-09-15T10:30:00Z",
        arr_utc="2026-09-15T13:15:00Z",
        block_minutes=165,
        aircraft_type="A320",
        passengers=162,
    )
    impact = ImpactReport(
        disruption_id="d-1",
        disrupted_crew_id="C-1042",
        broken_pairing_id="P-2291",
        uncrewed_flights=(fl,),
        delayed_rotations=(),
        stranded_companions=(),
        passengers_affected=162,
        source_rows=[],
    )
    ledger = LegalityLedger(subject="C-1042", context="P-2291", verdicts=[])

    # Template containing an unknown / hallucinated slot token
    malformed_template = "Captain {{impact.crew_id}} has {{hallucinated.slot.value}}."
    result = substitute_slots(malformed_template, impact, ledger, [], {})

    # Must fall back cleanly to deterministic briefing without crashing
    assert "Operational Disruption Briefing" in result
    assert "{{" not in result
