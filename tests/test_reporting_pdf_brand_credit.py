"""Approved brand credit in an opt-in source-bound PDF HTML candidate."""
from dataclasses import FrozenInstanceError
from hashlib import sha256
from html import escape
from importlib import import_module
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import os
import unittest

from reporting.brand_assets import BoundBrandAssets, REQUIRED_CREDIT_EN
from tests.test_reporting_pdf_bound_candidate import bound_request

ASSETS = Path(__file__).resolve().parents[1] / "reporting" / "assets"
MODE = "OMNIGENIS_PDF_QUALIFICATION_RECEIPT_MODE"


class BrandCreditCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = import_module("reporting.pdf_brand_credit")

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
        args = {"bound_request": self.request, "assets": self.assets,
                "expected_brand_bundle_sha256": self.expected}
        args.update(changes)
        with patch.dict(os.environ, {MODE: "qualification-only"}):
            return self.m.prepare_brand_credit_pdf_candidate(**args)

    def test_approved_credit_is_one_visible_semantic_section(self):
        prepared = self.prepare()
        html = prepared.html
        self.assertEqual(html.count('data-component="visual-asset-credits"'), 1)
        self.assertEqual(html.replace("\n", "").count(escape(REQUIRED_CREDIT_EN)), 1)
        self.assertIn("<h2>Visual asset credits</h2>", html)
        self.assertIn("<pre>{&quot;notice&quot;:&quot;" + escape(REQUIRED_CREDIT_EN) + "&quot;}</pre>", html.replace("\n", ""))
        self.assertEqual(prepared.html_sha256, sha256(html.encode()).hexdigest())
        from reporting.pdf_generated_source_job import _expected_content
        self.assertEqual(_expected_content(prepared.presentation_ir),
                         (prepared.credited_ir_sha256, prepared.expected_text_sha256))
        self.assertNotEqual(prepared.source_ir_sha256, prepared.credited_ir_sha256)
        self.assertEqual(html.count("<section "), 6)
        self.assertEqual(html.count("</section>"), 6)

    def test_binding_is_exact_and_no_media_is_accidentally_activated(self):
        prepared = self.prepare()
        receipt = prepared.to_dict()
        self.assertEqual(receipt["brand_bundle_sha256"], self.expected)
        self.assertEqual(receipt["credit"], REQUIRED_CREDIT_EN)
        self.assertEqual(receipt["status"], "CREDIT_HTML_PREPARED")
        self.assertEqual(receipt["scope"], "OPT_IN_CREDIT_HTML_ONLY")
        for key in ("pdf_inclusion_verified", "pdf_credit_verified",
                    "native_media_decoding_qualified", "visual_parity_verified",
                    "accessibility_verified", "release_authorized"):
            self.assertIs(receipt[key], False)
        self.assertNotIn("<svg", prepared.html)
        self.assertNotIn("<img", prepared.html)
        self.assertNotIn("data:image", prepared.html)

    def test_wrong_bundle_identity_refuses_before_html(self):
        for expected in ("0" * 64, self.expected.upper(), None, True):
            with self.subTest(expected=expected):
                with self.assertRaises(self.m.BrandCreditError):
                    self.prepare(expected_brand_bundle_sha256=expected)

    def test_original_artwork_and_permission_tampering_refuse(self):
        for name, raw in self.originals.items():
            changed = raw[:-1] + bytes([raw[-1] ^ 1])
            wrong = BoundBrandAssets.__new__(BoundBrandAssets)
            for field, content in self.originals.items():
                object.__setattr__(wrong, field, changed if field == name else content)
            with self.subTest(field=name), self.assertRaises(self.m.BrandCreditError):
                self.prepare(assets=wrong)

    def test_missing_real_source_binding_refuses(self):
        with self.assertRaises(self.m.BrandCreditError):
            self.prepare(bound_request={"report_id": "fake"})

    def test_caller_cannot_replace_notice_or_locale(self):
        with self.assertRaises(TypeError):
            self.prepare(credit_text="Other author")
        with self.assertRaises(TypeError):
            self.prepare(locale_id="pt-BR")

    def test_disabled_qualification_mode_refuses(self):
        with patch.dict(os.environ, {MODE: ""}):
            with self.assertRaises(self.m.BrandCreditError):
                self.m.prepare_brand_credit_pdf_candidate(
                    bound_request=self.request, assets=self.assets,
                    expected_brand_bundle_sha256=self.expected)

    def test_immutable_preparation_revalidates_hash(self):
        prepared = self.prepare()
        with self.assertRaises(FrozenInstanceError):
            prepared.html = "replacement"
        object.__setattr__(prepared, "html", "replacement")
        with self.assertRaises(self.m.BrandCreditError):
            prepared.to_dict()
        separate = self.prepare()
        object.__setattr__(separate, "permission_sha256", "0" * 64)
        with self.assertRaises(self.m.BrandCreditError):
            separate.to_dict()
        nested = self.prepare()
        nested.presentation_ir["components"][-1]["content"]["notice"] = "changed"
        with self.assertRaises(self.m.BrandCreditError):
            nested.to_dict()

    def test_actual_native_pdf_contains_approved_credit_once(self):
        from reporting.pdf_generated_source_job import render_pdf_candidate
        from pypdf import PdfReader
        prepared = self.prepare()
        candidate = render_pdf_candidate(prepared.html, prepared.html_sha256)
        reader = PdfReader(BytesIO(candidate.pdf_bytes), strict=True)
        text = "\n".join(page.extract_text() for page in reader.pages)
        compact = " ".join(text.split())
        self.assertEqual(compact.count(REQUIRED_CREDIT_EN), 1)
        self.assertEqual(candidate.to_dict()["release_authorized"], False)
        self.assertGreater(len(candidate.pdf_bytes), 1000)
        from reporting.pdf_candidate_qa import validate_pdf_candidate_qa
        checked = validate_pdf_candidate_qa(
            presentation_ir=prepared.presentation_ir,
            expected_ir_sha256=prepared.credited_ir_sha256,
            pdf_bytes=candidate.pdf_bytes,
            expected_pdf_sha256=candidate.pdf_sha256,
        )
        self.assertTrue(checked.passed, checked.to_dict())
        self.assertEqual(len(checked.gates), 6)


if __name__ == "__main__":
    unittest.main()
