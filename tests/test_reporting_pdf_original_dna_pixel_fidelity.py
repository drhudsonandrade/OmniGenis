"""Opt-in PDF QA: original DNA pixels survive native embedding on every page.

This test uses only owned synthetic report fixtures and already admitted assets.
Decoded pixel identity is not source PNG byte identity, SVG vector parity,
editorial content parity, PDF/UA, or release authorization.
"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import os
import unittest

from PIL import Image
from pypdf import PdfReader

from reporting.brand_assets import BoundBrandAssets
from reporting.pdf_original_brand_pagination_composite import (
    render_original_brand_paginated_candidate,
)
from tests.test_reporting_pdf_bound_candidate import bound_request

ASSETS = Path(__file__).resolve().parents[1] / "reporting/assets"
QUALIFICATION = "OMNIGENIS_PDF_QUALIFICATION_RECEIPT_MODE"


class OriginalDnaPdfPixelFidelityTests(unittest.TestCase):
    def test_native_pdf_contains_source_identical_dna_pixels_on_every_page(self):
        assets = BoundBrandAssets(
            logo_svg=(ASSETS / "omnigenis_logo.svg").read_bytes(),
            dna_png=(ASSETS / "dna_column.png").read_bytes(),
            permission_notice=(ASSETS / "brand-permission.md").read_bytes(),
        )
        with Image.open(BytesIO(assets.dna_png)) as original:
            self.assertEqual(original.size, (324, 1154))
            source_pixel_sha256 = sha256(original.convert("RGBA").tobytes()).hexdigest()

        with patch.dict(os.environ, {QUALIFICATION: "qualification-only"}):
            candidate = render_original_brand_paginated_candidate(
                bound_request=bound_request(),
                assets=assets,
                expected_brand_bundle_sha256=assets.to_dict()["bundle_sha256"],
            )
        reader = PdfReader(BytesIO(candidate.pdf_bytes), strict=True)
        self.assertGreater(len(reader.pages), 1)
        self.assertEqual(candidate.page_count, len(reader.pages))
        observed_names = set()
        for page_number, page in enumerate(reader.pages, start=1):
            with self.subTest(page=page_number):
                images = list(page.images)
                self.assertEqual(len(images), 1)
                observed_names.add(images[0].name)
                self.assertLessEqual(len(images[0].data), 1_048_576)
                with Image.open(BytesIO(images[0].data)) as embedded:
                    self.assertEqual(embedded.size, (324, 1154))
                    self.assertEqual(
                        sha256(embedded.convert("RGBA").tobytes()).hexdigest(),
                        source_pixel_sha256,
                    )
        self.assertEqual(len(observed_names), 1)
        self.assertIs(candidate.summary()["release_authorized"], False)
        self.assertIs(candidate.summary()["source_asset_bytes_authenticated_from_pdf"], False)


if __name__ == "__main__":
    unittest.main()
