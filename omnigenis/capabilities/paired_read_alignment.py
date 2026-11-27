"""Explicit paired-read alignment with bounded native execution and byte lineage.

Only operator-supplied resources matching the selected local profiles are used.
JSON result exports contain identities and technical assessments, never read names,
native stderr or operational paths. No default processing route is registered.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import errno
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import re
import resource
import selectors
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
import zlib

from .artifact_gate import verify_artifact_bytes
from .fastq_intake import validate_paired_fastq

CAPABILITY_ID = "paired-read-alignment"
CAPABILITY_VERSION = "1.0.0"
PROFILE_ID = "paired-fastq-grch38-p14-bwa-mem2-v1"
EXECUTION_PROFILE_ID = "paired-fastq-alignment-v1"
REFERENCE_PROFILE_ID = "grch38-p14-ncbi-refseq-full-alignment-v1"
EXECUTOR_PROFILE_ID = "bwa-mem2-2.3-sse41-samtools-1.25-v1"
MAX_MATE_BYTES = 67108864
MAX_PAIRS = 100000
MAX_READ_BASES = 1000
MAX_SAM_BYTES = MAX_BAM_BYTES = 536870912
MAX_BAI_BYTES = 67108864
MAX_RESULT_BYTES = 65536
MAX_HEADER_BYTES = 1048576
MAX_RECORD_BYTES = 16777216
MAX_DECODED_BYTES = 2147483648
MAX_MUTABLE_BYTES = 2147483648
MIN_FREE_BYTES = 8589934592
MAX_PARENT_RSS = 536870912
ROOT = Path(__file__).resolve().parents[2]
BGZF_EOF = bytes.fromhex("1f8b08040000000000ff0600424302001b0003000000000000000000")
INDEX_SUFFIXES = (".0123", ".amb", ".ann", ".bwt.2bit.64", ".pac")
CANONICAL_PG = b"@PG\tID:bwa-mem2\tPN:bwa-mem2\tVN:2.2.1"
PROFILE_FILES = {
    "alignment": ("resources/alignment-profiles/paired-fastq-grch38-p14-bwa-mem2-v1.json",
                  "6ea4e1631a73b730306915d7db90b34460025fd4c4f3a82eba525c96c2fc6096"),
    "execution": ("resources/execution-profiles/paired-fastq-alignment-v1.json",
                  "ccc2ede6da765ac766fb197960ab48fed594476c94d1a67b3cf8a5bb81683c85"),
    "executor": ("resources/executor-profiles/bwa-mem2-2.3-sse41-samtools-1.25-v1.json",
                 "a763f3789f664a9b678adc14365b30fb2c7ba588a621c7d9cfe2002b4c9ebdbb"),
    "reference": ("resources/reference-profiles/grch38-p14-ncbi-refseq-full-alignment-v1.json",
                  "58dc322611fdb586952e697123aba8c6deffc59724244a8e54164fa7e6a14ce8"),
    "bom": ("resources/scientific-resource-bom/grch38-p14-ncbi-refseq-full-alignment-v1.json",
            "96dfd057e19dbeb4480b705667ee047cd6cfe7b1b349159d67929c46c25d9d0e"),
}
SCHEMA_PATH = "schemas/paired-read-alignment-result.v1.schema.json"
SCHEMA_SHA256 = "607fc32e4e93fab5a55ea8c3238eefcd3d541a9dab3feac5f263033eaa68b8a7"
ARTIFACT_SCHEMA_SHA256 = "cbb32abed32b25392cc349f72cc9983d1e240b855c134c7f0dbaf3597db39d6a"
_EXECUTION = {"schema_version": "1.0.0", "execution_profile_id": EXECUTION_PROFILE_ID,
              "executor_id": "omnigenis.local-paired-alignment", "executor_version": "1.0.0",
              "deterministic": True}
_LIMITATIONS = (
    "One explicitly selected paired Illumina non-UMI PHRED33 alignment profile; inputs are bounded by the current FASTQ intake.",
    "Only the exact complete GRCh38.p14 RefSeq reference, five-file index, executor binaries and observed runtime library cohort are accepted.",
    "Engineering artifact conformance does not establish general mapping accuracy, whole-genome throughput, coverage or callability.",
    "Biological sample identity, variant calling and clinical validity are not assessed.",
    "BWA-MEM2 upstream issue 262 remains unresolved; bounded boundary and supplementary regression evidence does not establish general parity with BWA.",
    "No preprocessing, filtering, deduplication, GPU executor, automatic resource acquisition or hosted service is enabled.",
)
_PROVENANCE = (
    "https://github.com/samtools/hts-specs/blob/b5341fbb5181cc7811c10c741a682149ab0cccf8/SAMv1.tex",
    "https://github.com/bwa-mem2/bwa-mem2/tree/7aa5ff6c3330490e5629ab9b7327683d2dce02d6",
    "https://github.com/samtools/samtools/tree/1.25",
    "https://www.ncbi.nlm.nih.gov/assembly/GCF_000001405.40/",
)
_ASSESSMENTS = {
    "biological_sample_identity": "NOT_ASSESSED", "clinical_validity": "NOT_ASSESSED",
    "coordinate_order": "PASS", "coverage_callability": "NOT_ASSESSED",
    "declared_read_group_consistency": "PASS", "full_stream_integrity": "PASS",
    "index_consistency": "PASS", "input_accounting": "PASS", "reference_identity": "PASS",
    "variant_calling": "NOT_ASSESSED",
}
_CODES = frozenset((
    "invalid_input_profile", "input_intake_refused", "input_identity_changed",
    "unsupported_execution_profile", "executor_identity_mismatch", "executor_unavailable",
    "reference_identity_mismatch", "index_identity_mismatch", "unsafe_read_group",
    "resource_unavailable", "resource_limit_exceeded", "deadline_exceeded",
    "native_execution_failed", "malformed_output", "truncated_output", "output_checksum_error",
    "header_reference_mismatch", "record_out_of_bounds", "coordinate_order_mismatch",
    "primary_accounting_mismatch", "secondary_supplementary_accounting_mismatch",
    "index_validation_failed", "output_schema_failed", "promotion_failed",
))
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TAG = re.compile(r"[A-Za-z][A-Za-z0-9]\Z")
_CIGAR = re.compile(r"([1-9][0-9]*)([MIDNSHP=X])")
_COMPLEMENT = bytes.maketrans(b"=ACMGRSVTWYHKDBN", b"=TGKCYSBAWRDMHVN")


class _Failure(ValueError):
    """Closed diagnostic: no native output, caller identifier or path."""

    def __init__(self, code: str):
        self.code = code if code in _CODES else "malformed_output"
        super().__init__(self.code)


def _require(condition, code):
    if not condition:
        raise _Failure(code)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _capture_json(value, maximum, code):
    stack, nodes = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        _require(nodes <= 16384 and depth <= 16, code)
        kind = type(item)
        if kind is dict:
            _require(len(item) <= 16384 and all(type(k) is str for k in item), code)
            stack.extend((k, depth + 1) for k in item)
            stack.extend((v, depth + 1) for v in item.values())
        elif kind is list:
            _require(len(item) <= 16384, code)
            stack.extend((v, depth + 1) for v in item)
        elif kind is str:
            _require(len(item) <= maximum, code)
        elif kind is int:
            _require(item.bit_length() <= 64, code)
        else:
            _require(kind is bool or item is None, code)
    try:
        raw = _canonical(value)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise _Failure(code) from None
    _require(len(raw) <= maximum, code)
    return raw


def _rss(pid=None):
    try:
        for line in Path(f"/proc/{pid or os.getpid()}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (FileNotFoundError, ProcessLookupError):
        return 0
    if pid is not None:
        return 0  # A reaped or zombie child has no resident pages.
    raise _Failure("resource_unavailable")


def _remaining(deadline):
    _require(time.monotonic() < deadline, "deadline_exceeded")
    _require(_rss() <= MAX_PARENT_RSS, "resource_limit_exceeded")
    return deadline - time.monotonic()


def _fingerprint(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _open_read(path, maximum, code):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or not 0 <= st.st_size <= maximum:
            os.close(fd)
            raise _Failure(code)
        return os.fdopen(fd, "rb")
    except OSError:
        raise _Failure(code) from None


def _read_bounded(path, maximum, code="malformed_output"):
    with _open_read(path, maximum, code) as stream:
        before = _fingerprint(os.fstat(stream.fileno()))
        raw = stream.read(maximum + 1)
        after = _fingerprint(os.fstat(stream.fileno()))
    _require(len(raw) <= maximum and len(raw) == before[2] and before == after
             and _fingerprint(Path(path).stat()) == after, code)
    return raw


def _verify_file_identity(path: Path, expected_sha256: str, expected_size: int,
                          deadline: float, code: str) -> dict:
    _require(type(expected_size) is int and expected_size >= 0
             and type(expected_sha256) is str and _SHA256.fullmatch(expected_sha256), code)
    with _open_read(path, expected_size, code) as stream:
        before = _fingerprint(os.fstat(stream.fileno()))
        _require(before[2] == expected_size, code)
        h, count = hashlib.sha256(), 0
        while True:
            _remaining(deadline)
            chunk = stream.read(1048576)
            if not chunk:
                break
            count += len(chunk)
            _require(count <= expected_size, code)
            h.update(chunk)
        after = _fingerprint(os.fstat(stream.fileno()))
    try:
        same_path = _fingerprint(Path(path).stat()) == after and not Path(path).is_symlink()
    except OSError:
        same_path = False
    _require(count == expected_size and h.hexdigest() == expected_sha256
             and before == after and same_path, code)
    return {"sha256": h.hexdigest(), "size_bytes": count, "fingerprint": before}


def _load_profiles():
    result = {}
    for name, (path, expected) in PROFILE_FILES.items():
        raw = _read_bounded(ROOT / path, 262144, "unsupported_execution_profile")
        _require(_sha(raw) == expected, "unsupported_execution_profile")
        try:
            result[name] = json.loads(raw)
        except (ValueError, UnicodeError):
            raise _Failure("unsupported_execution_profile") from None
    _require(result["execution"] == _EXECUTION, "unsupported_execution_profile")
    _require(result["alignment"]["profile_id"] == PROFILE_ID
             and result["alignment"]["limitations"] == list(_LIMITATIONS)
             and result["alignment"]["provenance_refs"] == list(_PROVENANCE),
             "unsupported_execution_profile")
    return result


@dataclass(frozen=True, repr=False)
class AlignmentResources:
    reference_fasta: str | os.PathLike[str]
    reference_fai: str | os.PathLike[str]
    reference_dictionary: str | os.PathLike[str]
    bwa_index_prefix: str | os.PathLike[str]
    bwa_executable: str | os.PathLike[str]
    samtools_executable: str | os.PathLike[str]
    private_work_root: str | os.PathLike[str]


@dataclass(frozen=True, repr=False)
class PreparedAlignmentArtifact:
    artifact_id: str
    artifact_kind: str
    media_type: str
    content: bytes
    sha256: str
    size_bytes: int
    producer_execution_profile_id: str
    parent_artifact_ids: tuple[str, ...]

    def to_manifest(self):
        return {"schema_version": "1.0.0", "artifact_id": self.artifact_id,
                "artifact_kind": self.artifact_kind, "media_type": self.media_type,
                "sha256": self.sha256, "size_bytes": self.size_bytes,
                "producer_execution_profile_id": self.producer_execution_profile_id,
                "parent_artifact_ids": list(self.parent_artifact_ids)}


@dataclass(frozen=True, repr=False)
class PairedReadAlignmentResult:
    _canonical_json: bytes
    artifacts: tuple[PreparedAlignmentArtifact, ...]

    def __post_init__(self):
        _require(type(self._canonical_json) is bytes and len(self._canonical_json) <= MAX_RESULT_BYTES
                 and type(self.artifacts) is tuple, "promotion_failed")
        payload = json.loads(self._canonical_json)
        valid = payload["canonical_status"] == "VALID"
        _require((valid and len(self.artifacts) == 2) or (not valid and not self.artifacts), "promotion_failed")
        if valid:
            accepted = payload["canonical_payload"]["accepted_alignment"]
            for artifact, key in zip(self.artifacts, ("bam_artifact", "bai_artifact")):
                _require(type(artifact) is PreparedAlignmentArtifact and type(artifact.content) is bytes
                         and artifact.to_manifest() == accepted[key]
                         and verify_artifact_bytes(artifact.to_manifest(), artifact.content).passed,
                         "promotion_failed")

    @property
    def is_valid(self):
        return bool(self.artifacts)

    def to_dict(self):
        return json.loads(self._canonical_json)


def _payload(accepted=None, code=None):
    return {
        "capability_id": CAPABILITY_ID, "capability_version": CAPABILITY_VERSION,
        "canonical_status": "VALID" if accepted is not None else "INVALID",
        "canonical_payload": {"schema_version": "1.0.0", "profile_id": PROFILE_ID,
                              "accepted_alignment": accepted,
                              "diagnostics": [] if accepted is not None else [{"code": code}]},
        "availability": "AVAILABLE", "limitations": list(_LIMITATIONS),
        "executor_id": "omnigenis.local-paired-alignment", "executor_version": "1.0.0",
        "adapter_version": "1.0.0", "model_id": None, "resource_release": "GCF_000001405.40",
        "reference_bundle": REFERENCE_PROFILE_ID, "execution_profile": EXECUTION_PROFILE_ID,
        "raw_artifact_refs": [] if accepted is None else [
            accepted["bam_artifact"]["artifact_id"], accepted["bai_artifact"]["artifact_id"]],
        "provenance_refs": list(_PROVENANCE),
    }


def _validate_export(payload):
    try:
        _capture_json(payload, MAX_RESULT_BYTES, "output_schema_failed")
        raw = _read_bounded(ROOT / SCHEMA_PATH, 262144, "output_schema_failed")
        _require(_sha(raw) == SCHEMA_SHA256, "output_schema_failed")
        schema = json.loads(raw)
        nodes = [schema]
        while nodes:
            node = nodes.pop()
            if type(node) is dict:
                if "$ref" in node:
                    _require(type(node["$ref"]) is str and node["$ref"].startswith("#/$defs/"),
                             "output_schema_failed")
                nodes.extend(node.values())
            elif type(node) is list:
                nodes.extend(node)
        from jsonschema import Draft202012Validator
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(payload)
        artifact_raw = _read_bounded(ROOT / "schemas/artifact-manifest.v1.schema.json", 4096, "output_schema_failed")
        _require(_sha(artifact_raw) == ARTIFACT_SCHEMA_SHA256, "output_schema_failed")
        artifact_schema = json.loads(artifact_raw)
        accepted = payload["canonical_payload"]["accepted_alignment"]
        if accepted is not None:
            for key in ("bam_artifact", "bai_artifact"):
                Draft202012Validator(artifact_schema).validate(accepted[key])
    except _Failure:
        raise
    except Exception:
        raise _Failure("output_schema_failed") from None


def _refusal(code):
    _require(code in _CODES, "malformed_output")
    payload = _payload(code=code)
    try:
        _validate_export(payload)
    except _Failure:
        payload = _payload(code="output_schema_failed")
    return PairedReadAlignmentResult(_canonical(payload), ())


@dataclass(repr=False)
class _InputSnapshot:
    profile_json: bytes
    input_profile_sha256: str
    pair_count: int
    r1_path: Path
    r2_path: Path
    fds: tuple[int, int]
    identities: tuple[dict, dict]
    expected_reads: Counter
    source_reads: dict

    def close(self):
        for fd in self.fds:
            try:
                os.close(fd)
            except OSError:
                pass


class _Tee:
    def __init__(self, data, output, descriptor, deadline):
        self.source, self.output, self.fd, self.deadline = io.BytesIO(data), output, descriptor, deadline
        self.failure = None

    def read(self, count):
        try:
            _remaining(self.deadline)
            part = self.source.read(count)
            self.output.write(part)
            view = memoryview(part)
            while view:
                written = os.write(self.fd, view)
                _require(written > 0, "resource_unavailable")
                view = view[written:]
            return part
        except _Failure as error:
            self.failure = error
            raise


def _snapshot_inputs(r1: bytes, r2: bytes, input_profile: dict,
                     workspace: Path, deadline: float) -> _InputSnapshot:
    _require(type(r1) is bytes and type(r2) is bytes
             and len(r1) <= MAX_MATE_BYTES and len(r2) <= MAX_MATE_BYTES, "input_intake_refused")
    profile_json = _capture_json(input_profile, 4096, "invalid_input_profile")
    profile = json.loads(profile_json)
    fds, paths = [], (workspace / "input-r1.fastq", workspace / "input-r2.fastq")
    try:
        for name in ("alignment-input-r1", "alignment-input-r2"):
            fds.append(os.memfd_create(name, os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING))
        with paths[0].open("xb") as left, paths[1].open("xb") as right:
            os.chmod(paths[0], 0o600)
            os.chmod(paths[1], 0o600)
            streams = (_Tee(r1, left, fds[0], deadline), _Tee(r2, right, fds[1], deadline))
            intake = validate_paired_fastq(*streams, profile)
            for stream in streams:
                if stream.failure is not None:
                    raise stream.failure
            left.flush()
            right.flush()
            os.fsync(left.fileno())
            os.fsync(right.fileno())
        if not intake.is_valid:
            native_code = intake.to_dict()["canonical_payload"]["diagnostics"][0]["code"]
            raise _Failure("invalid_input_profile" if native_code == "invalid_profile" else "input_intake_refused")
        accepted = intake.to_dict()["canonical_payload"]["accepted_pair"]
        _require(accepted["input_profile_sha256"] == _sha(profile_json), "input_identity_changed")
        seals = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
        identities = []
        for i, (data, path) in enumerate(zip((r1, r2), paths)):
            fcntl.fcntl(fds[i], fcntl.F_ADD_SEALS, seals)
            _require(fcntl.fcntl(fds[i], fcntl.F_GET_SEALS) & seals == seals, "input_identity_changed")
            os.lseek(fds[i], 0, os.SEEK_SET)
            os.chmod(path, 0o400)
            identity = _verify_file_identity(path, _sha(data), len(data), deadline, "input_identity_changed")
            _require(accepted["r1" if i == 0 else "r2"] ==
                     {"content_sha256": identity["sha256"], "size_bytes": identity["size_bytes"]},
                     "input_identity_changed")
            identities.append(identity)
        expected, source = Counter(), defaultdict(set)
        for mate, data in enumerate((r1, r2), 1):
            stream = io.BytesIO(data)
            while True:
                _remaining(deadline)
                header = stream.readline(4097)
                if not header:
                    break
                seq, separator, qual = (stream.readline(4097) for _ in range(3))
                qname = header.rstrip(b"\r\n").split(b" ", 1)[0][1:]
                seq, qual = seq.rstrip(b"\r\n"), qual.rstrip(b"\r\n")
                _require(separator.rstrip(b"\r\n") == b"+" and 0 < len(seq) <= MAX_READ_BASES
                         and len(seq) == len(qual) and 0 < len(qname) <= 254, "input_identity_changed")
                expected[(qname, mate, seq, qual)] += 1
                source[(qname, mate)].add((seq, qual))
        _require(sum(expected.values()) == accepted["pair_count"] * 2, "input_identity_changed")
        return _InputSnapshot(profile_json, accepted["input_profile_sha256"], accepted["pair_count"],
                              paths[0], paths[1], tuple(fds), tuple(identities),
                              expected, {k: tuple(sorted(v)) for k, v in source.items()})
    except BaseException:
        for fd in fds:
            os.close(fd)
        raise


def _verify_snapshot(snapshot, deadline):
    seals = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
    for fd, path, expected in zip(snapshot.fds, (snapshot.r1_path, snapshot.r2_path), snapshot.identities):
        _remaining(deadline)
        actual = _verify_file_identity(path, expected["sha256"], expected["size_bytes"], deadline,
                                      "input_identity_changed")
        _require(actual == expected and fcntl.fcntl(fd, fcntl.F_GET_SEALS) & seals == seals
                 and os.fstat(fd).st_size == expected["size_bytes"], "input_identity_changed")
        digest = hashlib.sha256()
        offset = 0
        while offset < expected["size_bytes"]:
            part = os.pread(fd, min(65536, expected["size_bytes"] - offset), offset)
            _require(part, "input_identity_changed")
            digest.update(part)
            offset += len(part)
        _require(digest.hexdigest() == expected["sha256"], "input_identity_changed")


def _read_group(snapshot):
    profile = json.loads(snapshot.profile_json)
    values = {"ID": profile["declared_read_group_id"], "SM": profile["declared_sample_id"], "PL": "ILLUMINA"}
    _require(all(type(v) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", v)
                 for v in values.values()), "unsafe_read_group")
    return values


@dataclass(frozen=True)
class _CommandLimits:
    address_space_bytes: int
    rss_bytes: int
    wall_seconds: int
    stdout_bytes: int
    stderr_bytes: int
    file_bytes: int
    disk_bytes: int = MAX_MUTABLE_BYTES
    free_disk_bytes: int = MIN_FREE_BYTES


def _owned_size(root):
    total = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        _require(all(not (Path(directory) / name).is_symlink() for name in dirs), "resource_limit_exceeded")
        for name in files:
            path = Path(directory) / name
            try:
                st = path.lstat()
            except FileNotFoundError:
                continue  # A native temporary file may disappear after os.walk.
            _require(stat.S_ISREG(st.st_mode), "resource_limit_exceeded")
            total += st.st_size
    return total


def _private_json(path, value, *, replace=False):
    raw = _canonical(value) + b"\n"
    _require(len(raw) <= 65536, "resource_limit_exceeded")
    target = path.with_name(path.name + ".replacement") if replace else path
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if replace:
        os.replace(target, path)


def _group_usage(pgid):
    rss, cpu, members = 0, 0.0, []
    ticks = os.sysconf("SC_CLK_TCK")
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            fields = Path(f"/proc/{name}/stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) != pgid:
                continue
            members.append(int(name))
            rss += _rss(int(name))
            cpu += (int(fields[11]) + int(fields[12])) / ticks
        except (FileNotFoundError, ProcessLookupError):
            continue
    return rss, cpu, members


def _signal_group(pgid, sig):
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass


def _run_native(argv: list[str], *, cwd: Path, deadline: float, limits: _CommandLimits,
                stdout_path: Path, stderr_path: Path, pass_fds: tuple[int, ...] = ()) -> dict:
    """Supervise one owned process group; any failed bound is a refusal."""
    _remaining(deadline)
    _require(type(argv) is list and argv and all(type(x) is str and "\0" not in x for x in argv),
             "native_execution_failed")
    _require(type(limits) is _CommandLimits and all(type(getattr(limits, name)) is int
             and getattr(limits, name) > 0 for name in
             ("address_space_bytes", "rss_bytes", "wall_seconds", "stdout_bytes", "stderr_bytes", "file_bytes", "disk_bytes"))
             and type(limits.free_disk_bytes) is int and limits.free_disk_bytes >= 0, "resource_unavailable")
    cwd, stdout_path, stderr_path = Path(cwd), Path(stdout_path), Path(stderr_path)
    _require(cwd.is_absolute() and cwd.resolve() == cwd and cwd.is_dir()
             and cwd in stdout_path.parents and cwd in stderr_path.parents
             and stdout_path != stderr_path, "resource_unavailable")
    _require(_owned_size(cwd) <= limits.disk_bytes
             and shutil.disk_usage(cwd).free >= limits.free_disk_bytes, "resource_limit_exceeded")
    cpu = min(os.sched_getaffinity(0))
    wall = min(float(limits.wall_seconds), _remaining(deadline))
    cpu_limit = max(1, math.ceil(wall))
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "TZ": "UTC",
                   "TMPDIR": str(cwd), "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                   "MKL_NUM_THREADS": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
    receipt = {"argv": argv, "cwd": str(cwd), "status": "FAIL", "exit_code": None,
               "cpu_affinity": [cpu], "peak_sampled_group_rss_bytes": 0,
               "final_maxrss_bytes": None, "wall_seconds": None,
               "stdout_bytes": 0, "stderr_bytes": 0}
    started = time.monotonic()
    proc, usage, code = None, None, None
    reaped, killed_at, last_disk = False, None, started
    last_usage, members = started - 0.025, []

    def child_limits():
        os.sched_setaffinity(0, {cpu})
        resource.setrlimit(resource.RLIMIT_AS, (limits.address_space_bytes, limits.address_space_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit))
        resource.setrlimit(resource.RLIMIT_FSIZE, (limits.file_bytes, limits.file_bytes))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    try:
        stdout_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        stderr_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with stdout_path.open("xb") as out, stderr_path.open("xb") as err, selectors.DefaultSelector() as selector:
            os.chmod(stdout_path, 0o600)
            os.chmod(stderr_path, 0o600)
            proc = subprocess.Popen(argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                                    start_new_session=True, pass_fds=pass_fds, preexec_fn=child_limits)
            for stream, target, name, maximum in (
                (proc.stdout, out, "stdout_bytes", limits.stdout_bytes),
                (proc.stderr, err, "stderr_bytes", limits.stderr_bytes),
            ):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, (target, name, maximum))
            while True:
                for key, _ in selector.select(timeout=0.025):
                    part = os.read(key.fileobj.fileno(), 65536)
                    if not part:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    target, name, maximum = key.data
                    remaining = max(0, maximum - receipt[name])
                    target.write(part[:remaining])
                    target.flush()
                    receipt[name] += min(remaining, len(part))
                    if len(part) > remaining:
                        code = code or "resource_limit_exceeded"
                just_reaped = False
                if not reaped:
                    pid, wait_status, child_usage = os.wait4(proc.pid, os.WNOHANG)
                    if pid:
                        reaped, just_reaped, usage = True, True, child_usage
                        proc.returncode = os.waitstatus_to_exitcode(wait_status)
                        receipt["exit_code"] = proc.returncode
                now = time.monotonic()
                if now - last_usage >= 0.025 or just_reaped:
                    group_rss, group_cpu, members = _group_usage(proc.pid)
                    last_usage = now
                    receipt["peak_sampled_group_rss_bytes"] = max(receipt["peak_sampled_group_rss_bytes"], group_rss)
                    if group_rss > limits.rss_bytes or group_cpu > cpu_limit:
                        code = code or "resource_limit_exceeded"
                if time.monotonic() - started > wall or time.monotonic() >= deadline:
                    code = code or "deadline_exceeded"
                if reaped and members:
                    code = code or "native_execution_failed"
                if time.monotonic() - last_disk >= 0.1:
                    if _owned_size(cwd) > limits.disk_bytes or shutil.disk_usage(cwd).free < limits.free_disk_bytes:
                        code = code or "resource_limit_exceeded"
                    last_disk = time.monotonic()
                if code and killed_at is None:
                    _signal_group(proc.pid, signal.SIGTERM)
                    killed_at = time.monotonic()
                if killed_at is not None and time.monotonic() - killed_at > 1:
                    _signal_group(proc.pid, signal.SIGKILL)
                if reaped and not selector.get_map() and not members:
                    break
                if killed_at is not None and time.monotonic() - killed_at > 5:
                    raise _Failure(code or "native_execution_failed")
            out.flush()
            err.flush()
            os.fsync(out.fileno())
            os.fsync(err.fileno())
        _require(usage is not None, "native_execution_failed")
        receipt["final_maxrss_bytes"] = int(usage.ru_maxrss) * 1024
        receipt["user_seconds"], receipt["system_seconds"] = usage.ru_utime, usage.ru_stime
        if receipt["final_maxrss_bytes"] > limits.rss_bytes or usage.ru_utime + usage.ru_stime > cpu_limit:
            code = code or "resource_limit_exceeded"
        if time.monotonic() - started > wall:
            code = code or "deadline_exceeded"
        if receipt["exit_code"] != 0:
            if receipt["exit_code"] in (-signal.SIGXCPU, -signal.SIGXFSZ):
                code = code or "resource_limit_exceeded"
            code = code or "native_execution_failed"
        _require(code is None, code or "native_execution_failed")
        _require(_owned_size(cwd) <= limits.disk_bytes
                 and shutil.disk_usage(cwd).free >= limits.free_disk_bytes, "resource_limit_exceeded")
        _remaining(deadline)
        receipt["status"] = "PASS"
    except BaseException as error:
        if proc is not None:
            _signal_group(proc.pid, signal.SIGKILL)
            if not reaped:
                _, wait_status, usage = os.wait4(proc.pid, 0)
                proc.returncode = os.waitstatus_to_exitcode(wait_status)
                receipt["exit_code"] = proc.returncode
            if usage is not None:
                receipt["final_maxrss_bytes"] = int(usage.ru_maxrss) * 1024
                receipt["user_seconds"], receipt["system_seconds"] = usage.ru_utime, usage.ru_stime
        if isinstance(error, _Failure):
            receipt["diagnostic"] = error.code
            raise
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            receipt["diagnostic"] = "native_execution_failed"
            raise
        receipt["diagnostic"] = "native_execution_failed"
        raise _Failure("native_execution_failed") from None
    finally:
        receipt["wall_seconds"] = round(time.monotonic() - started, 6)
        _private_json(stdout_path.with_name(stdout_path.name + ".receipt.json"), receipt)
    return receipt


def _limits(profiles, kind, *, output=16777216, file_bytes=536870912):
    budget = profiles["alignment"]["resource_budget"]
    item = budget[kind]
    return _CommandLimits(item["address_space_bytes"], item["rss_bytes"], item["wall_seconds"],
                          output, budget["stderr_bytes"], file_bytes,
                          budget["mutable_output_bytes"], budget["min_disk_free_bytes"])


def _command(profiles, workspace, label, argv, deadline, kind="control",
             stdout_path=None, output=16777216, file_bytes=536870912, pass_fds=()):
    stdout_path = stdout_path or workspace / f"{label}.stdout"
    _run_native([str(x) for x in argv], cwd=workspace, deadline=deadline,
                limits=_limits(profiles, kind, output=output, file_bytes=file_bytes),
                stdout_path=stdout_path, stderr_path=workspace / f"{label}.stderr", pass_fds=pass_fds)
    return stdout_path


def _sealed_copy(path, expected, deadline):
    row = _verify_file_identity(path, expected["sha256"], expected["size_bytes"],
                                deadline, "executor_identity_mismatch")
    _require(os.access(path, os.X_OK), "executor_unavailable")
    fd = os.memfd_create("alignment-executor", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        with _open_read(path, expected["size_bytes"], "executor_unavailable") as stream:
            content = stream.read(expected["size_bytes"] + 1)
        _require(len(content) == expected["size_bytes"] and _sha(content) == expected["sha256"],
                 "executor_identity_mismatch")
        view = memoryview(content)
        while view:
            n = os.write(fd, view)
            _require(n > 0, "executor_unavailable")
            view = view[n:]
        os.fchmod(fd, 0o700)
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS,
                    fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
        os.lseek(fd, 0, os.SEEK_SET)
        _require(content[:6] == b"\x7fELF\x02\x01" and content[18:20] == b"\x3e\x00",
                 "executor_identity_mismatch")
        return fd, row
    except BaseException:
        os.close(fd)
        raise


def _cgroup_memory_headroom():
    """Read applicable unified-hierarchy limits; unexplained absence is a refusal."""
    membership = Path("/proc/self/cgroup").read_text().splitlines()
    _require(not any("memory" in line.split(":", 2)[1].split(",")
                     for line in membership if not line.startswith("0::")), "resource_unavailable")
    rows = [line.split(":", 2)[2] for line in membership if line.startswith("0::")]
    _require(len(rows) == 1 and ".." not in Path(rows[0]).parts, "resource_unavailable")
    mounts = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        left, right = line.split(" - ", 1)
        if right.split()[0] == "cgroup2":
            fields = left.split()
            unescape = lambda text: re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), text)
            mounts.append((Path(unescape(fields[3])), Path(unescape(fields[4]))))
    _require(len(mounts) == 1, "resource_unavailable")
    mount_root, top = mounts[0]
    try:
        current = top / Path(rows[0]).relative_to(mount_root)
    except ValueError:
        raise _Failure("resource_unavailable") from None
    ceilings = []
    while True:
        _require((current == top or top in current.parents) and current.is_dir(),
                 "resource_unavailable")
        # Successful enumeration and interface reads distinguish observed absence
        # from inaccessible hierarchy state. No controller configuration is changed.
        entries = set(os.listdir(current))
        (current / "cgroup.controllers").read_text()
        cap_path, used_path = current / "memory.max", current / "memory.current"
        if "memory.max" in entries:
            _require("memory.current" in entries, "resource_unavailable")
            cap, used = cap_path.read_text().strip(), used_path.read_text().strip()
            _require(used.isdigit() and (cap == "max" or cap.isdigit()), "resource_unavailable")
            if cap != "max":
                ceilings.append(int(cap) - int(used))
        elif current == top:
            # The kernel root has no memory.max/current. A subtree mount cannot
            # use that exception: its actual inherited control must be readable.
            _require(mount_root == Path("/") and "cgroup.type" not in entries
                     and "memory.current" not in entries, "resource_unavailable")
        else:
            # A controller disabled for this parent's immediate children adds
            # no child limit; applicable ancestor limits are still read below.
            parent_controls = (current.parent / "cgroup.subtree_control").read_text().split()
            _require("memory" not in parent_controls and "memory.current" not in entries
                     and (current / "cgroup.type").read_text().strip() == "domain",
                     "resource_unavailable")
        if current == top:
            break
        current = current.parent
    return ceilings


def _capacity(workspace, profiles):
    budget = profiles["alignment"]["resource_budget"]
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        if key == "MemAvailable":
            values[key] = int(value.split()[0]) * 1024
    _require(values.get("MemAvailable", 0) >= budget["min_mem_available_bytes"], "resource_unavailable")
    _require(all(x >= budget["min_mem_available_bytes"] for x in _cgroup_memory_headroom()), "resource_unavailable")
    _require(shutil.disk_usage(workspace).free >= budget["min_disk_free_bytes"]
             and _owned_size(workspace) <= budget["mutable_output_bytes"], "resource_unavailable")


@dataclass(repr=False)
class _ObservedResources:
    contigs: tuple[tuple[str, int], ...]
    reference_binding: dict
    executor_binding: dict
    identities: tuple
    index_prefix: Path
    executable_fds: tuple[int, int]

    def close(self):
        for fd in self.executable_fds:
            try:
                os.close(fd)
            except OSError:
                pass


def _index_inventory(prefix):
    _require(prefix.is_absolute() and prefix.parent.resolve() == prefix.parent, "index_identity_mismatch")
    expected = {prefix.name + suffix for suffix in INDEX_SUFFIXES}
    actual = {p.name for p in prefix.parent.iterdir() if p.name.startswith(prefix.name + ".")}
    _require(actual == expected, "index_identity_mismatch")


def _observe_resources(resources: AlignmentResources, workspace: Path, deadline: float, profiles: dict):
    _require(type(resources) is AlignmentResources, "resource_unavailable")
    executor, reference = profiles["executor"], profiles["reference"]
    platform_profile = executor["platform"]
    _require(platform.system() == platform_profile["system"]
             and platform.machine() == platform_profile["machine"]
             and sys.byteorder == platform_profile["byte_order"], "executor_unavailable")
    cpu = min(os.sched_getaffinity(0))
    blocks = Path("/proc/cpuinfo").read_text().split("\n\n")
    flag_sets = []
    for block in blocks:
        fields = dict(line.split(":", 1) for line in block.splitlines() if ":" in line)
        fields = {k.strip(): v.strip() for k, v in fields.items()}
        if fields.get("processor") == str(cpu):
            flag_sets.append(set(fields.get("flags", "").split()))
    _require(len(flag_sets) == 1 and set(platform_profile["required_cpu_flags"]) <= flag_sets[0],
             "executor_unavailable")
    identities, fds = [], []
    paths = {"bwa": Path(resources.bwa_executable), "samtools": Path(resources.samtools_executable)}
    try:
        for name in ("bwa", "samtools"):
            _require(paths[name].is_absolute() and paths[name].resolve() == paths[name], "executor_unavailable")
            fd, observed = _sealed_copy(paths[name], executor["executors"][name], deadline)
            fds.append(fd)
            identities.append((paths[name], observed, "executor_identity_mismatch"))
        ldd = Path("/usr/bin/ldd")
        lp = executor["system_tools"]["ldd"]
        identities.append((ldd, _verify_file_identity(ldd, lp["sha256"], lp["size_bytes"],
                                                      deadline, "executor_identity_mismatch"),
                           "executor_identity_mismatch"))
        expected_libraries = {row["soname"]: row for row in executor["runtime_libraries"]}
        found = {}
        for name in ("bwa", "samtools"):
            fd = fds[0 if name == "bwa" else 1]
            output = _command(profiles, workspace, f"{name}-libraries",
                              [ldd, f"/proc/self/fd/{fd}"], deadline, pass_fds=(fd,))
            text = _read_bounded(output, 1048576, "executor_identity_mismatch").decode("ascii")
            names = []
            for raw in text.splitlines():
                line = raw.strip()
                if not line or line.startswith("linux-vdso.so.1 "):
                    continue
                _require("not found" not in line, "executor_unavailable")
                match = re.match(r"(\S+)\s+=>\s+(/\S+)\s+\(", line)
                if match:
                    soname, lexical = match.group(1), Path(match.group(2))
                else:
                    match = re.match(r"(/\S+)\s+\(", line)
                    _require(match is not None, "executor_identity_mismatch")
                    lexical = Path(match.group(1))
                    soname = lexical.name
                resolved = lexical.resolve(strict=True)
                _require(soname in expected_libraries and soname not in names
                         and any(p in resolved.parents for p in (Path("/usr/lib"), Path("/lib"))),
                         "executor_identity_mismatch")
                wanted = expected_libraries[soname]
                observed = _verify_file_identity(resolved, wanted["sha256"], wanted["size_bytes"],
                                                 deadline, "executor_identity_mismatch")
                names.append(soname)
                row = {"soname": soname, "size_bytes": observed["size_bytes"], "sha256": observed["sha256"]}
                _require(soname not in found or found[soname] == row, "executor_identity_mismatch")
                found[soname] = row
                identities.append((resolved, observed, "executor_identity_mismatch"))
            _require(sorted(names) == executor["runtime_library_sets"][name], "executor_identity_mismatch")
        libraries = [found[name] for name in sorted(found)]
        library_digest = _sha(_canonical(libraries))
        _require(libraries == executor["runtime_libraries"]
                 and library_digest == executor["runtime_libraries_sha256"], "executor_identity_mismatch")
        bwa_version = _command(profiles, workspace, "bwa-version", [f"/proc/self/fd/{fds[0]}", "version"],
                               deadline, pass_fds=(fds[0],))
        sam_version = _command(profiles, workspace, "samtools-version",
                               [f"/proc/self/fd/{fds[1]}", "--version"], deadline, pass_fds=(fds[1],))
        _require(_read_bounded(bwa_version, 65536).decode("ascii").strip() == "2.2.1", "executor_identity_mismatch")
        version_text = _read_bounded(sam_version, 65536).decode("ascii")
        _require(re.search(r"^samtools 1\.25\s*$", version_text, re.M)
                 and re.search(r"^Using htslib 1\.25\s*$", version_text, re.M)
                 and "htscodecs=1.6.7" in version_text, "executor_identity_mismatch")
        resource_paths = {"fasta": Path(resources.reference_fasta), "fai": Path(resources.reference_fai),
                          "dictionary": Path(resources.reference_dictionary)}
        for name, path in resource_paths.items():
            wanted = reference[name]
            _require(path.is_absolute() and path.resolve() == path, "reference_identity_mismatch")
            observed = _verify_file_identity(path, wanted["sha256"], wanted["size_bytes"],
                                             deadline, "reference_identity_mismatch")
            identities.append((path, observed, "reference_identity_mismatch"))
        contigs = tuple((row["name"], row["length"]) for row in reference["contigs"])
        _require(len(contigs) == reference["sequence_count"]
                 and all(row["aliases"] == [row["name"]] for row in reference["contigs"]),
                 "reference_identity_mismatch")
        parsed = []
        for line in _read_bounded(resource_paths["fai"], 1048576).decode("ascii").splitlines():
            parts = line.split("\t")
            _require(len(parts) == 5 and all(x.isdigit() for x in parts[1:]), "reference_identity_mismatch")
            parsed.append((parts[0], int(parts[1])))
        _require(tuple(parsed) == contigs and max(n for _, n in contigs) < 2**29, "reference_identity_mismatch")
        dictionary = []
        for line in _read_bounded(resource_paths["dictionary"], 1048576).decode("ascii").splitlines():
            if line.startswith("@SQ\t"):
                fields = _header_fields(line.encode())
                dictionary.append((fields["SN"], int(fields["LN"])))
        _require(tuple(dictionary) == contigs, "reference_identity_mismatch")
        contig_digest = _sha(_canonical([{"name": name, "length": length} for name, length in contigs]))
        _require(contig_digest == reference["contig_dictionary_sha256"], "reference_identity_mismatch")
        prefix = Path(resources.bwa_index_prefix)
        _index_inventory(prefix)
        index_hashes = {}
        for suffix in INDEX_SUFFIXES:
            wanted = reference["index_files"][suffix]
            path = Path(str(prefix) + suffix)
            observed = _verify_file_identity(path, wanted["sha256"], wanted["size_bytes"],
                                             deadline, "index_identity_mismatch")
            identities.append((path, observed, "index_identity_mismatch"))
            index_hashes[suffix] = observed["sha256"]
        _capacity(workspace, profiles)
        reference_binding = {
            "profile_id": REFERENCE_PROFILE_ID, "profile_sha256": PROFILE_FILES["reference"][1],
            "fasta_sha256": reference["fasta"]["sha256"], "fai_sha256": reference["fai"]["sha256"],
            "dictionary_sha256": reference["dictionary"]["sha256"], "index_files": index_hashes,
            "contig_dictionary_sha256": contig_digest,
        }
        executor_binding = {
            "profile_id": EXECUTOR_PROFILE_ID, "profile_sha256": PROFILE_FILES["executor"][1],
            "bwa_sha256": executor["executors"]["bwa"]["sha256"],
            "samtools_sha256": executor["executors"]["samtools"]["sha256"],
            "runtime_libraries_sha256": library_digest,
        }
        return _ObservedResources(contigs, reference_binding, executor_binding,
                                  tuple(identities), prefix, tuple(fds))
    except BaseException:
        for fd in fds:
            os.close(fd)
        raise


def _verify_resources(observed, deadline):
    _index_inventory(observed.index_prefix)
    for path, expected, code in observed.identities:
        actual = _verify_file_identity(path, expected["sha256"], expected["size_bytes"], deadline, code)
        _require(actual == expected, code)
    seals = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
    for fd in observed.executable_fds:
        _require(fcntl.fcntl(fd, fcntl.F_GET_SEALS) & seals == seals, "executor_identity_mismatch")


def _header_fields(line):
    fields = line.decode("ascii").split("\t")
    result = {}
    for field in fields[1:]:
        _require(len(field) >= 4 and field[2] == ":" and field[:2] not in result,
                 "header_reference_mismatch")
        result[field[:2]] = field[3:]
    return result


def _check_header(lines, contigs, read_group, *, sorted_required, normalized):
    _require(lines and sum(len(line) + 1 for line in lines) <= MAX_HEADER_BYTES,
             "header_reference_mismatch")
    sq, groups, programs, hd = [], [], [], []
    for line in lines:
        kind = line.split(b"\t", 1)[0]
        _require(kind in (b"@HD", b"@SQ", b"@RG", b"@PG"), "header_reference_mismatch")
        values = _header_fields(line)
        if kind == b"@SQ":
            _require(set(values) <= {"SN", "LN", "UR"} and "SN" in values and "LN" in values,
                     "header_reference_mismatch")
            length = _integer(values["LN"].encode(), 1, 2**29 - 1, "header_reference_mismatch")
            sq.append((values["SN"], length))
        elif kind == b"@RG":
            groups.append(values)
        elif kind == b"@PG":
            programs.append(values)
        else:
            hd.append(values)
    _require(tuple(sq) == tuple(contigs) and groups == [read_group]
             and len(hd) <= 1 and len(programs) == 1, "header_reference_mismatch")
    if hd:
        _require(hd[0].get("VN") == "1.6", "header_reference_mismatch")
    if sorted_required:
        _require(len(hd) == 1 and hd[0].get("SO") == "coordinate", "coordinate_order_mismatch")
    pg = programs[0]
    _require(pg.get("ID") == "bwa-mem2" and pg.get("PN") == "bwa-mem2" and pg.get("VN") == "2.2.1"
             and set(pg) <= ({"ID", "PN", "VN"} if normalized else {"ID", "PN", "VN", "CL"}),
             "header_reference_mismatch")
    return (tuple(sq), tuple(sorted(read_group.items())), ("bwa-mem2", "2.2.1"))


def _integer(raw, lower, upper, code="malformed_output"):
    _require(type(raw) is bytes and 1 <= len(raw) <= 12
             and re.fullmatch(rb"-?[0-9]+", raw), code)
    number = int(raw)
    _require(lower <= number <= upper, code)
    return number


def _iter_sam(path, contigs, read_group, deadline, *, sorted_required=False, normalized=True,
              contig_names=None):
    """Yield one header tuple, then every record; EOF and file identity are checked."""
    if contig_names is None:
        contig_names = {name.encode(): i for i, (name, _) in enumerate(contigs)}
    with _open_read(path, MAX_SAM_BYTES, "malformed_output") as stream:
        before = _fingerprint(os.fstat(stream.fileno()))
        header, first = [], None
        while True:
            _remaining(deadline)
            raw = stream.readline(MAX_RECORD_BYTES + 1)
            if not raw:
                break
            _require(len(raw) <= MAX_RECORD_BYTES, "resource_limit_exceeded")
            _require(raw.endswith(b"\n") and b"\r" not in raw and raw.isascii(), "malformed_output")
            if raw.startswith(b"@"):
                header.append(raw[:-1])
                _require(sum(len(x) + 1 for x in header) <= MAX_HEADER_BYTES, "resource_limit_exceeded")
            else:
                first = raw
                break
        _check_header(header, contigs, read_group, sorted_required=sorted_required, normalized=normalized)
        yield tuple(header)
        previous = None
        while first is not None:
            _remaining(deadline)
            record = _parse_sam_record(first[:-1], contigs, contig_names)
            if sorted_required:
                key = (record.rid if record.rid >= 0 else len(contigs), record.pos)
                _require(previous is None or previous <= key, "coordinate_order_mismatch")
                previous = key
            yield record
            raw = stream.readline(MAX_RECORD_BYTES + 1)
            if not raw:
                first = None
            else:
                _require(len(raw) <= MAX_RECORD_BYTES, "resource_limit_exceeded")
                _require(raw.endswith(b"\n") and b"\r" not in raw and raw.isascii()
                         and not raw.startswith(b"@"), "malformed_output")
                first = raw
        _require(before == _fingerprint(os.fstat(stream.fileno()))
                 and before == _fingerprint(Path(path).stat()), "input_identity_changed")


def _normalize_sam_header(source: Path, target: Path, *, contigs, read_group, deadline: float) -> None:
    """Remove only operational PG command metadata; all record bytes are retained."""
    with _open_read(source, MAX_SAM_BYTES, "malformed_output") as src, target.open("xb") as dst:
        os.chmod(target, 0o600)
        before = _fingerprint(os.fstat(src.fileno()))
        header, first = [], None
        while True:
            _remaining(deadline)
            raw = src.readline(MAX_RECORD_BYTES + 1)
            if not raw:
                break
            _require(len(raw) <= MAX_RECORD_BYTES and raw.endswith(b"\n")
                     and b"\r" not in raw and raw.isascii(), "malformed_output")
            if raw.startswith(b"@"):
                header.append(raw[:-1])
                _require(sum(len(x) + 1 for x in header) <= MAX_HEADER_BYTES, "resource_limit_exceeded")
            else:
                first = raw
                break
        _check_header(header, contigs, read_group, sorted_required=False, normalized=False)
        normalized = [CANONICAL_PG if line.startswith(b"@PG\t") else line for line in header]
        _require(_check_header(normalized, contigs, read_group, sorted_required=False, normalized=True)
                 == _check_header(header, contigs, read_group, sorted_required=False, normalized=False),
                 "header_reference_mismatch")
        normalized_header = b"\n".join(normalized) + b"\n"
        dst.write(normalized_header)
        expected_content = hashlib.sha256(normalized_header)
        size = sum(len(x) + 1 for x in normalized)
        while first is not None:
            _remaining(deadline)
            _require(not first.startswith(b"@"), "malformed_output")
            size += len(first)
            _require(size <= MAX_SAM_BYTES, "resource_limit_exceeded")
            expected_content.update(first)
            dst.write(first)
            raw = src.readline(MAX_RECORD_BYTES + 1)
            if not raw:
                first = None
            else:
                _require(len(raw) <= MAX_RECORD_BYTES and raw.endswith(b"\n")
                         and b"\r" not in raw and raw.isascii(), "malformed_output")
                first = raw
        dst.flush()
        os.fsync(dst.fileno())
        _require(before == _fingerprint(os.fstat(src.fileno()))
                 and before == _fingerprint(source.stat()), "input_identity_changed")
    _verify_file_identity(target, expected_content.hexdigest(), size, deadline, "input_identity_changed")


def _cigar_ops(raw):
    if raw == b"*":
        return ()
    _require(len(raw) <= MAX_RECORD_BYTES and raw.isascii(), "malformed_output")
    text = raw.decode("ascii")
    parts = _CIGAR.findall(text)
    _require(parts and "".join(n + op for n, op in parts) == text, "malformed_output")
    operations = []
    for n, op in parts:
        length = _integer(n.encode(), 1, 2**28 - 1)
        operations.append((length, op))
    return tuple(operations)


def _float_value(value):
    # The pinned HTSlib text formatter uses six significant digits (%g).
    # Normalize binary32 and its SAM rendering identically; native text streams
    # are also compared in full, including all auxiliary values.
    try:
        v = struct.unpack("<f", struct.pack("<f", float(value)))[0]
    except (ValueError, OverflowError, struct.error):
        raise _Failure("malformed_output") from None
    return format(v, ".6g")


_ARRAY_TYPES = {"c": ("<b", -(2**7), 2**7 - 1), "C": ("<B", 0, 2**8 - 1),
                "s": ("<h", -(2**15), 2**15 - 1), "S": ("<H", 0, 2**16 - 1),
                "i": ("<i", -(2**31), 2**31 - 1), "I": ("<I", 0, 2**32 - 1)}


def _sam_tags(parts):
    tags, seen = [], set()
    for raw in parts:
        values = raw.split(b":", 2)
        _require(len(values) == 3, "malformed_output")
        tag, kind, data = (x.decode("ascii") for x in values)
        _require(_TAG.fullmatch(tag) and tag not in seen, "malformed_output")
        seen.add(tag)
        if kind == "i":
            value = _integer(data.encode(), -(2**31), 2**32 - 1)
        elif kind == "f":
            _require(len(data) <= 128, "malformed_output")
            value = _float_value(data)
        elif kind == "A":
            _require(len(data) == 1 and 33 <= ord(data) <= 126, "malformed_output")
            value = data
        elif kind == "Z":
            _require(all(32 <= ord(ch) <= 126 for ch in data), "malformed_output")
            value = data
        elif kind == "H":
            _require(len(data) % 2 == 0 and re.fullmatch("[0-9A-F]*", data), "malformed_output")
            value = data
        elif kind == "B":
            items = data.split(",")
            subtype = items[0]
            _require(subtype in _ARRAY_TYPES or subtype == "f", "malformed_output")
            if subtype == "f":
                value = (subtype, tuple(_float_value(x) for x in items[1:]))
            else:
                _, lower, upper = _ARRAY_TYPES[subtype]
                value = (subtype, tuple(_integer(x.encode(), lower, upper) for x in items[1:]))
        else:
            raise _Failure("malformed_output")
        tags.append((tag, kind, value))
    return tuple(sorted(tags))


@dataclass(frozen=True, slots=True)
class _Record:
    qname: bytes
    flag: int
    rid: int
    pos: int
    mapq: int
    cigar: tuple
    next_rid: int
    next_pos: int
    tlen: int
    sequence: bytes | None
    quality: bytes | None
    tags: tuple


def _check_record(record, contigs):
    r = record
    _require(0 < len(r.qname) <= 254 and all(33 <= x <= 126 and x != 64 for x in r.qname),
             "malformed_output")
    _require(0 <= r.flag <= 65535 and r.flag & 1
             and bool(r.flag & 64) != bool(r.flag & 128), "malformed_output")
    _require(-1 <= r.rid < len(contigs) and -1 <= r.next_rid < len(contigs)
             and r.pos >= -1 and r.next_pos >= -1, "record_out_of_bounds")
    _require(0 <= r.mapq <= 255 and -(2**31) <= r.tlen < 2**31, "malformed_output")
    if r.sequence is not None:
        _require(0 < len(r.sequence) <= MAX_READ_BASES
                 and set(r.sequence) <= set(b"=ACMGRSVTWYHKDBN"), "record_out_of_bounds")
    if r.quality is not None:
        _require(r.sequence is not None and len(r.quality) == len(r.sequence)
                 and all(33 <= x <= 126 for x in r.quality), "malformed_output")
    query = sum(n for n, op in r.cigar if op in "MIS=X")
    width = sum(n for n, op in r.cigar if op in "MDN=X")
    if r.cigar and r.sequence is not None:
        _require(query == len(r.sequence), "record_out_of_bounds")
    for i, (_, op) in enumerate(r.cigar):
        if op == "H":
            _require(i in (0, len(r.cigar) - 1), "malformed_output")
        if op == "S":
            _require(all(x[1] == "H" for x in r.cigar[:i])
                     or all(x[1] == "H" for x in r.cigar[i + 1:]), "malformed_output")
    if r.rid < 0:
        _require(r.pos == -1 and r.flag & 4, "record_out_of_bounds")
    else:
        _require(0 <= r.pos < contigs[r.rid][1], "record_out_of_bounds")
    if r.next_rid < 0:
        _require(r.next_pos == -1, "record_out_of_bounds")
    else:
        _require(0 <= r.next_pos < contigs[r.next_rid][1], "record_out_of_bounds")
    if not r.flag & 4:
        _require(r.rid >= 0 and r.cigar and r.pos + max(width, 1) <= contigs[r.rid][1],
                 "record_out_of_bounds")
    return r


def _parse_sam_record(line, contigs, contig_names):
    fields = line.split(b"\t")
    _require(len(fields) >= 11, "malformed_output")
    rid = -1 if fields[2] == b"*" else contig_names.get(fields[2], -2)
    next_rid = rid if fields[6] == b"=" else (-1 if fields[6] == b"*" else contig_names.get(fields[6], -2))
    record = _Record(
        fields[0], _integer(fields[1], 0, 65535), rid,
        _integer(fields[3], 0, 2**31 - 1, "record_out_of_bounds") - 1,
        _integer(fields[4], 0, 255), _cigar_ops(fields[5]), next_rid,
        _integer(fields[7], 0, 2**31 - 1, "record_out_of_bounds") - 1,
        _integer(fields[8], -(2**31), 2**31 - 1),
        None if fields[9] == b"*" else fields[9].upper(),
        None if fields[10] == b"*" else fields[10],
        _sam_tags(fields[11:]),
    )
    return _check_record(record, contigs)


def _record_hash(r):
    return hashlib.sha256(_canonical([
        r.qname.decode("ascii"), r.flag, r.rid, r.pos, r.mapq, r.cigar,
        r.next_rid, r.next_pos, r.tlen,
        None if r.sequence is None else r.sequence.decode("ascii"),
        None if r.quality is None else r.quality.decode("ascii"), r.tags,
    ])).digest()


class _BinaryReader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, n):
        _require(type(n) is int and 0 <= n <= len(self.data) - self.pos, "truncated_output")
        value = self.data[self.pos:self.pos + n]
        self.pos += n
        return value

    def unpack(self, fmt):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))

    def u32(self):
        return self.unpack("<I")[0]

    def u64(self):
        return self.unpack("<Q")[0]


class _BgzfReader:
    """One physical block at a time; retain only block offset metadata."""
    def __init__(self, path, deadline):
        self.path, self.deadline = Path(path), deadline
        self.stream = _open_read(path, MAX_BAM_BYTES, "malformed_output")
        self.identity = _fingerprint(os.fstat(self.stream.fileno()))
        self.buffer = b""
        self.buffer_pos = 0
        self.pos = self.decoded = self.compressed = 0
        self.blocks = {}
        self.eof = False

    def _block(self):
        _remaining(self.deadline)
        if self.eof:
            return False
        offset = self.compressed
        fixed = self.stream.read(12)
        _require(len(fixed) == 12, "truncated_output")
        _require(fixed[:4] == b"\x1f\x8b\x08\x04", "malformed_output")
        xlen = struct.unpack_from("<H", fixed, 10)[0]
        _require(6 <= xlen <= 65506, "malformed_output")
        extra = self.stream.read(xlen)
        _require(len(extra) == xlen, "truncated_output")
        j, sizes = 0, []
        while j < len(extra):
            _require(j + 4 <= len(extra), "truncated_output")
            tag = extra[j:j + 2]
            size = struct.unpack_from("<H", extra, j + 2)[0]
            j += 4
            _require(j + size <= len(extra), "truncated_output")
            if tag == b"BC":
                _require(size == 2, "malformed_output")
                sizes.append(struct.unpack_from("<H", extra, j)[0] + 1)
            j += size
        _require(len(sizes) == 1 and 12 + xlen + 8 <= sizes[0] <= 65536, "malformed_output")
        size = sizes[0]
        _require(offset + size <= self.identity[2], "truncated_output")
        tail = self.stream.read(size - 12 - xlen)
        _require(len(tail) == size - 12 - xlen, "truncated_output")
        inflater = zlib.decompressobj(-15)
        try:
            data = inflater.decompress(tail[:-8], 65537)
        except zlib.error:
            raise _Failure("output_checksum_error") from None
        _require(inflater.eof and not inflater.unused_data and not inflater.unconsumed_tail
                 and len(data) <= 65536, "output_checksum_error")
        crc, isize = struct.unpack("<II", tail[-8:])
        _require(isize == len(data) and crc == zlib.crc32(data) & 0xffffffff, "output_checksum_error")
        self.blocks[offset] = (self.decoded, len(data))
        self.compressed += size
        self.decoded += len(data)
        _require(self.decoded <= MAX_DECODED_BYTES, "resource_limit_exceeded")
        self.buffer, self.buffer_pos = data, 0
        if fixed + extra + tail == BGZF_EOF:
            _require(self.compressed == self.identity[2], "malformed_output")
            self.eof = True
        elif self.compressed == self.identity[2]:
            raise _Failure("truncated_output")
        return True

    def read(self, n, *, at_eof=False):
        _require(0 <= n <= MAX_RECORD_BYTES, "resource_limit_exceeded")
        parts, remaining = [], n
        while remaining:
            if self.buffer_pos == len(self.buffer):
                if not self._block():
                    break
                if not self.buffer and self.eof:
                    break
                continue
            count = min(remaining, len(self.buffer) - self.buffer_pos)
            parts.append(self.buffer[self.buffer_pos:self.buffer_pos + count])
            self.buffer_pos += count
            self.pos += count
            remaining -= count
        _require(remaining == 0 or at_eof and remaining == n and self.eof, "truncated_output")
        return b"".join(parts)

    def finish(self):
        _require(self.eof and self.compressed == self.identity[2]
                 and self.pos == self.decoded and self.buffer_pos == len(self.buffer), "truncated_output")
        _require(self.identity == _fingerprint(os.fstat(self.stream.fileno()))
                 and self.identity == _fingerprint(self.path.stat()), "input_identity_changed")

    def close(self):
        self.stream.close()


def _bam_tags(raw):
    r, tags, seen = _BinaryReader(raw), [], set()
    while r.pos < len(raw):
        tag, kind = r.take(2).decode("ascii"), r.take(1).decode("ascii")
        _require(_TAG.fullmatch(tag) and tag not in seen, "malformed_output")
        seen.add(tag)
        if kind in _ARRAY_TYPES:
            value = r.unpack(_ARRAY_TYPES[kind][0])[0]
            kind = "i"
        elif kind == "f":
            value = _float_value(r.unpack("<f")[0])
        elif kind == "A":
            value = r.take(1).decode("ascii")
            _require(33 <= ord(value) <= 126, "malformed_output")
        elif kind in ("Z", "H"):
            end = raw.find(b"\0", r.pos)
            _require(end >= r.pos, "truncated_output")
            value = r.take(end - r.pos).decode("ascii")
            r.take(1)
            if kind == "Z":
                _require(all(32 <= ord(ch) <= 126 for ch in value), "malformed_output")
            else:
                _require(len(value) % 2 == 0 and re.fullmatch("[0-9A-F]*", value), "malformed_output")
        elif kind == "B":
            subtype, count = r.take(1).decode("ascii"), r.u32()
            _require(subtype in _ARRAY_TYPES or subtype == "f", "malformed_output")
            fmt = "<f" if subtype == "f" else _ARRAY_TYPES[subtype][0]
            size = struct.calcsize(fmt)
            _require(count <= (len(raw) - r.pos) // size, "truncated_output")
            values = tuple(x[0] for x in struct.iter_unpack(fmt, r.take(count * size)))
            value = (subtype, tuple(_float_value(x) for x in values) if subtype == "f" else values)
        else:
            raise _Failure("malformed_output")
        tags.append((tag, kind, value))
    return tuple(sorted(tags))


def _bam_header(reader, contigs, read_group):
    _require(reader.read(4) == b"BAM\x01", "malformed_output")
    n = struct.unpack("<I", reader.read(4))[0]
    _require(n <= MAX_HEADER_BYTES, "resource_limit_exceeded")
    text = reader.read(n).rstrip(b"\0")
    _require(text.endswith(b"\n") and b"\0" not in text and b"\r" not in text and text.isascii(),
             "malformed_output")
    header = tuple(text.splitlines())
    _check_header(header, contigs, read_group, sorted_required=True, normalized=True)
    nref = struct.unpack("<I", reader.read(4))[0]
    _require(nref == len(contigs), "header_reference_mismatch")
    refs = []
    for _ in range(nref):
        size = struct.unpack("<I", reader.read(4))[0]
        _require(2 <= size <= 1024, "malformed_output")
        name = reader.read(size)
        _require(name[-1:] == b"\0" and b"\0" not in name[:-1], "malformed_output")
        length = struct.unpack("<I", reader.read(4))[0]
        refs.append((name[:-1].decode("ascii"), length))
    _require(tuple(refs) == tuple(contigs), "header_reference_mismatch")
    return header


def _reg2bin(begin, end):
    end -= 1
    for shift, offset in ((14, 4681), (17, 585), (20, 73), (23, 9), (26, 1)):
        if begin >> shift == end >> shift:
            return offset + (begin >> shift)
    return 0


def _containing_bins(begin, end):
    """Canonical record bin and containing ancestors, including HTSlib promotion."""
    end -= 1
    values = {0}
    for shift, offset in ((14, 4681), (17, 585), (20, 73), (23, 9), (26, 1)):
        if begin >> shift == end >> shift:
            values.add(offset + (begin >> shift))
    return values


def _bam_record(reader, contigs):
    start = reader.pos
    length_bytes = reader.read(4, at_eof=True)
    if not length_bytes:
        return None
    size = struct.unpack("<I", length_bytes)[0]
    _require(32 <= size <= MAX_RECORD_BYTES, "malformed_output")
    block = _BinaryReader(reader.read(size))
    rid, pos, lname, mapq, bin_id, ncigar, flag, lseq, nrid, npos, tlen = block.unpack("<iiBBHHHiiii")
    _require(1 <= lname <= 255 and 0 <= lseq <= MAX_READ_BASES, "record_out_of_bounds")
    name = block.take(lname)
    _require(name.endswith(b"\0") and b"\0" not in name[:-1], "malformed_output")
    operations = []
    for _ in range(ncigar):
        value = block.u32()
        _require((value & 15) <= 8 and value >> 4 > 0, "malformed_output")
        operations.append((value >> 4, "MIDNSHP=X"[value & 15]))
    packed = block.take((lseq + 1) // 2)
    sequence = bytes(b"=ACMGRSVTWYHKDBN"[(packed[i // 2] >> (4 if i % 2 == 0 else 0)) & 15]
                     for i in range(lseq)) if lseq else None
    qualities = block.take(lseq)
    if not qualities or all(q == 255 for q in qualities):
        quality = None
    else:
        _require(all(q <= 93 for q in qualities), "malformed_output")
        quality = bytes(q + 33 for q in qualities)
    tags = _bam_tags(block.take(len(block.data) - block.pos))
    cg = next((value for tag, kind, value in tags if tag == "CG" and kind == "B"), None)
    if cg is not None and len(operations) == 2 and operations[0] == (lseq, "S") and operations[1][1] == "N":
        _require(cg[0] == "I" and len(cg[1]) > 65535, "malformed_output")
        placeholder_span = operations[1][0]
        operations = []
        for value in cg[1]:
            _require((value & 15) <= 8 and value >> 4 > 0, "malformed_output")
            operations.append((value >> 4, "MIDNSHP=X"[value & 15]))
        _require(sum(n for n, op in operations if op in "MDN=X") == placeholder_span
                 and sum(n for n, op in operations if op in "MIS=X") == lseq, "record_out_of_bounds")
        tags = tuple(item for item in tags if item[0] != "CG")
    record = _check_record(_Record(name[:-1], flag, rid, pos, mapq, tuple(operations),
                                  nrid, npos, tlen, sequence, quality, tags), contigs)
    width = sum(n for n, op in record.cigar if op in "MDN=X") if not flag & 4 else 0
    width = max(1, width)
    _require(bin_id == _reg2bin(pos, pos + width), "malformed_output")
    return record, (start, reader.pos, rid, pos, width, flag, bin_id)


class _Accounting:
    def __init__(self, snapshot, read_group):
        self.snapshot, self.read_group = snapshot, read_group
        self.primary = Counter()
        self.mate_first, self.mate_second = Counter(), Counter()
        self.counts = dict(pairs=snapshot.pair_count, records=0, primary=0, primary_mapped=0,
                           primary_unmapped=0, secondary=0, supplementary=0,
                           secondary_and_supplementary=0, nonprimary_union=0, no_coordinate=0,
                           orientation_restored_multiplicity_equal_FASTQ=False)

    def add(self, r):
        c = self.counts
        c["records"] += 1
        c["no_coordinate"] += int(r.rid < 0)
        mate = 1 if r.flag & 64 else 2
        key = (r.qname, mate)
        _require(key in self.snapshot.source_reads, "primary_accounting_mismatch")
        _require(("RG", "Z", self.read_group["ID"]) in r.tags, "header_reference_mismatch")
        secondary, supplementary = bool(r.flag & 256), bool(r.flag & 2048)
        c["secondary"] += int(secondary)
        c["supplementary"] += int(supplementary)
        c["secondary_and_supplementary"] += int(secondary and supplementary)
        reverse = bool(r.flag & 16)
        seq = r.sequence.translate(_COMPLEMENT)[::-1] if reverse and r.sequence is not None else r.sequence
        qual = r.quality[::-1] if reverse and r.quality is not None else r.quality
        if secondary or supplementary:
            c["nonprimary_union"] += 1
            left_hard = r.cigar[0][0] if r.cigar and r.cigar[0][1] == "H" else 0
            right_hard = r.cigar[-1][0] if r.cigar and r.cigar[-1][1] == "H" else 0
            if reverse:
                left_hard, right_hard = right_hard, left_hard
            query = sum(n for n, op in r.cigar if op in "MIS=X")
            matched = False
            for original_seq, original_qual in self.snapshot.source_reads[key]:
                end = len(original_seq) - right_hard
                if r.cigar and query + left_hard + right_hard != len(original_seq):
                    continue
                if left_hard > end:
                    continue
                if seq is not None and seq != original_seq[left_hard:end]:
                    continue
                if qual is not None and qual != original_qual[left_hard:end]:
                    continue
                matched = True
                break
            _require(matched, "secondary_supplementary_accounting_mismatch")
            return
        c["primary"] += 1
        c["primary_unmapped" if r.flag & 4 else "primary_mapped"] += 1
        _require(seq is not None and qual is not None
                 and not any(op == "H" for _, op in r.cigar), "primary_accounting_mismatch")
        self.primary[(r.qname, mate, seq, qual)] += 1
        # An occurrence multiset, rather than a QNAME dictionary, retains aliases.
        own = (r.rid, r.pos, bool(r.flag & 16), bool(r.flag & 4))
        other = (r.next_rid, r.next_pos, bool(r.flag & 32), bool(r.flag & 8))
        if own[3]:
            own = own[:2] + (None, own[3])
        if other[3]:
            other = other[:2] + (None, other[3])
        token = (r.qname, own, other, bool(r.flag & 2), r.tlen)
        if mate == 1:
            self.mate_first[token] += 1
        else:
            mirror = (r.qname, other, own, bool(r.flag & 2), -r.tlen)
            self.mate_second[mirror] += 1

    def finish(self):
        c = self.counts
        _require(self.primary == self.snapshot.expected_reads
                 and c["primary"] == self.snapshot.pair_count * 2,
                 "primary_accounting_mismatch")
        _require(self.mate_first == self.mate_second, "primary_accounting_mismatch")
        _require(c["primary"] + c["nonprimary_union"] == c["records"]
                 and c["nonprimary_union"] == c["secondary"] + c["supplementary"] - c["secondary_and_supplementary"],
                 "secondary_supplementary_accounting_mismatch")
        c["orientation_restored_multiplicity_equal_FASTQ"] = True
        return c


def _validate_bai(path, contigs, blocks, spans, boundaries, no_coordinate, deadline):
    raw = _read_bounded(path, MAX_BAI_BYTES, "index_validation_failed")
    reader = _BinaryReader(raw)
    _require(reader.take(4) == b"BAI\x01" and reader.u32() == len(contigs), "index_validation_failed")
    by_ref = defaultdict(list)
    for item in spans:
        if item[2] >= 0:
            by_ref[item[2]].append(item)

    def virtual(value):
        compressed, inner = value >> 16, value & 65535
        _require(compressed in blocks and inner <= blocks[compressed][1], "index_validation_failed")
        absolute = blocks[compressed][0] + inner
        _require(absolute in boundaries, "index_validation_failed")
        return absolute

    for rid, (_, length) in enumerate(contigs):
        _remaining(deadline)
        nbin = reader.u32()
        _require(nbin <= 37451, "index_validation_failed")
        normal, metadata, seen = {}, None, set()
        for _ in range(nbin):
            bid, count = reader.u32(), reader.u32()
            _require(bid <= 37450 and bid != 37449 and bid not in seen
                     and count <= (len(raw) - reader.pos) // 16, "index_validation_failed")
            seen.add(bid)
            if bid == 37450:
                _require(count == 2, "index_validation_failed")
                begin, end, mapped, unmapped = (reader.u64() for _ in range(4))
                metadata = (virtual(begin), virtual(end), mapped, unmapped)
            else:
                chunks = []
                for _ in range(count):
                    begin, end = reader.u64(), reader.u64()
                    a, b = virtual(begin), virtual(end)
                    _require(begin < end and a < b, "index_validation_failed")
                    chunks.append((a, b))
                _require(chunks == sorted(chunks), "index_validation_failed")
                normal[bid] = chunks
        count = reader.u32()
        _require(count <= 2**17 and count <= (length + 16383) // 16384, "index_validation_failed")
        linear = [reader.u64() for _ in range(count)]
        linear_positions = [virtual(value) if value else None for value in linear]
        nonzero = [value for value in linear_positions if value is not None]
        _require(nonzero == sorted(nonzero), "index_validation_failed")
        placed = by_ref[rid]
        mapped = sum(not (item[5] & 4) for item in placed)
        unmapped = len(placed) - mapped
        chunks = [chunk for values in normal.values() for chunk in values]
        if placed:
            _require(metadata is not None and chunks and metadata[2:] == (mapped, unmapped),
                     "index_validation_failed")
            first, last = min(x[0] for x in placed), max(x[1] for x in placed)
            _require(metadata[:2] == (first, last)
                     and metadata[:2] == (min(x[0] for x in chunks), max(x[1] for x in chunks))
                     and all(first <= x <= last for x in nonzero), "index_validation_failed")
            for start, end, _, pos, width, flags, _ in placed:
                _remaining(deadline)
                matching = _containing_bins(pos, pos + width)
                _require(any(a <= start and end <= b for bid, values in normal.items()
                             if bid in matching for a, b in values), "index_validation_failed")
                if not flags & 4:
                    last_window = (pos + width - 1) >> 14
                    _require(last_window < len(linear), "index_validation_failed")
                    for window in range(pos >> 14, last_window + 1):
                        _require(linear_positions[window] is None or linear_positions[window] <= start,
                                 "index_validation_failed")
        else:
            _require(not normal and metadata is None and count == 0, "index_validation_failed")
    remaining = len(raw) - reader.pos
    _require(remaining in (0, 8), "index_validation_failed")
    if remaining:
        _require(reader.u64() == no_coordinate, "index_validation_failed")
    _require(reader.pos == len(raw), "index_validation_failed")


def _validate_alignment_outputs(snapshot, *, contigs, read_group, sam_path: Path,
                                bam_path: Path, bai_path: Path, decoded_path: Path,
                                indexed_path: Path, deadline: float) -> dict:
    _verify_snapshot(snapshot, deadline)
    _require(tuple(contigs) and len(dict(contigs)) == len(contigs)
             and all(type(name) is str and type(length) is int and 0 < length < 2**29
                     for name, length in contigs), "header_reference_mismatch")
    _require(read_group == _read_group(snapshot), "header_reference_mismatch")
    contig_names = {name.encode(): i for i, (name, _) in enumerate(contigs)}
    source = _iter_sam(sam_path, contigs, read_group, deadline, contig_names=contig_names)
    source_header = next(source)
    source_records = Counter(_record_hash(r) for r in source)
    indexed = _iter_sam(indexed_path, contigs, read_group, deadline, sorted_required=True,
                        contig_names=contig_names)
    indexed_header = next(indexed)
    indexed_records = Counter(_record_hash(r) for r in indexed)
    native = _iter_sam(decoded_path, contigs, read_group, deadline, sorted_required=True,
                       contig_names=contig_names)
    native_header = next(native)
    _require(indexed_header == native_header, "index_validation_failed")
    _require(_check_header(source_header, contigs, read_group, sorted_required=False, normalized=True)
             == _check_header(native_header, contigs, read_group, sorted_required=True, normalized=True),
             "header_reference_mismatch")
    physical = _BgzfReader(bam_path, deadline)
    spans, full_records, accounting = [], Counter(), _Accounting(snapshot, read_group)
    try:
        header = _bam_header(physical, contigs, read_group)
        _require(header == native_header, "header_reference_mismatch")
        boundaries = {physical.pos}
        previous = None
        while True:
            _remaining(deadline)
            item = _bam_record(physical, contigs)
            if item is None:
                break
            record, span = item
            decoded = next(native, None)
            _require(decoded is not None and record == decoded, "malformed_output")
            key = (record.rid if record.rid >= 0 else len(contigs), record.pos)
            _require(previous is None or previous <= key, "coordinate_order_mismatch")
            previous = key
            spans.append(span)
            boundaries.update(span[:2])
            full_records[_record_hash(record)] += 1
            accounting.add(record)
        _require(next(native, None) is None, "malformed_output")
        physical.finish()
        result = accounting.finish()
        _require(full_records == source_records, "primary_accounting_mismatch")
        _require(full_records == indexed_records, "index_validation_failed")
        try:
            _validate_bai(bai_path, contigs, physical.blocks, spans, boundaries,
                          result["no_coordinate"], deadline)
        except _Failure as error:
            if error.code in ("deadline_exceeded", "resource_limit_exceeded"):
                raise
            raise _Failure("index_validation_failed") from None
        _verify_snapshot(snapshot, deadline)
        return result
    finally:
        physical.close()
        native.close()


def _bind_output(path, maximum, deadline):
    with _open_read(path, maximum, "promotion_failed") as stream:
        before = _fingerprint(os.fstat(stream.fileno()))
        digest, count = hashlib.sha256(), 0
        while True:
            _remaining(deadline)
            part = stream.read(1048576)
            if not part:
                break
            digest.update(part)
            count += len(part)
            _require(count <= maximum, "promotion_failed")
        after = _fingerprint(os.fstat(stream.fileno()))
    _require(before == after == _fingerprint(path.lstat()) and count == before[2],
             "promotion_failed")
    return {"sha256": digest.hexdigest(), "size_bytes": count, "fingerprint": before}


def _verify_output_bindings(bindings, deadline):
    for path, expected in bindings.items():
        observed = _verify_file_identity(path, expected["sha256"], expected["size_bytes"],
                                         deadline, "promotion_failed")
        _require(observed == expected, "promotion_failed")


def _final_identity_readback(bindings, observed):
    for path, expected in bindings.items():
        _require(_fingerprint(path.lstat()) == expected["fingerprint"], "promotion_failed")
    _index_inventory(observed.index_prefix)
    for path, expected, code in observed.identities:
        _require(_fingerprint(path.lstat()) == expected["fingerprint"], code)


def _parent_ids(accepted):
    mates = accepted["input_mates"]
    return [
        "fastq-r1:sha256:" + mates["r1"]["content_sha256"],
        "fastq-r2:sha256:" + mates["r2"]["content_sha256"],
        "input-profile:sha256:" + accepted["input_profile_sha256"],
        "parameters:sha256:" + accepted["parameter_profile_sha256"],
        "reference-binding:sha256:" + _sha(_canonical(accepted["reference_binding"])),
        "executor-binding:sha256:" + _sha(_canonical(accepted["executor_binding"])),
    ]


def _artifact_manifest(kind, content, parents):
    digest = _sha(content)
    return {"schema_version": "1.0.0", "artifact_id": kind.lower() + ":sha256:" + digest,
            "artifact_kind": kind, "media_type": "application/octet-stream",
            "sha256": digest, "size_bytes": len(content),
            "producer_execution_profile_id": EXECUTION_PROFILE_ID,
            "parent_artifact_ids": parents}


def _promote(bam_bytes: bytes, bai_bytes: bytes, accepted_alignment: dict) -> PairedReadAlignmentResult:
    """Create the immutable pair only after both identities and the closed schema pass."""
    _require(type(bam_bytes) is bytes and 0 < len(bam_bytes) <= MAX_BAM_BYTES
             and type(bai_bytes) is bytes and 0 < len(bai_bytes) <= MAX_BAI_BYTES, "promotion_failed")
    accepted = json.loads(_capture_json(accepted_alignment, MAX_RESULT_BYTES, "output_schema_failed"))
    payload = _payload(accepted)
    _validate_export(payload)
    profiles = _load_profiles()
    reference, executor = profiles["reference"], profiles["executor"]
    expected_reference = {
        "profile_id": REFERENCE_PROFILE_ID, "profile_sha256": PROFILE_FILES["reference"][1],
        "fasta_sha256": reference["fasta"]["sha256"], "fai_sha256": reference["fai"]["sha256"],
        "dictionary_sha256": reference["dictionary"]["sha256"],
        "index_files": {suffix: reference["index_files"][suffix]["sha256"] for suffix in INDEX_SUFFIXES},
        "contig_dictionary_sha256": reference["contig_dictionary_sha256"],
    }
    expected_executor = {
        "profile_id": EXECUTOR_PROFILE_ID, "profile_sha256": PROFILE_FILES["executor"][1],
        "bwa_sha256": executor["executors"]["bwa"]["sha256"],
        "samtools_sha256": executor["executors"]["samtools"]["sha256"],
        "runtime_libraries_sha256": executor["runtime_libraries_sha256"],
    }
    _require(accepted["reference_binding"] == expected_reference
             and accepted["executor_binding"] == expected_executor
             and accepted["parameter_profile_sha256"] == _sha(_canonical(profiles["alignment"]["parameters"]))
             and accepted["assessments"] == _ASSESSMENTS, "promotion_failed")
    counts = accepted["accounting"]
    _require(counts["pairs"] == accepted["pair_count"]
             and counts["primary"] == 2 * counts["pairs"]
             and counts["primary_mapped"] + counts["primary_unmapped"] == counts["primary"]
             and counts["nonprimary_union"] == counts["secondary"] + counts["supplementary"]
                 - counts["secondary_and_supplementary"]
             and counts["secondary_and_supplementary"] <= min(counts["secondary"], counts["supplementary"])
             and counts["records"] == counts["primary"] + counts["nonprimary_union"]
             and counts["no_coordinate"] <= counts["records"]
             and counts["orientation_restored_multiplicity_equal_FASTQ"] is True, "promotion_failed")
    bam_manifest = _artifact_manifest("BAM", bam_bytes, _parent_ids(accepted))
    bai_manifest = _artifact_manifest("BAI", bai_bytes, [bam_manifest["artifact_id"]])
    _require(accepted["bam_artifact"] == bam_manifest and accepted["bai_artifact"] == bai_manifest,
             "promotion_failed")
    for manifest, data in ((bam_manifest, bam_bytes), (bai_manifest, bai_bytes)):
        _require(verify_artifact_bytes(manifest, data).passed, "promotion_failed")
    artifacts = tuple(PreparedAlignmentArtifact(
        manifest["artifact_id"], manifest["artifact_kind"], manifest["media_type"], data,
        manifest["sha256"], manifest["size_bytes"], manifest["producer_execution_profile_id"],
        tuple(manifest["parent_artifact_ids"]))
        for manifest, data in ((bam_manifest, bam_bytes), (bai_manifest, bai_bytes)))
    return PairedReadAlignmentResult(_canonical(payload), artifacts)


def _workspace(resources):
    _require(type(resources) is AlignmentResources, "resource_unavailable")
    try:
        root = Path(resources.private_work_root)
        st = root.lstat()
        _require(root.is_absolute() and root.resolve() == root and stat.S_ISDIR(st.st_mode)
                 and st.st_uid == os.geteuid() and not st.st_mode & 0o022, "resource_unavailable")
        result = Path(tempfile.mkdtemp(prefix="paired-alignment-", dir=root))
        os.chmod(result, 0o700)
        _require(result.resolve() == result and result.stat().st_uid == os.geteuid(),
                 "resource_unavailable")
        return result
    except (OSError, TypeError, ValueError):
        raise _Failure("resource_unavailable") from None


def _save_terminal(workspace, result, started, *, replace=False):
    """Private evidence is retained for both refusal and successful promotion."""
    _require(not replace or not result.is_valid, "promotion_failed")
    _private_json(workspace / "result.json", result.to_dict(), replace=replace)
    _private_json(workspace / "terminal.json", {
        "capability_id": CAPABILITY_ID,
        "status": result.to_dict()["canonical_status"],
        "wall_seconds": round(time.monotonic() - started, 6),
        "result_sha256": _sha(result._canonical_json),
        "artifact_ids": [artifact.artifact_id for artifact in result.artifacts],
    }, replace=replace)


def align_paired_fastq(
    r1: bytes, r2: bytes, input_profile: dict[str, object], *,
    execution_profile: dict[str, object],
    resources: AlignmentResources,
    timeout_seconds: int = 1800,
) -> PairedReadAlignmentResult:
    """Run the single explicitly selected local profile.

    Input bytes are validated while producing owned snapshots; native alignment
    consumes sealed copies of those exact bytes. Resource acquisition, index
    construction, alternate executors and default-route activation are absent.
    Private command/SAM evidence remains in a unique caller-owned work directory.
    """
    started = time.monotonic()
    workspace, snapshot, observed = None, None, None
    result = None
    try:
        _require(type(timeout_seconds) is int and 1 <= timeout_seconds <= 1800, "deadline_exceeded")
        deadline = started + timeout_seconds
        _remaining(deadline)
        profiles = _load_profiles()
        execution_json = _capture_json(execution_profile, 4096, "unsupported_execution_profile")
        _require(json.loads(execution_json) == profiles["execution"], "unsupported_execution_profile")
        workspace = _workspace(resources)
        snapshot = _snapshot_inputs(r1, r2, input_profile, workspace, deadline)
        read_group = _read_group(snapshot)
        observed = _observe_resources(resources, workspace, deadline, profiles)
        _verify_snapshot(snapshot, deadline)
        parameters = profiles["alignment"]["parameters"]
        bwa_fd, samtools_fd = observed.executable_fds
        bwa, samtools = f"/proc/self/fd/{bwa_fd}", f"/proc/self/fd/{samtools_fd}"
        read_group_argument = (r"@RG\tID:" + read_group["ID"] + r"\tSM:" + read_group["SM"]
                               + r"\tPL:" + read_group["PL"])
        native_sam = workspace / "native.sam"
        canonical_sam = workspace / "canonical.sam"
        bam_path, bai_path = workspace / "alignment.bam", workspace / "alignment.bam.bai"
        decoded_path, indexed_path = workspace / "decoded.sam", workspace / "indexed.sam"
        _command(
            profiles, workspace, "alignment",
            [bwa, *parameters["bwa_mem"], "-R", read_group_argument,
             str(observed.index_prefix), *(f"/proc/self/fd/{fd}" for fd in snapshot.fds)],
            deadline, "alignment", stdout_path=native_sam, output=MAX_SAM_BYTES,
            pass_fds=(bwa_fd, *snapshot.fds),
        )
        _verify_snapshot(snapshot, deadline)
        _normalize_sam_header(native_sam, canonical_sam, contigs=observed.contigs,
                              read_group=read_group, deadline=deadline)
        _command(
            profiles, workspace, "sort",
            [samtools, *parameters["samtools_sort"], "-T", str(workspace / "sort-tmp"),
             "-o", str(bam_path), str(canonical_sam)], deadline, "sort",
            pass_fds=(samtools_fd,),
        )
        _command(
            profiles, workspace, "index",
            [samtools, *parameters["samtools_index"], str(bam_path), str(bai_path)],
            deadline, "decode", file_bytes=MAX_BAI_BYTES, pass_fds=(samtools_fd,),
        )
        _command(
            profiles, workspace, "decode",
            [samtools, *parameters["samtools_view"], str(bam_path)], deadline, "decode",
            stdout_path=decoded_path, output=MAX_SAM_BYTES, pass_fds=(samtools_fd,),
        )
        _command(
            profiles, workspace, "indexed",
            [samtools, *parameters["samtools_indexed_view"], str(bam_path), str(bai_path),
             *(name for name, _ in observed.contigs), "*"], deadline, "decode",
            stdout_path=indexed_path, output=MAX_SAM_BYTES, pass_fds=(samtools_fd,),
        )
        bindings = {path: _bind_output(path, maximum, deadline) for path, maximum in (
            (native_sam, MAX_SAM_BYTES), (canonical_sam, MAX_SAM_BYTES),
            (decoded_path, MAX_SAM_BYTES), (indexed_path, MAX_SAM_BYTES),
            (bam_path, MAX_BAM_BYTES), (bai_path, MAX_BAI_BYTES))}
        _private_json(workspace / "validated-output-identities.json",
                      {path.name: row for path, row in bindings.items()})
        accounting = _validate_alignment_outputs(
            snapshot, contigs=observed.contigs, read_group=read_group,
            sam_path=canonical_sam, bam_path=bam_path, bai_path=bai_path,
            decoded_path=decoded_path, indexed_path=indexed_path, deadline=deadline,
        )
        _verify_resources(observed, deadline)
        _verify_snapshot(snapshot, deadline)
        _capacity(workspace, profiles)
        _verify_output_bindings(bindings, deadline)
        bam_bytes = _read_bounded(bam_path, MAX_BAM_BYTES)
        bai_bytes = _read_bounded(bai_path, MAX_BAI_BYTES)
        _require(_sha(bam_bytes) == bindings[bam_path]["sha256"]
                 and _sha(bai_bytes) == bindings[bai_path]["sha256"], "promotion_failed")
        _final_identity_readback(bindings, observed)
        _remaining(deadline)
        profile = json.loads(snapshot.profile_json)
        declared = {key: profile[key] for key in (
            "declared_case_id", "declared_sample_id", "declared_specimen_id",
            "declared_read_group_id", "declared_run")}
        accepted = {
            "input_profile_sha256": snapshot.input_profile_sha256,
            "input_mates": {mate: {"content_sha256": row["sha256"], "size_bytes": row["size_bytes"]}
                            for mate, row in zip(("r1", "r2"), snapshot.identities)},
            "pair_count": snapshot.pair_count,
            "reference_binding": observed.reference_binding,
            "executor_binding": observed.executor_binding,
            "parameter_profile_sha256": _sha(_canonical(parameters)),
            "declared_identity_binding_sha256": _sha(_canonical(declared)),
            "accounting": accounting, "assessments": dict(_ASSESSMENTS),
        }
        accepted["bam_artifact"] = _artifact_manifest("BAM", bam_bytes, _parent_ids(accepted))
        accepted["bai_artifact"] = _artifact_manifest("BAI", bai_bytes, [accepted["bam_artifact"]["artifact_id"]])
        result = _promote(bam_bytes, bai_bytes, accepted)
        _final_identity_readback(bindings, observed)
        _remaining(deadline)
        _save_terminal(workspace, result, started)
        _require(_owned_size(workspace) <= profiles["alignment"]["resource_budget"]["mutable_output_bytes"],
                 "resource_limit_exceeded")
        _remaining(deadline)
        return result
    except _Failure as error:
        result = _refusal(error.code)
    except MemoryError:
        result = _refusal("resource_limit_exceeded")
    except (OSError, TypeError, ValueError, KeyError, IndexError, OverflowError, struct.error):
        result = _refusal("malformed_output")
    finally:
        if snapshot is not None:
            snapshot.close()
        if observed is not None:
            observed.close()
    if workspace is not None:
        try:
            replace = (workspace / "result.json").exists() or (workspace / "terminal.json").exists()
            if replace:
                _private_json(workspace / "promotion-refusal.json", result.to_dict())
            _save_terminal(workspace, result, started, replace=replace)
        except (OSError, _Failure):
            # Public refusal never becomes success because private storage failed.
            result = _refusal("promotion_failed")
    return result
