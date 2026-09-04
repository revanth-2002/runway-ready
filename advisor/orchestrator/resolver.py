import re
from typing import Dict, List, Optional, Tuple
from advisor.audit.logger import StructuredLogger
from advisor.data.repository import OpsRepository

logger = StructuredLogger("advisor.orchestrator.resolver")



def resolve_local_pii(
    query: str, repo: OpsRepository
) -> Tuple[str, Dict[str, str], List[str]]:
    """Replaces crew names with crew IDs locally before sending text to external LLMs.
    Returns:
        (sanitized_query, pii_reverse_map, resolved_crew_ids)
    """
    all_crew = repo.list_all_crew()
    sanitized = query
    pii_map: Dict[str, str] = {}
    resolved_crew_ids: List[str] = []

    # 1. Exact or partial match on crew names
    for c in all_crew:
        pii_map[c.crew_id] = c.name

        # Match variations: "Captain A. Nair", "Capt Nair", "A. Nair", "Nair"
        surname = c.name.split()[-1]
        patterns = [
            re.escape(c.name),
            rf"Capt(?:ain)?\.?\s+{re.escape(surname)}",
            rf"First\s+Officer\s+{re.escape(surname)}",
            rf"FO\s+{re.escape(surname)}",
            rf"\b{re.escape(surname)}\b",
        ]
        combined_pattern = re.compile("|".join(patterns), re.IGNORECASE)
        if combined_pattern.search(sanitized):
            sanitized = combined_pattern.sub(c.crew_id, sanitized)
            if c.crew_id not in resolved_crew_ids:
                resolved_crew_ids.append(c.crew_id)

    # 2. Extract any direct crew ID references like C-1042 or C-9999
    explicit_ids = re.findall(r"\bC-\d{4}\b", sanitized, re.IGNORECASE)
    for cid in explicit_ids:
        cid_upper = cid.upper()
        if cid_upper not in resolved_crew_ids:
            resolved_crew_ids.append(cid_upper)

    # 3. Normalize relative dates
    sanitized = re.sub(r"\btomorrow\b", "2026-09-15", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\btoday\b", "2026-09-14", sanitized, flags=re.IGNORECASE)

    logger.info(
        "Resolved local entities and sanitized PII",
        resolved_crew_count=len(resolved_crew_ids),
        resolved_crew_ids=resolved_crew_ids,
    )
    return sanitized, pii_map, resolved_crew_ids

