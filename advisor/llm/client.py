"""Provider-agnostic LLM client with Google Gemini and StubClient for offline execution."""

import os
from typing import Any, Dict, Optional, Protocol
from advisor.audit.logger import StructuredLogger

# Attempt to load environment variables from .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = StructuredLogger("advisor.llm.client")


class LLMClient(Protocol):
    def generate(self, prompt: str, temperature: float = 0.0) -> str:
        ...


class StubClient:
    """Deterministic offline LLM client returning structured JSON or slotted prose."""

    def generate(self, prompt: str, temperature: float = 0.0) -> str:
        prompt_lower = prompt.lower()

        # 1. Intent parsing prompts
        if "extract intent" in prompt_lower or "queryintent" in prompt_lower:
            if "sick" in prompt_lower:
                return """{
                    "intents": [
                        {
                            "intent": "simulate_sick",
                            "entities": {"crew_ids": ["C-1042"]},
                            "time_scope": {"raw": "tomorrow", "resolved_utc": "2026-09-15T00:00:00Z"}
                        },
                        {
                            "intent": "recommend_replacement",
                            "entities": {}
                        }
                    ],
                    "confidence": 0.96,
                    "unsupported_aspects": []
                }"""
            elif "reserve" in prompt_lower:
                return """{
                    "intents": [
                        {
                            "intent": "lookup_reserves",
                            "entities": {"stations": ["BLR"]},
                            "time_scope": {"raw": "tomorrow", "resolved_utc": "2026-09-15T00:00:00Z"}
                        }
                    ],
                    "confidence": 0.98,
                    "unsupported_aspects": []
                }"""
            elif "c-9999" in prompt_lower:
                return """{
                    "intents": [
                        {
                            "intent": "check_crew_status",
                            "entities": {"crew_ids": ["C-9999"]},
                            "time_scope": {"raw": "tomorrow", "resolved_utc": "2026-09-15T00:00:00Z"}
                        }
                    ],
                    "confidence": 0.90,
                    "unsupported_aspects": []
                }"""
            elif "hotel" in prompt_lower or "baggage" in prompt_lower:
                return """{
                    "intents": [
                        {
                            "intent": "out_of_scope_service",
                            "entities": {},
                            "time_scope": {}
                        }
                    ],
                    "confidence": 0.99,
                    "unsupported_aspects": ["hotel bookings", "baggage vouchers"]
                }"""
            elif "afternoon" in prompt_lower:
                return """{
                    "intents": [
                        {
                            "intent": "simulate_duty",
                            "entities": {},
                            "time_scope": {"raw": "afternoon", "resolved_utc": null}
                        }
                    ],
                    "confidence": 0.60,
                    "unsupported_aspects": ["ambiguous time afternoon"]
                }"""
            else:
                return """{
                    "intents": [{"intent": "general_query", "entities": {}, "time_scope": {}}],
                    "confidence": 0.85,
                    "unsupported_aspects": []
                }"""

        # 2. Slotted prose rendering prompts
        if "summarize the evidence bundle" in prompt_lower or "slot" in prompt_lower:
            return (
                "Captain {{impact.crew_id}} is incapacitated for {{impact.date}}. "
                "This breaks pairing {{impact.pairing_id}}, leaving {{impact.uncrewed_count}} flights uncrewed "
                "and stranding {{impact.passengers_affected}} passengers. "
                "Option 1: Assign on-base reserve {{options.0.crew_id}} at a cost of ₹{{options.0.cost_inr}}. "
                "Candidate {{options.1.crew_id}} breaches duty limit — {{options.1.repair.text}}."
            )

        return "Query processed successfully."


class GeminiClientWrapper:
    """Google Gemini API wrapper using the official google-genai SDK."""

    def __init__(self, api_key: str, model_name: str = "gemini-2.5-flash"):
        from google import genai
        self.api_key = api_key
        self.model_name = model_name
        self.client = genai.Client(api_key=api_key)

    def generate(self, prompt: str, temperature: float = 0.0) -> str:
        from google.genai import types
        config = types.GenerateContentConfig(temperature=temperature)
        resp = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=config,
        )
        return resp.text or ""


def get_gemini_config() -> Dict[str, Optional[str]]:
    """Fetches Gemini API key and model name from the environment."""
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    model_name = (
        os.environ.get("GEMINI_MODEL")
        or os.environ.get("GEMINI_MODEL_NAME")
        or "gemini-2.5-flash"
    )
    return {"api_key": api_key, "model_name": model_name}


def get_active_llm_info() -> Dict[str, Any]:
    """Returns metadata about the active LLM engine for logging and UI display."""
    cfg = get_gemini_config()
    if cfg["api_key"]:
        return {
            "provider": "gemini",
            "model": cfg["model_name"],
            "configured": True,
        }
    return {
        "provider": "stub",
        "model": "offline-deterministic",
        "configured": False,
    }


def get_default_llm_client() -> LLMClient:
    """Returns real Gemini client if API key is present in environment, else StubClient."""
    cfg = get_gemini_config()
    api_key = cfg["api_key"]
    model_name = cfg["model_name"] or "gemini-2.5-flash"

    if api_key:
        try:
            client = GeminiClientWrapper(api_key=api_key, model_name=model_name)
            logger.info("Using Gemini API client", model=model_name)
            return client
        except Exception as e:
            logger.warning(
                "Failed to initialize Gemini client, falling back to StubClient",
                error=str(e),
                model=model_name,
            )

    logger.info("Operating in deterministic offline mode with StubClient")
    return StubClient()

