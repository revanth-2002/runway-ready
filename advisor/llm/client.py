import json
import os
import re
from typing import Any, Dict, Optional, Protocol
from advisor.audit.logger import StructuredLogger

# Attempt to load environment variables from .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = StructuredLogger("advisor.llm.client")


class LLMClient(Protocol):
    def generate(self, prompt: str, temperature: float = 0.0) -> str:
        ...


def _extract_relative_date(query: str, default_date: str = "2026-09-15") -> str:
    """Resolves natural language dates (tomorrow, today, 16 Sep) to ISO YYYY-MM-DD."""
    q_lower = query.lower()
    if "day after tomorrow" in q_lower:
        return "2026-09-17"
    if "tomorrow" in q_lower or "next day" in q_lower:
        return "2026-09-16"
    if "yesterday" in q_lower:
        return "2026-09-14"
    if any(k in q_lower for k in ["today", "tonight", "this afternoon", "this morning", "this evening"]):
        return "2026-09-15"

    date_m = re.search(r"\b(2026-\d{2}-\d{2})\b", query)
    if date_m:
        return date_m.group(1)

    day_m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:Sep|September)\b", query, re.IGNORECASE)
    if day_m:
        day = int(day_m.group(1))
        return f"2026-09-{day:02d}"

    sep_day_m = re.search(r"\b(?:Sep|September)\s+(\d{1,2})(?:st|nd|rd|th)?\b", query, re.IGNORECASE)
    if sep_day_m:
        day = int(sep_day_m.group(1))
        return f"2026-09-{day:02d}"

    return default_date


def _extract_time_range(query: str) -> Dict[str, Any]:
    """Extracts operational time windows from natural language expressions."""
    q_lower = query.lower()
    m_12h = re.search(
        r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*(?:to|-|until|till)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",
        q_lower,
    )
    if m_12h:
        h1, min1, ampm1, h2, min2, ampm2 = m_12h.groups()
        h1_int = int(h1) % 12 + (12 if ampm1 == "pm" else 0)
        h2_int = int(h2) % 12 + (12 if ampm2 == "pm" else 0)
        min1_int = int(min1) if min1 else 0
        min2_int = int(min2) if min2 else 0
        return {
            "time_window": f"{h1_int:02d}:{min1_int:02d}-{h2_int:02d}:{min2_int:02d}",
            "start_time": f"{h1_int:02d}:{min1_int:02d}",
            "end_time": f"{h2_int:02d}:{min2_int:02d}",
        }

    m_24h = re.search(r"\b(\d{1,2}:\d{2})\s*(?:to|-|–)\s*(\d{1,2}:\d{2})\b", query)
    if m_24h:
        t1, t2 = m_24h.groups()
        return {"time_window": f"{t1}-{t2}", "start_time": t1, "end_time": t2}

    if "afternoon" in q_lower:
        return {"time_window": "12:00-17:00", "period": "afternoon"}
    if "morning" in q_lower:
        return {"time_window": "06:00-12:00", "period": "morning"}
    if "evening" in q_lower:
        return {"time_window": "17:00-21:00", "period": "evening"}
    if "night" in q_lower:
        return {"time_window": "21:00-04:00", "period": "night"}
    return {}


def _stub_classify_intent(query: str) -> str:
    """Deterministic intent classifier for StubClient supporting offline execution and test suites."""
    q_lower = query.lower()

    # 1. Out of scope
    if any(k in q_lower for k in ["hotel", "baggage", "voucher", "passenger compensation", "cab booking"]):
        return json.dumps({
            "intents": [{
                "intent": "out_of_scope",
                "entities": {},
                "time_scope": {},
                "confidence": 0.99,
                "unsupported_aspects": ["hotel/baggage customer service requests"]
            }],
            "confidence": 0.99,
            "unsupported_aspects": ["hotel bookings", "baggage vouchers"]
        })

    # 2. Ambiguous time: ONLY for completely vague queries lacking station, flight, crew, or time range
    # e.g. "Who can fly sometime in the afternoon?"
    if (
        ("afternoon" in q_lower or "evening" in q_lower or "morning" in q_lower or "sometime" in q_lower)
        and ("who can fly" in q_lower or "someone" in q_lower or "anyone" in q_lower or "who is available" in q_lower)
        and not any(s in q_lower for s in ["blr", "del", "bom", "hyd", "maa", "ccu", "cok", "goi", "station", "airport", "base"])
        and not re.search(r"\b(dx\d{3,4}|p-\d{4}|c-\d{4})\b", query, re.IGNORECASE)
        and not re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", query, re.IGNORECASE)
        and not re.search(r"\b\d{1,2}:\d{2}\b", query)
    ):
        return json.dumps({
            "intents": [{
                "intent": "ambiguous_time",
                "entities": {},
                "time_scope": {"raw": "ambiguous relative time"},
                "confidence": 0.55,
                "unsupported_aspects": ["Ambiguous time of day without UTC timestamp"],
                "missing_parameters": ["time_window_utc"],
                "requires_clarification": True
            }],
            "confidence": 0.55,
            "unsupported_aspects": ["ambiguous time afternoon"]
        })

    # 3. Unknown crew ID
    unknown_ids = re.findall(r"\bC-9\d{3}\b", query, re.IGNORECASE)
    if unknown_ids:
        return json.dumps({
            "intents": [{
                "intent": "unknown_entity_check",
                "entities": {"crew_ids": [u.upper() for u in unknown_ids]},
                "time_scope": {},
                "confidence": 0.90,
                "unsupported_aspects": []
            }],
            "confidence": 0.90,
            "unsupported_aspects": []
        })

    # 4. Station cancellation
    if "cancel" in q_lower and any(k in q_lower for k in ["flight", "depart", "station", "blr", "del", "bom", "hyd", "maa", "cost", "loss", "all"]):
        stn = "BLR"
        for s in ["BLR", "DEL", "BOM", "HYD", "MAA"]:
            if s.lower() in q_lower:
                stn = s
                break
        date_m = re.search(r"\b(2026-\d{2}-\d{2})\b", query)
        dt = date_m.group(1) if date_m else "2026-09-15"
        return json.dumps({
            "intents": [{
                "intent": "cancel_station_departures",
                "entities": {
                    "station": stn,
                    "stations": [stn],
                    "action": "cancel",
                    "scope": "departures",
                    "requested_metrics": ["cost_loss", "passengers_affected", "grounded_tails"]
                },
                "time_scope": {"raw": dt, "resolved_utc": f"{dt}T00:00:00Z"},
                "confidence": 0.98,
                "unsupported_aspects": []
            }],
            "confidence": 0.98,
            "unsupported_aspects": []
        })

    # 5. Reassign crew
    if "reassign" in q_lower or "finalize" in q_lower or "assign reserve" in q_lower:
        crew_ids = re.findall(r"\bC-\d{4}\b", query, re.IGNORECASE)
        pairing_m = re.search(r"\b(P-\d{4})\b", query, re.IGNORECASE)
        return json.dumps({
            "intents": [{
                "intent": "reassign_crew",
                "entities": {
                    "crew_ids": [c.upper() for c in crew_ids],
                    "pairing_id": pairing_m.group(1).upper() if pairing_m else None
                },
                "time_scope": {},
                "confidence": 0.95,
                "unsupported_aspects": []
            }],
            "confidence": 0.95,
            "unsupported_aspects": []
        })

    # 6. Closure impact
    if ("closed" in q_lower or "closes" in q_lower or "closure" in q_lower) and not ("cancel" in q_lower):
        stn = "HYD" if "HYD" in query.upper() else "BLR"
        time_m = re.search(r"(\d{2}:\d{2})[–-](\d{2}:\d{2})Z?", query)
        t1, t2 = time_m.groups() if time_m else ("08:00", "14:00")
        day_m = re.search(r"(\d{1,2})\s+Sep", query, re.IGNORECASE)
        day = int(day_m.group(1)) if day_m else (19 if "19" in query else 17)
        start_utc = f"2026-09-{day:02d}T{t1}:00Z"
        end_utc = f"2026-09-{day:02d}T{t2}:00Z"
        return json.dumps({
            "intents": [{
                "intent": "lookup_closure_impact",
                "entities": {"station": stn, "start_utc": start_utc, "end_utc": end_utc},
                "time_scope": {"raw": f"{start_utc} - {end_utc}"},
                "confidence": 0.98,
                "unsupported_aspects": []
            }],
            "confidence": 0.98,
            "unsupported_aspects": []
        })

    # 7. Pairing crew
    if "pairing" in q_lower and ("assigned" in q_lower or "roles" in q_lower or "who is assigned" in q_lower) and not any(k in q_lower for k in ["out for", "sick", "replace", "cover", "if "]):
        p_match = re.search(r"\b(P-\d{4})\b", query, re.IGNORECASE)
        return json.dumps({
            "intents": [{
                "intent": "lookup_pairing_crew",
                "entities": {"pairing_id": p_match.group(1).upper() if p_match else "P-2291"},
                "time_scope": {},
                "confidence": 0.98,
                "unsupported_aspects": []
            }],
            "confidence": 0.98,
            "unsupported_aspects": []
        })

    # 8. Request recovery options (explicit opt-in)
    _rec_phrases = ["produce recovery", "generate recovery", "find recovery", "recovery options", "show recovery", "yes, produce", "ranked recovery", "show me recovery"]
    if any(phrase in q_lower for phrase in _rec_phrases):
        flight_ids = re.findall(r"\bDX\d{3,4}\b", query, re.IGNORECASE)
        displaced_m = re.search(r"displaced:(\S+)", query, re.IGNORECASE)
        displaced_crew_id = displaced_m.group(1) if displaced_m else None
        crew_ids_found = re.findall(r"\bC-\d{4}\b", query, re.IGNORECASE)
        return json.dumps({
            "intents": [{
                "intent": "request_recovery_options",
                "entities": {
                    "flight_ids": [f.upper() for f in flight_ids] if flight_ids else [],
                    "displaced_crew_id": displaced_crew_id,
                    "crew_ids": [c.upper() for c in crew_ids_found]
                },
                "time_scope": {"raw": "2026-09-15", "resolved_utc": "2026-09-15T00:00:00Z"},
                "confidence": 0.98,
                "unsupported_aspects": []
            }],
            "confidence": 0.98,
            "unsupported_aspects": []
        })

    # 9. Evaluate crew move / what-if legality
    if not any(k in q_lower for k in ["sick", "incapacitated", "fatigued", "recurrent training lapsed", "tech delay", "delay cascades", "cheapest legal way"]):
        if not ("reassign" in q_lower or "finalize" in q_lower or "assign reserve" in q_lower):
            move_triggers = [
                "if i move", "if captain", "if fo", "if crew", "if c-",
                "move fo", "move captain", "move c-", "put c-", "put captain", "put fo",
                "assign fo", "assign captain", "assign c-", "assigning",
                "breach a duty limit", "breach duty limit", "breaches duty limit", "duty limit breach",
                "does anyone breach", "who breaches", "breach any rule", "check duty limit",
                "legality of moving", "can c-", "can captain", "can fo", "can reserve",
                "legally cover", "legally operate", "cover p-", "operate their rostered"
            ]
            has_trigger = any(t in q_lower for t in move_triggers) or (
                ("move" in q_lower or "assign" in q_lower or "swap" in q_lower or "put" in q_lower or "cover" in q_lower)
                and ("onto" in q_lower or "to" in q_lower or "for" in q_lower)
                and ("dx" in q_lower or "p-" in q_lower or "flight" in q_lower or "pairing" in q_lower)
            ) or (
                ("can " in q_lower or "could " in q_lower)
                and ("legally" in q_lower or "operate" in q_lower or "cover" in q_lower)
            )
            if has_trigger:
                crew_ids = re.findall(r"\bC-\d{4}\b", query, re.IGNORECASE)
                flight_ids = re.findall(r"\bDX\d{3,4}\b", query, re.IGNORECASE)
                pairing_m = re.search(r"\b(P-\d{4})\b", query, re.IGNORECASE)
                tails = re.findall(r"\bVT-DX[A-F]\b", query, re.IGNORECASE)
                role = "First Officer" if ("first officer" in q_lower or " fo " in q_lower or " fo" in q_lower) else ("Captain" if "captain" in q_lower else None)
                return json.dumps({
                    "intents": [{
                        "intent": "evaluate_crew_move",
                        "entities": {
                            "crew_ids": [c.upper() for c in crew_ids],
                            "flight_ids": [f.upper() for f in flight_ids],
                            "pairing_id": pairing_m.group(1).upper() if pairing_m else None,
                            "tails": [t.upper() for t in tails],
                            "specified_role": role,
                            "action": "move_check"
                        },
                        "time_scope": {"raw": "2026-09-15"},
                        "confidence": 0.98,
                        "unsupported_aspects": []
                    }],
                    "confidence": 0.98,
                    "unsupported_aspects": []
                })

    # 10. Crew info / profile lookup
    if (
        (re.search(r"\bC-\d{4}\b", query, re.IGNORECASE) or "captain" in q_lower or "first officer" in q_lower)
        and any(k in q_lower for k in ["rank", "hours", "profile", "who is", "what is", "base and rating", "reachability", "details", "on-call window", "score", "why", "not in the standby", "not on standby", "standby roster"])
        and not any(k in q_lower for k in ["sick", "incapacitated", "move", "cancel", "out for", "replace", "cover", "assigned to cover", "onto"])
    ):
        crew_ids = re.findall(r"\bC-\d{4}\b", query, re.IGNORECASE)
        return json.dumps({
            "intents": [{
                "intent": "lookup_crew_info",
                "entities": {"crew_ids": [c.upper() for c in crew_ids]},
                "time_scope": {"raw": "2026-09-15"},
                "confidence": 0.98,
                "unsupported_aspects": []
            }],
            "confidence": 0.98,
            "unsupported_aspects": []
        })

    # 11. Disruption simulation (sick, incapacitated, training lapsed, tech delay)
    if (
        "sick" in q_lower
        or "unwell" in q_lower
        or "incapacitated" in q_lower
        or "fatigued" in q_lower
        or "fatigue" in q_lower
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
        return json.dumps({
            "intents": [{
                "intent": "simulate_sick",
                "entities": {
                    "crew_ids": [c.upper() for c in crew_ids],
                    "flight_ids": [f.upper() for f in flight_ids],
                    "tails": [t.upper() for t in tails],
                    "role": role
                },
                "time_scope": {"raw": dt, "resolved_utc": f"{dt}T00:00:00Z"},
                "confidence": 0.96,
                "unsupported_aspects": []
            }],
            "confidence": 0.96,
            "unsupported_aspects": []
        })

    # 12. Crew by base
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
            return json.dumps({
                "intents": [{
                    "intent": "lookup_crew_by_base",
                    "entities": {"base": stn, "rank": rank},
                    "time_scope": {},
                    "confidence": 0.98,
                    "unsupported_aspects": []
                }],
                "confidence": 0.98,
                "unsupported_aspects": []
            })

    # 13. High duty crew
    if ("duty hours" in q_lower and "7 days" in q_lower) or "45 or more" in q_lower:
        return json.dumps({
            "intents": [{
                "intent": "lookup_high_duty_crew",
                "entities": {"threshold": 45.0, "days": 7},
                "time_scope": {"raw": "2026-09-15"},
                "confidence": 0.98,
                "unsupported_aspects": []
            }],
            "confidence": 0.98,
            "unsupported_aspects": []
        })

    # 14. Reserves / Available lookup
    _res_triggers = [
        "reserve", "standby", "who is on", "available in", "available at",
        "available crew", "currently available", "who all are available",
        "who is available", "availability at", "availability in", "available at station",
        "available at base", "available at airport", "who are available",
    ]
    is_reserve_query = any(k in q_lower for k in _res_triggers) or (
        "available" in q_lower and any(s in q_lower for s in ["blr", "del", "bom", "hyd", "maa", "airport", "station", "base", "bangalore", "delhi", "mumbai", "hyderabad", "chennai"])
    )
    if is_reserve_query and not any(k in q_lower for k in ["move", "swap", "onto", "assign to", "can fly", "hotel", "baggage"]):
        stations = []
        station_aliases = {
            "blr": "BLR", "bangalore": "BLR", "bengaluru": "BLR",
            "del": "DEL", "delhi": "DEL", "new delhi": "DEL",
            "bom": "BOM", "mumbai": "BOM", "bombay": "BOM",
            "hyd": "HYD", "hyderabad": "HYD",
            "maa": "MAA", "chennai": "MAA", "madras": "MAA",
        }
        for alias, code in station_aliases.items():
            if re.search(r"\b" + re.escape(alias) + r"\b", q_lower):
                if code not in stations:
                    stations.append(code)
        for stn in ["BLR", "DEL", "BOM", "HYD", "MAA"]:
            if stn in query.upper() and stn not in stations:
                stations.append(stn)
        dt = _extract_relative_date(query)
        return json.dumps({
            "intents": [{
                "intent": "lookup_reserves",
                "entities": {"stations": stations or ["BLR"], "date": dt},
                "time_scope": {"raw": dt, "resolved_utc": f"{dt}T00:00:00Z"},
                "confidence": 0.98,
                "unsupported_aspects": []
            }],
            "confidence": 0.98,
            "unsupported_aspects": []
        })

    # 15. Expiring certifications / licenses / medicals / recurrent training
    is_expiring_query = (
        any(k in q_lower for k in ["expir", "licen", "medical", "recurrent", "dangerous goods", "cert"])
        and any(k in q_lower for k in ["list", "who", "which", "show", "check", "whose", "next", "within", "days", "due", "lapse", "renew", "all", "pilot", "crew", "captain"])
        and not any(k in q_lower for k in ["sick", "unwell", "incapacitated", "move", "cancel", "closure"])
    )
    if is_expiring_query:
        days_m = re.search(r"(\d+)\s*days?", q_lower)
        days = int(days_m.group(1)) if days_m else 30
        ref_date = _extract_relative_date(query)

        stn = None
        for s in ["BLR", "DEL", "BOM", "HYD", "MAA"]:
            if re.search(rf"\b(?:for|at|in|base)?\s*{s}\b", query, re.IGNORECASE) and s.lower() in q_lower:
                stn = s
                break

        cert_type = None
        if "licen" in q_lower:
            cert_type = "licence"
        elif "medical" in q_lower:
            cert_type = "medical"
        elif "training" in q_lower or "recurrent" in q_lower:
            cert_type = "recurrent_training"

        entities = {"within_days": days, "reference_date": ref_date}
        if stn:
            entities["base"] = stn
            entities["station"] = stn
            entities["stations"] = [stn]
        if cert_type:
            entities["cert_type"] = cert_type

        return json.dumps({
            "intents": [{
                "intent": "lookup_expiring_certs",
                "entities": entities,
                "time_scope": {"raw": ref_date},
                "confidence": 0.98,
                "unsupported_aspects": []
            }],
            "confidence": 0.98,
            "unsupported_aspects": []
        })

    # 16. Nonstop destinations
    if "nonstop" in q_lower:
        stn_m = re.search(r"\b(?:from|to)\s+([A-Z]{3})\b", query, re.IGNORECASE)
        stn = stn_m.group(1).upper() if stn_m else "BLR"
        return json.dumps({
            "intents": [{
                "intent": "lookup_nonstop_destinations",
                "entities": {"station": stn},
                "time_scope": {},
                "confidence": 0.98,
                "unsupported_aspects": []
            }],
            "confidence": 0.98,
            "unsupported_aspects": []
        })

    # 17. Flights lookup
    if (
        "flights depart" in q_lower
        or "flights fly" in q_lower
        or "which flights" in q_lower
        or "which aircraft operates" in q_lower
        or ("flights" in q_lower and any(k in q_lower for k in ["depart", "fly", "schedule", "from", "to", "del", "blr", "bom", "hyd", "maa"]))
        or ("how many flights" in q_lower)
    ):
        dt = _extract_relative_date(query)
        t_info = _extract_time_range(query)
        route_m = re.search(r"\b([A-Z]{3})\s*(?:→|->|to)\s*([A-Z]{3})\b", query, re.IGNORECASE)
        depart_m = re.search(r"\bdepart(?:ing)?\s+([A-Z]{3})\b", query, re.IGNORECASE)
        from_m = re.search(r"\bfrom\s+([A-Z]{3})\b", query, re.IGNORECASE)
        
        orig = route_m.group(1).upper() if route_m else (depart_m.group(1).upper() if depart_m else (from_m.group(1).upper() if from_m else None))
        dest = route_m.group(2).upper() if route_m else None
        
        if not orig:
            for s in ["DEL", "BLR", "BOM", "HYD", "MAA", "CCU", "COK", "GOI"]:
                if s.lower() in q_lower:
                    orig = s
                    break
        if not orig:
            orig = "DEL"

        entities = {"origin": orig, "destination": dest, "date": dt, **t_info}
        return json.dumps({
            "intents": [{
                "intent": "lookup_flights",
                "entities": entities,
                "time_scope": {"raw": dt, **t_info},
                "confidence": 0.98,
                "unsupported_aspects": []
            }],
            "confidence": 0.98,
            "unsupported_aspects": []
        })

    # 18. Default general query
    return json.dumps({
        "intents": [{"intent": "general_query", "entities": {}, "time_scope": {}}],
        "confidence": 0.85,
        "unsupported_aspects": []
    })


class StubClient:
    """Deterministic offline LLM client returning structured JSON or slotted prose."""

    def generate(self, prompt: str, temperature: float = 0.0) -> str:
        prompt_lower = prompt.lower()

        # 1. Intent parsing prompts
        if (
            "natural language intelligence parser" in prompt_lower
            or "extract intent" in prompt_lower
            or "queryintent" in prompt_lower
            or "recognized intents" in prompt_lower
            or "extract all operational entities" in prompt_lower
        ):
            q_m = re.search(r'Query:\s*"(.*?)"', prompt, re.DOTALL)
            q_str = q_m.group(1) if q_m else prompt
            return _stub_classify_intent(q_str)

        # 2. Slotted prose rendering prompts
        if "summarize the evidence bundle" in prompt_lower or "slot" in prompt_lower:
            return (
                "Captain {{impact.crew_id}} is incapacitated for {{impact.date}}. "
                "This breaks pairing {{impact.pairing_id}}, leaving {{impact.uncrewed_count}} flights uncrewed "
                "and stranding {{impact.passengers_affected}} passengers. "
                "Option 1: Assign on-base reserve {{options.0.crew_id}} at a cost of ₹{{options.0.cost_inr}}. "
                "Candidate {{options.1.crew_id}} breaches duty limit — {{options.1.repair.text}}."
            )

        if "airline operations control center" in prompt_lower or "aocc" in prompt_lower:
            return ""

        return "Query processed successfully."


class GeminiClientWrapper:
    """Google Gemini API wrapper using the official google-genai SDK."""

    def __init__(self, api_key: str, model_name: str = "gemini-2.5-flash", timeout_ms: int = 30000):
        from google import genai
        from google.genai import types

        self.api_key = api_key
        self.model_name = model_name
        self.timeout_ms = timeout_ms

        http_options = types.HttpOptions(
            timeout=timeout_ms,
            retry_options=types.HttpRetryOptions(attempts=1),
        )
        self.client = genai.Client(api_key=api_key, http_options=http_options)

    def generate(self, prompt: str, temperature: float = 0.0, max_output_tokens: int = 300) -> str:
        from google.genai import types

        # Disable internal reasoning loop to eliminate 30-60s thinking token latency
        try:
            thinking_cfg = types.ThinkingConfig(thinking_budget=0)
        except Exception:
            thinking_cfg = None

        config_kwargs: Dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }
        if thinking_cfg is not None:
            config_kwargs["thinking_config"] = thinking_cfg

        config = types.GenerateContentConfig(**config_kwargs)
        try:
            resp = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=config,
            )
            return resp.text or ""
        except Exception as e:
            logger.warning(
                "Gemini generate_content failed or timed out, falling back to StubClient",
                model=self.model_name,
                error=str(e),
            )
            return StubClient().generate(prompt, temperature=temperature)


def get_gemini_config() -> Dict[str, Optional[str]]:
    """Fetches Gemini API key and model name from the environment."""
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    model_name = (
        os.environ.get("GEMINI_MODEL")
        or os.environ.get("GEMINI_MODEL_NAME")
        or "gemini-2.5-flash"
    )
    return {"api_key": api_key, "model_name": model_name}


def get_active_llm_info() -> Dict[str, Any]:
    """Returns metadata about the active LLM engine for logging and UI display."""
    cfg = get_gemini_config()
    if cfg["api_key"]:
        return {
            "provider": "gemini",
            "model": cfg["model_name"],
            "configured": True,
        }
    return {
        "provider": "stub",
        "model": "offline-deterministic",
        "configured": False,
    }


_GEMINI_CLIENT: Optional[GeminiClientWrapper] = None


def init_gemini_client(force: bool = False) -> Optional[GeminiClientWrapper]:
    """Initializes the single persistent Gemini connection once at server startup."""
    global _GEMINI_CLIENT
    if _GEMINI_CLIENT is not None and not force:
        return _GEMINI_CLIENT

    cfg = get_gemini_config()
    api_key = cfg["api_key"]
    model_name = cfg["model_name"] or "gemini-2.5-flash"

    if api_key:
        try:
            _GEMINI_CLIENT = GeminiClientWrapper(api_key=api_key, model_name=model_name)
            logger.info("Initialized persistent Gemini API connection", model=model_name)
            return _GEMINI_CLIENT
        except Exception as e:
            logger.warning(
                "Failed to initialize persistent Gemini client, falling back to StubClient",
                error=str(e),
                model=model_name,
            )
            _GEMINI_CLIENT = None
            return None

    _GEMINI_CLIENT = None
    return None


def get_default_llm_client() -> LLMClient:
    """Returns the single persistent Gemini client if configured, else StubClient."""
    global _GEMINI_CLIENT
    cfg = get_gemini_config()
    api_key = cfg["api_key"]
    model_name = cfg["model_name"] or "gemini-2.5-flash"

    if not api_key:
        return StubClient()

    if (
        _GEMINI_CLIENT is not None
        and _GEMINI_CLIENT.api_key == api_key
        and _GEMINI_CLIENT.model_name == model_name
    ):
        return _GEMINI_CLIENT

    client = init_gemini_client(force=True)
    if client is not None:
        return client

    logger.info("Operating in deterministic offline mode with StubClient")
    return StubClient()


# Initialize single persistent connection once on startup
init_gemini_client()

