#!/usr/bin/env python3
"""Fail-closed privacy gate for genetic-data processing."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "genetic_data_privacy_policy.json"
RECORD_SCHEMA = "omnigenis-genetic-data-privacy-record-v1"
POLICY_SCHEMA = "omnigenis-genetic-data-privacy-policy-v1"
POLICY_STATUS = "ACTIVE"
POLICY_JURISDICTION = "BR"
REQUIRED_DATA_CLASSES = {
    "GENETIC_SENSITIVE_PERSONAL_DATA",
    "VERIFIED_ANONYMIZED_GENETIC_DATA",
    "SYNTHETIC_NON_PERSONAL_GENETIC_FIXTURE",
}
REQUIRED_POLICY_RULES = (
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
    "synthetic_fixture_requires_explicit_verification",
    "synthetic_fixture_separate_from_personal_data_legal_basis",
    "synthetic_fixture_requires_synthetic_case_identity",
    "synthetic_fixture_requires_no_natural_person",
    "synthetic_fixture_requires_no_personal_data",
    "verified_anonymized_data_requires_verified_determination",
)


class GeneticPrivacyError(ValueError):
    """The privacy record cannot authorize this processing context."""


def validate_policy_contract(policy: Any) -> list[str]:
    """Return Stage 9 policy-contract violations shared by runtime and repository validation."""
    if not isinstance(policy, dict):
        return ["privacy policy must be a JSON object"]

    errors: list[str] = []
    if policy.get("schema") != POLICY_SCHEMA:
        errors.append(f"privacy policy schema must be {POLICY_SCHEMA}")
    if policy.get("status") != POLICY_STATUS:
        errors.append("privacy policy status must be ACTIVE")
    if policy.get("jurisdiction") != POLICY_JURISDICTION:
        errors.append("privacy policy jurisdiction must be BR")
    if policy.get("required_status") != "VERIFICADO":
        errors.append("privacy policy required_status must be VERIFICADO")
    if policy.get("synthetic_fixture_class") != "SYNTHETIC_NON_PERSONAL_GENETIC_FIXTURE":
        errors.append("privacy policy synthetic fixture class drift")

    allowed = policy.get("allowed_data_classes")
    if not isinstance(allowed, list) or not REQUIRED_DATA_CLASSES.issubset(set(allowed)):
        errors.append("privacy policy allowed_data_classes is incomplete")

    classification = policy.get("classification")
    if not isinstance(classification, dict):
        errors.append("privacy policy classification missing")
    else:
        if classification.get("linked_genetic_data") != "GENETIC_SENSITIVE_PERSONAL_DATA":
            errors.append("linked genetic data classification drift")
        if classification.get("default_sensitive") is not True:
            errors.append("linked genetic data must default to sensitive")
        if classification.get("anonymous_upgrade_is_automatic") is not False:
            errors.append("anonymization may not be inferred automatically")

    rules = policy.get("rules")
    if not isinstance(rules, dict):
        errors.append("privacy policy rules missing")
    else:
        for key in REQUIRED_POLICY_RULES:
            if rules.get(key) is not True:
                errors.append(f"privacy policy protected rule must remain true: {key}")

    sources = policy.get("official_sources")
    authorities = (
        {str(item.get("authority")) for item in sources if isinstance(item, dict)}
        if isinstance(sources, list)
        else set()
    )
    if "ANPD" not in authorities or "Presidência da República" not in authorities:
        errors.append("privacy policy must retain official ANPD and LGPD primary sources")
    return errors


def load_policy(root: Path = ROOT) -> dict[str, Any]:
    payload = json.loads(
        (root / "config" / "genetic_data_privacy_policy.json").read_text(encoding="utf-8")
    )
    errors = validate_policy_contract(payload)
    if errors:
        raise GeneticPrivacyError("; ".join(errors))
    assert isinstance(payload, dict)
    return payload


def _verified_block(record: dict[str, Any], field: str, errors: list[str]) -> dict[str, Any]:
    value = record.get(field)
    if not isinstance(value, dict):
        errors.append(f"{field} must be an object")
        return {}
    if value.get("status") != "VERIFICADO":
        errors.append(f"{field}.status must be VERIFICADO")
    return value


def _synthetic_fixture_result(
    record: dict[str, Any],
    *,
    requested_purpose: str,
    case_id: str | None,
    input_sha256: str | None,
    errors: list[str],
) -> dict[str, Any]:
    """Evaluate a non-personal synthetic CI fixture without inventing an LGPD basis."""
    if requested_purpose != "genomic_analysis":
        errors.append("synthetic fixture is limited to genomic_analysis")

    record_case_id = str(record.get("case_id") or "").strip()
    if not record_case_id.startswith("SYNTHETIC-"):
        errors.append("synthetic fixture case_id must start with SYNTHETIC-")
    if case_id is not None and record_case_id != str(case_id):
        errors.append("privacy record does not bind to the requested case_id")

    record_input_sha = str(record.get("input_sha256") or "").strip().lower()
    if input_sha256 is not None and record_input_sha != str(input_sha256).lower():
        errors.append("privacy record does not bind to the requested input SHA-256")

    if record.get("subject_reference") != "NO_NATURAL_PERSON":
        errors.append("synthetic fixture subject_reference must be NO_NATURAL_PERSON")
    if "legal_basis" in record:
        errors.append("synthetic fixture must not claim a personal-data legal basis")

    fixture = _verified_block(record, "synthetic_fixture", errors)
    if fixture.get("contains_personal_data") is not False:
        errors.append("synthetic fixture must declare contains_personal_data=false")
    if fixture.get("generated_for") != "CI_CANARY":
        errors.append("synthetic_fixture.generated_for must be CI_CANARY")
    if not str(fixture.get("generator") or "").strip():
        errors.append("synthetic_fixture.generator missing")
    if not str(fixture.get("evidence_ref") or "").strip():
        errors.append("synthetic_fixture.evidence_ref missing")

    return {
        "schema": "omnigenis-genetic-data-privacy-gate-v1",
        "gate": "GENETIC_DATA_PRIVACY_GATE",
        "status": "VERIFICADO" if not errors else "NÃO DISPONÍVEL",
        "ready_for_genetic_processing": not errors,
        "requested_purpose": requested_purpose,
        "processing_context_id": str(record.get("processing_context_id") or "").strip() or None,
        "data_class": record.get("data_class"),
        "sensitive_personal_data": False,
        "synthetic_non_personal_fixture": True,
        "legal_basis_reference": None,
        "legal_basis_inferred": False,
        "lgpd_compliance_claimed": False,
        "errors": errors,
    }


def evaluate_privacy(
    record: Any,
    *,
    requested_purpose: str,
    case_id: str | None = None,
    input_sha256: str | None = None,
    root: Path = ROOT,
) -> dict[str, Any]:
    """Evaluate an operator-supplied privacy record without inventing legal clearance."""
    errors: list[str] = []
    try:
        policy = load_policy(root)
    except (OSError, json.JSONDecodeError, GeneticPrivacyError) as exc:
        policy = {}
        errors.append(f"privacy policy unavailable: {exc}")

    if not isinstance(record, dict):
        record = {}
        errors.append("privacy record must be an object")

    if record.get("schema") != RECORD_SCHEMA:
        errors.append(f"privacy record schema must be {RECORD_SCHEMA}")
    if record.get("status") != "VERIFICADO":
        errors.append("privacy status must be VERIFICADO")

    data_class = record.get("data_class")
    synthetic_class = policy.get("synthetic_fixture_class")
    is_synthetic_fixture = (
        isinstance(synthetic_class, str)
        and data_class == synthetic_class
    )
    allowed_classes = policy.get("allowed_data_classes")
    if not is_synthetic_fixture and (
        not isinstance(allowed_classes, list) or data_class not in allowed_classes
    ):
        errors.append("privacy data_class is not allowed by policy")

    context_id = str(record.get("processing_context_id") or "").strip()
    subject_ref = str(record.get("subject_reference") or "").strip()
    if not context_id:
        errors.append("processing_context_id missing")
    if not subject_ref:
        errors.append("subject_reference missing")

    purposes = record.get("authorized_purposes")
    if not isinstance(purposes, list) or requested_purpose not in purposes:
        errors.append(f"requested privacy purpose not authorized: {requested_purpose}")

    if is_synthetic_fixture:
        return _synthetic_fixture_result(
            record,
            requested_purpose=requested_purpose,
            case_id=case_id,
            input_sha256=input_sha256,
            errors=errors,
        )

    record_case_id = str(record.get("case_id") or "").strip()
    if case_id is not None and record_case_id != str(case_id):
        errors.append("privacy record does not bind to the requested case_id")

    record_input_sha = str(record.get("input_sha256") or "").strip().lower()
    if input_sha256 is not None and record_input_sha != str(input_sha256).lower():
        errors.append("privacy record does not bind to the requested input SHA-256")

    if data_class == "VERIFIED_ANONYMIZED_GENETIC_DATA":
        anonymization = _verified_block(record, "anonymization_determination", errors)
        if not str(anonymization.get("evidence_ref") or "").strip():
            errors.append("anonymization_determination.evidence_ref missing")

    legal_basis = _verified_block(record, "legal_basis", errors)
    if not str(legal_basis.get("reference") or "").strip():
        errors.append("legal_basis.reference missing")
    if not str(legal_basis.get("evidence_ref") or "").strip():
        errors.append("legal_basis.evidence_ref missing")
    if legal_basis.get("inferred_from_consent") is not False:
        errors.append("legal basis must not be inferred from consent")

    controller = _verified_block(record, "controller", errors)
    if not str(controller.get("reference") or "").strip():
        errors.append("controller.reference missing")

    for field in (
        "purpose_limitation",
        "data_minimization",
        "access_control",
        "retention",
        "incident_response",
        "data_subject_rights",
        "sharing_transfer_review",
        "risk_assessment",
    ):
        block = _verified_block(record, field, errors)
        if not str(block.get("evidence_ref") or "").strip():
            errors.append(f"{field}.evidence_ref missing")

    return {
        "schema": "omnigenis-genetic-data-privacy-gate-v1",
        "gate": "GENETIC_DATA_PRIVACY_GATE",
        "status": "VERIFICADO" if not errors else "NÃO DISPONÍVEL",
        "ready_for_genetic_processing": not errors,
        "requested_purpose": requested_purpose,
        "processing_context_id": context_id or None,
        "data_class": record.get("data_class"),
        "sensitive_personal_data": record.get("data_class") == "GENETIC_SENSITIVE_PERSONAL_DATA",
        "synthetic_non_personal_fixture": False,
        "legal_basis_reference": legal_basis.get("reference"),
        "legal_basis_inferred": False,
        "lgpd_compliance_claimed": False,
        "errors": errors,
    }


def load_and_evaluate(
    path: Path | str,
    *,
    requested_purpose: str,
    case_id: str | None = None,
    input_sha256: str | None = None,
) -> dict[str, Any]:
    try:
        raw = Path(path).read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GeneticPrivacyError(f"privacy record unreadable: {exc}") from exc
    result = evaluate_privacy(
        payload,
        requested_purpose=requested_purpose,
        case_id=case_id,
        input_sha256=input_sha256,
    )
    result["privacy_record_sha256"] = hashlib.sha256(raw).hexdigest()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", required=True)
    parser.add_argument("--purpose", required=True)
    parser.add_argument("--case-id")
    parser.add_argument("--input-sha256")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = load_and_evaluate(
        args.record,
        requested_purpose=args.purpose,
        case_id=args.case_id,
        input_sha256=args.input_sha256,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ready_for_genetic_processing"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
