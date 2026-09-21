from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts.run_stage11_governance_audit import (
    EXPECTED_STAGE_CHAIN,
    VALIDATORS,
    _execute_stage,
    _governance_manifest_control,
    _stage1_errors,
    load_policy,
    run_audit,
)
from scripts.validate_stage11_governance_audit import (
    validate_evidence_payload,
    validate_live_governance_evidence,
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
        repo_validator = (ROOT / "scripts/validate_repo.py").read_text(encoding="utf-8")
        self.assertIn(
            "docs/evidence/STAGE11_GITHUB_RULESET_READBACK_2026-09-21.json",
            repo_validator,
        )


class Stage11LiveGovernanceEvidenceTests(unittest.TestCase):
    def _fixture(self) -> dict:
        approval = json.loads(
            (ROOT / ".github/governance/main-approval-ruleset.json").read_text(
                encoding="utf-8"
            )
        )
        protected = json.loads(
            (ROOT / ".github/governance/main-ruleset.json").read_text(
                encoding="utf-8"
            )
        )
        status_rules = [
            rule
            for rule in protected["rules"]
            if rule["type"] == "required_status_checks"
        ]
        self.assertEqual(len(status_rules), 1)
        checks = status_rules[0]["parameters"]["required_status_checks"]
        fingerprints = [
            item["context_fingerprint"]
            for item in checks
            if "context_fingerprint" in item
        ]
        self.assertEqual(len(fingerprints), 1)
        fingerprint = fingerprints[0]
        return {
            "schema": "omnigenis-stage11-github-governance-readback-v1",
            "repository": "OmniGenis",
            "operational_status": "EXECUTADO",
            "result": "PASS",
            "rulesets": {
                "21303100": {
                    "id": 21303100,
                    "payload": protected,
                    "raw_semantics_sha256": "a" * 64,
                    "fingerprint_resolution": [
                        {
                            **fingerprint,
                            "matched_live_context": True,
                            "plaintext_persisted": False,
                        }
                    ],
                },
                "22347095": {
                    "id": 22347095,
                    "payload": approval,
                    "raw_semantics_sha256": "b" * 64,
                    "owner_only_update_verified": True,
                    "pr_only_owner_bypass_verified": True,
                },
            },
        }

    def test_live_ruleset_readback_matches_versioned_manifests(self) -> None:
        self.assertEqual(validate_live_governance_evidence(self._fixture(), ROOT), [])

    def test_live_ruleset_readback_rejects_non_object_manifests(self) -> None:
        """Reject valid JSON manifests whose root is not an object."""
        payload = self._fixture()
        manifests = {
            "main-ruleset.json": (
                [],
                "Stage 11 protected-main manifest must be an object",
            ),
            "main-approval-ruleset.json": (
                [],
                "Stage 11 approval manifest must be an object",
            ),
        }
        for filename, (invalid_manifest, expected_error) in manifests.items():
            with self.subTest(filename=filename):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    governance = root / ".github/governance"
                    governance.mkdir(parents=True)
                    protected = payload["rulesets"]["21303100"]["payload"]
                    approval = payload["rulesets"]["22347095"]["payload"]
                    (governance / "main-ruleset.json").write_text(
                        json.dumps(
                            invalid_manifest
                            if filename == "main-ruleset.json"
                            else protected
                        ),
                        encoding="utf-8",
                    )
                    (governance / "main-approval-ruleset.json").write_text(
                        json.dumps(
                            invalid_manifest
                            if filename == "main-approval-ruleset.json"
                            else approval
                        ),
                        encoding="utf-8",
                    )
                    errors = validate_live_governance_evidence(payload, root)
                self.assertIn(expected_error, errors)

    def test_live_ruleset_readback_accepts_provider_normalized_update_rule(self) -> None:
        payload = self._fixture()
        approval = payload["rulesets"]["22347095"]["payload"]
        update_rules = [
            rule for rule in approval["rules"] if rule.get("type") == "update"
        ]
        pull_rules = [
            rule for rule in approval["rules"] if rule.get("type") == "pull_request"
        ]
        self.assertEqual(len(update_rules), 1)
        self.assertEqual(len(pull_rules), 1)
        normalized_update = {"type": "update"}
        approval["rules"] = [pull_rules[0], normalized_update]
        self.assertEqual(validate_live_governance_evidence(payload, ROOT), [])

    def test_live_ruleset_readback_requires_owner_only_update_rule(self) -> None:
        payload = self._fixture()
        approval = payload["rulesets"]["22347095"]["payload"]
        approval["rules"] = [
            rule for rule in approval["rules"] if rule.get("type") != "update"
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            governance = root / ".github/governance"
            governance.mkdir(parents=True)
            protected = payload["rulesets"]["21303100"]["payload"]
            (governance / "main-ruleset.json").write_text(
                json.dumps(protected), encoding="utf-8"
            )
            (governance / "main-approval-ruleset.json").write_text(
                json.dumps(approval), encoding="utf-8"
            )
            errors = validate_live_governance_evidence(payload, root)
        self.assertIn(
            "Stage 11 approval ruleset missing owner-only update restriction",
            errors,
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
        self.assertEqual(
            policy["github_governance"]["rulesets"],
            {
                "protected_main": 21303100,
                "approval_gate": 22347095,
            },
        )
        self.assertTrue(
            policy["github_governance"]["owner_only_updates_required"]
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


    def test_stage1_nonobject_authorization_is_structured_failure(self) -> None:
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
            ):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("fixture\n", encoding="utf-8")
            auth = root / "config/identity_provenance_authorizations.json"
            auth.parent.mkdir(parents=True, exist_ok=True)
            auth.write_text("[]\n", encoding="utf-8")
            errors = _stage1_errors(root)
        self.assertIn(
            "Stage 1 identity provenance authorization must be an object", errors
        )


class Stage11RunnerFailClosedTests(unittest.TestCase):
    def test_invalid_policy_contract_returns_structured_failure(self) -> None:
        policy = load_policy(ROOT)
        policy["stages"] = policy["stages"][:-1]
        with patch(
            "scripts.run_stage11_governance_audit.load_policy",
            return_value=policy,
        ):
            payload = run_audit(ROOT)
        self.assertEqual(payload["operational_status"], "EXECUTADO")
        self.assertEqual(payload["result"], "FAIL")
        self.assertEqual(payload["stages"], [])
        self.assertTrue(payload["policy_contract_errors"])

    def test_missing_stage_validator_is_structured_unavailable_error(self) -> None:
        policy = load_policy(ROOT)
        stage = policy["stages"][-1]
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        with patch.dict(VALIDATORS, {10: None}):
            record = _execute_stage(ROOT, stage, head)
        self.assertEqual(record["operational_status"], "NÃO DISPONÍVEL")
        self.assertEqual(record["result"], "ERROR")
        self.assertIn("validator unavailable", record["errors"][0])

    def test_nonobject_governance_manifest_is_structured_unavailable_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            governance = root / ".github/governance"
            governance.mkdir(parents=True)
            (governance / "main-approval-ruleset.json").write_text(
                "[]\n", encoding="utf-8"
            )
            (governance / "main-ruleset.json").write_text(
                "{}\n", encoding="utf-8"
            )
            control = _governance_manifest_control(root)
        self.assertEqual(control["operational_status"], "NÃO DISPONÍVEL")
        self.assertEqual(control["result"], "ERROR")
        self.assertIn("must be an object", control["evidence"])

    def test_nonobject_rule_parameters_are_structured_unavailable_error(self) -> None:
        approval = json.loads(
            (ROOT / ".github/governance/main-approval-ruleset.json").read_text(
                encoding="utf-8"
            )
        )
        protected = json.loads(
            (ROOT / ".github/governance/main-ruleset.json").read_text(
                encoding="utf-8"
            )
        )
        for rule in protected["rules"]:
            if rule.get("type") == "required_status_checks":
                rule["parameters"] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            governance = root / ".github/governance"
            governance.mkdir(parents=True)
            (governance / "main-approval-ruleset.json").write_text(
                json.dumps(approval), encoding="utf-8"
            )
            (governance / "main-ruleset.json").write_text(
                json.dumps(protected), encoding="utf-8"
            )
            control = _governance_manifest_control(root)
        self.assertEqual(control["operational_status"], "NÃO DISPONÍVEL")
        self.assertEqual(control["result"], "ERROR")
        self.assertIn("parameters must be an object", control["evidence"])


class Stage11EvidenceTests(unittest.TestCase):
    def test_execution_audit_passes_current_stage_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "stage11.json"
            payload = run_audit(ROOT, output)
            self.assertEqual(payload["result"], "PASS")
            self.assertEqual(len(payload["stages"]), 10)
            self.assertTrue(all(item["result"] == "PASS" for item in payload["stages"]))
            controls = {item["id"]: item for item in payload["global_controls"]}
            self.assertEqual(controls["GITHUB_GOVERNANCE_MANIFESTS"]["result"], "PASS")
            self.assertTrue(output.is_file())

    def test_validator_accepts_repository_git_oid_width(self) -> None:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        policy_bytes = subprocess.run(
            [
                "git",
                "show",
                f"{head}:config/stage11_governance_audit_policy.json",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        runner_bytes = subprocess.run(
            [
                "git",
                "show",
                f"{head}:scripts/run_stage11_governance_audit.py",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        policy = json.loads(policy_bytes.decode("utf-8"))
        payload = {
            "schema": "omnigenis-final-governance-audit-evidence-v1",
            "audit_scope": "COPYRIGHT_AND_GOVERNANCE_CHAIN_ONLY",
            "operational_status": "EXECUTADO",
            "result": "PASS",
            "implementation_sha": head,
            "tree_sha": tree,
            "policy_sha256": hashlib.sha256(policy_bytes).hexdigest(),
            "runner_sha256": hashlib.sha256(runner_bytes).hexdigest(),
            "stages": [
                {
                    "stage": item["stage"],
                    "pr": item["pr"],
                    "name": item["name"],
                    "merge_commit": item["merge_commit"],
                    "merge_ancestry_verified": True,
                    "validator": "fixture",
                    "operational_status": "EXECUTADO",
                    "result": "PASS",
                    "errors": [],
                }
                for item in policy["stages"]
            ],
            "global_controls": [
                {
                    "id": control,
                    "operational_status": "EXECUTADO",
                    "result": "PASS",
                    "returncode": 0,
                    "evidence": "fixture",
                }
                for control in (
                    "GITHUB_GOVERNANCE_MANIFESTS",
                    "SUPPLY_CHAIN_LOCK",
                    "RESIDUAL_LANGUAGE_AUDIT",
                    "CODE_LANGUAGE_GUARD",
                )
            ],
            "claim_boundary": dict(policy["claim_boundary"]),
        }
        errors = validate_evidence_payload(payload, ROOT)
        self.assertNotIn("Stage 11 implementation SHA invalid", errors)
        self.assertNotIn("Stage 11 tree SHA invalid", errors)
        self.assertEqual(errors, [])


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
