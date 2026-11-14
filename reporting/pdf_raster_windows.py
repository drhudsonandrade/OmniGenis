"""Opt-in exact-plan raster-window inspection with bounded native execution.

Receipts contain unsigned hashes and counts, never raster pixels or report text.
They can populate a separate raster checkpoint plan but cannot replace text,
object-safety, metadata, accessibility or release checks. The caller must supply
sealed expectations and supervise aggregate resources across the complete job.
The default renderer and existing whole-document validators remain unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from subprocess import Popen as _Process
import sys
import tempfile
from time import monotonic_ns

from reporting._pdf_document_byte_budget import require_pdf_document_budget
from reporting._pdf_process_lifecycle import _observe as _observe_owned
from reporting._pdf_raster_child_observation import MODE as OBSERVATION_MODE, validate_observation
from reporting._pdf_native_launch_budget import admit_launch, wait_native_process_group, require_no_active_scope

from reporting._pdf_bounded_scratch import (
    _open_verified_bounded_child, _require_same_bounded_mount,
    bounded_scratch_parent, bounded_scratch_requested, raster_child_environment,
)
from reporting._pdf_raster_window_worker import (
    CPU_SECONDS, FILE_BYTES, IDENTITY_FIELDS, MAX_DOCUMENT_PAGES, MAX_PDF_BYTES,
    MAX_PIXELS_PER_PAGE, MAX_TOTAL_PIXELS, MAX_WINDOW_PAGES, MEMORY_BYTES,
    MIN_EDGE_MARGIN_PX, OPEN_FILES, PARSER_VERSION, PILLOW_VERSION, PROFILE_ID,
    PROTOCOL_VERSION, RESULT_BYTES, TOOL_SHA256, TOOL_VERSION, WALL_SECONDS,
    _is_digest, _validate_identity,
)
from reporting.page_windows import PageWindowPlan, WindowObservation
from reporting.pdf_text_windows import _canonical, _unique_pairs

_LIMITS = {"memory_bytes": MEMORY_BYTES, "cpu_seconds": CPU_SECONDS,
           "file_bytes": FILE_BYTES, "open_files": OPEN_FILES}
_MAX_DIRECT_OBSERVATION_BYTES = 2048


class PdfRasterWindowError(ValueError):
    """Fixed content-free failure; no partial successful raster observation is returned."""


@dataclass(frozen=True, slots=True, repr=False)
class PdfRasterWindowInspection:
    """Immutable unsigned geometry evidence for exactly one physical page interval."""

    pdf_sha256: str
    ir_sha256: str
    plan_sha256: str
    page_count: int
    window_index: int
    first_page: int
    last_page: int
    raster_sha256: str
    total_pixels: int
    min_edge_margin_px: int

    def __post_init__(self) -> None:
        """Reject invalid digests, counts, incomplete ranges and contradictory geometry."""
        try:
            _validate_identity(self._identity())
            if not _is_digest(self.raster_sha256):
                raise ValueError("raster digest")
            margin = self.min_edge_margin_px
            if type(margin) is not int or not MIN_EDGE_MARGIN_PX <= margin <= 1000:
                raise ValueError("raster margin")
            if (type(self.total_pixels) is not int
                    or not self.inspected_pages * (2 * margin + 1) ** 2 <= self.total_pixels
                    <= min(MAX_TOTAL_PIXELS, self.inspected_pages * MAX_PIXELS_PER_PAGE)):
                raise ValueError("raster pixel relation")
        except Exception:
            raise PdfRasterWindowError("pdf_raster_window_receipt_invalid") from None

    def _identity(self) -> dict:
        """Detach the closed request identity used by both worker and supervisor."""
        return {key: getattr(self, key) for key in IDENTITY_FIELDS}

    @property
    def inspected_pages(self) -> int:
        """Return the exact inclusive range size represented by this receipt."""
        return self.last_page - self.first_page + 1

    def to_dict(self) -> dict:
        """Expose only digest/count geometry evidence and unestablished global guarantees."""
        self.__post_init__()
        return {"schema_version": "1.0.0", "profile_id": PROFILE_ID,
                "status": "WINDOW_RASTER_INSPECTED", **self._identity(),
                "raster_sha256": self.raster_sha256, "total_pixels": self.total_pixels,
                "min_edge_margin_px": self.min_edge_margin_px,
                "inspected_pages": self.inspected_pages, "parser_version": PARSER_VERSION,
                "pillow_version": PILLOW_VERSION, "raster_tool_version": TOOL_VERSION,
                "raster_tool_sha256": TOOL_SHA256,
                "raster_digest_profile": "physical-page-rgb-geometry-framed-v1",
                "worker_results_authenticated": False, "whole_pdf_safety_qualified": False,
                "semantic_visual_equivalence_verified": False,
                "aggregate_job_resources_qualified": False, "release_authorized": False,
                "release_authorization": "NOT_ESTABLISHED"}

    @property
    def observation_sha256(self) -> str:
        """Hash the complete detached metadata; this digest is not authentication."""
        return hashlib.sha256(_canonical(self.to_dict())).hexdigest()

    def to_observation(self) -> WindowObservation:
        """Project into an existing raster-plan ledger without publishing any job state."""
        self.__post_init__()
        return WindowObservation(index=self.window_index, first_page=self.first_page,
            last_page=self.last_page, pdf_sha256=self.pdf_sha256, plan_sha256=self.plan_sha256,
            inspector_profile_id=PROFILE_ID, observation_sha256=self.observation_sha256)


def _decode(data: bytes, expected: dict) -> PdfRasterWindowInspection:
    """Require exact protocol shape, identities, process limits and complete range evidence."""
    try:
        if type(data) is not bytes or not 0 < len(data) <= RESULT_BYTES:
            raise ValueError("raster response size")
        _validate_identity(expected)
        record = json.loads(data.decode("ascii"), object_pairs_hook=_unique_pairs)
        if type(record) is not dict or set(record) != {
            "protocol_version", "profile_id", "identity", "limits", "inspection",
        }:
            raise ValueError("raster response shape")
        if (type(record["protocol_version"]) is not int
                or record["protocol_version"] != PROTOCOL_VERSION or record["profile_id"] != PROFILE_ID):
            raise ValueError("raster response version")
        _validate_identity(record["identity"])
        if record["identity"] != expected:
            raise ValueError("raster response identity")
        limits = record["limits"]
        if (type(limits) is not dict or set(limits) != set(_LIMITS)
                or any(type(v) is not int or v != _LIMITS[k] for k, v in limits.items())):
            raise ValueError("raster response limits")
        found = record["inspection"]
        if type(found) is not dict or set(found) != {
            "status", "raster_sha256", "total_pixels", "min_edge_margin_px", "inspected_pages",
        }:
            raise ValueError("raster inspection shape")
        if (found["status"] != "WINDOW_RASTER_INSPECTED" or type(found["inspected_pages"]) is not int
                or found["inspected_pages"] != expected["last_page"] - expected["first_page"] + 1):
            raise ValueError("raster inspection coverage")
        return PdfRasterWindowInspection(**expected, **{key: found[key] for key in
            ("raster_sha256", "total_pixels", "min_edge_margin_px")})
    except Exception:
        raise PdfRasterWindowError("pdf_raster_window_protocol_invalid") from None


def _start(command: list[str], fds: tuple[int, int, int], scratch: str) -> subprocess.Popen:
    """Launch the isolated worker with only declared protocol and private scratch descriptors."""
    with admit_launch("raster_windows", command, fds, scratch) as observed:
        process = subprocess.Popen(command, cwd=Path(__file__).resolve().parents[1],
            env=raster_child_environment(scratch), shell=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, pass_fds=fds, start_new_session=True)
        observed(process)
        return process


def _wait(process: subprocess.Popen) -> int:
    """Keep child identity reserved until original-group cleanup and final reap."""
    return wait_native_process_group(process, wall_seconds=WALL_SECONDS)


def _observe_raster_worker_spawn(process: object, command: list[str], launch_lower_bound_ns: int) -> dict:
    """Bind the prelaunch local lower bound only after actual child ownership is proved."""
    parent = os.getpid()
    worker = Path(__file__).resolve().with_name('_pdf_raster_window_worker.py')
    if (not isinstance(process, _Process) or type(process.pid) is not int
            or not 2 <= process.pid <= 2147483647 or process.pid == parent
            or type(parent) is not int or not 2 <= parent <= 2147483647
            or process.returncode is not None or process.args != command
            or type(command) is not list or len(command) != 8
            or any(type(value) is not str for value in command)
            or command[:3] != [sys.executable, '-I', str(worker)]
            or command[-2:] != [str(parent), OBSERVATION_MODE]
            or worker.is_symlink()):
        raise ValueError('raster observed worker identity')
    _observe_owned(process)
    if os.getpgid(process.pid) != process.pid:
        raise ValueError('raster observed worker group')
    if (type(launch_lower_bound_ns) is not int
            or not 0 <= launch_lower_bound_ns < (1 << 63)):
        raise ValueError('raster observed worker clock')
    return {
        'kind': 'raster_windows', 'worker': worker.name,
        'worker_source_sha256': hashlib.sha256(worker.read_bytes()).hexdigest(),
        'parent_pid': parent, 'pid': process.pid,
        'spawn_monotonic_ns': launch_lower_bound_ns,
    }


def _finish_raster_worker_observation(value: dict, identity: dict,
                                      receipt: PdfRasterWindowInspection,
                                      nested: dict) -> dict:
    """Join the retained direct child to its decoded receipt and enclosed children."""
    fields = {
        'kind', 'worker', 'worker_source_sha256', 'parent_pid', 'pid',
        'spawn_monotonic_ns', 'exit_monotonic_ns', 'exit_code',
        'request_header_sha256', 'identity',
    }
    if (type(value) is not dict or set(value) != fields
            or len(_canonical(value)) > _MAX_DIRECT_OBSERVATION_BYTES
            or type(receipt) is not PdfRasterWindowInspection):
        raise ValueError('raster direct observation shape')
    _validate_identity(identity)
    _validate_identity(value['identity'])
    worker = Path(__file__).resolve().with_name('_pdf_raster_window_worker.py')
    header = ' '.join([str(PROTOCOL_VERSION), *(str(identity[k]) for k in IDENTITY_FIELDS)]) + '\n'
    if (value['kind'] != 'raster_windows' or value['worker'] != worker.name
            or worker.is_symlink()
            or value['worker_source_sha256'] != hashlib.sha256(worker.read_bytes()).hexdigest()
            or value['request_header_sha256'] != hashlib.sha256(header.encode('ascii')).hexdigest()
            or _canonical(value['identity']) != _canonical(identity)
            or receipt._identity() != identity):
        raise ValueError('raster direct observation binding')
    for key in ('parent_pid', 'pid'):
        if type(value[key]) is not int or not 2 <= value[key] <= 2147483647:
            raise ValueError('raster direct observation pid')
    if (value['parent_pid'] != os.getpid() or value['pid'] == value['parent_pid']
            or value['pid'] != nested['worker_pid']
            or type(value['exit_code']) is not int or value['exit_code'] != 0):
        raise ValueError('raster direct observation ownership or exit')
    begin = value['spawn_monotonic_ns']
    end = value['exit_monotonic_ns']
    if (type(begin) is not int or type(end) is not int
            or not 0 <= begin <= end < (1 << 63)):
        raise ValueError('raster direct observation interval')
    for row in nested['processes']:
        if (row['pid'] in (value['parent_pid'], value['pid'])
                or not begin <= row['observed_spawn_ns'] <= row['observed_exit_ns'] <= end):
            raise ValueError('raster nested observation outside direct worker')
    result = {
        **value, 'inspection_sha256': receipt.observation_sha256,
        'nested_observation_sha256': hashlib.sha256(_canonical(nested)).hexdigest(),
    }
    result['observation_sha256'] = hashlib.sha256(_canonical(result)).hexdigest()
    encoded = _canonical(result)
    if len(encoded) > _MAX_DIRECT_OBSERVATION_BYTES:
        raise ValueError('raster direct observation budget')
    return json.loads(encoded)


def _run_worker(pdf: bytes, identity: dict, *, observe_processes: bool = False,
                observe_worker: bool = False) -> bytes | tuple[bytes, int] | tuple[bytes, int, dict]:
    """Own one original transport, with optional direct observations outside a job scope."""
    if (type(observe_processes) is not bool or type(observe_worker) is not bool
            or (observe_worker and not observe_processes)):
        raise ValueError("raster observation flag")
    if observe_worker:
        require_no_active_scope()
    opted_in = bounded_scratch_requested()
    parent = bounded_scratch_parent()
    with tempfile.TemporaryDirectory(
        prefix="omnigenis-pdf-visual-supervisor-", dir=parent
    ) as scratch:
        scratch_fd = (
            _open_verified_bounded_child(scratch)
            if opted_in
            else os.open(
                scratch,
                os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            )
        )
        try:
            protocol_options = {"dir": parent} if opted_in else {}
            with tempfile.TemporaryFile(**protocol_options) as source, \
                    tempfile.TemporaryFile(**protocol_options) as result:
                fds = source.fileno(), result.fileno(), scratch_fd
                infos = [os.fstat(fd) for fd in fds[:2]]
                _require_same_bounded_mount(scratch_fd, *fds[:2])
                if any(not stat.S_ISREG(i.st_mode) or i.st_nlink != 0 for i in infos):
                    raise ValueError("anonymous raster protocol required")
                if (infos[0].st_dev, infos[0].st_ino) == (infos[1].st_dev, infos[1].st_ino):
                    raise ValueError("distinct raster protocol required")
                header = " ".join([str(PROTOCOL_VERSION), *(str(identity[k]) for k in IDENTITY_FIELDS)])
                source.write(header.encode("ascii") + b"\n"); source.write(pdf); source.flush()
                command = [sys.executable, "-I",
                    str(Path(__file__).resolve().with_name("_pdf_raster_window_worker.py")),
                    *(str(fd) for fd in fds), str(os.getpid())]
                if observe_processes:
                    command.append(OBSERVATION_MODE)
                launch_lower_bound = None
                if observe_worker:
                    launch_lower_bound = monotonic_ns()
                    if (type(launch_lower_bound) is not int
                            or not 0 <= launch_lower_bound < (1 << 63)):
                        raise ValueError('raster observed prelaunch clock')
                process = _start(command, fds, scratch)
                direct = None
                if observe_worker:
                    try:
                        direct = _observe_raster_worker_spawn(process, command, launch_lower_bound)
                        direct.update(
                            request_header_sha256=hashlib.sha256(header.encode('ascii') + b'\n').hexdigest(),
                            identity=dict(identity),
                        )
                    except ChildProcessError:
                        # Lost waitable identity cannot authorize another numeric-PID lookup.
                        raise
                    except BaseException as failure:
                        if isinstance(process, _Process):
                            try:
                                wait_native_process_group(process, wall_seconds=0)
                            except BaseException as cleanup:
                                raise failure from cleanup
                        raise
                code = _wait(process)
                if code != 0 or not 0 < os.fstat(result.fileno()).st_size <= RESULT_BYTES:
                    raise ValueError("raster worker failed")
                if observe_worker:
                    current = monotonic_ns()
                    if (type(code) is not int or type(process.returncode) is not int
                            or process.returncode != code or process.pid != direct['pid']
                            or direct['parent_pid'] != os.getpid()
                            or type(current) is not int
                            or not direct['spawn_monotonic_ns'] <= current < (1 << 63)):
                        raise ValueError('raster observed worker exit or clock')
                    direct.update(exit_monotonic_ns=current, exit_code=code)
                result.seek(0)
                data = result.read(RESULT_BYTES + 1)
                if observe_worker:
                    return data, process.pid, direct
                return (data, process.pid) if observe_processes else data
        finally:
            os.close(scratch_fd)


def _admit_raster_window(*, pdf_bytes: object, plan: PageWindowPlan, window_index: int,
                         expected_plan_sha256: str) -> dict:
    """Preserve the existing input, disable, source-digest and exact-plan admission."""
    if (sys.platform != "linux"
            or os.environ.get("OMNIGENIS_PDF_RASTER_WINDOWS_DISABLED") == "1"):
        raise PdfRasterWindowError("pdf_raster_window_execution_unavailable")
    if (type(pdf_bytes) is not bytes or not pdf_bytes.startswith(b"%PDF-1.7")
            or len(pdf_bytes) > MAX_PDF_BYTES):
        raise PdfRasterWindowError("pdf_raster_window_input_invalid")
    try:
        if type(plan) is not PageWindowPlan or not _is_digest(expected_plan_sha256):
            raise ValueError("typed raster plan required")
        plan.__post_init__()
        require_pdf_document_budget(len(pdf_bytes), plan.page_count)
        if (plan.plan_sha256 != expected_plan_sha256 or plan.inspector_profile_id != PROFILE_ID
                or plan.page_count > MAX_DOCUMENT_PAGES or plan.pages_per_window > MAX_WINDOW_PAGES
                or type(window_index) is not int or not 0 <= window_index < plan.window_count
                or hashlib.sha256(pdf_bytes).hexdigest() != plan.pdf_sha256):
            raise ValueError("raster plan mismatch")
        window = plan.windows[window_index]
        identity = {"pdf_sha256": plan.pdf_sha256, "ir_sha256": plan.ir_sha256,
            "plan_sha256": plan.plan_sha256, "page_count": plan.page_count,
            "window_index": window.index, "first_page": window.first_page, "last_page": window.last_page}
        _validate_identity(identity)
    except Exception:
        raise PdfRasterWindowError("pdf_raster_window_plan_invalid") from None
    return identity


def inspect_pdf_raster_window(
    *, pdf_bytes: object, plan: PageWindowPlan, window_index: int,
    expected_plan_sha256: str,
) -> PdfRasterWindowInspection:
    """Inspect one native raster interval without modifying default whole-file qualification.

    The optional 512-page class accepts at most four selected pages per call.
    It checks blank pages, simple geometry and edge-contact risk, not visual
    semantics, font/color correctness, accessibility, full coverage or release.
    """
    identity = _admit_raster_window(pdf_bytes=pdf_bytes, plan=plan, window_index=window_index,
                                    expected_plan_sha256=expected_plan_sha256)
    try:
        from reporting import _pdf_native_launch_budget as job_scope
        nested = job_scope.raster_child_observation_requested()
        if nested:
            data, worker_pid = _run_worker(pdf_bytes, identity, observe_processes=True)
            receipt, observation = _decode_observed(data, identity, worker_pid)
        else:
            receipt = _decode(_run_worker(pdf_bytes, identity), identity)
        plan.__post_init__()
        if plan.plan_sha256 != expected_plan_sha256:
            raise ValueError("raster plan changed")
        if nested:
            job_scope.record_raster_child_observation(worker_pid, receipt, observation)
        return receipt
    except Exception:
        raise PdfRasterWindowError("pdf_raster_window_execution_failed") from None


def _decode_observed(data: bytes, expected: dict, worker_pid: int) -> tuple[PdfRasterWindowInspection, dict]:
    """Require the original raster receipt plus exact bounded actual-child metadata."""
    try:
        if type(data) is not bytes or not 0 < len(data) <= RESULT_BYTES:
            raise ValueError("raster observed response budget")
        record = json.loads(data.decode("ascii"), object_pairs_hook=_unique_pairs)
        if type(record) is not dict or set(record) != {
            "protocol_version", "profile_id", "identity", "limits", "inspection", "process_observation",
        } or _canonical(record) != data:
            raise ValueError("raster observed response shape")
        base = {key:value for key,value in record.items() if key != "process_observation"}
        receipt = _decode(_canonical(base), expected)
        observed = validate_observation(record["process_observation"], expected, record["inspection"], worker_pid)
        return receipt, observed
    except Exception:
        raise PdfRasterWindowError("pdf_raster_window_observation_invalid") from None


def inspect_observed_pdf_raster_window(
    *, pdf_bytes: object, plan: PageWindowPlan, window_index: int,
    expected_plan_sha256: str,
) -> tuple[PdfRasterWindowInspection, dict]:
    """Return unchanged raster geometry and actual probe/page-child observations.

    This explicit standalone operation does not widen existing native job scopes.
    It observes the fixed one-probe/selected-page sequence, not arbitrary native
    descendants, authenticated exec, complete-job resources or artifact release.
    Existing disable rules, request identity, timers and resource limits apply.
    """
    identity = _admit_raster_window(pdf_bytes=pdf_bytes, plan=plan, window_index=window_index,
                                    expected_plan_sha256=expected_plan_sha256)
    try:
        require_no_active_scope()
        data, worker_pid = _run_worker(pdf_bytes, identity, observe_processes=True)
        receipt, observation = _decode_observed(data, identity, worker_pid)
        plan.__post_init__()
        if plan.plan_sha256 != expected_plan_sha256:
            raise ValueError("raster plan changed")
        return receipt, observation
    except Exception:
        raise PdfRasterWindowError("pdf_raster_window_observed_execution_failed") from None



def inspect_process_observed_pdf_raster_window(
    *, pdf_bytes: object, plan: PageWindowPlan, window_index: int,
    expected_plan_sha256: str,
) -> tuple[PdfRasterWindowInspection, dict, dict]:
    """Return the original receipt, nested records and their actual direct worker.

    The fixed standalone operation observes one owned worker and its existing
    declared probe/page children. The spawn timestamp is a local lower bound
    sampled immediately before launch and attached only after child ownership
    is proved; the exit timestamp follows the original cleanup and reap.
    These observations and unsigned source hashes do not authenticate execution
    or qualify aggregate resources.
    The original native job scope, worker mode and all resource limits remain.
    """
    identity = _admit_raster_window(
        pdf_bytes=pdf_bytes, plan=plan, window_index=window_index,
        expected_plan_sha256=expected_plan_sha256)
    try:
        require_no_active_scope()
        data, worker_pid, direct = _run_worker(
            pdf_bytes, identity, observe_processes=True, observe_worker=True)
        receipt, nested = _decode_observed(data, identity, worker_pid)
        if type(direct) is not dict or direct.get('pid') != worker_pid:
            raise ValueError('raster direct worker mismatch')
        direct = _finish_raster_worker_observation(direct, identity, receipt, nested)
        plan.__post_init__()
        if plan.plan_sha256 != expected_plan_sha256:
            raise ValueError('raster plan changed')
        require_no_active_scope()
        return receipt, nested, direct
    except Exception:
        raise PdfRasterWindowError('pdf_raster_window_process_observed_execution_failed') from None
