"""Opt-in editorial styling over the existing validated typed PDF renderer.

Only the current sealed en-US technical projection is admitted. This wrapper
reuses the fixed installed renderer, original pinned Lato profile and inert
typed HTML. It does not select scientific facts, copy reference artwork, add a
second renderer, authenticate independent accessibility or publish a report.
Style/readability QA is separate from editorial equivalence and release.
"""
from __future__ import annotations

from hashlib import sha256
import os

from reporting.adapters import (
    FIXED_FONT_STYLE_ID, _FIXED_SEMANTIC_CSS,
    GENERATED_PDF_AUTHOR, GENERATED_PDF_PRODUCT,
)
from reporting._pdf_render_worker import HTML_BYTES
from reporting.pdf_generated_source_job import (
    PdfRenderedCandidate, render_pdf_candidate,
)
from reporting.typed_html import TypedHtmlValidation, prepare_typed_html


PROFILE_ID = "typed-editorial-pdf-candidate-v1"
_DISABLE = "OMNIGENIS_TYPED_EDITORIAL_PDF_DISABLED"
_SOURCE_HEAD = ('<!doctype html><html lang="en-US"><head>'
                '<meta charset="utf-8"><title>OmniGenis</title></head><body>')
_SOURCE_TAIL = "</body></html>"

# Source-derived fixed palette and existing licensed font: no arbitrary caller
# CSS, external resources, font uploads, executable content or PDF actions.
_EDITORIAL_CSS = (
    "h1{font-size:25pt;line-height:30pt;color:#003890;"
    "border-bottom:2pt solid #00D0F0;padding-bottom:10pt;break-after:avoid}"
    "h2{font-size:15pt;line-height:20pt;color:#003890;"
    "margin-top:16pt;margin-bottom:8pt;break-after:avoid}"
    "p{white-space:pre-wrap;overflow-wrap:anywhere}"
    "table{width:100%;table-layout:fixed;border-collapse:collapse;"
    "margin-top:12pt;margin-bottom:14pt;}"
    "thead{display:table-header-group}"
    "thead tr{break-inside:avoid}"
    "tbody tr{break-inside:auto}"
    "caption{text-align:left;font-weight:700;color:#0B1F33;"
    "margin-bottom:7pt}"
    "th{background:#F1F6FA;color:#0B1F33;text-align:left;font-weight:700}"
    "th,td{padding:6pt 7pt;overflow-wrap:anywhere;white-space:pre-wrap;"
    "border-bottom:0.7pt solid #D7E2EC;vertical-align:top}"
)
_ALL_CSS = _FIXED_SEMANTIC_CSS + _EDITORIAL_CSS
STYLE_SHA256 = sha256(_ALL_CSS.encode("ascii")).hexdigest()


class EditorialTypedPdfError(ValueError):
    """Content-free fail-closed exception, never a partial successful PDF."""


def _enabled() -> None:
    """Keep the candidate opt-in and explicitly disableable."""
    if os.environ.get(_DISABLE) == "1":
        raise EditorialTypedPdfError("editorial_typed_pdf_unavailable")


def _record(typed_html_sha256: str, source_ir_sha256: str,
            html_sha256: str) -> dict:
    """Return detached technical evidence without a clinical or release claim."""
    return {
        "schema_version": "1.0.0",
        "profile_id": PROFILE_ID,
        "status": "STAGED_TECHNICAL_CANDIDATE",
        "source_ir_sha256": source_ir_sha256,
        "typed_html_sha256": typed_html_sha256,
        "html_sha256": html_sha256,
        "style_sha256": STYLE_SHA256,
        "font_style_id": FIXED_FONT_STYLE_ID,
        "source_semantics_validated": False,
        "visual_equivalence_verified": False,
        "human_accessibility_verified": False,
        "whole_job_resources_qualified": False,
        "default_pipeline_activated": False,
        "release_authorized": False,
    }


def prepare_editorial_typed_html(*, presentation_ir: object,
                                 expected_ir_sha256: object) -> tuple[str, dict]:
    """Reuse existing typed admission and preserve the semantic body exactly."""
    try:
        _enabled()
        source_html, proof = prepare_typed_html(
            presentation_ir=presentation_ir,
            expected_ir_sha256=expected_ir_sha256,
        )
        if (type(proof) is not TypedHtmlValidation or not proof.passed
                or not source_html.startswith(_SOURCE_HEAD)
                or not source_html.endswith(_SOURCE_TAIL)):
            raise ValueError("typed source not validated")
        body = source_html[len(_SOURCE_HEAD):-len(_SOURCE_TAIL)]
        styled_head = (
            '<!doctype html><html lang="en-US"><head><meta charset="utf-8">'
            '<title>OmniGenis</title>'
            f'<meta name="author" content="{GENERATED_PDF_AUTHOR}">'
            f'<meta name="generator" content="{GENERATED_PDF_PRODUCT}">'
            f'<meta name="omnigenis-font-style" content="{FIXED_FONT_STYLE_ID}">'
            f'<style>{_ALL_CSS}</style></head><body>'
        )
        html = styled_head + body + _SOURCE_TAIL
        encoded = html.encode("utf-8")
        if not 0 < len(encoded) <= HTML_BYTES:
            raise ValueError("render input budget")
        digest = sha256(encoded).hexdigest()
        return html, _record(proof.html_sha256, proof.ir_sha256, digest)
    except Exception:
        raise EditorialTypedPdfError("editorial_typed_pdf_unavailable") from None


def render_editorial_typed_pdf(*, presentation_ir: object,
                               expected_ir_sha256: object) -> tuple[PdfRenderedCandidate, dict]:
    """Use one existing bounded renderer; never mark the result as final."""
    html, record = prepare_editorial_typed_html(
        presentation_ir=presentation_ir, expected_ir_sha256=expected_ir_sha256)
    try:
        _enabled()
        pdf = render_pdf_candidate(html, record["html_sha256"])
        if (type(pdf) is not PdfRenderedCandidate
                or not 1 <= pdf.page_count <= 64
                or sha256(pdf.pdf_bytes).hexdigest() != pdf.pdf_sha256):
            raise ValueError("render result outside bounded profile")
        return pdf, {**record, "pdf_sha256": pdf.pdf_sha256,
                     "page_count": pdf.page_count}
    except Exception:
        raise EditorialTypedPdfError("editorial_typed_pdf_unavailable") from None
