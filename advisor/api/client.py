"""Typed ApiClient SDK for communicating with the Crew Ops Advisor REST API."""

from dataclasses import dataclass, field
import os
from typing import Any, Dict, List, Optional
import httpx

from advisor.audit.logger import StructuredLogger

logger = StructuredLogger("advisor.api.client")


# -------------------------------------------------------------------------
# UI-Friendly Adaptor Classes for Options and Ledgers
# -------------------------------------------------------------------------

@dataclass
class ClientCost:
    total_inr: float
    line_items: List[str] = field(default_factory=list)


@dataclass
class ClientRepair:
    lever: str
    magnitude_minutes: int
    repaired_rule: str
    clears_binding: bool
    side_effects: str


@dataclass
class ClientRuleVerdict:
    rule_id: str
    headline: str
    passed: bool
    margin: float
    arithmetic: str
    inputs: Dict[str, Any] = field(default_factory=dict)
    source_rows: List[str] = field(default_factory=list)
    assumption: Optional[str] = None


@dataclass
class ClientLedger:
    legal: bool
    subject: str = ""
    breaches_count: int = 0
    verdicts: List[ClientRuleVerdict] = field(default_factory=list)

    @property
    def breaches(self) -> List[ClientRuleVerdict]:
        return [v for v in self.verdicts if not v.passed]


@dataclass
class ClientOption:
    crew_id: str
    candidate_type: str
    base: str
    ledger: ClientLedger
    cost: ClientCost
    repair: Optional[ClientRepair] = None
    deadhead_flight_id: Optional[str] = None
    expiry_utc: Optional[str] = None
    source_rows: List[str] = field(default_factory=list)


def parse_option_from_api(opt_dict: Dict[str, Any]) -> ClientOption:
    """Converts a raw JSON option payload from /api/v1/disruptions/simulate into a UI-renderable object."""
    cost_data = opt_dict.get("cost", {})
    cost = ClientCost(
        total_inr=float(cost_data.get("total_inr", 0.0)),
        line_items=cost_data.get("line_items", []),
    )

    repair = None
    if opt_dict.get("repair"):
        r_data = opt_dict["repair"]
        repair = ClientRepair(
            lever=r_data.get("lever", ""),
            magnitude_minutes=int(r_data.get("magnitude_minutes", 0)),
            repaired_rule=r_data.get("repaired_rule", ""),
            clears_binding=bool(r_data.get("clears_binding", False)),
            side_effects=r_data.get("side_effects", ""),
        )

    ledger = ClientLedger(
        legal=bool(opt_dict.get("legal", True)),
        subject=opt_dict.get("crew_id", ""),
    )

    return ClientOption(
        crew_id=opt_dict.get("crew_id", ""),
        candidate_type=opt_dict.get("candidate_type", "on_base_reserve"),
        base=opt_dict.get("base", "BLR"),
        ledger=ledger,
        cost=cost,
        repair=repair,
        deadhead_flight_id=opt_dict.get("deadhead_flight_id"),
        expiry_utc=opt_dict.get("expiry_utc"),
        source_rows=opt_dict.get("source_rows", []),
    )


def parse_ledger_from_api(led_dict: Optional[Dict[str, Any]]) -> Optional[ClientLedger]:
    """Converts a raw JSON ledger dictionary from the API into a UI-renderable ClientLedger."""
    if not led_dict:
        return None
    verdicts = [
        ClientRuleVerdict(
            rule_id=v.get("rule_id", ""),
            headline=v.get("headline", ""),
            passed=bool(v.get("passed", True)),
            margin=float(v.get("margin", 0.0)),
            arithmetic=v.get("arithmetic", ""),
            inputs=v.get("inputs", {}),
            source_rows=v.get("source_rows", []),
            assumption=v.get("assumption"),
        )
        for v in led_dict.get("verdicts", [])
    ]
    return ClientLedger(
        legal=bool(led_dict.get("legal", True)),
        subject=led_dict.get("subject", ""),
        breaches_count=int(led_dict.get("breaches_count", 0)),
        verdicts=verdicts,
    )


def parse_twin_view_from_api(tv_dict: Optional[Dict[str, Any]]) -> Any:
    """Re-instantiates a DigitalTwinState from the API payload for Plotly Gantt rendering."""
    if not tv_dict or "active_flights" not in tv_dict:
        return None
    from advisor.domain.types import Flight
    from advisor.twin.view import DigitalTwinState

    flights = {}
    for f_id, f_data in tv_dict.get("active_flights", {}).items():
        flights[f_id] = Flight(
            flight_id=f_data["flight_id"],
            origin=f_data["origin"],
            destination=f_data["destination"],
            dep_utc=f_data["dep_utc"],
            arr_utc=f_data["arr_utc"],
            block_minutes=int(f_data["block_minutes"]),
            aircraft_type=f_data["aircraft_type"],
            tail_id=f_data.get("tail_id"),
            rotation_id=f_data.get("rotation_id"),
            rotation_seq=f_data.get("rotation_seq"),
            passengers=f_data.get("passengers"),
        )

    return DigitalTwinState(
        timestamp_utc=tv_dict.get("timestamp_utc", ""),
        tails={},
        crew={},
        active_flights=flights,
        active_pairings={},
        flight_statuses=tv_dict.get("flight_statuses", {}),
        flight_estimated_deps=tv_dict.get("flight_estimated_deps", {}),
    )


# -------------------------------------------------------------------------
# ApiClient Class
# -------------------------------------------------------------------------

class ApiClient:
    """Client SDK supporting both remote HTTP service and in-process TestClient execution."""

    def __init__(self, base_url: Optional[str] = None):
        self.remote_url = base_url or os.environ.get("ADVISOR_API_URL")
        self._test_client = None

    def _request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        if self.remote_url:
            with httpx.Client(base_url=self.remote_url, timeout=60.0) as client:
                resp = client.request(method, path, **kwargs)
                resp.raise_for_status()
                return resp.json()
        else:
            if self._test_client is None:
                from starlette.testclient import TestClient
                from advisor.api.server import app
                self._test_client = TestClient(app)
            resp = self._test_client.request(method, path, **kwargs)
            resp.raise_for_status()
            return resp.json()

    # 1. Health
    def get_health(self) -> Dict[str, Any]:
        """GET /api/v1/health"""
        return self._request("GET", "/api/v1/health")

    # 2. Workspace 1 & Airport Hubs: Network Overview & Station Details
    def get_network_overview(self) -> Dict[str, Any]:
        """GET /api/v1/network/overview"""
        return self._request("GET", "/api/v1/network/overview")

    def get_station_details(self, station_code: str = "BLR") -> Dict[str, Any]:
        """GET /api/v1/stations/{station_code}"""
        return self._request("GET", f"/api/v1/stations/{station_code.upper()}")

    # 3. Workspace 2: Disruption Simulation & Finalize
    def simulate_disruption(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
        offline_mode: bool = False,
    ) -> Dict[str, Any]:
        """POST /api/v1/disruptions/simulate"""
        payload = {"query": query, "context": context or {}, "offline_mode": offline_mode}
        data = self._request("POST", "/api/v1/disruptions/simulate", json=payload)

        # Enrich raw payload with UI-friendly objects
        if "options" in data and data["options"]:
            data["parsed_options"] = [parse_option_from_api(o) for o in data["options"]]
        else:
            data["parsed_options"] = []

        data["parsed_ledger"] = parse_ledger_from_api(data.get("ledger"))
        data["parsed_twin_view"] = parse_twin_view_from_api(data.get("twin_view"))

        return data

    def finalize_recommendation(
        self,
        crew_id: str,
        candidate_type: str,
        pairing_id: str,
        disrupted_crew_id: str = "C-1042",
        flight_ids: Optional[List[str]] = None,
        cost_inr: float = 0.0,
        delay_minutes: int = 0,
        delayed_flight_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /api/v1/recommendations/finalize"""
        payload = {
            "crew_id": crew_id,
            "candidate_type": candidate_type,
            "pairing_id": pairing_id,
            "disrupted_crew_id": disrupted_crew_id,
            "flight_ids": flight_ids or [],
            "cost_inr": cost_inr,
            "delay_minutes": delay_minutes,
            "delayed_flight_id": delayed_flight_id,
        }
        return self._request("POST", "/api/v1/recommendations/finalize", json=payload)

    # 4. Workspace 3: Standby & Reserves Roster
    def get_reserves(
        self,
        station: str = "BLR",
        rank: Optional[str] = None,
        available_only: bool = False,
    ) -> Dict[str, Any]:
        """GET /api/v1/reserves"""
        params = {"station": station, "available_only": available_only}
        if rank:
            params["rank"] = rank
        return self._request("GET", "/api/v1/reserves", params=params)

    # 5. Workspace 4: Fleet Rotations & Manifest
    def get_fleet_rotations(self) -> Dict[str, Any]:
        """GET /api/v1/fleet/rotations"""
        return self._request("GET", "/api/v1/fleet/rotations")

    # 6. Digital Twin State & Undo / Reset Controls
    def get_twin_state(self) -> Dict[str, Any]:
        """GET /api/v1/twin/state"""
        return self._request("GET", "/api/v1/twin/state")

    def undo_overlay(self) -> Dict[str, Any]:
        """POST /api/v1/twin/undo"""
        return self._request("POST", "/api/v1/twin/undo")

    def get_chat_state(self) -> Dict[str, Any]:
        """GET /api/v1/chat/state"""
        return self._request("GET", "/api/v1/chat/state")

    def reset_chat(self) -> Dict[str, Any]:
        """POST /api/v1/chat/reset — forgets the conversation, keeps twin overlays."""
        return self._request("POST", "/api/v1/chat/reset")

    def reset_baseline(self) -> Dict[str, Any]:
        """POST /api/v1/twin/reset"""
        return self._request("POST", "/api/v1/twin/reset")


# -------------------------------------------------------------------------
# Global Singleton Accessor
# -------------------------------------------------------------------------

_DEFAULT_API_CLIENT: Optional[ApiClient] = None


def get_api_client(base_url: Optional[str] = None) -> ApiClient:
    """Returns singleton instance of ApiClient."""
    global _DEFAULT_API_CLIENT
    if _DEFAULT_API_CLIENT is None or base_url is not None:
        _DEFAULT_API_CLIENT = ApiClient(base_url=base_url)
    return _DEFAULT_API_CLIENT
