"""Opt-in, exact approved DNA PNG in a source-bound synthetic en-US PDF.

The original PNG with its pHYs chunk is retained byte-for-byte. The closed
preloaded raster resolver and confined PDF workers are reused; no other media
profile, file URL, unsafe PDF annotation or default release is activated.
The approved owner credit is mandatory. The original logo SVG is NOT rendered
here: it requires its separate M4-06 PDF structural-attribute qualification.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from time import monotonic_ns

from reporting import pdf_bound_raster_candidate as bounded
from reporting import pdf_generated_source_job as generated
from reporting import pdf_native_source_job as native
from reporting import pdf_raster_render as raster
from reporting.brand_assets import (
    BoundBrandAssets, REQUIRED_CREDIT_EN, _DNA_SHA256, _PERMISSION_SHA256,
)
from reporting.pdf_brand_credit import PreparedBrandCredit, prepare_brand_credit_pdf_candidate
from reporting.pdf_raster_resources import RasterAsset, RasterBundle
from reporting.requests import BoundReportRequest

PROFILE_ID = "approved-dna-png-bound-candidate-v1"
DNA_SECTION_ID = "original-dna-illustration"
CREDIT_SECTION_ID = "visual-asset-credits"
_APPROVED_BUNDLE = "c4332907b19ae45524534ad352c1b521db08ec9fc8fd88ae73fa2ec4ba8c0eeb"
_ACCEPTANCE_NS = 330 * 1000 * 1000 * 1000


class OriginalDnaCandidateError(ValueError):
    """One content-free refusal; no partial PDF or uncontrolled fallback."""


def _require(value: bool) -> None:
    if not value:
        raise OriginalDnaCandidateError("original_dna_candidate_unverified")


@dataclass(frozen=True, slots=True, repr=False)
class PreparedOriginalDna:
    html: str = field(repr=False)
    presentation_ir: dict = field(repr=False)
    resources: RasterBundle = field(repr=False)
    source_credit: PreparedBrandCredit = field(repr=False)
    html_sha256: str
    ir_sha256: str
    expected_text_sha256: str
    _seal: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _require(type(self) is PreparedOriginalDna and type(self.html) is str
                 and type(self.presentation_ir) is dict
                 and type(self.source_credit) is PreparedBrandCredit
                 and type(self.resources) is RasterBundle)
        credit = self.source_credit.to_dict()
        _require(credit["brand_bundle_sha256"] == _APPROVED_BUNDLE
                 and credit["permission_sha256"] == _PERMISSION_SHA256)
        assets = self.resources.assets
        _require(len(assets) == 1 and assets[0].asset_id == "dna-illustration"
                 and assets[0].expected_sha256 == _DNA_SHA256
                 and assets[0].source_sha256 == _DNA_SHA256
                 and assets[0].rights_sha256 == _PERMISSION_SHA256
                 and assets[0].width_px == 324 and assets[0].height_px == 1154)
        self.resources.to_bytes()
        original = self.source_credit.presentation_ir["components"]
        current = self.presentation_ir.get("components")
        _require(generated._validate_ir(self.presentation_ir)
                 and type(current) is list and len(current) == 7
                 and current[:5] == original[:5]
                 and current[-1] == original[-1]
                 and [item["component_id"] for item in current[-2:]]
                 == [DNA_SECTION_ID, CREDIT_SECTION_ID])
        digest, text_digest = generated._expected_content(self.presentation_ir)
        _require(digest == self.ir_sha256 and text_digest == self.expected_text_sha256)
        _require('<svg' not in self.html and 'data:image' not in self.html
                 and 'file://' not in self.html and 'https://' not in self.html)
        _require(self.html.count('data-component="' + DNA_SECTION_ID + '"') == 1
                 and self.html.count('data-component="' + CREDIT_SECTION_ID + '"') == 1
                 and self.html.replace("\n", "").count(REQUIRED_CREDIT_EN) == 1
                 and self.html.count(assets[0].uri) == 1
                 and self.html.endswith("</body></html>")
                 and sha256(self.html.encode("utf-8")).hexdigest() == self.html_sha256)
        seal = sha256("\x00".join((self.html_sha256, self.ir_sha256,
            text_digest, self.resources.bundle_sha256, _APPROVED_BUNDLE)).encode("ascii")).hexdigest()
        _require(not hasattr(self, "_seal") or self._seal == seal)
        object.__setattr__(self, "_seal", seal)

    def to_dict(self) -> dict:
        try:
            self.__post_init__()
            credit = self.source_credit.to_dict()
        except Exception:
            raise OriginalDnaCandidateError("original_dna_candidate_unverified") from None
        return {
            "schema_version": "1.0.0", "profile_id": PROFILE_ID,
            "status": "EXACT_ORIGINAL_DNA_HTML_BOUND",
            "scope": "OPT_IN_SYNTHETIC_EN_US_QUALIFICATION_ONLY",
            "source_ir_sha256": credit["source_ir_sha256"],
            "credited_ir_sha256": credit["credited_ir_sha256"],
            "ir_sha256": self.ir_sha256, "expected_text_sha256": self.expected_text_sha256,
            "html_sha256": self.html_sha256, "resources_sha256": self.resources.bundle_sha256,
            "brand_bundle_sha256": credit["brand_bundle_sha256"],
            "png_original_sha256": _DNA_SHA256,
            "permission_sha256": _PERMISSION_SHA256,
            "credit": REQUIRED_CREDIT_EN, "credit_count": 1,
            "source_components_preserved": True,
            "png_original_unmodified": True, "logo_svg_included": False,
            "pdf_inclusion_verified": False, "visual_parity_verified": False,
            "full_accessibility_verified": False, "release_authorized": False,
        }


def _prepare(*, bound_request: BoundReportRequest, assets: BoundBrandAssets,
        expected_brand_bundle_sha256: str) -> PreparedOriginalDna:
    _require(type(bound_request) is BoundReportRequest
             and type(assets) is BoundBrandAssets
             and type(expected_brand_bundle_sha256) is str
             and expected_brand_bundle_sha256 == _APPROVED_BUNDLE
             and assets.to_dict()["bundle_sha256"] == _APPROVED_BUNDLE)
    credited = prepare_brand_credit_pdf_candidate(
        bound_request=bound_request, assets=assets,
        expected_brand_bundle_sha256=expected_brand_bundle_sha256)
    original = credited.presentation_ir
    _require(original["locale_id"] == "en-US"
             and original["components"][-1]["component_id"] == CREDIT_SECTION_ID)
    resource = RasterAsset(
        "dna-illustration", assets.resolve("dna-illustration"), _DNA_SHA256,
        324, 1154, _DNA_SHA256, _PERMISSION_SHA256)
    resources = RasterBundle((resource,))
    ir = deepcopy(original)
    ir["components"].insert(-1, {
        "component_id": DNA_SECTION_ID,
        "component_type": "semantic-section",
        "title": "OmniGenis original DNA illustration",
        "state": "PRESENT",
        "content": {"asset_id": "dna-illustration", "sha256": _DNA_SHA256,
                    "media_type": "image/png", "purpose": "decorative qualification only"},
    })
    ir = generated._capture_adapter_ir(ir)
    _require(generated._validate_ir(ir)
             and ir["components"][:5] == original["components"][:5]
             and ir["components"][-1] == original["components"][-1])
    html, ids = bounded._bounded_semantic_html(ir)
    _require(ids[-2:] == [DNA_SECTION_ID, CREDIT_SECTION_ID])
    marker = '<section data-component="' + DNA_SECTION_ID + '">'
    _require(html.count(marker) == 1)
    style = ('break-before:page;min-height:790px;padding-right:240px;'
             'background-repeat:no-repeat;background-position:right bottom;'
             'background-size:auto 710px;'
             "background-image:url('" + resource.uri + "')")
    html = html.replace(marker, marker[:-1] + ' style="' + style + '">')
    # The exact source heading must remain one physical text run; otherwise
    # a narrower caption column drops its source space during PDF extraction.
    # Only this fixed and admitted synthetic DNA section is affected.
    heading = '<h2>OmniGenis original DNA illustration</h2>'
    _require(html.count(heading) == 1)
    html = html.replace(heading,
        '<h2 style="white-space:nowrap">OmniGenis original DNA illustration</h2>', 1)
    encoded = html.encode("utf-8")
    _require(0 < len(encoded) <= raster.worker.HTML_BYTES)
    ir_sha, text_sha = generated._expected_content(ir)
    return PreparedOriginalDna(html, ir, resources, credited,
                               sha256(encoded).hexdigest(), ir_sha, text_sha)


def prepare_original_dna_pdf_candidate(*, bound_request: BoundReportRequest,
        assets: BoundBrandAssets, expected_brand_bundle_sha256: str
) -> PreparedOriginalDna:
    """Prepare approved PNG with the source-derived credit, no PDF rendering."""
    try:
        return _prepare(bound_request=bound_request, assets=assets,
                        expected_brand_bundle_sha256=expected_brand_bundle_sha256)
    except Exception:
        raise OriginalDnaCandidateError("original_dna_candidate_unverified") from None


def execute_original_dna_pdf_candidate(*, bound_request: BoundReportRequest,
        assets: BoundBrandAssets, expected_brand_bundle_sha256: str
) -> tuple[raster.RasterPdfCandidate, native.PdfNativeSourceResult, dict]:
    """Render and independently inspect one exact bounded opt-in PDF candidate."""
    params = dict(bound_request=bound_request, assets=assets,
                  expected_brand_bundle_sha256=expected_brand_bundle_sha256)
    try:
        prepared = _prepare(**params)
        initial = generated._json_bytes(prepared.to_dict())
        started = monotonic_ns()

        def check() -> None:
            _require(0 <= monotonic_ns() - started < _ACCEPTANCE_NS)
            another = _prepare(**params)
            _require(generated._json_bytes(another.to_dict()) == initial
                     and another.html == prepared.html
                     and another.resources.to_bytes() == prepared.resources.to_bytes())
            prepared.to_dict()

        check()
        pdf = raster.render_preloaded_raster_pdf(
            html=prepared.html, expected_html_sha256=prepared.html_sha256,
            resources=prepared.resources,
            expected_resources_sha256=prepared.resources.bundle_sha256)
        check()
        render_record = pdf.to_dict()
        _require(render_record["html_sha256"] == prepared.html_sha256
                 and render_record["resources_sha256"] == prepared.resources.bundle_sha256
                 and render_record["resource_count"] == 1)
        native_result = native.execute_pdf_native_source_job(
            presentation_ir=prepared.presentation_ir, pdf_bytes=pdf.pdf_bytes,
            expected_pdf_sha256=pdf.pdf_sha256, expected_ir_sha256=prepared.ir_sha256,
            page_count=pdf.page_count, expected_language="en-US",
            namespace_id=bound_request.request.source_binding.identity.namespace_id,
            job_id=bound_request.request.request_id,
            request_sha256=bound_request.request.request_sha256,
            expected_resource_profile_sha256=native.PROFILE_SHA256)
        check()
        inspection = native_result.to_dict()
        _require(inspection["normalized_text_equivalence_verified"] is True
                 and inspection["pdf_sha256"] == pdf.pdf_sha256
                 and inspection["ir_sha256"] == prepared.ir_sha256
                 and inspection["expected_text_sha256"] == prepared.expected_text_sha256)
        receipt = prepared.to_dict() | {
            "status": "SAME_ARTIFACT_RENDER_AND_NATIVE_INSPECTION",
            "pdf_sha256": pdf.pdf_sha256, "pdf_bytes": len(pdf.pdf_bytes),
            "page_count": pdf.page_count,
            "render_result_sha256": sha256(generated._json_bytes(render_record)).hexdigest(),
            "native_inspection_sha256": sha256(native_result.to_bytes()).hexdigest(),
            "same_pdf_text_verified": True, "release_authorized": False,
        }
        check()
        return pdf, native_result, receipt
    except Exception:
        raise OriginalDnaCandidateError("original_dna_execution_unverified") from None
