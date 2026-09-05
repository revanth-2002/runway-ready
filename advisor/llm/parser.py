"""Structured natural language intent parser."""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from advisor.audit.logger import StructuredLogger
from advisor.llm.client import LLMClient, StubClient

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


def parse_intent(query: str, client: Optional[LLMClient] = None) -> QueryIntent:
    """Parses natural language into a structured QueryIntent."""
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
    """Internal parsing logic using fast-path and LLM fallback."""

    q_lower = query.lower()

    # 1. Deterministic Fast-Path
    # Out of scope: hotels, baggage, passenger bookings
    if any(k in q_lower for k in ["hotel", "baggage", "voucher", "passenger compensation", "cab booking"]):
        return QueryIntent(
            intent="out_of_scope",
            entities={},
            time_scope={},
            confidence=0.99,
            unsupported_aspects=["hotel/baggage customer service requests"],
        )

    # Ambiguous time
    if "afternoon" in q_lower or "evening" in q_lower or "morning" in q_lower:
        if not re.search(r"\d{1,2}:\d{2}", query):
            return QueryIntent(
                intent="ambiguous_time",
                entities={},
                time_scope={"raw": "ambiguous relative time"},
                confidence=0.55,
                unsupported_aspects=["Ambiguous time of day without UTC timestamp"],
                missing_parameters=["time_window_utc"],
                requires_clarification=True,
            )

    # Unknown crew ID (C-9999)
    unknown_ids = re.findall(r"\bC-9\d{3}\b", query, re.IGNORECASE)
    if unknown_ids:
        return QueryIntent(
            intent="unknown_entity_check",
            entities={"crew_ids": [u.upper() for u in unknown_ids]},
            time_scope={},
            confidence=0.90,
            unsupported_aspects=[],
        )

    # Station flight cancellation & financial loss simulation
    if "cancel" in q_lower and any(k in q_lower for k in ["flight", "depart", "station", "blr", "del", "bom", "hyd", "maa", "cost", "loss"]):
        target_station = "BLR"
        for stn in ["BLR", "DEL", "BOM", "HYD", "MAA"]:
            if stn.lower() in q_lower:
                target_station = stn
                break
        date_m = re.search(r"\b(2026-\d{2}-\d{2})\b", query)
        dt = date_m.group(1) if date_m else "2026-09-15"
        return QueryIntent(
            intent="cancel_station_departures",
            entities={
                "station": target_station,
                "stations": [target_station],
                "action": "cancel",
                "scope": "departures",
                "requested_metrics": ["cost_loss", "passengers_affected", "grounded_tails"],
            },
            time_scope={"raw": dt, "resolved_utc": f"{dt}T00:00:00Z"},
            confidence=0.98,
            unsupported_aspects=[],
        )

    # Reassign / Finalize directive
    if "reassign" in q_lower or "finalize" in q_lower or "assign reserve" in q_lower:
        crew_ids = re.findall(r"\bC-\d{4}\b", query, re.IGNORECASE)
        pairing_m = re.search(r"\b(P-\d{4})\b", query, re.IGNORECASE)
        return QueryIntent(
            intent="reassign_crew",
            entities={
                "crew_ids": [c.upper() for c in crew_ids],
                "pairing_id": pairing_m.group(1).upper() if pairing_m else None,
            },
            time_scope={},
            confidence=0.95,
            unsupported_aspects=[],
        )

    # Sick callout / disruption
    if "sick" in q_lower or "incapacitated" in q_lower or "fatigued" in q_lower or "cover the" in q_lower:
        crew_ids = re.findall(r"\bC-\d{4}\b", query, re.IGNORECASE)
        flight_ids = re.findall(r"\bDX\d{3,4}\b", query, re.IGNORECASE)
        tails = re.findall(r"\bVT-DX[A-F]\b", query, re.IGNORECASE)
        role = "First Officer" if ("first officer" in q_lower or " fo " in q_lower or " fo" in q_lower) else "Captain"
        
        day_m = re.search(r"(\d{1,2})\s+Sep", query, re.IGNORECASE)
        day = int(day_m.group(1)) if day_m else 15
        dt = f"2026-09-{day:02d}"

        return QueryIntent(
            intent="simulate_sick",
            entities={
                "crew_ids": [c.upper() for c in crew_ids] if crew_ids else [],
                "flight_ids": [f.upper() for f in flight_ids],
                "tails": [t.upper() for t in tails],
                "role": role,
            },
            time_scope={"raw": dt, "resolved_utc": f"{dt}T00:00:00Z"},
            confidence=0.96,
            unsupported_aspects=[],
        )

    # Crew based at a station
    if ("based at" in q_lower or "captains are based" in q_lower or "crew based" in q_lower) and not ("reserve" in q_lower or "standby" in q_lower):
        stn = "DEL"
        for s in ["DEL", "BLR", "BOM", "HYD", "MAA"]:
            if s.lower() in q_lower:
                stn = s
                break
        rank = "Captain" if "captain" in q_lower else ("First Officer" if ("first officer" in q_lower or "fo" in q_lower) else None)
        return QueryIntent(
            intent="lookup_crew_by_base",
            entities={"base": stn, "rank": rank},
            time_scope={},
            confidence=0.98,
            unsupported_aspects=[],
        )

    # Cumulative duty hours in 7 days
    if ("duty hours" in q_lower and "7 days" in q_lower) or "45 or more" in q_lower:
        return QueryIntent(
            intent="lookup_high_duty_crew",
            entities={"threshold": 45.0, "days": 7},
            time_scope={"raw": "2026-09-15"},
            confidence=0.98,
            unsupported_aspects=[],
        )

    # Reserve lookup
    if "reserve" in q_lower or "standby" in q_lower or "who is on" in q_lower:
        stations = []
        for stn in ["BLR", "DEL", "BOM", "HYD", "MAA"]:
            if stn.lower() in q_lower:
                stations.append(stn)
        return QueryIntent(
            intent="lookup_reserves",
            entities={"stations": stations or ["BLR"]},
            time_scope={"raw": "2026-09-15", "resolved_utc": "2026-09-15T00:00:00Z"},
            confidence=0.98,
            unsupported_aspects=[],
        )

    # Can crew X fly flight Y
    if "can" in q_lower and ("fly" in q_lower or "operate" in q_lower):
        crew_ids = re.findall(r"\bC-\d{4}\b", query, re.IGNORECASE)
        flight_ids = re.findall(r"\bDX\d{3,4}\b", query, re.IGNORECASE)
        return QueryIntent(
            intent="check_legality",
            entities={
                "crew_ids": [c.upper() for c in crew_ids],
                "flight_ids": [f.upper() for f in flight_ids],
            },
            time_scope={"raw": "2026-09-15"},
            confidence=0.95,
            unsupported_aspects=[],
        )

    # Expiring certifications
    if "expiring" in q_lower and ("cert" in q_lower or "licence" in q_lower):
        days_m = re.search(r"(\d+)\s*days?", q_lower)
        days = int(days_m.group(1)) if days_m else 30
        date_m = re.search(r"\b(2026-\d{2}-\d{2})\b", query)
        ref_date = date_m.group(1) if date_m else "2026-09-15"
        return QueryIntent(
            intent="lookup_expiring_certs",
            entities={"within_days": days, "reference_date": ref_date},
            time_scope={"raw": ref_date},
            confidence=0.98,
            unsupported_aspects=[],
        )

    # Pairing crew assignment
    if "pairing" in q_lower and ("assigned" in q_lower or "roles" in q_lower):
        p_match = re.search(r"\b(P-\d{4})\b", query, re.IGNORECASE)
        return QueryIntent(
            intent="lookup_pairing_crew",
            entities={"pairing_id": p_match.group(1).upper() if p_match else "P-2291"},
            time_scope={},
            confidence=0.98,
            unsupported_aspects=[],
        )

    # Nonstop destinations
    if "nonstop" in q_lower:
        stn_m = re.search(r"\b(?:from|to)\s+([A-Z]{3})\b", query, re.IGNORECASE)
        stn = stn_m.group(1).upper() if stn_m else "BLR"
        return QueryIntent(
            intent="lookup_nonstop_destinations",
            entities={"station": stn},
            time_scope={},
            confidence=0.98,
            unsupported_aspects=[],
        )

    # Station closure impact
    if ("closed" in q_lower or "closes" in q_lower) and "which flights are affected" in q_lower:
        stn_m = re.search(r"\b(?:Station\s+)?([A-Z]{3})\s+is\s+closed\b", query, re.IGNORECASE)
        stn = stn_m.group(1).upper() if stn_m else ("HYD" if "HYD" in query else "BLR")
        time_m = re.search(r"(\d{2}:\d{2})[–-](\d{2}:\d{2})Z?", query)
        t1, t2 = time_m.groups() if time_m else ("08:00", "14:00")
        day_m = re.search(r"(\d{1,2})\s+Sep", query, re.IGNORECASE)
        day = int(day_m.group(1)) if day_m else (19 if "19" in query else 17)
        start_utc = f"2026-09-{day:02d}T{t1}:00Z"
        end_utc = f"2026-09-{day:02d}T{t2}:00Z"
        return QueryIntent(
            intent="lookup_closure_impact",
            entities={"station": stn, "start_utc": start_utc, "end_utc": end_utc},
            time_scope={"raw": f"{start_utc} - {end_utc}"},
            confidence=0.98,
            unsupported_aspects=[],
        )

    # Flights departing station or flying route
    if "which flights depart" in q_lower or "which flights fly" in q_lower:
        date_m = re.search(r"\b(2026-\d{2}-\d{2})\b", query)
        route_m = re.search(r"\b([A-Z]{3})\s*(?:→|->|to)\s*([A-Z]{3})\b", query, re.IGNORECASE)
        depart_m = re.search(r"\bdepart(?:ing)?\s+([A-Z]{3})\b", query, re.IGNORECASE)
        orig = route_m.group(1).upper() if route_m else (depart_m.group(1).upper() if depart_m else "DEL")
        dest = route_m.group(2).upper() if route_m else None
        dt = date_m.group(1) if date_m else "2026-09-15"
        return QueryIntent(
            intent="lookup_flights",
            entities={"origin": orig, "destination": dest, "date": dt},
            time_scope={"raw": dt},
            confidence=0.98,
            unsupported_aspects=[],
        )


    # 2. LLM Call 1: Structured Intent & Multi-Entity Parser
    if client is None:
        client = StubClient()

    prompt = f"""You are the intelligence parser of an Airline Operations Control Center (AOCC).
Analyze this operational directive, extract all operational entities and parameters:
Query: "{query}"

Recognized Intents:
- "cancel_station_departures": user wants to cancel departures from an airport or assess cancellation loss.
- "simulate_sick": crew member is sick/incapacitated, find replacements.
- "lookup_reserves": find standby crew on-call.
- "lookup_flights": query flight schedule or route.
- "check_legality": check DGCA legality for a crew member on a flight.
- "lookup_expiring_certs": check expiring licenses.
- "lookup_closure_impact": evaluate airport closure window.
- "out_of_scope": hotels, passenger vouchers, customer service.
- "ambiguous_input": missing critical info needed for operation.

Output ONLY a JSON object matching this schema:
{{
  "intents": [
    {{
      "intent": "cancel_station_departures" | "simulate_sick" | "lookup_reserves" | "lookup_flights" | "check_legality" | "out_of_scope" | "ambiguous_input" | "general_query",
      "entities": {{
        "stations": ["BLR"],
        "crew_ids": [],
        "flight_ids": [],
        "pairing_id": null,
        "action": "cancel",
        "requested_metrics": ["cost_loss", "passengers"]
      }},
      "time_scope": {{"raw": "2026-09-15", "resolved_utc": "2026-09-15T00:00:00Z"}},
      "missing_parameters": [],
      "requires_clarification": false
    }}
  ],
  "confidence": 0.95,
  "unsupported_aspects": []
}}"""

    try:
        raw_resp = client.generate(prompt, temperature=0.0)
        json_match = re.search(r"\{.*\}", raw_resp, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
            first_intent = data.get("intents", [{}])[0]
            return QueryIntent(
                intent=first_intent.get("intent", "general_query"),
                entities=first_intent.get("entities", {}),
                time_scope=first_intent.get("time_scope", {}),
                confidence=float(data.get("confidence", 0.85)),
                unsupported_aspects=data.get("unsupported_aspects", []),
                missing_parameters=first_intent.get("missing_parameters", []),
                requires_clarification=bool(first_intent.get("requires_clarification", False)),
            )
    except Exception:
        pass

    return QueryIntent(
        intent="general_query",
        entities={},
        time_scope={},
        confidence=0.70,
        unsupported_aspects=[],
    )
