"""Catalog of parameterized deterministic tools."""

from pathlib import Path
from typing import Any, Dict, List, Optional

from advisor.data.repository import OpsRepository
from advisor.domain.types import Crew, Flight, Pairing, Reserve


def get_tool_catalog(repo: OpsRepository) -> Dict[str, Any]:
    """Returns a callable dictionary of typed tools wrapping the repository."""
    return {
        "get_crew": repo.get_crew,
        "find_crew": repo.find_crew,
        "get_flight": repo.get_flight,
        "find_flight": repo.find_flight,
        "list_flights": repo.list_flights,
        "get_pairing": repo.get_pairing,
        "get_pairing_for_crew": repo.get_pairing_for_crew,
        "list_reserves": repo.list_reserves,
        "list_certifications": repo.list_certifications,
        "list_ratings": repo.list_ratings,
        "get_duty_clock": repo.get_duty_clock,
        "get_cost_rates": repo.get_cost_rates,
    }
