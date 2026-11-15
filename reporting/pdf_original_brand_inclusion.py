"""Opt-in exact original-logo SVG plus original-DNA PNG technical PDF.

The two original bytes and once-only owner permission credit are pinned through
the existing source-bound DNA preparation. The added inline SVG is deterministic
and is qualified only by the separate structural Figure /Layout safety profile.
The default passive v1 policy and every existing renderer remain unchanged.
No native source-consumer, frozen visual-semantic, assistive or release claim.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from time import monotonic_ns

from reporting.brand_assets import (
    BoundBrandAssets, REQUIRED_CREDIT_EN, _DNA_SHA256, _LOGO_SHA256,
    _PERMISSION_SHA256,
)
from reporting.pdf_original_dna_inclusion import (
    PreparedOriginalDna, prepare_original_dna_pdf_candidate,
)
from reporting.pdf_original_brand_candidate_qa import (
    OriginalBrandCandidateQA, validate_original_brand_candidate_qa,
)
from reporting.pdf_raster_render import RasterPdfCandidate, render_preloaded_raster_pdf
from reporting.requests import BoundReportRequest

PROFILE_ID = "original-logo-dna-combined-candidate-v1"
_BUNDLE = "c4332907b19ae45524534ad352c1b521db08ec9fc8fd88ae73fa2ec4ba8c0eeb"
_MARKER = '<section data-component="original-dna-illustration"'
_LOGO_MARKER = 'data-brand="original-logo"'
_START = '<div data-brand="original-logo" style="width:180px;margin:0 auto">'
_END = "</div>"
_DEADLINE_NS = 330 * 1000 * 1000 * 1000


class OriginalBrandCandidateError(ValueError):
    """Content-free refusal: no partial candidate or unqualified fallback."""


def _require(ok: bool) -> None:
    if not ok:
        raise OriginalBrandCandidateError("original_brand_candidate_unverified")


def _logo_markup(assets: BoundBrandAssets) -> str:
    """Decode the one exact admitted SVG, never arbitrary caller SVG/HTML."""
    manifest = assets.to_dict()
    _require(manifest["bundle_sha256"] == _BUNDLE
             and manifest["permission_reference"]["sha256"] == _PERMISSION_SHA256
             and manifest["required_credit_en"] == REQUIRED_CREDIT_EN)
    raw = assets.resolve("brand-logo")
    _require(sha256(raw).hexdigest() == _LOGO_SHA256)
    return _START + raw.decode("utf-8", errors="strict") + _END


@dataclass(frozen=True, slots=True, repr=False)
class PreparedOriginalBrand:
    """Seal the only forward source-to-HTML composition allowed by this unit."""

    originals: BoundBrandAssets = field(repr=False)
    dna: PreparedOriginalDna = field(repr=False)
    html: str = field(repr=False)
    html_sha256: str
    _seal: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _require(type(self) is PreparedOriginalBrand
                 and type(self.originals) is BoundBrandAssets
                 and type(self.dna) is PreparedOriginalDna
                 and type(self.html) is str and type(self.html_sha256) is str)
        source = self.dna.to_dict()
        manifest = self.originals.to_dict()
        _require(source["brand_bundle_sha256"] == manifest["bundle_sha256"] == _BUNDLE
                 and source["permission_sha256"] == _PERMISSION_SHA256
                 and source["png_original_sha256"] == _DNA_SHA256
                 and source["credit_count"] == 1 and source["logo_svg_included"] is False)
        markup = _logo_markup(self.originals)
        _require(self.dna.html.count(_MARKER) == 1
                 and self.dna.html.count(_LOGO_MARKER) == 0)
        expected = self.dna.html.replace(_MARKER, markup + _MARKER, 1)
        raw = expected.encode("utf-8")
        _require(self.html == expected and self.html.count(markup) == 1
                 and self.html.count(_LOGO_MARKER) == 1
                 and self.html.replace("\n", "").count(REQUIRED_CREDIT_EN) == 1
                 and self.html.count(self.dna.resources.assets[0].uri) == 1
                 and 0 < len(raw) <= 64 * 1024 * 1024
                 and sha256(raw).hexdigest() == self.html_sha256)
        values = (self.html_sha256, source["ir_sha256"],
                  source["resources_sha256"], _LOGO_SHA256, _DNA_SHA256,
                  _PERMISSION_SHA256, _BUNDLE)
        seal = sha256("\x00".join(values).encode("ascii")).hexdigest()
        _require(not hasattr(self, "_seal") or self._seal == seal)
        object.__setattr__(self, "_seal", seal)

    def to_dict(self) -> dict[str, object]:
        try:
            self.__post_init__()
            source = self.dna.to_dict()
        except Exception:
            raise OriginalBrandCandidateError("original_brand_candidate_unverified") from None
        return {
            "schema_version": "1.0.0", "profile_id": PROFILE_ID,
            "status": "ORIGINAL_BRAND_HTML_SOURCE_BOUND",
            "scope": "OPT_IN_SYNTHETIC_EN_US_QUALIFICATION_ONLY",
            "html_sha256": self.html_sha256,
            "ir_sha256": source["ir_sha256"],
            "expected_text_sha256": source["expected_text_sha256"],
            "resources_sha256": source["resources_sha256"],
            "brand_bundle_sha256": _BUNDLE,
            "logo_original_sha256": _LOGO_SHA256,
            "png_original_sha256": _DNA_SHA256,
            "permission_sha256": _PERMISSION_SHA256,
            "approved_credit": REQUIRED_CREDIT_EN, "credit_count": 1,
            "logo_source_bytes_preserved": True,
            "dna_source_bytes_preserved": True,
            "logo_svg_included_in_exact_html": True,
            "pdf_source_asset_authenticated": False,
            "native_source_consumer_qualified": False,
            "visual_semantic_parity_verified": False,
            "human_assistive_accessibility_verified": False,
            "release_authorized": False,
        }


def prepare_original_brand_pdf_candidate(
    *, bound_request: BoundReportRequest, assets: BoundBrandAssets,
    expected_brand_bundle_sha256: str,
) -> PreparedOriginalBrand:
    """Compose one exact original SVG ahead of the already admitted DNA section."""
    try:
        _require(type(bound_request) is BoundReportRequest
                 and type(assets) is BoundBrandAssets
                 and type(expected_brand_bundle_sha256) is str
                 and expected_brand_bundle_sha256 == _BUNDLE
                 and assets.to_dict()["bundle_sha256"] == _BUNDLE)
        dna = prepare_original_dna_pdf_candidate(
            bound_request=bound_request, assets=assets,
            expected_brand_bundle_sha256=expected_brand_bundle_sha256)
        _require(dna.html.count(_MARKER) == 1)
        html = dna.html.replace(_MARKER, _logo_markup(assets) + _MARKER, 1)
        return PreparedOriginalBrand(assets, dna, html,
                                     sha256(html.encode("utf-8")).hexdigest())
    except Exception:
        raise OriginalBrandCandidateError("original_brand_candidate_unverified") from None


def execute_original_brand_pdf_candidate(
    *, bound_request: BoundReportRequest, assets: BoundBrandAssets,
    expected_brand_bundle_sha256: str,
) -> tuple[RasterPdfCandidate, OriginalBrandCandidateQA, dict[str, object]]:
    """Render/inspect exact bytes once; return no artifact when any gate fails."""
    try:
        params = dict(bound_request=bound_request, assets=assets,
                      expected_brand_bundle_sha256=expected_brand_bundle_sha256)
        prepared = prepare_original_brand_pdf_candidate(**params)
        initial = prepared.to_dict()
        started = monotonic_ns()

        def check() -> None:
            _require(0 <= monotonic_ns() - started < _DEADLINE_NS)
            other = prepare_original_brand_pdf_candidate(**params)
            _require(other.to_dict() == initial and other.html == prepared.html
                     and other.dna.resources.to_bytes() == prepared.dna.resources.to_bytes())
            prepared.to_dict()

        check()
        pdf = render_preloaded_raster_pdf(
            html=prepared.html, expected_html_sha256=prepared.html_sha256,
            resources=prepared.dna.resources,
            expected_resources_sha256=prepared.dna.resources.bundle_sha256)
        check()
        render_record = pdf.to_dict()
        _require(render_record["html_sha256"] == prepared.html_sha256
                 and render_record["resources_sha256"] == initial["resources_sha256"]
                 and render_record["resource_count"] == 1)
        qualified = validate_original_brand_candidate_qa(
            presentation_ir=prepared.dna.presentation_ir,
            expected_ir_sha256=initial["ir_sha256"],
            pdf_bytes=pdf.pdf_bytes, expected_pdf_sha256=pdf.pdf_sha256)
        check()
        result = qualified.to_dict()
        _require(qualified.passed and result["page_count"] == pdf.page_count
                 and result["pdf_sha256"] == pdf.pdf_sha256
                 and result["native_source_inspection_qualified"] is False
                 and result["release_authorized"] is False)
        receipt = initial | {
            "status": "SAME_PDF_BRAND_STRUCTURAL_CANDIDATE_QA_PASS",
            "pdf_sha256": pdf.pdf_sha256, "pdf_bytes": len(pdf.pdf_bytes),
            "page_count": pdf.page_count, "qa_profile_id": result["profile_id"],
            "qa_gates": result["stages"],
            "same_pdf_qa_verified": True,
            "pdf_source_asset_authenticated": False,
            "native_source_consumer_qualified": False,
            "visual_semantic_parity_verified": False,
            "human_assistive_accessibility_verified": False,
            "release_authorized": False,
        }
        check()
        return pdf, qualified, receipt
    except Exception:
        raise OriginalBrandCandidateError("original_brand_execution_unverified") from None
