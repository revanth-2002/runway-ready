"""Crew Ops Advisor — Streamlit Airline Operations Controller Console."""

import os
import sys
from pathlib import Path

# Ensure root workspace is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import importlib
import streamlit as st
from advisor.audit.logger import StructuredLogger
from advisor.data.repository import OpsRepository, DEFAULT_DB_PATH
from advisor.domain.state import OpsState
from advisor.llm.client import get_active_llm_info
from advisor.orchestrator.runner import orchestrate

import ui.components.cards
import ui.components.gantt
import ui.components.ledger
import ui.components.reserves
importlib.reload(ui.components.cards)
importlib.reload(ui.components.gantt)
importlib.reload(ui.components.ledger)
importlib.reload(ui.components.reserves)

from ui.components.cards import render_option_cards
from ui.components.gantt import render_gantt_diff
from ui.components.ledger import render_ledger_table
from ui.components.reserves import render_reserve_board

logger = StructuredLogger("ui.app")


# Page Configuration
st.set_page_config(
    page_title="Crew Ops Advisor | Airline Digital Twin",
    page_icon="✈️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Global Anti-Autoscroll & Docked Bottom Prompt CSS
st.markdown(
    """
    <style>
    /* Prevent browser autoscroll jumping and anchor locks across reruns */
    html, body, [data-testid="stAppViewContainer"], section.main, [data-testid="stVerticalBlock"] {
        overflow-anchor: none !important;
        scroll-behavior: auto !important;
    }

    /* =========================================================
       STATIC FIXED FOOTER: 100% Locked to Bottom of Window
       ========================================================= */
    div[data-testid="stForm"] {
        position: fixed !important;
        bottom: 0px !important;
        left: 21rem !important;
        right: 0px !important;
        width: calc(100% - 21rem) !important;
        height: auto !important;
        min-height: 0 !important;
        max-height: 80px !important;
        z-index: 999999 !important;
        background: #0f172a !important; /* solid opaque slate-900 */
        border-top: 2px solid rgba(255, 255, 255, 0.18) !important;
        box-shadow: 0 -10px 35px rgba(0, 0, 0, 0.6) !important;
        padding: 10px 2.5rem !important;
        margin: 0 !important;
        box-sizing: border-box !important;
    }

    div[data-testid="stForm"] > div {
        height: auto !important;
        min-height: 0 !important;
    }

    /* Expand to full window width when sidebar is collapsed */
    [data-testid="stAppViewContainer"]:has([data-testid="stSidebar"][aria-expanded="false"]) div[data-testid="stForm"] {
        left: 0px !important;
        width: 100% !important;
    }

    /* Responsive for mobile and tablet screens */
    @media (max-width: 991px) {
        div[data-testid="stForm"] {
            left: 0px !important;
            width: 100% !important;
            padding: 10px 1.5rem 12px 1.5rem !important;
        }
    }

    /* Ensure ample bottom clearance so the static footer never obscures cards/tables when scrolled to the end */
    div[data-testid="stMainBlockContainer"] {
        padding-bottom: 7.5rem !important;
    }

    /* Command prompt input field styling */
    div[data-testid="stTextInput"] input {
        font-size: 14px;
        padding: 9px 14px;
        border-radius: 6px;
        background-color: #1e293b !important;
        border: 1px solid rgba(255, 255, 255, 0.22) !important;
        color: #f8fafc !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Initialize Session State
if "ops_state" not in st.session_state:
    st.session_state.ops_state = OpsState(db_path=DEFAULT_DB_PATH)
if "repo" not in st.session_state:
    st.session_state.repo = OpsRepository(DEFAULT_DB_PATH)
if "active_query" not in st.session_state:
    st.session_state.active_query = ""
if "selected_query" not in st.session_state:
    st.session_state.selected_query = ""
if "directive_input_field" not in st.session_state:
    st.session_state.directive_input_field = ""

# Sidebar: Controls & Scenario Presets
with st.sidebar:
    st.title("✈️ Crew Control Desk")
    st.caption("Operational Digital Twin · 06:00 UTC Disruption Window")
    st.markdown("---")

    def _trigger_scenario(query: str):
        st.session_state.active_query = query
        st.session_state.selected_query = query
        st.session_state.directive_input_field = query

    st.markdown("### 🚨 Disruption Scenarios")
    if st.button("🔴 Sick Callout: Capt Nair (C-1042)", use_container_width=True):
        _trigger_scenario("Captain A. Nair is sick for flight DX412. What is the impact and who is the recommended replacement?")

    if st.button("🔍 Reserve Lookup: BLR Station", use_container_width=True):
        _trigger_scenario("Who is on reserve at BLR tomorrow?")

    if st.button("🔍 Reserve Lookup: DEL Station", use_container_width=True):
        _trigger_scenario("Who is on reserve at DEL on 2026-09-15?")

    if st.button("⚖️ Legality Check: FO Patel (C-2087)", use_container_width=True):
        _trigger_scenario("Can First Officer V. Patel fly flight DX412 tomorrow?")

    if st.button("❓ Unknown Crew Abstention (C-9999)", use_container_width=True):
        _trigger_scenario("Is Captain C-9999 available to fly flight DX412?")

    if st.button("🛑 Out of Scope: Baggage Vouchers", use_container_width=True):
        _trigger_scenario("Can we book hotel accommodations and baggage vouchers for DX412 passengers?")

    if st.button("🕒 Ambiguous Time Abstention", use_container_width=True):
        _trigger_scenario("Who can fly sometime in the afternoon?")

    st.markdown("---")
    st.markdown("### ⚙️ System Controls")
    if st.button("🔄 Reset Baseline Digital Twin", use_container_width=True):
        from advisor.data.ingest import build_database
        build_database()
        st.session_state.ops_state = OpsState(db_path=DEFAULT_DB_PATH)
        st.session_state.repo = OpsRepository(DEFAULT_DB_PATH)
        st.success("Digital twin baseline re-materialized!")

    st.markdown("---")
    st.markdown("### 🤖 Reasoning Engine")
    llm_info = get_active_llm_info()
    if llm_info["configured"]:
        st.success(f"**Gemini Live**\n\n`{llm_info['model']}`")
    else:
        st.info("**Offline Stub**\n\nDeterministic sandbox mode\n\n*(Set `GEMINI_API_KEY` in `.env`)*")


# Main Screen Header
st.header("Operational Digital Twin — Decision Support Console")

# Determine active query for this run
active_query = st.session_state.get("active_query", "") or st.session_state.get("selected_query", "")

# Operational Workspaces Tabs
tab_decision, tab_reserves, tab_fleet = st.tabs([
    "🚨 Disruption & Decision Support",
    "👥 Standby & Reserve Roster",
    "✈️ Fleet & Aircraft Rotation Monitor",
])

# -------------------------------------------------------------
# TAB 1: Disruption & Decision Support
# -------------------------------------------------------------
with tab_decision:
    # 1. Initial baseline rendering if no query active
    if not active_query:
        col_left, col_right = st.columns([3, 2], gap="large")
        with col_left:
            st.info(
                "👋 **Welcome, Controller.**\n\n"
                "Select an operational scenario on the left or enter a natural language directive above to initiate simulation.\n\n"
                "• **Live Disruption Simulation:** Evaluates pairing breakdowns, passenger impacts, and minimal repair levers.\n"
                "• **Deterministic Legality Ledgers:** DGCA CAR Section 7 compliance verification with signed arithmetic margins.\n"
                "• **Anti-Hallucination Guard:** Mathematical slot substitution guaranteed free of invented IDs or figures."
            )
        with col_right:
            baseline_view = st.session_state.ops_state.materialize()
            render_gantt_diff(baseline_view, col_right, key_prefix="decision_base_gantt")

    # 2. Dynamic execution layout
    else:
        st.session_state.selected_query = ""  # Reset trigger
        st.session_state.active_query = ""    # Reset trigger
        logger.info("Executing user query in UI console", query=active_query)

        st.markdown(f"**Disruption Directive:** `{active_query}`")
        status_box = st.status("Executing operational pipeline in-process...", expanded=True)

        events = []
        for stage, payload in orchestrate(active_query, st.session_state.ops_state, st.session_state.repo):
            if stage == "status":
                status_box.update(label=payload)
            events.append((stage, payload))

        # Extract pipeline results
        abstain_event = next((p for s, p in events if s == "abstain"), None)
        evidence_payload = next((p for s, p in events if s == "evidence"), {})
        options_payload = next((p for s, p in events if s == "options"), None)
        prose_payload = next((p for s, p in events if s == "prose"), None)

        if abstain_event:
            logger.warning("Query aborted by abstention gate", query=active_query, reason=abstain_event["reason"])
            status_box.update(label=f"Abstention Triggered: {abstain_event['reason']}", state="error", expanded=False)
            st.error(f"🛑 **Operational Abstention ({abstain_event['reason']})**\n\n{abstain_event['message']}")

        elif "reserve_details" in evidence_payload or "reserves" in evidence_payload:
            status_box.update(label="Reserve Roster Loaded", state="complete", expanded=False)
            # Full-width dedicated reserve board without dry text bullet list
            render_reserve_board(
                reserve_details=evidence_payload.get("reserve_details"),
                default_station=evidence_payload.get("station", "BLR"),
                repo=st.session_state.repo,
                container=tab_decision,
                key_prefix="decision_reserves",
            )

        elif "twin_view" in evidence_payload:
            status_box.update(label="Operational Decision Ready", state="complete", expanded=False)
            # Bar chart / twin view present -> 2-pane layout
            col_left, col_right = st.columns([3, 2], gap="large")
            with col_left:
                if prose_payload:
                    st.markdown(prose_payload)
                if options_payload:
                    render_option_cards(options_payload, col_left)
                if "ledger" in evidence_payload:
                    render_ledger_table(evidence_payload["ledger"], col_left)

            with col_right:
                render_gantt_diff(evidence_payload["twin_view"], col_right, key_prefix="decision_diff_gantt")

        else:
            status_box.update(label="Operational Decision Ready", state="complete", expanded=False)
            # No bar chart -> utilize full screen width
            if prose_payload:
                st.markdown(prose_payload)
            if options_payload:
                render_option_cards(options_payload)
            if "ledger" in evidence_payload:
                render_ledger_table(evidence_payload["ledger"])


# -------------------------------------------------------------
# TAB 2: Standby & Reserve Roster (Dedicated Explorer)
# -------------------------------------------------------------
with tab_reserves:
    render_reserve_board(
        default_station="BLR",
        repo=st.session_state.repo,
        container=tab_reserves,
        key_prefix="tab_reserves",
    )

# -------------------------------------------------------------
# TAB 3: Fleet & Aircraft Rotation Monitor (Dedicated Fleet Matrix)
# -------------------------------------------------------------
with tab_fleet:
    active_view = st.session_state.ops_state.materialize()
    render_gantt_diff(active_view, container=tab_fleet, key_prefix="fleet_monitor_gantt")


# -------------------------------------------------------------
# DOCKED OPERATIONAL DIRECTIVE PROMPT (Bottom of Window)
# -------------------------------------------------------------
with st.container():
    with st.form("docked_directive_form", clear_on_submit=False, border=False):
        col_in, col_btn = st.columns([5, 1.2], gap="small")
        with col_in:
            typed_directive = st.text_input(
                "Operational Directive",
                value=st.session_state.get("directive_input_field", ""),
                placeholder="Type disruption directive (e.g. Captain A. Nair is sick for flight DX412 tomorrow...)",
                label_visibility="collapsed",
                key="directive_input_widget",
            )
        with col_btn:
            submit_clicked = st.form_submit_button("🚀 Run Directive", use_container_width=True, type="primary")

    if submit_clicked and typed_directive.strip():
        st.session_state.active_query = typed_directive.strip()
        st.session_state.directive_input_field = typed_directive.strip()
        st.rerun()
