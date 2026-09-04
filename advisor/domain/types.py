"""Frozen domain entities for Crew Ops Advisor."""

from dataclasses import dataclass, field
from typing import Optional, Tuple


@dataclass(frozen=True)
class Crew:
    crew_id: str
    name: str
    rank: str
    base: str
    seniority: Optional[int] = None
    reachability_minutes: Optional[int] = None


@dataclass(frozen=True)
class Flight:
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


@dataclass(frozen=True)
class PairingLeg:
    pairing_id: str
    leg_seq: int
    flight_id: str
    duty_id: Optional[str] = None


@dataclass(frozen=True)
class Pairing:
    pairing_id: str
    base: Optional[str]
    start_utc: str
    end_utc: str
    legs: Tuple[Flight, ...] = ()


@dataclass(frozen=True)
class Assignment:
    crew_id: str
    pairing_id: str
    role: str


@dataclass(frozen=True)
class Duty:
    duty_id: str
    pairing_id: str
    crew_id: str
    start_utc: str
    end_utc: str
    duty_minutes: int
    block_minutes: int
    sectors: int


@dataclass(frozen=True)
class DutyClock:
    crew_id: str
    duty_hours_7d: float
    flight_hours_28d: float
    last_rest_ended: Optional[str] = None


@dataclass(frozen=True)
class Certification:
    crew_id: str
    cert_type: str
    valid_from: Optional[str]
    expires_on: str


@dataclass(frozen=True)
class Reserve:
    crew_id: str
    base: str
    oncall_start_utc: str
    oncall_end_utc: str
    standby_status: str


@dataclass(frozen=True)
class DutyProposal:
    proposal_id: str
    flight_id: Optional[str] = None
    pairing_id: Optional[str] = None
    flights: Tuple[Flight, ...] = ()
    start_utc: str = ""
    end_utc: str = ""
    duty_minutes: int = 0
    block_minutes: int = 0
    sectors: int = 0
    passengers: int = 0
    is_deadhead: bool = False
