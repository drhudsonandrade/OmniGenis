#!/usr/bin/env python3
"""Fail-closed declared-use boundary for research, clinical and regulatory release."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY_REL = "config/use_boundary_policy.json"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_RULES = (
    "explicit_use_class_required",
    "software_must_not_self_classify_regulatory_status",
    "software_must_not_self_determine_research_ethics",
    "nonclinical_outputs_must_disclaim_clinical_and_regulatory_authorization",
    "human_subjects_research_requires_verified_ethics_assessment",
    "research_scope_requires_verified_external_assessment",
    "clinical_release_requires_verified_clinical_validation",
    "clinical_release_requires_verified_professional_review",
    "clinical_release_requires_verified_regulatory_assessment",
    "regulatory_release_requires_verified_regulatory_assessment",
    "intended_use_reference_required",
    "case_and_input_binding_required",
    "purpose_change_requires_new_evaluation",
)

class UseBoundaryError(ValueError):
    """The declared-use record cannot authorize the requested release."""


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def validate_policy_contract(policy: object) -> list[str]:
    """Validate the canonical Stage 10 policy independently of a release record."""
    if not isinstance(policy, dict):
        return ["use-boundary policy must be an object"]
    errors: list[str] = []
    if policy.get("schema") != "omnigenis-use-boundary-policy-v1":
        errors.append("use-boundary policy schema mismatch")
    if policy.get("status") != "ACTIVE":
        errors.append("use-boundary policy status must be ACTIVE")
    if policy.get("jurisdiction_profile") != "BRAZIL_ANVISA_SINEP":
        errors.append("use-boundary jurisdiction profile mismatch")
    use_classes = policy.get("use_classes")
    nonclinical = policy.get("nonclinical_use_classes")
    clinical = policy.get("clinical_use_classes")
    regulatory = policy.get("regulatory_use_classes")
    if not isinstance(use_classes, list) or len(use_classes) != 7 or len(set(use_classes)) != 7:
        errors.append("use-boundary use-class vocabulary drift")
    if not all(isinstance(value, list) for value in (nonclinical, clinical, regulatory)):
        errors.append("use-boundary class partitions must be lists")
    else:
        expected = set(use_classes) if isinstance(use_classes, list) else set()
        partitions = [set(nonclinical), set(clinical), set(regulatory)]
        if set().union(*partitions) != expected:
            errors.append("use-boundary class partitions do not cover the vocabulary")
        if any(partitions[i] & partitions[j] for i in range(3) for j in range(i + 1, 3)):
            errors.append("use-boundary class partitions must be disjoint")
    rules = policy.get("rules")
    if not isinstance(rules, dict):
        errors.append("use-boundary protected rules missing")
    else:
        for key in REQUIRED_RULES:
            if rules.get(key) is not True:
                errors.append(f"use-boundary protected rule must remain true: {key}")
    regulatory_decisions = policy.get("regulatory_decisions_authorizing_stated_use")
    if regulatory_decisions != ["CLEARED_FOR_STATED_USE", "NOT_REGULATED_FOR_STATED_USE"]:
        errors.append("use-boundary regulatory decision vocabulary drift")
    scope_decisions = policy.get("research_scope_decisions")
    if scope_decisions != ["HUMAN_SUBJECTS_RESEARCH", "NOT_HUMAN_SUBJECTS_RESEARCH"]:
        errors.append("use-boundary research-scope decision vocabulary drift")
    ethics_decisions = policy.get("research_ethics_decisions_authorizing_research")
    if ethics_decisions != ["APPROVED_FOR_DECLARED_RESEARCH"]:
        errors.append("use-boundary research-ethics decision vocabulary drift")
    sources = policy.get("official_sources")
    if not isinstance(sources, list) or len(sources) < 4:
        errors.append("use-boundary official-source provenance incomplete")
    return errors


def load_policy(root: Path = ROOT) -> dict[str, Any]:
    try:
        payload = json.loads((root / POLICY_REL).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UseBoundaryError(f"use-boundary policy could not be loaded: {exc}") from exc
    errors = validate_policy_contract(payload)
    if errors:
        raise UseBoundaryError("; ".join(errors))
    return payload


def load_record(path: Path | str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UseBoundaryError(f"use-boundary record could not be loaded: {exc}") from exc
    if not isinstance(payload, dict):
        raise UseBoundaryError("use-boundary record must be an object")
    return payload


def _verified_evidence(block: object, label: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(block, dict):
        errors.append(f"{label} missing")
        return {}
    if block.get("status") != "VERIFICADO":
        errors.append(f"{label}.status must be VERIFICADO")
    if not _text(block.get("evidence_ref")):
        errors.append(f"{label}.evidence_ref missing")
    return block


def evaluate_use_boundary(
    record: dict[str, Any],
    *,
    requested_operation: str,
    expected_case_id: str,
    expected_input_sha256: str,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify an external declared-use decision without making the decision itself."""
    if not isinstance(record, dict):
        raise UseBoundaryError("use-boundary record must be an object")
    if policy is None:
        policy = load_policy()
    policy_errors = validate_policy_contract(policy)
    if policy_errors:
        raise UseBoundaryError("; ".join(policy_errors))
    errors: list[str] = []
    if record.get("schema") != "omnigenis-use-boundary-record-v1":
        errors.append("use-boundary record schema mismatch")
    if record.get("status") != "VERIFICADO":
        errors.append("use-boundary record status must be VERIFICADO")
    record_id = _text(record.get("record_id"))
    if not record_id:
        errors.append("record_id missing")
    use_class = _text(record.get("use_class"))
    use_classes = policy["use_classes"]
    if use_class not in use_classes:
        errors.append("use_class is missing or unsupported")
    if _text(record.get("requested_operation")) != requested_operation:
        errors.append("requested_operation does not match this release")
    if not _text(record.get("intended_use_ref")):
        errors.append("intended_use_ref missing")

    case_id = _text(record.get("case_id"))
    input_sha256 = _text(record.get("input_sha256"))
    if not case_id or case_id != expected_case_id:
        errors.append("case_id does not match this release")
    if HEX64.fullmatch(input_sha256) is None or input_sha256 != expected_input_sha256:
        errors.append("input_sha256 does not match this release")

    nonclinical = set(policy["nonclinical_use_classes"])
    clinical = set(policy["clinical_use_classes"])
    regulatory = set(policy["regulatory_use_classes"])

    if use_class in nonclinical:
        if record.get("clinical_use_authorized") is not False:
            errors.append("nonclinical release must explicitly deny clinical authorization")
        if record.get("regulatory_use_authorized") is not False:
            errors.append("nonclinical release must explicitly deny regulatory authorization")
        if not _text(record.get("nonclinical_label_ref")):
            errors.append("nonclinical_label_ref missing")
    if use_class == "RESEARCH_ONLY":
        scope = record.get("research_scope")
        if not isinstance(scope, dict):
            errors.append("research_scope missing")
        else:
            involves_humans = scope.get("involves_human_subjects")
            if not isinstance(involves_humans, bool):
                errors.append("research_scope.involves_human_subjects must be boolean")
            scope_assessment = _verified_evidence(
                scope.get("assessment"),
                "research_scope.assessment",
                errors,
            )
            scope_decision = scope_assessment.get("decision")
            if scope_decision not in policy["research_scope_decisions"]:
                errors.append("research scope assessment decision is unsupported")
            if involves_humans is True and scope_decision != "HUMAN_SUBJECTS_RESEARCH":
                errors.append("research scope assessment conflicts with human-subjects declaration")
            if involves_humans is False and scope_decision != "NOT_HUMAN_SUBJECTS_RESEARCH":
                errors.append("research scope assessment conflicts with non-human-subjects declaration")
            if involves_humans is True:
                ethics = _verified_evidence(
                    record.get("research_ethics_assessment"),
                    "research_ethics_assessment",
                    errors,
                )
                allowed_ethics = policy["research_ethics_decisions_authorizing_research"]
                if ethics.get("decision") not in allowed_ethics:
                    errors.append("research ethics decision does not authorize declared research")

    if use_class in clinical:
        if record.get("clinical_use_authorized") is not True:
            errors.append("clinical release must explicitly authorize clinical use")
        _verified_evidence(record.get("clinical_validation"), "clinical_validation", errors)
        review = record.get("professional_review")
        if not isinstance(review, dict):
            errors.append("professional_review missing")
        else:
            if review.get("status") != "VERIFICADO":
                errors.append("professional_review.status must be VERIFICADO")
            if not _text(review.get("responsible_professional_ref")):
                errors.append("professional_review.responsible_professional_ref missing")

    if use_class in clinical or use_class in regulatory:
        assessment = _verified_evidence(
            record.get("regulatory_assessment"),
            "regulatory_assessment",
            errors,
        )
        if assessment.get("decision") not in policy["regulatory_decisions_authorizing_stated_use"]:
            errors.append("regulatory assessment decision does not authorize stated use")

    if use_class in regulatory and record.get("regulatory_use_authorized") is not True:
        errors.append("regulatory evidence release must explicitly authorize regulatory use")

    ready = not errors
    return {
        "schema": "omnigenis-use-boundary-gate-v1",
        "gate": "RESEARCH_CLINICAL_REGULATORY_BOUNDARY_GATE",
        "status": "VERIFICADO" if ready else "NÃO DISPONÍVEL",
        "ready_for_requested_release": ready,
        "record_id": record_id or None,
        "use_class": use_class or None,
        "requested_operation": requested_operation,
        "case_id": expected_case_id,
        "input_sha256": expected_input_sha256,
        "policy_version": policy.get("version"),
        "policy_effective_date": policy.get("effective_date"),
        "regulatory_classification_determined_by_software": False,
        "clinical_validity_determined_by_software": False,
        "research_ethics_determined_by_software": False,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", required=True)
    parser.add_argument("--operation", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--input-sha256", required=True)
    args = parser.parse_args()
    try:
        result = evaluate_use_boundary(
            load_record(args.record),
            requested_operation=args.operation,
            expected_case_id=args.case_id,
            expected_input_sha256=args.input_sha256,
        )
    except UseBoundaryError as exc:
        result = {
            "schema": "omnigenis-use-boundary-gate-v1",
            "gate": "RESEARCH_CLINICAL_REGULATORY_BOUNDARY_GATE",
            "status": "NÃO DISPONÍVEL",
            "ready_for_requested_release": False,
            "errors": [str(exc)],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ready_for_requested_release"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
