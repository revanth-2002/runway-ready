"""Voice Agent UI Cockpit powered by Sarvam AI (Speech-to-Text & Text-to-Speech)."""

from datetime import datetime, timezone
import os
import streamlit as st

from advisor.voice.sarvam import (
    AVAILABLE_VOICE_SPEAKERS,
    get_sarvam_client,
)
from advisor.audit.logger import append_audit_event


def render_voice_cockpit_css():
    """Injects high-end ATC / Radio Cockpit styling for the voice agent."""
    st.markdown(
        """
        <style>
        .voice-card {
            background: linear-gradient(135deg, rgba(13, 27, 42, 0.95), rgba(27, 38, 59, 0.9));
            border: 1px solid rgba(0, 229, 255, 0.3);
            border-radius: 12px;
            padding: 1.25rem;
            margin-bottom: 1.5rem;
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
        }
        .voice-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 1rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            padding-bottom: 0.5rem;
        }
        .voice-title {
            font-size: 1.2rem;
            font-weight: 700;
            color: #00e5ff;
            letter-spacing: 0.5px;
            margin: 0;
        }
        .soundwave-container {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 4px;
            height: 36px;
            margin: 0.75rem 0;
        }
        .soundwave-bar {
            width: 4px;
            background: #00e5ff;
            border-radius: 2px;
            animation: soundwave 1.2s ease-in-out infinite alternate;
        }
        .soundwave-bar:nth-child(1) { height: 12px; animation-delay: 0.1s; }
        .soundwave-bar:nth-child(2) { height: 24px; animation-delay: 0.2s; }
        .soundwave-bar:nth-child(3) { height: 32px; animation-delay: 0.4s; }
        .soundwave-bar:nth-child(4) { height: 18px; animation-delay: 0.3s; }
        .soundwave-bar:nth-child(5) { height: 28px; animation-delay: 0.5s; }
        .soundwave-bar:nth-child(6) { height: 10px; animation-delay: 0.15s; }
        @keyframes soundwave {
            0% { transform: scaleY(0.4); opacity: 0.4; }
            100% { transform: scaleY(1.0); opacity: 1.0; }
        }
        .transcript-bubble {
            background: rgba(0, 229, 255, 0.08);
            border-left: 3px solid #00e5ff;
            padding: 0.75rem 1rem;
            border-radius: 0 8px 8px 0;
            margin: 0.75rem 0;
            font-family: monospace;
            color: #e0e6ed;
            font-size: 0.95rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_voice_agent_cockpit(on_query_submit=None):
    """Renders the comprehensive Sarvam AI Voice Agent interface."""
    render_voice_cockpit_css()
    client = get_sarvam_client()

    # Session state keys
    if "sarvam_transcript" not in st.session_state:
        st.session_state.sarvam_transcript = ""
    if "sarvam_last_audio" not in st.session_state:
        st.session_state.sarvam_last_audio = None
    if "sarvam_speaking" not in st.session_state:
        st.session_state.sarvam_speaking = False

    is_configured = client.is_configured()

    with st.container():
        st.markdown(
            f"""
            <div class="voice-card">
                <div class="voice-header">
                    <span class="voice-title">🎙️ Sarvam AI Operations Voice Agent</span>
                    <span style="font-size: 0.85rem; color: {'#00e676' if is_configured else '#ffab00'};">
                        {'🟢 Sarvam AI Online (Saaras STT + Bulbul TTS)' if is_configured else '🟡 Sarvam API Key Required'}
                    </span>
                </div>
            """,
            unsafe_allow_html=True,
        )

        # Configuration Accordion
        with st.expander("⚙️ Voice Settings & Sarvam API Key", expanded=not is_configured):
            col_k, col_s = st.columns([3, 2])
            with col_k:
                user_key = st.text_input(
                    "Sarvam API Subscription Key",
                    value=client.api_key or "",
                    type="password",
                    placeholder="Enter your Sarvam AI API subscription key...",
                    help="Get your key at https://dashboard.sarvam.ai/",
                    key="input_sarvam_api_key",
                )
                if user_key and user_key != client.api_key:
                    client.set_api_key(user_key)
                    st.success("Sarvam API key updated successfully!")

            with col_s:
                speaker_choice = st.selectbox(
                    "ATC Controller Voice Persona",
                    options=list(AVAILABLE_VOICE_SPEAKERS.keys()),
                    format_func=lambda k: AVAILABLE_VOICE_SPEAKERS[k],
                    index=0,
                    key="voice_speaker_select",
                )

        # Two operational columns: Live Mic & 1-Click Radio Presets
        col_mic, col_presets = st.columns([3, 2])

        with col_mic:
            st.markdown("##### 🎤 Speak to Controller Console")
            # Streamlit native audio recorder
            audio_data = st.audio_input(
                "Click microphone to record your operational directive",
                key="voice_agent_audio_recorder",
            )

            if audio_data is not None:
                audio_bytes = audio_data.read()
                if audio_bytes and (
                    "last_recorded_bytes_len" not in st.session_state
                    or st.session_state.last_recorded_bytes_len != len(audio_bytes)
                ):
                    st.session_state.last_recorded_bytes_len = len(audio_bytes)
                    with st.spinner("Transcribing voice directive via Sarvam Saaras STT..."):
                        try:
                            res = client.transcribe(audio_bytes, filename="directive.wav")
                            transcript = res.get("transcript", "")
                            st.session_state.sarvam_transcript = transcript
                            append_audit_event("VOICE_TRANSCRIBED", {"transcript": transcript, "engine": "sarvam_saaras"})
                        except Exception as e:
                            st.error(f"Sarvam STT Error: {e}")

            if st.session_state.sarvam_transcript:
                st.markdown(
                    f"""
                    <div class="transcript-bubble">
                        <strong>Transcribed Directive:</strong><br/>
                        "{st.session_state.sarvam_transcript}"
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if st.button("🚀 Execute Directive in Digital Twin", type="primary", use_container_width=True):
                    query_text = st.session_state.sarvam_transcript
                    if on_query_submit:
                        on_query_submit(query_text)
                    st.session_state.sarvam_transcript = ""

        with col_presets:
            st.markdown("##### 📻 Quick Radio Presets")
            st.caption("1-click simulated pilot callouts and controller inquiries:")

            preset_queries = [
                ("🚨 Captain A. Nair Sick Call (DX412)", "Captain A. Nair is sick for flight DX412 tomorrow. What is the impact and who is the recommended replacement?"),
                ("👥 Bangalore Standby Roster", "Who is on reserve at BLR tomorrow?"),
                ("🛑 BLR Station Closure Loss", "Simulate mass cancellation loss at BLR station on 2026-09-15."),
                ("🛡️ Safety Abstention Test", "Is Captain C-9999 available to fly DX412?"),
            ]

            for label, p_query in preset_queries:
                if st.button(label, use_container_width=True, key=f"preset_btn_{label[:8]}"):
                    st.session_state.sarvam_transcript = p_query
                    if on_query_submit:
                        on_query_submit(p_query)

        # Spoken Audio Briefing Output Section (if audio synthesized)
        if st.session_state.sarvam_last_audio:
            st.markdown("---")
            st.markdown("##### 🔊 Spoken Operational Briefing (Sarvam Bulbul TTS)")
            st.markdown(
                """
                <div class="soundwave-container">
                    <div class="soundwave-bar"></div>
                    <div class="soundwave-bar"></div>
                    <div class="soundwave-bar"></div>
                    <div class="soundwave-bar"></div>
                    <div class="soundwave-bar"></div>
                    <div class="soundwave-bar"></div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.audio(
                st.session_state.sarvam_last_audio,
                format="audio/wav",
                autoplay=True,
            )

        st.markdown("</div>", unsafe_allow_html=True)


def synthesize_and_play_response(prose_briefing: str, speaker: str = "meera"):
    """Helper called after simulation to synthesize and play spoken briefing via Sarvam AI."""
    client = get_sarvam_client()
    if not client.is_configured() or not prose_briefing:
        return

    try:
        wav_bytes = client.synthesize(prose_briefing, speaker=speaker)
        st.session_state.sarvam_last_audio = wav_bytes
        append_audit_event("VOICE_SYNTHESIZED", {"length_chars": len(prose_briefing), "speaker": speaker, "engine": "sarvam_bulbul"})
    except Exception as e:
        # Graceful degradation if TTS rate limited or network issue
        st.caption(f"ℹ️ Sarvam Voice Synthesis notice: {e}")
