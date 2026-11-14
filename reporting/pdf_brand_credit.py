"""Opt-in, source-bound PDF credit preparation for the two original brand assets.

This adds one visible en-US credit section to a separately qualified report
candidate. It preserves the source inventory and permission bytes and does not
embed or decode either image. Image admission, actual PDF inclusion, native
media safety, frozen visual comparison, accessibility, final QA and release
remain separate obligations. No default pipeline or executor is activated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from html import escape

from reporting import pdf_bound_candidate as bound_source
from reporting import pdf_generated_source_job as generated
from reporting.brand_assets import (
    BoundBrandAssets, BrandAssetError, REQUIRED_CREDIT_EN,
)
from reporting.pdf_bound_raster_candidate import _bounded_semantic_html
from reporting.requests import BoundReportRequest

PROFILE_ID = "original-brand-credit-html-v1"
CREDIT_SECTION_ID = "visual-asset-credits"
_CLOSING_HTML = "</body></html>"
_MAX_HTML_BYTES = 64 * 1024 * 1024


class BrandCreditError(ValueError):
    """Content-free failure; no partial credited document is returned."""


def _require(condition: bool) -> None:
    if not condition:
        raise BrandCreditError("brand_credit_candidate_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class PreparedBrandCredit:
    """One exact, non-rendered HTML candidate, retained under its byte identity."""

    html: str = field(repr=False)
    html_sha256: str
    source_ir_sha256: str
    brand_bundle_sha256: str
    permission_sha256: str
    _seal: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _require(type(self.html) is str and self.html.endswith(_CLOSING_HTML))
        payload = self.html.encode("utf-8")
        _require(0 < len(payload) <= _MAX_HTML_BYTES)
        _require(sha256(payload).hexdigest() == self.html_sha256)
        for value in (self.source_ir_sha256, self.brand_bundle_sha256, self.permission_sha256):
            _require(type(value) is str and len(value) == 64
                     and all(char in "0123456789abcdef" for char in value))
        _require(self.html.count('data-component="' + CREDIT_SECTION_ID + '"') == 1)
        _require(self.html.count("<p>" + escape(REQUIRED_CREDIT_EN) + "</p>") == 1)
        identity = "\x00".join((self.html_sha256, self.source_ir_sha256,
            self.brand_bundle_sha256, self.permission_sha256))
        seal = sha256(identity.encode("ascii")).hexdigest()
        _require(not hasattr(self, "_seal") or self._seal == seal)
        object.__setattr__(self, "_seal", seal)

    def to_dict(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "schema_version": "1.0.0",
            "profile_id": PROFILE_ID,
            "status": "CREDIT_HTML_PREPARED",
            "scope": "OPT_IN_CREDIT_HTML_ONLY",
            "credit_section_id": CREDIT_SECTION_ID,
            "credit": REQUIRED_CREDIT_EN,
            "source_ir_sha256": self.source_ir_sha256,
            "brand_bundle_sha256": self.brand_bundle_sha256,
            "permission_sha256": self.permission_sha256,
            "html_sha256": self.html_sha256,
            "pdf_inclusion_verified": False,
            "pdf_credit_verified": False,
            "native_media_decoding_qualified": False,
            "visual_parity_verified": False,
            "accessibility_verified": False,
            "release_authorized": False,
        }


def prepare_brand_credit_pdf_candidate(
    *, bound_request: BoundReportRequest, assets: BoundBrandAssets,
    expected_brand_bundle_sha256: str,
) -> PreparedBrandCredit:
    """Bind one mandatory notice to the admitted technical en-US source.

    No caller-provided HTML, credit, asset name, locale, renderer or resource
    resolution is accepted. Reuses the existing request/source qualification
    policy and sealed two-original permission; does not run a PDF worker.
    """
    try:
        _require(type(assets) is BoundBrandAssets
                 and type(bound_request) is BoundReportRequest
                 and type(expected_brand_bundle_sha256) is str)
        admitted = assets.to_dict()
        _require(admitted["bundle_sha256"] == expected_brand_bundle_sha256
                 and admitted["required_credit_en"] == REQUIRED_CREDIT_EN
                 and admitted["credit_obligation"]
                 == "PRODUCT_NOTICES_AND_ONCE_IN_EACH_PDF_USING_THE_ASSETS")
        args, source = bound_source._prepare(
            bound_request, generated.PROFILE_SHA256, "technical-assembly-v1")
        ir = args["presentation_ir"]
        _require(ir["locale_id"] == "en-US"
                 and len(ir["components"]) == 5
                 and source["ir_sha256"] == args["expected_ir_sha256"])
        source_html, ids = _bounded_semantic_html(ir)
        _require(ids == [c["component_id"] for c in ir["components"]]
                 and CREDIT_SECTION_ID not in ids
                 and source_html.endswith(_CLOSING_HTML)
                 and source_html.count(_CLOSING_HTML) == 1
                 and REQUIRED_CREDIT_EN not in source_html)
        credit = (
            '<section data-component="' + CREDIT_SECTION_ID + '" '
            'aria-label="Visual asset credits"><h2>Visual asset credits</h2>'
            '<p>' + escape(REQUIRED_CREDIT_EN) + '</p></section>'
        )
        html = source_html[:-len(_CLOSING_HTML)] + credit + _CLOSING_HTML
        raw = html.encode("utf-8")
        _require(0 < len(raw) <= _MAX_HTML_BYTES)
        permission = admitted["permission_reference"]["sha256"]
        return PreparedBrandCredit(
            html, sha256(raw).hexdigest(), args["expected_ir_sha256"],
            admitted["bundle_sha256"], permission,
        )
    except (BrandCreditError, BrandAssetError, ValueError, TypeError, KeyError,
            AttributeError, UnicodeError, OverflowError):
        raise BrandCreditError("brand_credit_candidate_invalid") from None
