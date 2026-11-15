"""Opt-in candidate QA for one original-logo SVG Figure on a technical PDF.

Only the object-safety gate differs from the existing passive v1 conjunction:
it invokes the separate bounded Figure /Layout structural successor. Every
other existing validator and its exact scalar evidence contract is reused.
This does not activate the profile globally, qualify native source inspection,
certify PDF/UA, establish visual-semantic parity or authorize publication.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from reporting import pdf_candidate_qa as inherited
from reporting.pdf_content import validate_pdf_text_content
from reporting.pdf_original_svg_layout_safety import (
    OriginalSvgLayoutValidation, validate_original_svg_layout_safety,
)
from reporting.pdf_qa import GENERATED_PDF_AUTHOR, GENERATED_PDF_METADATA_PROFILE
from reporting.pdf_validation import (
    validate_generated_accessibility, validate_generated_metadata,
)
from reporting.pdf_visual import validate_pdf_visual_geometry

PROFILE_ID = "original-brand-figure-candidate-qa-v1"
_STAGES = (
    "identity_preflight", "svg_figure_layout", "controlled_metadata",
    "accessibility_prerequisites", "text_content", "visual_geometry",
)
_ERRORS = frozenset({
    "combined_pdf_qa_input_invalid", "combined_pdf_qa_identity_mismatch",
    "combined_pdf_qa_safety_blocked", "combined_pdf_qa_child_blocked",
    "combined_pdf_qa_child_invalid", "combined_pdf_qa_worker_failed",
})


@dataclass(frozen=True, slots=True)
class OriginalBrandCandidateQA:
    """Bounded scalar validation, not a release token or external approval."""

    pdf_sha256: str | None
    ir_sha256: str | None
    page_count: int | None
    statuses: tuple[tuple[str, str], ...]
    errors: tuple[str, ...]

    def __post_init__(self) -> None:
        if (type(self.statuses) is not tuple or len(self.statuses) != len(_STAGES)
                or tuple(name for name, _ in self.statuses) != _STAGES
                or any(type(name) is not str or type(status) is not str
                       or status not in ("PASS", "BLOCKED", "NOT_EXECUTED")
                       for name, status in self.statuses)):
            raise ValueError("invalid candidate QA stages")
        if (type(self.errors) is not tuple or len(self.errors) > 1 or any(
                type(error) is not str or error not in _ERRORS for error in self.errors)):
            raise ValueError("invalid candidate QA error")
        states = tuple(status for _, status in self.statuses)
        if "BLOCKED" in states:
            at = states.index("BLOCKED")
            if (len(self.errors) != 1 or any(s != "PASS" for s in states[:at])
                    or any(s != "NOT_EXECUTED" for s in states[at + 1:])
                    or self.page_count is not None):
                raise ValueError("contradictory blocked candidate")
        elif (states != ("PASS",) * len(_STAGES) or self.errors
              or not inherited._digest(self.pdf_sha256)
              or not inherited._digest(self.ir_sha256)
              or type(self.page_count) is not int or not 1 <= self.page_count <= 32):
            raise ValueError("incomplete candidate PASS")
        if self.pdf_sha256 is not None and not inherited._digest(self.pdf_sha256):
            raise ValueError("invalid PDF SHA")
        if self.ir_sha256 is not None and not inherited._digest(self.ir_sha256):
            raise ValueError("invalid IR SHA")

    @property
    def passed(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "schema_version": "1.0.0", "profile_id": PROFILE_ID,
            "status": "PASS" if self.passed else "BLOCKED",
            "pdf_sha256": self.pdf_sha256, "ir_sha256": self.ir_sha256,
            "page_count": self.page_count,
            "stages": {name: status for name, status in self.statuses},
            "errors": list(self.errors),
            "default_passive_v1_policy_unchanged": True,
            "svg_structural_policy": "ONE_FIGURE_LAYOUT_BBOX_ONLY",
            "native_source_inspection_qualified": False,
            "source_asset_inclusion_authenticated_by_pdf_qa": False,
            "visual_semantic_parity_verified": False,
            "human_assistive_accessibility_verified": False,
            "pdf_ua_certified": False, "release_authorized": False,
        }


def validate_original_brand_candidate_qa(
    *, presentation_ir: object, expected_ir_sha256: object,
    pdf_bytes: object, expected_pdf_sha256: object,
) -> OriginalBrandCandidateQA:
    """Re-run every real applicable child on one exact immutable PDF/IR pair."""
    pdf_digest = ir_digest = None
    stages: list[tuple[str, str]] = []

    def block(name: str, code: str) -> OriginalBrandCandidateQA:
        stages.append((name, "BLOCKED"))
        stages.extend((item, "NOT_EXECUTED") for item in _STAGES[len(stages):])
        return OriginalBrandCandidateQA(
            pdf_digest, ir_digest, None, tuple(stages), (code,),
        )

    try:
        if (type(pdf_bytes) is not bytes or not pdf_bytes.startswith(b"%PDF-")
                or len(pdf_bytes) > inherited.MAX_PDF_BYTES
                or not inherited._digest(expected_pdf_sha256)
                or not inherited._digest(expected_ir_sha256)):
            return block(_STAGES[0], "combined_pdf_qa_input_invalid")
        pdf_digest = sha256(pdf_bytes).hexdigest()
        if pdf_digest != expected_pdf_sha256:
            return block(_STAGES[0], "combined_pdf_qa_identity_mismatch")
        snapshot = inherited._snapshot_ir(presentation_ir)
        if snapshot.get("locale_id") != "en-US":
            return block(_STAGES[0], "combined_pdf_qa_input_invalid")
        ir_digest, expected_text_sha256 = inherited._expected_content(snapshot)
        if ir_digest != expected_ir_sha256:
            return block(_STAGES[0], "combined_pdf_qa_identity_mismatch")
    except Exception:
        return block(_STAGES[0], "combined_pdf_qa_input_invalid")
    stages.append((_STAGES[0], "PASS"))

    try:
        structural = validate_original_svg_layout_safety(
            pdf_bytes=pdf_bytes, expected_pdf_sha256=pdf_digest)
        if (type(structural) is not OriginalSvgLayoutValidation
                or not structural.passed or structural.pdf_sha256 != pdf_digest
                or type(structural.page_count) is not int
                or not 1 <= structural.page_count <= 32
                or structural.figure_layout_count != 1):
            return block(_STAGES[1], "combined_pdf_qa_safety_blocked")
        pages = structural.page_count
    except Exception:
        return block(_STAGES[1], "combined_pdf_qa_worker_failed")
    stages.append((_STAGES[1], "PASS"))

    checks = (
        ("controlled_metadata", lambda: validate_generated_metadata(
            pdf_bytes=pdf_bytes, expected_pdf_sha256=pdf_digest,
            expected_author=GENERATED_PDF_AUTHOR, expected_language="en-US",
            metadata_profile=GENERATED_PDF_METADATA_PROFILE)),
        ("accessibility_prerequisites", lambda: validate_generated_accessibility(
            pdf_bytes=pdf_bytes, expected_language="en-US")),
        ("text_content", lambda: validate_pdf_text_content(
            presentation_ir=snapshot, expected_ir_sha256=ir_digest,
            pdf_bytes=pdf_bytes, expected_pdf_sha256=pdf_digest)),
        ("visual_geometry", lambda: validate_pdf_visual_geometry(
            pdf_bytes=pdf_bytes, expected_pdf_sha256=pdf_digest)),
    )
    for name, call in checks:
        try:
            child = call()
            evidence = inherited._project(name, child, pdf_digest, ir_digest)
            inherited._check_evidence(
                name, evidence, pdf_sha256=pdf_digest, ir_sha256=ir_digest,
                page_count=pages, expected_text_sha256=expected_text_sha256,
            )
        except inherited._ChildRejected:
            return block(name, "combined_pdf_qa_child_blocked")
        except Exception:
            return block(name, "combined_pdf_qa_child_invalid")
        stages.append((name, "PASS"))
    return OriginalBrandCandidateQA(
        pdf_digest, ir_digest, pages, tuple(stages), (),
    )
