"""Qualification-only editorial styling of already bound typed reports."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import importlib
import os
import unittest
from unittest.mock import patch

from pypdf import PdfReader
from reporting.typed_html import prepare_typed_html
from test_reporting_typed_pdf_source import document, digest


class EditorialTypedPdfTests(unittest.TestCase):
    def setUp(self):
        self.m = importlib.import_module("reporting.editorial_typed_pdf")
        self.source = document(2)
        self.expected = digest(self.source)

    def test_styling_preserves_every_original_typed_body_byte(self):
        original, proof = prepare_typed_html(
            presentation_ir=self.source, expected_ir_sha256=self.expected)
        html, record = self.m.prepare_editorial_typed_html(
            presentation_ir=self.source, expected_ir_sha256=self.expected)
        self.assertEqual(html.split("<body>", 1)[1], original.split("<body>", 1)[1])
        self.assertEqual(record["typed_html_sha256"], proof.html_sha256)
        self.assertEqual(record["source_ir_sha256"], self.expected)
        self.assertEqual(record["html_sha256"], sha256(html.encode("utf-8")).hexdigest())
        self.assertIn("thead{display:table-header-group}", html)
        self.assertIn("break-inside:avoid", html)
        self.assertIn("#003890", html)
        self.assertIn("#00D0F0", html)
        self.assertIn('scope="col"', html)
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        for key in ("release_authorized", "default_pipeline_activated",
                    "visual_equivalence_verified", "human_accessibility_verified",
                    "whole_job_resources_qualified"):
            self.assertIs(record[key], False)

    def test_existing_renderer_makes_bounded_nonreleased_readable_pdf(self):
        candidate, record = self.m.render_editorial_typed_pdf(
            presentation_ir=self.source, expected_ir_sha256=self.expected)
        pdf = candidate.pdf_bytes
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertEqual(sha256(pdf).hexdigest(), candidate.pdf_sha256)
        self.assertEqual(record["pdf_sha256"], candidate.pdf_sha256)
        self.assertEqual(record["page_count"], candidate.page_count)
        self.assertGreater(candidate.page_count, 0)
        self.assertLessEqual(candidate.page_count, 64)
        reader = PdfReader(BytesIO(pdf), strict=True)
        self.assertEqual(len(reader.pages), candidate.page_count)
        extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
        for expected in ("Technical report", "Declared findings",
                         "All declared synthetic findings", "Variant", "Value",
                         "NM_000001.1:c.1A>G", "NM_000001.1:c.2A>G"):
            self.assertIn(expected, extracted)
        self.assertNotIn('"canonical_interpretation.items"', extracted)
        self.assertFalse(record["release_authorized"])

    def test_multipage_table_preserves_every_admitted_row_and_headers(self):
        """Content length reflows; no row is removed to fit a fixed page."""
        source = document(80)
        candidate, record = self.m.render_editorial_typed_pdf(
            presentation_ir=source, expected_ir_sha256=digest(source))
        self.assertGreater(candidate.page_count, 1)
        reader = PdfReader(BytesIO(candidate.pdf_bytes), strict=True)
        self.assertEqual(len(reader.pages), candidate.page_count)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        for row in range(1, 81):
            with self.subTest(row=row):
                self.assertEqual(text.count(f"NM_000001.1:c.{row}A>G"), 1)
        self.assertGreaterEqual(text.count("Variant"), candidate.page_count)
        self.assertFalse(record["release_authorized"])

    def test_oversized_table_row_repeats_header_on_every_page(self):
        """One admitted multiline row may split without abandoning table headings."""
        source = document(1, cells=("ROW-BEGIN\n" + "line\n" * 60 + "ROW-END", "0.1"))
        candidate, record = self.m.render_editorial_typed_pdf(
            presentation_ir=source, expected_ir_sha256=digest(source))
        self.assertGreater(candidate.page_count, 1)
        reader = PdfReader(BytesIO(candidate.pdf_bytes), strict=True)
        pages = [page.extract_text() or "" for page in reader.pages]
        self.assertIn("ROW-BEGIN", pages[0])
        self.assertIn("ROW-END", "\n".join(pages))
        self.assertEqual(sum(page.count("ROW-BEGIN") for page in pages), 1)
        self.assertEqual(sum(page.count("ROW-END") for page in pages), 1)
        for index, page in enumerate(pages, 1):
            with self.subTest(page=index):
                self.assertIn("Variant", page)
                self.assertIn("Value", page)
        self.assertFalse(record["release_authorized"])

    def test_wrong_digest_and_noncanonical_inputs_refuse_without_render(self):
        with patch.object(self.m, "render_pdf_candidate",
                          side_effect=AssertionError("render reached")) as render:
            for bad in ("0" * 64, "not-a-digest", None):
                with self.subTest(expected=bad):
                    with self.assertRaises(self.m.EditorialTypedPdfError):
                        self.m.render_editorial_typed_pdf(
                            presentation_ir=self.source, expected_ir_sha256=bad)
            render.assert_not_called()

    def test_disabled_profile_refuses_before_render(self):
        with patch.dict(os.environ, {"OMNIGENIS_TYPED_EDITORIAL_PDF_DISABLED": "1"}):
            with patch.object(self.m, "render_pdf_candidate",
                              side_effect=AssertionError("unexpected render")) as render:
                with self.assertRaises(self.m.EditorialTypedPdfError):
                    self.m.render_editorial_typed_pdf(
                        presentation_ir=self.source, expected_ir_sha256=self.expected)
                render.assert_not_called()

    def test_repeat_prepare_is_deterministic_without_modifying_input(self):
        initial = digest(self.source)
        html1, record1 = self.m.prepare_editorial_typed_html(
            presentation_ir=self.source, expected_ir_sha256=initial)
        html2, record2 = self.m.prepare_editorial_typed_html(
            presentation_ir=self.source, expected_ir_sha256=initial)
        self.assertEqual(html1, html2)
        self.assertEqual(record1, record2)
        record1["release_authorized"] = True
        self.assertFalse(record2["release_authorized"])
        self.assertEqual(digest(self.source), initial)


if __name__ == "__main__":
    unittest.main()
