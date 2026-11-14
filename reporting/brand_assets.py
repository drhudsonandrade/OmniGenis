"""Closed byte admission for OmniGenis's two original visual assets.

Only the exact reviewed originals and their specific permission notice are
accepted. This is an immutable preloaded resource bundle, not an extensible SVG
or PNG sanitizer. Hash identity reuses the review of those particular bytes;
no XML/image parsing, filesystem access, URL fetching or native decoding occurs.

The SVG geometry is its viewBox, not an invented intrinsic pixel size. The PNG
keeps its original physical-resolution chunk. No normalization or derivative is
made. Existing staging, rendering and physical-observation profiles retain their
current meanings. This opt-in bundle does not activate a renderer or prove PDF
inclusion, notice placement, visual parity, accessibility or release readiness.

Consumers must preserve the permission and add the required credit to product
notices and once in each PDF using these assets. Renderer integration must bind
this profile and its exact bundle identity through a separately qualified path.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from reporting.bindings import ReportArtifactBinding

PROFILE_ID = "original-brand-assets-v1"
REQUIRED_CREDIT_EN = (
    "Logo and DNA illustration © Hudson Silva Andrade. "
    "Used under specific permission for OmniGenis."
)
_LOGO_SHA256 = "29983790bef75837d683a392515ed35d0410d919d49b6c52a7121f7659770c51"
_DNA_SHA256 = "596ca17e055b473c5d265ab1865a9adb7e453bf20a001aab79a1a255b36b6437"
_PERMISSION_SHA256 = "14a5170bec0e347ec274312f1b915aa9a9d94de61ad32260fc8305f1eb9ddeab"


class BrandAssetError(ValueError):
    """Content-free refusal; no partial or substituted resource is returned."""


@dataclass(frozen=True, slots=True, repr=False)
class BoundBrandAssets:
    """Exact originals and mandatory permission, with no caller-selected policy."""

    logo_svg: bytes
    dna_png: bytes
    permission_notice: bytes

    def __post_init__(self) -> None:
        payloads = ((self.logo_svg, 5909, _LOGO_SHA256),
                    (self.dna_png, 164799, _DNA_SHA256),
                    (self.permission_notice, 3098, _PERMISSION_SHA256))
        # Bound every input before hashing any payload. No coercion or fallback.
        for raw, size, _ in payloads:
            if type(raw) is not bytes:
                raise BrandAssetError("immutable_brand_bytes_required")
            if len(raw) != size:
                raise BrandAssetError("brand_asset_size_mismatch")
        for raw, _, expected in payloads:
            if hashlib.sha256(raw).hexdigest() != expected:
                raise BrandAssetError("brand_asset_digest_mismatch")

    def resolve(self, asset_id: str) -> bytes:
        """Return retained originals only for one of two literal opaque IDs."""
        self.__post_init__()
        if type(asset_id) is not str:
            raise BrandAssetError("brand_asset_id_not_supported")
        if asset_id == "brand-logo":
            return self.logo_svg
        if asset_id == "dna-illustration":
            return self.dna_png
        raise BrandAssetError("brand_asset_id_not_supported")

    def to_dict(self) -> dict[str, object]:
        """Return detached identity/obligation metadata without image payloads."""
        self.__post_init__()
        manifest = {
            "profile_id": PROFILE_ID,
            "assets": [
                {"asset_id": "brand-logo", "media_type": "image/svg+xml",
                 "sha256": _LOGO_SHA256, "size_bytes": 5909,
                 "view_box": [0, 0, 1054, 340]},
                {"asset_id": "dna-illustration", "media_type": "image/png",
                 "sha256": _DNA_SHA256, "size_bytes": 164799,
                 "width_px": 324, "height_px": 1154},
            ],
            "permission_reference": ReportArtifactBinding(
                "omnigenis-visual-asset-permission-v1", _PERMISSION_SHA256).to_dict(),
            "required_credit_en": REQUIRED_CREDIT_EN,
            "credit_obligation": "PRODUCT_NOTICES_AND_ONCE_IN_EACH_PDF_USING_THE_ASSETS",
            "permission_scope": "TWO_IDENTIFIED_VISUAL_ASSETS_ONLY",
        }
        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
        return manifest | {
            "schema_version": "1.0.0",
            "status": "EXACT_ORIGINALS_BOUND",
            "bundle_sha256": hashlib.sha256(encoded).hexdigest(),
            "total_asset_bytes": 170708,
            "source_bytes_retained": True,
            "source_assets_unmodified": True,
            "admission_basis": "EXACT_DIGEST_OF_REVIEWED_ORIGINALS",
            "renderer_activated": False,
            "native_media_decoding_qualified": False,
            "pdf_inclusion_verified": False,
            "pdf_credit_verified": False,
            "visual_parity_verified": False,
            "accessibility_verified": False,
            "release_authorization": "NOT_ESTABLISHED",
        }
