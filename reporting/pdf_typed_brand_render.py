"""Opt-in four-descriptor launcher for typed original-brand structural emission."""
from dataclasses import dataclass, field
from hashlib import sha256
import dis
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import FunctionType

from reporting import _pdf_render_worker as worker
from reporting import _pdf_typed_brand_emission as emission
from reporting import pdf_raster_render as inherited
from reporting._pdf_native_launch_budget import require_no_active_scope, reserve_standalone_typed_render
from reporting.pdf_raster_resources import RasterBundle, _canonical, _digest

DISABLE = ("OMNIGENIS_TYPED_BRAND_RENDER_DISABLED", "OMNIGENIS_TYPED_ORIGINAL_BRAND_PDF_DISABLED",
           "OMNIGENIS_TYPED_EDITORIAL_PDF_DISABLED", "OMNIGENIS_PDF_PRELOADED_RASTER_DISABLED",
           "OMNIGENIS_PDF_QUALIFICATION_RECEIPT_DISABLED", "OMNIGENIS_PDF_RENDERER_DISABLED")


def require_policy():
    inherited._require_policy()
    emission.require(not any(os.environ.get(key) == "1" for key in DISABLE))
    digest = emission.profile_sha256()
    emission.require(os.environ.get("OMNIGENIS_PDF_QUALIFICATION_RECEIPT_MODE") == "qualification-only"
                     and not any(os.environ.get(key) == "1" for key in DISABLE))
    return digest


@dataclass(frozen=True)
class TypedBrandPdfCandidate:
    _pdf: bytes = field(repr=False)
    html_sha256: str
    resources_sha256: str
    page_count: int
    resource_count: int
    render_profile_sha256: str
    _seal: str = field(init=False, repr=False, compare=False)

    def _metadata(self):
        emission.require(type(self) is TypedBrandPdfCandidate)
        for value in (self.html_sha256, self.resources_sha256, self.render_profile_sha256):
            _digest(value)
        emission.require(type(self._pdf) is bytes and self._pdf.startswith(b"%PDF-1.7")
                         and len(self._pdf) <= worker.NATIVE_PDF_BYTES
                         and type(self.page_count) is int and 1 <= self.page_count <= 64
                         and type(self.resource_count) is int and 1 <= self.resource_count <= 3
                         and self.render_profile_sha256 == emission.profile_sha256())
        return {"profile_id": emission.PROFILE_ID, "profile_sha256": self.render_profile_sha256,
                "html_sha256": self.html_sha256, "resources_sha256": self.resources_sha256,
                "page_count": self.page_count, "resource_count": self.resource_count,
                "pdf_bytes": len(self._pdf), "pdf_sha256": sha256(self._pdf).hexdigest(),
                "qualification_only": True, "release_authorized": False,
                "aggregate_job_resources_qualified": False, "accessibility_qualified": False,
                "visual_semantics_verified": False, "content_equivalence_verified": False,
                "default_pipeline_activated": False}

    def __post_init__(self):
        seal = sha256(_canonical(self._metadata())).hexdigest()
        emission.require(not hasattr(self, "_seal") or self._seal == seal)
        object.__setattr__(self, "_seal", seal)

    def to_dict(self):
        record = self._metadata()
        emission.require(sha256(_canonical(record)).hexdigest() == self._seal)
        return record

    @property
    def pdf_bytes(self):
        self.to_dict()
        return self._pdf

    @property
    def pdf_sha256(self):
        return self.to_dict()["pdf_sha256"]


def _decode(raw, fd, html_sha, resources_sha, count, profile):
    require = emission.require
    require(type(raw) is bytes and 0 < len(raw) <= worker.RESULT_BYTES)
    data = inherited._strict(raw.decode("ascii"))
    require(type(data) is dict and set(data) == {
        "protocol_version", "profile_id", "profile_sha256", "status", "pdf_bytes", "pdf_sha256",
        "limits", "html_sha256", "page_count", "resources_sha256", "resource_count"})
    limits = {"memory_bytes": worker.MEMORY_BYTES, "cpu_seconds": worker.CPU_SECONDS,
              "file_bytes": worker.NATIVE_PDF_BYTES, "open_files": worker.OPEN_FILES}
    require(type(data["protocol_version"]) is int and data["protocol_version"] == 3
            and data["profile_id"] == emission.PROFILE_ID and data["profile_sha256"] == profile
            and data["status"] == "PASS" and data["html_sha256"] == html_sha
            and data["resources_sha256"] == resources_sha and type(data["resource_count"]) is int
            and data["resource_count"] == count and data["limits"] == limits
            and all(type(value) is int for value in data["limits"].values()))
    size = data["pdf_bytes"]
    require(type(size) is int and 0 < size <= worker.NATIVE_PDF_BYTES)
    info = os.fstat(fd)
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 0 and info.st_size == size)
    pdf = os.pread(fd, size + 1, 0)
    require(len(pdf) == size and sha256(pdf).hexdigest() == data["pdf_sha256"])
    return TypedBrandPdfCandidate(pdf, html_sha, resources_sha, data["page_count"], count, profile)


def _pre_wait_offsets(code):
    """Certify the single execution-wait expression in the pinned CPython layout."""
    emission.require(sys.implementation.name == "cpython" and sys.version_info[:2] == (3, 12)
                     and len(code.co_code) <= 4096)
    instructions = tuple(dis.get_instructions(code, adaptive=False, show_caches=False))
    pattern = (("PUSH_NULL", None), ("LOAD_FAST", "waiter"), ("LOAD_FAST", "process"),
               ("LOAD_FAST", "wall"), ("LOAD_FAST", "observer"),
               ("KW_NAMES", ("wall_seconds", "cleanup_observer")), ("CALL", 3),
               ("STORE_FAST", "status"))
    operations = tuple((item.opname, item.argval) for item in instructions)
    matches = [i for i in range(len(operations) - len(pattern) + 1)
               if operations[i:i + len(pattern)] == pattern]
    emission.require(len(matches) == 1)
    # CALL and its inline caches cannot establish that the waiter was never entered.
    return frozenset(item.offset for item in instructions[:matches[0] + 6])


def _confirmed_pre_wait(error, controller_code, waiter_code, offsets):
    """Use saved exception offsets, never the unwound frame's current instruction."""
    tb = error.__traceback__
    position = None
    for _ in range(32):
        if tb is None:
            return position in offsets
        code = tb.tb_frame.f_code
        if code is waiter_code:
            return False
        if code is controller_code:
            if position is not None:
                return False
            position = tb.tb_lasti
        tb = tb.tb_next
    return False


def _run_owned_child(command, env, fds):
    from reporting import adapters
    waiter = adapters._wait_pdf_render_worker
    wall = worker.WALL_SECONDS
    emission.require(type(waiter) is FunctionType and type(wall) is int and wall == 30)
    waiter_code = waiter.__code__
    controller_code = _run_owned_child.__code__
    offsets = _pre_wait_offsets(controller_code)
    with reserve_standalone_typed_render() as ticket:
        process = None
        observer = ticket.settle
        try:
            process = adapters._start_pdf_render_worker(
                command, env=env, pass_fds=fds, before_spawn=ticket.arm)
            if process is None:
                raise ValueError("typed render child unavailable")
            status = waiter(process, wall_seconds=wall, cleanup_observer=observer)
            emission.require(ticket.phase == "settled" and type(status) is int)
            return status
        except BaseException as error:
            try:
                if process is not None and _confirmed_pre_wait(error, controller_code, waiter_code, offsets):
                    waiter(process, wall_seconds=0, cleanup_observer=observer)
            except BaseException:
                # Keep the original failure. Unproved custody stays quarantined.
                pass
            raise


def render_typed_brand_pdf(*, html: str, expected_html_sha256: str, resources: RasterBundle,
                           expected_resources_sha256: str) -> TypedBrandPdfCandidate:
    from reporting import adapters
    require = emission.require
    try:
        require_no_active_scope()
        profile = require_policy()
        _digest(expected_html_sha256)
        _digest(expected_resources_sha256)
        require(type(html) is str and 0 < len(html) <= worker.HTML_BYTES and type(resources) is RasterBundle)
        encoded = html.encode("utf-8")
        require(len(encoded) <= worker.HTML_BYTES and sha256(encoded).hexdigest() == expected_html_sha256)
        payload = resources.to_bytes()
        require(sha256(payload).hexdigest() == expected_resources_sha256)
        count = len(resources.assets)
        with (adapters._PDF_RENDER_ENV_LOCK, tempfile.TemporaryFile() as request,
              tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as response,
              tempfile.TemporaryFile() as resource):
            fds = tuple(f.fileno() for f in (request, output, response, resource))
            require(len(set(fds)) == 4 and all(stat.S_ISREG(os.fstat(fd).st_mode)
                    and os.fstat(fd).st_nlink == 0 for fd in fds))
            request.write(f"3 {expected_html_sha256} {expected_resources_sha256}\n".encode("ascii"))
            request.write(encoded)
            request.flush()
            request.seek(0)
            resource.write(payload)
            resource.flush()
            resource.seek(0)
            env = {key: os.environ[key] for key in (
                "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "FONTCONFIG_FILE",
                "FONTCONFIG_PATH", *DISABLE) if key in os.environ}
            env.update(SOURCE_DATE_EPOCH="0", OMNIGENIS_PDF_QUALIFICATION_RECEIPT_MODE="qualification-only")
            command = [sys.executable, "-I", "-B", str(Path(__file__).with_name("_pdf_typed_brand_render_worker.py")),
                       *(str(fd) for fd in fds[:3]), str(os.getpid()), emission.MODE, str(fds[3])]
            require_no_active_scope()
            require(require_policy() == profile)
            code = _run_owned_child(command, env, fds)
            require(code == 0 and os.fstat(fds[2]).st_size <= worker.RESULT_BYTES)
            response.seek(0)
            result = _decode(response.read(worker.RESULT_BYTES + 1), fds[1], expected_html_sha256,
                             expected_resources_sha256, count, profile)
            require(resources.to_bytes() == payload and len(resources.assets) == count
                    and require_policy() == profile)
            require_no_active_scope()
            return result
    except Exception:
        raise ValueError("typed_brand_render_refused") from None
