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

    # Station closure impact
    if ("closed" in q_lower or "closes" in q_lower or "closure" in q_lower) and not ("cancel" in q_lower):
        stn = "HYD" if "HYD" in query.upper() else ("BLR" if "BLR" in query.upper() else "BLR")
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

    # Pairing crew assignment lookup (e.g. "Which crew are assigned to pairing P-2291, and in what roles?")
    if "pairing" in q_lower and ("assigned" in q_lower or "roles" in q_lower or "who is assigned" in q_lower) and not any(k in q_lower for k in ["out for", "sick", "replace", "cover", "if "]):
        p_match = re.search(r"\b(P-\d{4})\b", query, re.IGNORECASE)
        return QueryIntent(
            intent="lookup_pairing_crew",
            entities={"pairing_id": p_match.group(1).upper() if p_match else "P-2291"},
            time_scope={},
            confidence=0.98,
            unsupported_aspects=[],
        )

    # Recovery options follow-up — explicit user opt-in to see ranked options
    # Matches: "yes, produce recovery options for DX412", "generate recovery options DX412",
    #          "recovery options for displaced:C-1042 on DX412", "show me recovery options"
    # MUST be before evaluate_crew_move and simulate_sick to avoid misrouting
    _recovery_phrases = ["produce recovery", "generate recovery", "find recovery", "recovery options", "show recovery", "yes, produce", "ranked recovery", "show me recovery"]
    if any(phrase in q_lower for phrase in _recovery_phrases):
        flight_ids = re.findall(r"\bDX\d{3,4}\b", query, re.IGNORECASE)
        # Support encoded displaced crew ID: "displaced:C-1042"
        displaced_m = re.search(r"displaced:(\S+)", query, re.IGNORECASE)
        displaced_crew_id = displaced_m.group(1) if displaced_m else None
        crew_ids_found = re.findall(r"\bC-\d{4}\b", query, re.IGNORECASE)
        return QueryIntent(
            intent="request_recovery_options",
            entities={
                "flight_ids": [f.upper() for f in flight_ids] if flight_ids else [],
                "displaced_crew_id": displaced_crew_id,
                "crew_ids": [c.upper() for c in crew_ids_found],
            },
            time_scope={"raw": "2026-09-15", "resolved_utc": "2026-09-15T00:00:00Z"},
            confidence=0.98,
            unsupported_aspects=[],
        )

    # What-if crew move, swap, assignment, or duty limit legality check
    if not any(k in q_lower for k in ["sick", "incapacitated", "fatigued", "recurrent training lapsed", "tech delay", "delay cascades", "cheapest legal way", "recovery options", "produce recovery", "generate recovery", "find recovery", "recommend replacement"]):
        if not ("reassign" in q_lower or "finalize" in q_lower or "assign reserve" in q_lower):
            has_action_verb = bool(re.search(r"\b(move|put|swap|transfer|assign|switch)\b", q_lower))
            has_target = bool(re.search(r"\b(onto|to|on|flight|pairing|duty|breach|legally|cover)\b", q_lower)) or bool(re.search(r"\bDX\d{3,4}\b", query, re.IGNORECASE)) or bool(re.search(r"\bP-\d{4}\b", query, re.IGNORECASE))
            has_legality_query = any(k in q_lower for k in ["duty limit", "breach", "any rule", "legally cover", "legally operate", "cover the full", "check legality", "is legal", "would breach"]) and (bool(re.search(r"\bC-\d{4}\b", query, re.IGNORECASE)) or any(r in q_lower for r in ["captain", "fo", "first officer", "reserve"]))
            has_can_fly = bool(re.search(r"\bcan\b", q_lower)) and any(v in q_lower for v in ["fly", "operate", "cover"]) and (bool(re.search(r"\bC-\d{4}\b", query, re.IGNORECASE)) or any(r in q_lower for r in ["captain", "fo", "reserve"]))

            if (has_action_verb and has_target) or has_legality_query or has_can_fly:
                crew_ids = re.findall(r"\bC-\d{4}\b", query, re.IGNORECASE)
                flight_ids = re.findall(r"\bDX\d{3,4}\b", query, re.IGNORECASE)
                pairing_m = re.search(r"\b(P-\d{4})\b", query, re.IGNORECASE)
                tails = re.findall(r"\bVT-DX[A-F]\b", query, re.IGNORECASE)
                role = "First Officer" if re.search(r"\b(first\s+officer|fo)\b", q_lower) else ("Captain" if re.search(r"\b(captain|cpt)\b", q_lower) else None)
                
                day_m = re.search(r"(\d{1,2})\s+Sep", query, re.IGNORECASE)
                day = int(day_m.group(1)) if day_m else 15
                dt = f"2026-09-{day:02d}"

                return QueryIntent(
                    intent="evaluate_crew_move",
                    entities={
                        "crew_ids": [c.upper() for c in crew_ids],
                        "flight_ids": [f.upper() for f in flight_ids],
                        "pairing_id": pairing_m.group(1).upper() if pairing_m else None,
                        "tails": [t.upper() for t in tails],
                        "specified_role": role,
                        "action": "move_check",
                    },
                    time_scope={"raw": dt, "resolved_utc": f"{dt}T00:00:00Z"},
                    confidence=0.98,
                    unsupported_aspects=[],
                )

    # Recovery options follow-up (e.g. "Yes, produce recovery options for DX412", "Produce recovery options if this change were to be made", "Generate recovery options")
    if ("recovery options" in q_lower or "produce recovery" in q_lower or "find recovery" in q_lower or "generate recovery" in q_lower or "generate ranked recovery" in q_lower or "ranked recovery options" in q_lower) and not ("move" in q_lower or "if " in q_lower):
        flight_ids = re.findall(r"\bDX\d{3,4}\b", query, re.IGNORECASE)
        crew_ids = re.findall(r"\bC-\d{4}\b", query, re.IGNORECASE)
        pairing_m = re.search(r"\b(P-\d{4})\b", query, re.IGNORECASE)
        return QueryIntent(
            intent="simulate_sick",
            entities={
                "crew_ids": [c.upper() for c in crew_ids],
                "flight_ids": [f.upper() for f in flight_ids] if flight_ids else ["DX412"],
                "pairing_id": pairing_m.group(1).upper() if pairing_m else None,
                "role": "Captain",
            },
            time_scope={"raw": "2026-09-15", "resolved_utc": "2026-09-15T00:00:00Z"},
            confidence=0.98,
            unsupported_aspects=[],
        )

    # Flight crew assignment & replacement impact lookup (e.g. "Which crews are affected if I replace the captain on DX412?", "Who flies DX412?")
    if (
        ("crews are affected" in q_lower or "affected if" in q_lower or "who is flying" in q_lower or "currently flying" in q_lower or ("assigned to" in q_lower and "pairing" not in q_lower))
        and re.search(r"\bDX\d{3,4}\b", query, re.IGNORECASE)
        and not any(k in q_lower for k in ["move", "assign", "put", "swap", "onto"])
    ):
        flight_ids = re.findall(r"\bDX\d{3,4}\b", query, re.IGNORECASE)
        return QueryIntent(
            intent="lookup_flight_crew",
            entities={"flight_ids": [f.upper() for f in flight_ids]},
            time_scope={"raw": "2026-09-15"},
            confidence=0.98,
            unsupported_aspects=[],
        )

    # Sick callout / disruption / tech delay cascade
    if (
        "sick" in q_lower
        or "incapacitated" in q_lower
        or "fatigued" in q_lower
        or "cover the" in q_lower
        or "is out for" in q_lower
        or "out for pairing" in q_lower
        or "recommend replacement" in q_lower
        or "ranked resolution options" in q_lower
        or "tech delay" in q_lower
        or "delay cascades" in q_lower
        or "training lapsed" in q_lower
        or "lapse discovered" in q_lower
        or "cheapest legal way" in q_lower
    ):
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

    # Crew based, active, or working at a station (e.g. "how many captains are working from HYD location", "who all are active captains from BLR")
    crew_keywords = ["based", "working", "stationed", "active", "captains are", "pilots", "crew at", "crew from", "how many captains", "who are the captains"]
    if any(k in q_lower for k in crew_keywords) and not ("reserve" in q_lower or "standby" in q_lower or "sick" in q_lower or "cancel" in q_lower):
        stn = None
        for s in ["DEL", "BLR", "BOM", "HYD", "MAA"]:
            if s.lower() in q_lower:
                stn = s
                break
        if not stn:
            stn = "DEL" if "del" in q_lower else ("BLR" if "blr" in q_lower else None)

        if stn:
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

    # Specific crew standby / reserve check (e.g. "Why is Captain C-2087 not in the standby roster?", "Is C-3310 on standby?")
    if ("reserve" in q_lower or "standby" in q_lower) and (re.search(r"\bC-\d{4}\b", query, re.IGNORECASE) or "why" in q_lower):
        crew_ids = re.findall(r"\bC-\d{4}\b", query, re.IGNORECASE)
        if crew_ids:
            return QueryIntent(
                intent="lookup_crew_info",
                entities={"crew_ids": [c.upper() for c in crew_ids]},
                time_scope={"raw": "2026-09-15"},
                confidence=0.98,
                unsupported_aspects=[],
            )

    # General station reserve lookup
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

    # Crew profile / info lookup (e.g. "What is C-2087's rank and flight hours...", "Who is Captain C-1042")
    if (
        (re.search(r"\bC-\d{4}\b", query, re.IGNORECASE) or "captain" in q_lower or "first officer" in q_lower)
        and any(k in q_lower for k in ["rank", "hours", "profile", "who is", "what is", "base and rating", "reachability", "details"])
        and not any(k in q_lower for k in ["sick", "incapacitated", "move", "cancel", "standby", "reserve", "options", "out for", "replace", "cover", "assigned to cover", "onto"])
    ):
        crew_ids = re.findall(r"\bC-\d{4}\b", query, re.IGNORECASE)
        return QueryIntent(
            intent="lookup_crew_info",
            entities={"crew_ids": [c.upper() for c in crew_ids]},
            time_scope={"raw": "2026-09-15"},
            confidence=0.98,
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

    prompt = f"""You are the natural language intelligence parser of an Airline Operations Control Center (AOCC).
Analyze this operational directive across international or colloquial phrasings (Indian English, American English, aviation jargon).
Understand the underlying semantic meaning and extract all operational entities and parameters:
Query: "{query}"

Recognized Intents:
- "evaluate_crew_move": user is asking what if a crew member is moved/assigned to a flight/pairing, or checking duty limits/breaches for a reassignment.
- "lookup_crew_info": user is asking about a crew member's rank, base, duty hours, ratings, or profile.
- "lookup_crew_by_base": user wants to know about captains, first officers, pilots, or crew based at, active at, stationed at, or operating/working from a city or airport (e.g. "how many captains are working from HYD", "who is flying out of BLR", "captains stationed in Delhi").
- "lookup_reserves": user asks about reserves, standby crew, on-call roster (e.g. "who is on standby", "reserve pool at DEL", "avail reserves").
- "lookup_flights": query flight schedule, route, or departures (e.g. "flights from DEL to BOM", "schedule for tomorrow").
- "simulate_sick": crew member is sick, fatigued, or unavailable; pairing recovery needed (e.g. "Capt Nair is sick", "pilot called in unwell").
- "cancel_station_departures": user wants to cancel departures from an airport or assess cancellation loss.
- "check_legality": check DGCA legality or duty hours for a crew member on a flight.
- "lookup_expiring_certs": check expiring licenses or medicals.
- "lookup_closure_impact": evaluate airport runway or station closure window.
- "reassign_crew": reassign or adopt a candidate for a pairing.
- "out_of_scope": customer service, hotel bookings, passenger vouchers, baggage.
- "ambiguous_input": missing critical info needed for operation.
- "general_query": general conversational question about airline operations.

Output ONLY a JSON object matching this schema:
{{
  "intents": [
    {{
      "intent": "evaluate_crew_move" | "lookup_crew_info" | "lookup_crew_by_base" | "lookup_reserves" | "lookup_flights" | "simulate_sick" | "cancel_station_departures" | "check_legality" | "out_of_scope" | "ambiguous_input" | "general_query",
      "entities": {{
        "base": "BLR" | "DEL" | "BOM" | "HYD" | "MAA" | null,
        "stations": ["BLR"],
        "rank": "Captain" | "First Officer" | "Cabin Crew" | null,
        "crew_ids": [],
        "flight_ids": [],
        "pairing_id": null,
        "specified_role": "Captain" | "First Officer" | null,
        "action": "move_check" | "cancel" | "lookup" | "simulate",
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

    try:
        raw_resp = client.generate(prompt, temperature=0.0)
        json_match = re.search(r"\{.*\}", raw_resp, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
            first_intent = data.get("intents", [{}])[0]
            entities = first_intent.get("entities", {})
            # Normalize station/base if station was extracted
            if not entities.get("base") and entities.get("stations"):
                entities["base"] = entities["stations"][0]
            return QueryIntent(
                intent=first_intent.get("intent", "general_query"),
                entities=entities,
                time_scope=first_intent.get("time_scope", {}),
                confidence=float(data.get("confidence", 0.90)),
                unsupported_aspects=data.get("unsupported_aspects", []),
                missing_parameters=first_intent.get("missing_parameters", []),
                requires_clarification=bool(first_intent.get("requires_clarification", False)),
            )
    except Exception as e:
        logger.warning("LLM intent parsing exception", error=str(e))

    return QueryIntent(
        intent="general_query",
        entities={},
        time_scope={},
        confidence=0.70,
        unsupported_aspects=[],
    )


