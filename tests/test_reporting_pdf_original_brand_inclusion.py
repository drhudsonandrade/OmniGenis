"""One exact original logo + DNA PDF remains opt-in and release-ineligible."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import os
import unittest

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject

from reporting.brand_assets import BoundBrandAssets, REQUIRED_CREDIT_EN
from reporting.pdf_candidate_qa import validate_pdf_candidate_qa
from reporting.pdf_original_brand_candidate_qa import (
    PROFILE_ID as QA_PROFILE, validate_original_brand_candidate_qa,
)
from reporting.pdf_original_brand_inclusion import (
    PROFILE_ID, OriginalBrandCandidateError,
    execute_original_brand_pdf_candidate, prepare_original_brand_pdf_candidate,
)
from tests.test_reporting_pdf_bound_candidate import bound_request

ASSETS = Path(__file__).resolve().parents[1] / "reporting" / "assets"
MODE = "OMNIGENIS_PDF_QUALIFICATION_RECEIPT_MODE"
ORIGINAL_LOGO_SHA = "29983790bef75837d683a392515ed35d0410d919d49b6c52a7121f7659770c51"
ORIGINAL_DNA_SHA = "596ca17e055b473c5d265ab1865a9adb7e453bf20a001aab79a1a255b36b6437"
ORIGINAL_PDF_SHA = "4844a35b6a8ce6000038c44dd66929cef862e4e584873d2a2df0617f077398c4"
EXPECTED_STAGES = (
    "identity_preflight", "svg_figure_layout", "controlled_metadata",
    "accessibility_prerequisites", "text_content", "visual_geometry",
)


class OriginalBrandInclusionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.originals = {
            "logo_svg": (ASSETS / "omnigenis_logo.svg").read_bytes(),
            "dna_png": (ASSETS / "dna_column.png").read_bytes(),
            "permission_notice": (ASSETS / "brand-permission.md").read_bytes(),
        }
        cls.assets = BoundBrandAssets(**cls.originals)
        cls.request = bound_request()
        cls.bundle = cls.assets.to_dict()["bundle_sha256"]

    def prepare(self, **changes):
        values = dict(bound_request=self.request, assets=self.assets,
                      expected_brand_bundle_sha256=self.bundle)
        values.update(changes)
        with patch.dict(os.environ, {MODE: "qualification-only"}):
            return prepare_original_brand_pdf_candidate(**values)

    def test_only_exact_original_assets_and_single_approved_credit(self):
        self.assertEqual(sha256(self.originals["logo_svg"]).hexdigest(), ORIGINAL_LOGO_SHA)
        self.assertEqual(sha256(self.originals["dna_png"]).hexdigest(), ORIGINAL_DNA_SHA)
        prepared = self.prepare()
        record = prepared.to_dict()
        self.assertEqual(record["profile_id"], PROFILE_ID)
        self.assertEqual(record["logo_original_sha256"], ORIGINAL_LOGO_SHA)
        self.assertEqual(record["png_original_sha256"], ORIGINAL_DNA_SHA)
        self.assertEqual(record["html_sha256"], sha256(prepared.html.encode()).hexdigest())
        self.assertEqual(prepared.html.count(self.originals["logo_svg"].decode()), 1)
        self.assertEqual(prepared.html.count('data-brand="original-logo"'), 1)
        self.assertEqual(prepared.html.replace("\n", "").count(REQUIRED_CREDIT_EN), 1)
        self.assertEqual(prepared.dna.resources.assets[0].png_bytes,
                         self.originals["dna_png"])
        self.assertEqual(len(prepared.dna.presentation_ir["components"]), 7)

    def test_input_mismatch_or_mutation_cannot_be_silently_admitted(self):
        for bad in ("0" * 64, self.bundle.upper(), None):
            with self.subTest(bad=bad), self.assertRaises(OriginalBrandCandidateError):
                self.prepare(expected_brand_bundle_sha256=bad)
        with self.assertRaises(OriginalBrandCandidateError):
            self.prepare(bound_request={})
        for name in self.originals:
            wrong = BoundBrandAssets.__new__(BoundBrandAssets)
            for key, raw in self.originals.items():
                object.__setattr__(wrong, key,
                                   raw[:-1] + bytes([raw[-1] ^ 1]) if name == key else raw)
            with self.subTest(name=name), self.assertRaises(OriginalBrandCandidateError):
                self.prepare(assets=wrong)
        prepared = self.prepare()
        object.__setattr__(prepared, "html", prepared.html + "<!-- mutated -->")
        with self.assertRaises(OriginalBrandCandidateError):
            prepared.to_dict()
        prepared = self.prepare()
        prepared.dna.presentation_ir["components"][-1]["content"]["notice"] = "changed"
        with self.assertRaises(OriginalBrandCandidateError):
            prepared.to_dict()
        with patch.dict(os.environ, {MODE: ""}):
            with self.assertRaises(OriginalBrandCandidateError):
                prepare_original_brand_pdf_candidate(
                    bound_request=self.request, assets=self.assets,
                    expected_brand_bundle_sha256=self.bundle)

    def test_one_real_pdf_passes_opt_in_qa_but_old_v1_remains_blocked(self):
        with patch.dict(os.environ, {MODE: "qualification-only"}):
            pdf, qa, receipt = execute_original_brand_pdf_candidate(
                bound_request=self.request, assets=self.assets,
                expected_brand_bundle_sha256=self.bundle)
            prepared = self.prepare()
        self.assertEqual(pdf.pdf_sha256, ORIGINAL_PDF_SHA)
        self.assertEqual(pdf.page_count, 7)
        self.assertTrue(qa.passed, qa.to_dict())
        self.assertEqual(tuple(qa.to_dict()["stages"]), EXPECTED_STAGES)
        self.assertEqual(set(qa.to_dict()["stages"].values()), {"PASS"})
        self.assertEqual(qa.to_dict()["profile_id"], QA_PROFILE)
        self.assertIs(receipt["same_pdf_qa_verified"], True)
        for key in ("native_source_consumer_qualified", "visual_semantic_parity_verified",
                    "human_assistive_accessibility_verified", "release_authorized"):
            self.assertIs(receipt[key], False)

        old = validate_pdf_candidate_qa(
            presentation_ir=prepared.dna.presentation_ir,
            expected_ir_sha256=receipt["ir_sha256"],
            pdf_bytes=pdf.pdf_bytes, expected_pdf_sha256=pdf.pdf_sha256)
        self.assertFalse(old.passed)
        self.assertEqual(old.gates[1].name, "object_safety")
        self.assertEqual(old.gates[1].status, "BLOCKED")

        reader = PdfReader(BytesIO(pdf.pdf_bytes), strict=True)
        content = " ".join(" ".join(page.extract_text() for page in reader.pages).split())
        self.assertEqual(content.count(REQUIRED_CREDIT_EN), 1)
        self.assertGreaterEqual(sum(len(page.images) for page in reader.pages), 1)

        def assert_blocked(payload, **overrides):
            props = dict(presentation_ir=prepared.dna.presentation_ir,
                         expected_ir_sha256=receipt["ir_sha256"],
                         pdf_bytes=payload,
                         expected_pdf_sha256=sha256(payload).hexdigest())
            props.update(overrides)
            checked = validate_original_brand_candidate_qa(**props)
            self.assertFalse(checked.passed, checked.to_dict())
            self.assertEqual(checked.to_dict()["status"], "BLOCKED")
            self.assertIsNone(checked.page_count)
            self.assertIs(checked.to_dict()["release_authorized"], False)
            return checked

        mismatched = assert_blocked(pdf.pdf_bytes, expected_pdf_sha256="0" * 64)
        self.assertEqual(mismatched.statuses[0], ("identity_preflight", "BLOCKED"))
        wrong_ir = assert_blocked(pdf.pdf_bytes, expected_ir_sha256="0" * 64)
        self.assertEqual(wrong_ir.statuses[0], ("identity_preflight", "BLOCKED"))

        writer = PdfWriter(clone_from=BytesIO(pdf.pdf_bytes))
        writer._root_object[NameObject("/OpenAction")] = DictionaryObject({
            NameObject("/S"): NameObject("/JavaScript"),
            NameObject("/JS"): NameObject("/alert"),
        })
        stream = BytesIO()
        writer.write(stream)
        unsafe = assert_blocked(stream.getvalue())
        self.assertEqual(unsafe.statuses[1], ("svg_figure_layout", "BLOCKED"))
        self.assertEqual(unsafe.statuses[2][1], "NOT_EXECUTED")


if __name__ == "__main__":
    unittest.main()
