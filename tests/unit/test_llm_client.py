"""Unit tests for Gemini LLM client wrapper, configuration fetching, and offline fallback."""

import os
from unittest.mock import MagicMock, patch
from advisor.llm.client import (
    GeminiClientWrapper,
    StubClient,
    get_active_llm_info,
    get_default_llm_client,
    get_gemini_config,
)


def test_stub_client_deterministic():
    client = StubClient()
    resp = client.generate("extract intent: Captain C-1042 is sick")
    assert "simulate_sick" in resp
    assert "C-1042" in resp


def test_gemini_config_default():
    with patch.dict(os.environ, {}, clear=True):
        cfg = get_gemini_config()
        assert cfg["api_key"] is None
        assert cfg["model_name"] == "gemini-2.5-flash"


def test_gemini_config_custom_env():
    with patch.dict(
        os.environ,
        {"GEMINI_API_KEY": "test-key-123", "GEMINI_MODEL": "gemini-2.5-pro"},
        clear=True,
    ):
        cfg = get_gemini_config()
        assert cfg["api_key"] == "test-key-123"
        assert cfg["model_name"] == "gemini-2.5-pro"

        info = get_active_llm_info()
        assert info["provider"] == "gemini"
        assert info["model"] == "gemini-2.5-pro"
        assert info["configured"] is True


def test_gemini_config_google_api_key_fallback():
    with patch.dict(
        os.environ,
        {"GOOGLE_API_KEY": "google-fallback-key", "GEMINI_MODEL_NAME": "gemini-2.0-flash"},
        clear=True,
    ):
        cfg = get_gemini_config()
        assert cfg["api_key"] == "google-fallback-key"
        assert cfg["model_name"] == "gemini-2.0-flash"


def test_get_default_llm_client_offline_stub():
    with patch.dict(os.environ, {}, clear=True):
        client = get_default_llm_client()
        assert isinstance(client, StubClient)


def test_gemini_client_wrapper_execution():
    mock_genai_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = '{"intent": "custom_gemini_response"}'
    mock_genai_client.models.generate_content.return_value = mock_resp

    with patch("google.genai.Client", return_value=mock_genai_client):
        wrapper = GeminiClientWrapper(api_key="mock_key", model_name="gemini-2.5-flash")
        result = wrapper.generate("Test prompt", temperature=0.2)

        assert result == '{"intent": "custom_gemini_response"}'
        mock_genai_client.models.generate_content.assert_called_once()
        call_kwargs = mock_genai_client.models.generate_content.call_args.kwargs
        assert call_kwargs["model"] == "gemini-2.5-flash"
        assert call_kwargs["contents"] == "Test prompt"
        assert call_kwargs["config"].temperature == 0.2


def test_get_default_llm_client_fallback_on_init_error():
    with patch.dict(os.environ, {"GEMINI_API_KEY": "bad_key"}, clear=True):
        with patch("google.genai.Client", side_effect=RuntimeError("SDK init failure")):
            client = get_default_llm_client()
            # Should gracefully fall back to StubClient without crashing
            assert isinstance(client, StubClient)


def test_gemini_client_wrapper_http_options():
    mock_genai_client = MagicMock()
    with patch("google.genai.Client", return_value=mock_genai_client) as mock_client_cls:
        wrapper = GeminiClientWrapper(api_key="mock_key", model_name="gemini-2.5-flash", timeout_ms=30000)
        assert wrapper.timeout_ms == 30000
        mock_client_cls.assert_called_once()
        http_opt = mock_client_cls.call_args.kwargs["http_options"]
        assert http_opt.timeout == 30000
        assert http_opt.retry_options.attempts == 1


def test_render_slotted_prose_rate_limit_notice():
    from advisor.domain.evidence import ImpactReport, LegalityLedger
    from advisor.llm.renderer import render_slotted_prose

    failing_client = MagicMock()
    failing_client.generate.side_effect = RuntimeError("429 RESOURCE_EXHAUSTED rate limit exceeded")

    mock_impact = ImpactReport(
        disruption_id="disp-001",
        disrupted_crew_id="C-1042",
        broken_pairing_id="P-2291",
        uncrewed_flights=(),
        delayed_rotations=(),
        stranded_companions=(),
        passengers_affected=180,
        source_rows=[],
    )
    mock_ledger = LegalityLedger(subject="C-1042", context="test", verdicts=[])

    prose = render_slotted_prose(mock_impact, mock_ledger, [], client=failing_client)
    assert "You have hit the limit, please try again after some time." in prose
    assert "{{impact.crew_rank}} {{impact.crew_id}}" in prose


def test_render_slotted_prose_non_429_no_warning():
    from advisor.domain.evidence import ImpactReport, LegalityLedger
    from advisor.llm.renderer import render_slotted_prose

    failing_client = MagicMock()
    failing_client.generate.side_effect = RuntimeError("Network timeout or connection refused")

    mock_impact = ImpactReport(
        disruption_id="disp-002",
        disrupted_crew_id="C-1042",
        broken_pairing_id="P-2291",
        uncrewed_flights=(),
        delayed_rotations=(),
        stranded_companions=(),
        passengers_affected=180,
        source_rows=[],
    )
    mock_ledger = LegalityLedger(subject="C-1042", context="test", verdicts=[])

    prose = render_slotted_prose(mock_impact, mock_ledger, [], client=failing_client)
    assert "You have hit the limit" not in prose
    assert "{{impact.crew_rank}} {{impact.crew_id}}" in prose
