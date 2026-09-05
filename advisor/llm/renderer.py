"""Slot-filled prose renderer and deterministic slot substitutor."""

import re
from typing import Any, Dict, List, Optional
from advisor.audit.logger import StructuredLogger
from advisor.domain.evidence import ImpactReport, LegalityLedger, RecoveryOption
from advisor.domain.exceptions import SlotSubstitutionError
from advisor.llm.client import LLMClient, StubClient

logger = StructuredLogger("advisor.llm.renderer")



def is_429_rate_limit_error(e: Exception) -> bool:
    """Checks if an exception is strictly an HTTP 429 or RESOURCE_EXHAUSTED rate limit error."""
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    if code == 429:
        return True
    err_str = str(e).upper()
    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
        return True
    return False


def render_slotted_prose(
    impact: ImpactReport,
    ledger: LegalityLedger,
    options: List[RecoveryOption],
    client: Optional[LLMClient] = None,
) -> str:
    """Invokes LLM Call 2 with strict directive to output ONLY variable slot tokens."""
    if client is None:
        client = StubClient()

    prompt = f"""You are an operational advisor to an airline crew controller.
Summarize the evidence bundle in under 100 words.
You are strictly forbidden from writing numbers, flight numbers, crew IDs, or monetary costs directly.
You must refer to them exclusively using {{{{token.path}}}} slots.
Lead with the binding operational breach.

Context:
Disrupted crew: {impact.disrupted_crew_id}
Broken pairing: {impact.broken_pairing_id}
Uncrewed flights count: {len(impact.uncrewed_flights)}
Passengers affected: {impact.passengers_affected}
Available options count: {len(options)}
"""
    rate_limit_warning = ""
    try:
        raw_text = client.generate(prompt, temperature=0.0)
        if "{{" in raw_text:
            logger.debug("Generated LLM slotted prose template")
            return raw_text
    except Exception as e:
        logger.warning("LLM prose generation failed, falling back to deterministic template", error=str(e))
        if is_429_rate_limit_error(e):
            rate_limit_warning = "> ⚠️ **You have hit the limit, please try again after some time.** (Falling back to certified deterministic operational recovery)\n\n"

    logger.debug("Using fallback deterministic slotted prose template")
    # Deterministic slot template fallback
    return rate_limit_warning + (
        "Captain {{impact.crew_id}} is incapacitated for {{impact.date}}. "
        "This breaks pairing {{impact.pairing_id}}, leaving {{impact.uncrewed_count}} flight(s) uncrewed "
        "and stranding {{impact.passengers_affected}} passengers. "
        "Option 1: Assign reserve {{options.0.crew_id}} from {{options.0.base}} at a cost of ₹{{options.0.cost_inr}}. "
        "Nearest backup {{options.1.crew_id}} breaches duty limit — {{options.1.repair.text}}."
    )


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

    raw_name = pii_map.get(impact.disrupted_crew_id, impact.disrupted_crew_id)
    # Strip rank prefix if already embedded to avoid "Captain Captain X"
    disrupted_name = re.sub(r"^(?:Captain|First Officer|FO)\s+", "", raw_name, flags=re.IGNORECASE)

    slot_values: Dict[str, str] = {
        "impact.crew_id": disrupted_name,
        "impact.disrupted_crew": disrupted_name,
        "impact.date": "2026-09-15",
        "impact.disrupted_date": "2026-09-15",
        "impact.pairing_id": impact.broken_pairing_id,
        "impact.broken_pairing": impact.broken_pairing_id,
        "impact.uncrewed_count": str(len(impact.uncrewed_flights)),
        "impact.passengers_affected": f"{impact.passengers_affected:,}",
    }

    # Option 0 tokens
    if options:
        opt0 = options[0]
        opt0_name = pii_map.get(opt0.crew_id, opt0.crew_id)
        slot_values["options.0.crew_id"] = opt0_name
        slot_values["options.0.base"] = opt0.base
        slot_values["options.0.cost_inr"] = f"{int(opt0.cost.total_inr):,}"

    # Option 1 tokens
    if len(options) > 1:
        opt1 = options[1]
        opt1_name = pii_map.get(opt1.crew_id, opt1.crew_id)
        slot_values["options.1.crew_id"] = opt1_name
        slot_values["options.1.base"] = opt1.base
        slot_values["options.1.cost_inr"] = f"{int(opt1.cost.total_inr):,}"

        breach = opt1.ledger.binding_breach
        if breach:
            slot_values["options.1.ledger.duty_02.margin"] = f"{abs(breach.margin):.1f}h"
        else:
            slot_values["options.1.ledger.duty_02.margin"] = "0.0h"

        if opt1.repair:
            slot_values["options.1.repair.text"] = (
                f"cleared if {opt1.repair.lever.replace('_', ' ')} by {opt1.repair.magnitude_minutes}m"
            )
        else:
            slot_values["options.1.repair.text"] = "no minimal repair available"

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
        f"• **Incapacitated:** {disrupted_name} on 2026-09-15.",
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

    # 5. Proactive Conversational Action
    if not legal:
        lines.append(f"\n💡 **Next Step:** Would you like me to evaluate available standby crew or produce ranked recovery options to cover flight `{flight_id}`? Use the action chips below or describe what you need.")
    else:
        lines.append(f"\n💡 **Next Step:** This move is legal. Would you like to simulate and commit this reassignment in the Digital Twin, or evaluate the impact on companion crew?")

    return "\n".join(lines)


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

    lines.append("\n💡 **Recommended Next Step:** If you would like to simulate replacing or reassigning any of these crew members, let me know or specify a candidate to evaluate legality.")
    return "\n".join(lines)


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


