from __future__ import annotations

import copy
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.use_boundary_gate import (
    UseBoundaryError,
    evaluate_use_boundary,
    load_policy,
)
from scripts.validate_stage10_use_boundary import collect_errors

ROOT = Path(__file__).resolve().parents[1]
INPUT_SHA = "a" * 64


class Stage10UseBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy()
        self.research = {
            "schema": "omnigenis-use-boundary-record-v1",
            "status": "VERIFICADO",
            "record_id": "USE-RESEARCH-1",
            "case_id": "CASE-1",
            "input_sha256": INPUT_SHA,
            "use_class": "RESEARCH_ONLY",
            "requested_operation": "FINAL_AUDITED_REPORT",
            "intended_use_ref": "protocol:research-only",
            "clinical_use_authorized": False,
            "regulatory_use_authorized": False,
            "nonclinical_label_ref": "label:not-for-clinical-use",
            "research_scope": {
                "involves_human_subjects": False,
                "assessment": {
                    "status": "VERIFICADO",
                    "decision": "NOT_HUMAN_SUBJECTS_RESEARCH",
                    "evidence_ref": "assessment:not-human-subjects-research",
                },
            },
        }
        self.clinical = {
            "schema": "omnigenis-use-boundary-record-v1",
            "status": "VERIFICADO",
            "record_id": "USE-CLINICAL-1",
            "case_id": "CASE-1",
            "input_sha256": INPUT_SHA,
            "use_class": "CLINICAL_DECISION_SUPPORT",
            "requested_operation": "FINAL_AUDITED_REPORT",
            "intended_use_ref": "intended-use:clinical-ds",
            "clinical_use_authorized": True,
            "regulatory_use_authorized": False,
            "clinical_validation": {
                "status": "VERIFICADO",
                "evidence_ref": "validation:clinical-1",
            },
            "professional_review": {
                "status": "VERIFICADO",
                "responsible_professional_ref": "professional:responsible-1",
            },
            "regulatory_assessment": {
                "status": "VERIFICADO",
                "decision": "NOT_REGULATED_FOR_STATED_USE",
                "evidence_ref": "regulatory-assessment:1",
            },
        }

    def evaluate(self, record: dict) -> dict:
        return evaluate_use_boundary(
            record,
            requested_operation="FINAL_AUDITED_REPORT",
            expected_case_id="CASE-1",
            expected_input_sha256=INPUT_SHA,
            policy=self.policy,
        )

    def test_research_release_requires_explicit_nonclinical_boundary(self) -> None:
        result = self.evaluate(self.research)
        self.assertTrue(result["ready_for_requested_release"])
        self.assertFalse(result["regulatory_classification_determined_by_software"])
        self.assertFalse(result["research_ethics_determined_by_software"])

    def test_human_subjects_research_requires_verified_external_ethics(self) -> None:
        record = copy.deepcopy(self.research)
        record["research_scope"]["involves_human_subjects"] = True
        record["research_scope"]["assessment"]["decision"] = "HUMAN_SUBJECTS_RESEARCH"
        result = self.evaluate(record)
        self.assertFalse(result["ready_for_requested_release"])
        self.assertIn("research_ethics_assessment missing", result["errors"])

    def test_human_subjects_research_passes_with_verified_external_approval(self) -> None:
        record = copy.deepcopy(self.research)
        record["research_scope"]["involves_human_subjects"] = True
        record["research_scope"]["assessment"]["decision"] = "HUMAN_SUBJECTS_RESEARCH"
        record["research_ethics_assessment"] = {
            "status": "VERIFICADO",
            "decision": "APPROVED_FOR_DECLARED_RESEARCH",
            "evidence_ref": "ethics:external-approval-1",
        }
        result = self.evaluate(record)
        self.assertTrue(result["ready_for_requested_release"])

    def test_research_cannot_claim_clinical_authorization(self) -> None:
        record = copy.deepcopy(self.research)
        record["clinical_use_authorized"] = True
        self.assertFalse(self.evaluate(record)["ready_for_requested_release"])

    def test_clinical_release_requires_validation(self) -> None:
        record = copy.deepcopy(self.clinical)
        record["clinical_validation"]["status"] = "PROPOSTO"
        result = self.evaluate(record)
        self.assertFalse(result["ready_for_requested_release"])
        self.assertTrue(any("clinical_validation.status" in item for item in result["errors"]))

    def test_clinical_release_requires_professional_review(self) -> None:
        record = copy.deepcopy(self.clinical)
        record["professional_review"]["status"] = "PROPOSTO"
        result = self.evaluate(record)
        self.assertFalse(result["ready_for_requested_release"])

    def test_clinical_release_requires_authorizing_regulatory_assessment(self) -> None:
        record = copy.deepcopy(self.clinical)
        record["regulatory_assessment"]["decision"] = "REVIEW_REQUIRED"
        self.assertFalse(self.evaluate(record)["ready_for_requested_release"])

    def test_case_binding_mismatch_fails_closed(self) -> None:
        record = copy.deepcopy(self.research)
        record["case_id"] = "CASE-OTHER"
        self.assertFalse(self.evaluate(record)["ready_for_requested_release"])

    def test_input_hash_binding_mismatch_fails_closed(self) -> None:
        record = copy.deepcopy(self.research)
        record["input_sha256"] = "b" * 64
        self.assertFalse(self.evaluate(record)["ready_for_requested_release"])

    def test_operation_mismatch_fails_closed(self) -> None:
        record = copy.deepcopy(self.research)
        result = evaluate_use_boundary(
            record,
            requested_operation="REGULATORY_SUBMISSION",
            expected_case_id="CASE-1",
            expected_input_sha256=INPUT_SHA,
            policy=self.policy,
        )
        self.assertFalse(result["ready_for_requested_release"])

    def test_invalid_policy_cannot_authorize_release(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["status"] = "DISABLED"
        with self.assertRaises(UseBoundaryError):
            evaluate_use_boundary(
                self.research,
                requested_operation="FINAL_AUDITED_REPORT",
                expected_case_id="CASE-1",
                expected_input_sha256=INPUT_SHA,
                policy=policy,
            )


class Stage10ValidatorMutationTests(unittest.TestCase):
    @staticmethod
    def _fixture(root: Path) -> None:
        for rel in (
            "config/use_boundary_policy.json",
            "scripts/use_boundary_gate.py",
            "scripts/prepare_report_release.py",
            "docs/compliance/STAGE10_RESEARCH_CLINICAL_REGULATORY_BOUNDARY.md",
            "tests/test_stage10_use_boundary.py",
            ".github/workflows/genoma-ngs-runtime-gate.yml",
            ".github/workflows/scaffold-validation.yml",
        ):
            source = ROOT / rel
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def test_validator_rejects_gate_without_explicit_returned_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._fixture(root)
            gate = root / "scripts/use_boundary_gate.py"
            text = gate.read_text(encoding="utf-8")
            text = text.replace(
                "    ready = not errors\n    return {",
                "    ready = not errors\n    decision = {",
                1,
            )
            gate.write_text(text, encoding="utf-8")
            errors = collect_errors(root)
            self.assertIn(
                "Stage 10 gate must return an explicit auditable decision",
                errors,
            )

    def test_validator_rejects_second_operational_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._fixture(root)
            duplicate = root / "config/research_clinical_regulatory_policy.json"
            duplicate.write_text("{}\n", encoding="utf-8")
            errors = collect_errors(root)
            self.assertIn(
                "Stage 10 must have only one operational policy source",
                errors,
            )


if __name__ == "__main__":
    unittest.main()
