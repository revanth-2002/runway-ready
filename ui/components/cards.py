"""Candidate option cards and Do-Nothing benchmark component."""

from typing import List, Optional
import streamlit as st
from advisor.domain.evidence import RecoveryOption


def render_option_cards(options: List[RecoveryOption], container=None) -> None:
    """Renders ranked recovery candidate cards with costs, repairs, and cancellation comparison."""
    target = container if container else st

    if not options:
        target.info("No recovery options evaluated.")
        return

    target.subheader("Ranked Recovery Options")

    # Benchmark option is the last one if present
    do_nothing = next((o for o in options if o.candidate_type == "do_nothing"), None)
    operational_options = [o for o in options if o.candidate_type != "do_nothing"]

    for idx, opt in enumerate(operational_options):
        is_top = (idx == 0)
        card_title = f"{'⭐ Top Recommendation: ' if is_top else f'Option {idx+1}: '} {opt.crew_id} ({opt.candidate_type.replace('_', ' ').title()})"

        with target.container(border=True):
            cols = st.columns([3, 2])
            with cols[0]:
                st.markdown(f"### {card_title}")
                st.markdown(f"**Base:** `{opt.base}` | **Legality:** {'✅ **LEGAL**' if opt.ledger.legal else '❌ **ILLEGAL**'}")

                if opt.expiry_utc:
                    st.markdown(f"⏳ **Decision Half-Life Expiry:** `{opt.expiry_utc}`")

                if opt.repair:
                    st.warning(f"🔧 **Actionable Minimal Repair:** `{opt.repair.lever.replace('_', ' ')}` by **{opt.repair.magnitude_minutes}m** to clear {opt.repair.repaired_rule}.\n\n*{opt.repair.side_effects}*")

            with cols[1]:
                st.metric(label="Total Cost (INR)", value=f"₹{int(opt.cost.total_inr):,}")
                with st.expander("Line-Item Cost Breakdown"):
                    for item in opt.cost.line_items:
                        st.markdown(f"• {item}")

    # Render Do-Nothing Benchmark
    if do_nothing:
        with target.container(border=True):
            st.markdown("### ⚠️ Do-Nothing Benchmark (Flight Cancellation)")
            cols = st.columns([3, 2])
            with cols[0]:
                st.error("Cancelling disrupted flights causes immediate regulatory passenger compensation and network delay.")
                if operational_options and operational_options[0].ledger.legal:
                    savings = do_nothing.cost.total_inr - operational_options[0].cost.total_inr
                    st.success(f"💰 **Adopting Top Option Saves: ₹{int(savings):,}**")
            with cols[1]:
                st.metric(label="Cancellation Penalty (INR)", value=f"₹{int(do_nothing.cost.total_inr):,}")
                with st.expander("Cost Breakdown"):
                    for item in do_nothing.cost.line_items:
                        st.markdown(f"• {item}")
