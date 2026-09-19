from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.validate_stage6_data_sources import EXPECTED_RESOURCE_IDS, REQUIRED_FIELDS, collect_errors

ROOT = Path(__file__).resolve().parents[1]


class Stage6ScientificDataRegistryTest(unittest.TestCase):
    @staticmethod
    def _copy_contract_root(destination: Path) -> None:
        registry = json.loads((ROOT / "config/data_source_registry.yaml").read_text(encoding="utf-8"))
        relative_paths = {
            "config/data_source_registry.yaml",
            "manifests/GRCh38.sources.tsv",
        }
        for resource in registry["resources"]:
            local = resource.get("local_artifact")
            if isinstance(local, str):
                relative_paths.add(local)
        for relative in sorted(relative_paths):
            source = ROOT / relative
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    @staticmethod
    def _registry(root: Path) -> dict:
        return json.loads((root / "config/data_source_registry.yaml").read_text(encoding="utf-8"))

    @staticmethod
    def _write_registry(root: Path, payload: dict) -> None:
        (root / "config/data_source_registry.yaml").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def test_current_registry_passes(self) -> None:
        self.assertEqual(collect_errors(ROOT), [])

    def test_required_static_job_executes_stage6_gate(self) -> None:
        workflow = (ROOT / ".github/workflows/scaffold-validation.yml").read_text(encoding="utf-8")
        self.assertIn("name: Enforce Stage 6 scientific data registry", workflow)
        self.assertIn("python3 scripts/validate_stage6_data_sources.py", workflow)

    def test_expected_resources_and_required_fields_are_complete(self) -> None:
        payload = self._registry(ROOT)
        by_id = {item["id"]: item for item in payload["resources"]}
        self.assertEqual(set(by_id), EXPECTED_RESOURCE_IDS)
        self.assertEqual(len(by_id), 19)
        for resource_id, resource in by_id.items():
            with self.subTest(resource_id=resource_id):
                for field in REQUIRED_FIELDS:
                    self.assertIn(field, resource)
                    self.assertNotIn(resource[field], (None, "", []))

    def test_unknown_license_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_contract_root(root)
            payload = self._registry(root)
            payload["resources"][0]["license"] = "UNKNOWN"
            self._write_registry(root, payload)
            errors = collect_errors(root)
        self.assertTrue(any("silent/unknown license" in error for error in errors), errors)

    def test_unverified_terms_cannot_be_promoted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_contract_root(root)
            payload = self._registry(root)
            target = next(item for item in payload["resources"] if item["id"] == "panelapp-australia")
            target["status"] = "DOCUMENTED_OPEN"
            self._write_registry(root, payload)
            errors = collect_errors(root)
        self.assertTrue(any("unverified terms cannot pass" in error for error in errors), errors)

    def test_local_artifact_digest_drift_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_contract_root(root)
            artifact = root / "docs/evidence/ASSESSED_ALLELES_CLINVAR.json"
            artifact.write_bytes(artifact.read_bytes() + b"\n")
            errors = collect_errors(root)
        self.assertTrue(any("local artifact digest mismatch: ncbi-dbsnp" in error for error in errors), errors)

    def test_grch38_manifest_requires_complete_rights_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_contract_root(root)
            payload = self._registry(root)
            gencode = next(item for item in payload["resources"] if item["id"] == "gencode-human")
            gencode["covers_manifest_artifact_ids"] = []
            self._write_registry(root, payload)
            errors = collect_errors(root)
        self.assertTrue(any("GRCh38 manifest licensing coverage mismatch" in error for error in errors), errors)

    def test_panelapp_england_must_remain_restricted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_contract_root(root)
            payload = self._registry(root)
            panelapp = next(item for item in payload["resources"] if item["id"] == "panelapp-genomics-england")
            panelapp["status"] = "DOCUMENTED_WITH_OBLIGATIONS"
            self._write_registry(root, payload)
            errors = collect_errors(root)
        self.assertIn("Stage 6 Genomics England PanelApp must remain RESTRICTED without a separate agreement", errors)

    def test_unresolved_official_terms_remain_review_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_contract_root(root)
            payload = self._registry(root)
            gnomad = next(item for item in payload["resources"] if item["id"] == "gnomad")
            gnomad["status"] = "DOCUMENTED_WITH_OBLIGATIONS"
            self._write_registry(root, payload)
            errors = collect_errors(root)
        self.assertIn("Stage 6 unresolved official terms must fail closed: gnomad", errors)

    def test_pgs_every_score_has_record_level_license(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_contract_root(root)
            path = root / "docs/evidence/PGS_CATALOG_REGISTRY.json.gz"
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                pgs = json.load(handle)
            first = next(iter(pgs["scores"].values()))
            first["license"] = ""
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                json.dump(pgs, handle, ensure_ascii=False, sort_keys=True)
            payload = self._registry(root)
            resource = next(item for item in payload["resources"] if item["id"] == "ebi-pgs-catalog")
            resource["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            self._write_registry(root, payload)
            errors = collect_errors(root)
        self.assertTrue(any("PGS scores without license terms" in error for error in errors), errors)

    def test_pgs_score_weight_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_contract_root(root)
            (root / "PGS999999.txt.gz").write_bytes(b"not-a-real-score")
            errors = collect_errors(root)
        self.assertTrue(any("PGS score-weight files must not be committed" in error for error in errors), errors)

    def test_all_evidence_adapters_are_registered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_contract_root(root)
            payload = self._registry(root)
            clinpgx = next(item for item in payload["resources"] if item["id"] == "clinpgx")
            clinpgx["adapter_ids"] = []
            self._write_registry(root, payload)
            errors = collect_errors(root)
        self.assertTrue(any("evidence-adapter coverage mismatch" in error for error in errors), errors)

    def test_pgs_retained_registry_has_6972_licensed_scores(self) -> None:
        with gzip.open(ROOT / "docs/evidence/PGS_CATALOG_REGISTRY.json.gz", "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(len(payload["scores"]), 6972)
        self.assertEqual(payload["totals"]["scores_with_restrictive_license"], 40)
        self.assertTrue(all(str(record.get("license", "")).strip() for record in payload["scores"].values()))


if __name__ == "__main__":
    unittest.main()
