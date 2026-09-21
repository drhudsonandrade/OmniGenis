from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.run_stage11_governance_audit import (
    EXPECTED_STAGE_CHAIN,
    _stage1_errors,
    load_policy,
    run_audit,
)
from scripts.validate_stage11_governance_audit import (
    validate_evidence_payload,
    validate_policy_contract,
)

ROOT = Path(__file__).resolve().parents[1]


class Stage11CliTests(unittest.TestCase):
    def test_runner_script_is_directly_executable(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/run_stage11_governance_audit.py"),
                "--help",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--output", completed.stdout)


class Stage11IntegrationTests(unittest.TestCase):
    def test_validate_repo_invokes_stage11_gate(self) -> None:
        text = (ROOT / "scripts/validate_repo.py").read_text(encoding="utf-8")
        self.assertIn(
            "from scripts.validate_stage11_governance_audit import collect_errors as validate_stage11_governance_audit",
            text,
        )
        self.assertIn("errors.extend(validate_stage11_governance_audit(root))", text)

    def test_scaffold_executes_stage11_validator(self) -> None:
        text = (ROOT / ".github/workflows/scaffold-validation.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "- name: Enforce Stage 11 final governance audit",
            text,
        )
        self.assertIn(
            "run: python3 scripts/validate_stage11_governance_audit.py",
            text,
        )


class Stage11PolicyContractTests(unittest.TestCase):
    def test_policy_declares_exact_stage_chain(self) -> None:
        policy = load_policy(ROOT)
        self.assertEqual(validate_policy_contract(policy), [])
        stages = policy["stages"]
        self.assertEqual([item["stage"] for item in stages], list(range(1, 11)))
        self.assertEqual([item["pr"] for item in stages], list(range(74, 84)))
        self.assertEqual(
            [item["merge_commit"] for item in stages],
            [item["merge_commit"] for item in EXPECTED_STAGE_CHAIN],
        )

    def test_policy_does_not_replace_existing_final_audit_gate(self) -> None:
        policy = load_policy(ROOT)
        boundary = policy["claim_boundary"]
        self.assertFalse(boundary["legal_compliance_determined_by_software"])
        self.assertFalse(boundary["license_clean_certification"])
        self.assertFalse(boundary["clinical_validity_determined_by_software"])
        self.assertFalse(boundary["regulatory_approval_determined_by_software"])
        self.assertEqual(
            policy["audit_scope"],
            "COPYRIGHT_AND_GOVERNANCE_CHAIN_ONLY",
        )


class Stage11Stage1Tests(unittest.TestCase):
    def test_stage1_fails_closed_when_baseline_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relative in (
                "LICENSE",
                "policy_engine/LICENSE",
                "COPYRIGHT.md",
                "AUTHORS.md",
                "THIRD_PARTY_NOTICES.md",
                "docs/compliance/LICENSING_POLICY.md",
                "docs/compliance/DEPENDENCY_POLICY.md",
                "licenses/README.md",
                "config/identity_provenance_authorizations.json",
            ):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if relative.endswith(".json"):
                    target.write_text(
                        json.dumps(
                            {
                                "schema": "omnigenis-identity-provenance-authorization-v1",
                                "authorizations": [],
                            }
                        ),
                        encoding="utf-8",
                    )
                else:
                    target.write_text("fixture\n", encoding="utf-8")
            (root / "COPYRIGHT.md").unlink()
            errors = _stage1_errors(root)
            self.assertTrue(
                any("COPYRIGHT.md" in item and "missing" in item for item in errors),
                errors,
            )


class Stage11EvidenceTests(unittest.TestCase):
    def test_execution_audit_passes_current_stage_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "stage11.json"
            payload = run_audit(ROOT, output)
            self.assertEqual(payload["result"], "PASS")
            self.assertEqual(len(payload["stages"]), 10)
            self.assertTrue(all(item["result"] == "PASS" for item in payload["stages"]))
            self.assertTrue(output.is_file())

    def test_validator_rejects_missing_stage_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "stage11.json"
            payload = run_audit(ROOT, output)
            payload["stages"] = payload["stages"][:-1]
            errors = validate_evidence_payload(payload, ROOT)
            self.assertTrue(
                any("stage coverage" in item for item in errors),
                errors,
            )

    def test_validator_rejects_legal_or_regulatory_claims(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "stage11.json"
            payload = run_audit(ROOT, output)
            payload["claim_boundary"]["legal_compliance_determined_by_software"] = True
            errors = validate_evidence_payload(payload, ROOT)
            self.assertTrue(
                any("claim boundary" in item for item in errors),
                errors,
            )


if __name__ == "__main__":
    unittest.main()
