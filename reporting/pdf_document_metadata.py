"""Opt-in exact-document metadata inspection without new release authority.

The unchanged controlled metadata and catalog accessibility-prerequisite policies
run in a disposable guarded process. Receipts expose fixed policy identities and
counts, never raw metadata or XML. They are unsigned local observations, not job
authentication, full PDF sanitization, tag semantics or whole-report qualification.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from subprocess import Popen as _Process
import sys
import tempfile
from time import monotonic_ns

from reporting._pdf_document_byte_budget import require_pdf_document_budget
from reporting._pdf_native_launch_budget import require_no_active_scope
from reporting._pdf_process_lifecycle import (
    _observe as _observe_owned, wait_owned_process_group as _cleanup_owned,
)
from reporting._pdf_document_metadata_worker import (
    CPU_SECONDS, IDENTITY_FIELDS, LANGUAGE, MAX_DOCUMENT_PAGES, MAX_PDF_BYTES,
    MEMORY_BYTES, METADATA_PROFILE_ID, OPEN_FILES, PARSER_VERSION, PROFILE_ID,
    PROTOCOL_VERSION, RESULT_BYTES, STATUS, _is_digest, _validate_identity,
)
from reporting.page_windows import PageWindowPlan
from reporting.pdf_object_graph import _canonical, _start, _unique_pairs, _wait

_LIMITS = {"memory_bytes": MEMORY_BYTES, "cpu_seconds": CPU_SECONDS,
           "file_bytes": RESULT_BYTES, "open_files": OPEN_FILES}
_INSPECTION = {"status": STATUS, "metadata_profile_id": METADATA_PROFILE_ID,
               "language": LANGUAGE, "marked": True, "struct_tree_root": True}


class PdfDocumentMetadataError(ValueError):
    """Content-free failure; no rejected candidate receives a successful receipt."""


@dataclass(frozen=True, slots=True, repr=False)
class PdfDocumentMetadataInspection:
    """Unsigned document identity with controlled metadata and catalog prerequisites only."""

    pdf_sha256: str
    ir_sha256: str
    plan_sha256: str
    page_count: int

    def __post_init__(self) -> None:
        """Require exact digests and native bounded counts even for direct construction."""
        try:
            _validate_identity(self._identity())
        except Exception:
            raise PdfDocumentMetadataError("pdf_document_metadata_receipt_invalid") from None

    def _identity(self) -> dict:
        """Detach only the closed document identity shared with the worker."""
        return {key: getattr(self, key) for key in IDENTITY_FIELDS}

    def to_dict(self) -> dict:
        """Expose constants and counts while withholding all unestablished global guarantees."""
        self.__post_init__()
        return {"schema_version": "1.0.0", "profile_id": PROFILE_ID,
                **self._identity(), **_INSPECTION, "parser_version": PARSER_VERSION,
                "inspection_scope": "CONTROLLED_INFO_DOCUMENT_XMP_AND_CATALOG_PREREQUISITES_ONLY",
                "worker_results_authenticated": False, "all_metadata_channels_inspected": False,
                "semantic_tag_tree_verified": False, "full_accessibility_qualified": False,
                "whole_pdf_safety_qualified": False, "text_equivalence_verified": False,
                "aggregate_job_resources_qualified": False, "release_authorized": False,
                "release_authorization": "NOT_ESTABLISHED"}

    @property
    def inspection_sha256(self) -> str:
        """Hash the revalidated unsigned receipt without authenticating its execution origin."""
        return hashlib.sha256(_canonical(self.to_dict())).hexdigest()


def _decode(data: bytes, expected: dict) -> PdfDocumentMetadataInspection:
    """Require exact protocol, native types, limits and complete prerequisite decisions."""
    try:
        if type(data) is not bytes or not 0 < len(data) <= RESULT_BYTES:
            raise ValueError("metadata response byte budget")
        _validate_identity(expected)
        record = json.loads(data.decode("ascii"), object_pairs_hook=_unique_pairs)
        if type(record) is not dict or set(record) != {
            "protocol_version", "profile_id", "parser_version", "identity", "limits", "inspection",
        }:
            raise ValueError("metadata response shape")
        if (type(record["protocol_version"]) is not int or record["protocol_version"] != PROTOCOL_VERSION
                or record["profile_id"] != PROFILE_ID or record["parser_version"] != PARSER_VERSION):
            raise ValueError("metadata response profile")
        _validate_identity(record["identity"])
        if record["identity"] != expected:
            raise ValueError("metadata response identity")
        limits = record["limits"]
        if (type(limits) is not dict or set(limits) != set(_LIMITS)
                or any(type(v) is not int or v != _LIMITS[k] for k, v in limits.items())):
            raise ValueError("metadata response limits")
        found = record["inspection"]
        if (type(found) is not dict or set(found) != set(_INSPECTION)
                or found != _INSPECTION or found["marked"] is not True
                or found["struct_tree_root"] is not True):
            raise ValueError("metadata response prerequisites")
        return PdfDocumentMetadataInspection(**expected)
    except Exception:
        raise PdfDocumentMetadataError("pdf_document_metadata_protocol_invalid") from None


_MAX_PROCESS_OBSERVATION_BYTES = 2048
_PROCESS_FIELDS = {
    "kind", "worker", "worker_source_sha256", "parent_pid", "pid",
    "spawn_monotonic_ns", "exit_monotonic_ns", "exit_code",
    "request_header_sha256", "identity", "inspection_sha256", "profile_id",
    "metadata_profile_id", "parser_version", "observation_sha256",
}


def _process_integer(value, lower=0, upper=(1 << 63) - 1):
    """Require bounded native counters and local clocks without coercion."""
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError("metadata process integer")
    return value


def _worker_source() -> tuple[Path, str]:
    """Identify the unchanged fixed worker without claiming authenticated execution."""
    path = Path(__file__).resolve().with_name("_pdf_document_metadata_worker.py")
    if path.is_symlink() or not path.is_file():
        raise ValueError("metadata worker source")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _request_header(identity: dict) -> bytes:
    """Preserve the exact existing anonymous request frame."""
    return (" ".join([str(PROTOCOL_VERSION), *(str(identity[k]) for k in IDENTITY_FIELDS)])
            + "\n").encode("ascii")


def _validate_process_observation(value: object, receipt: PdfDocumentMetadataInspection) -> dict:
    """Recheck a closed unsigned row against its unchanged exact-document receipt."""
    if type(value) is not dict or set(value) != _PROCESS_FIELDS:
        raise ValueError("metadata process shape")
    raw = _canonical(value)
    if not 0 < len(raw) <= _MAX_PROCESS_OBSERVATION_BYTES or type(receipt) is not PdfDocumentMetadataInspection:
        raise ValueError("metadata process budget or receipt")
    snapshot = json.loads(raw)
    identity = receipt._identity()
    receipt.__post_init__()
    worker, worker_sha = _worker_source()
    expected = {
        "kind": "document_metadata", "worker": worker.name,
        "worker_source_sha256": worker_sha, "parent_pid": os.getpid(), "exit_code": 0,
        "request_header_sha256": hashlib.sha256(_request_header(identity)).hexdigest(),
        "identity": identity, "inspection_sha256": receipt.inspection_sha256,
        "profile_id": PROFILE_ID, "metadata_profile_id": METADATA_PROFILE_ID,
        "parser_version": PARSER_VERSION,
    }
    if _canonical({key: snapshot[key] for key in expected}) != _canonical(expected):
        raise ValueError("metadata process identity")
    parent = _process_integer(snapshot["parent_pid"], 2, 2147483647)
    if (_process_integer(snapshot["pid"], 2, 2147483647) == parent
            or _process_integer(snapshot["spawn_monotonic_ns"])
            > _process_integer(snapshot["exit_monotonic_ns"])
            or not _is_digest(snapshot["observation_sha256"])
            or snapshot["observation_sha256"] != hashlib.sha256(_canonical({
                key: item for key, item in snapshot.items() if key != "observation_sha256"
            })).hexdigest()):
        raise ValueError("metadata process lifecycle or digest")
    return snapshot


def _observe_metadata_spawn(process, command: list[str], started: int) -> dict:
    """Observe the creator-owned unreaped fixed child before the original wait."""
    if not isinstance(process, _Process):
        raise ValueError("actual metadata process required")
    if process.returncode is not None:
        raise ChildProcessError("metadata child ownership lost")
    parent = _process_integer(os.getpid(), 2, 2147483647)
    pid = _process_integer(process.pid, 2, 2147483647)
    worker = Path(__file__).resolve().with_name("_pdf_document_metadata_worker.py")
    if (pid == parent or type(process.args) is not list or process.args != command
            or type(command) is not list or len(command) != 6
            or command[:3] != [sys.executable, "-I", str(worker)]
            or command[-1] != str(parent)):
        raise ValueError("metadata process launch identity")
    _observe_owned(process)
    if os.getpgid(pid) != pid:
        raise ValueError("metadata process group identity")
    _, worker_sha = _worker_source()
    return {"kind": "document_metadata", "worker": worker.name,
            "worker_source_sha256": worker_sha, "parent_pid": parent, "pid": pid,
            "spawn_monotonic_ns": _process_integer(started)}


def _run_worker(pdf: bytes, identity: dict, *, observe_processes=False):
    """Use the unchanged launch/wait boundary; optionally observe its actual child."""
    if type(observe_processes) is not bool:
        raise ValueError("metadata process observation flag")
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as result:
        fds = source.fileno(), result.fileno()
        infos = [os.fstat(fd) for fd in fds]
        if any(not stat.S_ISREG(i.st_mode) or i.st_nlink != 0 or i.st_uid != os.geteuid() for i in infos):
            raise ValueError("metadata anonymous protocol required")
        if (infos[0].st_dev, infos[0].st_ino) == (infos[1].st_dev, infos[1].st_ino):
            raise ValueError("metadata distinct protocol required")
        header = _request_header(identity)
        source.write(header); source.write(pdf); source.flush()
        command = [sys.executable, "-I",
            str(Path(__file__).resolve().with_name("_pdf_document_metadata_worker.py")),
            str(fds[0]), str(fds[1]), str(os.getpid())]
        started = monotonic_ns() if observe_processes else None
        process = _start(command, fds)
        if observe_processes:
            try:
                observation = _observe_metadata_spawn(process, command, started)
                if os.pread(fds[0], len(header), 0) != header:
                    raise ValueError("metadata process request changed")
                observation.update(request_header_sha256=hashlib.sha256(header).hexdigest(),
                                   identity=dict(identity))
            except ChildProcessError:
                # A lost waitable identity never grants a later numeric PID retry.
                raise
            except BaseException as failure:
                if isinstance(process, _Process):
                    try:
                        _cleanup_owned(process, wall_seconds=0)
                    except BaseException as cleanup:
                        raise failure from cleanup
                raise
        code = _wait(process)
        if observe_processes:
            ended = _process_integer(monotonic_ns())
            if (type(code) is not int or code != 0 or type(process.returncode) is not int
                    or process.returncode != code or process.pid != observation["pid"]
                    or process.args != command or os.getpid() != observation["parent_pid"]
                    or ended < observation["spawn_monotonic_ns"]):
                raise ValueError("metadata process final identity")
            observation.update(exit_code=code, exit_monotonic_ns=ended)
        if code != 0 or not 0 < os.fstat(result.fileno()).st_size <= RESULT_BYTES:
            raise ValueError("metadata worker failed")
        result.seek(0)
        raw = result.read(RESULT_BYTES + 1)
        return (raw, observation) if observe_processes else raw


def _inspect_pdf_document_metadata(
    *, pdf_bytes: object, plan: PageWindowPlan, expected_plan_sha256: str,
    expected_language: str, observe_processes=False,
):
    """Inspect the current controlled en-US document profile without expanding default routes.

    The plan binds the physical count and PDF/IR identities, not job authority.
    Catalog prerequisites do not prove all-page tag coverage or full PDF/UA.
    Missing metadata, wrong identity or unavailable execution returns no receipt.
    """
    if (sys.platform != "linux"
            or os.environ.get("OMNIGENIS_PDF_DOCUMENT_METADATA_DISABLED") == "1"):
        raise PdfDocumentMetadataError("pdf_document_metadata_execution_unavailable")
    if (type(pdf_bytes) is not bytes or not pdf_bytes.startswith(b"%PDF-1.7")
            or len(pdf_bytes) > MAX_PDF_BYTES):
        raise PdfDocumentMetadataError("pdf_document_metadata_input_invalid")
    if type(expected_language) is not str or expected_language != LANGUAGE:
        raise PdfDocumentMetadataError("pdf_document_metadata_language_unavailable")
    try:
        if type(plan) is not PageWindowPlan or not _is_digest(expected_plan_sha256):
            raise ValueError("metadata sealed plan required")
        plan.__post_init__()
        require_pdf_document_budget(len(pdf_bytes), plan.page_count)
        if (plan.plan_sha256 != expected_plan_sha256 or plan.page_count > MAX_DOCUMENT_PAGES
                or hashlib.sha256(pdf_bytes).hexdigest() != plan.pdf_sha256):
            raise ValueError("metadata plan mismatch")
        identity = {"pdf_sha256": plan.pdf_sha256, "ir_sha256": plan.ir_sha256,
                    "plan_sha256": plan.plan_sha256, "page_count": plan.page_count}
        _validate_identity(identity)
    except Exception:
        raise PdfDocumentMetadataError("pdf_document_metadata_plan_invalid") from None
    try:
        if observe_processes:
            raw, observation = _run_worker(pdf_bytes, identity, observe_processes=True)
            receipt = _decode(raw, identity)
        else:
            receipt = _decode(_run_worker(pdf_bytes, identity), identity)
        plan.__post_init__()
        if plan.plan_sha256 != expected_plan_sha256:
            raise ValueError("metadata plan changed during inspection")
        if observe_processes:
            observation.update(inspection_sha256=receipt.inspection_sha256,
                               profile_id=PROFILE_ID, metadata_profile_id=METADATA_PROFILE_ID,
                               parser_version=PARSER_VERSION)
            observation["observation_sha256"] = hashlib.sha256(_canonical(observation)).hexdigest()
            return receipt, _validate_process_observation(observation, receipt)
        return receipt
    except Exception:
        raise PdfDocumentMetadataError("pdf_document_metadata_execution_failed") from None


def inspect_pdf_document_metadata(
    *, pdf_bytes: object, plan: PageWindowPlan, expected_plan_sha256: str,
    expected_language: str,
) -> PdfDocumentMetadataInspection:
    """Preserve the existing receipt and ambient approved native-scope compatibility."""
    return _inspect_pdf_document_metadata(
        pdf_bytes=pdf_bytes, plan=plan, expected_plan_sha256=expected_plan_sha256,
        expected_language=expected_language)


def inspect_observed_pdf_document_metadata(
    *, pdf_bytes: object, plan: PageWindowPlan, expected_plan_sha256: str,
    expected_language: str,
) -> tuple[PdfDocumentMetadataInspection, dict]:
    """Return the same receipt and an optional standalone unsigned actual-child row.

    Local launch lower bounds and post-reap times are not kernel timestamps.
    This does not authenticate execution or establish aggregate resources,
    complete accessibility, arbitrary descendants, or release qualification.
    """
    try:
        require_no_active_scope()
        result = _inspect_pdf_document_metadata(
            pdf_bytes=pdf_bytes, plan=plan, expected_plan_sha256=expected_plan_sha256,
            expected_language=expected_language, observe_processes=True)
        require_no_active_scope()
        return result
    except Exception:
        raise PdfDocumentMetadataError("pdf_document_metadata_execution_failed") from None
