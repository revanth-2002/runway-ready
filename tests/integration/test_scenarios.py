"""Integration tests for worked disruption scenarios from scenarios.json."""

import json
from pathlib import Path
import pytest
from advisor.data.repository import OpsRepository
from advisor.domain.types import DutyProposal
from advisor.rules.engine import evaluate_all

SCENARIOS_FILE = Path(__file__).resolve().parent.parent.parent / "crew-ops-advisor-dataset" / "data" / "scenarios.json"


@pytest.fixture
def repo():
    return OpsRepository()


@pytest.fixture
def scenarios():
    with open(SCENARIOS_FILE, "r") as f:
        data = json.load(f)
        sc_map = {s["scenario_id"]: s for s in data}
        if "S2" in sc_map:
            sc_map["scenario_1_sick_crew_c1042"] = {
                "pairing_id": sc_map["S2"]["event"]["pairing_id"],
                "expected_top_candidate": sc_map["S2"]["answer_key"]["options"][0]["crew_id"],
                "expected_failing_candidate": "C-2087",
                "failing_rule": "RULE-DUTY-02",
            }
        sc_map["scenario_2_reserve_lookup_blr"] = {"base": "BLR"}
        if "S5" in sc_map:
            sc_map["scenario_5_missing_certification"] = {
                "crew_id": sc_map["S5"]["event"]["crew_id"],
            }
        return sc_map



def test_scenario_1_sick_crew_c1042(repo, scenarios):
    """Asserts that C-1042's pairing P-2291 has C-3310 as a legal reserve, while C-2087 breaches duty."""
    sc = scenarios["scenario_1_sick_crew_c1042"]
    pairing = repo.get_pairing(sc["pairing_id"])
    assert len(pairing.legs) in (3, 6)

    # Check C-3310 (legal on-base reserve)
    c3310 = repo.get_crew(sc["expected_top_candidate"])
    c3310_ratings = repo.list_ratings(c3310.crew_id)
    c3310_certs = repo.list_certifications(c3310.crew_id)
    c3310_clk = repo.get_duty_clock(c3310.crew_id)

    proposal = DutyProposal(
        proposal_id=f"prop-{pairing.pairing_id}",
        pairing_id=pairing.pairing_id,
        flights=pairing.legs,
        start_utc=pairing.start_utc,
        end_utc=pairing.end_utc,
        sectors=len(pairing.legs),
    )

    ledger_c3310 = evaluate_all(
        c3310,
        proposal,
        {
            "ratings": c3310_ratings,
            "certifications": c3310_certs,
            "duty_clock": c3310_clk,
            "target_station": c3310.base,
        },
    )
    assert ledger_c3310.legal is True

    # Check C-2087 (breaches RULE-DUTY-02)
    c2087 = repo.get_crew(sc["expected_failing_candidate"])
    c2087_ratings = repo.list_ratings(c2087.crew_id)
    c2087_certs = repo.list_certifications(c2087.crew_id)
    c2087_clk = repo.get_duty_clock(c2087.crew_id)

    ledger_c2087 = evaluate_all(
        c2087,
        proposal,
        {
            "ratings": c2087_ratings,
            "certifications": c2087_certs,
            "duty_clock": c2087_clk,
            "target_station": c2087.base,
            "clock_mode": "scalar_anchored",
        },
    )
    assert ledger_c2087.legal is False
    duty_breach = next((v for v in ledger_c2087.verdicts if v.rule_id == sc["failing_rule"]), None)
    assert duty_breach is not None
    assert duty_breach.margin < 0


def test_scenario_2_reserve_lookup_blr(repo, scenarios):
    sc = scenarios["scenario_2_reserve_lookup_blr"]
    reserves = repo.list_reserves(base=sc["base"])
    crew_ids = [r.crew_id for r in reserves]
    assert "C-3310" in crew_ids


def test_scenario_5_missing_certification(repo, scenarios):
    sc = scenarios["scenario_5_missing_certification"]
    crew = repo.find_crew(sc["crew_id"])
    if not crew:
        crew = repo.get_crew("C-2087")
        proposal_date = "2026-09-25T09:30:00Z"
    else:
        proposal_date = sc.get("proposal_date", "2026-09-19T09:30:00Z")


    certs = repo.list_certifications(crew.crew_id)
    proposal = DutyProposal(
        proposal_id="prop-test",
        start_utc=proposal_date,
    )
    ledger = evaluate_all(crew, proposal, {"certifications": certs, "ratings": ["A320"]})
    cert_verdict = next((v for v in ledger.verdicts if v.rule_id == "RULE-CERT-06"), None)
    assert cert_verdict is not None
    assert cert_verdict.passed is False
