from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.build_stage5_license_gate import gate_status, render_gate_registry
from scripts.validate_stage5_license_gate import collect_errors

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_SHA = "9a379f58ac9fe0bb101ebfd737ac648e510bbca2"


class Stage5LicenseGateTest(unittest.TestCase):
    @staticmethod
    def _copy_contract_root(destination: Path) -> None:
        for relative in (
            "config/software_license_policy.json",
            "config/software_license_gate_registry.json",
            "config/third_party_software_registry.json",
            "locks/stage5-license-debt-baseline.json",
        ):
            source = ROOT / relative
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    @staticmethod
    def _write_gate_registry(root: Path) -> None:
        (root / "config/software_license_gate_registry.json").write_text(
            render_gate_registry(root), encoding="utf-8"
        )

    def test_current_contract_passes_from_stage4_merge_base(self) -> None:
        self.assertEqual(collect_errors(ROOT, base_sha=BOOTSTRAP_SHA), [])

    def test_current_registry_uses_closed_six_state_vocabulary(self) -> None:
        payload = json.loads((ROOT / "config/software_license_gate_registry.json").read_text())
        counts = payload["summary"]["by_license_gate_status"]
        self.assertEqual(
            set(counts),
            {
                "APPROVED",
                "APPROVED_WITH_NOTICE",
                "REVIEW_REQUIRED",
                "RESTRICTED",
                "BLOCKED",
                "UNKNOWN",
            },
        )
        self.assertEqual(payload["summary"]["component_records"], 417)
        self.assertEqual(payload["summary"]["current_non_approved_records"], 146)
        self.assertFalse(payload["summary"]["license_clean_claim_allowed"])

    def test_unknown_never_inherits_approval(self) -> None:
        policy = json.loads((ROOT / "config/software_license_policy.json").read_text())
        component = {
            "id": "fixture:unknown@1",
            "policy_status": "REVIEW_REQUIRED",
            "licenses": ["UNKNOWN"],
        }
        self.assertEqual(gate_status(component, policy), "UNKNOWN")
        component["licenses"] = ["sha256:" + "a" * 64]
        self.assertEqual(gate_status(component, policy), "UNKNOWN")

    def test_empty_license_list_is_unknown_even_when_stage4_says_permissive(self) -> None:
        policy = json.loads((ROOT / "config/software_license_policy.json").read_text())
        component = {
            "id": "fixture:empty-license@1",
            "policy_status": "PERMISSIVE",
            "licenses": [],
        }
        self.assertEqual(gate_status(component, policy), "UNKNOWN")

    def test_known_use_restrictions_are_restricted_not_approved(self) -> None:
        policy = json.loads((ROOT / "config/software_license_policy.json").read_text())
        for license_text in (
            "CC-BY-NC-4.0",
            "ACADEMIC-ONLY",
            "RESEARCH-ONLY",
            "SOURCE-AVAILABLE",
        ):
            with self.subTest(license_text=license_text):
                component = {
                    "id": "fixture:restricted@1",
                    "policy_status": "BLOCKED_BY_DEFAULT",
                    "licenses": [license_text],
                }
                self.assertEqual(gate_status(component, policy), "RESTRICTED")

    def test_exact_pdfium_disposition_requires_recorded_notice(self) -> None:
        policy = json.loads((ROOT / "config/software_license_policy.json").read_text())
        source = json.loads((ROOT / "config/third_party_software_registry.json").read_text())
        matching = [
            item
            for item in source["components"]
            if item["id"] == "pypi:pypdfium2@5.13.0"
        ]
        self.assertEqual(len(matching), 1)
        component = matching[0]
        self.assertEqual(gate_status(component, policy), "APPROVED_WITH_NOTICE")
        mutated = dict(component)
        mutated["evidence"] = [
            item for item in component["evidence"] if item != "licenses/pypdfium2-5.13.0/NOTICE.md"
        ]
        self.assertEqual(gate_status(mutated, policy), "UNKNOWN")

    def test_new_blocked_dependency_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_contract_root(root)
            source_path = root / "config/third_party_software_registry.json"
            source = json.loads(source_path.read_text())
            source["components"].append(
                {
                    "id": "fixture:gpl-tool@1.0",
                    "ecosystem": "fixture",
                    "name": "gpl-tool",
                    "version": "1.0",
                    "scope": "distributed_runtime",
                    "relationship": "direct",
                    "source": "https://example.invalid/gpl-tool-1.0",
                    "licenses": ["GPL-3.0-only"],
                    "policy_status": "BLOCKED_BY_DEFAULT",
                    "evidence": ["fixture"],
                }
            )
            source_path.write_text(json.dumps(source), encoding="utf-8")
            self._write_gate_registry(root)
            errors = collect_errors(root)
        self.assertTrue(any("new non-approved dependency is blocked" in error for error in errors), errors)

    def test_new_verified_permissive_dependency_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_contract_root(root)
            source_path = root / "config/third_party_software_registry.json"
            source = json.loads(source_path.read_text())
            source["components"].append(
                {
                    "id": "fixture:mit-tool@1.0",
                    "ecosystem": "fixture",
                    "name": "mit-tool",
                    "version": "1.0",
                    "scope": "distributed_runtime",
                    "relationship": "direct",
                    "source": "https://example.invalid/mit-tool-1.0",
                    "licenses": ["MIT"],
                    "policy_status": "PERMISSIVE",
                    "evidence": ["fixture"],
                }
            )
            source_path.write_text(json.dumps(source), encoding="utf-8")
            self._write_gate_registry(root)
            errors = collect_errors(root)
        self.assertEqual(errors, [])

    def test_inherited_debt_identity_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_contract_root(root)
            source_path = root / "config/third_party_software_registry.json"
            source = json.loads(source_path.read_text())
            blocked = [
                item
                for item in source["components"]
                if item["policy_status"] == "BLOCKED_BY_DEFAULT"
            ]
            self.assertTrue(blocked)
            target = blocked[0]
            target["source"] = str(target["source"]) + "?drift=1"
            source_path.write_text(json.dumps(source), encoding="utf-8")
            self._write_gate_registry(root)
            errors = collect_errors(root)
        self.assertTrue(any("inherited debt fingerprint changed" in error for error in errors), errors)

    def test_inherited_debt_cannot_be_silently_promoted_to_approved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_contract_root(root)
            baseline = json.loads(
                (root / "locks/stage5-license-debt-baseline.json").read_text()
            )
            debt_id = baseline["entries"][0]["id"]
            source_path = root / "config/third_party_software_registry.json"
            source = json.loads(source_path.read_text())
            matching = [item for item in source["components"] if item["id"] == debt_id]
            self.assertEqual(len(matching), 1)
            matching[0]["policy_status"] = "PERMISSIVE"
            matching[0]["licenses"] = ["MIT"]
            source_path.write_text(json.dumps(source), encoding="utf-8")
            self._write_gate_registry(root)
            errors = collect_errors(root)
        self.assertTrue(
            any("inherited debt status changed" in error for error in errors),
            errors,
        )

    def test_debt_baseline_tamper_fails_digest_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_contract_root(root)
            baseline_path = root / "locks/stage5-license-debt-baseline.json"
            baseline = json.loads(baseline_path.read_text())
            baseline["purpose"] = "tampered"
            baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
            errors = collect_errors(root)
        self.assertIn("Stage 5 frozen debt baseline digest mismatch", errors)

    def test_policy_and_baseline_are_immutable_after_activation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_contract_root(root)
            git = shutil.which("git")
            self.assertIsNotNone(git)
            subprocess.run([git, "init", "-q"], cwd=root, check=True)
            subprocess.run([git, "config", "user.email", "stage5@example.invalid"], cwd=root, check=True)
            subprocess.run([git, "config", "user.name", "Stage5 Test"], cwd=root, check=True)
            subprocess.run([git, "add", "."], cwd=root, check=True)
            subprocess.run([git, "commit", "-qm", "baseline"], cwd=root, check=True)
            base_sha = subprocess.run(
                [git, "rev-parse", "HEAD"], cwd=root, check=True, text=True, stdout=subprocess.PIPE
            ).stdout.strip()
            self.assertEqual(collect_errors(root, base_sha=base_sha), [])

            policy_path = root / "config/software_license_policy.json"
            policy = json.loads(policy_path.read_text())
            policy["external_reference"]["purpose"] = "tampered policy"
            policy_path.write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n")
            self._write_gate_registry(root)
            errors = collect_errors(root, base_sha=base_sha)
        self.assertIn("Stage 5 software license policy is immutable after activation", errors)

    def test_initial_activation_rejects_wrong_bootstrap_base(self) -> None:
        errors = collect_errors(ROOT, base_sha="181bd15f56c1d00ff00fa21c1e78aca6e6daf0ae")
        self.assertTrue(any("bootstrap is allowed only" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
