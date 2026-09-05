"""Unit tests for Advisor REST API Endpoints (/api/v1/...)."""

import pytest
from starlette.testclient import TestClient

from advisor.api.server import app
from advisor.api.client import get_api_client


@pytest.fixture
def client():
    return TestClient(app)


def test_api_health(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "llm_mode" in data
    assert data["twin_warmed"] is True


def test_api_network_overview(client):
    response = client.get("/api/v1/network/overview")
    assert response.status_code == 200
    data = response.json()
    assert "kpis" in data
    assert data["kpis"]["total_active_tails"] == 6
    assert data["kpis"]["scheduled_flights"] > 0
    assert len(data["stations"]) == 5
    station_codes = {s["station"] for s in data["stations"]}
    assert station_codes == {"BLR", "DEL", "BOM", "HYD", "MAA"}


def test_api_reserves_listing(client):
    response = client.get("/api/v1/reserves?station=BLR")
    assert response.status_code == 200
    data = response.json()
    assert data["station"] == "BLR"
    assert data["total_count"] > 0
    assert len(data["reserves"]) == data["total_count"]
    sample = data["reserves"][0]
    assert "crew_id" in sample
    assert "standby_status" in sample
    assert "ratings" in sample


def test_api_fleet_rotations(client):
    response = client.get("/api/v1/fleet/rotations")
    assert response.status_code == 200
    data = response.json()
    assert data["active_tails_count"] == 6
    assert data["total_flights_count"] > 0
    assert len(data["manifest"]) == data["total_flights_count"]


def test_api_simulate_disruption(client):
    payload = {
        "query": "Captain A. Nair is sick for flight DX412. What is the impact and who is the recommended replacement?"
    }
    response = client.post("/api/v1/disruptions/simulate", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["abstained"] is False
    assert len(data["uncrewed_flight_ids"]) > 0
    assert len(data["options"]) > 0
    top_opt = data["options"][0]
    assert "crew_id" in top_opt
    assert "cost" in top_opt
    assert top_opt["cost"]["total_inr"] > 0


def test_api_simulate_disruption_offline_mode(client):
    payload = {
        "query": "Captain A. Nair is sick for flight DX412. What is the impact and who is the recommended replacement?",
        "offline_mode": True,
    }
    response = client.post("/api/v1/disruptions/simulate", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["abstained"] is False
    assert len(data["options"]) > 0


def test_api_simulate_abstention(client):
    payload = {"query": "Is Captain C-9999 available to fly DX412?"}
    response = client.post("/api/v1/disruptions/simulate", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "abstained"
    assert data["abstained"] is True
    assert data["abstain_reason"] == "UNKNOWN_ENTITY"


def test_api_finalize_undo_reset_flow(client):
    # 1. Finalize recommendation
    fin_payload = {
        "crew_id": "C-3310",
        "candidate_type": "on_base_reserve",
        "pairing_id": "P-2291",
        "disrupted_crew_id": "C-1042",
        "flight_ids": ["DX412-2026-09-15"],
        "cost_inr": 18500.0,
    }
    fin_resp = client.post("/api/v1/recommendations/finalize", json=fin_payload)
    assert fin_resp.status_code == 200
    fin_data = fin_resp.json()
    assert fin_data["success"] is True
    assert fin_data["dispatched_crew_id"] == "C-3310"

    # 2. Check reserves reflects CALLED
    res_resp = client.get("/api/v1/reserves?station=BLR")
    c3310 = next((r for r in res_resp.json()["reserves"] if r["crew_id"] == "C-3310"), None)
    assert c3310 is not None
    assert "CALLED" in c3310["standby_status"]

    # 3. Undo
    undo_resp = client.post("/api/v1/twin/undo")
    assert undo_resp.status_code == 200
    assert undo_resp.json()["success"] is True

    # 4. Reset
    reset_resp = client.post("/api/v1/twin/reset")
    assert reset_resp.status_code == 200
    assert reset_resp.json()["success"] is True
    assert reset_resp.json()["active_overlays_count"] == 0


def test_api_station_details_blr(client):
    response = client.get("/api/v1/stations/BLR")
    assert response.status_code == 200
    data = response.json()
    assert data["station_code"] == "BLR"
    assert data["icao_code"] == "VOBL"
    assert data["departure_count"] > 0
    assert data["arrival_count"] > 0
    assert data["total_movements"] == data["departure_count"] + data["arrival_count"]
    assert "weather" in data
    assert data["weather"]["flight_category"] == "VFR"
    assert len(data["weather"]["forecast_periods"]) == 4
    assert len(data["departures"]) == data["departure_count"]
    assert len(data["arrivals"]) == data["arrival_count"]
    sample_dep = data["departures"][0]
    assert sample_dep["movement_type"] == "DEPARTURE"
    assert sample_dep["origin"] == "BLR"
    assert "gate" in sample_dep


def test_api_station_details_all_hubs(client):
    for hub in ["BLR", "DEL", "BOM", "HYD", "MAA"]:
        resp = client.get(f"/api/v1/stations/{hub}")
        assert resp.status_code == 200
        d = resp.json()
        assert d["station_code"] == hub
        assert d["weather"]["metar_raw"] is not None
        assert len(d["weather"]["forecast_periods"]) == 4


def test_api_station_details_not_found(client):
    resp = client.get("/api/v1/stations/XYZ")
    assert resp.status_code == 404
    assert "not recognized" in resp.json()["detail"]


def test_weather_dataset_file_integrity():
    import json
    from pathlib import Path

    p = Path("data/weather.json")
    assert p.exists(), "data/weather.json must exist"
    data = json.loads(p.read_text(encoding="utf-8"))

    expected_hubs = {"BLR", "DEL", "BOM", "HYD", "MAA"}
    assert set(data.keys()) == expected_hubs

    for hub, info in data.items():
        assert "airport_name" in info
        assert "icao" in info
        assert "weather" in info
        w = info["weather"]
        assert "metar_raw" in w
        assert "taf_raw" in w
        assert "flight_category" in w
        assert "forecast_periods" in w
        assert len(w["forecast_periods"]) == 4


