"""Unit tests for hash-anchored verification certificates."""

from pathlib import Path
import pytest
from advisor.audit.certificate import generate_certificate, verify_certificate
from advisor.data.repository import OpsRepository, DEFAULT_DB_PATH
from advisor.domain.evidence import LegalityLedger, RuleVerdict


@pytest.fixture
def repo():
    return OpsRepository(DEFAULT_DB_PATH)


def test_certificate_generate_and_verify(tmp_path, repo):
    cert_path = tmp_path / "test_cert.json"

    verdicts = [
        RuleVerdict(
            rule_id="RULE-DUTY-02",
            passed=True,
            headline="Legal",
            arithmetic="32.0h + 7.5h = 39.5h <= 60.0h",
            inputs={},
            margin=20.5,
            source_rows=["crew:C-1042"],
        )
    ]
    ledger = LegalityLedger(subject="C-1042", context="P-2291", verdicts=verdicts)

    cert = generate_certificate(
        trace_id="tr-test-01",
        overlay_stack=["sick:C-1042:2026-09-15"],
        source_rows=["crew:C-1042", "pairing:P-2291"],
        ledger=ledger,
        repair_offered=None,
        output_path=cert_path,
    )

    assert cert_path.exists()
    assert "dataset_sha256" in cert
    assert "ruleset_sha256" in cert

    # Verify certificate offline
    assert verify_certificate(cert_path, db_path=DEFAULT_DB_PATH) is True
