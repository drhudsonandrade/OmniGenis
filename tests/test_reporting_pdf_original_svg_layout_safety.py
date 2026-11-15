"""Successor structural attribute safety proof, without changing passive v1."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import os
import unittest

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject, DictionaryObject, NameObject, NumberObject, TextStringObject,
)

from reporting.brand_assets import BoundBrandAssets
from reporting.pdf_original_dna_inclusion import prepare_original_dna_pdf_candidate
from reporting.pdf_original_svg_layout_safety import validate_original_svg_layout_safety
from reporting.pdf_raster_render import render_preloaded_raster_pdf
from reporting.pdf_safety import validate_pdf_object_safety
from tests.test_reporting_pdf_bound_candidate import bound_request

ASSETS = Path(__file__).resolve().parents[1] / "reporting" / "assets"
MODE = "OMNIGENIS_PDF_QUALIFICATION_RECEIPT_MODE"
ORIGINAL_LOGO = "29983790bef75837d683a392515ed35d0410d919d49b6c52a7121f7659770c51"


class OriginalSvgLayoutSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assets = BoundBrandAssets(
            (ASSETS / "omnigenis_logo.svg").read_bytes(),
            (ASSETS / "dna_column.png").read_bytes(),
            (ASSETS / "brand-permission.md").read_bytes(),
        )
        with patch.dict(os.environ, {MODE: "qualification-only"}):
            prepared = prepare_original_dna_pdf_candidate(
                bound_request=bound_request(), assets=cls.assets,
                expected_brand_bundle_sha256=cls.assets.to_dict()["bundle_sha256"],
            )
            html = prepared.html.replace(
                '<section data-component="original-dna-illustration"',
                '<div data-brand="original-logo" style="width:180px;margin:0 auto">' +
                cls.assets.logo_svg.decode("utf-8") + '</div>' +
                '<section data-component="original-dna-illustration"',
                1,
            )
            cls.pdf = render_preloaded_raster_pdf(
                html=html, expected_html_sha256=sha256(html.encode()).hexdigest(),
                resources=prepared.resources,
                expected_resources_sha256=prepared.resources.bundle_sha256,
            )
        cls.original_pdf = cls.pdf.pdf_bytes
        cls.original_sha = cls.pdf.pdf_sha256

    def check(self, pdf: bytes):
        return validate_original_svg_layout_safety(
            pdf_bytes=pdf, expected_pdf_sha256=sha256(pdf).hexdigest())

    def rewrite(self, change):
        writer = PdfWriter(clone_from=BytesIO(self.original_pdf))
        change(writer)
        output = BytesIO()
        writer.write(output)
        return output.getvalue()

    @staticmethod
    def figure(writer):
        root = writer._root_object["/StructTreeRoot"]
        items = [root]
        seen = set()
        while items:
            value = items.pop()
            if hasattr(value, "get_object"):
                value = value.get_object()
            if id(value) in seen:
                continue
            seen.add(id(value))
            if isinstance(value, DictionaryObject):
                if value.get("/Type") == "/StructElem" and value.get("/S") == "/Figure":
                    return value
                if "/K" in value:
                    items.append(value.raw_get("/K"))
            elif isinstance(value, ArrayObject):
                items.extend(value)
        raise AssertionError("original Figure struct element missing")

    def test_original_svg_layout_is_inert_and_v1_still_blocks_it(self):
        self.assertEqual(sha256(self.assets.logo_svg).hexdigest(), ORIGINAL_LOGO)
        self.assertEqual(self.pdf.page_count, 7)
        old = validate_pdf_object_safety(
            pdf_bytes=self.original_pdf, expected_pdf_sha256=self.original_sha)
        self.assertFalse(old.passed)
        self.assertIn("pdf_actions_forbidden", old.errors)
        result = self.check(self.original_pdf)
        self.assertTrue(result.passed, result.to_dict())
        self.assertEqual(result.figure_layout_count, 1)
        self.assertEqual(result.pdf_sha256, self.original_sha)
        self.assertFalse(result.to_dict()["release_authorized"])
        self.assertFalse(result.to_dict()["original_logo_source_authenticated"])
        self.assertEqual(sha256(self.original_pdf).hexdigest(), self.original_sha)

    def test_exact_digest_and_invalid_pdf_fail_closed(self):
        mismatched = validate_original_svg_layout_safety(
            pdf_bytes=self.original_pdf, expected_pdf_sha256="0" * 64)
        self.assertEqual(mismatched.errors, ("pdf_digest_mismatch",))
        invalid = validate_original_svg_layout_safety(
            pdf_bytes=b"%PDF-1.7\nnot a PDF", expected_pdf_sha256=sha256(
                b"%PDF-1.7\nnot a PDF").hexdigest())
        self.assertFalse(invalid.passed)
        self.assertFalse(self.check(b"not pdf").passed)

    def test_missing_structural_attribute_fails(self):
        def remove(writer):
            fig = self.figure(writer)
            del fig[NameObject("/A")]
        candidate = self.rewrite(remove)
        result = self.check(candidate)
        self.assertFalse(result.passed, result.to_dict())
        self.assertIn("pdf_svg_layout_missing", result.errors)

    def test_invalid_layout_shape_and_outside_page_bbox_fail(self):
        def wrong_owner(writer):
            self.figure(writer).raw_get("/A")[NameObject("/O")] = NameObject("/URI")
        candidate = self.rewrite(wrong_owner)
        self.assertIn("pdf_svg_layout_invalid", self.check(candidate).errors)

        def text_owner(writer):
            self.figure(writer).raw_get("/A")[NameObject("/O")] = TextStringObject("/Layout")
        candidate = self.rewrite(text_owner)
        self.assertIn("pdf_svg_layout_invalid", self.check(candidate).errors)

        def wrong_bbox(writer):
            self.figure(writer).raw_get("/A")[NameObject("/BBox")] = ArrayObject([
                NumberObject(-1), NumberObject(0), NumberObject(50000), NumberObject(100)])
        candidate = self.rewrite(wrong_bbox)
        self.assertIn("pdf_svg_layout_invalid", self.check(candidate).errors)

        def wrong_role(writer):
            self.figure(writer)[NameObject("/S")] = NameObject("/Link")
        candidate = self.rewrite(wrong_role)
        self.assertIn("pdf_svg_layout_invalid", self.check(candidate).errors)

    def test_active_catalog_actions_remain_forbidden(self):
        def inject_action(writer):
            writer._root_object[NameObject("/OpenAction")] = DictionaryObject({
                NameObject("/S"): NameObject("/JavaScript"),
                NameObject("/JS"): NameObject("/alert"),
            })
        candidate = self.rewrite(inject_action)
        result = self.check(candidate)
        self.assertFalse(result.passed, result.to_dict())
        self.assertIn("pdf_actions_forbidden", result.errors)

    def test_unexpected_second_A_and_embedded_file_are_forbidden(self):
        def inject_extra_A(writer):
            writer.pages[0][NameObject("/A")] = DictionaryObject({
                NameObject("/S"): NameObject("/URI"),
                NameObject("/URI"): NameObject("/https"),
            })
        candidate = self.rewrite(inject_extra_A)
        self.assertFalse(self.check(candidate).passed)

        def inject_attachment(writer):
            writer._root_object[NameObject("/EmbeddedFiles")] = DictionaryObject()
        candidate = self.rewrite(inject_attachment)
        self.assertIn("pdf_embedded_files_forbidden", self.check(candidate).errors)


if __name__ == "__main__":
    unittest.main()
