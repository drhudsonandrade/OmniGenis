"""Opt-in safety evidence for one inert, original-logo PDF figure attribute.

This qualifies only the separately inspected structural /A /O Layout /BBox
shape. It does not change passive-report-pdf-v1, authenticate brand source
bytes, permit active PDF actions, certify PDF/UA or authorize a report release.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from reporting import _pdf_original_svg_layout_worker as worker
from reporting.pdf_safety import (
    _SafetyWorkerResponseOverflow, _exchange_safety_pipes, _wait_safety_worker,
)

PROFILE_ID = "passive-report-pdf-svg-figure-layout-v2"
_WORKER = Path(__file__).with_name("_pdf_original_svg_layout_worker.py")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ERRORS = worker._ERRORS | frozenset({
    "expected_pdf_digest_invalid", "pdf_digest_mismatch",
    "pdf_safety_worker_timeout", "pdf_safety_worker_failed",
    "pdf_safety_worker_invalid_response", "pdf_safety_worker_unavailable",
})


def _digest(value: object) -> bool:
    return type(value) is str and _HASH.fullmatch(value) is not None


@dataclass(frozen=True, slots=True)
class OriginalSvgLayoutValidation:
    """Only fixed scalar evidence from one exact immutable PDF."""

    pdf_sha256: str | None
    page_count: int | None
    object_count: int | None
    figure_layout_count: int | None
    errors: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.errors) is not tuple or any(
                type(error) is not str or error not in _ERRORS for error in self.errors):
            raise ValueError("invalid structural layout response errors")
        if self.pdf_sha256 is not None and not _digest(self.pdf_sha256):
            raise ValueError("invalid PDF identity")
        if not self.errors:
            if (not _digest(self.pdf_sha256) or
                    type(self.page_count) is not int or not 1 <= self.page_count <= worker._PAGE_LIMIT or
                    type(self.object_count) is not int or not 1 <= self.object_count <= worker._VISIT_LIMIT or
                    type(self.figure_layout_count) is not int or self.figure_layout_count != 1):
                raise ValueError("incomplete structural layout PASS")
        elif (self.page_count is not None or self.object_count is not None or
              self.figure_layout_count is not None):
            raise ValueError("partial structural layout response")

    @property
    def passed(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "profile_id": PROFILE_ID,
            "status": "PASS" if self.passed else "BLOCKED",
            "pdf_sha256": self.pdf_sha256,
            "page_count": self.page_count,
            "object_count": self.object_count,
            "figure_layout_count": self.figure_layout_count,
            "errors": list(self.errors),
            "v1_default_policy_unchanged": True,
            "allowed_structural_shape": "ONE_FIGURE_LAYOUT_BBOX_ONLY",
            "interactive_pdf_actions_allowed": False,
            "original_logo_source_authenticated": False,
            "visual_parity_verified": False,
            "assistive_accessibility_verified": False,
            "release_authorized": False,
            "limitations": [
                "A separate opt-in inspection of generated PDFs, not uploaded arbitrary files.",
                "The default passive-report-pdf-v1 policy still rejects every /A.",
                "Only one reachable /StructElem /Figure with /A /O /Layout and a bounded /BBox is allowed.",
                "The original PDF bytes are unchanged; only an ephemeral parsed graph is inspected.",
                "Source binding, rendering fidelity, text, metadata, full QA, accessibility and release are separate gates.",
            ],
        }


def _run_worker(pdf_bytes: bytes) -> bytes:
    """Reuse the v1 bounded pipe protocol and direct-child lifecycle logic."""
    process = subprocess.Popen(
        [sys.executable, "-I", str(_WORKER), str(os.getpid())],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
    )
    completed = False
    try:
        deadline = time.monotonic() + worker._WALL_LIMIT
        output = _exchange_safety_pipes(process, pdf_bytes, deadline)
        if time.monotonic() > deadline:
            raise subprocess.TimeoutExpired(process.args, worker._WALL_LIMIT)
        result = _wait_safety_worker(process, deadline)
        completed = True
        if result != 0:
            raise RuntimeError("safety worker failed")
        return output
    finally:
        if not completed:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


def _decode(output: bytes, expected_digest: str) -> OriginalSvgLayoutValidation:
    """Refuse unknown, duplicated, incomplete or contradictory worker fields."""
    def pairs_without_duplicates(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("duplicate worker response key")
            obj[key] = value
        return obj

    def reject_float(value):
        raise ValueError("worker JSON float not allowed")

    obj = json.loads(output, object_pairs_hook=pairs_without_duplicates,
                     parse_float=reject_float, parse_constant=reject_float)
    if (type(obj) is not dict or set(obj) != {
            "status", "pdf_sha256", "page_count", "object_count",
            "figure_layout_count", "errors"} or
            obj["pdf_sha256"] not in (expected_digest, None)):
        raise ValueError("invalid structural layout response")
    errors = obj["errors"]
    if type(errors) is not list or len(errors) > 8 or any(
            type(error) is not str or error not in _ERRORS for error in errors):
        raise ValueError("invalid structural layout error list")
    if obj["status"] == "PASS" and errors:
        raise ValueError("contradictory PASS response")
    if obj["status"] == "BLOCKED" and not errors:
        raise ValueError("contradictory BLOCKED response")
    if obj["status"] not in ("PASS", "BLOCKED"):
        raise ValueError("unrecognized structural layout status")
    return OriginalSvgLayoutValidation(
        obj["pdf_sha256"], obj["page_count"], obj["object_count"],
        obj["figure_layout_count"], tuple(errors),
    )


def validate_original_svg_layout_safety(
    *, pdf_bytes: object, expected_pdf_sha256: object,
) -> OriginalSvgLayoutValidation:
    """Inspect exact generated bytes under a separate, nondefault v2 policy."""
    digest = None

    def fail(error: str) -> OriginalSvgLayoutValidation:
        return OriginalSvgLayoutValidation(digest, None, None, None, (error,))

    if type(pdf_bytes) is not bytes or not pdf_bytes.startswith(b"%PDF-"):
        return fail("pdf_safety_structure_invalid")
    if len(pdf_bytes) > worker._PDF_LIMIT:
        return fail("pdf_safety_budget_exceeded")
    digest = sha256(pdf_bytes).hexdigest()
    if not _digest(expected_pdf_sha256):
        return fail("expected_pdf_digest_invalid")
    if digest != expected_pdf_sha256:
        return fail("pdf_digest_mismatch")
    try:
        result = _run_worker(pdf_bytes)
        validated = _decode(result, digest)
        if validated.passed and validated.pdf_sha256 != digest:
            raise ValueError("missing PASS PDF identity")
        return validated
    except subprocess.TimeoutExpired:
        return fail("pdf_safety_worker_timeout")
    except RuntimeError:
        return fail("pdf_safety_worker_failed")
    except (ValueError, UnicodeError, json.JSONDecodeError, _SafetyWorkerResponseOverflow):
        return fail("pdf_safety_worker_invalid_response")
    except (OSError, AttributeError, TypeError):
        return fail("pdf_safety_worker_unavailable")
