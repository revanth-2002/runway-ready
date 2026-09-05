"""Pydantic request and response schemas for the Crew Ops Advisor REST API."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# -------------------------------------------------------------------------
# Health & Status
# -------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str = "healthy"
    version: str = "1.0.0"
    twin_warmed: bool = True
    llm_mode: str = "deterministic_stub"
    timestamp: str


# -------------------------------------------------------------------------
# Network Overview & KPIs (Workspace 1)
# -------------------------------------------------------------------------

class StationSummary(BaseModel):
    station: str
    total_reserves: int
    available_reserves: int
    scheduled_departures: int
    weather_advisory: Optional[str] = None
    maintenance_notice: Optional[str] = None


class NetworkKPIs(BaseModel):
    total_active_tails: int
    scheduled_flights: int
    on_time_rate_pct: float
    disruption_alerts_count: int
    passenger_seats_at_risk: int
    total_available_reserves: int


class NetworkOverviewResponse(BaseModel):
    kpis: NetworkKPIs
    stations: List[StationSummary]
    active_overlays_count: int
    timestamp: str


# -------------------------------------------------------------------------
# Disruption Simulation & Recovery (Workspace 2)
# -------------------------------------------------------------------------

class DisruptionSimulateRequest(BaseModel):
    query: str
    context: Optional[Dict[str, Any]] = None
    offline_mode: bool = False


class RepairOptionSchema(BaseModel):
    lever: str
    magnitude_minutes: int
    repaired_rule: str
    clears_binding: bool
    side_effects: str


class CostBreakdownSchema(BaseModel):
    total_inr: float
    line_items: List[str] = Field(default_factory=list)


class RuleVerdictSchema(BaseModel):
    rule_code: str
    rule_name: str
    legal: bool
    margin: float
    unit: str
    threshold: float
    actual: float
    binding: bool = False
    details: str = ""


class LegalityLedgerSchema(BaseModel):
    crew_id: str
    legal: bool
    verdicts: List[RuleVerdictSchema] = Field(default_factory=list)
    binding_constraint: Optional[str] = None


class CandidateOptionSchema(BaseModel):
    crew_id: str
    candidate_type: str
    base: str
    legal: bool
    cost: CostBreakdownSchema
    repair: Optional[RepairOptionSchema] = None
    deadhead_flight_id: Optional[str] = None
    expiry_utc: Optional[str] = None
    source_rows: List[str] = Field(default_factory=list)


class DisruptionSimulateResponse(BaseModel):
    status: str
    query: str
    abstained: bool = False
    abstain_reason: Optional[str] = None
    abstain_message: Optional[str] = None
    disrupted_crew_id: Optional[str] = None
    broken_pairing_id: Optional[str] = None
    uncrewed_flight_ids: List[str] = Field(default_factory=list)
    options: List[CandidateOptionSchema] = Field(default_factory=list)
    ledger: Optional[Dict[str, Any]] = None
    twin_view: Optional[Dict[str, Any]] = None
    prose_summary: Optional[str] = None


class FinalizeRecommendationRequest(BaseModel):
    crew_id: str
    candidate_type: str
    pairing_id: str
    disrupted_crew_id: Optional[str] = "C-1042"
    flight_ids: List[str] = Field(default_factory=list)
    cost_inr: float = 0.0
    delay_minutes: int = 0
    delayed_flight_id: Optional[str] = None


class FinalizeRecommendationResponse(BaseModel):
    success: bool
    message: str
    finalized_overlay_id: str
    active_overlays_count: int
    dispatched_crew_id: str
    pairing_id: str
    timestamp: str


# -------------------------------------------------------------------------
# Standby & Reserves Roster (Workspace 3)
# -------------------------------------------------------------------------

class ReserveItemSchema(BaseModel):
    crew_id: str
    name: str
    rank: str
    base: str
    ratings: List[str] = Field(default_factory=list)
    oncall_start_utc: str
    oncall_end_utc: str
    standby_status: str
    reachability_minutes: int = 45
    duty_hours_7d: float = 0.0


class ReserveListResponse(BaseModel):
    station: str
    total_count: int
    available_count: int
    reserves: List[ReserveItemSchema]


# -------------------------------------------------------------------------
# Fleet Rotations & Flight Manifest (Workspace 4)
# -------------------------------------------------------------------------

class FlightManifestItem(BaseModel):
    flight_id: str
    origin: str
    destination: str
    dep_utc: str
    arr_utc: str
    block_minutes: int
    aircraft_type: str
    tail_id: Optional[str] = None
    rotation_id: Optional[str] = None
    rotation_seq: Optional[int] = None
    passengers: Optional[int] = None
    status: str = "ON_TIME"


class TailRotation(BaseModel):
    tail_id: str
    aircraft_type: str
    flight_count: int
    flights: List[FlightManifestItem]


class FleetRotationsResponse(BaseModel):
    active_tails_count: int
    total_flights_count: int
    tails: List[TailRotation]
    manifest: List[FlightManifestItem]


# -------------------------------------------------------------------------
# Twin Overlays & History Controls
# -------------------------------------------------------------------------

class OverlaySummary(BaseModel):
    overlay_id: str
    kind: str
    label: str
    created_utc: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class TwinStateResponse(BaseModel):
    active_overlays_count: int
    overlays: List[OverlaySummary]
    last_action: Optional[str] = None


class TwinActionResponse(BaseModel):
    success: bool
    action: str
    message: str
    active_overlays_count: int
    timestamp: str


# -------------------------------------------------------------------------
# Airport Station Deep-Dive & Weather (Airport Operations Workspace)
# -------------------------------------------------------------------------

class WeatherForecastPeriod(BaseModel):
    period_name: str
    time_utc: str
    condition: str
    icon: str
    temp_c: int
    wind_str: str
    precip_prob_pct: int
    ceiling: str


class WeatherCondition(BaseModel):
    metar_raw: str
    taf_raw: str
    flight_category: str  # VFR, MVFR, IFR
    temperature_c: float
    dewpoint_c: float
    wind_direction_deg: int
    wind_speed_kts: int
    wind_gusts_kts: Optional[int] = None
    visibility_m: int
    altimeter_hpa: int
    clouds: str
    runway_in_use: str
    crosswind_component_kts: int = 0
    advisory: Optional[str] = None
    forecast_periods: List[WeatherForecastPeriod] = Field(default_factory=list)


class StationFlightMovement(BaseModel):
    flight_id: str
    movement_type: str  # DEPARTURE or ARRIVAL
    origin: str
    destination: str
    route: str
    scheduled_utc: str
    estimated_utc: str
    status: str  # ON_TIME, DELAYED, UNCREWED, CANCELLED
    delay_minutes: int = 0
    tail_id: Optional[str] = None
    aircraft_type: str
    passengers: int = 0
    gate: str = ""


class StationDetailResponse(BaseModel):
    station_code: str
    airport_name: str
    city: str
    icao_code: str
    elevation_ft: int
    runways: List[str]
    weather: WeatherCondition
    total_movements: int
    departure_count: int
    arrival_count: int
    on_time_rate_pct: float
    total_passengers: int
    departures: List[StationFlightMovement]
    arrivals: List[StationFlightMovement]
    standby_reserves_count: int

