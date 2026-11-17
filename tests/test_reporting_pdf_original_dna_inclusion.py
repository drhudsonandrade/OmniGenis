"""Same-artifact exact-DNA PNG/owner-credit qualification, not logo admission."""
from hashlib import sha256
from importlib import import_module
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import os
import unittest

from reporting.brand_assets import BoundBrandAssets, REQUIRED_CREDIT_EN
from reporting.pdf_generated_source_job import _expected_content
from tests.test_reporting_pdf_bound_candidate import bound_request

ASSETS = Path(__file__).resolve().parents[1] / "reporting" / "assets"
MODE = "OMNIGENIS_PDF_QUALIFICATION_RECEIPT_MODE"


class OriginalDnaInclusionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = import_module("reporting.pdf_original_dna_inclusion")

    def setUp(self):
        self.originals = {
            "logo_svg": (ASSETS / "omnigenis_logo.svg").read_bytes(),
            "dna_png": (ASSETS / "dna_column.png").read_bytes(),
            "permission_notice": (ASSETS / "brand-permission.md").read_bytes(),
        }
        self.assets = BoundBrandAssets(**self.originals)
        self.request = bound_request()
        self.expected = self.assets.to_dict()["bundle_sha256"]

    def prepare(self, **changes):
        params = {"bound_request": self.request, "assets": self.assets,
                  "expected_brand_bundle_sha256": self.expected}
        params.update(changes)
        with patch.dict(os.environ, {MODE: "qualification-only"}):
            return self.m.prepare_original_dna_pdf_candidate(**params)

    def test_exact_source_and_one_credit_with_explicit_logo_exclusion(self):
        prepared = self.prepare()
        metadata = prepared.to_dict()
        self.assertEqual(len(prepared.presentation_ir["components"]), 7)
        self.assertEqual([row["component_id"] for row in prepared.presentation_ir["components"][-2:]],
                         ["original-dna-illustration", "visual-asset-credits"])
        self.assertEqual(_expected_content(prepared.presentation_ir),
                         (metadata["ir_sha256"], metadata["expected_text_sha256"]))
        self.assertEqual(prepared.resources.assets[0].png_bytes, self.originals["dna_png"])
        self.assertEqual(prepared.resources.assets[0].uri,
                         "omnigenis-raster:" + sha256(self.originals["dna_png"]).hexdigest())
        self.assertEqual(metadata["brand_bundle_sha256"], self.expected)
        self.assertEqual(metadata["png_original_sha256"], sha256(self.originals["dna_png"]).hexdigest())
        self.assertEqual(metadata["permission_sha256"], sha256(self.originals["permission_notice"]).hexdigest())
        self.assertEqual(metadata["html_sha256"], sha256(prepared.html.encode()).hexdigest())
        self.assertEqual(prepared.html.replace("\n", "").count(REQUIRED_CREDIT_EN), 1)
        self.assertEqual(prepared.html.count("omnigenis-raster:"), 1)
        self.assertNotIn("<svg", prepared.html)
        self.assertIs(metadata["logo_svg_included"], False)
        self.assertIs(metadata["release_authorized"], False)

    def test_original_dna_background_has_disjoint_source_text_column(self):
        """The original DNA artwork may not be painted behind source text.

        The CSS box reserves a right-side gutter wider than the proportional
        324x1154 PNG scaled to 710 CSS px, plus a finite 24 CSS px gap.
        Actual PDF raster inspection remains required for visual acceptance.
        """
        import re

        prepared=self.prepare()
        section=re.search(
            r'<section data-component="original-dna-illustration" style="([^"]+)">',
            prepared.html,
        )
        self.assertIsNotNone(section)
        style=dict(rule.split(':',1) for rule in section.group(1).split(';') if rule)
        self.assertEqual(style['break-before'],'page')
        self.assertEqual(style['background-repeat'],'no-repeat')
        self.assertEqual(style['background-position'],'right bottom')
        self.assertEqual(style['background-size'],'auto 710px')
        self.assertEqual(style['min-height'],'790px')
        self.assertTrue(style['background-image'].startswith("url('omnigenis-raster:"))
        self.assertTrue(style['background-image'].endswith("')"))
        self.assertTrue(style['padding-right'].endswith('px'))
        reserved=float(style['padding-right'][:-2])
        painted_width=710.0*self.assets.to_dict()['assets'][1]['width_px']/self.assets.to_dict()['assets'][1]['height_px']
        self.assertGreaterEqual(reserved-painted_width,24.0)
        self.assertLess(reserved,300.0)
        self.assertIn(
            '<h2 style="white-space:nowrap">OmniGenis original DNA illustration</h2>',
            prepared.html,
        )

    def test_exact_special_case_pHys_does_not_widen_generic_png_parser(self):
        from reporting.png_inspection import _inspect_png_container, PngInspectionError
        from reporting.pdf_raster_resources import RasterAsset, RasterResourceError
        from reporting._pdf_original_dna_png_admission import (
            inspect_exact_original_dna_png, OriginalDnaPngError)
        payload = self.originals["dna_png"]
        digest = sha256(payload).hexdigest()
        rights = sha256(self.originals["permission_notice"]).hexdigest()
        with self.assertRaises(PngInspectionError):
            _inspect_png_container(payload, 324, 1154)
        result = inspect_exact_original_dna_png(payload, digest, digest, rights, 324, 1154)
        self.assertEqual(result["allowed_ancillary_chunk"], "pHYs")
        self.assertIs(result["bytes_normalized"], False)
        accepted = RasterAsset("dna-illustration", payload, digest, 324, 1154, digest, rights)
        self.assertEqual(accepted.png_bytes, payload)
        for changes in ({"asset_id": "wrong"}, {"source_sha256": "0" * 64},
                        {"rights_sha256": "0" * 64}, {"width_px": 323},
                        {"png_bytes": payload[:42] + bytes([payload[42] ^ 1]) + payload[43:]}):
            fields = {"asset_id": "dna-illustration", "png_bytes": payload,
                      "expected_sha256": digest, "width_px": 324, "height_px": 1154,
                      "source_sha256": digest, "rights_sha256": rights}
            fields.update(changes)
            with self.subTest(changes=list(changes)), self.assertRaises(RasterResourceError):
                RasterAsset(**fields)
        with self.assertRaises(OriginalDnaPngError):
            inspect_exact_original_dna_png(payload, digest, digest, "0" * 64, 324, 1154)

    def test_wrong_asset_bundle_or_disabled_mode_refuse(self):
        for sha in ("0" * 64, self.expected.upper(), None):
            with self.subTest(sha=sha), self.assertRaises(self.m.OriginalDnaCandidateError):
                self.prepare(expected_brand_bundle_sha256=sha)
        for name, raw in self.originals.items():
            wrong = BoundBrandAssets.__new__(BoundBrandAssets)
            for key, payload in self.originals.items():
                altered = payload[:-1] + bytes([payload[-1] ^ 1])
                object.__setattr__(wrong, key, altered if name == key else payload)
            with self.subTest(name=name), self.assertRaises(self.m.OriginalDnaCandidateError):
                self.prepare(assets=wrong)
        with self.assertRaises(self.m.OriginalDnaCandidateError):
            self.prepare(bound_request={})
        with self.assertRaises(TypeError):
            self.prepare(resource_url="https://example.com")
        with patch.dict(os.environ, {MODE: ""}):
            with self.assertRaises(self.m.OriginalDnaCandidateError):
                self.m.prepare_original_dna_pdf_candidate(
                    bound_request=self.request, assets=self.assets,
                    expected_brand_bundle_sha256=self.expected)

    def test_seals_detect_post_preparation_mutation(self):
        prepared = self.prepare()
        prepared.presentation_ir["components"][-1]["content"]["notice"] = "mutated"
        with self.assertRaises(self.m.OriginalDnaCandidateError):
            prepared.to_dict()
        prepared = self.prepare()
        object.__setattr__(prepared.resources.assets[0], "png_bytes", b"bad")
        with self.assertRaises(self.m.OriginalDnaCandidateError):
            prepared.to_dict()

    def test_actual_confined_pdf_passes_six_qa_and_visible_owner_credit(self):
        from pypdf import PdfReader
        from reporting.pdf_candidate_qa import validate_pdf_candidate_qa
        with patch.dict(os.environ, {MODE: "qualification-only"}):
            pdf, inspected, receipt = self.m.execute_original_dna_pdf_candidate(
                bound_request=self.request, assets=self.assets,
                expected_brand_bundle_sha256=self.expected)
            prepared = self.m.prepare_original_dna_pdf_candidate(
                bound_request=self.request, assets=self.assets,
                expected_brand_bundle_sha256=self.expected)
            checked = validate_pdf_candidate_qa(
                presentation_ir=prepared.presentation_ir,
                expected_ir_sha256=receipt["ir_sha256"],
                pdf_bytes=pdf.pdf_bytes,
                expected_pdf_sha256=pdf.pdf_sha256)
        self.assertTrue(checked.passed, checked.to_dict())
        self.assertEqual(len(checked.gates), 6)
        self.assertEqual(inspected.to_dict()["normalized_text_equivalence_verified"], True)
        self.assertEqual(receipt["pdf_sha256"], pdf.pdf_sha256)
        self.assertIs(receipt["logo_svg_included"], False)
        self.assertIs(receipt["release_authorized"], False)
        reader = PdfReader(BytesIO(pdf.pdf_bytes), strict=True)
        text = " ".join(" ".join(page.extract_text() for page in reader.pages).split())
        self.assertEqual(text.count(REQUIRED_CREDIT_EN), 1)
        self.assertGreaterEqual(sum(len(page.images) for page in reader.pages), 1)


if __name__ == "__main__":
    unittest.main()
