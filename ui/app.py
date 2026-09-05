"""Crew Ops Advisor — Enterprise Airline Operations Center (AOC) Console.

Implements the 4 Essential Airline Dashboard Workspaces (Information Design framework)
backed by a decoupled REST API service layer (/api/v1/...) via ApiClient.
"""

from datetime import datetime, timezone
import importlib
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional
import uuid

# Ensure root workspace is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pandas as pd
import streamlit as st

from advisor.api.client import get_api_client
from advisor.audit.logger import StructuredLogger
from advisor.data.repository import OpsRepository, DEFAULT_DB_PATH
from advisor.llm.client import get_active_llm_info

import ui.components.airport
import ui.components.cards
import ui.components.gantt
import ui.components.ledger
import ui.components.reserves

importlib.reload(ui.components.airport)
importlib.reload(ui.components.cards)
importlib.reload(ui.components.gantt)
importlib.reload(ui.components.ledger)
importlib.reload(ui.components.reserves)

from ui.components.airport import render_airport_hub
from ui.components.cards import render_option_cards
from ui.components.gantt import render_gantt_diff
from ui.components.ledger import render_ledger_table
from ui.components.reserves import render_reserve_board

logger = StructuredLogger("ui.app")

# Page Configuration
st.set_page_config(
    page_title="Crew Ops Advisor | AOC Digital Twin Console",
    page_icon="✈️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Global Anti-Autoscroll, Cockpit Theme & Docked Bottom Prompt CSS
st.markdown(
    """
    <style>
    /* Prevent browser autoscroll jumping and anchor locks across reruns */
    html, body, [data-testid="stAppViewContainer"], section.main, [data-testid="stVerticalBlock"] {
        overflow-anchor: none !important;
        scroll-behavior: auto !important;
    }

    /* Cohesive Airline Cockpit Styling */
    .stApp {
        background-color: #0b132b;
        color: #f8fafc;
    }

    /* Equal-size Network Operational Pulse Cards */
    .pulse-metric-card {
        background: #1e293b;
        border: 1px solid rgba(255, 255, 255, 0.14);
        border-radius: 8px;
        padding: 12px 14px;
        height: 96px;
        min-height: 96px;
        max-height: 96px;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        box-sizing: border-box;
    }
    .pulse-label {
        font-size: 11.5px;
        font-weight: 600;
        color: #94a3b8;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .pulse-value {
        font-size: 20px;
        font-weight: 800;
        color: #f8fafc;
        line-height: 1.1;
    }
    .pulse-delta {
        font-size: 11.5px;
        font-weight: 600;
        display: flex;
        align-items: center;
        gap: 4px;
    }
    .delta-green { color: #34d399; }
    .delta-amber { color: #fbbf24; }
    .delta-red { color: #f87171; }
    .delta-muted { color: #94a3b8; }

    /* Equal-size Hub Station Operations Cards */
    .hub-station-card {
        background: #1e293b;
        border: 1px solid rgba(255, 255, 255, 0.14);
        border-radius: 8px 8px 0 0;
        padding: 14px 14px 10px 14px;
        height: 185px;
        min-height: 185px;
        max-height: 185px;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        box-sizing: border-box;
    }
    .hub-title-row {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        border-bottom: 1px solid rgba(255, 255, 255, 0.08);
        padding-bottom: 6px;
    }
    .hub-code {
        font-size: 20px;
        font-weight: 800;
        color: #38bdf8;
        letter-spacing: 0.5px;
    }
    .hub-city {
        font-size: 12.5px;
        color: #94a3b8;
        font-weight: 500;
    }
    .hub-stats-grid {
        font-size: 12.5px;
        color: #e2e8f0;
        display: flex;
        flex-direction: column;
        gap: 3px;
        margin: 4px 0;
    }
    .hub-stat-lbl {
        color: #94a3b8;
        font-weight: 500;
    }
    .hub-advisory-box {
        font-size: 11.5px;
        padding: 6px 8px;
        border-radius: 6px;
        line-height: 1.3;
        height: 48px;
        min-height: 48px;
        max-height: 48px;
        overflow-y: auto;
        box-sizing: border-box;
    }
    .hub-adv-warning {
        background: rgba(245, 158, 11, 0.12);
        border: 1px solid rgba(245, 158, 11, 0.35);
        color: #fcd34d;
    }
    .hub-adv-info {
        background: rgba(56, 189, 248, 0.12);
        border: 1px solid rgba(56, 189, 248, 0.35);
        color: #7dd3fc;
    }
    .hub-adv-success {
        background: rgba(16, 185, 129, 0.12);
        border: 1px solid rgba(16, 185, 129, 0.35);
        color: #6ee7b7;
    }

    /* Enterprise Navigation Tab Bar Styling */
    div[data-testid="stRadio"] > div[role="radiogroup"] {
        display: flex;
        flex-direction: row;
        gap: 8px;
        border-bottom: 2px solid rgba(255, 255, 255, 0.12);
        padding-bottom: 8px;
        margin-bottom: 20px;
    }
    div[data-testid="stRadio"] > div[role="radiogroup"] > label {
        background: #1e293b !important;
        border: 1px solid rgba(255, 255, 255, 0.14) !important;
        border-radius: 8px !important;
        padding: 9px 18px !important;
        font-size: 14px !important;
        font-weight: 600 !important;
        color: #94a3b8 !important;
        cursor: pointer !important;
        transition: all 0.2s ease-in-out !important;
    }
    div[data-testid="stRadio"] > div[role="radiogroup"] > label:hover {
        background: #334155 !important;
        color: #f8fafc !important;
        border-color: rgba(255, 255, 255, 0.25) !important;
    }
    div[data-testid="stRadio"] > div[role="radiogroup"] > label:has(input:checked) {
        background: #2563eb !important;
        color: #ffffff !important;
        border-color: #3b82f6 !important;
        box-shadow: 0 4px 12px rgba(37, 99, 235, 0.35) !important;
    }
    div[data-testid="stRadio"] > div[role="radiogroup"] > label > div:first-child {
        display: none !important;
    }

    /* Manifest Table Card Styling & Border/Padding */
    div[data-testid="stVerticalBlockBorderWrapper"]:has(.manifest-table-box) {
        background: #1e293b !important;
        border: 1px solid rgba(255, 255, 255, 0.16) !important;
        border-radius: 12px !important;
        padding: 22px 26px !important;
        margin-top: 16px !important;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25) !important;
    }
    .manifest-table-box {
        padding: 10px 0 4px 0;
    }
    div[data-testid="stDataFrame"] {
        border: 1px solid rgba(255, 255, 255, 0.12) !important;
        border-radius: 8px !important;
        overflow: hidden !important;
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

    /* Ensure ample bottom clearance so the static footer never obscures content */
    div[data-testid="stMainBlockContainer"] {
        padding-bottom: 8.5rem !important;
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

# Initialize Decoupled Backend API Client
api_client = get_api_client()

# Initialize Session State
if "active_query" not in st.session_state:
    st.session_state.active_query = ""
if "selected_query" not in st.session_state:
    st.session_state.selected_query = ""
if "directive_input_field" not in st.session_state:
    st.session_state.directive_input_field = ""
if "action_history" not in st.session_state:
    st.session_state.action_history = []
if "input_history" not in st.session_state:
    st.session_state.input_history = []
if "last_finalized" not in st.session_state:
    st.session_state.last_finalized = None
if "pending_evidence" not in st.session_state:
    st.session_state.pending_evidence = {}
if "pending_options" not in st.session_state:
    st.session_state.pending_options = None
if "pending_prose" not in st.session_state:
    st.session_state.pending_prose = None
if "pending_ledger" not in st.session_state:
    st.session_state.pending_ledger = None
if "pending_twin_view" not in st.session_state:
    st.session_state.pending_twin_view = None
if "active_station_hub" not in st.session_state:
    st.session_state.active_station_hub = "BLR"
if "offline_sandbox_mode" not in st.session_state:
    st.session_state.offline_sandbox_mode = False
if "nav_target" not in st.session_state:
    st.session_state.nav_target = None

# Query Live Health & Twin State from Backend API
try:
    health_info = api_client.get_health()
    twin_state_info = api_client.get_twin_state()
except Exception as e:
    health_info = {"status": "offline", "version": "1.0.0", "twin_warmed": False, "llm_mode": "stub"}
    twin_state_info = {"active_overlays_count": 0, "overlays": [], "last_action": None}


# =========================================================================
# Sidebar: Controls, Presets & Decision Memory
# =========================================================================
with st.sidebar:
    st.title("✈️ AOC Control Desk")
    st.caption("Airline Operations Center · Digital Twin Engine")

    # Visual API & Digital Twin Status Badge
    is_live = health_info.get("status") == "healthy"
    status_icon = "🟢" if is_live else "🔴"
    status_color = "#10b981" if is_live else "#ef4444"

    is_sandbox = st.session_state.get("offline_sandbox_mode", False)
    engine_badge = "Offline Sandbox (Deterministic)" if is_sandbox else health_info.get('llm_mode', 'standard')

    st.markdown(
        f"""
        <div style="background: rgba(16, 185, 129, 0.12); border: 1px solid {status_color}; border-radius: 8px; padding: 10px 14px; margin-bottom: 14px;">
            <div style="display: flex; align-items: center; gap: 8px;">
                <span style="font-size: 14px;">{status_icon}</span>
                <strong style="color: {status_color}; font-size: 13px;">API Service: /api/v1</strong>
            </div>
            <div style="color: #94a3b8; font-size: 11px; margin-top: 4px;">
                Engine: <b>{engine_badge}</b><br/>
                Digital Twin: <b>{'Pre-Warmed' if health_info.get('twin_warmed') else 'Standby'}</b>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Active Overlays & Decision Undo
    active_overlays = twin_state_info.get("overlays", [])
    if active_overlays:
        st.markdown(f"**⚡ Held Overlays Stack (`{len(active_overlays)}`):**")
        for idx, ov in enumerate(active_overlays):
            ov_icon = "🔴" if ov.get("kind") == "sick" else ("🟢" if ov.get("kind") == "reassign" else "🟡")
            st.caption(f"{idx+1}. {ov_icon} `{ov.get('label')}`")
        if st.button("↩️ Undo Last Decision", use_container_width=True):
            undo_res = api_client.undo_overlay()
            st.session_state.last_finalized = None
            now_str = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
            st.session_state.action_history.insert(0, {
                "timestamp": now_str,
                "action": "UNDO",
                "description": undo_res.get("message", "Reverted top overlay"),
            })
            st.rerun()

    st.markdown("---")

    def _trigger_scenario(query: str):
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
        st.session_state.active_query = query
        st.session_state.selected_query = query
        st.session_state.directive_input_field = query
        st.session_state.nav_target = "🚨 Disruption Cockpit"
        st.session_state.last_finalized = None
        st.session_state.input_history.insert(0, query)
        st.session_state.action_history.insert(0, {
            "timestamp": now_str,
            "action": "DIRECTIVE_INJECTED",
            "description": query,
        })

    st.markdown("### 🚨 Disruption Presets")
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
        rst_res = api_client.reset_baseline()
        st.session_state.last_finalized = None
        st.session_state.pending_evidence = {}
        st.session_state.pending_options = None
        st.session_state.pending_prose = None
        st.session_state.pending_ledger = None
        st.session_state.pending_twin_view = None
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
        st.session_state.action_history.insert(0, {
            "timestamp": now_str,
            "action": "RESET_BASELINE",
            "description": "Purged all overlays and re-materialized clean 06:00Z baseline",
        })
        st.success(rst_res.get("message", "Digital twin baseline re-materialized!"))
        st.rerun()

    # Controller Decision & Action Memory Panel
    with st.expander("🕒 Decision & Action Memory", expanded=False):
        if st.session_state.action_history:
            for item in st.session_state.action_history[:8]:
                st.markdown(f"**[{item['timestamp']}]** `{item['action']}`\n\n{item['description']}")
                st.markdown("---")
        else:
            st.caption("No controller actions recorded yet.")

    st.markdown("---")
    st.markdown("### 🤖 Reasoning Engine")

    offline_sandbox = st.toggle(
        "⚡ Offline Sandbox Mode",
        value=st.session_state.get("offline_sandbox_mode", False),
        help="Run 100% locally in deterministic sandbox mode with zero external network latency.",
        key="offline_sandbox_mode",
    )

    llm_info = get_active_llm_info()
    if offline_sandbox:
        st.info("**Offline Sandbox Active**\n\nDeterministic operational engine (<0.4s response, 0 external API calls)")
    elif llm_info["configured"]:
        st.success(f"**Gemini Live**\n\n`{llm_info['model']}`\n\n*(30s timeout breaker active)*")
    else:
        st.info("**Offline Stub**\n\nDeterministic sandbox mode\n\n*(Set `GEMINI_API_KEY` in `.env`)*")


# =========================================================================
# Main Screen: 4 Essential Airline Dashboard Workspaces
# =========================================================================

st.header("Operational Digital Twin — Airline Operations Center")

# Determine active query for this run
active_query = st.session_state.get("active_query", "") or st.session_state.get("selected_query", "")

WORKSPACE_TABS = [
    "🌐 Network Overview",
    "📍 Airport Hubs (BLR, DEL...)",
    "🚨 Disruption Cockpit",
    "👥 Standby Roster",
    "✈️ Fleet & Schedule",
]

# Apply pending programmatic workspace navigation before radio widget instantiation
if st.session_state.get("nav_target") in WORKSPACE_TABS:
    st.session_state.active_tab = st.session_state.nav_target
    st.session_state.nav_target = None

if "active_tab" not in st.session_state or st.session_state.active_tab not in WORKSPACE_TABS:
    st.session_state.active_tab = "🌐 Network Overview"

active_tab = st.radio(
    "Workspace Navigation",
    WORKSPACE_TABS,
    horizontal=True,
    label_visibility="collapsed",
    key="active_tab",
)


# -------------------------------------------------------------------------
# WORKSPACE 1: Global Operations & Network Pulse
# -------------------------------------------------------------------------
if active_tab == "🌐 Network Overview":
    try:
        overview_data = api_client.get_network_overview()
        kpis = overview_data.get("kpis", {})
        stations = overview_data.get("stations", [])
    except Exception as exc:
        st.error(f"Failed to fetch network overview from API: {exc}")
        kpis = {}
        stations = []

    st.subheader("🌐 Network Operational Pulse & Health")

    # Executive Metric Ribbon (Equal-Size Cards)
    rate = kpis.get('on_time_rate_pct', 100.0)
    alerts = kpis.get('disruption_alerts_count', 0)
    seats = kpis.get('passenger_seats_at_risk', 0)
    avail_res = kpis.get('total_available_reserves', 0)

    p_col1, p_col2, p_col3, p_col4, p_col5, p_col6 = st.columns(6)
    pulse_metrics = [
        ("Active Fleet", f"{kpis.get('total_active_tails', 6)} Tails", "VT-DXA to VT-DXF", "delta-muted"),
        ("Scheduled Flights", f"{kpis.get('scheduled_flights', 147)} Flights", "Total scheduled", "delta-muted"),
        ("On-Time Rate", f"{rate}%", "🟢 Nominal" if rate == 100 else f"🔻 {rate - 100:.1f}%", "delta-green" if rate == 100 else "delta-red"),
        ("Disruption Alerts", f"{alerts} Alerts", "🟢 Network clear" if alerts == 0 else f"⚠️ {alerts} Active", "delta-green" if alerts == 0 else "delta-amber"),
        ("Seats at Risk", f"{seats} Seats", "🟢 Zero pax impact" if seats == 0 else f"⚠️ {seats} Disrupted", "delta-green" if seats == 0 else "delta-red"),
        ("Available Reserves", f"{avail_res} Crew", "Standby ready", "delta-muted"),
    ]

    for idx, col in enumerate([p_col1, p_col2, p_col3, p_col4, p_col5, p_col6]):
        title, val, sub, d_cls = pulse_metrics[idx]
        with col:
            st.markdown(
                f"""
                <div class="pulse-metric-card">
                    <div class="pulse-label">{title}</div>
                    <div class="pulse-value">{val}</div>
                    <div class="pulse-delta {d_cls}">{sub}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    st.markdown("---")

    # Hub Station Health Matrix (5 Hub Bases - Equal-Size Cards)
    st.subheader("📍 Hub Station Operations Matrix")
    st.caption("Click any hub station below to inspect live weather observations, 24h forecasts, and arrivals/departures.")

    curr_hub = st.session_state.get("active_station_hub", "BLR")
    stn_cols = st.columns(len(stations) if stations else 5)
    
    city_map = {
        "BLR": "Bengaluru",
        "DEL": "Delhi",
        "BOM": "Mumbai",
        "HYD": "Hyderabad",
        "MAA": "Chennai",
    }

    for idx, stn_info in enumerate(stations):
        stn_code = stn_info['station']
        city_name = city_map.get(stn_code, stn_code)
        
        if stn_info.get("weather_advisory"):
            icon = "⚠️"
            notice_text = stn_info["weather_advisory"]
            badge_class = "hub-adv-warning"
        elif stn_info.get("maintenance_notice"):
            icon = "🔧"
            notice_text = stn_info["maintenance_notice"]
            badge_class = "hub-adv-info"
        else:
            icon = "🟢"
            notice_text = "Operations Nominal"
            badge_class = "hub-adv-success"

        with stn_cols[idx]:
            st.markdown(
                f"""
                <div class="hub-station-card">
                    <div class="hub-title-row">
                        <span class="hub-code">{stn_code}</span>
                        <span class="hub-city">{city_name}</span>
                    </div>
                    <div class="hub-stats-grid">
                        <div><span class="hub-stat-lbl">Departures:</span> <b>{stn_info['scheduled_departures']}</b></div>
                        <div><span class="hub-stat-lbl">Standby Crew:</span> <b>{stn_info['available_reserves']} / {stn_info['total_reserves']}</b></div>
                    </div>
                    <div class="hub-advisory-box {badge_class}" title="{notice_text}">
                        {icon} {notice_text}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            # Primary drill-down button: switches to Airport Hubs workspace
            if st.button(
                f"🔍 View {stn_code} Hub",
                key=f"btn_hub_inspect_{stn_code}",
                use_container_width=True,
                type="primary" if stn_code == curr_hub else "secondary",
            ):
                st.session_state.active_station_hub = stn_code
                st.session_state.nav_target = "📍 Airport Hubs (BLR, DEL...)"
                st.rerun()

    st.markdown("---")

    # Quick Disruption Launcher
    st.subheader("⚡ Quick-Launch Disruption Directives")
    q_c1, q_c2, q_c3 = st.columns(3)
    with q_c1:
        if st.button("🔴 Trigger Capt Nair Sick Callout", use_container_width=True):
            _trigger_scenario("Captain A. Nair is sick for flight DX412. What is the impact and who is the recommended replacement?")
            st.rerun()
    with q_c2:
        if st.button("⚖️ Verify FO Patel Legality (DX412)", use_container_width=True):
            _trigger_scenario("Can First Officer V. Patel fly flight DX412 tomorrow?")
            st.rerun()
    with q_c3:
        if st.button("🔍 Check BLR Standby Strength", use_container_width=True):
            _trigger_scenario("Who is on reserve at BLR tomorrow?")
            st.rerun()


# -------------------------------------------------------------------------
# WORKSPACE: Dedicated Airport Hub Deep-Dive (BLR, DEL, BOM, HYD, MAA)
# -------------------------------------------------------------------------
elif active_tab == "📍 Airport Hubs (BLR, DEL...)":
    def handle_tab_airport_select(stn):
        st.session_state.active_station_hub = stn

    render_airport_hub(
        station_code=st.session_state.get("active_station_hub", "BLR"),
        api_client=api_client,
        on_select_station=handle_tab_airport_select,
        key_prefix="tab_airport_view",
    )


# -------------------------------------------------------------------------
# WORKSPACE 2: Disruption & Recovery Cockpit
# -------------------------------------------------------------------------
elif active_tab == "🚨 Disruption Cockpit":
    def handle_finalize(opt):
        evidence = st.session_state.get("pending_evidence", {})
        disrupted_id = evidence.get("disrupted_crew_id", "C-1042")
        pairing_id = evidence.get("broken_pairing_id", "P-2291")
        flight_ids = evidence.get("flight_ids", [])

        # Call API endpoint to finalize recommendation
        res = api_client.finalize_recommendation(
            crew_id=opt.crew_id,
            candidate_type=opt.candidate_type,
            pairing_id=pairing_id,
            disrupted_crew_id=disrupted_id,
            flight_ids=flight_ids,
            cost_inr=opt.cost.total_inr,
            delay_minutes=opt.repair.magnitude_minutes if opt.repair and opt.repair.lever == "delay_departure" else 0,
            delayed_flight_id=opt.repair.repaired_rule if opt.repair else None,
        )

        now_str = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
        st.session_state.action_history.insert(0, {
            "timestamp": now_str,
            "action": "FINALIZED_RECOMMENDATION",
            "description": f"Adopted {opt.crew_id} ({opt.candidate_type.replace('_', ' ').title()}) for Pairing {pairing_id} (Cost: ₹{int(opt.cost.total_inr):,})",
        })

        st.session_state.last_finalized = {
            "candidate": opt.crew_id,
            "candidate_type": opt.candidate_type.replace('_', ' ').title(),
            "pairing_id": pairing_id,
            "cost_inr": opt.cost.total_inr,
            "disrupted_crew": disrupted_id,
        }

        st.session_state.pending_options = None
        st.session_state.pending_evidence = {}
        st.session_state.pending_prose = None
        st.session_state.pending_ledger = None
        st.session_state.pending_twin_view = None
        st.session_state.active_query = ""
        st.session_state.selected_query = ""
        st.session_state.directive_input_field = ""
        st.rerun()

    # 0. Banner for finalized recommendation if set
    if st.session_state.get("last_finalized"):
        fin = st.session_state.last_finalized
        st.success(
            f"🎉 **Operational Decision Finalized & Committed to Digital Twin!**\n\n"
            f"• **Dispatched Crew:** `{fin['candidate']}` ({fin['candidate_type']})\n"
            f"• **Pairing Recovered:** `{fin['pairing_id']}` (Disrupted: `{fin['disrupted_crew']}`)\n"
            f"• **Cost Incurred:** `₹{int(fin['cost_inr']):,}` | **Status:** All legs restored to `ON_TIME`."
        )

    # 1. Dynamic execution layout if new query entered
    if active_query:
        st.session_state.selected_query = ""  # Reset trigger
        st.session_state.active_query = ""    # Reset trigger
        logger.info("Executing user directive via API client", query=active_query)

        st.markdown(f"**Disruption Directive:** `{active_query}`")
        status_box = st.status("Executing operational pipeline via API...", expanded=True)

        try:
            is_offline = st.session_state.get("offline_sandbox_mode", False)
            sim_res = api_client.simulate_disruption(active_query, offline_mode=is_offline)
        except Exception as err:
            status_box.update(label=f"API Error: {err}", state="error", expanded=False)
            st.error(f"Error communicating with backend API: {err}")
            sim_res = None

        if sim_res:
            if sim_res.get("abstained"):
                status_box.update(label=f"Abstention Triggered: {sim_res['abstain_reason']}", state="error", expanded=False)
                st.error(f"🛑 **Operational Abstention ({sim_res['abstain_reason']})**\n\n{sim_res['abstain_message']}")
            else:
                status_box.update(label="Operational Decision Ready", state="complete", expanded=False)

                options = sim_res.get("parsed_options", [])
                ledger = sim_res.get("parsed_ledger")
                twin_view = sim_res.get("parsed_twin_view")
                prose = sim_res.get("prose_summary")

                st.session_state.pending_options = options
                st.session_state.pending_ledger = ledger
                st.session_state.pending_twin_view = twin_view
                st.session_state.pending_prose = prose
                st.session_state.pending_evidence = {
                    "disrupted_crew_id": sim_res.get("disrupted_crew_id"),
                    "broken_pairing_id": sim_res.get("broken_pairing_id"),
                    "flight_ids": sim_res.get("uncrewed_flight_ids", []),
                }

                col_left, col_right = st.columns([3, 2], gap="large")
                with col_left:
                    if prose:
                        st.markdown(prose)
                    if options:
                        render_option_cards(options, col_left, on_finalize=handle_finalize, key_prefix="live_opt")
                    if ledger:
                        render_ledger_table(ledger, col_left)

                with col_right:
                    if twin_view:
                        render_gantt_diff(twin_view, col_right, key_prefix="live_gantt_diff")

    # 2. Retain evaluated options across re-renders
    elif st.session_state.get("pending_options"):
        options = st.session_state.pending_options
        ledger = st.session_state.pending_ledger
        twin_view = st.session_state.pending_twin_view
        prose = st.session_state.pending_prose

        col_left, col_right = st.columns([3, 2], gap="large")
        with col_left:
            if prose:
                st.markdown(prose)
            if options:
                render_option_cards(options, col_left, on_finalize=handle_finalize, key_prefix="retained_opt")
            if ledger:
                render_ledger_table(ledger, col_left)

        with col_right:
            if twin_view:
                render_gantt_diff(twin_view, col_right, key_prefix="retained_gantt_diff")

    # 3. Baseline idle state
    else:
        col_left, col_right = st.columns([3, 2], gap="large")
        with col_left:
            st.info(
                "👋 **Welcome, Airline Operations Controller.**\n\n"
                "Select an operational scenario on the left or enter a natural language directive below to initiate simulation.\n\n"
                "• **Decoupled REST API:** Queries routed cleanly to structured `/api/v1/...` endpoints.\n"
                "• **Live Disruption Simulation:** Evaluates pairing breakdowns, passenger impacts, and minimal repair levers.\n"
                "• **Deterministic Legality Ledgers:** DGCA CAR Section 7 compliance verification with signed arithmetic margins.\n"
                "• **Anti-Hallucination Guard:** Mathematical slot substitution guaranteed free of invented IDs or figures.\n"
                "• **State Memory & Finalization:** Accept recovery options to update the digital twin and hold changes in memory."
            )
        with col_right:
            # Materialize baseline view for idle state
            try:
                from advisor.api.routes import twin_manager
                idle_view = twin_manager.state.materialize()
                render_gantt_diff(idle_view, col_right, key_prefix="idle_base_gantt")
            except Exception:
                st.caption("Fleet timeline ready for scenario injection.")


# -------------------------------------------------------------------------
# WORKSPACE 3: Standby & Reserve Crew Board (Preserved 100% As-Is)
# -------------------------------------------------------------------------
elif active_tab == "👥 Standby Roster":
    # 100% Unchanged crew cards architecture as mandated by user
    render_reserve_board(
        default_station="BLR",
        repo=OpsRepository(DEFAULT_DB_PATH),
        key_prefix="tab_reserves",
    )


# -------------------------------------------------------------------------
# WORKSPACE 4: Fleet Rotations & Flight Manifest
# -------------------------------------------------------------------------
elif active_tab == "✈️ Fleet & Schedule":
    from advisor.api.routes import twin_manager
    current_twin_view = twin_manager.state.materialize()
    render_gantt_diff(current_twin_view, key_prefix="fleet_workspace_gantt")


# =========================================================================
# DOCKED OPERATIONAL DIRECTIVE PROMPT (Bottom of Window across all tabs)
# =========================================================================
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
        st.session_state.nav_target = "🚨 Disruption Cockpit"
        st.rerun()
