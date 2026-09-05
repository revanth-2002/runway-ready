"""Sarvam AI Voice Agent SDK for Airline Operations Control.

Provides Speech-to-Text (STT via Saaras) and Text-to-Speech (TTS via Bulbul)
tailored for Indian English ATC/AOC operational communication.
"""

import base64
from io import BytesIO
import os
import re
from typing import Any, Dict, List, Optional
import httpx

from advisor.audit.logger import StructuredLogger

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = StructuredLogger("advisor.voice.sarvam")

SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"
SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"

# Available Sarvam Indian English voices
AVAILABLE_VOICE_SPEAKERS = {
    "meera": "Meera (Clear Indian English - OCC Controller)",
    "pavithra": "Pavithra (Indian English - Operations Coordinator)",
    "anushka": "Anushka (Expressive Indian English)",
    "arvind": "Arvind (Authoritative Indian English - Chief Pilot)",
    "amol": "Amol (Deep Indian English - Technical Dispatch)",
}


class SarvamVoiceClient:
    """Client for Sarvam AI Speech-to-Text and Text-to-Speech services."""

    def __init__(self, api_key: Optional[str] = None):
        try:
            from dotenv import load_dotenv
            load_dotenv(override=False)
        except ImportError:
            pass
        self._api_key = api_key or os.environ.get("SARVAM_API_KEY")

    @property
    def api_key(self) -> Optional[str]:
        if self._api_key:
            return self._api_key
        try:
            from dotenv import load_dotenv
            load_dotenv(override=False)
        except ImportError:
            pass
        return os.environ.get("SARVAM_API_KEY")

    def set_api_key(self, key: str) -> None:
        self._api_key = key.strip()
        os.environ["SARVAM_API_KEY"] = self._api_key

    def is_configured(self) -> bool:
        k = self.api_key
        return bool(k and len(k) > 10 and not k.startswith("your_"))

    def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "directive.wav",
        language_code: str = "en-IN",
        model: str = "saaras:v2",
    ) -> Dict[str, Any]:
        """Transcribes operational audio directive using Sarvam AI Saaras STT."""
        key = self.api_key
        if not key:
            raise ValueError(
                "Sarvam API key is not configured. Set SARVAM_API_KEY environment variable."
            )

        headers = {
            "api-subscription-key": key,
        }

        # Determine MIME type
        content_type = "audio/wav"
        if filename.endswith(".mp3"):
            content_type = "audio/mpeg"
        elif filename.endswith(".ogg"):
            content_type = "audio/ogg"
        elif filename.endswith(".webm"):
            content_type = "audio/webm"

        files = {
            "file": (filename, audio_bytes, content_type),
        }
        data = {
            "model": model,
            "language_code": language_code,
        }

        logger.info(
            "Sending audio to Sarvam AI STT",
            bytes_size=len(audio_bytes),
            filename=filename,
            model=model,
        )

        with httpx.Client(timeout=30.0) as client:
            try:
                resp = client.post(SARVAM_STT_URL, headers=headers, files=files, data=data)
                resp.raise_for_status()
                result = resp.json()
                transcript = result.get("transcript", "").strip()
                logger.info("Sarvam AI STT transcription successful", transcript=transcript)
                return {
                    "success": True,
                    "transcript": transcript,
                    "raw": result,
                }
            except httpx.HTTPStatusError as e:
                # Fallback without explicit language_code or model if v2 rejected
                logger.warning(
                    f"Sarvam STT failed with status {e.response.status_code}: {e.response.text}"
                )
                try:
                    fallback_data = {"model": "saaras:v1"}
                    resp2 = client.post(
                        SARVAM_STT_URL, headers=headers, files=files, data=fallback_data
                    )
                    resp2.raise_for_status()
                    result2 = resp2.json()
                    transcript = result2.get("transcript", "").strip()
                    return {"success": True, "transcript": transcript, "raw": result2}
                except Exception as ex:
                    logger.error("Sarvam STT fallback failed", error=ex)
                    raise RuntimeError(f"Sarvam STT API error: {e.response.text}") from e

    def synthesize(
        self,
        text: str,
        speaker: str = "meera",
        target_language_code: str = "en-IN",
        model: str = "bulbul:v1",
    ) -> bytes:
        """Synthesizes clean operational briefing text into spoken audio via Sarvam Bulbul TTS."""
        key = self.api_key
        if not key:
            raise ValueError(
                "Sarvam API key is not configured. Set SARVAM_API_KEY environment variable."
            )

        clean_text = self.clean_for_speech(text)
        if not clean_text:
            clean_text = "Operational directive processed."

        headers = {
            "api-subscription-key": key,
            "Content-Type": "application/json",
        }

        # Truncate text to 500 characters for optimal TTS latency and token budgets
        spoken_input = clean_text[:500]

        payloads_to_try = [
            # Schema 1: Standard inputs array (bulbul:v1 / v2)
            {
                "inputs": [spoken_input],
                "target_language_code": target_language_code,
                "speaker": speaker,
                "model": model,
            },
            # Schema 2: text string (bulbul:v3)
            {
                "text": spoken_input,
                "language_code": target_language_code,
                "speaker": speaker,
                "model": "bulbul:v3",
            },
        ]

        logger.info("Synthesizing speech via Sarvam AI TTS", speaker=speaker, length=len(spoken_input))

        with httpx.Client(timeout=30.0) as client:
            last_err = None
            for p in payloads_to_try:
                try:
                    resp = client.post(SARVAM_TTS_URL, headers=headers, json=p)
                    if resp.status_code == 200:
                        data = resp.json()
                        # Extract base64 audio
                        audio_b64 = None
                        if "audios" in data and data["audios"]:
                            audio_b64 = data["audios"][0]
                        elif "audio" in data:
                            audio_b64 = data["audio"]

                        if audio_b64:
                            raw_wav = base64.b64decode(audio_b64)
                            logger.info(
                                "Sarvam AI TTS synthesized successfully",
                                wav_bytes=len(raw_wav),
                            )
                            return raw_wav
                except Exception as ex:
                    last_err = ex
                    continue

            raise RuntimeError(
                f"Sarvam TTS failed across candidate schemas: {last_err}"
            )

    @staticmethod
    def clean_for_speech(markdown_text: str) -> str:
        """Converts raw markdown briefing into clear, natural spoken ATC/OCC radio phrasing."""
        if not markdown_text:
            return ""

        text = markdown_text
        # Remove markdown links [text](url) -> text
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        # Remove bold/italic markers
        text = re.sub(r"[*_~`#]", "", text)
        # Remove bullet points and headers
        text = re.sub(r"^[\s*•\-+>]+\s*", "", text, flags=re.MULTILINE)
        # Replace INR currency symbol with spoken words
        text = text.replace("₹", "Rupees ")
        # Clean multiple spaces and newlines
        text = re.sub(r"\n+", ". ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text


# Global singleton
_default_sarvam_client = SarvamVoiceClient()


def get_sarvam_client() -> SarvamVoiceClient:
    return _default_sarvam_client
