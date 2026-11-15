"""Separate opt-in original brand + pagination technical PDF candidate.

The input comes only from the already admitted source-bound brand compiler.
This new profile does not change either existing worker, authorize a release, or
claim a human assistive-technology result. No PDF postprocessing is performed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

from reporting import _pdf_pagination_core as pagination
from reporting import _pdf_render_worker as engine
from reporting import _pdf_typed_observation_worker as native
from reporting import pdf_raster_render as raster
from reporting import pdf_typed_observation as typed
from reporting._pdf_native_launch_budget import require_no_active_scope
from reporting._pdf_process_lifecycle import wait_owned_process_group
from reporting.adapters import _PDF_RENDER_ENV_LOCK, _start_pdf_render_worker, _wait_pdf_render_worker
from reporting.brand_assets import BoundBrandAssets, _DNA_SHA256, _LOGO_SHA256
from reporting.pdf_content import _expected_content
from reporting.pdf_original_brand_inclusion import prepare_original_brand_pdf_candidate
from reporting.pdf_raster_resources import RasterBundle
from reporting.requests import BoundReportRequest

PROFILE_ID = "original-brand-pagination-composite-v1"
OBSERVER_ID = "original-brand-pagination-observer-v1"
MODE = "native-original-brand-pagination-v1"
PROTOCOL_VERSION = 3
_DISABLE = "OMNIGENIS_PDF_ORIGINAL_BRAND_PAGINATION_DISABLED"
_QUALIFICATION = "OMNIGENIS_PDF_QUALIFICATION_RECEIPT_MODE"
_ERROR = "original_brand_pagination_composite_unverified"
_SOURCE_FILES = (
    "reporting/pdf_original_brand_pagination_composite.py",
    "reporting/_pdf_original_brand_pagination_render_worker.py",
    "reporting/_pdf_original_brand_pagination_observer_worker.py",
    "schemas/report-original-brand-pagination-composite.v1.schema.json",
    "tests/test_reporting_pdf_original_brand_pagination_composite.py",
)


class CompositeCandidateError(ValueError):
    """Fixed error: no partial bytes or misleading review status escape."""


def _require(value: bool) -> None:
    if not value:
        raise CompositeCandidateError(_ERROR)


def _sha(value: object) -> None:
    _require(type(value) is str and len(value) == 64
             and all(letter in "0123456789abcdef" for letter in value))


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
                      allow_nan=False).encode("ascii")


def _manifest() -> str:
    """Bind every new code/schema/test source and refuse unexpected symlinks."""
    root = Path(__file__).resolve().parents[1]
    target = root / "reporting/pdf-original-brand-pagination-composite.v1.json"
    _require(not target.is_symlink())
    raw = target.read_bytes()
    _require(1 <= len(raw) <= 8192)
    record = json.loads(raw.decode("ascii"))
    _require(type(record) is dict and set(record) == {
        "schema_version", "profile_id", "sources", "default_pipeline_activated",
        "release_authorized", "max_pages", "pdf_bytes"})
    _require(record["schema_version"] == "1.0.0" and record["profile_id"] == PROFILE_ID
             and record["default_pipeline_activated"] is False
             and record["release_authorized"] is False
             and type(record["max_pages"]) is int and record["max_pages"] == 64
             and type(record["pdf_bytes"]) is int and record["pdf_bytes"] == 16777216
             and type(record["sources"]) is dict
             and set(record["sources"]) == set(_SOURCE_FILES))
    for name in _SOURCE_FILES:
        path = root / name
        expected = record["sources"][name]
        _sha(expected)
        _require(not path.is_symlink() and path.resolve().is_relative_to(root)
                 and sha256(path.read_bytes()).hexdigest() == expected)
    return sha256(raw).hexdigest()


def _profile() -> dict:
    return {
        "schema_version": "1.0.0", "profile_id": PROFILE_ID, "mode": MODE,
        "protocol_version": PROTOCOL_VERSION, "renderer": "weasyprint:70.0",
        "pydyf": "0.12.1", "parser": "pypdf:7.1.0", "max_pages": 64,
        "pdf_bytes": 16777216, "html_bytes": engine.HTML_BYTES,
        "memory_bytes": engine.MEMORY_BYTES, "cpu_seconds": engine.CPU_SECONDS,
        "wall_seconds": engine.WALL_SECONDS, "open_files": engine.OPEN_FILES,
        "pagination_core_sha256": pagination.renderer_profile_sha256(),
        "original_raster_sha256": raster.profile_sha256(),
        "brand_logo_sha256": _LOGO_SHA256, "brand_dna_sha256": _DNA_SHA256,
        "manifest_sha256": _manifest(),
        "qualification_only": True, "default_pipeline_activated": False,
        "release_authorized": False, "human_assistive_accessibility_verified": False,
    }


def composite_profile_sha256() -> str:
    return sha256(_canonical(_profile())).hexdigest()


def _admit() -> str:
    require_no_active_scope()
    _require(sys.platform == "linux" and os.environ.get(_QUALIFICATION) == "qualification-only"
             and all(os.environ.get(name) != "1" for name in (
                 _DISABLE, "OMNIGENIS_PDF_RENDERER_DISABLED",
                 "OMNIGENIS_PDF_PAGINATION_ARTIFACT_DISABLED",
                 "OMNIGENIS_PDF_PRELOADED_RASTER_DISABLED",
                 "OMNIGENIS_PDF_TYPED_OBSERVATION_DISABLED")))
    raster.profile_sha256()
    typed._profile()
    pagination.require_source_manifest()
    return composite_profile_sha256()


@dataclass(frozen=True, slots=True, repr=False)
class CompositeCandidate:
    """Sealed exact PDF identity plus source/brand/renderer digests."""

    _pdf: bytes = field(repr=False)
    _source_ir_bytes: bytes = field(repr=False)
    html_sha256: str
    resources_sha256: str
    brand_bundle_sha256: str
    ir_sha256: str
    expected_text_sha256: str
    page_count: int
    renderer_profile_sha256: str
    _seal: str = field(init=False, repr=False, compare=False)

    def summary(self) -> dict[str, object]:
        _require(type(self) is CompositeCandidate and type(self._pdf) is bytes
                 and self._pdf.startswith(b"%PDF-1.7")
                 and 0 < len(self._pdf) <= engine.NATIVE_PDF_BYTES
                 and type(self.page_count) is int and 1 <= self.page_count <= 64
                 and type(self._source_ir_bytes) is bytes
                 and 0 < len(self._source_ir_bytes) <= engine.NATIVE_PDF_BYTES
                 and sha256(self._source_ir_bytes).hexdigest() == self.ir_sha256)
        for value in (self.html_sha256, self.resources_sha256, self.brand_bundle_sha256,
                      self.ir_sha256, self.expected_text_sha256,
                      self.renderer_profile_sha256):
            _sha(value)
        _require(self.renderer_profile_sha256 == composite_profile_sha256())
        raw_ir = json.loads(self._source_ir_bytes.decode("utf-8"))
        _require(_expected_content(raw_ir) == (self.ir_sha256, self.expected_text_sha256)
                 and self._source_ir_bytes == json.dumps(
                     raw_ir, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode("utf-8"))
        receipt = {
            "status": "OPT_IN_BRAND_PAGINATION_CANDIDATE",
            "profile_id": PROFILE_ID,
            "profile_sha256": self.renderer_profile_sha256,
            "pdf_sha256": sha256(self._pdf).hexdigest(),
            "pdf_bytes": len(self._pdf), "page_count": self.page_count,
            "html_sha256": self.html_sha256,
            "resources_sha256": self.resources_sha256,
            "ir_sha256": self.ir_sha256,
            "expected_text_sha256": self.expected_text_sha256,
            "brand_bundle_sha256": self.brand_bundle_sha256,
            "original_logo_sha256": _LOGO_SHA256,
            "original_dna_sha256": _DNA_SHA256,
            "default_pipeline_activated": False,
            "source_asset_bytes_authenticated_from_pdf": False,
            "human_assistive_accessibility_verified": False,
            "aggregate_job_resources_qualified": False,
            "release_authorized": False,
        }
        if hasattr(self, "_seal"):
            _require(sha256(_canonical(receipt)).hexdigest() == self._seal)
        return receipt

    def __post_init__(self) -> None:
        object.__setattr__(self, "_seal", sha256(_canonical(self.summary())).hexdigest())

    @property
    def pdf_bytes(self) -> bytes:
        self.summary()
        return self._pdf

    @property
    def pdf_sha256(self) -> str:
        return self.summary()["pdf_sha256"]


def _decode(raw: bytes, output_fd: int, *, html_sha: str, resources_sha: str,
            resource_count: int, profile_sha: str) -> tuple[bytes, int]:
    _require(type(raw) is bytes and 1 <= len(raw) <= engine.RESULT_BYTES)
    record = raster._strict(raw)
    _require(type(record) is dict and set(record) == {
        "protocol_version", "profile_id", "profile_sha256", "status", "pdf_bytes",
        "pdf_sha256", "limits", "html_sha256", "page_count",
        "resources_sha256", "resource_count"})
    _require(type(record["protocol_version"]) is int and record["protocol_version"] == 3
             and record["profile_id"] == PROFILE_ID
             and record["profile_sha256"] == profile_sha
             and record["status"] == "PASS" and record["html_sha256"] == html_sha
             and record["resources_sha256"] == resources_sha
             and type(record["resource_count"]) is int
             and record["resource_count"] == resource_count
             and type(record["page_count"]) is int and 1 <= record["page_count"] <= 64)
    limits = dict(memory_bytes=engine.MEMORY_BYTES, cpu_seconds=engine.CPU_SECONDS,
                  file_bytes=engine.NATIVE_PDF_BYTES, open_files=engine.OPEN_FILES)
    _require(record["limits"] == limits
             and type(record["pdf_bytes"]) is int
             and 0 < record["pdf_bytes"] <= engine.NATIVE_PDF_BYTES)
    st = os.fstat(output_fd)
    _require(stat.S_ISREG(st.st_mode) and st.st_nlink == 0
             and st.st_size == record["pdf_bytes"])
    pdf = os.pread(output_fd, st.st_size + 1, 0)
    _sha(record["pdf_sha256"])
    _require(len(pdf) == st.st_size and sha256(pdf).hexdigest() == record["pdf_sha256"])
    return pdf, record["page_count"]


def render_original_brand_paginated_candidate(
    *, bound_request: BoundReportRequest, assets: BoundBrandAssets,
    expected_brand_bundle_sha256: str,
) -> CompositeCandidate:
    """One forward source-to-PDF render with exact original assets and page artifacts."""
    try:
        profile = _admit()
        prepared = prepare_original_brand_pdf_candidate(
            bound_request=bound_request, assets=assets,
            expected_brand_bundle_sha256=expected_brand_bundle_sha256)
        record = prepared.to_dict()
        ir_sha, text_sha = _expected_content(prepared.dna.presentation_ir)
        source_ir_bytes = json.dumps(
            prepared.dna.presentation_ir, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False).encode("utf-8")
        _require(ir_sha == record["ir_sha256"]
                 and text_sha == record["expected_text_sha256"])
        html = pagination.inject_pagination_css(prepared.html)
        pagination.require_folio_html(html)
        encoded = html.encode("utf-8")
        _require(0 < len(encoded) <= engine.HTML_BYTES)
        html_sha = sha256(encoded).hexdigest()
        resources = prepared.dna.resources
        _require(type(resources) is RasterBundle)
        resource_bytes = resources.to_bytes()
        resources_sha = sha256(resource_bytes).hexdigest()
        _require(resources_sha == record["resources_sha256"]
                 and assets.to_dict()["bundle_sha256"] == expected_brand_bundle_sha256
                 and len(resources.assets) == 1)

        with (_PDF_RENDER_ENV_LOCK, tempfile.TemporaryFile() as request,
              tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as response,
              tempfile.TemporaryFile() as resource_file):
            fds = tuple(f.fileno() for f in (request, output, response, resource_file))
            _require(all(stat.S_ISREG(os.fstat(fd).st_mode)
                         and os.fstat(fd).st_nlink == 0 for fd in fds))
            request.write(f"3 {html_sha} {resources_sha}\n".encode("ascii") + encoded)
            request.flush()
            resource_file.write(resource_bytes)
            resource_file.flush()
            env = {key: os.environ[key] for key in (
                "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
                "FONTCONFIG_FILE", "FONTCONFIG_PATH") if key in os.environ}
            env.update(SOURCE_DATE_EPOCH="0", **{_QUALIFICATION: "qualification-only"})
            command = [sys.executable, "-I", "-B", str(Path(__file__).with_name(
                "_pdf_original_brand_pagination_render_worker.py")),
                *(str(fd) for fd in fds[:3]), str(os.getpid()), MODE, str(fds[3])]
            process = _start_pdf_render_worker(command, env=env, pass_fds=fds)
            code = _wait_pdf_render_worker(process, wall_seconds=engine.WALL_SECONDS)
            _require(code == 0 and 0 < os.fstat(response.fileno()).st_size <= engine.RESULT_BYTES)
            response.seek(0)
            pdf, pages = _decode(response.read(engine.RESULT_BYTES + 1), output.fileno(),
                                 html_sha=html_sha, resources_sha=resources_sha,
                                 resource_count=1, profile_sha=profile)
        _require(_admit() == profile and prepared.to_dict() == record)
        return CompositeCandidate(pdf, source_ir_bytes, html_sha, resources_sha,
                                  expected_brand_bundle_sha256, ir_sha, text_sha,
                                  pages, profile)
    except Exception:
        raise CompositeCandidateError(_ERROR) from None


def observe_original_brand_paginated_candidate(candidate: CompositeCandidate) -> dict:
    """New confined structural observer: never infer human PDF/UA acceptance."""
    try:
        profile = _admit()
        _require(type(candidate) is CompositeCandidate)
        summary = candidate.summary()
        with tempfile.TemporaryFile() as request, tempfile.TemporaryFile() as response:
            fds = (request.fileno(), response.fileno())
            _require(all(stat.S_ISREG(os.fstat(fd).st_mode)
                         and os.fstat(fd).st_nlink == 0 for fd in fds))
            request.write(b"1 " + candidate.pdf_sha256.encode("ascii") + b"\n"
                          + candidate.pdf_bytes)
            request.flush()
            command = [sys.executable, "-I", "-B", str(Path(__file__).with_name(
                "_pdf_original_brand_pagination_observer_worker.py")),
                str(fds[0]), str(fds[1]), str(os.getpid())]
            process = subprocess.Popen(
                command, cwd=Path(__file__).resolve().parents[1],
                env={"PATH": os.defpath, "LANG": "C.UTF-8",
                     _QUALIFICATION: "qualification-only"},
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True, pass_fds=fds,
                start_new_session=True)
            code = wait_owned_process_group(process, wall_seconds=10, cleanup_seconds=2)
            _require(code == 0 and 0 < os.fstat(response.fileno()).st_size <= typed.RESULT_BYTES)
            response.seek(0)
            result = typed._loads(response.read(typed.RESULT_BYTES + 1).decode("ascii"))
        _require(type(result) is dict and set(result) == {
            "status", "profile_id", "pdf_sha256", "page_count",
            "figure_layout_count", "literal_artifact_numbers", "annotation_count",
            "extracted_text_sha256", "text_characters", "decorative_artifact_count",
            "release_authorized", "source_asset_bytes_authenticated_from_pdf",
            "human_assistive_accessibility_verified"})
        _require(result["status"] == "OBSERVED_BRAND_PAGINATION_COMPOSITE"
                 and result["profile_id"] == OBSERVER_ID
                 and result["pdf_sha256"] == summary["pdf_sha256"]
                 and type(result["page_count"]) is int
                 and result["page_count"] == summary["page_count"]
                 and type(result["figure_layout_count"]) is int
                 and result["figure_layout_count"] == 1
                 and type(result["annotation_count"]) is int
                 and result["annotation_count"] == 0
                 and result["extracted_text_sha256"] == candidate.expected_text_sha256
                 and type(result["text_characters"]) is int
                 and 0 < result["text_characters"] <= 1048576
                 and type(result["decorative_artifact_count"]) is int
                 and result["decorative_artifact_count"] == 1
                 and result["literal_artifact_numbers"] == [
                     str(i) for i in range(1, result["page_count"] + 1)]
                 and all(result[name] is False for name in (
                     "release_authorized", "source_asset_bytes_authenticated_from_pdf",
                     "human_assistive_accessibility_verified")))
        _require(_admit() == profile)
        return {**result, "source_text_equivalence_verified": True,
                "all_page_visual_raster_verified": False}
    except Exception:
        raise CompositeCandidateError(_ERROR) from None


def inspect_original_brand_paginated_raster_geometry(candidate: CompositeCandidate) -> dict:
    """Inspect every physical page in existing guarded four-page raster windows.

    This is bounded geometric evidence only. A complete raster window ledger
    does not establish semantic visual equivalence, PDF/UA, consumer assistive
    behavior, or an aggregate resource attestation across child processes.
    """
    try:
        from reporting.page_windows import (
            bind_pdf_page_window_evidence, plan_pdf_page_windows,
        )
        from reporting.pdf_raster_windows import (
            PROFILE_ID as RASTER_WINDOW_PROFILE, inspect_pdf_raster_window,
        )

        profile = _admit()
        _require(type(candidate) is CompositeCandidate)
        summary = candidate.summary()
        pdf = candidate.pdf_bytes
        plan = plan_pdf_page_windows(
            pdf_sha256=summary["pdf_sha256"], ir_sha256=summary["ir_sha256"],
            page_count=summary["page_count"], admitted_page_budget=64,
            pages_per_window=4, inspector_profile_id=RASTER_WINDOW_PROFILE,
        )
        receipts = tuple(inspect_pdf_raster_window(
            pdf_bytes=pdf, plan=plan, window_index=index,
            expected_plan_sha256=plan.plan_sha256,
        ) for index in range(plan.window_count))
        ledger = bind_pdf_page_window_evidence(
            plan=plan, observations=tuple(r.to_observation() for r in receipts),
        )
        _require(sum(r.inspected_pages for r in receipts) == summary["page_count"]
                 and all(r.pdf_sha256 == summary["pdf_sha256"]
                         and r.ir_sha256 == summary["ir_sha256"]
                         and r.plan_sha256 == plan.plan_sha256 for r in receipts)
                 and ledger.plan.plan_sha256 == plan.plan_sha256
                 and _admit() == profile)
        return {
            "status": "ALL_PAGE_RASTER_GEOMETRY_OBSERVED_ONLY",
            "pdf_sha256": summary["pdf_sha256"],
            "source_ir_sha256": summary["ir_sha256"],
            "plan_sha256": plan.plan_sha256,
            "page_count": summary["page_count"],
            "window_count": plan.window_count,
            "inspected_pages": sum(r.inspected_pages for r in receipts),
            "windows": [dict(first_page=r.first_page, last_page=r.last_page,
                             raster_sha256=r.raster_sha256,
                             min_edge_margin_px=r.min_edge_margin_px)
                        for r in receipts],
            "all_page_raster_geometry_covered": True,
            "semantic_visual_equivalence_verified": False,
            "human_assistive_accessibility_verified": False,
            "aggregate_job_resources_qualified": False,
            "release_authorized": False,
        }
    except Exception:
        raise CompositeCandidateError(_ERROR) from None
