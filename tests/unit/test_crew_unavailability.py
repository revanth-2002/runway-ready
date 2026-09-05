"""Tests for detecting a crew member reported unavailable in everyday phrasing."""

import pytest

from advisor.llm.client import _stub_classify_intent, reports_crew_unavailable


@pytest.mark.parametrize("query", [
    "Captain C-1042 is out",
    "C-1042 is unavailable",
    "Captain Nair is off today",
    "FO C-2087 called in",
    "Captain C-1042 is a no-show",
    "Captain C-1042 can't fly tomorrow",
    "Captain C-1042 cannot operate DX412",
    "the captain won't make it",
    "C-1042 has dropped off the roster",
    "Captain C-1042 was pulled from the pairing",
    "Captain C-1042 stood down",
    "Captain C-1042 is indisposed",
    "C-1042 is on medical",
])
def test_recognises_unavailability_phrasings(query):
    assert reports_crew_unavailable(query) is True


@pytest.mark.parametrize("query", [
    "the aircraft is grounded",          # no crew reference
    "VT-DXA is unavailable",             # a tail, not a person
    "Who is on reserve at BLR?",
    "Which flights depart DEL?",
    "Captain C-1042 duty hours",         # a crew reference but no unavailability
])
def test_does_not_over_trigger(query):
    assert reports_crew_unavailable(query) is False


@pytest.mark.parametrize("query", [
    "Captain C-1042 is out — what should I do?",
    "C-1042 is unavailable",
    "FO C-2087 is a no-show",
])
def test_offline_classifier_routes_unavailability_to_simulate_sick(query):
    """These previously fell through to general_query and asked for the crew ID."""
    assert '"simulate_sick"' in _stub_classify_intent(query)


def test_role_detection_is_not_tripped_by_the_word_for():
    """`" fo" in query` matched "for", flipping a Captain to First Officer."""
    out = _stub_classify_intent("Captain is sick, find a replacement for DX412")
    assert '"role": "Captain"' in out


def test_role_detection_still_finds_a_real_first_officer():
    out = _stub_classify_intent("FO C-2087 is sick")
    assert '"role": "First Officer"' in out
