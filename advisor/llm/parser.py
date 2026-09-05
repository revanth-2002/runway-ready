"""Structured natural language intent parser."""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from advisor.audit.logger import StructuredLogger
from advisor.domain.exceptions import LLMUnavailableError
from advisor.llm.client import LLMClient, StubClient, get_default_llm_client

logger = StructuredLogger("advisor.llm.parser")


@dataclass(frozen=True)
class QueryIntent:
    intent: str
    entities: Dict[str, Any]
    time_scope: Dict[str, Any]
    confidence: float
    unsupported_aspects: List[str] = field(default_factory=list)
    missing_parameters: List[str] = field(default_factory=list)
    requires_clarification: bool = False
    # Set when the external LLM could not be reached and this intent came from the
    # deterministic offline classifier instead. Logged, not shown to the controller.
    degraded: bool = False


def parse_intent(query: str, client: Optional[LLMClient] = None) -> QueryIntent:
    """Parses natural language into a structured QueryIntent using direct LLM classification."""
    result = _parse_intent_internal(query, client)
    logger.info(
        "Parsed query intent",
        intent=result.intent,
        confidence=result.confidence,
        entities=result.entities,
        missing_parameters=result.missing_parameters,
    )
    return result


def _parse_intent_internal(query: str, client: Optional[LLMClient] = None) -> QueryIntent:
    """Classifies user intent and extracts operational entities via direct LLM inference."""
    if client is None:
        client = get_default_llm_client()

    prompt = f"""You are the natural language intelligence parser of an Airline Operations Control Center (AOCC).
Analyze this operational directive across international or colloquial phrasings (Indian English, American English, aviation jargon).
Understand the underlying semantic meaning and extract all operational entities, dates, and parameters.

Current Operations Snapshot Date: 2026-09-15 (Today)
- "tomorrow" resolves to "2026-09-16"
- "yesterday" resolves to "2026-09-14"
- "today", "this afternoon", "this morning", "tonight" resolve to "2026-09-15"
- Expressions like "12pm to 2pm", "12:00-14:00", "afternoon", "morning" extract time_window and start/end times.

Query: "{query}"

Recognized Intents:
- "evaluate_crew_move": user is asking what if a crew member is moved/assigned to a flight/pairing, or checking duty limits/breaches for a reassignment (e.g. "If I move FO C-2087 onto DX412, does anyone breach a duty limit?").
- "request_recovery_options": user is asking for recovery plans, recommendations, standby crew options, or how to resolve a disruption.
- "lookup_crew_info": user is asking about a crew member's rank, base, duty hours, ratings, profile, or standby status (e.g. "Why is Captain C-2087 not in the standby roster?").
- "lookup_crew_by_base": user wants to know about captains, first officers, pilots, or crew based at, active at, stationed at, or operating/working from a city or airport (e.g. "how many captains are working from HYD", "who is flying out of BLR", "captains stationed in Delhi").
- "lookup_reserves": user asks about reserves, standby crew, on-call roster (e.g. "who is on standby", "reserve pool at DEL", "avail reserves", "who all are currently available in BLR airport", "who is on reserve tomorrow").
- "lookup_flights": query flight schedule, route, or departures (e.g. "flights from DEL to BOM", "schedule for tomorrow", "Which flights depart DEL this afternoon 12pm to 2pm ?").
- "lookup_pairing_crew": query crew members currently assigned to a specific pairing or flight.
- "lookup_expiring_certs": user asks for expiring licenses, ratings, medical certificates, or recurrent training across a specific base or fleet-wide across the entire database (e.g. "List crew whose licence expires in the next 30 days for BLR", "List all certifications expiring within 30 days of 2026-09-15").
- "lookup_nonstop_destinations": query nonstop destinations or city pairs served from a station.
- "lookup_closure_impact": evaluate airport runway, airspace, or station closure window.
- "lookup_high_duty_crew": query crew members with high duty hours or near duty limits.
- "simulate_sick": crew member is sick, fatigued, or unavailable; pairing recovery needed (e.g. "Capt Nair is sick", "pilot called in unwell").
- "cancel_station_departures": user wants to cancel departures from an airport or assess cancellation loss.
- "check_legality": check DGCA legality or duty hours for a crew member on a flight.
- "reassign_crew": reassign or finalize adoption of a candidate for a pairing.
- "out_of_scope": customer service, hotel bookings, passenger vouchers, baggage.
- "ambiguous_time": strictly ONLY for completely vague, unresolvable queries lacking flight, crew, station, and concrete parameters (e.g. "Who can fly sometime in the afternoon?").
- "unknown_entity_check": references non-existent or unknown crew IDs/flight numbers.
- "general_query": general conversational question about airline operations.

Output ONLY a JSON object matching this schema:
{{
  "intents": [
    {{
      "intent": "evaluate_crew_move" | "request_recovery_options" | "lookup_crew_info" | "lookup_crew_by_base" | "lookup_reserves" | "lookup_flights" | "lookup_pairing_crew" | "lookup_expiring_certs" | "lookup_nonstop_destinations" | "lookup_closure_impact" | "lookup_high_duty_crew" | "simulate_sick" | "cancel_station_departures" | "check_legality" | "reassign_crew" | "out_of_scope" | "ambiguous_time" | "unknown_entity_check" | "general_query",
      "entities": {{
        "base": "BLR" | "DEL" | "BOM" | "HYD" | "MAA" | null,
        "stations": ["BLR"],
        "origin": "DEL" | "BLR" | "BOM" | "HYD" | "MAA" | null,
        "destination": "BLR" | "DEL" | "BOM" | "HYD" | "MAA" | null,
        "date": "2026-09-15",
        "time_window": "12:00-14:00" | null,
        "start_time": "12:00" | null,
        "end_time": "14:00" | null,
        "rank": "Captain" | "First Officer" | "Cabin Crew" | null,
        "crew_ids": [],
        "flight_ids": [],
        "pairing_id": null,
        "displaced_crew_id": null,
        "tails": [],
        "specified_role": "Captain" | "First Officer" | null,
        "action": "move_check" | "cancel" | "lookup" | "simulate" | "reassign",
        "requested_metrics": ["duty_limits", "cost_loss", "passengers"]
      }},
      "time_scope": {{"raw": "2026-09-15", "resolved_utc": "2026-09-15T00:00:00Z"}},
      "missing_parameters": [],
      "requires_clarification": false
    }}
  ],
  "confidence": 0.95,
  "unsupported_aspects": []
}}"""

    degraded = False
    raw_resp = ""

    try:
        raw_resp = client.generate(prompt, temperature=0.0)
    except LLMUnavailableError as e:
        # Provider is down or rate-limited. Classify offline rather than collapsing to
        # "general_query" — the deterministic classifier still routes most directives
        # correctly. The degradation is logged, not surfaced to the controller.
        degraded = True
        logger.warning(
            "LLM unavailable for intent parsing, using deterministic classifier",
            error=str(e),
            is_rate_limit=e.is_rate_limit,
        )
        try:
            raw_resp = StubClient().generate(prompt, temperature=0.0)
        except Exception as stub_err:  # pragma: no cover - stub is pure-python
            logger.error("Deterministic intent classifier failed", error=str(stub_err))
    except Exception as e:
        logger.warning("LLM intent parsing exception", error=str(e))

    parsed = _intent_from_response(raw_resp, degraded)
    if parsed is not None:
        return parsed

    return QueryIntent(
        intent="general_query",
        entities={},
        time_scope={},
        confidence=0.70,
        unsupported_aspects=[],
        degraded=degraded,
    )


def _intent_from_response(
    raw_resp: str, degraded: bool = False
) -> Optional[QueryIntent]:
    """Parses a raw JSON intent response into a QueryIntent, or None if unparseable."""
    if not raw_resp:
        return None

    json_match = re.search(r"\{.*\}", raw_resp, re.DOTALL)
    if not json_match:
        return None

    try:
        data = json.loads(json_match.group(0))
    except json.JSONDecodeError as e:
        # Most commonly a response truncated by max_output_tokens.
        logger.warning(
            "Intent response was not valid JSON (possibly truncated)",
            error=str(e),
            response_length=len(raw_resp),
        )
        return None

    first_intent = data.get("intents", [{}])[0]
    entities = first_intent.get("entities", {})

    # Normalize base and stations
    if not entities.get("base") and entities.get("stations"):
        entities["base"] = entities["stations"][0]
    elif entities.get("base") and not entities.get("stations"):
        entities["stations"] = [entities["base"]]

    # Confidence resolution
    confidence = float(first_intent.get("confidence", data.get("confidence", 0.90)))

    unsupported = first_intent.get("unsupported_aspects", data.get("unsupported_aspects", []))
    missing = first_intent.get("missing_parameters", [])
    req_clarify = bool(first_intent.get("requires_clarification", False))

    return QueryIntent(
        intent=first_intent.get("intent", "general_query"),
        entities=entities,
        time_scope=first_intent.get("time_scope", {}),
        confidence=confidence,
        unsupported_aspects=unsupported,
        missing_parameters=missing,
        requires_clarification=req_clarify,
        degraded=degraded,
    )
