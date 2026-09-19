from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.validate_stage6_data_sources import (
    EXPECTED_RESOURCE_IDS,
    PANELAPP_ENGLAND_PROTECTED_FIELDS,
    REQUIRED_FIELDS,
    collect_errors,
)
from tests.workflow_test_utils import job_block

ROOT = Path(__file__).resolve().parents[1]


def _named_step_run(workflow: str, job_name: str, step_name: str) -> str:
    job = job_block(workflow, job_name)
    marker = f"      - name: {step_name}\n"
    if marker not in job:
        raise AssertionError(f"step {step_name!r} is missing from job {job_name!r}")
    step = job.split(marker, 1)[1].split("\n      - ", 1)[0]
    run_lines = [
        line.removeprefix("        run: ").strip()
        for line in step.splitlines()
        if line.startswith("        run: ")
    ]
    if len(run_lines) != 1:
        raise AssertionError(f"step {step_name!r} must have exactly one executable run command")
    return run_lines[0]


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

    def _resource(self, payload: dict, resource_id: str) -> dict:
        matches = [
            item
            for item in payload["resources"]
            if isinstance(item, dict) and item.get("id") == resource_id
        ]
        self.assertEqual(len(matches), 1, resource_id)
        return matches[0]

    def test_current_registry_passes(self) -> None:
        self.assertEqual(collect_errors(ROOT), [])

    def test_required_static_job_executes_stage6_gate(self) -> None:
        workflow = (ROOT / ".github/workflows/scaffold-validation.yml").read_text(encoding="utf-8")
        step_name = "Enforce Stage 6 scientific data registry"
        command = "python3 scripts/validate_stage6_data_sources.py"
        self.assertEqual(_named_step_run(workflow, "static", step_name), command)

        commented = workflow.replace(
            f"        run: {command}",
            f"        # run: {command}",
            1,
        )
        self.assertNotEqual(commented, workflow)
        with self.assertRaises(AssertionError):
            _named_step_run(commented, "static", step_name)

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

    def test_duplicate_json_key_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_contract_root(root)
            path = root / "config/data_source_registry.yaml"
            raw = path.read_text(encoding="utf-8")
            mutated = raw.replace('"stage": 6,', '"stage": 6,\n  "stage": 5,', 1)
            self.assertNotEqual(mutated, raw)
            path.write_text(mutated, encoding="utf-8")
            errors = collect_errors(root)
        self.assertTrue(any("duplicate JSON key: stage" in error for error in errors), errors)

    def test_local_artifact_cannot_escape_repository_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "repo"
            root.mkdir()
            self._copy_contract_root(root)
            outside = base / "outside.json"
            outside.write_text('{"outside": true}\n', encoding="utf-8")
            digest = hashlib.sha256(outside.read_bytes()).hexdigest()

            candidates = {
                "absolute": str(outside),
                "parent": "../outside.json",
            }
            symlink = root / "outside-link.json"
            symlink.symlink_to(outside)
            candidates["symlink"] = symlink.name

            for case, local_artifact in candidates.items():
                with self.subTest(case=case):
                    payload = self._registry(root)
                    target = self._resource(payload, "ncbi-dbsnp")
                    target["local_artifact"] = local_artifact
                    target["sha256"] = digest
                    self._write_registry(root, payload)
                    errors = collect_errors(root)
                    self.assertTrue(
                        any("local artifact escapes repository root: ncbi-dbsnp" in error for error in errors),
                        errors,
                    )

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
            target = self._resource(payload, "panelapp-australia")
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
            gencode = self._resource(payload, "gencode-human")
            gencode["covers_manifest_artifact_ids"] = []
            self._write_registry(root, payload)
            errors = collect_errors(root)
        self.assertTrue(any("GRCh38 manifest licensing coverage mismatch" in error for error in errors), errors)

    def test_panelapp_england_protected_fields_cannot_be_weakened_individually(self) -> None:
        permissive = {
            "status": "DOCUMENTED_OPEN",
            "license": "CC0-1.0",
            "commercial_use": "PERMITTED",
            "clinical_use": "PERMITTED",
            "research_use": "PERMITTED",
            "redistribution": "PERMITTED",
            "modification": "PERMITTED",
            "derived_data": "PERMITTED",
            "local_copy_allowed": "YES",
            "attribution_required": "NO",
        }
        self.assertEqual(set(permissive), set(PANELAPP_ENGLAND_PROTECTED_FIELDS))
        for field, replacement in permissive.items():
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    self._copy_contract_root(root)
                    payload = self._registry(root)
                    panelapp = self._resource(payload, "panelapp-genomics-england")
                    panelapp[field] = replacement
                    self._write_registry(root, payload)
                    errors = collect_errors(root)
                self.assertIn(
                    f"Stage 6 Genomics England PanelApp protected field drift: {field}",
                    errors,
                )

    def test_unresolved_official_terms_remain_review_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_contract_root(root)
            payload = self._registry(root)
            gnomad = self._resource(payload, "gnomad")
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
            score_ids = sorted(pgs["scores"])
            self.assertTrue(score_ids)
            first = pgs["scores"][score_ids[0]]
            first["license"] = ""
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                json.dump(pgs, handle, ensure_ascii=False, sort_keys=True)
            payload = self._registry(root)
            resource = self._resource(payload, "ebi-pgs-catalog")
            resource["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            self._write_registry(root, payload)
            errors = collect_errors(root)
        self.assertTrue(any("PGS scores without license terms" in error for error in errors), errors)

    def test_pgs_score_weight_file_is_rejected_including_dist(self) -> None:
        for relative in ("PGS999999.txt.gz", "dist/PGS999999.txt.gz"):
            with self.subTest(relative=relative):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    self._copy_contract_root(root)
                    weight = root / relative
                    weight.parent.mkdir(parents=True, exist_ok=True)
                    weight.write_bytes(b"not-a-real-score")
                    errors = collect_errors(root)
                self.assertTrue(
                    any("PGS score-weight files must not be committed" in error for error in errors),
                    errors,
                )

    def test_all_evidence_adapters_are_registered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_contract_root(root)
            payload = self._registry(root)
            clinpgx = self._resource(payload, "clinpgx")
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
