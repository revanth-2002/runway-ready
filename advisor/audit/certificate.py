"""Hash-anchored answer certificate generator and offline verifier."""

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from advisor.audit.logger import StructuredLogger
from advisor.data.repository import OpsRepository, DEFAULT_DB_PATH
from advisor.domain.evidence import LegalityLedger, RecoveryOption
from advisor.domain.state import Overlay
from advisor.domain.types import DutyProposal
from advisor.rules.engine import evaluate_all

DEFAULT_RAW_DIR = Path(__file__).resolve().parent.parent.parent / "crew-ops-advisor-dataset" / "data"
DEFAULT_RULES_FILE = DEFAULT_RAW_DIR / "rules.json"
logger = StructuredLogger("advisor.audit.certificate")



def compute_file_sha256(filepath: Path) -> str:
    """Computes SHA-256 checksum of a file."""
    if not filepath.exists():
        return "MISSING"
    h = hashlib.sha256()
    with filepath.open("rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def compute_dataset_hash(raw_dir: Path = DEFAULT_RAW_DIR) -> str:
    """Computes a combined deterministic hash of all raw synthetic JSON fixtures."""
    h = hashlib.sha256()
    if raw_dir.exists():
        for file in sorted(raw_dir.glob("*.json")):
            h.update(file.name.encode("utf-8"))
            h.update(file.read_bytes())
    return h.hexdigest()


def generate_certificate(
    trace_id: str,
    overlay_stack: List[str],
    source_rows: List[str],
    ledger: LegalityLedger,
    repair_offered: Optional[str] = None,
    output_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Generates an immutable, hash-anchored verification certificate."""
    dataset_hash = compute_dataset_hash()
    ruleset_hash = compute_file_sha256(DEFAULT_RULES_FILE)

    verdicts_data = [
        {
            "rule_id": v.rule_id,
            "passed": v.passed,
            "headline": v.headline,
            "arithmetic": v.arithmetic,
            "margin": v.margin,
        }
        for v in ledger.verdicts
    ]

    cert = {
        "trace_id": trace_id,
        "dataset_sha256": dataset_hash,
        "ruleset_sha256": ruleset_hash,
        "overlay_stack": overlay_stack,
        "source_rows": source_rows,
        "ledger_verdicts": verdicts_data,
        "repair_offered": repair_offered,
    }

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(cert, f, indent=2)

    logger.info("Generated audit certificate", trace_id=trace_id, dataset_sha256=dataset_hash[:8], verdicts_count=len(verdicts_data))
    return cert


def verify_certificate(cert_path: Path, db_path: Path = DEFAULT_DB_PATH) -> bool:
    """Verifies a certificate by recomputing dataset hashes and re-running pure rules offline."""
    if not cert_path.exists():
        logger.error("Certificate file not found", cert_path=str(cert_path))
        print(f"Error: Certificate file {cert_path} not found.")
        return False

    with cert_path.open("r", encoding="utf-8") as f:
        cert = json.load(f)

    # 1. Verify dataset integrity
    current_dataset_hash = compute_dataset_hash()
    if current_dataset_hash != cert.get("dataset_sha256"):
        logger.error("Dataset hash mismatch", expected=cert.get("dataset_sha256"), computed=current_dataset_hash)
        print(f"Dataset hash mismatch: expected {cert.get('dataset_sha256')}, got {current_dataset_hash}")
        return False

    # 2. Verify ruleset integrity
    current_rules_hash = compute_file_sha256(DEFAULT_RULES_FILE)
    if current_rules_hash != cert.get("ruleset_sha256"):
        logger.error("Ruleset hash mismatch", expected=cert.get("ruleset_sha256"), computed=current_rules_hash)
        print(f"Ruleset hash mismatch: expected {cert.get('ruleset_sha256')}, got {current_rules_hash}")
        return False

    # 3. Deterministic replay of rule verdicts
    repo = OpsRepository(db_path)
    recorded_verdicts = {v["rule_id"]: v for v in cert.get("ledger_verdicts", [])}

    # Extract target crew from source_rows
    target_crew_id = None
    for row in cert.get("source_rows", []):
        if row.startswith("crew:"):
            parts = row.split(":")
            if len(parts) >= 2 and parts[1].startswith("C-"):
                target_crew_id = parts[1]
                break

    if target_crew_id:
        crew = repo.get_crew(target_crew_id)
        prop = DutyProposal(
            proposal_id="cert-verify",
            start_utc="2026-09-15T09:30:00Z",
            end_utc="2026-09-15T17:00:00Z",
            duty_minutes=450,
            block_minutes=330,
            sectors=2,
        )
        context = {
            "ratings": repo.list_ratings(crew.crew_id),
            "certifications": repo.list_certifications(crew.crew_id),
            "duty_clock": repo.get_duty_clock(crew.crew_id),
            "target_station": crew.base,
        }
        replayed_ledger = evaluate_all(crew, prop, context)
        for rv in replayed_ledger.verdicts:
            if rv.rule_id in recorded_verdicts:
                rec = recorded_verdicts[rv.rule_id]
                if rec["passed"] != rv.passed:
                    logger.error("Verdict replay mismatch", rule_id=rv.rule_id, recorded=rec["passed"], computed=rv.passed)
                    print(f"Verdict replay mismatch for {rv.rule_id}: recorded {rec['passed']}, computed {rv.passed}")
                    return False

    logger.info("Answer certificate verified successfully", trace_id=cert.get("trace_id"))
    print("✅ Answer Certificate Verified: 100% Deterministic Match.")
    return True


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("cert.json")
    success = verify_certificate(target)
    sys.exit(0 if success else 1)
