"""Isolated opt-in pagination artifact render and native verification.

No default adapter integration, resource escalation, PDF rewriting or release gate.
All content remains private to the caller; summaries contain only exact hashes,
bounded counts, and explicitly unqualified outcomes.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

from reporting import _pdf_pagination_core as core
from reporting import _pdf_pagination_observer_worker as observed
from reporting import pdf_typed_observation as typed
from reporting.adapters import _PDF_RENDER_ENV_LOCK, _start_pdf_render_worker, _wait_pdf_render_worker
from reporting._pdf_native_launch_budget import require_no_active_scope
from reporting._pdf_process_lifecycle import wait_owned_process_group
from reporting.pdf_generated_source_job import _render_profile as require_legacy_render_profile
from reporting.typed_pdf_candidate import _render_html

_RENDER_LIMITS = {
    "memory_bytes": 1073741824, "cpu_seconds": 20,
    "file_bytes": 16777216, "open_files": 128}
_ERROR = "pagination_artifact_unavailable"
_DISABLE = "OMNIGENIS_PDF_PAGINATION_ARTIFACT_DISABLED"


class PaginationArtifactError(ValueError):
    """A fixed refusal; partial candidates do not count as evidence."""


@dataclass(frozen=True, slots=True, repr=False)
class PaginationCandidate:
    pdf_bytes: bytes
    pdf_sha256: str
    html_sha256: str
    page_count: int
    profile_sha256: str

    def summary(self) -> dict:
        if (type(self.pdf_bytes) is not bytes
                or not self.pdf_bytes.startswith(b"%PDF-1.7")
                or not 0 < len(self.pdf_bytes) <= 16777216
                or sha256(self.pdf_bytes).hexdigest() != self.pdf_sha256
                or type(self.html_sha256) is not str or len(self.html_sha256) != 64
                or any(char not in "0123456789abcdef" for char in self.html_sha256)
                or type(self.page_count) is not int or not 1 <= self.page_count <= 64
                or self.profile_sha256 != core.renderer_profile_sha256()):
            raise PaginationArtifactError(_ERROR)
        return {
            "status": "OPT_IN_PAGINATION_CANDIDATE",
            "profile_id": core.PROFILE_ID, "profile_sha256": self.profile_sha256,
            "pdf_sha256": self.pdf_sha256, "html_sha256": self.html_sha256,
            "pdf_bytes": len(self.pdf_bytes), "page_count": self.page_count,
            "default_pipeline_activated": False, "release_authorized": False,
        }


def _admit() -> None:
    require_no_active_scope()
    if (sys.platform != "linux" or os.environ.get(_DISABLE) == "1"
            or os.environ.get("OMNIGENIS_PDF_RENDERER_DISABLED") == "1"
            or os.environ.get("OMNIGENIS_TYPED_PDF_OBSERVATION_DISABLED") == "1"):
        raise ValueError("pagination profile disabled")
    # Both unchanged pre-existing profiles must remain exact and usable.
    require_legacy_render_profile()
    typed._profile()
    core.require_source_manifest()


def _result(data: bytes, output_fd: int, html_digest: str) -> PaginationCandidate:
    record = typed._loads(data.decode("ascii"))
    allowed = {"protocol_version", "profile_id", "profile_sha256",
               "html_sha256", "pdf_bytes", "pdf_sha256", "page_count",
               "status", "limits"}
    if (type(record) is not dict or set(record) != allowed
            or type(record["protocol_version"]) is not int or record["protocol_version"] != 2
            or record["status"] != "PASS" or record["profile_id"] != core.PROFILE_ID
            or record["profile_sha256"] != core.renderer_profile_sha256()
            or record["html_sha256"] != html_digest
            or record["limits"] != _RENDER_LIMITS
            or type(record["pdf_bytes"]) is not int or not 1 <= record["pdf_bytes"] <= 16777216
            or type(record["page_count"]) is not int or not 1 <= record["page_count"] <= 64
            or type(record["pdf_sha256"]) is not str):
        raise ValueError("pagination render response invalid")
    info = os.fstat(output_fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 0
            or info.st_size != record["pdf_bytes"]):
        raise ValueError("pagination output descriptor invalid")
    pdf = os.pread(output_fd, record["pdf_bytes"] + 1, 0)
    if sha256(pdf).hexdigest() != record["pdf_sha256"] or len(pdf) != record["pdf_bytes"]:
        raise ValueError("pagination output hash mismatch")
    candidate = PaginationCandidate(pdf, record["pdf_sha256"], html_digest,
                                    record["page_count"], record["profile_sha256"])
    candidate.summary()
    return candidate


def render_paginated_candidate(typed_html: str) -> PaginationCandidate:
    """Opt-in typed HTML only. Existing _render_html() remains untouched."""
    try:
        _admit()
        if type(typed_html) is not str:
            raise ValueError("typed HTML invalid")
        html = core.inject_pagination_css(_render_html(typed_html))
        encoded = html.encode("utf-8")
        if not 0 < len(encoded) <= 67108864:
            raise ValueError("HTML budget")
        digest = sha256(encoded).hexdigest()
        with (_PDF_RENDER_ENV_LOCK, tempfile.TemporaryFile() as request,
              tempfile.TemporaryFile() as pdf_output, tempfile.TemporaryFile() as response):
            fds = tuple(item.fileno() for item in (request, pdf_output, response))
            if any(not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_nlink != 0
                   for fd in fds):
                raise ValueError("pagination descriptors")
            request.write(f"2 {digest}\n".encode("ascii") + encoded)
            request.flush()
            env = {key: os.environ[key] for key in (
                "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "FONTCONFIG_FILE",
                "FONTCONFIG_PATH") if key in os.environ}
            env["SOURCE_DATE_EPOCH"] = "0"
            command = [
                sys.executable, "-I", "-B",
                str(Path(__file__).with_name("_pdf_pagination_render_worker.py")),
                *(str(fd) for fd in fds), str(os.getpid()), core.MODE]
            process = _start_pdf_render_worker(command, env=env, pass_fds=fds)
            code = _wait_pdf_render_worker(process, wall_seconds=30)
            if code != 0 or not 0 < os.fstat(response.fileno()).st_size <= 65536:
                raise ValueError("pagination worker refused")
            response.seek(0)
            value = _result(response.read(65537), pdf_output.fileno(), digest)
        require_no_active_scope()
        return value
    except Exception:
        raise PaginationArtifactError(_ERROR) from None


def observe_paginated_candidate(candidate: PaginationCandidate) -> dict:
    """Use a separate strictly bounded child and existing typed native rules."""
    try:
        _admit()
        if type(candidate) is not PaginationCandidate:
            raise ValueError("candidate type")
        candidate.summary()
        with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as output:
            fds = (source.fileno(), output.fileno())
            if any(not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_nlink != 0
                   for fd in fds):
                raise ValueError("observer descriptors")
            source.write(b"1 " + candidate.pdf_sha256.encode("ascii")
                         + b"\n" + candidate.pdf_bytes)
            source.flush()
            command = [
                sys.executable, "-I", "-B",
                str(Path(__file__).with_name("_pdf_pagination_observer_worker.py")),
                str(fds[0]), str(fds[1]), str(os.getpid())]
            process = subprocess.Popen(
                command, cwd=Path(__file__).resolve().parents[1],
                env={"PATH": os.defpath, "LANG": "C.UTF-8"},
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True,
                pass_fds=fds, start_new_session=True)
            code = wait_owned_process_group(process, wall_seconds=10, cleanup_seconds=2)
            if code != 0 or not 0 < os.fstat(output.fileno()).st_size <= typed.RESULT_BYTES:
                raise ValueError("observer child refused")
            output.seek(0)
            result = typed._loads(output.read(typed.RESULT_BYTES + 1).decode("ascii"))
        required = {"protocol_version", "profile_id", "profile_sha256", "pdf_sha256",
                    "status", "page_count", "page_labels", "literal_artifact_numbers",
                    "total_physical_operators", "semantic_observation", "limits",
                    "release_authorized"}
        if (type(result) is not dict or set(result) != required
                or type(result["protocol_version"]) is not int or result["protocol_version"] != 2
                or result["profile_id"] != observed.PROFILE_ID
                or result["profile_sha256"] != observed.observer_profile_sha256()
                or result["pdf_sha256"] != candidate.pdf_sha256
                or result["status"] != "OBSERVED_NATIVE_PAGINATION_ARTIFACTS"
                or result["limits"] != typed.LIMITS
                or result["release_authorized"] is not False
                or result["page_count"] != candidate.page_count
                or result["page_labels"] != [str(i) for i in range(1, candidate.page_count + 1)]
                or result["literal_artifact_numbers"] != result["page_labels"]
                or type(result["total_physical_operators"]) is not int
                or not 0 < result["total_physical_operators"] <= typed.MAX_OPERATIONS):
            raise ValueError("pagination observation receipt invalid")
        typed._validate(
            result["semantic_observation"], candidate.pdf_sha256,
            len(candidate.pdf_bytes), typed._profile())
        require_no_active_scope()
        return {
            "status": result["status"], "pdf_sha256": candidate.pdf_sha256,
            "page_count": candidate.page_count,
            "literal_artifact_numbers": list(result["literal_artifact_numbers"]),
            "semantic_mcids": result["semantic_observation"]["counts"]["mcids"],
            "total_physical_operators": result["total_physical_operators"],
            "render_profile_sha256": candidate.profile_sha256,
            "observer_profile_sha256": result["profile_sha256"],
            "source_equivalence_verified": False,
            "reading_order_verified": False, "accessibility_qualified": False,
            "aggregate_job_resources_qualified": False,
            "default_pipeline_activated": False, "release_authorized": False,
        }
    except Exception:
        raise PaginationArtifactError(_ERROR) from None
