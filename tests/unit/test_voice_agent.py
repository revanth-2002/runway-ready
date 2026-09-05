"""Unit tests for the Sarvam AI Voice Agent module and API endpoints."""

import pytest
from starlette.testclient import TestClient

from advisor.api.server import app
from advisor.voice.sarvam import SarvamVoiceClient, get_sarvam_client


@pytest.fixture
def client():
    return TestClient(app)


def test_clean_for_speech():
    """Verify markdown formatting, currency symbols, and emojis are sanitized for natural speech."""
    raw = """
    🚨 **Operational Impact Report:**
    • Disrupted Pilot: **C-1042** (Captain, Base: BLR)
    • Broken Pairing: `P-2291`
    • Total Cost: ₹18,500 [Details](file:///link)
    """
    cleaned = SarvamVoiceClient.clean_for_speech(raw)
    assert "**" not in cleaned
    assert "`" not in cleaned
    assert "₹" not in cleaned
    assert "Rupees 18,500" in cleaned
    assert "Disrupted Pilot: C-1042" in cleaned
    assert "Details" in cleaned
    assert "file:///" not in cleaned


def test_sarvam_client_configuration():
    """Verify Sarvam client key configuration and detection."""
    client = SarvamVoiceClient(api_key="test_dummy_key_1234567890")
    assert client.is_configured() is True
    assert client.api_key == "test_dummy_key_1234567890"

    unconfigured = SarvamVoiceClient(api_key="")
    assert unconfigured.is_configured() is False


def test_voice_synthesize_endpoint_unconfigured(client, monkeypatch):
    """Verify /api/v1/voice/synthesize returns 400 when Sarvam API key is not configured."""
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    sarvam = get_sarvam_client()
    sarvam.set_api_key("")

    resp = client.post(
        "/api/v1/voice/synthesize",
        json={"text": "Captain Nair is sick for flight DX412", "speaker": "meera"},
    )
    assert resp.status_code == 400
    assert "Sarvam AI API key is not configured" in resp.json()["detail"]
