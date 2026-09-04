"""Interactive Standby & Reserve Roster Console for Airline Operations Control."""

from typing import Any, Dict, List, Optional
import pandas as pd
import streamlit as st
from advisor.data.repository import OpsRepository, DEFAULT_DB_PATH


def render_reserve_board(
    reserve_details: Optional[List[Dict[str, Any]]] = None,
    default_station: str = "BLR",
    repo: Optional[OpsRepository] = None,
    container=None,
    key_prefix: str = "reserves",
) -> None:
    """Renders an interactive, comprehensive Standby & Reserve Roster."""
    target = container if container else st

    if repo is None:
        repo = OpsRepository(DEFAULT_DB_PATH)

    target.subheader("👥 Crew Standby & Reserve Operations Board")

    # 1. Base Selection & Filters Row
    col_stn, col_rank, col_fleet, col_search = target.columns([2, 2, 2, 3])

    station_options = ["BLR", "DEL", "BOM", "HYD", "MAA"]
    current_station_idx = station_options.index(default_station) if default_station in station_options else 0

    with col_stn:
        selected_station = st.selectbox(
            "📍 Crew Base Station",
            station_options,
            index=current_station_idx,
            key=f"{key_prefix}_station_select",
        )

    # If selected station matches what was passed in reserve_details, use it; otherwise query repo
    if reserve_details and selected_station == default_station:
        reserves = reserve_details
    else:
        raw_reserves = repo.list_reserves(base=selected_station)
        reserves = []
        for r in raw_reserves:
            c = repo.get_crew(r.crew_id)
            ratings = repo.list_ratings(r.crew_id)
            clk = repo.get_duty_clock(r.crew_id)
            duty_7d = clk.duty_hours_7d if clk else 0.0
            reserves.append({
                "crew_id": c.crew_id,
                "name": c.name,
                "rank": c.rank,
                "base": r.base,
                "ratings": ratings,
                "oncall_start_utc": r.oncall_start_utc,
                "oncall_end_utc": r.oncall_end_utc,
                "standby_status": r.standby_status,
                "reachability_minutes": c.reachability_minutes or 45,
                "duty_hours_7d": duty_7d,
            })

    with col_rank:
        rank_filter = st.selectbox(
            "🎖️ Rank Filter",
            ["All Ranks", "Captain", "First Officer", "Senior Cabin Crew", "Cabin Crew"],
            key=f"{key_prefix}_rank",
        )

    with col_fleet:
        fleet_filter = st.selectbox(
            "✈️ Aircraft Fleet",
            ["All Fleets", "A320", "ATR72"],
            key=f"{key_prefix}_fleet",
        )

    with col_search:
        search_term = st.text_input(
            "🔍 Search Name / ID",
            placeholder="e.g. C-3310 or Sharma",
            key=f"{key_prefix}_search",
        ).strip().lower()

    # Apply filters
    filtered = []
    for r in reserves:
        if rank_filter != "All Ranks" and r["rank"].lower() != rank_filter.lower():
            continue
        if fleet_filter != "All Fleets" and fleet_filter not in r["ratings"]:
            continue
        if search_term:
            in_id = search_term in r["crew_id"].lower()
            in_name = search_term in r["name"].lower()
            if not (in_id or in_name):
                continue
        filtered.append(r)

    # 2. Executive KPI Header Metrics
    captains_count = sum(1 for r in reserves if "captain" in r["rank"].lower() and "senior" not in r["rank"].lower())
    fo_count = sum(1 for r in reserves if "first officer" in r["rank"].lower() or "fo" in r["rank"].lower())
    cc_count = sum(1 for r in reserves if "cabin" in r["rank"].lower())
    avg_reach = int(sum(r["reachability_minutes"] for r in reserves) / len(reserves)) if reserves else 0

    kpi1, kpi2, kpi3, kpi4 = target.columns(4)
    kpi1.metric("Total Standby Pool", f"{len(reserves)} Crew", f"{len(filtered)} Showing")
    kpi2.metric("Available Captains", f"{captains_count} CPT", "Ready to deploy")
    kpi3.metric("First Officers", f"{fo_count} FO", "Ready to deploy")
    kpi4.metric("Avg Reachability", f"{avg_reach} min", "Callout Notice")

    target.markdown("---")

    if not filtered:
        target.warning(f"No standby reserves match criteria for station **{selected_station}**.")
        return

    # 3. Pagination State & Controls (20 items per page)
    PAGE_SIZE = 20
    page_key = f"{key_prefix}_page"
    filter_sig_key = f"{key_prefix}_last_filter"
    current_filter_sig = f"{selected_station}_{rank_filter}_{fleet_filter}_{search_term}"

    if page_key not in st.session_state:
        st.session_state[page_key] = 0

    # Reset page on filter change
    if st.session_state.get(filter_sig_key) != current_filter_sig:
        st.session_state[page_key] = 0
        st.session_state[filter_sig_key] = current_filter_sig

    total_items = len(filtered)
    total_pages = max(1, (total_items + PAGE_SIZE - 1) // PAGE_SIZE)
    if st.session_state[page_key] >= total_pages:
        st.session_state[page_key] = max(0, total_pages - 1)

    current_page = st.session_state[page_key]
    start_idx = current_page * PAGE_SIZE
    end_idx = min(start_idx + PAGE_SIZE, total_items)
    page_items = filtered[start_idx:end_idx]

    def _go_prev():
        st.session_state[page_key] = max(0, st.session_state.get(page_key, 0) - 1)

    def _go_next():
        st.session_state[page_key] = min(total_pages - 1, st.session_state.get(page_key, 0) + 1)

    # Pagination Header
    p_header_cols = target.columns([5, 1, 2, 1])
    with p_header_cols[0]:
        st.markdown(f"### 📇 Standby Crew Cards <span style='font-size:14px; color:#6b7280; font-weight:normal;'>({start_idx + 1}–{end_idx} of {total_items})</span>", unsafe_allow_html=True)

    with p_header_cols[1]:
        st.button("◀ Prev", key=f"{key_prefix}_p_prev_top", on_click=_go_prev, disabled=(current_page == 0), use_container_width=True)

    with p_header_cols[2]:
        st.markdown(f"<div style='text-align:center; padding-top:6px; font-weight:500;'>Page {current_page + 1} of {total_pages}</div>", unsafe_allow_html=True)

    with p_header_cols[3]:
        st.button("Next ▶", key=f"{key_prefix}_p_next_top", on_click=_go_next, disabled=(current_page >= total_pages - 1), use_container_width=True)

    # 4. Standby Crew Cards Grid (2 Columns, 20 items max)
    card_cols = target.columns(2)
    for idx, r in enumerate(page_items):
        with card_cols[idx % 2]:
            with st.container(border=True):
                rank_icon = "👨‍✈️" if "captain" in r["rank"].lower() else ("🧑‍✈️" if "first" in r["rank"].lower() else "👩‍✈️")
                status_badge = "🟢 AVAILABLE" if r["standby_status"] == "AVAILABLE" else "🟡 CALLED"

                st.markdown(
                    f"**{rank_icon} {r['name']}** (`{r['crew_id']}`) "
                    f"<span style='float:right; font-size:12px; color:#10b981; font-weight:600;'>{status_badge}</span>",
                    unsafe_allow_html=True,
                )

                ratings_str = ", ".join(r["ratings"]) if r["ratings"] else "None"
                st.caption(f"**Rank:** `{r['rank']}` | **Fleet:** `{ratings_str}` | **Base:** `{r['base']}`")

                w_start = r["oncall_start_utc"][11:16] if len(r["oncall_start_utc"]) >= 16 else r["oncall_start_utc"]
                w_end = r["oncall_end_utc"][11:16] if len(r["oncall_end_utc"]) >= 16 else r["oncall_end_utc"]
                w_date = r["oncall_start_utc"][:10]

                st.caption(f"⏰ **Window:** `{w_date} {w_start}–{w_end}Z` | ⏱️ **Reachability:** `{r['reachability_minutes']}m`")

                duty_7d = r["duty_hours_7d"]
                duty_ratio = min(duty_7d / 60.0, 1.0)
                duty_margin = 60.0 - duty_7d
                margin_color = "#10b981" if duty_margin >= 10 else "#f59e0b"

                st.progress(duty_ratio)
                st.caption(
                    f"📊 **7D Duty:** `{duty_7d:.1f}h / 60h` "
                    f"(<span style='color:{margin_color}; font-weight:bold;'>+{duty_margin:.1f}h Margin</span>)",
                    unsafe_allow_html=True,
                )

                with st.expander("📲 Dispatch Callout Directive"):
                    callout_msg = (
                        f"URGENT CREW CALLOUT DIRECTIVE\n"
                        f"==============================\n"
                        f"To: {r['rank']} {r['name']} ({r['crew_id']})\n"
                        f"Base: {r['base']} | Fleet: {ratings_str}\n"
                        f"Standby Window: {w_date} {w_start}-{w_end}Z\n"
                        f"Notice: {r['reachability_minutes']}m reachability\n"
                        f"Accrued 7D Duty: {duty_7d:.1f}h / 60.0h (Compliant)\n"
                        f"Directive: Stand by for immediate pairing assignment. Confirm receipt with Crew Control Desk."
                    )
                    st.code(callout_msg, language="text")

    # Bottom pagination if more than 1 page
    if total_pages > 1:
        target.markdown("---")
        p_bottom_cols = target.columns([5, 1, 2, 1])
        with p_bottom_cols[0]:
            st.caption(f"Showing {start_idx + 1}–{end_idx} of {total_items} standby crew members")
        with p_bottom_cols[1]:
            st.button("◀ Prev", key=f"{key_prefix}_p_prev_btm", on_click=_go_prev, disabled=(current_page == 0), use_container_width=True)
        with p_bottom_cols[2]:
            st.markdown(f"<div style='text-align:center; padding-top:4px;'>Page {current_page + 1} of {total_pages}</div>", unsafe_allow_html=True)
        with p_bottom_cols[3]:
            st.button("Next ▶", key=f"{key_prefix}_p_next_btm", on_click=_go_next, disabled=(current_page >= total_pages - 1), use_container_width=True)
