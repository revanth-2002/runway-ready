"""Sarvam AI Voice Agent UI Component for Runway Ready Cockpit.

Provides real-time Speech-to-Text (STT via Saaras) and Text-to-Speech (TTS via Bulbul)
integrated seamlessly into the Disruption Cockpit conversation interface.
"""

import os
import streamlit as st

from advisor.audit.logger import append_audit_event
from advisor.voice.sarvam import AVAILABLE_VOICE_SPEAKERS, get_sarvam_client


def render_voice_cockpit_css():
    """Injects high-end avionics styling for the voice agent."""
    st.markdown(
        """
        <style>
        .soundwave-container {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 4px;
            height: 36px;
            margin: 8px 0;
        }
        .soundwave-bar {
            width: 4px;
            background: linear-gradient(180deg, #38bdf8 0%, #0284c7 100%);
            border-radius: 2px;
            animation: soundwave-pulse 1.2s ease-in-out infinite;
        }
        .soundwave-bar:nth-child(1) { height: 12px; animation-delay: 0.1s; }
        .soundwave-bar:nth-child(2) { height: 24px; animation-delay: 0.3s; }
        .soundwave-bar:nth-child(3) { height: 34px; animation-delay: 0.2s; }
        .soundwave-bar:nth-child(4) { height: 28px; animation-delay: 0.4s; }
        .soundwave-bar:nth-child(5) { height: 18px; animation-delay: 0.15s; }
        .soundwave-bar:nth-child(6) { height: 10px; animation-delay: 0.35s; }

        @keyframes soundwave-pulse {
            0%, 100% { transform: scaleY(0.4); opacity: 0.5; }
            50% { transform: scaleY(1); opacity: 1; filter: drop-shadow(0 0 6px #38bdf8); }
        }

        .voice-card {
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            border: 1px solid rgba(56, 189, 248, 0.25);
            border-radius: 12px;
            padding: 1.25rem;
            margin-bottom: 1.25rem;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
        }
        .voice-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            padding-bottom: 0.5rem;
        }
        .voice-title {
            font-size: 1.1rem;
            font-weight: 700;
            color: #f8fafc;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .transcript-bubble {
            background: rgba(15, 23, 42, 0.85);
            border-left: 3px solid #38bdf8;
            padding: 10px 14px;
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


def render_docked_voice_agent(on_directive_ready=None):
    """Renders the audio popover widget positioned directly next to the conversation field.
    
    The Sarvam API key is handled automatically from .env in the backend.
    """
    render_voice_cockpit_css()
    client = get_sarvam_client()
    is_configured = client.is_configured()

    with st.popover("🎙️", help="Voice Agent — Speak operational directive via Sarvam AI"):
        st.markdown(
            f"""
            <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(255, 255, 255, 0.1); padding-bottom: 8px; margin-bottom: 10px;">
                <span style="font-weight: 700; font-size: 14px; color: #38bdf8;">🎙️ Sarvam AI Voice Agent</span>
                <span style="font-size: 11px; font-weight: 600; color: {'#10b981' if is_configured else '#f59e0b'};">
                    {'🟢 Connected (.env)' if is_configured else '⚠️ Needs SARVAM_API_KEY in .env'}
                </span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if not is_configured:
            st.info(
                "💡 Set your `SARVAM_API_KEY` in the `.env` file to enable live Saaras STT and Bulbul TTS. "
                "You can still test with the 1-click radio scenario presets below."
            )

        # Voice Speaker Persona selection
        speaker_choice = st.selectbox(
            "Controller Voice Persona",
            options=list(AVAILABLE_VOICE_SPEAKERS.keys()),
            format_func=lambda k: AVAILABLE_VOICE_SPEAKERS[k],
            index=0,
            key="docked_voice_speaker_select",
        )

        st.markdown("##### 🎤 Record Operational Radio Transmission")
        audio_data = st.audio_input(
            "Speak your disruption directive",
            key="docked_audio_input_widget",
        )

        if audio_data is not None:
            audio_bytes = audio_data.read()
            if audio_bytes and (
                "last_docked_audio_len" not in st.session_state
                or st.session_state.last_docked_audio_len != len(audio_bytes)
            ):
                st.session_state.last_docked_audio_len = len(audio_bytes)
                if not is_configured:
                    st.error("Sarvam API key is not configured in .env. Please set SARVAM_API_KEY in your .env file.")
                else:
                    with st.spinner("Transcribing radio transmission via Sarvam Saaras STT..."):
                        try:
                            res = client.transcribe(audio_bytes, filename="directive.wav")
                            transcript = res.get("transcript", "")
                            st.session_state.sarvam_transcript = transcript
                            append_audit_event("VOICE_TRANSCRIBED", {"transcript": transcript, "engine": "sarvam_saaras"})
                            if transcript and on_directive_ready:
                                on_directive_ready(transcript)
                        except Exception as e:
                            st.error(f"Sarvam STT Error: {e}")

        if st.session_state.get("sarvam_transcript"):
            st.markdown(
                f"""
                <div class="transcript-bubble">
                    <strong>Recognized Voice Directive:</strong><br/>
                    "{st.session_state.sarvam_transcript}"
                </div>
                """,
                unsafe_allow_html=True,
            )
            if st.button("🚀 Run Recognized Directive", type="primary", use_container_width=True, key="btn_run_recognized_voice"):
                query_text = st.session_state.sarvam_transcript
                st.session_state.sarvam_transcript = ""
                if on_directive_ready:
                    on_directive_ready(query_text)

        st.markdown("<div style='font-size:12px; color:#94a3b8; margin: 12px 0 4px 0;'>📻 <b>1-Click Radio Scenario Presets:</b></div>", unsafe_allow_html=True)
        col_p1, col_p2 = st.columns(2)
        with col_p1:
            if st.button("🚨 Capt Sick (DX412)", use_container_width=True, key="docked_p_sick"):
                p_text = "Captain A. Nair is sick for flight DX412 tomorrow. What is the impact and who is the recommended replacement?"
                if on_directive_ready:
                    on_directive_ready(p_text)
            if st.button("🛑 BLR Station Closure", use_container_width=True, key="docked_p_blr"):
                p_text = "Simulate mass cancellation loss at BLR station on 2026-09-15."
                if on_directive_ready:
                    on_directive_ready(p_text)

        with col_p2:
            if st.button("👥 BLR Standby Roster", use_container_width=True, key="docked_p_roster"):
                p_text = "Who is on reserve at BLR tomorrow?"
                if on_directive_ready:
                    on_directive_ready(p_text)
            if st.button("🛡️ Safety Abstention", use_container_width=True, key="docked_p_abstain"):
                p_text = "Is Captain C-9999 available to fly DX412?"
                if on_directive_ready:
                    on_directive_ready(p_text)

        # Spoken Audio Briefing Output Section (if response synthesized)
        if st.session_state.get("sarvam_last_audio"):
            st.markdown("---")
            st.markdown("##### 🔊 Latest Voice Briefing (Sarvam Bulbul TTS)")
            st.audio(
                st.session_state.sarvam_last_audio,
                format="audio/wav",
                autoplay=True,
            )


def render_voice_agent_cockpit(on_query_submit=None):
    """Renders standalone voice cockpit if needed."""
    render_docked_voice_agent(on_directive_ready=on_query_submit)


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
        st.caption(f"ℹ️ Sarvam Voice Synthesis notice: {e}")
