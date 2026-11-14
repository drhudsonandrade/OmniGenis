"""Closed original-asset byte binding and required permission, without rendering."""
from dataclasses import FrozenInstanceError
import importlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch


ASSETS = Path(__file__).resolve().parents[1] / "reporting" / "assets"


class OriginalBrandAssetTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("reporting.brand_assets"),
                             "missing closed original brand asset admission")
        self.api = importlib.import_module("reporting.brand_assets")
        self.payloads = {
            "logo_svg": (ASSETS / "omnigenis_logo.svg").read_bytes(),
            "dna_png": (ASSETS / "dna_column.png").read_bytes(),
            "permission_notice": (ASSETS / "brand-permission.md").read_bytes(),
        }

    def bundle(self, **changes):
        return self.api.BoundBrandAssets(**(self.payloads | changes))

    def test_exact_original_pair_is_retained_without_transformation(self):
        bound = self.bundle()
        self.assertIs(bound.resolve("brand-logo"), self.payloads["logo_svg"])
        self.assertIs(bound.resolve("dna-illustration"), self.payloads["dna_png"])
        receipt = bound.to_dict()
        self.assertEqual(receipt["assets"], [
            {"asset_id": "brand-logo", "media_type": "image/svg+xml",
             "sha256": "29983790bef75837d683a392515ed35d0410d919d49b6c52a7121f7659770c51",
             "size_bytes": 5909, "view_box": [0, 0, 1054, 340]},
            {"asset_id": "dna-illustration", "media_type": "image/png",
             "sha256": "596ca17e055b473c5d265ab1865a9adb7e453bf20a001aab79a1a255b36b6437",
             "size_bytes": 164799, "width_px": 324, "height_px": 1154},
        ])

    def test_one_byte_asset_substitution_is_rejected(self):
        for name in ("logo_svg", "dna_png"):
            with self.subTest(name=name):
                raw = self.payloads[name]
                replacement = bytes([raw[0] ^ 1]) + raw[1:]
                with self.assertRaisesRegex(self.api.BrandAssetError,
                                            "^brand_asset_digest_mismatch$"):
                    self.bundle(**{name: replacement})

    def test_missing_truncated_appended_and_oversized_bytes_are_rejected(self):
        for name, raw in self.payloads.items():
            for replacement in (b"", raw[:-1], raw + b"x", b"x" * 200000):
                with self.subTest(name=name, size=len(replacement)):
                    with self.assertRaisesRegex(self.api.BrandAssetError,
                                                "^brand_asset_size_mismatch$"):
                        self.bundle(**{name: replacement})

    def test_only_immutable_exact_bytes_are_admitted(self):
        class BytesSubclass(bytes):
            pass
        for name, raw in self.payloads.items():
            for replacement in (None, False, "https://example.invalid", bytearray(raw),
                                memoryview(raw), BytesSubclass(raw)):
                with self.subTest(name=name, kind=type(replacement).__name__):
                    with self.assertRaisesRegex(self.api.BrandAssetError,
                                                "^immutable_brand_bytes_required$"):
                        self.bundle(**{name: replacement})

    def test_roles_cannot_be_swapped(self):
        with self.assertRaises(self.api.BrandAssetError):
            self.bundle(logo_svg=self.payloads["dna_png"], dna_png=self.payloads["logo_svg"])

    def test_permission_cannot_be_replaced_by_a_caller_declaration(self):
        raw = self.payloads["permission_notice"]
        replacement = b" " + raw[1:]
        with self.assertRaisesRegex(self.api.BrandAssetError,
                                    "^brand_asset_digest_mismatch$"):
            self.bundle(permission_notice=replacement)

    def test_alternative_active_svg_cannot_enter_exact_original_profile(self):
        original = self.payloads["logo_svg"]
        alternatives = [
            b'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]><svg>&x;</svg>',
            b'<svg><script>alert(1)</script></svg>',
            b'<svg onload="alert(1)"><image href="https://example.invalid/a"/></svg>',
            b'<svg><foreignObject/><use href="data:image/svg+xml,test"/></svg>',
        ]
        for raw in alternatives:
            with self.subTest(kind=raw[:30]):
                padded = raw + b" " * (len(original) - len(raw))
                with self.assertRaisesRegex(self.api.BrandAssetError,
                                            "^brand_asset_digest_mismatch$"):
                    self.bundle(logo_svg=padded)

    def test_altered_png_metadata_cannot_enter_exact_original_profile(self):
        raw = self.payloads["dna_png"]
        location = raw.index(b"pHYs") + 4
        changed = raw[:location] + bytes([raw[location] ^ 1]) + raw[location + 1:]
        with self.assertRaisesRegex(self.api.BrandAssetError,
                                    "^brand_asset_digest_mismatch$"):
            self.bundle(dna_png=changed)

    def test_lookup_rejects_urls_paths_digests_and_lookalike_ids(self):
        bound = self.bundle()
        for value in (None, True, 1, [], "", "brand-logo ", "BRAND-LOGO",
                      "../brand-logo", "omnigenis_logo.svg", "file:///etc/passwd",
                      "https://example.invalid/logo", "data:image/png;base64,eA==",
                      "omnigenis-raster:596ca17e055b473c5d265ab1865a9adb7e453bf20a001aab79a1a255b36b6437",
                      "29983790bef75837d683a392515ed35d0410d919d49b6c52a7121f7659770c51"):
            with self.subTest(value=repr(value)):
                with self.assertRaisesRegex(self.api.BrandAssetError,
                                            "^brand_asset_id_not_supported$"):
                    bound.resolve(value)

    def test_admission_and_lookup_perform_no_external_io(self):
        with patch("builtins.open", side_effect=AssertionError("file access")), \
             patch("os.open", side_effect=AssertionError("descriptor access")), \
             patch("socket.socket", side_effect=AssertionError("network access")), \
             patch("subprocess.Popen", side_effect=AssertionError("process launch")):
            bound = self.bundle()
            self.assertEqual(len(bound.resolve("brand-logo")), 5909)
            self.assertEqual(len(bound.resolve("dna-illustration")), 164799)
            self.assertEqual(bound.to_dict()["total_asset_bytes"], 170708)

    def test_receipt_binds_credit_without_claiming_its_pdf_placement(self):
        receipt = self.bundle().to_dict()
        self.assertEqual(receipt["required_credit_en"],
                         "Logo and DNA illustration © Hudson Silva Andrade. "
                         "Used under specific permission for OmniGenis.")
        self.assertEqual(receipt["credit_obligation"],
                         "PRODUCT_NOTICES_AND_ONCE_IN_EACH_PDF_USING_THE_ASSETS")
        self.assertEqual(receipt["permission_scope"], "TWO_IDENTIFIED_VISUAL_ASSETS_ONLY")
        for claim in ("renderer_activated", "native_media_decoding_qualified",
                      "pdf_inclusion_verified", "pdf_credit_verified",
                      "visual_parity_verified", "accessibility_verified"):
            self.assertIs(receipt[claim], False)
        self.assertEqual(receipt["release_authorization"], "NOT_ESTABLISHED")
        self.assertNotIn("<svg", json.dumps(receipt))

    def test_receipt_is_detached_from_later_resolutions(self):
        bound = self.bundle()
        receipt = bound.to_dict()
        receipt["assets"][0]["sha256"] = "0" * 64
        receipt["assets"][0]["view_box"][2] = 1
        self.assertEqual(bound.to_dict()["assets"][0]["view_box"], [0, 0, 1054, 340])
        self.assertIs(bound.resolve("brand-logo"), self.payloads["logo_svg"])

    def test_frozen_bundle_prevents_ordinary_mutation(self):
        bound = self.bundle()
        with self.assertRaises(FrozenInstanceError):
            bound.logo_svg = b"replacement"

    def test_bypassed_field_mutation_is_revalidated_at_each_boundary(self):
        for field in self.payloads:
            for operation in (lambda value: value.resolve("brand-logo"),
                              lambda value: value.to_dict()):
                with self.subTest(field=field):
                    bound = self.bundle()
                    object.__setattr__(bound, field, b"replacement")
                    with self.assertRaises(self.api.BrandAssetError):
                        operation(bound)

    def test_third_asset_and_caller_selected_policy_are_not_parameters(self):
        for extra in ({"third_asset": b"image"}, {"expected_digest": "0" * 64},
                      {"rights_verified": True}, {"renderer_activated": True}):
            with self.subTest(extra=next(iter(extra))):
                with self.assertRaises(TypeError):
                    self.bundle(**extra)


if __name__ == "__main__":
    unittest.main()
