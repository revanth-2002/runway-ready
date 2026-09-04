"""Unit tests for parameterized OpsRepository."""

import pytest
from advisor.data.repository import OpsRepository
from advisor.domain.exceptions import EntityNotFoundError


@pytest.fixture
def repo():
    return OpsRepository()


def test_get_crew_success(repo):
    crew = repo.get_crew("C-1042")
    assert crew.crew_id == "C-1042"
    assert "Nair" in crew.name
    assert crew.rank == "Captain"
    assert crew.base == "BLR"


def test_get_crew_not_found(repo):
    with pytest.raises(EntityNotFoundError):
        repo.get_crew("C-9999")


def test_find_crew_none(repo):
    assert repo.find_crew("C-9999") is None


def test_get_flight_success(repo):
    flight = repo.get_flight("DX412")
    assert "DX412" in flight.flight_id
    assert flight.origin == "BLR"
    assert flight.destination in ("DEL", "BOM")
    assert flight.block_minutes in (105, 150, 165)
    assert flight.passengers == 162


def test_list_flights_filter(repo):
    del_blr = repo.list_flights(origin="DEL", destination="BLR")
    assert len(del_blr) > 0
    assert any("DX" in f.flight_id for f in del_blr)


def test_get_pairing(repo):
    pairing = repo.get_pairing("P-2291")
    assert pairing.pairing_id == "P-2291"
    assert len(pairing.legs) in (3, 6)
    assert [f.flight_id.split("-")[0] for f in pairing.legs[:3]] == ["DX412", "DX413", "DX588"]


def test_get_pairing_for_crew(repo):
    pairing = repo.get_pairing_for_crew("C-1042", at_utc="2026-09-15T00:00:00Z")
    assert pairing is not None
    assert pairing.pairing_id == "P-2291"


def test_list_reserves(repo):
    blr_reserves = repo.list_reserves(base="BLR", report_time_utc="2026-09-15T10:00:00Z")
    crew_ids = [r.crew_id for r in blr_reserves]
    assert "C-3310" in crew_ids


def test_ratings_and_certs(repo):
    ratings = repo.list_ratings("C-1042")
    assert "A320" in ratings

    certs = repo.list_certifications("C-1042")
    cert_types = [c.cert_type.upper() for c in certs]
    assert any("MEDIC" in ct for ct in cert_types)


def test_cost_rates(repo):
    rates = repo.get_cost_rates()
    assert rates["reserve_callout"] in (15000.0, 18500.0)
    assert "deadhead_DEL_BLR" in rates or "deadhead_positioning" in rates
