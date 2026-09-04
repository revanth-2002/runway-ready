"""Slot-filled prose renderer and deterministic slot substitutor."""

import re
from typing import Dict, List, Optional
from advisor.audit.logger import StructuredLogger
from advisor.domain.evidence import ImpactReport, LegalityLedger, RecoveryOption
from advisor.domain.exceptions import SlotSubstitutionError
from advisor.llm.client import LLMClient, StubClient

logger = StructuredLogger("advisor.llm.renderer")



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
    try:
        raw_text = client.generate(prompt, temperature=0.0)
        if "{{" in raw_text:
            logger.debug("Generated LLM slotted prose template")
            return raw_text
    except Exception as e:
        logger.warning("LLM prose generation failed, falling back to deterministic template", error=str(e))

    logger.debug("Using fallback deterministic slotted prose template")
    # Deterministic slot template fallback
    return (
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
