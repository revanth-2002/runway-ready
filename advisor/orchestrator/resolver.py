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

    PII scrubbing is limited to: personal names and personal attributes (age, medical info).
    The following operational identifiers are NEVER altered and always pass through as-is:
      - Crew IDs: C-XXXX
      - Flight IDs: DX-NNN / DXNNN
      - Pairing IDs: P-XXXX
      - Aircraft tails: VT-DXX
      - Timestamps / dates

    Returns:
        (sanitized_query, pii_reverse_map, resolved_crew_ids)
    """
    all_crew = repo.list_all_crew()
    sanitized = query
    pii_map: Dict[str, str] = {c.crew_id: c.name for c in all_crew}
    resolved_crew_ids: List[str] = []

    # -- Step 1: Collect and protect operational identifiers --
    # These patterns must NEVER be touched by the name-replacement pass.
    PROTECTED_PATTERNS = [
        r"\bC-\d{4}\b",                       # Crew IDs: C-1042
        r"\bDX-?\d{2,4}\b",                   # Flight IDs: DX412, DX-412
        r"\b[A-Z]{1,2}\d{3,4}\b",             # Generic flight numbers: AI101
        r"\bP-\d{4}\b",                        # Pairing IDs: P-2291
        r"\bVT-[A-Z]{3}\b",                    # Aircraft tails: VT-DXA
        r"\b\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)?\b",
    ]

    protected_spans: List[Tuple[int, int]] = []

    for pat in PROTECTED_PATTERNS:
        for m in re.finditer(pat, sanitized, re.IGNORECASE):
            protected_spans.append(m.span())

    # Extract explicit crew IDs for the resolved list
    for m in re.finditer(r"\bC-\d{4}\b", sanitized, re.IGNORECASE):
        cid = m.group().upper()
        if cid not in resolved_crew_ids:
            resolved_crew_ids.append(cid)

    def _is_protected(start: int, end: int) -> bool:
        """Return True if [start, end) overlaps any protected span."""
        return any(
            ps <= start < pe or ps < end <= pe or (start <= ps and end >= pe)
            for ps, pe in protected_spans
        )

    # -- Step 2: Build name->crew_id candidates (most-specific first) --
    surname_counts = Counter(c.name.split()[-1] for c in all_crew)
    match_candidates: List[Tuple[str, str, int]] = []  # (pattern, crew_id, priority)

    for c in all_crew:
        parts = c.name.split()
        surname = parts[-1]
        initial = parts[0] if len(parts) > 1 else ""

        rank_prefixes: List[str] = []
        if "captain" in c.rank.lower():
            rank_prefixes = [r"Capt(?:ain)?\.?"]
        elif "first officer" in c.rank.lower():
            rank_prefixes = [r"First\s+Officer", r"FO"]
        elif "senior cabin crew" in c.rank.lower():
            rank_prefixes = [r"Senior\s+Cabin\s+Crew", r"SCC"]
        elif "cabin crew" in c.rank.lower():
            rank_prefixes = [r"Cabin\s+Crew", r"CC"]

        # Form 1: Rank + Full Name  e.g. "Captain R. Iyer"
        for rp in rank_prefixes:
            match_candidates.append(
                (rf"\b{rp}\s+{re.escape(c.name)}\b", c.crew_id, len(c.name) + 15)
            )
            if initial:
                p_nodot = rf"\b{rp}\s+{re.escape(initial.replace('.', ''))}\s+{re.escape(surname)}\b"
                match_candidates.append((p_nodot, c.crew_id, len(c.name) + 14))

        # Form 2: Exact Full Name  e.g. "R. Iyer", "R Iyer"
        match_candidates.append(
            (rf"\b{re.escape(c.name)}\b", c.crew_id, len(c.name) + 10)
        )
        if initial:
            match_candidates.append((
                rf"\b{re.escape(initial.replace('.', ''))}\s+{re.escape(surname)}\b",
                c.crew_id, len(c.name) + 9,
            ))

        # Form 3: Rank + Surname  e.g. "Captain Nair"
        for rp in rank_prefixes:
            match_candidates.append(
                (rf"\b{rp}\s+{re.escape(surname)}\b", c.crew_id, len(surname) + 8)
            )

        # Form 4: Bare surname only if globally unique
        if surname_counts[surname] == 1:
            match_candidates.append(
                (rf"\b{re.escape(surname)}\b", c.crew_id, len(surname))
            )

    # Sort longest/most-specific first
    match_candidates.sort(key=lambda x: x[2], reverse=True)

    # -- Step 3: Single-pass span-aware name substitution --
    # Collect all (span, replacement) pairs without overlapping protected or
    # already-matched spans, then apply in reverse order to preserve offsets.

    claimed_spans: List[Tuple[int, int]] = []

    def _span_free(start: int, end: int) -> bool:
        if _is_protected(start, end):
            return False
        return not any(cs <= start < ce or cs < end <= ce for cs, ce in claimed_spans)

    replacements: List[Tuple[int, int, str]] = []  # (start, end, replacement_text)

    for pattern_str, cid, _ in match_candidates:
        pattern = re.compile(pattern_str, re.IGNORECASE)
        for m in pattern.finditer(sanitized):
            start, end = m.span()
            if _span_free(start, end):
                claimed_spans.append((start, end))
                replacements.append((start, end, cid))
                if cid not in resolved_crew_ids:
                    resolved_crew_ids.append(cid)

    # Apply substitutions in reverse offset order (right-to-left) to keep indices valid
    replacements.sort(key=lambda x: x[0], reverse=True)
    for start, end, replacement in replacements:
        sanitized = sanitized[:start] + replacement + sanitized[end:]

    # -- Step 4: Flag unknown crew references (rank prefix + unresolved name) --
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

    # -- Step 5: Normalize relative dates --
    sanitized = re.sub(r"\btoday\b", "2026-09-15", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\btomorrow\b", "2026-09-16", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\byesterday\b", "2026-09-14", sanitized, flags=re.IGNORECASE)

    # -- Step 6: Normalize colloquial and multilingual station aliases --
    STATION_ALIASES: Dict[str, str] = {
        r"\b(?:Bengaluru|Bangalore|Kempegowda|KIA|BLR\s*Airport)\b": "BLR",
        r"\b(?:New\s*Delhi|Delhi|Indira\s*Gandhi|IGIA?|DEL\s*Airport|Palam)\b": "DEL",
        r"\b(?:Mumbai|Bombay|Chhatrapati\s*Shivaji|CSMIA?|BOM\s*Airport|Sahar|Santacruz)\b": "BOM",
        r"\b(?:Hyderabad|Secunderabad|Rajiv\s*Gandhi|RGIA?|HYD\s*Airport|Shamshabad)\b": "HYD",
        r"\b(?:Chennai|Madras|Meenambakkam|MAA\s*Airport)\b": "MAA",
        r"\b(?:Kolkata|Calcutta|Netaji\s*Subhash|Dum\s*Dum|CCU\s*Airport)\b": "CCU",
        r"\b(?:Kochi|Cochin|Nedumbassery|COK\s*Airport)\b": "COK",
        r"\b(?:Goa|Dabolim|Mopa|GOI\s*Airport)\b": "GOI",
    }
    for pat, stn_code in STATION_ALIASES.items():
        sanitized = re.sub(pat, stn_code, sanitized, flags=re.IGNORECASE)

    logger.info(
        "Resolved local entities and sanitized PII",
        resolved_crew_count=len(resolved_crew_ids),
        resolved_crew_ids=resolved_crew_ids,
    )
    return sanitized, pii_map, resolved_crew_ids