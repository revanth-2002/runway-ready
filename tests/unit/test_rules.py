"""Unit tests for all 7 regulatory rules."""

import pytest
from advisor.domain.types import Crew, DutyProposal, Flight, DutyClock, Certification
from advisor.rules.fdp_01 import evaluate_fdp_01
from advisor.rules.duty_02 import evaluate_duty_02
from advisor.rules.flt_03 import evaluate_flt_03
from advisor.rules.rest_04 import evaluate_rest_04
from advisor.rules.qual_05 import evaluate_qual_05
from advisor.rules.cert_06 import evaluate_cert_06
from advisor.rules.base_07 import evaluate_base_07
from advisor.rules.engine import evaluate_all


@pytest.fixture
def captain_nair():
    return Crew(crew_id="C-1042", name="A. Nair", rank="Captain", base="BLR", seniority=12, reachability_minutes=60)


@pytest.fixture
def proposal_standard():
    return DutyProposal(
        proposal_id="prop-1",
        flight_id="DX412",
        pairing_id="P-2291",
        start_utc="2026-09-15T09:30:00Z",
        end_utc="2026-09-15T17:00:00Z",
        duty_minutes=450,
        block_minutes=330,
        sectors=2,
    )


def test_fdp_01_passing(captain_nair, proposal_standard):
    verdict = evaluate_fdp_01(captain_nair, proposal_standard, {})
    assert verdict.passed is True
    assert verdict.margin > 0


def test_fdp_01_excess_sectors(captain_nair):
    # 5 sectors -> 3 extra sectors -> 1.5h penalty -> limit 11.5h
    # 12h proposed -> breaches by 0.5h
    prop = DutyProposal(
        proposal_id="prop-long",
        start_utc="2026-09-15T08:00:00Z",
        end_utc="2026-09-15T20:00:00Z",
        duty_minutes=720,
        block_minutes=500,
        sectors=5,
    )
    verdict = evaluate_fdp_01(captain_nair, prop, {})
    assert verdict.passed is False
    assert verdict.margin < 0


def test_duty_02_breach(captain_nair):
    # 54h accrued + 7.5h proposed = 61.5h > 60h -> breach with margin -1.5h
    clock = DutyClock(crew_id="C-2087", duty_hours_7d=54.0, flight_hours_28d=60.0)
    prop = DutyProposal(
        proposal_id="prop-p2291",
        start_utc="2026-09-15T09:30:00Z",
        end_utc="2026-09-15T17:00:00Z",  # 7.5h
    )
    verdict = evaluate_duty_02(captain_nair, prop, {"duty_clock": clock})
    assert verdict.passed is False
    assert verdict.margin == -1.5
    assert "54.0h accrued" in verdict.arithmetic


def test_flt_03_deadhead_excluded(captain_nair):
    # Accrued 98.0h + 3.0h deadhead proposed -> deadhead excluded -> remains 98.0h <= 100.0h
    clock = DutyClock(crew_id="C-1042", duty_hours_7d=20.0, flight_hours_28d=98.0)
    prop = DutyProposal(
        proposal_id="prop-dh",
        block_minutes=180,
        is_deadhead=True,
    )
    verdict = evaluate_flt_03(captain_nair, prop, {"duty_clock": clock})
    assert verdict.passed is True
    assert verdict.margin == 2.0


def test_rest_04_insufficient_rest(captain_nair):
    # Duty starts at 09:30, last rest ended at 02:00 (only 7.5h rest) -> breaches 12h
    prop = DutyProposal(proposal_id="prop-rest", start_utc="2026-09-15T09:30:00Z")
    verdict = evaluate_rest_04(captain_nair, prop, {"last_rest_ended": "2026-09-15T02:00:00Z"})
    assert verdict.passed is False
    assert verdict.margin < 0


def test_qual_05_rating(captain_nair, proposal_standard):
    # With A320 rating
    verdict = evaluate_qual_05(captain_nair, proposal_standard, {"ratings": ["A320"]})
    assert verdict.passed is True

    # Missing A320 rating
    verdict_fail = evaluate_qual_05(captain_nair, proposal_standard, {"ratings": ["B777"]})
    assert verdict_fail.passed is False


def test_cert_06_expired_medical(captain_nair, proposal_standard):
    certs = [
        Certification(crew_id="C-1042", cert_type="MEDICAL", valid_from="2025-01-01", expires_on="2026-08-01"),  # Expired
        Certification(crew_id="C-1042", cert_type="LINE_CHECK", valid_from="2026-01-01", expires_on="2026-12-31"),
        Certification(crew_id="C-1042", cert_type="IR", valid_from="2026-01-01", expires_on="2027-01-01"),
    ]
    verdict = evaluate_cert_06(captain_nair, proposal_standard, {"certifications": certs})
    assert verdict.passed is False
    assert "MEDICAL" in verdict.headline


def test_base_07_on_base_and_off_base(captain_nair, proposal_standard):
    # On-base BLR
    verdict = evaluate_base_07(captain_nair, proposal_standard, {"target_station": "BLR"})
    assert verdict.passed is True

    # Off-base DEL without deadhead
    del_crew = Crew(crew_id="C-4015", name="S. Sen", rank="Captain", base="DEL", reachability_minutes=60)
    verdict_off = evaluate_base_07(del_crew, proposal_standard, {"target_station": "BLR"})
    assert verdict_off.passed is False

    # Off-base DEL with schedule-feasible deadhead flight DX102 (arr 09:45, but report is 10:30)
    dh_flight = Flight(
        flight_id="DX102",
        origin="DEL",
        destination="BLR",
        dep_utc="2026-09-15T07:15:00Z",
        arr_utc="2026-09-15T09:45:00Z",
        block_minutes=150,
        aircraft_type="A320",
    )
    prop_feasible = DutyProposal(
        proposal_id="prop-del",
        start_utc="2026-09-15T10:30:00Z",
    )
    verdict_dh = evaluate_base_07(del_crew, prop_feasible, {"target_station": "BLR", "deadhead_flight": dh_flight})
    assert verdict_dh.passed is True
    assert verdict_dh.margin > 0


def test_evaluate_all_ledger(captain_nair, proposal_standard):
    context = {
        "ratings": ["A320"],
        "certifications": [
            Certification(crew_id="C-1042", cert_type="MEDICAL", valid_from="2026-01-01", expires_on="2027-01-01"),
            Certification(crew_id="C-1042", cert_type="LINE_CHECK", valid_from="2026-01-01", expires_on="2026-12-31"),
            Certification(crew_id="C-1042", cert_type="IR", valid_from="2026-01-01", expires_on="2027-01-01"),
        ],
        "target_station": "BLR",
    }
    ledger = evaluate_all(captain_nair, proposal_standard, context)
    assert len(ledger.verdicts) == 7
    assert ledger.legal is True
    assert len(ledger.breaches) == 0
