#!/usr/bin/env python3
"""Fail-closed privacy gate for genetic-data processing."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "genetic_data_privacy_policy.json"
RECORD_SCHEMA = "omnigenis-genetic-data-privacy-record-v1"


class GeneticPrivacyError(ValueError):
    """The privacy record cannot authorize this processing context."""


def load_policy(root: Path = ROOT) -> dict[str, Any]:
    payload = json.loads((root / "config" / "genetic_data_privacy_policy.json").read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise GeneticPrivacyError("privacy policy must be a JSON object")
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
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GeneticPrivacyError(f"privacy record unreadable: {exc}") from exc
    return evaluate_privacy(
        payload,
        requested_purpose=requested_purpose,
        case_id=case_id,
        input_sha256=input_sha256,
    )


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
