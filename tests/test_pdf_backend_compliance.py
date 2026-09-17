from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from reportlab.lib.colors import Color
from reportlab.pdfgen import canvas

from scripts.build_report_coordinate_pack import compile_pack
from reporting.template_v3 import TemplateV3Error, _field_value

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = json.loads((ROOT / "reporting" / "reference_v3_manifest.json").read_text(encoding="utf-8"))
MIGRATION = json.loads(
    (ROOT / "docs" / "evidence" / "PDFIUM_COORDINATE_MIGRATION_2026-09-17.json").read_text(
        encoding="utf-8"
    )
)


def _load_validator():
    path = ROOT / "scripts" / "validate_repo.py"
    spec = importlib.util.spec_from_file_location("validate_repo_pdf_compliance", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load repository validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PdfBackendComplianceTest(unittest.TestCase):
    def test_reporting_runtime_uses_pinned_pdfium_not_pymupdf(self) -> None:
        requirements_in = (ROOT / "reporting" / "requirements.in").read_text(encoding="utf-8")
        requirements_lock = (ROOT / "reporting" / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("pypdfium2==5.13.0", requirements_in)
        self.assertIn("pypdfium2==5.13.0", requirements_lock)
        self.assertNotIn("PyMuPDF", requirements_in)
        self.assertNotIn("pymupdf==", requirements_lock.lower())

    def test_active_coordinate_surfaces_do_not_import_fitz(self) -> None:
        active = (
            "scripts/build_report_coordinate_pack.py",
            "tests/test_report_coordinate_pack_ruleset_markers.py",
            ".github/workflows/genoma-visual-qa-candidates.yml",
        )
        for relative in active:
            with self.subTest(path=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertNotIn("import fitz", text)

    def test_reference_manifest_declares_pdfium_compiler(self) -> None:
        compiler = REFERENCE["coordinate_compiler"]
        self.assertEqual(compiler["id"], "pypdfium2-5.13.0-pdfium-genoma-v3")
        self.assertEqual(compiler["backend"], "PDFium")
        self.assertEqual(compiler["pypdfium2"], "5.13.0")
        self.assertNotIn("pymupdf", compiler)
        self.assertEqual(
            REFERENCE["generated_coordinate_manifest"]["sha256"],
            MIGRATION["candidate_coordinate_artifacts"]["manifest_sha256"],
        )
        self.assertEqual(
            REFERENCE["generated_coordinate_detail"]["sha256"],
            MIGRATION["candidate_coordinate_artifacts"]["compressed_detail_sha256"],
        )

    def test_legacy_pixel_qa_is_not_relabelled_as_pdfium_evidence(self) -> None:
        self.assertEqual(MIGRATION["pixel_qa"]["pdfium_candidate_status"], "NOT_EXECUTED")
        self.assertEqual(
            REFERENCE["legacy_coordinate_compiler"]["id"],
            "fitz-1.26.7-genoma-v2",
        )
        self.assertEqual(
            REFERENCE["legacy_generated_coordinate_manifest"]["sha256"],
            "1d2e6b745b338b18530d5dc0e42cb542a01947a81a0515bece4882b4e09539a5",
        )

    def test_installed_pdfium_wheel_preserves_bundled_license_notices(self) -> None:
        files = {str(item) for item in (importlib.metadata.distribution("pypdfium2").files or [])}
        self.assertIn(
            "pypdfium2-5.13.0.dist-info/licenses/LICENSES/Apache-2.0.txt",
            files,
        )
        self.assertIn(
            "pypdfium2-5.13.0.dist-info/licenses/LICENSES/BSD-3-Clause.txt",
            files,
        )
        self.assertGreaterEqual(
            sum("/BUILD_LICENSES/" in item for item in files),
            16,
        )
        evidence = (ROOT / "licenses" / "pypdfium2-5.13.0" / "README.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "81df25c1ab4c13ff773102d3cbea1967511d079123b067fc077bd0c4d57d91d8",
            evidence,
        )

    def test_pdfium_compiler_preserves_legacy_geometry_on_synthetic_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "fixture.pdf"
            pdf = canvas.Canvas(str(pdf_path), pagesize=(300, 400))
            pdf.setFillColor(Color(0.93, 0.95, 0.97))
            pdf.setStrokeColor(Color(0.2, 0.3, 0.4))
            pdf.setLineWidth(1)
            pdf.rect(20, 300, 260, 60, fill=1, stroke=1)
            pdf.setFillColor(Color(0.1, 0.2, 0.3))
            pdf.setFont("Helvetica", 8)
            pdf.drawString(30, 340, "[[CASE_ID]]")
            pdf.save()
            index = {"reports": {"01": {
                "filename": pdf_path.name,
                "sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
                "size_bytes": pdf_path.stat().st_size,
                "page_count": 1,
                "page_size_pt": [300, 400],
                "placeholder_count": 1,
            }}}
            index_path = root / "index.json"
            index_path.write_text(json.dumps(index), encoding="utf-8")
            report = compile_pack(root, index_path)["reports"]["01"]
        field = report["fields"][0]
        self.assertEqual(field["bbox"], [30.0, 51.4, 73.128, 62.392])
        self.assertEqual(field["cell_bbox"], [20.0, 40.0, 280.0, 100.0])
        self.assertEqual(field["background"], "#EDF2F7")

    def test_template_field_lookup_accepts_legacy_field_id_alias(self) -> None:
        item = {
            "field_id": "current-field-id",
            "legacy_field_ids": ["legacy-field-id"],
            "token": "[[TOKEN]]",
            "occurrence": 1,
        }
        self.assertEqual(
            _field_value({"legacy-field-id": "preserved-value"}, item),
            "preserved-value",
        )

    def test_template_field_lookup_rejects_malformed_legacy_aliases(self) -> None:
        item = {
            "field_id": "current-field-id",
            "legacy_field_ids": ["legacy-field-id", 7],
            "token": "[[TOKEN]]",
            "occurrence": 1,
        }
        with self.assertRaisesRegex(TemplateV3Error, "legacy field aliases"):
            _field_value({}, item)

    def test_legacy_field_alias_registry_is_hash_bound(self) -> None:
        registry = json.loads(
            (ROOT / "reporting" / "legacy_field_aliases.json").read_text(encoding="utf-8")
        )
        self.assertEqual(registry["schema"], "omnigenis-legacy-template-field-aliases-v1")
        self.assertEqual(len(registry["aliases"]), 1)
        record = registry["aliases"][0]
        self.assertEqual(
            record["current_field_id_sha256"],
            "7d315ae6393bc07659a6831e86624534dcb5c1c0c8accebd5e8da9c0e09aac58",
        )
        decoded = base64.b64decode(record["legacy_field_id_utf8_b64"], validate=True)
        self.assertEqual(len(decoded.decode("utf-8")), 268)
        self.assertEqual(
            hashlib.sha256(decoded).hexdigest(),
            "2ac96d9bdfa4080c2828ae8a4e0335a627c668c72de54504fb71f554ab534925",
        )
        self.assertEqual(hashlib.sha256(decoded).hexdigest(), record["legacy_field_id_sha256"])

    def test_stage2_compliance_artifacts_are_repository_contract_paths(self) -> None:
        validator = _load_validator()
        for relative in (
            "scripts/pdfium_backend.py",
            "reporting/requirements.in",
            "reporting/legacy_field_aliases.json",
            "licenses/pypdfium2-5.13.0/README.md",
            "docs/evidence/PDFIUM_COORDINATE_MIGRATION_2026-09-17.json",
        ):
            with self.subTest(path=relative):
                self.assertIn(relative, validator.REQUIRED_PATHS)


if __name__ == "__main__":
    unittest.main()
