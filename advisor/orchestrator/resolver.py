import re
from typing import Dict, List, Optional, Tuple
from collections import Counter
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
    pii_map: Dict[str, str] = {c.crew_id: c.name for c in all_crew}
    resolved_crew_ids: List[str] = []

    # 1. First extract explicit crew IDs like C-1042, C-2087, C-9999
    explicit_ids = re.findall(r"\bC-\d{4}\b", sanitized, re.IGNORECASE)
    for cid in explicit_ids:
        cid_upper = cid.upper()
        if cid_upper not in resolved_crew_ids:
            resolved_crew_ids.append(cid_upper)

    # Count frequency of surnames across the fleet
    surname_counts = Counter(c.name.split()[-1] for c in all_crew)

    # Build prioritized candidate match patterns: (pattern, crew_id, priority_length)
    match_candidates = []

    for c in all_crew:
        surname = c.name.split()[-1]
        initial = c.name.split()[0] if len(c.name.split()) > 1 else ""

        # Rank-specific prefixes
        rank_prefixes = []
        if "captain" in c.rank.lower():
            rank_prefixes = [r"Capt(?:ain)?\.?"]
        elif "first officer" in c.rank.lower():
            rank_prefixes = [r"First\s+Officer", r"FO"]
        elif "senior cabin crew" in c.rank.lower():
            rank_prefixes = [r"Senior\s+Cabin\s+Crew", r"SCC"]
        elif "cabin crew" in c.rank.lower():
            rank_prefixes = [r"Cabin\s+Crew", r"CC"]

        # Form 1: Rank + Full Name (e.g. "Captain R. Iyer", "FO D. Iyer")
        for rp in rank_prefixes:
            p = rf"\b{rp}\s+{re.escape(c.name)}\b"
            match_candidates.append((p, c.crew_id, len(c.name) + 15))
            if initial:
                # without dot e.g. "Captain R Iyer"
                p_nodot = rf"\b{rp}\s+{re.escape(initial.replace('.', ''))}\s+{re.escape(surname)}\b"
                match_candidates.append((p_nodot, c.crew_id, len(c.name) + 14))

        # Form 2: Exact Full Name (e.g. "R. Iyer", "A. Nair", "R Iyer")
        match_candidates.append((rf"\b{re.escape(c.name)}\b", c.crew_id, len(c.name) + 10))
        if initial:
            match_candidates.append(
                (rf"\b{re.escape(initial.replace('.', ''))}\s+{re.escape(surname)}\b", c.crew_id, len(c.name) + 9)
            )

        # Form 3: Rank + Surname (e.g. "Captain Nair") if unique for that rank+surname
        for rp in rank_prefixes:
            match_candidates.append((rf"\b{rp}\s+{re.escape(surname)}\b", c.crew_id, len(surname) + 8))

        # Form 4: Pure Surname if unique in the entire airline
        if surname_counts[surname] == 1:
            match_candidates.append((rf"\b{re.escape(surname)}\b", c.crew_id, len(surname)))

    # Sort candidates by specificity length descending
    match_candidates.sort(key=lambda x: x[2], reverse=True)

    matched_spans = []
    for pattern_str, cid, _ in match_candidates:
        pattern = re.compile(pattern_str, re.IGNORECASE)
        for m in pattern.finditer(sanitized):
            start, end = m.span()
            if not any(s <= start < e or s < end <= e for s, e in matched_spans):
                matched_spans.append((start, end))
                if cid not in resolved_crew_ids:
                    resolved_crew_ids.append(cid)

    # Apply substitutions for matched names
    for pattern_str, cid, _ in match_candidates:
        pattern = re.compile(pattern_str, re.IGNORECASE)
        sanitized = pattern.sub(cid, sanitized)

    # 3. Detect unknown crew names (e.g. "Captain John Doe", "FO Jane Smith", "Captain Nobody")
    STOP_WORDS = {
        "is", "was", "are", "calls", "called", "sick", "on", "in", "to", "for", "with",
        "has", "had", "out", "can", "will", "at", "from", "and", "or", "not", "available",
        "flying", "operating", "duty", "rest", "standby", "reserve", "line", "tomorrow", "today",
        "who", "which", "what", "how", "the", "a", "an", "this", "that", "off", "fatigued",
    }
    unknown_name_pattern = re.compile(
        r"\b(?:Captain|Capt\.?|First\s+Officer|FO)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b"
    )
    for m in unknown_name_pattern.finditer(sanitized):
        name_found = m.group(1).strip()
        words = [w.lower() for w in name_found.split()]
        if any(w in STOP_WORDS for w in words):
            continue
        if not re.match(r"^C-\d{4}$", name_found, re.IGNORECASE):
            unknown_token = f"UNKNOWN:{name_found}"
            if unknown_token not in resolved_crew_ids:
                resolved_crew_ids.append(unknown_token)

    # 4. Normalize relative dates
    sanitized = re.sub(r"\btomorrow\b", "2026-09-15", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\btoday\b", "2026-09-14", sanitized, flags=re.IGNORECASE)

    logger.info(
        "Resolved local entities and sanitized PII",
        resolved_crew_count=len(resolved_crew_ids),
        resolved_crew_ids=resolved_crew_ids,
    )
    return sanitized, pii_map, resolved_crew_ids

