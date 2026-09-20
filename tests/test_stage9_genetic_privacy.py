from __future__ import annotations

import copy
import unittest

from scripts.genetic_data_privacy_gate import evaluate_privacy


class Stage9GeneticPrivacyTests(unittest.TestCase):
    @staticmethod
    def _record() -> dict[str, object]:
        verified = lambda ref: {"status": "VERIFICADO", "evidence_ref": ref}
        return {
            "schema": "omnigenis-genetic-data-privacy-record-v1",
            "status": "VERIFICADO",
            "processing_context_id": "CTX-1",
            "data_class": "GENETIC_SENSITIVE_PERSONAL_DATA",
            "subject_reference": "SUBJECT-1",
            "case_id": "CASE-1",
            "input_sha256": "a" * 64,
            "authorized_purposes": ["genomic_analysis", "clinical_report"],
            "legal_basis": {
                "status": "VERIFICADO",
                "reference": "LGPD-ART11-OPERATOR-REVIEW-1",
                "evidence_ref": "legal-review-1",
                "inferred_from_consent": False,
            },
            "controller": {
                "status": "VERIFICADO",
                "reference": "controller-register-1",
            },
            "purpose_limitation": verified("purpose-record-1"),
            "data_minimization": verified("minimization-record-1"),
            "access_control": verified("access-policy-1"),
            "retention": verified("retention-policy-1"),
            "incident_response": verified("incident-plan-1"),
            "data_subject_rights": verified("rights-channel-1"),
            "sharing_transfer_review": verified("sharing-review-1"),
            "risk_assessment": verified("risk-assessment-1"),
        }

    @staticmethod
    def _synthetic_record() -> dict[str, object]:
        return {
            "schema": "omnigenis-genetic-data-privacy-record-v1",
            "status": "VERIFICADO",
            "processing_context_id": "CI-ARRAY-SYNTHETIC",
            "data_class": "SYNTHETIC_NON_PERSONAL_GENETIC_FIXTURE",
            "subject_reference": "NO_NATURAL_PERSON",
            "case_id": "SYNTHETIC-CASE-1",
            "input_sha256": "c" * 64,
            "authorized_purposes": ["genomic_analysis"],
            "synthetic_fixture": {
                "status": "VERIFICADO",
                "contains_personal_data": False,
                "generated_for": "CI_CANARY",
                "generator": "tests.test_stage9_genetic_privacy",
                "evidence_ref": "unit-test:deterministic-synthetic-fixture",
            },
        }

    def test_complete_sensitive_genetic_record_passes(self) -> None:
        result = evaluate_privacy(
            self._record(),
            requested_purpose="genomic_analysis",
            case_id="CASE-1",
            input_sha256="a" * 64,
        )
        self.assertTrue(result["ready_for_genetic_processing"])
        self.assertEqual(result["status"], "VERIFICADO")
        self.assertTrue(result["sensitive_personal_data"])
        self.assertFalse(result["legal_basis_inferred"])
        self.assertFalse(result["lgpd_compliance_claimed"])

    def test_verified_synthetic_fixture_passes_without_legal_basis(self) -> None:
        result = evaluate_privacy(
            self._synthetic_record(),
            requested_purpose="genomic_analysis",
            case_id="SYNTHETIC-CASE-1",
            input_sha256="c" * 64,
        )
        self.assertTrue(result["ready_for_genetic_processing"])
        self.assertFalse(result["sensitive_personal_data"])
        self.assertTrue(result["synthetic_non_personal_fixture"])
        self.assertIsNone(result["legal_basis_reference"])
        self.assertFalse(result["lgpd_compliance_claimed"])

    def test_synthetic_fixture_requires_no_natural_person(self) -> None:
        record = self._synthetic_record()
        record["subject_reference"] = "SUBJECT-1"
        result = evaluate_privacy(record, requested_purpose="genomic_analysis")
        self.assertFalse(result["ready_for_genetic_processing"])
        self.assertTrue(any("NO_NATURAL_PERSON" in error for error in result["errors"]))

    def test_synthetic_fixture_must_attest_no_personal_data(self) -> None:
        record = self._synthetic_record()
        fixture = record["synthetic_fixture"]
        assert isinstance(fixture, dict)
        fixture["contains_personal_data"] = True
        result = evaluate_privacy(record, requested_purpose="genomic_analysis")
        self.assertFalse(result["ready_for_genetic_processing"])
        self.assertTrue(any("contains_personal_data=false" in error for error in result["errors"]))

    def test_synthetic_fixture_cannot_authorize_clinical_report(self) -> None:
        record = self._synthetic_record()
        record["authorized_purposes"] = ["genomic_analysis", "clinical_report"]
        result = evaluate_privacy(record, requested_purpose="clinical_report")
        self.assertFalse(result["ready_for_genetic_processing"])
        self.assertTrue(any("limited to genomic_analysis" in e for e in result["errors"]))

    def test_synthetic_fixture_requires_synthetic_case_identity(self) -> None:
        record = self._synthetic_record()
        record["case_id"] = "REAL-CASE-1"
        result = evaluate_privacy(record, requested_purpose="genomic_analysis")
        self.assertFalse(result["ready_for_genetic_processing"])
        self.assertTrue(any("must start with SYNTHETIC-" in e for e in result["errors"]))

    def test_synthetic_fixture_rejects_invented_legal_basis(self) -> None:
        record = self._synthetic_record()
        record["legal_basis"] = {"status": "VERIFICADO", "reference": "not-applicable"}
        result = evaluate_privacy(record, requested_purpose="genomic_analysis")
        self.assertFalse(result["ready_for_genetic_processing"])
        self.assertTrue(any("must not claim" in e for e in result["errors"]))

    def test_consent_cannot_substitute_for_legal_basis(self) -> None:
        record = self._record()
        record["legal_basis"] = {
            "status": "VERIFICADO",
            "reference": "consent-present",
            "evidence_ref": "consent-record",
            "inferred_from_consent": True,
        }
        result = evaluate_privacy(record, requested_purpose="genomic_analysis")
        self.assertFalse(result["ready_for_genetic_processing"])
        self.assertTrue(any("must not be inferred" in error for error in result["errors"]))

    def test_purpose_mismatch_fails_closed(self) -> None:
        result = evaluate_privacy(self._record(), requested_purpose="model_training")
        self.assertFalse(result["ready_for_genetic_processing"])
        self.assertTrue(any("purpose not authorized" in error for error in result["errors"]))

    def test_input_binding_mismatch_fails_closed(self) -> None:
        result = evaluate_privacy(
            self._record(),
            requested_purpose="genomic_analysis",
            input_sha256="b" * 64,
        )
        self.assertFalse(result["ready_for_genetic_processing"])
        self.assertTrue(any("input SHA-256" in error for error in result["errors"]))

    def test_missing_incident_response_fails_closed(self) -> None:
        record = copy.deepcopy(self._record())
        record.pop("incident_response")
        result = evaluate_privacy(record, requested_purpose="genomic_analysis")
        self.assertFalse(result["ready_for_genetic_processing"])
        self.assertTrue(any("incident_response" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
