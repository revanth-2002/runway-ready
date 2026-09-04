"""Baseline unit tests for exceptions and structured logger."""

import json
from advisor.audit.logger import StructuredLogger, append_audit_event
from advisor.domain.exceptions import (
    OpsAdvisorError,
    EntityNotFoundError,
    DataIngestionError,
    RuleEvaluationError,
    AbstentionTriggered,
    SlotSubstitutionError,
)


def test_exception_hierarchy():
    """Verify that domain exceptions inherit properly and preserve context."""
    err = EntityNotFoundError("Crew C-9999 not found", trace_id="tr-001")
    assert isinstance(err, OpsAdvisorError)
    assert err.message == "Crew C-9999 not found"
    assert err.trace_id == "tr-001"

    abstain = AbstentionTriggered("OUT_OF_SCOPE", "Baggage query out of scope", trace_id="tr-002")
    assert isinstance(abstain, OpsAdvisorError)
    assert abstain.reason == "OUT_OF_SCOPE"


def test_structured_logger(capsys):
    """Verify that StructuredLogger outputs valid JSON with contextual fields."""
    logger = StructuredLogger("test_logger")
    logger.info("Test message", crew_id="C-1042", latency_ms=42)

    captured = capsys.readouterr()
    payload = json.loads(captured.err or captured.out)
    assert payload["level"] == "INFO"
    assert payload["message"] == "Test message"
    assert payload["crew_id"] == "C-1042"
    assert payload["latency_ms"] == 42
    assert "timestamp" in payload


def test_structured_logger_error(capsys):
    """Verify structured logger error output formatting."""
    logger = StructuredLogger("test_error_logger")
    exc = ValueError("Invalid parameter")
    logger.error("Operation failed", error=exc, trace_id="tr-err")

    captured = capsys.readouterr()
    payload = json.loads(captured.err or captured.out)
    assert payload["level"] == "ERROR"
    assert payload["error_type"] == "ValueError"
    assert payload["error_detail"] == "Invalid parameter"
    assert payload["trace_id"] == "tr-err"


def test_audit_event_append(tmp_path):
    """Verify that append_audit_event appends valid JSONL entries."""
    log_file = tmp_path / "test_audit.jsonl"
    append_audit_event("SIMULATION_STARTED", {"scenario": "sick_crew"}, log_file=log_file)
    append_audit_event("SIMULATION_FINISHED", {"status": "SUCCESS"}, log_file=log_file)

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["event_type"] == "SIMULATION_STARTED"
    assert first["payload"]["scenario"] == "sick_crew"
