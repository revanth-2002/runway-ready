"""Legality Ledger component rendering regulatory rule chips and arithmetic."""

from typing import Optional
import streamlit as st
from advisor.domain.evidence import LegalityLedger


def render_ledger_table(ledger: Optional[LegalityLedger], container=None) -> None:
    """Renders the 7 pure regulatory rule verdicts with pass/fail chips and arithmetic formulas."""
    target = container if container else st

    if not ledger or not ledger.verdicts:
        target.info("No legality ledger available for this query.")
        return

    overall_status = "✅ 100% REGULATORY COMPLIANT" if ledger.legal else f"❌ {len(ledger.breaches)} REGULATORY BREACH(ES) DETECTED"
    target.subheader(f"Legality Ledger: {ledger.subject}")
    if ledger.legal:
        target.success(overall_status)
    else:
        target.error(overall_status)

    for v in ledger.verdicts:
        badge = "🟢 PASS" if v.passed else "🔴 BREACH"
        margin_str = f"+{v.margin:.1f}h" if v.margin > 0 else f"{v.margin:.1f}h"

        with target.expander(f"{badge} | **{v.rule_id}**: {v.headline} ({margin_str})", expanded=not v.passed):
            st.markdown(f"**Mathematical Arithmetic:** `{v.arithmetic}`")
            st.markdown(f"**Signed Margin:** `{margin_str}` ({'Legal buffer' if v.passed else 'Violation depth'})")
            if v.inputs:
                st.json(v.inputs, expanded=False)
            if v.source_rows:
                st.caption(f"Source Rows: {', '.join(v.source_rows)}")
            if v.assumption:
                st.info(f"ℹ️ Assumption: {v.assumption}")
