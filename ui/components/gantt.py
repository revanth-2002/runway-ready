"""Digital Twin Aircraft Rotation & Flight Matrix Visualizer."""

from datetime import timedelta
from typing import Optional
import pandas as pd
import plotly.express as px
import streamlit as st
from advisor.domain.timeutil import parse_utc
from advisor.twin.view import DigitalTwinState


def render_gantt_diff(
    twin_view: Optional[DigitalTwinState],
    container=None,
    key_prefix: str = "gantt",
) -> None:
    """Renders an interactive Aircraft Tail Gantt matrix and flight status manifest."""
    target = container if container else st

    if not twin_view or not twin_view.active_flights:
        target.info("Digital twin flight projection not available.")
        return

    target.subheader("✈️ Aircraft Rotation & Fleet Status Monitor")

    # 1. Prepare flight records
    records = []
    for f_id, fl in twin_view.active_flights.items():
        dep_str = twin_view.flight_estimated_deps.get(f_id, fl.dep_utc)
        dep_dt = parse_utc(dep_str)
        arr_dt = dep_dt + timedelta(minutes=fl.block_minutes)
        status = twin_view.flight_statuses.get(f_id, "ON_TIME")

        records.append({
            "Flight": fl.flight_id,
            "Tail": fl.tail_id or "UNASSIGNED",
            "Fleet": fl.aircraft_type,
            "Origin": fl.origin,
            "Destination": fl.destination,
            "Route": f"{fl.origin} ➔ {fl.destination}",
            "Start": dep_dt,
            "Finish": arr_dt,
            "Date": dep_str[:10],
            "Dep_Time": dep_str[11:16] + "Z",
            "Arr_Time": arr_dt.strftime("%H:%M") + "Z",
            "Block_Min": fl.block_minutes,
            "Status": status,
            "Passengers": fl.passengers or 0,
            "Label": f"{fl.flight_id} ({fl.origin}→{fl.destination})",
            "Rotation": fl.rotation_id or "None",
        })

    if not records:
        target.info("No active flights found in digital twin.")
        return

    df = pd.DataFrame(records)

    # 2. Top-Level Filter Controls
    all_dates = sorted(df["Date"].unique().tolist())
    default_date_idx = all_dates.index("2026-09-15") if "2026-09-15" in all_dates else 0

    col_d, col_tail, col_stat = target.columns([2, 2, 2])

    with col_d:
        selected_date = st.selectbox(
            "📅 Operational Date",
            ["All Dates"] + all_dates,
            index=default_date_idx + 1 if "2026-09-15" in all_dates else 0,
            key=f"{key_prefix}_date_filter",
        )

    all_tails = sorted(df["Tail"].unique().tolist())
    with col_tail:
        selected_tail = st.selectbox(
            "🛩️ Aircraft Tail",
            ["All Aircraft Tails"] + all_tails,
            key=f"{key_prefix}_tail_filter",
        )

    with col_stat:
        selected_status = st.selectbox(
            "🚦 Flight Status",
            ["All Statuses", "🚨 Disrupted Only (Uncrewed/Delayed)", "🟢 On-Time Only"],
            key=f"{key_prefix}_status_filter",
        )

    # Apply Filters
    filtered_df = df.copy()
    if selected_date != "All Dates":
        filtered_df = filtered_df[filtered_df["Date"] == selected_date]
    if selected_tail != "All Aircraft Tails":
        filtered_df = filtered_df[filtered_df["Tail"] == selected_tail]
    if selected_status == "🚨 Disrupted Only (Uncrewed/Delayed)":
        filtered_df = filtered_df[filtered_df["Status"].isin(["UNCREWED", "DELAYED", "CANCELLED"])]
    elif selected_status == "🟢 On-Time Only":
        filtered_df = filtered_df[filtered_df["Status"] == "ON_TIME"]

    # 3. Fleet Health Executive KPI Metrics
    total_f = len(filtered_df)
    ontime_f = sum(filtered_df["Status"] == "ON_TIME")
    uncrewed_f = sum(filtered_df["Status"] == "UNCREWED")
    delayed_f = sum(filtered_df["Status"] == "DELAYED")
    disrupted_f = uncrewed_f + delayed_f
    affected_pax = filtered_df[filtered_df["Status"] != "ON_TIME"]["Passengers"].sum()

    m1, m2, m3, m4 = target.columns(4)
    m1.metric("Scheduled Flights", f"{total_f} Sectors", f"Date: {selected_date}")
    m2.metric("On-Time Flights", f"{ontime_f}", f"{(ontime_f / total_f * 100):.1f}% OTP" if total_f else "0%")
    m3.metric(
        "Uncrewed / Disrupted",
        f"{disrupted_f} Flights",
        f"-{affected_pax:,} Pax Risk" if disrupted_f else "Normal",
        delta_color="inverse" if disrupted_f else "normal",
    )
    m4.metric("Total Passengers", f"{filtered_df['Passengers'].sum():,} Pax", "En-route / Booked")

    target.markdown("---")

    if filtered_df.empty:
        target.warning("No flights match the selected date and status filters.")
        return

    # 4. Tabbed Views: Aircraft Tail Timeline vs. Manifest Table
    tab_gantt, tab_manifest = target.tabs(["✈️ Aircraft Tail Gantt Matrix", "📋 Flight Operational Manifest"])

    status_colors = {
        "ON_TIME": "#10b981",   # Emerald Green
        "UNCREWED": "#ef4444",  # Crimson Red
        "DELAYED": "#f59e0b",   # Amber Gold
        "CANCELLED": "#64748b", # Slate Gray
    }

    with tab_gantt:
        try:
            # Sort tails in standard alphabetical order
            tail_order = sorted(filtered_df["Tail"].unique().tolist())
            fig = px.timeline(
                filtered_df,
                x_start="Start",
                x_end="Finish",
                y="Tail",
                text="Label",
                color="Status",
                color_discrete_map=status_colors,
                category_orders={"Tail": tail_order},
                hover_data={
                    "Flight": True,
                    "Route": True,
                    "Dep_Time": True,
                    "Arr_Time": True,
                    "Passengers": True,
                    "Status": True,
                    "Tail": False,
                    "Label": False,
                    "Start": False,
                    "Finish": False,
                },
                title=f"Fleet Rotation Timeline · {selected_date}",
            )
            fig.update_yaxes(autorange="reversed", title_text="Aircraft Tail")
            fig.update_xaxes(title_text="UTC Time (Timeline)")
            fig.update_traces(textposition="inside", insidetextanchor="middle")

            # Add vertical reference line for 06:00Z Disruption Window if on 2026-09-15
            if selected_date in ["2026-09-15", "All Dates"]:
                fig.add_vline(
                    x="2026-09-15 06:00:00",
                    line_dash="dash",
                    line_color="#dc2626",
                    annotation_text="06:00Z Disruption Snapshot",
                    annotation_position="top right",
                )

            chart_height = max(380, len(tail_order) * 75 + 100)
            fig.update_layout(
                height=chart_height,
                margin=dict(l=10, r=10, t=40, b=10),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_plotly")
        except Exception as e:
            st.error(f"Plotly Gantt rendering error: {e}")
            st.dataframe(filtered_df[["Flight", "Tail", "Route", "Dep_Time", "Arr_Time", "Status", "Passengers"]], key=f"{key_prefix}_error_df")

    with tab_manifest:
        manifest_display = filtered_df[[
            "Flight", "Tail", "Fleet", "Origin", "Destination", "Date", "Dep_Time", "Arr_Time", "Block_Min", "Passengers", "Status"
        ]].copy()
        manifest_display.columns = [
            "Flight ID", "Aircraft Tail", "Fleet", "Origin", "Destination", "Date", "STD (UTC)", "STA (UTC)", "Block (min)", "Pax", "Status"
        ]
        st.dataframe(manifest_display, use_container_width=True, hide_index=True, key=f"{key_prefix}_manifest_df")
