"""Slot-filled prose renderer and deterministic slot substitutor."""

import re
from typing import Any, Dict, List, Optional
from advisor.audit.logger import StructuredLogger
from advisor.domain.evidence import ImpactReport, LegalityLedger, RecoveryOption
from advisor.domain.exceptions import LLMUnavailableError, SlotSubstitutionError
from advisor.llm.client import LLMClient, StubClient

logger = StructuredLogger("advisor.llm.renderer")

RATE_LIMIT_BANNER = (
    "> ⚠️ **You have hit the limit, please try again after some time.** "
    "(Falling back to certified deterministic operational recovery)\n\n"
)



def is_429_rate_limit_error(e: Exception) -> bool:
    """Checks if an exception is strictly an HTTP 429 or RESOURCE_EXHAUSTED rate limit error."""
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    if code == 429:
        return True
    err_str = str(e).upper()
    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
        return True
    return False


def _conversation_block(brief: Optional[str]) -> str:
    """Prompt section carrying what this conversation has already established."""
    if not brief or not brief.strip():
        return ""
    return (
        "\nConversation so far (do not repeat what is already established; build on it):\n"
        f"{brief.strip()}\n"
    )


def _impact_date(impact: ImpactReport) -> str:
    """Derives the operational date of the disruption from its uncrewed flights."""
    if impact.uncrewed_flights:
        return impact.uncrewed_flights[0].dep_utc[:10]
    return "2026-09-15"


def build_slot_values(
    impact: ImpactReport,
    ledger: LegalityLedger,
    options: List[RecoveryOption],
    pii_map: Optional[Dict[str, str]] = None,
    crew_rank: Optional[str] = None,
) -> Dict[str, str]:
    """Builds the authoritative {{slot}} -> verified value table.

    This is the single source of truth: the prompt advertises exactly these keys and
    `substitute_slots` resolves exactly these keys, so the two can never drift.
    """
    if pii_map is None:
        pii_map = {}

    raw_name = pii_map.get(impact.disrupted_crew_id, impact.disrupted_crew_id)
    # Strip rank prefix if already embedded to avoid "Captain Captain X"
    disrupted_name = re.sub(r"^(?:Captain|First Officer|FO)\s+", "", raw_name, flags=re.IGNORECASE)
    # Prefer the roster rank; the display name usually carries no rank prefix, and
    # defaulting to "Captain" mislabelled every First Officer.
    rank_match = re.match(r"^(Captain|First Officer|FO)\b", raw_name, flags=re.IGNORECASE)
    disrupted_rank = crew_rank or (rank_match.group(1) if rank_match else "Captain")

    date_str = _impact_date(impact)
    slot_values: Dict[str, str] = {
        "impact.crew_id": disrupted_name,
        "impact.disrupted_crew": disrupted_name,
        "impact.crew_rank": disrupted_rank,
        "impact.date": date_str,
        "impact.disrupted_date": date_str,
        "impact.pairing_id": impact.broken_pairing_id,
        "impact.broken_pairing": impact.broken_pairing_id,
        "impact.uncrewed_count": str(len(impact.uncrewed_flights)),
        "impact.uncrewed_flights": (
            ", ".join(f.flight_id for f in impact.uncrewed_flights) or "none"
        ),
        "impact.passengers_affected": f"{impact.passengers_affected:,}",
        "impact.option_count": str(len(options)),
    }

    binding = ledger.binding_breach
    slot_values["impact.binding_rule"] = binding.rule_id if binding else "none"
    slot_values["impact.binding_headline"] = binding.headline if binding else "no binding breach"

    # Every ranked option gets a full token set, not just the first two. Prose that
    # referenced options.2+ previously blew up slot substitution and silently
    # collapsed the whole briefing to the generic fallback.
    for idx, opt in enumerate(options):
        p = f"options.{idx}"
        slot_values[f"{p}.crew_id"] = pii_map.get(opt.crew_id, opt.crew_id)
        slot_values[f"{p}.base"] = opt.base
        slot_values[f"{p}.cost_inr"] = f"{int(opt.cost.total_inr):,}"
        slot_values[f"{p}.type"] = opt.candidate_type.replace("_", " ")
        slot_values[f"{p}.status"] = "legal" if opt.ledger.legal else "illegal"

        opt_breach = opt.ledger.binding_breach
        slot_values[f"{p}.ledger.duty_02.margin"] = (
            f"{abs(opt_breach.margin):.1f}h" if opt_breach else "0.0h"
        )
        slot_values[f"{p}.breach"] = (
            opt_breach.headline if opt_breach else "no regulatory breach"
        )

        if opt.repair:
            slot_values[f"{p}.repair.text"] = (
                f"cleared if {opt.repair.lever.replace('_', ' ')} by {opt.repair.magnitude_minutes}m"
            )
        else:
            slot_values[f"{p}.repair.text"] = "no minimal repair available"

    return slot_values


def render_slotted_prose(
    impact: ImpactReport,
    ledger: LegalityLedger,
    options: List[RecoveryOption],
    client: Optional[LLMClient] = None,
    question: Optional[str] = None,
    pii_map: Optional[Dict[str, str]] = None,
    return_meta: bool = False,
    conversation_brief: Optional[str] = None,
) -> Any:
    """Renders a briefing that answers `question`, using only verified {{slot}} tokens.

    With `return_meta=True` returns `(template, meta)` where meta carries `source`
    ("llm" or "deterministic") and any degradation `banner`, so the caller can decide
    whether the narrative adds anything over its own deterministic sections.

    The prompt is given the controller's actual question and the exact slot vocabulary
    available for this evidence bundle, so the narration addresses what was asked
    instead of restating a fixed disruption summary.
    """
    if client is None:
        client = StubClient()

    slot_values = build_slot_values(impact, ledger, options, pii_map)
    allowed_tokens = sorted(slot_values.keys())
    token_menu = "\n".join(f"  {{{{{t}}}}}" for t in allowed_tokens)

    asked = (question or "").strip()
    question_block = (
        f'The controller asked: "{asked}"\n'
        "Answer THAT question first, in your opening sentence. Only add disruption "
        "context that helps answer it. If the question is about cost, lead with cost; "
        "if it is about legality, lead with the binding rule; if it is about who is "
        "available, lead with the candidates."
        if asked
        else "No specific question was supplied. Lead with the binding operational breach."
    )

    prompt = f"""You are an operational advisor to an airline crew controller.

{question_block}

Write under 100 words. Be direct and operational — no preamble, no restating the question.

HARD CONSTRAINT: you must not write any number, crew ID, flight number, station code,
date, or monetary amount directly. Every such value must be written as one of the
slot tokens listed below, copied exactly. Do not invent tokens that are not on this
list — a response containing an unlisted token is discarded entirely.

Available slot tokens for this bundle:
{token_menu}

Evidence summary (for your understanding only — do not copy these raw values):
Disrupted crew: {impact.disrupted_crew_id}
Broken pairing: {impact.broken_pairing_id}
Uncrewed flights: {len(impact.uncrewed_flights)}
Passengers affected: {impact.passengers_affected}
Ranked options available: {len(options)}
Top option legality: {"legal" if options and options[0].ledger.legal else "illegal or none"}
{_conversation_block(conversation_brief)}"""
    degraded_banner = ""
    try:
        raw_text = client.generate(prompt, temperature=0.0)
        validated = _validate_template(raw_text, allowed_tokens)
        if validated:
            logger.debug("Generated LLM slotted prose template", question=asked or None)
            if return_meta:
                return validated, {"source": "llm", "banner": ""}
            return validated
    except LLMUnavailableError as e:
        logger.warning(
            "LLM unavailable for prose generation, using deterministic briefing",
            error=str(e),
            is_rate_limit=e.is_rate_limit,
        )
        if e.is_rate_limit:
            degraded_banner = RATE_LIMIT_BANNER
    except Exception as e:
        logger.warning("LLM prose generation failed, falling back to deterministic template", error=str(e))
        if is_429_rate_limit_error(e):
            degraded_banner = RATE_LIMIT_BANNER

    logger.debug("Using fallback deterministic slotted prose template")
    if return_meta:
        return _deterministic_template(options), {
            "source": "deterministic",
            "banner": degraded_banner,
        }
    return degraded_banner + _deterministic_template(options)


def _validate_template(raw_text: str, allowed_tokens: List[str]) -> Optional[str]:
    """Returns the template if it uses at least one slot and no unlisted tokens."""
    if not raw_text or "{{" not in raw_text:
        return None

    used = {m.strip() for m in re.findall(r"\{\{([^}]+)\}\}", raw_text)}
    unknown = used - set(allowed_tokens)
    if unknown:
        # Previously this survived render_slotted_prose and only blew up later inside
        # substitute_slots, discarding the whole briefing with no diagnostic.
        logger.warning(
            "Discarding LLM template containing unlisted slot tokens",
            unknown_tokens=sorted(unknown),
        )
        return None
    return raw_text


def _deterministic_template(options: List[RecoveryOption]) -> str:
    """Certified fallback template, sized to the options actually available."""
    lines = [
        "{{impact.crew_rank}} {{impact.crew_id}} is incapacitated for {{impact.date}}. "
        "This breaks pairing {{impact.pairing_id}}, leaving {{impact.uncrewed_count}} flight(s) uncrewed "
        "and stranding {{impact.passengers_affected}} passengers."
    ]
    if options:
        lines.append(
            "Option 1: Assign {{options.0.type}} {{options.0.crew_id}} from {{options.0.base}} "
            "at a cost of ₹{{options.0.cost_inr}} ({{options.0.status}})."
        )
    if len(options) > 1:
        lines.append(
            "Nearest backup {{options.1.crew_id}} — {{options.1.breach}}; {{options.1.repair.text}}."
        )
    return " ".join(lines)


def substitute_slots(
    template_str: str,
    impact: ImpactReport,
    ledger: LegalityLedger,
    options: List[RecoveryOption],
    pii_map: Optional[Dict[str, str]] = None,
) -> str:
    """Substitutes verified factual values into slot tokens, falling back to deterministic template on error."""
    if pii_map is None:
        pii_map = {}

    slot_values = build_slot_values(impact, ledger, options, pii_map)

    def replacer(match: re.Match) -> str:
        token = match.group(1).strip()
        if token in slot_values:
            return slot_values[token]
        # Unknown slot token triggers fallback
        raise SlotSubstitutionError(f"Unknown slot token: {token}")

    try:
        substituted = re.sub(r"\{\{([^}]+)\}\}", replacer, template_str)
        logger.info("Successfully substituted slots into prose", length=len(substituted))
        return substituted
    except SlotSubstitutionError as e:
        logger.warning("Slot substitution error, falling back to deterministic briefing", error=str(e))
        return build_deterministic_briefing(impact, ledger, options, pii_map)



def build_deterministic_briefing(
    impact: ImpactReport,
    ledger: LegalityLedger,
    options: List[RecoveryOption],
    pii_map: Optional[Dict[str, str]] = None,
) -> str:
    """Pre-compiled deterministic fallback template."""
    if pii_map is None:
        pii_map = {}

    disrupted_name = pii_map.get(impact.disrupted_crew_id, impact.disrupted_crew_id)
    lines = [
        f"**Operational Disruption Briefing**",
        f"• **Incapacitated:** {disrupted_name} on {_impact_date(impact)}.",
        f"• **Cascade:** Breaks pairing {impact.broken_pairing_id}, leaving {len(impact.uncrewed_flights)} flights uncrewed and affecting {impact.passengers_affected:,} passengers.",
    ]

    if options:
        top = options[0]
        top_name = pii_map.get(top.crew_id, top.crew_id)
        status_str = "LEGAL" if top.ledger.legal else "ILLEGAL"
        lines.append(
            f"• **Recommended Action:** Assign {top_name} ({top.candidate_type}, base {top.base}) — **{status_str}** at cost of ₹{int(top.cost.total_inr):,}."
        )

        if len(options) > 1:
            backup = options[1]
            backup_name = pii_map.get(backup.crew_id, backup.crew_id)
            if backup.repair:
                lines.append(
                    f"• **Backup Candidate:** {backup_name} ({backup.candidate_type}) — Requires repair: {backup.repair.lever} by {backup.repair.magnitude_minutes}m."
                )

    return "\n".join(lines)


def render_cancellation_briefing(
    station: str,
    date: str,
    flight_count: int,
    passengers: int,
    tails: List[str],
    cost_breakdown: Any,
) -> str:
    """Generates clear, factual operational briefing for mass flight cancellations and financial loss."""
    lines = [
        f"**🚨 Mass Flight Cancellation Simulation — {station} Hub ({date})**\n",
        f"• **Scope:** Cancelling all scheduled departures from **{station}** on **{date}**.",
        f"• **Operational Impact:** **{flight_count} flights** cancelled across **{len(tails)} aircraft tails** ({', '.join(tails) if tails else 'N/A'}).",
        f"• **Passengers Stranded:** **{passengers:,} passengers** affected.",
        f"\n**💰 Total Estimated Financial Loss: ₹{int(cost_breakdown.total_inr):,}**",
    ]
    if hasattr(cost_breakdown, "line_items") and cost_breakdown.line_items:
        lines.append("\n**Itemized Financial Loss Breakdown:**")
        for item in cost_breakdown.line_items:
            lines.append(f"  • {item}")

    return "\n".join(lines)


def render_crew_move_evaluation(
    eval_res: Dict[str, Any],
    pii_map: Optional[Dict[str, str]] = None,
) -> str:
    """Renders a conversational, authoritative controller briefing for a what-if crew move."""
    if pii_map is None:
        pii_map = {}

    if eval_res.get("status") == "crew_not_found":
        cid = eval_res.get("crew_id", "Unknown")
        avail = eval_res.get("available_crew", [])
        avail_str = f" Available crew at base include: {', '.join(avail)}." if avail else ""
        return f"⚠️ **Crew Member Not Found:** Could not locate crew member `{cid}` in active roster records.{avail_str} Please verify the ID or station."

    if eval_res.get("status") in ("flight_not_found", "pairing_not_found"):
        target_id = eval_res.get("flight_id") or eval_res.get("pairing_id", "Unknown")
        return f"⚠️ **Flight / Pairing Not Found:** Could not locate `{target_id}` in active schedule records. Active flights from BLR include `DX412`, `DX588`, `DX702`."

    crew = eval_res["crew"]
    pairing = eval_res["pairing"]
    flight_id = eval_res.get("flight_id") or (pairing.legs[0].flight_id if pairing.legs else "pairing")
    legal = eval_res["legal"]
    breaches = eval_res.get("breaches", [])
    displaced_crew = eval_res.get("displaced_crew")
    rank_note = eval_res.get("rank_mismatch_note")
    ratings = eval_res.get("ratings", [])
    clk = eval_res.get("duty_clock")

    lines = []

    # 1. Headline verdict
    if not legal:
        lines.append(f"❌ **ACTION REJECTED (Duty Limits Breached):** Assigning **{crew.rank} {crew.name} (`{crew.crew_id}`)** onto flight `{flight_id}` (Pairing `{pairing.pairing_id}`) cannot be made because it breaches DGCA CAR Section 7 regulations.")
    else:
        lines.append(f"✅ **ACTION APPROVED (Fully Legal & Compliant):** Assigning **{crew.rank} {crew.name} (`{crew.crew_id}`)** onto flight `{flight_id}` (Pairing `{pairing.pairing_id}`) can be made legally with 0 regulatory breaches.")

    # 2. Rank clarification note (if user wrote FO for a Captain or vice versa)
    if rank_note:
        lines.append(f"\n> ℹ️ *{rank_note}*")

    # 3. DGCA CAR Section 7 Legality Breakdown
    lines.append("\n**📋 DGCA CAR Section 7 Compliance Ledger:**")
    if not legal:
        for b in breaches:
            lines.append(f"• ❌ **{b.rule_id}**: {b.headline} (*{b.arithmetic}*)")
    else:
        lines.append(f"• ✅ **RULE-FDP-01 / FLT-03**: Flight Duty Period and flight hours within regulatory limits.")
        if clk:
            lines.append(f"• ✅ **RULE-DUTY-02**: 7-Day Cumulative Duty: {clk.duty_hours_7d:.1f}h (within 60.0h max limit).")
        lines.append(f"• ✅ **RULE-REST-04**: Minimum rest period prior to report is fully satisfied.")
        lines.append(f"• ✅ **RULE-QUAL-05 / BASE-07**: Qualified on {', '.join(ratings) if ratings else 'A320'} and base matches flight origin ({crew.base}).")

    # 4. Impacted / Displaced Crew
    lines.append("\n**👥 Operational Roster Impact:**")
    if displaced_crew:
        disp_name = displaced_crew.get("name") or pii_map.get(displaced_crew.get("crew_id"), displaced_crew.get("crew_id"))
        disp_rank = displaced_crew.get("role", "Captain")
        disp_id = displaced_crew.get("crew_id")
        lines.append(f"• **Displaced Crew:** This reassignment displaces rostered **{disp_rank} {disp_name} (`{disp_id}`)** from flight `{flight_id}`.")
    else:
        lines.append(f"• **Displaced Crew:** No existing rostered crew members are displaced.")

    companion_crew = eval_res.get("companion_crew", [])
    if companion_crew:
        comp_str = ", ".join(f"{c.get('role', c.get('rank', 'Crew'))} {c.get('name', '')} (`{c.get('crew_id')}`)" for c in companion_crew)
        lines.append(f"• **Companion Pairing Crew:** {comp_str}")

    # The recommendation is returned separately (see `recommend_after_crew_move`) so
    # the caller can place it after the option cards and legality ledger, rather than
    # stranding "see the chips below" above a table the controller must scroll past.
    return "\n".join(lines)


def recommend_after_crew_move(eval_res: Dict[str, Any]) -> Optional[str]:
    """Next-step recommendation for a what-if crew move."""
    if eval_res.get("status") in ("crew_not_found", "flight_not_found", "pairing_not_found"):
        return None

    pairing = eval_res["pairing"]
    flight_id = eval_res.get("flight_id") or (
        pairing.legs[0].flight_id if pairing.legs else "this pairing"
    )
    if not eval_res["legal"]:
        return (
            f"Would you like me to evaluate available standby crew or produce ranked "
            f"recovery options to cover flight `{flight_id}`?"
        )
    return (
        "This move is legal. Would you like to simulate and commit this reassignment "
        "in the Digital Twin, or evaluate the impact on companion crew?"
    )


def render_flight_crew_impact(
    impact_res: Dict[str, Any],
) -> str:
    """Renders a breakdown of crew assigned to a flight and potential replacement effects."""
    if impact_res.get("status") == "flight_not_found":
        fid = impact_res.get("flight_id", "Unknown")
        return f"⚠️ **Flight Not Found:** Could not locate `{fid}` in active flight schedules."

    fl = impact_res["flight"]
    pairing = impact_res.get("pairing")
    assigns = impact_res.get("assignments", [])

    lines = [f"### ✈️ Flight Crew Roster: {fl.flight_id} ({fl.origin} ➔ {fl.destination})"]
    if pairing:
        lines.append(f"• **Operating Pairing:** `{pairing.pairing_id}` ({pairing.start_utc[11:16]}Z – {pairing.end_utc[11:16]}Z)")
    lines.append(f"• **Aircraft Tail:** `{fl.tail_id}` ({fl.aircraft_type}) | **Passengers:** {fl.passengers}")

    lines.append("\n**👥 Assigned Crew Members:**")
    for a in assigns:
        lines.append(f"• **{a.get('rank', 'Crew')} {a.get('name', '')} (`{a['crew_id']}`)** — Role: {a.get('role', a.get('rank'))}")

    return "\n".join(lines)


def render_uncrewed_flights(
    flights: List[Any],
    crew_id: str,
    pairing_id: str,
    resolved_by: Optional[str] = None,
) -> str:
    """Renders only the flights left uncrewed by the disruption under discussion.

    Answers "which flights are affected" with the affected legs rather than the whole
    station schedule, which is what the controller is actually asking during a
    disruption.
    """
    header = (
        f"### ✈️ Uncrewed Flights — {crew_id} disruption"
        + (f" (pairing `{pairing_id}`)" if pairing_id else "")
    )
    if not flights:
        return f"{header}\n\nNo flights are currently uncrewed for this disruption."

    rows = [
        "| Flight | Route | Departure (UTC) | Arrival (UTC) | Tail | Pax |",
        "|---|---|---|---|---|---|",
    ]
    total_pax = 0
    for f in flights:
        pax = getattr(f, "passengers", 0) or 0
        total_pax += pax
        rows.append(
            f"| `{f.flight_id}` | `{f.origin} ➔ {f.destination}` | `{f.dep_utc[11:16]}Z` "
            f"| `{f.arr_utc[11:16]}Z` | `{f.tail_id}` | {pax} |"
        )

    body = "\n".join(rows)
    footer = (
        f"\n\n***{len(flights)}** leg(s) uncrewed, **{total_pax:,}** passengers exposed.*"
    )
    if resolved_by:
        footer += f"\n\n> ✅ Cover already committed: `{resolved_by}` was adopted for this pairing."
    return f"{header}\n\n{body}{footer}"


def _uncrewed_manifest_table(flights: List[Any]) -> str:
    """Markdown table of the uncrewed legs with times, tail and passenger load."""
    rows = [
        "| Flight | Route | Departure (UTC) | Arrival (UTC) | Tail | Passengers |",
        "|---|---|---|---|---|---|",
    ]
    for f in flights:
        pax = getattr(f, "passengers", 0) or 0
        rows.append(
            f"| `{f.flight_id}` | `{f.origin} ➔ {f.destination}` | `{f.dep_utc[11:16]}Z` "
            f"| `{f.arr_utc[11:16]}Z` | `{f.tail_id}` | {pax} |"
        )
    return "\n".join(rows)


def render_disruption_briefing(
    impact: ImpactReport,
    ledger: LegalityLedger,
    options: List[RecoveryOption],
    narrative: str,
    pii_map: Optional[Dict[str, str]] = None,
    banner: str = "",
    crew_rank: Optional[str] = None,
) -> str:
    """Composes the full disruption answer: headline, affected-leg manifest, narrative.

    The controller asking "which flights are now uncrewed" needs the legs, their
    times and their passenger loads — an aggregate count alone does not answer it.

    `narrative` should be omitted when it is the deterministic fallback, which only
    restates the headline this function already renders.
    """
    if pii_map is None:
        pii_map = {}

    slots = build_slot_values(impact, ledger, options, pii_map, crew_rank)
    who = f"{slots['impact.crew_rank']} {slots['impact.crew_id']} (`{impact.disrupted_crew_id}`)"

    parts = []
    if banner:
        parts.append(banner.strip())
    parts.append(f"### 🚨 Disruption Impact — {who} unavailable {slots['impact.date']}")

    headline_bits = []
    if impact.broken_pairing_id:
        headline_bits.append(f"Pairing `{impact.broken_pairing_id}` broken")
    headline_bits.append(f"**{len(impact.uncrewed_flights)}** leg(s) uncrewed")
    headline_bits.append(f"**{impact.passengers_affected:,}** passengers exposed")
    parts.append(" · ".join(headline_bits))

    if impact.uncrewed_flights:
        parts.append("\n**✈️ Uncrewed Legs**\n\n" + _uncrewed_manifest_table(impact.uncrewed_flights))

    if impact.delayed_rotations:
        delayed = "\n".join(
            f"• `{d['flight_id']}` — scheduled `{d['scheduled_dep_utc'][11:16]}Z`, "
            f"now estimated `{d['estimated_dep_utc'][11:16]}Z`"
            for d in impact.delayed_rotations[:5]
        )
        parts.append(f"\n**⏱️ Knock-on Rotation Delays**\n\n{delayed}")

    if narrative and narrative.strip():
        parts.append(f"\n**📋 Assessment**\n\n{narrative.strip()}")

    return "\n\n".join(parts)


def recommend_recovery(
    options: List[RecoveryOption],
    impact: ImpactReport,
    pii_map: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """States the best way to cover the disruption, with the trade-off against runner-up."""
    if pii_map is None:
        pii_map = {}

    real = [o for o in options if o.crew_id != "DO_NOTHING"]
    if not real:
        return None

    if not impact.uncrewed_flights:
        # The crew member is unavailable but holds no active pairing, so there is no
        # gap to fill. Recommending paid cover here would be plainly wrong.
        return (
            f"`{impact.disrupted_crew_id}` is not rostered on any active pairing for "
            f"this date, so no legs are uncrewed and no cover is required. Shall I "
            f"check their duty clock or standby eligibility instead?"
        )

    legal = [o for o in real if o.ledger.legal]
    do_nothing = next((o for o in options if o.crew_id == "DO_NOTHING"), None)

    if not legal:
        blocked = next((o for o in real if o.repair), None)
        if blocked:
            return (
                f"No candidate is legal as scheduled. The closest is `{blocked.crew_id}`, "
                f"which clears if you {blocked.repair.lever.replace('_', ' ')} by "
                f"{blocked.repair.magnitude_minutes}m — {blocked.repair.side_effects}. "
                f"Shall I apply that repair?"
            )
        return (
            "No legal cover is available for these legs. The remaining lever is "
            "cancellation — shall I price the do-nothing benchmark?"
        )

    best = legal[0]
    name = pii_map.get(best.crew_id, best.crew_id)
    lines = [
        f"**Best way to cover:** assign **{name}** (`{best.crew_id}`) — "
        f"{best.candidate_type.replace('_', ' ')} at `{best.base}`, "
        f"**legal** on all 7 DGCA rules, **₹{int(best.cost.total_inr):,}**."
    ]
    if best.expiry_utc:
        lines.append(f"Callout must be issued before `{best.expiry_utc}` to stay feasible.")

    runner_up = next((o for o in legal[1:]), None)
    if runner_up:
        delta = int(runner_up.cost.total_inr - best.cost.total_inr)
        ru_name = pii_map.get(runner_up.crew_id, runner_up.crew_id)
        lines.append(
            f"Next legal alternative is `{runner_up.crew_id}` ({ru_name}) at "
            f"₹{int(runner_up.cost.total_inr):,}"
            + (f" — ₹{delta:,} more." if delta > 0 else ".")
        )
    if do_nothing:
        saving = int(do_nothing.cost.total_inr - best.cost.total_inr)
        if saving > 0:
            lines.append(
                f"Cancelling instead would cost ₹{int(do_nothing.cost.total_inr):,}, "
                f"so this saves **₹{saving:,}**."
            )

    lines.append("Adopt it below, or ask me to compare the full ranked list.")
    return " ".join(lines)


def recommend_after_flight_crew(impact_res: Dict[str, Any]) -> Optional[str]:
    """Next-step recommendation after listing the crew on a flight."""
    if impact_res.get("status") == "flight_not_found":
        return None
    return (
        "If you would like to simulate replacing or reassigning any of these crew "
        "members, specify a candidate and I will evaluate legality."
    )


def render_crew_info(
    info_res: Dict[str, Any],
) -> str:
    """Renders a complete, factual profile for a crew member."""
    if info_res.get("status") == "not_found":
        cid = info_res.get("crew_id", "Unknown")
        return f"⚠️ **Crew Member Not Found:** Could not locate crew member `{cid}` in active roster records."

    crew = info_res["crew"]
    ratings = info_res.get("ratings", [])
    duty_clock = info_res.get("duty_clock")
    assignments = info_res.get("assignments", [])
    is_reserve = info_res.get("is_reserve", False)
    reserve_shift = info_res.get("reserve_shift")

    lines = [f"### 👨‍✈️ Crew Profile: {crew.rank} {crew.name} (`{crew.crew_id}`)"]
    lines.append(f"• **Base Station:** {crew.base}")
    lines.append(f"• **Seniority:** Level {crew.seniority}")
    lines.append(f"• **Aircraft Type Ratings:** {', '.join(ratings) if ratings else 'A320'}")
    lines.append(f"• **Callout Reachability:** {crew.reachability_minutes} minutes")
    
    if duty_clock:
        lines.append(f"• **7-Day Cumulative Duty:** {duty_clock.duty_hours_7d:.1f} hours (DGCA Limit: 60.0h)")
        lines.append(f"• **28-Day Flight Hours:** {duty_clock.flight_hours_28d:.1f} hours (DGCA Limit: 100.0h)")
        if duty_clock.last_rest_ended:
            lines.append(f"• **Last Rest Ended:** {duty_clock.last_rest_ended}")

    if is_reserve and reserve_shift:
        lines.append(f"• **Roster Status:** 🟢 Active Standby Reserve ({reserve_shift.get('oncall_start_utc', '')[11:16]}Z – {reserve_shift.get('oncall_end_utc', '')[11:16]}Z)")
    elif assignments:
        p_ids = [a["pairing_id"] for a in assignments]
        lines.append(f"• **Roster Status:** ✈️ Rostered on Pairing(s): {', '.join(p_ids)}")
    else:
        lines.append(f"• **Roster Status:** 🛌 Off-Duty / Scheduled Rest (Not on standby reserve)")

    return "\n".join(lines)


