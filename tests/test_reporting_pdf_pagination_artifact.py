"""Exact synthetic opt-in PDF pagination artifacts; no default activation."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import os
from unittest import TestCase
from unittest.mock import patch

from pypdf import PdfReader
from pypdf.generic import ContentStream, NameObject

from reporting import _pdf_pagination_core as core
from reporting._pdf_pagination_core import (
    BASE_CSS, CSS, PINNED, inject_pagination_css, renderer_profile,
)
from reporting.pdf_pagination_artifact import (
    PaginationArtifactError, PaginationCandidate,
    observe_paginated_candidate, render_paginated_candidate,
)
from reporting.typed_html import prepare_typed_html
from reporting.pdf_typed_observation import TypedPdfObservationError, observe_typed_pdf
from test_reporting_typed_pdf_association import declarations
from test_reporting_typed_pdf_source import digest


class PaginationArtifactTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.presentation = declarations("long")
        cls.typed_html, _ = prepare_typed_html(
            presentation_ir=cls.presentation,
            expected_ir_sha256=digest(cls.presentation))
        cls.candidate = render_paginated_candidate(cls.typed_html)
        cls.observed = observe_paginated_candidate(cls.candidate)

    def test_every_page_has_literal_footer_artifact_and_matching_label(self):
        candidate = self.candidate
        self.assertEqual(candidate.page_count, 3)
        self.assertEqual(self.observed["literal_artifact_numbers"], ["1", "2", "3"])
        self.assertEqual(self.observed["semantic_mcids"], 202)
        self.assertEqual(self.observed["pdf_sha256"], candidate.pdf_sha256)
        self.assertFalse(self.observed["default_pipeline_activated"])
        self.assertFalse(self.observed["release_authorized"])
        reader = PdfReader(BytesIO(candidate.pdf_bytes), strict=True)
        self.assertEqual(reader.page_labels, ["1", "2", "3"])
        for page in reader.pages:
            events = ContentStream(page["/Contents"], reader).operations
            artefacts = [
                arguments[1] for arguments, op in events
                if op == b"BDC" and arguments[0] == NameObject("/Artifact")]
            self.assertEqual(len(artefacts), 1)
            self.assertEqual(artefacts[0]["/Type"], "/Pagination")
            self.assertEqual(artefacts[0]["/Subtype"], "/Footer")

    def test_five_page_dense_row_has_all_five_pagination_artifacts(self):
        source = declarations("row")
        typed, _ = prepare_typed_html(
            presentation_ir=source, expected_ir_sha256=digest(source))
        candidate = render_paginated_candidate(typed)
        result = observe_paginated_candidate(candidate)
        self.assertEqual(candidate.page_count, 5)
        self.assertEqual(result["literal_artifact_numbers"], ["1", "2", "3", "4", "5"])
        self.assertFalse(result["reading_order_verified"])
        self.assertFalse(result["accessibility_qualified"])

    def test_original_observer_remains_strict_and_unmodified(self):
        with self.assertRaises(TypedPdfObservationError):
            observe_typed_pdf(
                pdf_bytes=self.candidate.pdf_bytes,
                expected_pdf_sha256=self.candidate.pdf_sha256)

    def test_page_label_tampering_fails_closed(self):
        # Synthetic negative only. The production renderer never rewrites PDFs.
        from pypdf import PdfWriter

        writer = PdfWriter(clone_from=BytesIO(self.candidate.pdf_bytes))
        writer._header = b"%PDF-1.7"
        labels = writer._root_object["/PageLabels"]["/Nums"][1]
        labels[NameObject("/S")] = NameObject("/R")
        buffer = BytesIO()
        writer.write(buffer)
        tampered = buffer.getvalue()
        self.assertTrue(tampered.startswith(b"%PDF-1.7"))
        self.assertNotEqual(PdfReader(BytesIO(tampered)).page_labels, ["1", "2", "3"])
        negative = PaginationCandidate(
            tampered, sha256(tampered).hexdigest(), self.candidate.html_sha256,
            self.candidate.page_count, self.candidate.profile_sha256)
        with self.assertRaises(PaginationArtifactError):
            observe_paginated_candidate(negative)

    def test_source_manifest_drift_refuses_before_launch(self):
        with patch.object(core, "_SOURCE_FILES", ("reporting/unknown.py",)):
            with self.assertRaises(PaginationArtifactError):
                render_paginated_candidate(self.typed_html)

    def test_disable_switch_and_candidate_integrity(self):
        with patch.dict(os.environ, {"OMNIGENIS_PDF_PAGINATION_ARTIFACT_DISABLED": "1"}):
            with self.assertRaises(PaginationArtifactError):
                render_paginated_candidate(self.typed_html)
            with self.assertRaises(PaginationArtifactError):
                observe_paginated_candidate(self.candidate)
        tampered = PaginationCandidate(
            self.candidate.pdf_bytes + b"x", self.candidate.pdf_sha256,
            self.candidate.html_sha256, self.candidate.page_count,
            self.candidate.profile_sha256)
        with self.assertRaises(PaginationArtifactError):
            observe_paginated_candidate(tampered)

    def test_separate_closed_profile_and_css_contract(self):
        profile = renderer_profile()
        self.assertFalse(profile["default_pipeline_activated"])
        self.assertFalse(profile["release_authorized"])
        self.assertEqual(profile["memory_bytes"], 1073741824)
        self.assertEqual(profile["cpu_seconds"], 20)
        self.assertEqual(profile["max_pages"], 64)
        self.assertEqual(profile["renderer_pins"], PINNED)
        self.assertIn(CSS, inject_pagination_css(BASE_CSS))
        for input_value in ("", CSS, BASE_CSS * 2,
                            BASE_CSS + "@bottom-right{content:foo}"):
            with self.assertRaises(ValueError):
                inject_pagination_css(input_value)
