from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.build_third_party_registry import license_policy, render_payload
from scripts.validate_stage4_compliance import REQUIRED, collect_errors

ROOT = Path(__file__).resolve().parents[1]

class Stage4ThirdPartyComplianceTest(unittest.TestCase):
    @staticmethod
    def _copy_contract_root(destination: Path) -> None:
        relative_paths = set(REQUIRED) | {
            "locks/runtime-lock.json",
            "locks/actions-lock.json",
            "environment.yml",
            "reporting/requirements.in",
            "reporting/requirements.txt",
            "mcp/package.json",
            "mcp/package-lock.json",
            "Dockerfile",
            ".github/workflows/genoma-ngs-runtime-gate.yml",
        }
        for relative in sorted(relative_paths):
            source = ROOT / relative
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def test_stage4_contract_passes_current_repository(self) -> None:
        self.assertEqual(collect_errors(ROOT), [])

    def test_license_policy_never_inherits_permissive_from_mixed_text(self) -> None:
        self.assertEqual(license_policy("MIT"), "PERMISSIVE")
        self.assertEqual(
            license_policy("MIT AND BSD-3-Clause"),
            "PERMISSIVE",
        )
        self.assertEqual(
            license_policy("MIT AND LicenseRef-Custom-Terms"),
            "REVIEW_REQUIRED",
        )
        self.assertEqual(
            license_policy("IJG AND BSD-3-Clause AND Zlib"),
            "REVIEW_REQUIRED",
        )
        self.assertEqual(
            license_policy("BSD-3-Clause, Apache-2.0, dependency licenses"),
            "REVIEW_REQUIRED",
        )

    def test_registry_is_deterministically_rebuildable(self) -> None:
        expected = render_payload(ROOT)
        actual = (ROOT / "config/third_party_software_registry.json").read_text(
            encoding="utf-8"
        )
        self.assertEqual(actual, expected)

    def test_registry_covers_all_declared_software_ecosystems(self) -> None:
        payload = json.loads(
            (ROOT / "config/third_party_software_registry.json").read_text()
        )
        summary = payload["summary"]
        self.assertEqual(summary["component_records"], 417)
        self.assertEqual(
            summary["by_ecosystem"],
            {
                "conda": 161,
                "deb": 101,
                "github-action": 10,
                "npm": 137,
                "pypi": 8,
            },
        )
        self.assertFalse(summary["license_clean_claim_allowed"])
        self.assertGreater(summary["by_policy_status"]["BLOCKED_BY_DEFAULT"], 0)
        self.assertGreater(summary["by_policy_status"]["REVIEW_REQUIRED"], 0)

    def test_known_copyleft_findings_remain_visible(self) -> None:
        payload = json.loads(
            (ROOT / "config/third_party_software_registry.json").read_text()
        )
        by_id = {item["id"]: item for item in payload["components"]}
        conda_by_name = {
            item["name"]: item
            for item in by_id.values()
            if item["ecosystem"] == "conda"
        }
        for required in ("bcftools", "coreutils", "openjdk"):
            self.assertIn(required, conda_by_name)
        bcftools = conda_by_name["bcftools"]
        coreutils = conda_by_name["coreutils"]
        openjdk = conda_by_name["openjdk"]
        self.assertEqual(bcftools["policy_status"], "BLOCKED_BY_DEFAULT")
        self.assertEqual(coreutils["policy_status"], "BLOCKED_BY_DEFAULT")
        self.assertEqual(openjdk["policy_status"], "REVIEW_REQUIRED")

    def test_exact_pdfium_artifact_keeps_prior_review_status(self) -> None:
        payload = json.loads(
            (ROOT / "config/third_party_software_registry.json").read_text()
        )
        by_id = {item["id"]: item for item in payload["components"]}
        self.assertIn("pypi:pypdfium2@5.13.0", by_id)
        record = by_id["pypi:pypdfium2@5.13.0"]
        self.assertEqual(
            record["policy_status"], "REVIEWED_ACCEPTED_EXACT_ARTIFACT"
        )
        self.assertIn(
            "licenses/pypdfium2-5.13.0/NOTICE.md",
            record["evidence"],
        )

    def test_explicit_conda_lock_matches_resolution_snapshot(self) -> None:
        resolution = json.loads(
            (ROOT / "locks/conda-linux-64-resolution.json").read_text()
        )
        lines = (ROOT / "locks/conda-linux-64-explicit.txt").read_text().splitlines()
        self.assertEqual(lines[0], "@EXPLICIT")
        self.assertEqual(len(lines) - 1, resolution["package_count"])
        expected = [
            f"{item['url']}#sha256:{item['sha256']}"
            for item in resolution["packages"]
        ]
        self.assertEqual(lines[1:], expected)

    def test_docker_and_ci_use_stage4_artifacts(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text()
        workflow = (ROOT / ".github/workflows/scaffold-validation.yml").read_text()
        runtime_workflow = (
            ROOT / ".github/workflows/genoma-ngs-runtime-gate.yml"
        ).read_text()
        self.assertIn(
            "ARG OMNIGENIS_CONDA_SPEC=locks/conda-linux-64-explicit.txt",
            dockerfile,
        )
        self.assertIn("${OMNIGENIS_CONDA_SPEC}", dockerfile)
        self.assertIn("--file /tmp/omnigenis-conda-spec", dockerfile)
        self.assertEqual(
            runtime_workflow.count(
                "--build-arg OMNIGENIS_CONDA_SPEC=environment.yml"
            ),
            2,
        )
        self.assertIn("npm prune --omit=dev --ignore-scripts", dockerfile)
        self.assertIn("scripts/generate_stage4_sbom.sh", workflow)
        sbom_script = (ROOT / "scripts/generate_stage4_sbom.sh").read_text()
        self.assertIn("micromamba list --name base --json", sbom_script)
        self.assertIn("omnigenis.conda.json", sbom_script)
        self.assertIn("name: stage4-sbom-${{ github.sha }}", workflow)
        self.assertIn("provenance: mode=max", workflow)
        self.assertIn("sbom: true", workflow)

    def test_evidence_does_not_claim_local_final_image_scan(self) -> None:
        evidence = json.loads(
            (
                ROOT
                / "docs/evidence/STAGE4_THIRD_PARTY_INVENTORY_2026-09-18.json"
            ).read_text()
        )
        self.assertEqual(
            evidence["execution"]["final_omnigenis_image_sbom"]["status"],
            "CI_REQUIRED",
        )
        self.assertFalse(
            evidence["registry_summary"]["license_clean_claim_allowed"]
        )

    def test_mutated_explicit_lock_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_contract_root(root)
            lock = root / "locks/conda-linux-64-explicit.txt"
            lines = lock.read_text().splitlines()
            lock.write_text("\n".join(lines[:-1]) + "\n")
            errors = collect_errors(root)
            self.assertTrue(
                any("explicit Conda lock differs" in error for error in errors),
                errors,
            )

    def test_missing_npm_license_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_contract_root(root)
            lock = root / "mcp/package-lock.json"
            payload = json.loads(lock.read_text())
            package_paths = [path for path in payload["packages"] if path]
            self.assertTrue(package_paths)
            path = package_paths[0]
            payload["packages"][path].pop("license", None)
            lock.write_text(json.dumps(payload))
            errors = collect_errors(root)
            self.assertTrue(
                any("npm licenses missing" in error for error in errors),
                errors,
            )

if __name__ == "__main__":
    unittest.main()
