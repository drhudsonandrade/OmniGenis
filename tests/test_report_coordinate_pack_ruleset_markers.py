from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reportlab.pdfgen.canvas import Canvas

import scripts.build_report_coordinate_pack as coordinate_pack
from reporting.template_v3 import LEGACY_RULESET_TEMPLATE_SOURCE_SHA256
from scripts import pdfium_backend as pdf_backend
from scripts.build_report_coordinate_pack import (
    CANONICAL_RULESET_CONTROL,
    _controls,
    _preserve_legacy_field_semantics,
    _ruleset_control_occurrences,
    _ruleset_control_sources,
    compile_pack,
)


def _write_text_pdf(
    path: Path,
    items: list[tuple[float, float, str]],
    *,
    width: float = 595.0,
    height: float = 842.0,
    font_size: float = 12.0,
) -> None:
    canvas = Canvas(str(path), pagesize=(width, height), pageCompression=0)
    canvas.setFont("Helvetica", font_size)
    for x, y_from_top, text in items:
        canvas.drawString(x, height - y_from_top, text)
    canvas.save()


class ReportCoordinatePackRulesetMarkerTests(unittest.TestCase):
    def test_canonical_marker_is_accepted(self) -> None:
        self.assertEqual(
            _ruleset_control_sources(f"control={CANONICAL_RULESET_CONTROL}"),
            [CANONICAL_RULESET_CONTROL],
        )

    def test_malformed_marker_is_rejected_even_after_valid_marker(self) -> None:
        text = f"{CANONICAL_RULESET_CONTROL}\nGENOMA-RULESET-v"
        with self.assertRaisesRegex(RuntimeError, "malformed GENOMA ruleset control marker"):
            _ruleset_control_sources(text)

    def test_noncanonical_marker_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "noncanonical GENOMA ruleset control marker"):
            _ruleset_control_sources("GENOMA-RULESET-v3.3")

    def test_compiler_uses_the_same_pinned_legacy_marker_digest_as_renderer(self) -> None:
        self.assertEqual(
            getattr(coordinate_pack, "LEGACY_RULESET_CONTROL_SHA256", None),
            LEGACY_RULESET_TEMPLATE_SOURCE_SHA256,
        )

    def test_pinned_legacy_marker_is_normalized_to_canonical_control(self) -> None:
        synthetic = "legacy-template-marker-v1"
        digest = hashlib.sha256(synthetic.encode("utf-8")).hexdigest()
        with (
            mock.patch.object(coordinate_pack, "LEGACY_RULESET_CONTROL_SHA256", digest),
            mock.patch.object(coordinate_pack, "LEGACY_RULESET_CONTROL_LENGTH", len(synthetic)),
            tempfile.TemporaryDirectory() as td,
        ):
            self.assertEqual(
                _ruleset_control_sources(f"before {synthetic} after"),
                [CANONICAL_RULESET_CONTROL],
            )
            pdf = Path(td) / "legacy.pdf"
            _write_text_pdf(pdf, [(72, 72, synthetic)])
            doc = pdf_backend.open_document(pdf)
            page = doc[0]
            try:
                controls = [
                    item for item in _controls(page) if item[0] == CANONICAL_RULESET_CONTROL
                ]
            finally:
                page.close()
                doc.close()
            self.assertEqual(len(controls), 1)

    def test_pinned_legacy_marker_rejects_embedded_boundaries(self) -> None:
        synthetic = "legacy-template-marker-v1"
        digest = hashlib.sha256(synthetic.encode("utf-8")).hexdigest()
        with (
            mock.patch.object(coordinate_pack, "LEGACY_RULESET_CONTROL_SHA256", digest),
            mock.patch.object(coordinate_pack, "LEGACY_RULESET_CONTROL_LENGTH", len(synthetic)),
        ):
            for malformed in (f"x{synthetic}", f"{synthetic}.x"):
                with self.subTest(malformed=malformed), self.assertRaisesRegex(
                    RuntimeError,
                    "malformed pinned legacy ruleset control marker",
                ):
                    _ruleset_control_sources(malformed)

    def test_multiline_canonical_marker_becomes_one_controlled_span(self) -> None:
        first_line = pdf_backend.Rect(72, 60, 180, 74)
        second_line = pdf_backend.Rect(72, 78, 96, 92)
        synthetic_spans = [
            {
                "text": "GENOMA-RULESET-",
                "bbox": first_line,
                "size": 12.0,
                "font": "Synthetic",
                "color": 0,
            },
            {
                "text": "v3.4",
                "bbox": second_line,
                "size": 12.0,
                "font": "Synthetic",
                "color": 0,
            },
        ]
        with mock.patch.object(coordinate_pack, "_spans", return_value=synthetic_spans):
            controls = _ruleset_control_occurrences(object())
        self.assertEqual(len(controls), 1)
        _, rect, _ = controls[0]
        self.assertLessEqual(rect.y0, first_line.y0)
        self.assertGreaterEqual(rect.y1, second_line.y1)

    def test_disjoint_fragments_do_not_form_a_canonical_marker(self) -> None:
        synthetic_spans = [
            {
                "text": "GENOMA-",
                "bbox": pdf_backend.Rect(72, 60, 120, 74),
                "size": 12.0,
                "font": "Synthetic",
                "color": 0,
            },
            {
                "text": "RULESET-v3.4",
                "bbox": pdf_backend.Rect(320, 500, 420, 514),
                "size": 12.0,
                "font": "Synthetic",
                "color": 0,
            },
        ]
        with mock.patch.object(coordinate_pack, "_spans", return_value=synthetic_spans):
            controls = _ruleset_control_occurrences(object())
        self.assertEqual(controls, [])

    def test_report_01_page_06_legacy_field_semantics_preserve_order(self) -> None:
        combined = "[[SYNTHETIC_COMBINED]]"
        calibration = "[[CALIBRATION]]"
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "legacy-01.pdf"
            _write_text_pdf(
                pdf,
                [
                    (72, 72, "[[IMPACTO_CLINICO]]"),
                    (72, 96, calibration),
                ],
                width=300,
                height=400,
            )
            doc = pdf_backend.open_document(pdf)
            page = doc[0]
            meta = {"size": 8.0, "font": "Synthetic", "color": 0}
            tokens = [
                (combined, pdf_backend.Rect(10, 10, 20, 20), meta),
                ("[[INDICACAO]]", pdf_backend.Rect(20, 10, 30, 20), meta),
                ("[[STATUS]]", pdf_backend.Rect(30, 10, 40, 20), meta),
            ]
            try:
                with (
                    mock.patch.object(
                        coordinate_pack,
                        "LEGACY_REPORT_01_PAGE_06_COMBINED_SHA256",
                        hashlib.sha256(combined.encode()).hexdigest(),
                    ),
                    mock.patch.object(
                        coordinate_pack,
                        "LEGACY_REPORT_01_PAGE_06_CALIBRATION_SHA256",
                        hashlib.sha256(calibration.encode()).hexdigest(),
                    ),
                    mock.patch.object(
                        coordinate_pack,
                        "LEGACY_REPORT_01_PAGE_06_CALIBRATION_LENGTH",
                        len(calibration),
                    ),
                ):
                    rebuilt = _preserve_legacy_field_semantics("01", 6, page, tokens)
            finally:
                page.close()
                doc.close()
        self.assertEqual(
            [item[0] for item in rebuilt],
            ["[[IMPACTO_CLINICO]]", "[[STATUS]]", calibration],
        )

    def test_report_01_page_06_legacy_field_semantics_fail_closed_on_bad_counts(self) -> None:
        combined = "[[SYNTHETIC_COMBINED]]"
        calibration = "[[CALIBRATION]]"
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "legacy-01-fail.pdf"
            _write_text_pdf(
                pdf,
                [(72, 72, "[[IMPACTO_CLINICO]]"), (72, 96, calibration)],
                width=300,
                height=400,
            )
            doc = pdf_backend.open_document(pdf)
            page = doc[0]
            meta = {"size": 8.0, "font": "Synthetic", "color": 0}
            base = [
                (combined, pdf_backend.Rect(10, 10, 20, 20), meta),
                ("[[STATUS]]", pdf_backend.Rect(30, 10, 40, 20), meta),
            ]
            try:
                with (
                    mock.patch.object(
                        coordinate_pack,
                        "LEGACY_REPORT_01_PAGE_06_COMBINED_SHA256",
                        hashlib.sha256(combined.encode()).hexdigest(),
                    ),
                    mock.patch.object(
                        coordinate_pack,
                        "LEGACY_REPORT_01_PAGE_06_CALIBRATION_SHA256",
                        hashlib.sha256(calibration.encode()).hexdigest(),
                    ),
                    mock.patch.object(
                        coordinate_pack,
                        "LEGACY_REPORT_01_PAGE_06_CALIBRATION_LENGTH",
                        len(calibration),
                    ),
                ):
                    with self.assertRaisesRegex(RuntimeError, "legacy field compatibility mismatch"):
                        _preserve_legacy_field_semantics("01", 6, page, base)
                    duplicated = base + [
                        (combined, pdf_backend.Rect(40, 10, 50, 20), meta),
                        ("[[INDICACAO]]", pdf_backend.Rect(50, 10, 60, 20), meta),
                    ]
                    with self.assertRaisesRegex(RuntimeError, "legacy field compatibility mismatch"):
                        _preserve_legacy_field_semantics("01", 6, page, duplicated)
            finally:
                page.close()
                doc.close()

    def test_report_07_page_05_legacy_field_semantics_preserve_identifier(self) -> None:
        combined = "[[MONOGENICO_PRS_ASSOCIACAO]][[IDENTIFICADOR]]"
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "legacy-07.pdf"
            _write_text_pdf(pdf, [(72, 72, "[[IDENTIFICADOR]]")], width=300, height=400)
            doc = pdf_backend.open_document(pdf)
            page = doc[0]
            meta = {"size": 8.0, "font": "Synthetic", "color": 0}
            try:
                rebuilt = _preserve_legacy_field_semantics(
                    "07",
                    5,
                    page,
                    [(combined, pdf_backend.Rect(10, 10, 20, 20), meta)],
                )
                self.assertEqual([item[0] for item in rebuilt], ["[[IDENTIFICADOR]]"])
                with self.assertRaisesRegex(RuntimeError, "legacy field compatibility mismatch"):
                    _preserve_legacy_field_semantics("07", 5, page, [])
                with self.assertRaisesRegex(RuntimeError, "legacy field compatibility mismatch"):
                    _preserve_legacy_field_semantics(
                        "07",
                        5,
                        page,
                        [
                            (combined, pdf_backend.Rect(10, 10, 20, 20), meta),
                            (combined, pdf_backend.Rect(20, 10, 30, 20), meta),
                        ],
                    )
            finally:
                page.close()
                doc.close()

    def test_compile_pack_fails_closed_if_canonical_occurrence_lacks_controlled_span(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            template_dir = root / "templates"
            template_dir.mkdir()
            pdf = template_dir / "report.pdf"
            _write_text_pdf(
                pdf,
                [(72, 72, CANONICAL_RULESET_CONTROL)],
                width=300,
                height=400,
            )
            raw = pdf.read_bytes()
            reference = root / "reference.json"
            reference.write_text(
                json.dumps(
                    {
                        "reports": {
                            "01": {
                                "filename": pdf.name,
                                "sha256": hashlib.sha256(raw).hexdigest(),
                                "size_bytes": len(raw),
                                "page_count": 1,
                                "page_size_pt": [300, 400],
                                "placeholder_count": 0,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch("scripts.build_report_coordinate_pack._controls", return_value=[]):
                with self.assertRaisesRegex(RuntimeError, "canonical ruleset controlled-span mismatch"):
                    compile_pack(template_dir, reference)


if __name__ == "__main__":
    unittest.main()
