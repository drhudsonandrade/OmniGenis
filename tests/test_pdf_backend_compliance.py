from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reportlab.lib.colors import Color
from reportlab.pdfgen import canvas

import scripts.pdfium_backend as pdf_backend
from scripts.build_report_coordinate_pack import compile_pack
from reporting.template_v3 import TemplateV3Error, _field_value

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = json.loads((ROOT / "reporting" / "reference_v3_manifest.json").read_text(encoding="utf-8"))
MIGRATION_PATH = ROOT / "docs" / "evidence" / "PDFIUM_COORDINATE_MIGRATION_2026-09-17.json"
MIGRATION = json.loads(MIGRATION_PATH.read_text(encoding="utf-8"))
PIXEL_QA_PATH = ROOT / "docs" / "evidence" / "PDFIUM_STATIC_PIXEL_QA_200DPI_2026-09-17.json"
LEGACY_PIXEL_QA = json.loads((ROOT / "docs" / "evidence" / "EDITORIAL_V3_STATIC_PIXEL_QA_200DPI_2026-08-16.json").read_text(encoding="utf-8"))


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
                self.assertNotIn("pymupdf", text.lower())
                self.assertIn("pdfium", text.lower())

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

    def test_pdfium_pixel_qa_is_independent_and_legacy_evidence_is_preserved(self) -> None:
        self.assertTrue(PIXEL_QA_PATH.is_file())
        raw = PIXEL_QA_PATH.read_bytes()
        qa = json.loads(raw.decode("utf-8"))
        self.assertEqual(qa["status"], LEGACY_PIXEL_QA["status"])
        self.assertEqual(qa["aggregate"], {
            "outside_changed_pixels": 0,
            "reference_pages": 100,
            "reports": 11,
            "result": "PASS",
        })
        self.assertEqual(qa["coordinate_compiler"]["id"], "pypdfium2-5.13.0-pdfium-genoma-v3")
        self.assertEqual(
            qa["coordinate_compiler"]["manifest_sha256"],
            REFERENCE["generated_coordinate_manifest"]["sha256"],
        )
        self.assertEqual(MIGRATION["pixel_qa"]["pdfium_candidate_status"], "PASS")
        self.assertEqual(MIGRATION["pixel_qa"]["evidence_sha256"], hashlib.sha256(raw).hexdigest())
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

    def test_lock_accepts_only_audited_linux_x86_64_pdfium_wheel(self) -> None:
        lock = (ROOT / "reporting" / "requirements.txt").read_text(encoding="utf-8")
        block = lock.split("pypdfium2==5.13.0", 1)[1].split("\n    # via", 1)[0]
        hashes = [line.split("sha256:", 1)[1].strip(" \\\n") for line in block.splitlines() if "--hash=sha256:" in line]
        self.assertEqual(
            hashes,
            ["81df25c1ab4c13ff773102d3cbea1967511d079123b067fc077bd0c4d57d91d8"],
        )

    def test_pixmap_closes_native_pdfium_bitmap_after_copy(self) -> None:
        class FakeImage:
            width = 2
            height = 1

            def convert(self, mode: str):
                self.assert_mode = mode
                return self

            def tobytes(self) -> bytes:
                return b"\x01\x02\x03\x04\x05\x06"

            def close(self) -> None:
                self.closed = True

        image = FakeImage()
        bitmap = mock.Mock()
        bitmap.to_pil.return_value = image
        raw_page = mock.Mock()
        raw_page.render.return_value = bitmap
        page = pdf_backend.Page(raw_page)
        pixmap = page.get_pixmap(
            matrix=pdf_backend.Matrix(1, 1),
            alpha=False,
            colorspace=pdf_backend.csRGB,
        )
        self.assertEqual(pixmap.samples, b"\x01\x02\x03\x04\x05\x06")
        bitmap.close.assert_called_once_with()

    def test_text_extraction_fails_closed_on_character_geometry_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "text.pdf"
            pdf = canvas.Canvas(str(pdf_path), pagesize=(300, 400))
            pdf.drawString(30, 340, "[[CASE_ID]]")
            pdf.save()
            doc = pdf_backend.open_document(pdf_path)
            page = doc[0]
            try:
                with (
                    mock.patch.object(
                        pdf_backend,
                        "_legacy_char_rect",
                        side_effect=RuntimeError("geometry failed"),
                    ),
                    self.assertRaisesRegex(RuntimeError, "geometry failed"),
                ):
                    page.get_text("dict", sort=True)
            finally:
                page.close()
                doc.close()

    def test_search_fails_closed_on_character_geometry_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "search.pdf"
            pdf = canvas.Canvas(str(pdf_path), pagesize=(300, 400))
            pdf.drawString(30, 340, "CONTROL")
            pdf.save()
            doc = pdf_backend.open_document(pdf_path)
            page = doc[0]
            try:
                with (
                    mock.patch.object(
                        pdf_backend,
                        "_legacy_char_rect",
                        side_effect=RuntimeError("search geometry failed"),
                    ),
                    self.assertRaisesRegex(RuntimeError, "search geometry failed"),
                ):
                    page.search_for("CONTROL")
            finally:
                page.close()
                doc.close()

    def test_drawing_extraction_fails_closed_on_path_geometry_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "drawing.pdf"
            pdf = canvas.Canvas(str(pdf_path), pagesize=(300, 400))
            pdf.rect(20, 300, 260, 60, fill=0, stroke=1)
            pdf.save()
            doc = pdf_backend.open_document(pdf_path)
            page = doc[0]
            try:
                with (
                    mock.patch.object(
                        pdf_backend,
                        "_path_geometry_rect",
                        side_effect=RuntimeError("path geometry failed"),
                    ),
                    self.assertRaisesRegex(RuntimeError, "path geometry failed"),
                ):
                    page.get_drawings()
            finally:
                page.close()
                doc.close()

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
            "docs/evidence/PDFIUM_STATIC_PIXEL_QA_200DPI_2026-09-17.json",
        ):
            with self.subTest(path=relative):
                self.assertIn(relative, validator.REQUIRED_PATHS)

    def test_visual_qa_workflow_requires_pdfium_pixel_evidence(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "genoma-visual-qa-candidates.yml").read_text(encoding="utf-8")
        self.assertIn("PDFIUM_STATIC_PIXEL_QA_200DPI_2026-09-17.json", workflow)
        self.assertIn("pdfium_candidate_status'] == 'PASS'", workflow)
        self.assertNotIn("pdfium_candidate_status'] == 'NOT_EXECUTED'", workflow)


if __name__ == "__main__":
    unittest.main()
