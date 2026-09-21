#!/usr/bin/env python3
"""Fail-closed declared-use boundary for research, clinical and regulatory release."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY_REL = "config/use_boundary_policy.json"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_POLICY_VERSION = "1.2"
EXPECTED_POLICY_EFFECTIVE_DATE = "2026-09-20"
EVIDENCE_LEDGER_SCHEMA = "omnigenis-use-boundary-evidence-ledger-v1"
EVIDENCE_HMAC_ALGORITHM = "HMAC-SHA256"
EVIDENCE_HMAC_FIELD = "hmac_sha256"

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
    "authenticated_external_evidence_required",
    "evidence_reuse_requires_exact_binding",
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
    if policy.get("version") != EXPECTED_POLICY_VERSION:
        errors.append(
            f"use-boundary policy version must be {EXPECTED_POLICY_VERSION}"
        )
    if policy.get("effective_date") != EXPECTED_POLICY_EFFECTIVE_DATE:
        errors.append(
            "use-boundary policy effective_date must be "
            f"{EXPECTED_POLICY_EFFECTIVE_DATE}"
        )
    if policy.get("jurisdiction_profile") != "BRAZIL_ANVISA_SINEP":
        errors.append("use-boundary jurisdiction profile mismatch")

    use_classes = policy.get("use_classes")
    nonclinical = policy.get("nonclinical_use_classes")
    clinical = policy.get("clinical_use_classes")
    regulatory = policy.get("regulatory_use_classes")
    if (
        not isinstance(use_classes, list)
        or len(use_classes) != 7
        or len(set(use_classes)) != 7
    ):
        errors.append("use-boundary use-class vocabulary drift")
    class_partitions: list[list[Any]] = []
    for name, value in (
        ("nonclinical_use_classes", nonclinical),
        ("clinical_use_classes", clinical),
        ("regulatory_use_classes", regulatory),
    ):
        if not isinstance(value, list):
            errors.append(f"use-boundary {name} must be a list")
        else:
            class_partitions.append(value)
    if len(class_partitions) == 3:
        expected = set(use_classes) if isinstance(use_classes, list) else set()
        partitions = [set(value) for value in class_partitions]
        if set().union(*partitions) != expected:
            errors.append("use-boundary class partitions do not cover the vocabulary")
        if any(
            partitions[i] & partitions[j]
            for i in range(3)
            for j in range(i + 1, 3)
        ):
            errors.append("use-boundary class partitions must be disjoint")

    rules = policy.get("rules")
    if not isinstance(rules, dict):
        errors.append("use-boundary protected rules missing")
    else:
        for key in REQUIRED_RULES:
            if rules.get(key) is not True:
                errors.append(f"use-boundary protected rule must remain true: {key}")

    if policy.get("regulatory_decisions_authorizing_stated_use") != [
        "CLEARED_FOR_STATED_USE",
        "NOT_REGULATED_FOR_STATED_USE",
    ]:
        errors.append("use-boundary regulatory decision vocabulary drift")
    if policy.get("research_scope_decisions") != [
        "HUMAN_SUBJECTS_RESEARCH",
        "NOT_HUMAN_SUBJECTS_RESEARCH",
    ]:
        errors.append("use-boundary research-scope decision vocabulary drift")
    if policy.get("research_ethics_decisions_authorizing_research") != [
        "APPROVED_FOR_DECLARED_RESEARCH"
    ]:
        errors.append("use-boundary research-ethics decision vocabulary drift")
    if policy.get("clinical_validation_decisions") != ["VALIDATED_FOR_STATED_USE"]:
        errors.append("use-boundary clinical-validation decision vocabulary drift")
    if policy.get("professional_review_decisions") != [
        "PROFESSIONAL_REVIEW_COMPLETED"
    ]:
        errors.append("use-boundary professional-review decision vocabulary drift")

    auth = policy.get("evidence_authentication")
    if not isinstance(auth, dict):
        errors.append("use-boundary evidence_authentication config missing")
    else:
        if auth.get("ledger_schema") != EVIDENCE_LEDGER_SCHEMA:
            errors.append("use-boundary evidence ledger schema drift")
        if auth.get("algorithm") != EVIDENCE_HMAC_ALGORITHM:
            errors.append("use-boundary evidence authentication algorithm drift")
        if auth.get("key_id") != "stage10-evidence-v1":
            errors.append("use-boundary evidence key_id drift")
        if auth.get("secret_env") != "OMNIGENIS_STAGE10_EVIDENCE_HMAC_KEY":
            errors.append("use-boundary evidence secret environment drift")
        minimum = auth.get("minimum_secret_bytes")
        if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 32:
            errors.append("use-boundary evidence minimum secret length invalid")

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


def load_evidence_ledger(path: Path | str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UseBoundaryError(
            f"use-boundary evidence ledger could not be loaded: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise UseBoundaryError("use-boundary evidence ledger must be an object")
    return payload


def _evidence_key(
    policy: dict[str, Any],
    explicit_key: bytes | None,
) -> bytes:
    auth = policy["evidence_authentication"]
    minimum = int(auth["minimum_secret_bytes"])
    if explicit_key is not None:
        key = explicit_key
    else:
        value = os.environ.get(str(auth["secret_env"]), "")
        key = value.encode("utf-8")
    if len(key) < minimum:
        raise UseBoundaryError(
            f"authenticated evidence key must contain at least {minimum} bytes"
        )
    return key


def evidence_entry_hmac(entry: dict[str, Any], key: bytes) -> str:
    """Return the deterministic HMAC for one Stage 10 evidence-ledger entry."""
    unsigned = {name: value for name, value in entry.items() if name != EVIDENCE_HMAC_FIELD}
    raw = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(key, raw, hashlib.sha256).hexdigest()


def _authenticated_evidence_index(
    ledger: object,
    *,
    policy: dict[str, Any],
    evidence_key: bytes | None,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    errors: list[str] = []
    if not isinstance(ledger, dict):
        return {}, ["authenticated evidence ledger missing or invalid"]
    auth = policy["evidence_authentication"]
    if ledger.get("schema") != auth["ledger_schema"]:
        errors.append("authenticated evidence ledger schema mismatch")
    if ledger.get("key_id") != auth["key_id"]:
        errors.append("authenticated evidence ledger key_id mismatch")
    entries = ledger.get("entries")
    if not isinstance(entries, list):
        return {}, errors + ["authenticated evidence ledger entries missing"]
    try:
        key = _evidence_key(policy, evidence_key)
    except UseBoundaryError as exc:
        return {}, errors + [str(exc)]

    index: dict[str, dict[str, Any]] = {}
    for position, raw_entry in enumerate(entries):
        if not isinstance(raw_entry, dict):
            errors.append(f"authenticated evidence entry {position} must be an object")
            continue
        ref = _text(raw_entry.get("evidence_ref"))
        if not ref:
            errors.append(f"authenticated evidence entry {position} evidence_ref missing")
            continue
        if ref in index:
            errors.append(f"authenticated evidence_ref duplicated: {ref}")
            continue
        digest = _text(raw_entry.get("evidence_sha256"))
        signature = _text(raw_entry.get(EVIDENCE_HMAC_FIELD))
        if HEX64.fullmatch(digest) is None:
            errors.append(f"authenticated evidence digest invalid: {ref}")
            continue
        if HEX64.fullmatch(signature) is None:
            errors.append(f"authenticated evidence signature invalid: {ref}")
            continue
        expected_signature = evidence_entry_hmac(raw_entry, key)
        if not hmac.compare_digest(signature, expected_signature):
            errors.append(f"authenticated evidence signature mismatch: {ref}")
            continue
        index[ref] = raw_entry
    return index, errors


def _verified_evidence(
    block: object,
    label: str,
    errors: list[str],
    *,
    evidence_index: dict[str, dict[str, Any]],
    expected_case_id: str,
    expected_input_sha256: str,
    requested_operation: str,
    use_class: str,
    allowed_decisions: list[str],
) -> dict[str, Any]:
    if not isinstance(block, dict):
        errors.append(f"{label} missing")
        return {}
    if block.get("status") != "VERIFICADO":
        errors.append(f"{label}.status must be VERIFICADO")
    evidence_ref = _text(block.get("evidence_ref"))
    if not evidence_ref:
        errors.append(f"{label}.evidence_ref missing")
        return block
    decision = _text(block.get("decision"))
    if decision not in allowed_decisions:
        errors.append(f"{label}.decision is not authorized")
    evidence = evidence_index.get(evidence_ref)
    if evidence is None:
        errors.append(f"{label} authenticated evidence missing: {evidence_ref}")
        return block

    expected_bindings = (
        ("label", label),
        ("status", "VERIFICADO"),
        ("case_id", expected_case_id),
        ("input_sha256", expected_input_sha256),
        ("requested_operation", requested_operation),
        ("use_class", use_class),
        ("decision", decision),
    )
    for field, expected in expected_bindings:
        if evidence.get(field) != expected:
            errors.append(f"{label} authenticated evidence {field} binding mismatch")
    return block


def evaluate_use_boundary(
    record: dict[str, Any],
    *,
    requested_operation: str,
    expected_case_id: str,
    expected_input_sha256: str,
    policy: dict[str, Any] | None = None,
    evidence_ledger: dict[str, Any] | None = None,
    evidence_key: bytes | None = None,
) -> dict[str, Any]:
    """Verify an external declared-use decision without making the decision itself."""
    if not isinstance(record, dict):
        raise UseBoundaryError("use-boundary record must be an object")
    if policy is None:
        policy = load_policy()
    policy_errors = validate_policy_contract(policy)
    if policy_errors:
        raise UseBoundaryError("; ".join(policy_errors))

    evidence_index, evidence_errors = _authenticated_evidence_index(
        evidence_ledger,
        policy=policy,
        evidence_key=evidence_key,
    )
    errors: list[str] = list(evidence_errors)

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
                evidence_index=evidence_index,
                expected_case_id=expected_case_id,
                expected_input_sha256=expected_input_sha256,
                requested_operation=requested_operation,
                use_class=use_class,
                allowed_decisions=policy["research_scope_decisions"],
            )
            scope_decision = scope_assessment.get("decision")
            if involves_humans is True and scope_decision != "HUMAN_SUBJECTS_RESEARCH":
                errors.append(
                    "research scope assessment conflicts with human-subjects declaration"
                )
            if (
                involves_humans is False
                and scope_decision != "NOT_HUMAN_SUBJECTS_RESEARCH"
            ):
                errors.append(
                    "research scope assessment conflicts with non-human-subjects declaration"
                )
            if involves_humans is True:
                _verified_evidence(
                    record.get("research_ethics_assessment"),
                    "research_ethics_assessment",
                    errors,
                    evidence_index=evidence_index,
                    expected_case_id=expected_case_id,
                    expected_input_sha256=expected_input_sha256,
                    requested_operation=requested_operation,
                    use_class=use_class,
                    allowed_decisions=policy[
                        "research_ethics_decisions_authorizing_research"
                    ],
                )

    if use_class in clinical:
        if record.get("clinical_use_authorized") is not True:
            errors.append("clinical release must explicitly authorize clinical use")
        _verified_evidence(
            record.get("clinical_validation"),
            "clinical_validation",
            errors,
            evidence_index=evidence_index,
            expected_case_id=expected_case_id,
            expected_input_sha256=expected_input_sha256,
            requested_operation=requested_operation,
            use_class=use_class,
            allowed_decisions=policy["clinical_validation_decisions"],
        )
        review = _verified_evidence(
            record.get("professional_review"),
            "professional_review",
            errors,
            evidence_index=evidence_index,
            expected_case_id=expected_case_id,
            expected_input_sha256=expected_input_sha256,
            requested_operation=requested_operation,
            use_class=use_class,
            allowed_decisions=policy["professional_review_decisions"],
        )
        if not _text(review.get("responsible_professional_ref")):
            errors.append("professional_review.responsible_professional_ref missing")

    if use_class in clinical or use_class in regulatory:
        _verified_evidence(
            record.get("regulatory_assessment"),
            "regulatory_assessment",
            errors,
            evidence_index=evidence_index,
            expected_case_id=expected_case_id,
            expected_input_sha256=expected_input_sha256,
            requested_operation=requested_operation,
            use_class=use_class,
            allowed_decisions=policy[
                "regulatory_decisions_authorizing_stated_use"
            ],
        )

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
        "policy_version": EXPECTED_POLICY_VERSION,
        "policy_effective_date": EXPECTED_POLICY_EFFECTIVE_DATE,
        "regulatory_classification_determined_by_software": False,
        "clinical_validity_determined_by_software": False,
        "research_ethics_determined_by_software": False,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", required=True)
    parser.add_argument("--evidence-ledger", required=True)
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
            evidence_ledger=load_evidence_ledger(args.evidence_ledger),
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
