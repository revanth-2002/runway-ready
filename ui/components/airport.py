"""Airport Hub Operations & Aviation Weather Component.

Implements deep-dive station situational awareness:
1. Runway configurations and ground operational status.
2. Decoded METAR / TAF aviation weather forecasts with visual gauges.
3. Inbound (Arrivals) and Outbound (Departures) flight boards with real-time delays.
4. Station standby and reserve crew readiness.
"""

from typing import Any, Callable, Dict, List, Optional
import pandas as pd
import streamlit as st

from advisor.api.client import ApiClient


def render_airport_hub(
    station_code: str = "BLR",
    api_client: Optional[ApiClient] = None,
    on_select_station: Optional[Callable[[str], None]] = None,
    on_back: Optional[Callable[[], None]] = None,
    container=None,
    key_prefix: str = "airport_hub",
) -> None:
    """Renders comprehensive Airport Hub Operations view for a selected station."""
    target = container if container else st

    if api_client is None:
        from advisor.api.client import get_api_client
        api_client = get_api_client()

    code = station_code.upper()

    # 1. Top Navigation & Airport Hub Selector
    col_nav1, col_nav2 = target.columns([3, 7])
    with col_nav1:
        st.markdown(f"### 📍 Station Hub: `{code}`")

    with col_nav2:
        station_options = ["BLR", "DEL", "BOM", "HYD", "MAA"]
        stn_idx = station_options.index(code) if code in station_options else 0
        selected_code = st.radio(
            "Select Station Hub",
            station_options,
            index=stn_idx,
            horizontal=True,
            key=f"{key_prefix}_stn_radio",
            label_visibility="collapsed",
        )
        if selected_code != code and on_select_station:
            on_select_station(selected_code)
            st.rerun()

    # 2. Fetch Station Data from Backend API
    try:
        data = api_client.get_station_details(selected_code)
    except Exception as exc:
        target.error(f"Failed to fetch station data for {selected_code}: {exc}")
        return

    # 3. Station Header Banner
    weather = data.get("weather", {})
    runways_str = " · ".join(data.get("runways", []))
    flt_cat = weather.get("flight_category", "VFR")
    cat_badge = "🟢 VFR" if flt_cat == "VFR" else ("🟡 MVFR" if flt_cat == "MVFR" else "🔴 IFR")

    with target.container(border=True):
        h_col1, h_col2 = st.columns([4, 2])
        with h_col1:
            st.markdown(f"## 📍 {data['airport_name']} (`{data['station_code']}` / `{data['icao_code']}`)")
            st.markdown(f"**City:** {data['city']} · **Elevation:** `{data['elevation_ft']:,} ft` · **Runways:** `{runways_str}`")
        with h_col2:
            st.markdown(f"<div style='text-align: right;'><span style='font-size: 20px;'>{cat_badge}</span><br/><small style='color: #94a3b8;'>Runway in Use: <b>{weather.get('runway_in_use', 'Nominal')}</b></small></div>", unsafe_allow_html=True)

    # Operational Advisory Alert if present
    if weather.get("advisory"):
        target.warning(f"⚠️ **Aerodrome Operational Notice:** {weather['advisory']}")

    st.markdown("")

    # =========================================================================
    # SECTION 1: Aviation Weather & 24-Hour Forecasts (Decoded METAR / TAF)
    # =========================================================================
    target.subheader("🌤️ Aviation Weather Observations & 24-Hour Forecasts")
    target.caption("Translating cryptic METAR & TAF aerodrome messages into visual, operational decision metrics.")

    # Visual METAR Gauges
    w_col1, w_col2, w_col3, w_col4, w_col5, w_col6 = target.columns(6)
    with w_col1:
        st.metric(
            "Temperature",
            f"{weather.get('temperature_c', 27):.0f}°C",
            help=f"Dewpoint: {weather.get('dewpoint_c', 18):.0f}°C",
        )
    with w_col2:
        gusts = weather.get("wind_gusts_kts")
        gust_str = f" G{gusts}kt" if gusts else ""
        st.metric(
            "Surface Wind",
            f"{weather.get('wind_direction_deg', 90):03d}° / {weather.get('wind_speed_kts', 10)}kt{gust_str}",
            help=f"Crosswind component: {weather.get('crosswind_component_kts', 0)} kts",
        )
    with w_col3:
        vis_km = weather.get("visibility_m", 6000) / 1000
        st.metric("Visibility", f"{vis_km:.1f} km", help=f"{weather.get('visibility_m', 6000):,} meters")
    with w_col4:
        st.metric("Cloud Coverage", weather.get("clouds", "FEW 2,500 ft"))
    with w_col5:
        qnh = weather.get("altimeter_hpa", 1012)
        inhg = qnh * 0.02953
        st.metric("Altimeter (QNH)", f"{qnh} hPa", help=f"{inhg:.2f} inHg")
    with w_col6:
        st.metric("Flight Rules", cat_badge)

    # 24-Hour TAF Forecast Timeline Cards
    forecast_periods = weather.get("forecast_periods", [])
    if forecast_periods:
        st.markdown("**24-Hour Aerodrome Forecast Horizon (Decoded TAF):**")
        fc_cols = target.columns(len(forecast_periods))
        for idx, fc in enumerate(forecast_periods):
            with fc_cols[idx]:
                with st.container(border=True):
                    st.markdown(f"**{fc['period_name']}**")
                    st.markdown(f"<span style='font-size: 24px;'>{fc['icon']}</span> **{fc['condition']}**", unsafe_allow_html=True)
                    st.caption(f"🌡️ **{fc['temp_c']}°C** · 💨 **{fc['wind_str']}**")
                    st.caption(f"🌧️ Rain Risk: **{fc['precip_prob_pct']}%** · ☁️ **{fc['ceiling']}**")

    # Cryptic Dispatcher Code (METAR / TAF) in Expander
    with target.expander("📄 Raw ICAO METAR & TAF Aerodrome Telegraphs", expanded=False):
        st.code(f"METAR: {weather.get('metar_raw', '')}\nTAF:   {weather.get('taf_raw', '')}", language="plaintext")

    st.markdown("---")

    # =========================================================================
    # SECTION 2: Station Flight Operations Board (Arrivals & Departures)
    # =========================================================================
    target.subheader(f"🛫 {selected_code} Flight Movements & Schedule Board")

    # High-level Station Flight Metrics
    m1, m2, m3, m4, m5, m6 = target.columns(6)
    with m1:
        st.metric("Total Movements", f"{data.get('total_movements', 0)}")
    with m2:
        st.metric("Departures", f"{data.get('departure_count', 0)}")
    with m3:
        st.metric("Arrivals", f"{data.get('arrival_count', 0)}")
    with m4:
        rate = data.get('on_time_rate_pct', 100.0)
        st.metric("On-Time Rate", f"{rate}%")
    with m5:
        st.metric("Passengers Handled", f"{data.get('total_passengers', 0):,}")
    with m6:
        st.metric("Station Reserves", f"{data.get('standby_reserves_count', 0)} Crew")

    # Flight Movement Filters Row
    f_filter_col1, f_filter_col2, f_filter_col3 = target.columns([3, 2, 3])
    with f_filter_col1:
        movement_filter = st.radio(
            "Movement Type",
            [f"🛫 Departures ({data.get('departure_count', 0)})", f"🛬 Arrivals ({data.get('arrival_count', 0)})", f"All Movements ({data.get('total_movements', 0)})"],
            horizontal=True,
            key=f"{key_prefix}_movement_radio",
        )
    with f_filter_col2:
        status_filter = st.selectbox(
            "Status Filter",
            ["All Statuses", "🟢 ON_TIME", "🟡 DELAYED", "🔴 UNCREWED"],
            key=f"{key_prefix}_status_filter",
        )
    with f_filter_col3:
        search_term = st.text_input(
            "Search Flight, Tail or Airport",
            placeholder="e.g. DX412, VT-DXA, DEL...",
            key=f"{key_prefix}_search_input",
        )

    # Select flights based on movement filter
    if "Departures" in movement_filter:
        raw_list = data.get("departures", [])
    elif "Arrivals" in movement_filter:
        raw_list = data.get("arrivals", [])
    else:
        raw_list = data.get("departures", []) + data.get("arrivals", [])

    # Filter flights
    filtered_flights = []
    for f in raw_list:
        # Status filter
        if status_filter != "All Statuses":
            clean_status = status_filter.split()[-1]
            if f.get("status") != clean_status:
                continue

        # Search filter
        if search_term.strip():
            term = search_term.strip().upper()
            searchable = f"{f.get('flight_id', '')} {f.get('origin', '')} {f.get('destination', '')} {f.get('tail_id', '')}".upper()
            if term not in searchable:
                continue

        filtered_flights.append(f)

    if filtered_flights:
        df = pd.DataFrame(filtered_flights)

        # Reorder and format dataframe columns
        cols_map = {
            "flight_id": "Flight",
            "movement_type": "Type",
            "route": "Route",
            "scheduled_utc": "Scheduled (UTC)",
            "estimated_utc": "Estimated (UTC)",
            "delay_minutes": "Delay (min)",
            "status": "Operational Status",
            "tail_id": "Aircraft Tail",
            "aircraft_type": "Fleet",
            "passengers": "Pax",
            "gate": "Gate / Stand",
        }

        # Keep available columns in neat order
        avail = [c for c in cols_map.keys() if c in df.columns]
        df_display = df[avail].rename(columns=cols_map)

        # Clean timestamps for concise readability (HH:MMZ)
        if "Scheduled (UTC)" in df_display.columns:
            df_display["Scheduled (UTC)"] = df_display["Scheduled (UTC)"].apply(lambda s: s[11:16] + "Z" if isinstance(s, str) and len(s) >= 16 else s)
        if "Estimated (UTC)" in df_display.columns:
            df_display["Estimated (UTC)"] = df_display["Estimated (UTC)"].apply(lambda s: s[11:16] + "Z" if isinstance(s, str) and len(s) >= 16 else s)

        target.dataframe(
            df_display,
            use_container_width=True,
            hide_index=True,
            height=min(450, 45 + len(df_display) * 35),
        )
    else:
        target.info(f"No flights match the current filter criteria for {selected_code}.")
