"""Opt-in four-descriptor launcher for typed original-brand structural emission."""
from dataclasses import dataclass, field
from hashlib import sha256
import dis
import os
from pathlib import Path
import stat
from subprocess import Popen as _Process
import sys
import tempfile
from time import monotonic_ns
from types import FunctionType

from reporting import _pdf_render_worker as worker
from reporting import _pdf_typed_brand_emission as emission
from reporting import pdf_raster_render as inherited
from reporting._pdf_native_launch_budget import require_no_active_scope, reserve_standalone_typed_render
from reporting._pdf_process_lifecycle import _observe as _observe_owned
from reporting.pdf_raster_resources import RasterBundle, _canonical, _digest

_MAX_PROCESS_OBSERVATION_BYTES = 2048
_PROCESS_FIELDS = frozenset({
    "kind", "worker", "worker_source_sha256", "parent_pid", "pid",
    "spawn_monotonic_ns", "exit_monotonic_ns", "exit_code", "request_header_sha256",
})

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
    call, following = instructions[matches[0] + 6:matches[0] + 8]
    call_offsets = frozenset(item.offset for item in dis.get_instructions(
        code, adaptive=False, show_caches=True) if call.offset <= item.offset < following.offset)
    return frozenset(item.offset for item in instructions[:matches[0] + 6]), call_offsets


def _adapter_prefix(waiter):
    """Recognize only the actual adapter's finite prefix before lifecycle entry."""
    from reporting import adapters, _pdf_process_lifecycle as lifecycle
    code = waiter.__code__
    if (waiter.__globals__ is not vars(adapters) or code.co_name != "_wait_pdf_render_worker"
            or code.co_argcount != 1 or code.co_kwonlyargcount != 2 or code.co_posonlyargcount != 0
            or code.co_varnames != ("process", "wall_seconds", "cleanup_observer", "options", "exc")
            or code.co_freevars or code.co_cellvars or len(code.co_code) > 4096
            or adapters.wait_owned_process_group is not lifecycle.wait_owned_process_group
            or type(lifecycle.wait_owned_process_group) is not FunctionType):
        return frozenset(), None
    instructions = tuple(dis.get_instructions(code, adaptive=False, show_caches=False))
    if len(instructions) < 21:
        return frozenset(), None
    # Both forward option-map branches are call-free and rejoin before delegation.
    expected = (("RESUME", 0), ("LOAD_FAST", "cleanup_observer"),
                ("POP_JUMP_IF_NOT_NONE", instructions[5].offset), ("BUILD_MAP", 0),
                ("JUMP_FORWARD", instructions[8].offset), ("LOAD_CONST", "cleanup_observer"),
                ("LOAD_FAST", "cleanup_observer"), ("BUILD_MAP", 1), ("STORE_FAST", "options"),
                ("NOP", None), ("LOAD_GLOBAL", "wait_owned_process_group"),
                ("LOAD_FAST", "process"), ("BUILD_TUPLE", 1), ("LOAD_FAST", "wall_seconds"),
                ("LOAD_CONST", 5), ("LOAD_CONST", ("wall_seconds", "cleanup_seconds")),
                ("BUILD_CONST_KEY_MAP", 2), ("LOAD_FAST", "options"), ("DICT_MERGE", 1),
                ("CALL_FUNCTION_EX", 1), ("RETURN_VALUE", None))
    if tuple((item.opname, item.argval) for item in instructions[:21]) != expected:
        return frozenset(), None
    return frozenset(item.offset for item in instructions[:19]), lifecycle.wait_owned_process_group.__code__


def _confirmed_pre_wait(error, controller_code, waiter_code, offsets,
                        call_offsets=(), adapter_offsets=(), lifecycle_code=None):
    """Use saved exception offsets and a direct, certified adapter-prefix chain."""
    tb = error.__traceback__
    position = previous = None
    adapter_seen = False
    for _ in range(32):
        if tb is None:
            return adapter_seen or position in offsets
        code = tb.tb_frame.f_code
        if code is waiter_code:
            if (adapter_seen or previous is not controller_code or position not in call_offsets
                    or tb.tb_lasti not in adapter_offsets):
                return False
            adapter_seen = True
        elif code is controller_code:
            if position is not None:
                return False
            position = tb.tb_lasti
        elif lifecycle_code is not None and code is lifecycle_code:
            return False
        previous = code
        tb = tb.tb_next
    return False


def _observe_typed_render_spawn(process, command, fds, launch_lower_bound):
    """Attach a local prelaunch bound only after proving the fixed child's ownership."""
    require = emission.require
    require(type(process) is _Process)
    if process.returncode is not None:
        raise ChildProcessError("typed render child ownership lost")
    parent = os.getpid()
    path = Path(__file__).with_name("_pdf_typed_brand_render_worker.py")
    require(type(parent) is int and 2 <= parent <= 2147483647
            and type(process.pid) is int and 2 <= process.pid <= 2147483647
            and process.pid != parent and type(command) is list
            and type(fds) is tuple and len(fds) == 4
            and all(type(fd) is int and fd >= 0 for fd in fds)
            and command == [sys.executable, "-I", "-B", str(path),
                            *(str(fd) for fd in fds[:3]), str(parent),
                            emission.MODE, str(fds[3])]
            and process.args == command and not path.is_symlink()
            and type(launch_lower_bound) is int
            and 0 <= launch_lower_bound < (1 << 63))
    _observe_owned(process)
    require(os.getpgid(process.pid) == process.pid)
    header = os.pread(fds[0], 132, 0)
    require(len(header) == 132)
    return {
        "kind": "typed_brand_render", "worker": path.name,
        "worker_source_sha256": emission._bounded_file(path, 65536)[1],
        "parent_pid": parent, "pid": process.pid,
        "spawn_monotonic_ns": launch_lower_bound,
        "request_header_sha256": sha256(header).hexdigest(),
    }


def _finish_typed_render_observation(value, candidate):
    """Seal a bounded unsigned row against the decoded unchanged candidate."""
    require = emission.require
    require(type(value) is dict and set(value) == _PROCESS_FIELDS
            and type(candidate) is TypedBrandPdfCandidate)
    metadata = candidate.to_dict()
    path = Path(__file__).with_name("_pdf_typed_brand_render_worker.py")
    header = f"3 {metadata['html_sha256']} {metadata['resources_sha256']}\n".encode("ascii")
    for name in ("worker_source_sha256", "request_header_sha256"):
        _digest(value[name])
    require(value["kind"] == "typed_brand_render" and value["worker"] == path.name
            and not path.is_symlink()
            and value["worker_source_sha256"] == emission._bounded_file(path, 65536)[1]
            and value["request_header_sha256"] == sha256(header).hexdigest()
            and type(value["parent_pid"]) is int and value["parent_pid"] == os.getpid()
            and 2 <= value["parent_pid"] <= 2147483647
            and type(value["pid"]) is int and 2 <= value["pid"] <= 2147483647
            and value["pid"] != value["parent_pid"]
            and type(value["exit_code"]) is int and value["exit_code"] == 0
            and type(value["spawn_monotonic_ns"]) is int
            and type(value["exit_monotonic_ns"]) is int
            and 0 <= value["spawn_monotonic_ns"] <= value["exit_monotonic_ns"] < (1 << 63))
    result = {
        **value, "html_sha256": metadata["html_sha256"],
        "resources_sha256": metadata["resources_sha256"],
        "pdf_sha256": metadata["pdf_sha256"], "pdf_byte_count": metadata["pdf_bytes"],
        "page_count": metadata["page_count"], "resource_count": metadata["resource_count"],
        "render_sha256": sha256(_canonical(metadata)).hexdigest(),
        "profile_sha256": metadata["profile_sha256"],
    }
    result["observation_sha256"] = sha256(_canonical(result)).hexdigest()
    raw = _canonical(result)
    require(0 < len(raw) <= _MAX_PROCESS_OBSERVATION_BYTES)
    return inherited._strict(raw.decode("ascii"))


def _run_owned_child(command, env, fds, *, observe_processes=False):
    from reporting import adapters
    emission.require(type(observe_processes) is bool)
    waiter = adapters._wait_pdf_render_worker
    wall = worker.WALL_SECONDS
    emission.require(type(waiter) is FunctionType and type(wall) is int and wall == 30)
    waiter_code = waiter.__code__
    controller_code = _run_owned_child.__code__
    offsets, call_offsets = _pre_wait_offsets(controller_code)
    adapter_offsets, lifecycle_code = _adapter_prefix(waiter)
    with reserve_standalone_typed_render() as ticket:
        process = None
        observer = ticket.settle
        observation_ownership_lost = False
        try:
            launch_lower_bound = monotonic_ns() if observe_processes else None
            if observe_processes:
                emission.require(type(launch_lower_bound) is int
                                 and 0 <= launch_lower_bound < (1 << 63))
                observed_command = tuple(command)
            process = adapters._start_pdf_render_worker(
                command, env=env, pass_fds=fds, before_spawn=ticket.arm)
            if process is None:
                raise ValueError("typed render child unavailable")
            observation = None
            if observe_processes:
                try:
                    observation = _observe_typed_render_spawn(
                        process, command, fds, launch_lower_bound)
                except ChildProcessError:
                    # Only this optional ownership check forbids another PID lookup.
                    observation_ownership_lost = True
                    raise
            status = waiter(process, wall_seconds=wall, cleanup_observer=observer)
            emission.require(ticket.phase == "settled" and type(status) is int)
            if observe_processes:
                current = monotonic_ns()
                emission.require(type(process.returncode) is int
                    and process.returncode == status and process.pid == observation["pid"]
                    and command == list(observed_command) and process.args == command
                    and os.getpid() == observation["parent_pid"]
                    and type(current) is int
                    and observation["spawn_monotonic_ns"] <= current < (1 << 63))
                observation.update(exit_monotonic_ns=current, exit_code=status)
                return status, observation
            return status
        except BaseException as error:
            try:
                if process is not None and not observation_ownership_lost and _confirmed_pre_wait(
                        error, controller_code, waiter_code, offsets, call_offsets,
                        adapter_offsets, lifecycle_code):
                    waiter(process, wall_seconds=0, cleanup_observer=observer)
            except BaseException:
                # Keep the original failure. Unproved custody stays quarantined.
                pass
            raise


def _render_typed_brand_pdf(*, html: str, expected_html_sha256: str, resources: RasterBundle,
                            expected_resources_sha256: str, observe_processes: bool):
    """Share the original renderer and custody boundary with optional observations."""
    from reporting import adapters
    require = emission.require
    try:
        require_no_active_scope()
        require(type(observe_processes) is bool)
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
            if observe_processes:
                code, observation = _run_owned_child(command, env, fds, observe_processes=True)
            else:
                code, observation = _run_owned_child(command, env, fds), None
            require(code == 0 and os.fstat(fds[2]).st_size <= worker.RESULT_BYTES)
            response.seek(0)
            result = _decode(response.read(worker.RESULT_BYTES + 1), fds[1], expected_html_sha256,
                             expected_resources_sha256, count, profile)
            if observe_processes:
                observation = _finish_typed_render_observation(observation, result)
            require(resources.to_bytes() == payload and len(resources.assets) == count
                    and require_policy() == profile)
            require_no_active_scope()
            return result, observation
    except Exception:
        raise ValueError("typed_brand_render_refused") from None


def render_typed_brand_pdf(*, html: str, expected_html_sha256: str, resources: RasterBundle,
                           expected_resources_sha256: str) -> TypedBrandPdfCandidate:
    """Keep the original candidate-only contract without process observation."""
    candidate, _ = _render_typed_brand_pdf(
        html=html, expected_html_sha256=expected_html_sha256, resources=resources,
        expected_resources_sha256=expected_resources_sha256, observe_processes=False)
    return candidate


def render_observed_typed_brand_pdf(
    *, html: str, expected_html_sha256: str, resources: RasterBundle,
    expected_resources_sha256: str,
) -> tuple[TypedBrandPdfCandidate, dict]:
    """Return the unchanged candidate and one detached unsigned direct-child row.

    The local launch lower bound and post-reap clock are not kernel event times.
    Source checksums and parent observations do not authenticate execution or
    qualify aggregate resources, accessibility, supported defaults or release.
    """
    return _render_typed_brand_pdf(
        html=html, expected_html_sha256=expected_html_sha256, resources=resources,
        expected_resources_sha256=expected_resources_sha256, observe_processes=True)
