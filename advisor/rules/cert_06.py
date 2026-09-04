"""RULE-CERT-06: Certification Validity calculation."""

from datetime import datetime
from typing import Any, Dict
from advisor.domain.evidence import RuleVerdict
from advisor.domain.timeutil import parse_utc
from advisor.domain.types import Crew, DutyProposal


def evaluate_cert_06(
    crew: Crew, proposal: DutyProposal, context: Dict[str, Any]
) -> RuleVerdict:
    certs = context.get("certifications", [])
    rule_cfg = context.get("rules_config", {}).get("RULE-CERT-06", {})
    configured_reqs = rule_cfg.get("required_certs")

    if configured_reqs and any(c.cert_type in configured_reqs for c in certs):
        required_certs = configured_reqs
    elif certs:
        required_certs = [c.cert_type for c in certs]
    else:
        required_certs = configured_reqs or ["licence", "medical_class1"]

    duty_date = (
        parse_utc(proposal.start_utc).date()
        if proposal.start_utc
        else parse_utc("2026-09-15T00:00:00Z").date()
    )

    cert_map = {c.cert_type: c for c in certs}
    missing_certs = []
    expired_certs = []
    earliest_expiry_days = 99999.0

    for req in required_certs:
        if req not in cert_map:
            missing_certs.append(req)
        else:
            c = cert_map[req]
            exp_date = datetime.strptime(c.expires_on[:10], "%Y-%m-%d").date()
            delta_days = (exp_date - duty_date).days
            if delta_days < 0:
                expired_certs.append(f"{req} (expired {abs(delta_days)}d ago)")
            if delta_days < earliest_expiry_days:
                earliest_expiry_days = float(delta_days)

    passed = (len(missing_certs) == 0) and (len(expired_certs) == 0)
    margin = earliest_expiry_days if passed else (
        -1.0 if missing_certs else -float(max(1, abs(earliest_expiry_days)))
    )

    if passed:
        arithmetic = f"All {len(required_certs)} required certs valid; earliest expiry in {int(earliest_expiry_days)} days"
        headline = f"Certifications valid ({int(earliest_expiry_days)}d buffer)"
    else:
        issues = missing_certs + expired_certs
        arithmetic = f"Certification deficiencies: {', '.join(issues)}"
        headline = f"Invalid certifications: {', '.join(issues)}"

    source_rows = [f"crew:{crew.crew_id}"]
    for c in certs:
        source_rows.append(f"certification:{crew.crew_id}:{c.cert_type}:{c.expires_on}")

    return RuleVerdict(
        rule_id="RULE-CERT-06",
        passed=passed,
        headline=headline,
        arithmetic=arithmetic,
        inputs={
            "required_certs": required_certs,
            "held_certs": list(cert_map.keys()),
            "missing_certs": missing_certs,
            "expired_certs": expired_certs,
            "duty_date": str(duty_date),
        },
        margin=margin,
        source_rows=source_rows,
        assumption="Missing certification record evaluates strictly to FAIL",
    )
