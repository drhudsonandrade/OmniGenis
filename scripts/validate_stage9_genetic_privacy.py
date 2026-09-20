#!/usr/bin/env python3
"""Validate Stage 9 LGPD/genetic-data privacy controls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY_REL = "config/genetic_data_privacy_policy.json"


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def collect_errors(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    path = root / POLICY_REL
    if not path.is_file():
        return [f"missing {POLICY_REL}"]
    try:
        policy = _json(path)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"Stage 9 policy load failed: {exc}"]
    if not isinstance(policy, dict) or policy.get("schema") != "omnigenis-genetic-data-privacy-policy-v1":
        return ["invalid Stage 9 privacy policy schema"]
    if policy.get("status") != "ACTIVE":
        errors.append("Stage 9 privacy policy must be ACTIVE")
    if policy.get("jurisdiction") != "BR":
        errors.append("Stage 9 jurisdiction must remain explicit as BR")

    classification = policy.get("classification")
    if not isinstance(classification, dict):
        errors.append("Stage 9 classification missing")
    else:
        if classification.get("linked_genetic_data") != "GENETIC_SENSITIVE_PERSONAL_DATA":
            errors.append("linked genetic data classification drift")
        if classification.get("default_sensitive") is not True:
            errors.append("linked genetic data must default to sensitive")
        if classification.get("anonymous_upgrade_is_automatic") is not False:
            errors.append("anonymization may not be inferred automatically")

    rules = policy.get("rules")
    required_true = (
        "legal_basis_must_be_explicitly_verified",
        "legal_basis_must_not_be_inferred_from_consent",
        "purpose_limitation_required",
        "data_minimization_required",
        "access_control_required",
        "retention_policy_required",
        "incident_response_required",
        "data_subject_rights_channel_required",
        "sharing_or_transfer_review_required",
        "risk_assessment_required",
        "privacy_record_must_precede_genetic_processing",
        "gate_does_not_claim_legal_compliance",
    )
    if not isinstance(rules, dict):
        errors.append("Stage 9 privacy rules missing")
    else:
        for key in required_true:
            if rules.get(key) is not True:
                errors.append(f"Stage 9 protected rule must remain true: {key}")

    sources = policy.get("official_sources")
    authorities = {
        str(item.get("authority"))
        for item in sources
        if isinstance(sources, list) and isinstance(item, dict)
    } if isinstance(sources, list) else set()
    if "ANPD" not in authorities or "Presidência da República" not in authorities:
        errors.append("Stage 9 must retain official ANPD and LGPD primary sources")

    gate = (root / "scripts/genetic_data_privacy_gate.py").read_text(encoding="utf-8")
    if '"lgpd_compliance_claimed": False' not in gate:
        errors.append("privacy gate must not claim LGPD legal compliance")
    if '"legal_basis_inferred": False' not in gate:
        errors.append("privacy gate must state legal basis is not inferred")

    wgs = (root / "scripts/wgs_consent_gate.py").read_text(encoding="utf-8")
    if "evaluate_privacy" not in wgs or "ready_for_genetic_processing" not in wgs:
        errors.append("WGS pre-DNA gate is not bound to Stage 9 privacy clearance")

    array_runner = (root / "scripts/run_snp_array.py").read_text(encoding="utf-8")
    if '--privacy-record' not in array_runner or "load_and_evaluate" not in array_runner:
        errors.append("SNP-array runner is not bound to Stage 9 privacy clearance")

    main_workflow = (root / "main.nf").read_text(encoding="utf-8")
    if "privacy_record" not in main_workflow:
        errors.append("Nextflow entrypoint does not require a privacy record for array processing")

    array_flow = (root / "workflows/array.nf").read_text(encoding="utf-8")
    if "--privacy-record" not in array_flow:
        errors.append("array workflow does not pass privacy record to the processing gate")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    errors = collect_errors(ROOT)
    if errors:
        for error in errors:
            print(f"FAIL\t{error}")
        return 1
    print("PASS\tstage9_genetic_privacy\tfail_closed=true\tlegal_basis_inference=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
