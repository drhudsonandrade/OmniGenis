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

    def test_originals_and_semantics_survive_single_existing_raster_render(self):
        pdf, record = self.m.render_typed_original_brand_pdf(**self.arguments())
        self.assertTrue(pdf.pdf_bytes.startswith(b"%PDF-1.7"))
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

    def test_invalid_identity_or_disabled_profile_refuses_before_render(self):
        with patch.object(self.m, "render_preloaded_raster_pdf",
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
