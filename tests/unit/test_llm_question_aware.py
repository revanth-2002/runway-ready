"""Tests for question-aware prompting, slot-vocabulary safety, and follow-up suggestions."""

import pytest

from advisor.domain.evidence import (
    CostBreakdown,
    ImpactReport,
    LegalityLedger,
    RecoveryOption,
    RepairOption,
    RuleVerdict,
)
from advisor.domain.exceptions import LLMUnavailableError
from advisor.domain.types import Flight
from advisor.llm.parser import QueryIntent
from advisor.llm.renderer import build_slot_values, render_slotted_prose, substitute_slots
from advisor.llm.suggest import derive_suggestions


class SpyClient:
    """Captures the prompt it was given and returns a canned reply."""

    def __init__(self, reply="{{impact.crew_id}} is out."):
        self.reply = reply
        self.prompt = None

    def generate(self, prompt, temperature=0.0):
        self.prompt = prompt
        return self.reply


def _flight(flight_id="DX412", dep="2026-09-16T09:30:00Z"):
    return Flight(
        flight_id=flight_id,
        origin="BLR",
        destination="DEL",
        dep_utc=dep,
        arr_utc="2026-09-16T12:15:00Z",
        block_minutes=165,
        aircraft_type="A320",
        tail_id="VT-DXA",
        rotation_id="R1",
        rotation_seq=1,
        passengers=162,
    )


def _impact(flights=None):
    flights = (_flight(),) if flights is None else flights
    return ImpactReport(
        disruption_id="d1",
        disrupted_crew_id="C-1042",
        broken_pairing_id="P-2291",
        uncrewed_flights=flights,
        delayed_rotations=(),
        stranded_companions=(),
        passengers_affected=162,
        source_rows=[],
    )


def _option(crew_id="C-3310", legal=True, cost=18500.0, repair=None):
    verdicts = []
    if not legal:
        verdicts.append(
            RuleVerdict(
                rule_id="RULE-DUTY-02",
                passed=False,
                headline="Exceeds 60h/7d limit by 2.0h",
                arithmetic="62.0h > 60.0h",
                inputs={},
                margin=-2.0,
                source_rows=[],
            )
        )
    return RecoveryOption(
        crew_id=crew_id,
        candidate_type="on_base_reserve",
        base="BLR",
        ledger=LegalityLedger(subject=crew_id, context="p", verdicts=verdicts),
        cost=CostBreakdown(cost, 0.0, 0.0, 0.0, cost, []),
        repair=repair,
    )


PII = {"C-1042": "Captain A. Nair", "C-3310": "D. Reddy"}


# --------------------------------------------------------------------------
# Question-aware prompting
# --------------------------------------------------------------------------

def test_prompt_carries_the_controllers_question():
    spy = SpyClient()
    render_slotted_prose(
        _impact(), LegalityLedger("C-1042", "P-2291", []), [_option()], spy,
        question="What is the cheapest legal option?", pii_map=PII,
    )
    assert "What is the cheapest legal option?" in spy.prompt


def test_prompt_advertises_the_exact_slot_vocabulary():
    impact, ledger, options = _impact(), LegalityLedger("C-1042", "P-2291", []), [_option()]
    spy = SpyClient()
    render_slotted_prose(impact, ledger, options, spy, question="who?", pii_map=PII)

    for token in build_slot_values(impact, ledger, options, PII):
        assert "{{" + token + "}}" in spy.prompt


def test_prompt_without_question_falls_back_to_breach_lead():
    spy = SpyClient()
    render_slotted_prose(_impact(), LegalityLedger("C-1042", "P-2291", []), [], spy, pii_map=PII)
    assert "Lead with the binding operational breach." in spy.prompt


# --------------------------------------------------------------------------
# Slot vocabulary safety
# --------------------------------------------------------------------------

def test_template_using_listed_tokens_is_accepted():
    template = "Cheapest cover is {{options.0.crew_id}} at ₹{{options.0.cost_inr}}."
    out = render_slotted_prose(
        _impact(), LegalityLedger("C-1042", "P-2291", []), [_option()],
        SpyClient(template), question="cheapest?", pii_map=PII,
    )
    assert out == template


def test_template_with_invented_token_is_discarded():
    """An unlisted token used to survive rendering and blow up in substitute_slots."""
    out = render_slotted_prose(
        _impact(), LegalityLedger("C-1042", "P-2291", []), [_option()],
        SpyClient("Try {{options.9.crew_id}} in {{impact.weather}}."),
        question="cheapest?", pii_map=PII,
    )
    assert "options.9.crew_id" not in out
    assert "impact.weather" not in out


def test_third_option_tokens_resolve():
    """options.2+ previously had no slot values, collapsing the whole briefing."""
    impact = _impact()
    ledger = LegalityLedger("C-1042", "P-2291", [])
    options = [_option("C-3310"), _option("C-3311"), _option("C-3312")]
    out = substitute_slots("{{options.2.crew_id}} at {{options.2.base}}", impact, ledger, options, PII)
    assert out == "C-3312 at BLR"


def test_date_is_derived_from_evidence_not_hardcoded():
    slots = build_slot_values(
        _impact((_flight(dep="2026-09-18T06:00:00Z"),)),
        LegalityLedger("C-1042", "P-2291", []), [], PII,
    )
    assert slots["impact.date"] == "2026-09-18"


def test_rank_slot_reflects_actual_rank():
    slots = build_slot_values(
        _impact(), LegalityLedger("C-2087", "P-2291", []), [],
        {"C-1042": "First Officer D. Menon"},
    )
    assert slots["impact.crew_rank"] == "First Officer"
    assert slots["impact.crew_id"] == "D. Menon"


# --------------------------------------------------------------------------
# Degraded mode
# --------------------------------------------------------------------------

def test_llm_unavailable_falls_back_without_narrating_it():
    class Down:
        def generate(self, prompt, temperature=0.0):
            raise LLMUnavailableError("boom", is_rate_limit=False)

    out = render_slotted_prose(
        _impact(), LegalityLedger("C-1042", "P-2291", []), [], Down(),
        question="options?", pii_map=PII,
    )
    # Degradation is logged only - the controller just gets the certified briefing.
    assert "{{impact.crew_rank}} {{impact.crew_id}}" in out
    assert "narration" not in out.lower()
    assert not out.startswith(">")


def test_rate_limited_surfaces_the_limit_notice():
    class Limited:
        def generate(self, prompt, temperature=0.0):
            raise LLMUnavailableError("429", is_rate_limit=True)

    out = render_slotted_prose(
        _impact(), LegalityLedger("C-1042", "P-2291", []), [], Limited(),
        question="options?", pii_map=PII,
    )
    assert "You have hit the limit" in out


# --------------------------------------------------------------------------
# Question-driven suggestions
# --------------------------------------------------------------------------

def _intent(name, **entities):
    return QueryIntent(intent=name, entities=entities, time_scope={}, confidence=0.95)


def test_suggestions_use_the_station_that_was_asked_about():
    out = derive_suggestions(_intent("lookup_reserves", base="MAA"))
    assert out
    assert all("MAA" in s["query"] for s in out)
    assert not any("BLR" in s["query"] for s in out)


def test_suggestions_reference_evidence_over_query_text():
    """After a simulation, follow-ups should point at what was actually found."""
    impact = _impact((_flight("DX702"),))
    out = derive_suggestions(
        _intent("simulate_sick", crew_ids=["C-1042"]),
        impact=impact,
        options=[_option("C-3310", legal=True)],
    )
    labels = " ".join(s["label"] for s in out)
    assert "C-3310" in labels
    assert "P-2291" in " ".join(s["query"] for s in out)


def test_suggestions_offer_the_repair_for_a_blocked_candidate():
    blocked = _option(
        "C-3305",
        legal=False,
        repair=RepairOption("delay_departure", 121, "RULE-DUTY-02", True, "delays 121m"),
    )
    out = derive_suggestions(
        _intent("simulate_sick"), impact=_impact(), options=[_option("C-3310"), blocked]
    )
    assert any("C-3305" in s["query"] for s in out)


def test_no_suggestions_while_awaiting_clarification():
    out = derive_suggestions(_intent("lookup_reserves", base="BLR"), awaiting_clarification=True)
    assert out == []


def test_suggestions_never_invent_entities():
    """With no station or flight named, suggestions must stay empty rather than guess."""
    out = derive_suggestions(_intent("lookup_reserves"))
    assert out == []


def test_suggestions_are_capped():
    impact = _impact()
    out = derive_suggestions(
        _intent("simulate_sick", flight_ids=["DX412"], base="BLR"),
        impact=impact,
        options=[_option("C-3310"), _option("C-3305", legal=False,
                repair=RepairOption("delay_departure", 60, "RULE-DUTY-02", True, "x"))],
    )
    assert len(out) <= 3


# --------------------------------------------------------------------------
# Composed disruption briefing
# --------------------------------------------------------------------------

def test_briefing_lists_the_affected_legs_not_just_a_count():
    """"Which flights are uncrewed" is not answered by "6 flights"."""
    from advisor.llm.renderer import render_disruption_briefing

    impact = _impact((_flight("DX412"), _flight("DX588", dep="2026-09-16T12:15:00Z")))
    out = render_disruption_briefing(
        impact, LegalityLedger("C-1042", "P-2291", []), [_option()], "", PII
    )

    assert "DX412" in out and "DX588" in out
    assert "09:30Z" in out          # departure time
    assert "12:15Z" in out
    assert "VT-DXA" in out          # tail
    assert "162" in out             # per-leg passenger load
    assert "Uncrewed Legs" in out


def test_briefing_headline_carries_pairing_legs_and_passengers():
    from advisor.llm.renderer import render_disruption_briefing

    out = render_disruption_briefing(
        _impact(), LegalityLedger("C-1042", "P-2291", []), [], "", PII
    )
    assert "P-2291" in out
    assert "Captain A. Nair" in out


def test_briefing_omits_an_empty_narrative():
    from advisor.llm.renderer import render_disruption_briefing

    out = render_disruption_briefing(
        _impact(), LegalityLedger("C-1042", "P-2291", []), [], "", PII
    )
    assert "Assessment" not in out


def test_briefing_includes_an_llm_narrative():
    from advisor.llm.renderer import render_disruption_briefing

    out = render_disruption_briefing(
        _impact(), LegalityLedger("C-1042", "P-2291", []), [], "Crew shortfall at BLR.", PII
    )
    assert "Assessment" in out
    assert "Crew shortfall at BLR." in out


def test_banner_is_hoisted_above_the_headline():
    from advisor.llm.renderer import RATE_LIMIT_BANNER, render_disruption_briefing

    out = render_disruption_briefing(
        _impact(), LegalityLedger("C-1042", "P-2291", []), [], "", PII,
        banner=RATE_LIMIT_BANNER,
    )
    assert out.lstrip().startswith(">")
    assert out.index("hit the limit") < out.index("Disruption Impact")


def test_return_meta_reports_the_narrative_source():
    impact, ledger = _impact(), LegalityLedger("C-1042", "P-2291", [])
    good = "{{options.0.crew_id}} covers it."

    text, meta = render_slotted_prose(
        impact, ledger, [_option()], SpyClient(good),
        question="who?", pii_map=PII, return_meta=True,
    )
    assert (text, meta["source"]) == (good, "llm")

    class Down:
        def generate(self, prompt, temperature=0.0):
            raise LLMUnavailableError("429", is_rate_limit=True)

    text, meta = render_slotted_prose(
        impact, ledger, [_option()], Down(),
        question="who?", pii_map=PII, return_meta=True,
    )
    assert meta["source"] == "deterministic"
    assert "hit the limit" in meta["banner"]
    # The banner is handed back separately, not glued onto the template.
    assert "hit the limit" not in text


# --------------------------------------------------------------------------
# "Best way to overcome" recommendation
# --------------------------------------------------------------------------

def test_recommendation_names_the_cheapest_legal_option_with_tradeoffs():
    from advisor.llm.renderer import recommend_recovery

    cheap = _option("C-3310", legal=True, cost=18500.0)
    dearer = _option("C-5566", legal=True, cost=24000.0)
    rec = recommend_recovery([cheap, dearer], _impact(), PII)

    assert "C-3310" in rec
    assert "18,500" in rec
    assert "C-5566" in rec
    assert "5,500 more" in rec


def test_recommendation_offers_the_repair_when_nothing_is_legal():
    from advisor.llm.renderer import recommend_recovery

    blocked = _option(
        "C-3305", legal=False,
        repair=RepairOption("delay_departure", 121, "RULE-DUTY-02", True, "impacts 162 pax"),
    )
    rec = recommend_recovery([blocked], _impact(), PII)

    assert "C-3305" in rec
    assert "delay departure by 121m" in rec


def test_recommendation_compares_against_the_cancellation_benchmark():
    from advisor.llm.renderer import recommend_recovery

    cheap = _option("C-3310", legal=True, cost=18500.0)
    do_nothing = _option("DO_NOTHING", legal=True, cost=1500000.0)
    rec = recommend_recovery([cheap, do_nothing], _impact(), PII)

    assert "1,481,500" in rec


def test_recommendation_is_none_without_candidates():
    from advisor.llm.renderer import recommend_recovery

    assert recommend_recovery([], _impact(), PII) is None
