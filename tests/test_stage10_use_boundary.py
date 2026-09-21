from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.use_boundary_gate import (
    UseBoundaryError,
    evaluate_use_boundary,
    load_policy,
)
from scripts.validate_stage10_use_boundary import collect_errors

ROOT = Path(__file__).resolve().parents[1]
INPUT_SHA = "a" * 64
TEST_EVIDENCE_KEY_TEXT = "public-test-stage10-evidence-key-material-32-bytes"
TEST_EVIDENCE_KEY = TEST_EVIDENCE_KEY_TEXT.encode("utf-8")


def _signed_entry(*, evidence_ref: str, label: str, case_id: str, input_sha256: str,
                  requested_operation: str, use_class: str, decision: str) -> dict:
    entry = {
        "evidence_ref": evidence_ref,
        "label": label,
        "status": "VERIFICADO",
        "case_id": case_id,
        "input_sha256": input_sha256,
        "requested_operation": requested_operation,
        "use_class": use_class,
        "decision": decision,
        "evidence_sha256": hashlib.sha256(evidence_ref.encode("utf-8")).hexdigest(),
    }
    raw = json.dumps(
        entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    entry["hmac_sha256"] = hmac.new(TEST_EVIDENCE_KEY, raw, hashlib.sha256).hexdigest()
    return entry


def signed_evidence_ledger(record: dict) -> dict:
    blocks: list[tuple[str, dict]] = []
    scope = record.get("research_scope")
    if isinstance(scope, dict) and isinstance(scope.get("assessment"), dict):
        blocks.append(("research_scope.assessment", scope["assessment"]))
    for label in (
        "research_ethics_assessment",
        "clinical_validation",
        "professional_review",
        "regulatory_assessment",
    ):
        block = record.get(label)
        if isinstance(block, dict):
            blocks.append((label, block))
    entries = []
    for label, block in blocks:
        evidence_ref = block.get("evidence_ref")
        decision = block.get("decision")
        if isinstance(evidence_ref, str) and evidence_ref and isinstance(decision, str) and decision:
            entries.append(
                _signed_entry(
                    evidence_ref=evidence_ref,
                    label=label,
                    case_id=str(record["case_id"]),
                    input_sha256=str(record["input_sha256"]),
                    requested_operation=str(record["requested_operation"]),
                    use_class=str(record["use_class"]),
                    decision=decision,
                )
            )
    return {
        "schema": "omnigenis-use-boundary-evidence-ledger-v1",
        "key_id": "stage10-evidence-v1",
        "entries": entries,
    }



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
                "decision": "VALIDATED_FOR_STATED_USE",
                "evidence_ref": "validation:clinical-1",
            },
            "professional_review": {
                "status": "VERIFICADO",
                "decision": "PROFESSIONAL_REVIEW_COMPLETED",
                "evidence_ref": "professional-review:1",
                "responsible_professional_ref": "professional:responsible-1",
            },
            "regulatory_assessment": {
                "status": "VERIFICADO",
                "decision": "NOT_REGULATED_FOR_STATED_USE",
                "evidence_ref": "regulatory-assessment:1",
            },
        }

    def evaluate(self, record: dict) -> dict:
        with patch.dict(
            os.environ,
            {"OMNIGENIS_STAGE10_EVIDENCE_HMAC_KEY": TEST_EVIDENCE_KEY_TEXT},
        ):
            return evaluate_use_boundary(
                record,
                requested_operation="FINAL_AUDITED_REPORT",
                expected_case_id="CASE-1",
                expected_input_sha256=INPUT_SHA,
                policy=self.policy,
                evidence_ledger=signed_evidence_ledger(record),
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
            "scripts/generate_all_reports.py",
            "scripts/validate_repo.py",
            "main.nf",
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


class Stage10EvidenceAuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy()
        self.record = {
            "schema": "omnigenis-use-boundary-record-v1",
            "status": "VERIFICADO",
            "record_id": "USE-EVIDENCE-1",
            "case_id": "CASE-1",
            "input_sha256": INPUT_SHA,
            "use_class": "RESEARCH_ONLY",
            "requested_operation": "FINAL_AUDITED_REPORT",
            "intended_use_ref": "protocol:evidence",
            "clinical_use_authorized": False,
            "regulatory_use_authorized": False,
            "nonclinical_label_ref": "label:not-for-clinical-use",
            "research_scope": {
                "involves_human_subjects": False,
                "assessment": {
                    "status": "VERIFICADO",
                    "decision": "NOT_HUMAN_SUBJECTS_RESEARCH",
                    "evidence_ref": "scope:verified",
                },
            },
        }

    def _evaluate(self, ledger: dict) -> dict:
        with patch.dict(
            os.environ,
            {"OMNIGENIS_STAGE10_EVIDENCE_HMAC_KEY": TEST_EVIDENCE_KEY_TEXT},
        ):
            return evaluate_use_boundary(
                self.record,
                requested_operation="FINAL_AUDITED_REPORT",
                expected_case_id="CASE-1",
                expected_input_sha256=INPUT_SHA,
                policy=self.policy,
                evidence_ledger=ledger,
            )

    def test_signed_bound_evidence_is_accepted(self) -> None:
        result = self._evaluate(signed_evidence_ledger(self.record))
        self.assertTrue(result["ready_for_requested_release"])

    def test_tampered_evidence_signature_is_rejected(self) -> None:
        ledger = signed_evidence_ledger(self.record)
        ledger["entries"][0]["hmac_sha256"] = "0" * 64
        result = self._evaluate(ledger)
        self.assertFalse(result["ready_for_requested_release"])
        self.assertTrue(any("signature" in item for item in result["errors"]))

    def test_evidence_reuse_with_wrong_case_binding_is_rejected(self) -> None:
        ledger = signed_evidence_ledger(self.record)
        entry = ledger["entries"][0]
        entry["case_id"] = "CASE-OTHER"
        unsigned = {k: v for k, v in entry.items() if k != "hmac_sha256"}
        raw = json.dumps(
            unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        entry["hmac_sha256"] = hmac.new(
            TEST_EVIDENCE_KEY, raw, hashlib.sha256
        ).hexdigest()
        result = self._evaluate(ledger)
        self.assertFalse(result["ready_for_requested_release"])
        self.assertTrue(any("case_id binding" in item for item in result["errors"]))




class Stage10ReviewerRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy()
        self.record = {
            "schema": "omnigenis-use-boundary-record-v1",
            "status": "VERIFICADO",
            "record_id": "USE-REVIEW-1",
            "case_id": "CASE-1",
            "input_sha256": INPUT_SHA,
            "use_class": "RESEARCH_ONLY",
            "requested_operation": "FINAL_AUDITED_REPORT",
            "intended_use_ref": "protocol:review",
            "clinical_use_authorized": False,
            "regulatory_use_authorized": False,
            "nonclinical_label_ref": "label:not-for-clinical-use",
            "research_scope": {
                "involves_human_subjects": False,
                "assessment": {
                    "status": "VERIFICADO",
                    "decision": "NOT_HUMAN_SUBJECTS_RESEARCH",
                    "evidence_ref": "fabricated:scope",
                },
            },
        }

    def test_policy_version_drift_is_rejected(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["version"] = "999"
        with self.assertRaises(UseBoundaryError):
            evaluate_use_boundary(
                self.record,
                requested_operation="FINAL_AUDITED_REPORT",
                expected_case_id="CASE-1",
                expected_input_sha256=INPUT_SHA,
                policy=policy,
            )

    def test_policy_effective_date_drift_is_rejected(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["effective_date"] = "2099-01-01"
        with self.assertRaises(UseBoundaryError):
            evaluate_use_boundary(
                self.record,
                requested_operation="FINAL_AUDITED_REPORT",
                expected_case_id="CASE-1",
                expected_input_sha256=INPUT_SHA,
                policy=policy,
            )

    def test_fabricated_evidence_reference_cannot_authorize_release(self) -> None:
        result = evaluate_use_boundary(
            self.record,
            requested_operation="FINAL_AUDITED_REPORT",
            expected_case_id="CASE-1",
            expected_input_sha256=INPUT_SHA,
            policy=self.policy,
        )
        self.assertFalse(result["ready_for_requested_release"])
        self.assertTrue(any("authenticated evidence" in item for item in result["errors"]))

    def test_report_entrypoints_require_stage10_inputs(self) -> None:
        generator = (ROOT / "scripts/generate_all_reports.py").read_text(encoding="utf-8")
        main = (ROOT / "main.nf").read_text(encoding="utf-8")
        array = (ROOT / "workflows/array.nf").read_text(encoding="utf-8")
        wgs = (ROOT / "workflows/wgs.nf").read_text(encoding="utf-8")
        for token in ("--use-boundary", "--use-boundary-evidence-ledger"):
            self.assertIn(token, generator)
            self.assertIn(token, array)
            self.assertIn(token, wgs)
        for token in ("use_boundary", "use_boundary_evidence_ledger"):
            self.assertIn(f"params.{token}", main)
            self.assertIn(token, array)
            self.assertIn(token, wgs)

    @staticmethod
    def _mutated_root():
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        Stage10ValidatorMutationTests._fixture(root)
        return temporary, root

    def test_validator_rejects_nested_only_boundary_call(self) -> None:
        temporary, root = self._mutated_root()
        try:
            release = root / "scripts/prepare_report_release.py"
            text = release.read_text(encoding="utf-8")
            old = '''            boundary_result = evaluate_use_boundary(
                use_boundary,
                requested_operation="FINAL_AUDITED_REPORT",
                expected_case_id=case_id,
                expected_input_sha256=input_sha256,
                evidence_ledger=use_boundary_evidence_ledger,
            )
'''
            new = '''            def hidden_boundary_call():
                return evaluate_use_boundary(
                    use_boundary,
                    requested_operation="FINAL_AUDITED_REPORT",
                    expected_case_id=case_id,
                    expected_input_sha256=input_sha256,
                    evidence_ledger=use_boundary_evidence_ledger,
                )
            boundary_result = _blocked_use_boundary("hidden call must not count")
'''
            self.assertIn(old, text)
            release.write_text(text.replace(old, new, 1), encoding="utf-8")
            errors = collect_errors(root)
            self.assertIn(
                "report release does not execute a reachable top-level Stage 10 boundary decision",
                errors,
            )
        finally:
            temporary.cleanup()

    def test_validator_rejects_unreachable_boundary_call(self) -> None:
        temporary, root = self._mutated_root()
        try:
            release = root / "scripts/prepare_report_release.py"
            text = release.read_text(encoding="utf-8")
            old = '''            boundary_result = evaluate_use_boundary(
                use_boundary,
                requested_operation="FINAL_AUDITED_REPORT",
                expected_case_id=case_id,
                expected_input_sha256=input_sha256,
                evidence_ledger=use_boundary_evidence_ledger,
            )
'''
            new = '''            if False:
                boundary_result = evaluate_use_boundary(
                    use_boundary,
                    requested_operation="FINAL_AUDITED_REPORT",
                    expected_case_id=case_id,
                    expected_input_sha256=input_sha256,
                    evidence_ledger=use_boundary_evidence_ledger,
                )
            else:
                boundary_result = _blocked_use_boundary("dead branch must not count")
'''
            self.assertIn(old, text)
            release.write_text(text.replace(old, new, 1), encoding="utf-8")
            errors = collect_errors(root)
            self.assertIn(
                "report release does not execute a reachable top-level Stage 10 boundary decision",
                errors,
            )
        finally:
            temporary.cleanup()

    def test_validator_rejects_wrong_release_operation_binding(self) -> None:
        temporary, root = self._mutated_root()
        try:
            release = root / "scripts/prepare_report_release.py"
            text = release.read_text(encoding="utf-8")
            text = text.replace(
                'requested_operation="FINAL_AUDITED_REPORT"',
                'requested_operation="REGULATORY_SUBMISSION"',
                1,
            )
            release.write_text(text, encoding="utf-8")
            errors = collect_errors(root)
            self.assertIn(
                "report release Stage 10 requested_operation must be FINAL_AUDITED_REPORT",
                errors,
            )
        finally:
            temporary.cleanup()

    def test_nextflow_requires_single_regular_stage10_artifacts(self) -> None:
        main = (ROOT / "main.nf").read_text(encoding="utf-8")
        self.assertIn("def requireSingleRegularFile", main)
        self.assertEqual(
            2,
            len(
                re.findall(
                    r"requireSingleRegularFile\(\s*params\.use_boundary,",
                    main,
                )
            ),
        )
        self.assertEqual(
            2,
            len(
                re.findall(
                    r"requireSingleRegularFile\(\s*"
                    r"params\.use_boundary_evidence_ledger,",
                    main,
                )
            ),
        )
        self.assertNotIn("Channel.fromPath(params.use_boundary", main)
        self.assertNotIn(
            "Channel.fromPath(params.use_boundary_evidence_ledger",
            main,
        )

    def test_generate_all_reports_cannot_bypass_stage10_without_policy(self) -> None:
        import scripts.generate_all_reports as generator

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "curated.json"
            output_dir = root / "reports"
            input_path.write_text(
                json.dumps(
                    {
                        "case_id": "CASE-1",
                        "input_sha256": INPUT_SHA,
                        "publication_gate": {"passed": True},
                    }
                ),
                encoding="utf-8",
            )
            blocked_payload = {
                "case_id": "CASE-1",
                "publication_gate": {"passed": False},
                "report_release_blockers": [
                    "use_boundary",
                    "policy_evaluation_binding",
                ],
            }
            argv = [
                "generate_all_reports.py",
                "--input",
                str(input_path),
                "--output-dir",
                str(output_dir),
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(
                    generator,
                    "assemble_release",
                    return_value=blocked_payload,
                ) as assemble,
            ):
                result = generator.main()
            self.assertEqual(result, 0)
            assemble.assert_called_once()
            self.assertEqual(assemble.call_args.args[1], {})
            self.assertIsNone(assemble.call_args.args[2])
            self.assertIsNone(assemble.call_args.args[3])
            blocked = json.loads(
                (output_dir / "REPORTS_BLOCKED.json").read_text(encoding="utf-8")
            )
            self.assertIn("use_boundary", blocked["blockers"])
            self.assertIn("policy_evaluation_binding", blocked["blockers"])

    def test_generate_all_reports_rejects_explicit_missing_stage10_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "curated.json"
            output_dir = root / "reports"
            input_path.write_text(
                json.dumps(
                    {
                        "case_id": "CASE-1",
                        "input_sha256": INPUT_SHA,
                        "publication_gate": {"passed": False},
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/generate_all_reports.py"),
                    "--input",
                    str(input_path),
                    "--use-boundary",
                    str(root / "missing-boundary.json"),
                    "--output-dir",
                    str(output_dir),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("STAGE10 INPUT MISSING", completed.stderr)

    def test_validator_rejects_boundary_result_reassignment_before_guard(self) -> None:
        temporary, root = self._mutated_root()
        try:
            release = root / "scripts/prepare_report_release.py"
            text = release.read_text(encoding="utf-8")
            anchor = '    result["use_boundary_verification"] = copy.deepcopy(boundary_result)\n'
            self.assertIn(anchor, text)
            release.write_text(
                text.replace(
                    anchor,
                    '    boundary_result = {"ready_for_requested_release": True}\n' + anchor,
                    1,
                ),
                encoding="utf-8",
            )
            errors = collect_errors(root)
            self.assertIn(
                "report release reassigns boundary_result before the Stage 10 guard",
                errors,
            )
        finally:
            temporary.cleanup()

    def _workflow_bypass_errors(self, replacement: str) -> list[str]:
        temporary, root = self._mutated_root()
        try:
            workflow = root / ".github/workflows/scaffold-validation.yml"
            text = workflow.read_text(encoding="utf-8")
            original = (
                "      - name: Enforce Stage 10 research-clinical-regulatory boundary\n"
                "        run: python3 scripts/validate_stage10_use_boundary.py\n"
            )
            self.assertIn(original, text)
            workflow.write_text(text.replace(original, replacement, 1), encoding="utf-8")
            return collect_errors(root)
        finally:
            temporary.cleanup()

    def test_validator_rejects_workflow_if_false_bypass(self) -> None:
        errors = self._workflow_bypass_errors(
            "      - name: Enforce Stage 10 research-clinical-regulatory boundary\n"
            "        if: false\n"
            "        run: python3 scripts/validate_stage10_use_boundary.py\n"
        )
        self.assertIn(
            "Stage 10 validator is not executed by .github/workflows/scaffold-validation.yml",
            errors,
        )

    def test_validator_rejects_workflow_continue_on_error_bypass(self) -> None:
        errors = self._workflow_bypass_errors(
            "      - name: Enforce Stage 10 research-clinical-regulatory boundary\n"
            "        continue-on-error: true\n"
            "        run: python3 scripts/validate_stage10_use_boundary.py\n"
        )
        self.assertIn(
            "Stage 10 validator is not executed by .github/workflows/scaffold-validation.yml",
            errors,
        )

    def test_validator_rejects_workflow_or_true_bypass(self) -> None:
        errors = self._workflow_bypass_errors(
            "      - name: Enforce Stage 10 research-clinical-regulatory boundary\n"
            "        run: python3 scripts/validate_stage10_use_boundary.py || true\n"
        )
        self.assertIn(
            "Stage 10 validator is not executed by .github/workflows/scaffold-validation.yml",
            errors,
        )

    def test_validator_requires_executable_workflow_step(self) -> None:
        temporary, root = self._mutated_root()
        try:
            workflow = root / ".github/workflows/scaffold-validation.yml"
            text = workflow.read_text(encoding="utf-8")
            old = "run: python3 scripts/validate_stage10_use_boundary.py"
            self.assertIn(old, text)
            workflow.write_text(
                text.replace(old, "run: echo scripts/validate_stage10_use_boundary.py", 1),
                encoding="utf-8",
            )
            errors = collect_errors(root)
            self.assertIn(
                "Stage 10 validator is not executed by .github/workflows/scaffold-validation.yml",
                errors,
            )
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
