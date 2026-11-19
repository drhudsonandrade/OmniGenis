"""Opt-in original OmniGenis branding over an admitted typed editorial PDF."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import importlib
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from pypdf import PdfReader

from reporting.brand_assets import BoundBrandAssets, REQUIRED_CREDIT_EN
from reporting.components import (TextComponent, FindingsTable, FindingRow,
                                  TableColumn, build_typed_report_projection,
                                  component_content_sha256)
from test_reporting_typed_projection import context
from reporting.editorial_typed_pdf import prepare_editorial_typed_html
from reporting.pdf_raster_resources import RasterAsset
from test_reporting_typed_pdf_source import document, digest


ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = ROOT / "reporting" / "assets"


class TypedOriginalBrandPdfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = {
            "logo_svg": (ASSET_ROOT / "omnigenis_logo.svg").read_bytes(),
            "dna_png": (ASSET_ROOT / "dna_column.png").read_bytes(),
            "permission_notice": (ASSET_ROOT / "brand-permission.md").read_bytes(),
        }
        cls.assets = BoundBrandAssets(**cls.sources)
        cls.bundle_sha = cls.assets.to_dict()["bundle_sha256"]

    def setUp(self):
        self.m = importlib.import_module("reporting.typed_original_brand_pdf")
        self.source = document(2)
        self.expected_ir_sha = digest(self.source)
        self.mode = patch.dict(os.environ, {
            "OMNIGENIS_PDF_QUALIFICATION_RECEIPT_MODE": "qualification-only",
            "OMNIGENIS_TYPED_ORIGINAL_BRAND_PDF_DISABLED": "",
            "OMNIGENIS_TYPED_BRAND_RENDER_DISABLED": "",
            "OMNIGENIS_TYPED_EDITORIAL_PDF_DISABLED": "",
            "OMNIGENIS_PDF_PRELOADED_RASTER_DISABLED": "",
            "OMNIGENIS_PDF_QUALIFICATION_RECEIPT_DISABLED": "",
            "OMNIGENIS_PDF_RENDERER_DISABLED": "",
        })
        self.mode.start()
        self.addCleanup(self.mode.stop)

    def arguments(self):
        return {
            "presentation_ir": self.source,
            "expected_ir_sha256": self.expected_ir_sha,
            "assets": self.assets,
            "expected_brand_bundle_sha256": self.bundle_sha,
        }

    def test_exact_original_asset_bytes_and_one_credit_are_in_prepared_markup(self):
        typed_html, typed_record = prepare_editorial_typed_html(
            presentation_ir=self.source, expected_ir_sha256=self.expected_ir_sha)
        html, resources, record = self.m.prepare_typed_original_brand_pdf(
            **self.arguments())
        self.assertEqual(record["typed_editorial_html_sha256"],
                         typed_record["html_sha256"])
        self.assertEqual(html.count(self.sources["logo_svg"].decode("utf-8")), 1)
        self.assertEqual(html.count(REQUIRED_CREDIT_EN), 1)
        self.assertEqual(html.count(resources.assets[0].uri), 1)
        self.assertIn('data-brand="original-logo"', html)
        self.assertIn("Technical report", html)
        self.assertIn("All declared synthetic findings", html)
        self.assertIn('scope="col"', html)
        self.assertEqual(record["logo_original_sha256"],
                         sha256(self.sources["logo_svg"]).hexdigest())
        self.assertEqual(record["dna_original_sha256"],
                         sha256(self.sources["dna_png"]).hexdigest())
        self.assertEqual(record["html_sha256"], sha256(html.encode()).hexdigest())
        self.assertEqual(record["brand_bundle_sha256"], self.bundle_sha)
        self.assertEqual(resources.assets[0].png_bytes, self.sources["dna_png"])
        self.assertIs(type(resources.assets[0]), RasterAsset)
        self.assertEqual(typed_html.count("<table"), html.count("<table"))
        for flag in ("release_authorized", "default_pipeline_activated",
                     "visual_equivalence_verified", "human_accessibility_verified",
                     "whole_job_resources_qualified", "pdf_ua_certified"):
            self.assertIs(record[flag], False)

    def test_originals_and_semantics_survive_single_typed_brand_render(self):
        pdf, record = self.m.render_typed_original_brand_pdf(**self.arguments())
        self.assertTrue(pdf.pdf_bytes.startswith(b"%PDF-1.7"))
        self.assertEqual(record["render_profile_id"], "typed-original-brand-render-v1")
        self.assertEqual(pdf.to_dict()["profile_id"], record["render_profile_id"])
        self.assertEqual(pdf.to_dict()["profile_sha256"], record["render_profile_sha256"])
        self.assertEqual(sha256(pdf.pdf_bytes).hexdigest(), pdf.pdf_sha256)
        self.assertEqual(record["pdf_sha256"], pdf.pdf_sha256)
        self.assertGreater(pdf.page_count, 0)
        self.assertLessEqual(pdf.page_count, 64)
        reader = PdfReader(BytesIO(pdf.pdf_bytes), strict=True)
        self.assertEqual(len(reader.pages), pdf.page_count)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        for literal in ("Technical report", "Declared findings",
                        "All declared synthetic findings", "Variant", "Value",
                        "NM_000001.1:c.1A>G", "NM_000001.1:c.2A>G"):
            self.assertIn(literal, text)
        self.assertEqual(text.count(REQUIRED_CREDIT_EN), 1)
        self.assertFalse(record["release_authorized"])
        self.assertFalse(record["human_accessibility_verified"])

    def test_disable_retained_during_source_postflight_withholds_candidate(self):
        from reporting import _pdf_typed_brand_emission as emission
        from reporting.pdf_typed_brand_render import TypedBrandPdfCandidate
        prepare = self.m.prepare_typed_original_brand_pdf
        calls = []
        def preparation(**kwargs):
            value = prepare(**kwargs)
            calls.append(True)
            if len(calls) == 2:
                os.environ["OMNIGENIS_TYPED_BRAND_RENDER_DISABLED"] = "1"
            return value
        def sealed(**kwargs):
            return TypedBrandPdfCandidate(b"%PDF-1.7 synthetic boundary fixture",
                kwargs["expected_html_sha256"], kwargs["expected_resources_sha256"],
                1, 1, emission.profile_sha256())
        with (patch.object(self.m, "prepare_typed_original_brand_pdf", side_effect=preparation),
              patch.object(self.m, "render_typed_brand_pdf", side_effect=sealed),
              self.assertRaises(self.m.TypedOriginalBrandError)):
            self.m.render_typed_original_brand_pdf(**self.arguments())
        self.assertEqual(len(calls), 2)

    def test_nested_render_refuses_before_preparation_and_poisons_next_valid_launch(self):
        from reporting._pdf_native_launch_budget import NativeLaunchScope
        from reporting.page_windows import plan_pdf_page_windows
        from test_reporting_pdf_native_launch_budget import launch_one, launch_specs
        sample = b"%PDF-1.7 synthetic"
        plan = plan_pdf_page_windows(pdf_sha256=sha256(sample).hexdigest(), ir_sha256="a"*64,
            page_count=1, admitted_page_budget=1, pages_per_window=16,
            inspector_profile_id="generated-pdf-text-window-v1")
        with (NativeLaunchScope(plan, len(sample)),
              patch.object(self.m, "prepare_typed_original_brand_pdf", wraps=self.m.prepare_typed_original_brand_pdf) as prepare,
              patch.object(self.m, "render_typed_brand_pdf") as render):
            with self.assertRaisesRegex(self.m.TypedOriginalBrandError, "^typed_original_brand_pdf_unavailable$"):
                self.m.render_typed_original_brand_pdf(**(self.arguments() | {"expected_brand_bundle_sha256": None}))
            prepare.assert_not_called()
            render.assert_not_called()
            with self.assertRaises(ValueError):
                launch_one(launch_specs(plan)[0], sample)

    def test_variable_length_content_remains_complete_with_original_assets(self):
        """Brand composition never truncates the source's admitted finding rows."""
        source = document(60)
        pdf, record = self.m.render_typed_original_brand_pdf(
            **(self.arguments() | {"presentation_ir": source,
                                   "expected_ir_sha256": digest(source)}))
        reader = PdfReader(BytesIO(pdf.pdf_bytes), strict=True)
        pages = [page.extract_text() or "" for page in reader.pages]
        text = "\n".join(pages)
        for i in range(1, 61):
            with self.subTest(row=i):
                self.assertEqual(text.count(f"NM_000001.1:c.{i}A>G"), 1)
        for page in pages:
            if "NM_000001.1:c." in page:
                self.assertIn("Variant", page)
                self.assertIn("Value", page)
        self.assertEqual(text.count(REQUIRED_CREDIT_EN), 1)
        self.assertGreater(pdf.page_count, 2)
        self.assertFalse(record["release_authorized"])

    def test_required_credit_is_in_a_labeled_section_on_the_same_page(self):
        """The source permission requires a credits/information section."""
        html, _, _ = self.m.prepare_typed_original_brand_pdf(**self.arguments())
        label = '<section data-brand-credit-section="original"'
        self.assertEqual(html.count(label), 1)
        credit_section = html.split(label, 1)[1].split("</section>", 1)[0]
        self.assertIn("Visual asset credits", credit_section)
        self.assertEqual(credit_section.count(REQUIRED_CREDIT_EN), 1)
        pdf, _ = self.m.render_typed_original_brand_pdf(**self.arguments())
        pages = [page.extract_text() or ""
                 for page in PdfReader(BytesIO(pdf.pdf_bytes), strict=True).pages]
        self.assertTrue(any("Visual asset credits" in page and
                            REQUIRED_CREDIT_EN in page for page in pages))

    def test_raster_uri_in_admitted_plain_text_does_not_become_a_resource(self):
        """A declared literal URI in a paragraph is not a second CSS fetch."""
        dna_sha = sha256(self.sources["dna_png"]).hexdigest()
        source = document(1, paragraph="Synthetic literal: omnigenis-raster:" + dna_sha)
        html, resources, _ = self.m.prepare_typed_original_brand_pdf(
            **(self.arguments() | {"presentation_ir": source,
                                   "expected_ir_sha256": digest(source)}))
        uri = resources.assets[0].uri
        self.assertIn("Synthetic literal: " + uri, html)
        self.assertEqual(html.count("background-image:url('" + uri + "')"), 1)

    def test_wrapped_duplicate_credit_is_refused_before_render(self):
        """Whitespace cannot disguise an existing required legal notice."""
        wrapped = REQUIRED_CREDIT_EN.replace(". Used", ".\nUsed")
        source = document(1, paragraph=wrapped)
        with patch.object(self.m, "render_typed_brand_pdf",
                          side_effect=AssertionError("unexpected render")) as render:
            with self.assertRaises(self.m.TypedOriginalBrandError):
                self.m.render_typed_original_brand_pdf(
                    **(self.arguments() | {"presentation_ir": source,
                                           "expected_ir_sha256": digest(source)}))
            render.assert_not_called()

    def test_credit_split_across_adjacent_paragraphs_refuses_pre_render(self):
        """Two adjacent typed paragraphs cannot hide a duplicate legal notice."""
        request, ledger = context(1, 'SYNTHETIC-CASE')
        first, second = REQUIRED_CREDIT_EN.split('. Used ', 1)
        row_id = request.plan.expected_finding_ids[0]
        parts = (
            TextComponent('title', 'identity', 'document_title', 'Technical report'),
            TextComponent('heading', 'findings', 'section_heading', 'Declared findings'),
            TextComponent('credit-first-half', 'findings', 'paragraph', first + '.'),
            TextComponent('credit-second-half', 'findings', 'paragraph', 'Used ' + second),
            FindingsTable('table', 'findings', 'Synthetic findings',
                (TableColumn('variant', 'Variant'), TableColumn('value', 'Value')),
                (FindingRow(row_id, ('NM_000001.1:c.1A>G', '0.1')),)),
        )
        source = build_typed_report_projection(
            request=request, ledger=ledger,
            projection_request_sha256=request.request.request_sha256,
            expected_content_sha256=component_content_sha256(parts),
            components=parts).to_dict()
        kwargs = self.arguments() | {'presentation_ir': source,
                                     'expected_ir_sha256': digest(source)}
        with patch.object(self.m, 'render_typed_brand_pdf',
                          side_effect=AssertionError('unexpected render')) as renderer:
            with self.assertRaises(self.m.TypedOriginalBrandError):
                self.m.prepare_typed_original_brand_pdf(**kwargs)
            with self.assertRaises(self.m.TypedOriginalBrandError):
                self.m.render_typed_original_brand_pdf(**kwargs)
            renderer.assert_not_called()

    def test_soft_hyphen_hidden_duplicate_credit_refuses_before_render(self):
        """An invisible U+00AD cannot conceal a duplicate owner's notice."""
        paragraph = REQUIRED_CREDIT_EN.replace("Logo", "Lo\u00adgo", 1)
        source = document(1, paragraph=paragraph)
        kwargs = self.arguments() | {
            "presentation_ir": source,
            "expected_ir_sha256": digest(source),
        }
        with patch.object(self.m, "render_typed_brand_pdf",
                          side_effect=AssertionError("unexpected render")) as render:
            with self.assertRaises(self.m.TypedOriginalBrandError):
                self.m.prepare_typed_original_brand_pdf(**kwargs)
            with self.assertRaises(self.m.TypedOriginalBrandError):
                self.m.render_typed_original_brand_pdf(**kwargs)
            render.assert_not_called()

    def test_carriage_return_encoded_duplicate_credit_is_refused(self):
        """An HTML-encoded carriage return cannot hide a second notice."""
        wrapped = REQUIRED_CREDIT_EN.replace(". Used", ".\rUsed")
        source = document(1, paragraph=wrapped)
        with patch.object(self.m, "render_typed_brand_pdf",
                          side_effect=AssertionError("unexpected render")) as render:
            with self.assertRaises(self.m.TypedOriginalBrandError):
                self.m.render_typed_original_brand_pdf(
                    **(self.arguments() | {"presentation_ir": source,
                                           "expected_ir_sha256": digest(source)}))
            render.assert_not_called()

    def test_positive_fixture_is_independent_of_inherited_disable_flags(self):
        """setUp explicitly supplies the opt-in test environment."""
        flags = (
            "OMNIGENIS_TYPED_ORIGINAL_BRAND_PDF_DISABLED",
            "OMNIGENIS_TYPED_EDITORIAL_PDF_DISABLED",
            "OMNIGENIS_PDF_PRELOADED_RASTER_DISABLED",
            "OMNIGENIS_PDF_QUALIFICATION_RECEIPT_DISABLED",
            "OMNIGENIS_PDF_RENDERER_DISABLED",
        )
        with patch.dict(os.environ, dict.fromkeys(flags, "1")):
            case = type(self)("test_exact_original_asset_bytes_and_one_credit_are_in_prepared_markup")
            case.setUp()
            try:
                html, _, record = case.m.prepare_typed_original_brand_pdf(
                    **case.arguments())
                self.assertIn("Technical report", html)
                self.assertFalse(record["release_authorized"])
            finally:
                case.doCleanups()

    def test_invalid_identity_or_disabled_profile_refuses_before_render(self):
        with patch.object(self.m, "render_typed_brand_pdf",
                          side_effect=AssertionError("unexpected rendering")) as render:
            for invalid in ("0" * 64, self.bundle_sha.upper(), None):
                with self.subTest(invalid=invalid):
                    kwargs=self.arguments()
                    kwargs["expected_brand_bundle_sha256"]=invalid
                    with self.assertRaises(self.m.TypedOriginalBrandError):
                        self.m.render_typed_original_brand_pdf(**kwargs)
            kwargs=self.arguments()
            kwargs["expected_ir_sha256"]="0" * 64
            with self.assertRaises(self.m.TypedOriginalBrandError):
                self.m.render_typed_original_brand_pdf(**kwargs)
            with patch.dict(os.environ, {
                    "OMNIGENIS_PDF_QUALIFICATION_RECEIPT_MODE": ""}):
                with self.assertRaises(self.m.TypedOriginalBrandError):
                    self.m.render_typed_original_brand_pdf(**self.arguments())
            with patch.dict(os.environ, {
                    "OMNIGENIS_TYPED_ORIGINAL_BRAND_PDF_DISABLED": "1"}):
                with self.assertRaises(self.m.TypedOriginalBrandError):
                    self.m.render_typed_original_brand_pdf(**self.arguments())
            render.assert_not_called()

    def test_tampered_originals_and_mutated_result_do_not_gain_authority(self):
        original=self.sources["dna_png"]
        altered=BoundBrandAssets.__new__(BoundBrandAssets)
        for name,data in self.sources.items():
            object.__setattr__(altered,name,
                               data[:-1] + bytes([data[-1]^1]) if name=="dna_png" else data)
        with self.assertRaises(self.m.TypedOriginalBrandError):
            self.m.prepare_typed_original_brand_pdf(
                **(self.arguments() | {"assets":altered}))
        _, _, first=self.m.prepare_typed_original_brand_pdf(**self.arguments())
        first["release_authorized"]=True
        _, _, second=self.m.prepare_typed_original_brand_pdf(**self.arguments())
        self.assertFalse(second["release_authorized"])
        self.assertEqual(self.sources["dna_png"],original)


if __name__=="__main__":
    unittest.main()
