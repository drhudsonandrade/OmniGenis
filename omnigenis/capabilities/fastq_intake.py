"""Opt-in structural intake for a bounded, declared paired FASTQ profile.

The supplied binary streams are read from their current positions without being
opened, repositioned or closed. Acceptance binds complete uncompressed contents
and declared metadata; it does not establish biological or instrument identity.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import re
from typing import BinaryIO


PROFILE_ID = "paired-fastq-illumina-non-umi-phred33-v1"
MAX_BYTES_PER_MATE = 67108864
MAX_PAIRS = 100000
MAX_PHYSICAL_LINE_BYTES = 4096
MAX_HEADER_CONTENT_BYTES = 512
MAX_READ_BASES = 1000
MAX_STREAM_READ_BYTES = 65536
MAX_PROFILE_JSON_BYTES = 4096
MAX_RESULT_JSON_BYTES = 16384
_MAX_INTEGER = 2147483647
_MIN_COORDINATE = -2147483648

_PROFILE_CONSTANTS = {
    "schema_version": "1.0.0",
    "profile_id": PROFILE_ID,
    "transport": "UNCOMPRESSED",
    "layout": "PAIRED_END",
    "quality_encoding": "PHRED33",
}
_DECLARED_IDS = (
    "declared_case_id", "declared_sample_id",
    "declared_specimen_id", "declared_read_group_id",
)
_PROFILE_FIELDS = frozenset(_PROFILE_CONSTANTS) | frozenset(_DECLARED_IDS) | {
    "declared_run", "r1", "r2",
}
_RUN_FIELDS = frozenset(("instrument", "run", "flowcell", "lane"))
_MATE_FIELDS = frozenset((
    "schema_version", "input_id", "input_kind",
    "media_type", "content_sha256", "size_bytes",
))
_MATE_CONSTANTS = {
    "schema_version": "1.0.0", "input_kind": "FASTQ", "media_type": "text/plain",
}
_OPAQUE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z")
_RUN_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_HEADER_TOKEN = re.compile(rb"[A-Za-z0-9_-]{1,64}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SEQUENCE = re.compile(rb"[ACGTN]+\Z")
_QUALITY = re.compile(rb"[!-~]+\Z")
_INDEX_SEQUENCE = re.compile(rb"[ACGTN]{1,32}(?:\+[ACGTN]{1,32})?\Z")
_COMPRESSION_SIGNATURES = (
    b"\x1f\x8b", b"BZh", b"PK\x03\x04", b"PK\x05\x06",
    b"PK\x07\x08", b"\xfd7zXZ\x00", b"\x28\xb5\x2f\xfd",
)
_DIAGNOSTIC_CODES = frozenset((
    "invalid_profile", "invalid_stream", "same_stream", "unsupported_transport",
    "stream_read_error", "stream_protocol_error", "input_limit_exceeded",
    "line_limit_exceeded", "header_limit_exceeded", "unterminated_line",
    "invalid_line_ending", "non_ascii_input", "truncated_record", "invalid_header",
    "declaration_mismatch", "invalid_sequence", "invalid_separator",
    "invalid_quality", "read_length_exceeded", "empty_input",
    "pair_limit_exceeded", "pair_count_mismatch", "mate_mismatch",
    "pair_identity_mismatch", "size_mismatch", "digest_mismatch",
    "output_limit_exceeded",
))
_ASSESSMENTS = (
    ("biological_sample_identity", "NOT_ASSESSED"),
    ("declared_run_consistency", "PASS"),
    ("downstream_processing", "NOT_ASSESSED"),
    ("format_conformance", "PASS"),
    ("full_stream_integrity", "PASS"),
    ("instrument_authenticity", "NOT_VERIFIED"),
    ("mate_correspondence", "PASS"),
    ("preprocessing_history", "NOT_VERIFIED"),
    ("quality_encoding_provenance", "DECLARED"),
)
_PROVENANCE_REFS = (
    "https://support.illumina.com/content/dam/illumina-support/documents/"
    "documentation/software_documentation/bcl2fastq/"
    "bcl2fastq2-v2-20-software-guide-15051736-03.pdf",
    "https://pmc.ncbi.nlm.nih.gov/articles/PMC2847217/",
    "https://help.basespace.illumina.com/files-used-by-basespace/fastq-files",
)
_LIMITATIONS = (
    "Structural validation is limited to the declared uncompressed, non-UMI, "
    "four-line paired FASTQ profile; full upstream compatibility is not established.",
    "PHRED33 and run metadata are declarations. Overlapping quality alphabets "
    "cannot establish encoding provenance or instrument authenticity.",
    "Ordinal mate correspondence does not establish biological sample identity, "
    "demultiplexing correctness, orientation or global identifier uniqueness.",
    "Input hashes describe the supplied uncompressed contents from each stream's "
    "current position. No decompression or preprocessing history is verified.",
    "No trimming, filtering, alignment, reference concordance, coverage, callability, "
    "variant calling, interpretation, reporting or clinical assessment is performed.",
    "Only identical stream objects are rejected as aliases. Distinct stream "
    "wrappers do not prove distinct backing storage.",
    "The caller must enforce a deadline for blocking stream reads. Results contain "
    "no raw reads or identifiers; a declaration digest is not anonymization.",
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    ).encode("utf-8")


def _integer_in_range(value: object, lower: int, upper: int) -> bool:
    return type(value) is int and lower <= value <= upper


def _valid_digest(value: object) -> bool:
    return type(value) is str and len(value) == 64 and _SHA256.fullmatch(value) is not None


@dataclass(frozen=True)
class _AcceptedPair:
    input_profile_sha256: str
    pair_count: int
    r1_sha256: str
    r1_size: int
    r2_sha256: str
    r2_size: int

    def __post_init__(self) -> None:
        if not (
            all(_valid_digest(value) for value in (
                self.input_profile_sha256, self.r1_sha256, self.r2_sha256,
            ))
            and _integer_in_range(self.pair_count, 1, MAX_PAIRS)
            and _integer_in_range(self.r1_size, 1, MAX_BYTES_PER_MATE)
            and _integer_in_range(self.r2_size, 1, MAX_BYTES_PER_MATE)
        ):
            raise ValueError("Invalid accepted-pair shape")

    def to_dict(self) -> dict[str, object]:
        return {
            "input_profile_sha256": self.input_profile_sha256,
            "layout": "PAIRED_END",
            "quality_encoding": "PHRED33",
            "pair_count": self.pair_count,
            "r1": {"content_sha256": self.r1_sha256, "size_bytes": self.r1_size},
            "r2": {"content_sha256": self.r2_sha256, "size_bytes": self.r2_size},
            "assessments": dict(_ASSESSMENTS),
        }


@dataclass(frozen=True)
class _Diagnostic:
    code: str
    mate: str | None = None
    record_index: int | None = None

    def __post_init__(self) -> None:
        if not (
            type(self.code) is str and self.code in _DIAGNOSTIC_CODES
            and (self.mate is None or type(self.mate) is str and self.mate in ("R1", "R2"))
            and (self.record_index is None
                 or _integer_in_range(self.record_index, 1, MAX_PAIRS + 1))
        ):
            raise ValueError("Invalid diagnostic shape")

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code, "mate": self.mate, "record_index": self.record_index}


@dataclass(frozen=True)
class FastqIntakeResult:
    """Immutable bounded outcome; each export is a detached JSON-compatible value."""

    _accepted_pair: _AcceptedPair | None
    _diagnostic: _Diagnostic | None

    def __post_init__(self) -> None:
        accepted = type(self._accepted_pair) is _AcceptedPair and self._diagnostic is None
        refused = self._accepted_pair is None and type(self._diagnostic) is _Diagnostic
        if not (accepted or refused):
            raise ValueError("Exactly one intake outcome is required")

    @property
    def is_valid(self) -> bool:
        return self._accepted_pair is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "capability_id": "fastq-intake",
            "capability_version": "1.0.0",
            "canonical_status": "VALID" if self.is_valid else "INVALID",
            "canonical_payload": {
                "schema_version": "1.0.0",
                "profile_id": PROFILE_ID,
                "accepted_pair": (
                    self._accepted_pair.to_dict() if self._accepted_pair is not None else None
                ),
                "diagnostics": (
                    [self._diagnostic.to_dict()] if self._diagnostic is not None else []
                ),
            },
            "availability": "AVAILABLE",
            "limitations": list(_LIMITATIONS),
            "executor_id": "omnigenis.python-stdlib",
            "executor_version": "1.0.0",
            "adapter_version": None,
            "model_id": None,
            "resource_release": None,
            "reference_bundle": None,
            "execution_profile": PROFILE_ID,
            "raw_artifact_refs": [],
            "provenance_refs": list(_PROVENANCE_REFS),
        }


class _Refused(Exception):
    def __init__(
        self, code: str, mate: str | None = None, record_index: int | None = None,
    ) -> None:
        self.diagnostic = _Diagnostic(code, mate, record_index)
        super().__init__(code)


@dataclass(frozen=True)
class _Profile:
    digest: str
    instrument: bytes
    run: int
    flowcell: bytes
    lane: int
    r1_sha256: str
    r1_size: int
    r2_sha256: str
    r2_size: int


def _closed_copy(value: object, fields: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict or len(value) != len(fields):
        raise _Refused("invalid_profile")
    copied = value.copy()
    if any(type(key) is not str for key in copied) or set(copied) != fields:
        raise _Refused("invalid_profile")
    return copied


def _profile_string(value: object, pattern: re.Pattern[str], limit: int) -> str:
    if type(value) is not str or not 1 <= len(value) <= limit or pattern.fullmatch(value) is None:
        raise _Refused("invalid_profile")
    return value


def _profile_integer(value: object, lower: int, upper: int) -> int:
    if not _integer_in_range(value, lower, upper):
        raise _Refused("invalid_profile")
    return value


def _profile_constants(values: dict[str, object], constants: dict[str, str]) -> None:
    for key, expected in constants.items():
        if type(values[key]) is not str or values[key] != expected:
            raise _Refused("invalid_profile")


def _snapshot_profile(input_profile: object) -> _Profile:
    snapshot = _closed_copy(input_profile, _PROFILE_FIELDS)
    _profile_constants(snapshot, _PROFILE_CONSTANTS)
    for key in _DECLARED_IDS:
        snapshot[key] = _profile_string(snapshot[key], _OPAQUE_ID, 64)

    run = _closed_copy(snapshot["declared_run"], _RUN_FIELDS)
    run["instrument"] = _profile_string(run["instrument"], _RUN_TOKEN, 64)
    run["flowcell"] = _profile_string(run["flowcell"], _RUN_TOKEN, 64)
    run["run"] = _profile_integer(run["run"], 1, _MAX_INTEGER)
    run["lane"] = _profile_integer(run["lane"], 1, 999)
    snapshot["declared_run"] = run

    for key in ("r1", "r2"):
        mate = _closed_copy(snapshot[key], _MATE_FIELDS)
        _profile_constants(mate, _MATE_CONSTANTS)
        mate["input_id"] = _profile_string(mate["input_id"], _OPAQUE_ID, 64)
        if not _valid_digest(mate["content_sha256"]):
            raise _Refused("invalid_profile")
        mate["size_bytes"] = _profile_integer(mate["size_bytes"], 0, MAX_BYTES_PER_MATE)
        snapshot[key] = mate
    left, right = snapshot["r1"], snapshot["r2"]
    if left["input_id"] == right["input_id"]:
        raise _Refused("invalid_profile")

    serialized = _canonical_bytes(snapshot)
    if len(serialized) > MAX_PROFILE_JSON_BYTES:
        raise _Refused("invalid_profile")
    return _Profile(
        hashlib.sha256(serialized).hexdigest(),
        run["instrument"].encode("ascii"), run["run"],
        run["flowcell"].encode("ascii"), run["lane"],
        left["content_sha256"], left["size_bytes"],
        right["content_sha256"], right["size_bytes"],
    )


def _read_method(stream: object, mate: str) -> Callable[[int], bytes]:
    try:
        method = getattr(stream, "read")
    except Exception:
        raise _Refused("invalid_stream", mate) from None
    if not callable(method):
        raise _Refused("invalid_stream", mate)
    return method


class _Reader:
    """Bounded lookahead with whole-supplied-stream identity and explicit EOF."""

    def __init__(self, read: Callable[[int], bytes], mate: str, expected_size: int) -> None:
        self._read = read
        self.mate = mate
        self.expected_size = expected_size
        self.size = 0
        self.hasher = hashlib.sha256()
        self.eof = False
        self._buffer = bytearray()
        self._offset = 0
        self._prefix = b""
        self._transport_checked = False

    def _fill(self, record_index: int) -> None:
        # One extra byte distinguishes exact-size EOF from an overflow.
        requested = min(
            MAX_STREAM_READ_BYTES,
            MAX_BYTES_PER_MATE + 1 - self.size,
            self.expected_size + 1 - self.size,
        )
        try:
            chunk = self._read(requested)
        except Exception:
            raise _Refused("stream_read_error", self.mate, record_index) from None
        if not issubclass(type(chunk), bytes):
            raise _Refused("stream_protocol_error", self.mate, record_index)
        # Read the immutable byte buffer without trusting subclass length overrides.
        try:
            view = memoryview(chunk)
        except TypeError:
            raise _Refused("stream_protocol_error", self.mate, record_index) from None
        if view.nbytes > requested:
            raise _Refused("stream_protocol_error", self.mate, record_index)
        if not view.nbytes:
            self.eof = True
            return
        data = view.tobytes()
        self.size += len(data)
        if self.size > MAX_BYTES_PER_MATE:
            raise _Refused("input_limit_exceeded", self.mate, record_index)
        if self.size > self.expected_size:
            raise _Refused("size_mismatch", self.mate, record_index)
        self.hasher.update(data)

        if not self._transport_checked:
            self._prefix += data[:6 - len(self._prefix)]
            if any(self._prefix.startswith(signature) for signature in _COMPRESSION_SIGNATURES):
                raise _Refused("unsupported_transport", self.mate, record_index)
            self._transport_checked = not any(
                signature.startswith(self._prefix) for signature in _COMPRESSION_SIGNATURES
            )
        if self._offset:
            del self._buffer[:self._offset]
            self._offset = 0
        self._buffer.extend(data)

    def line(self, record_index: int) -> bytes | None:
        while True:
            end = self._buffer.find(b"\n", self._offset)
            if end != -1:
                if end + 1 - self._offset > MAX_PHYSICAL_LINE_BYTES:
                    raise _Refused("line_limit_exceeded", self.mate, record_index)
                raw = bytes(self._buffer[self._offset:end + 1])
                self._offset = end + 1
                content = raw[:-2] if raw.endswith(b"\r\n") else raw[:-1]
                if b"\r" in content:
                    raise _Refused("invalid_line_ending", self.mate, record_index)
                if not content.isascii():
                    raise _Refused("non_ascii_input", self.mate, record_index)
                return content
            remaining = len(self._buffer) - self._offset
            if remaining >= MAX_PHYSICAL_LINE_BYTES:
                raise _Refused("line_limit_exceeded", self.mate, record_index)
            if self.eof:
                if remaining:
                    raise _Refused("unterminated_line", self.mate, record_index)
                return None
            self._fill(record_index)


def _header_integer(
    token: bytes, lower: int, upper: int, mate: str, record_index: int,
    *, signed: bool = False,
) -> int:
    magnitude = token[1:] if signed and token.startswith(b"-") else token
    if not 1 <= len(magnitude) <= 10 or not magnitude.isdigit():
        raise _Refused("invalid_header", mate, record_index)
    number = int(token)
    if not lower <= number <= upper:
        raise _Refused("invalid_header", mate, record_index)
    return number


def _header(
    line: bytes, profile: _Profile, mate: str, record_index: int,
) -> tuple[bytes, ...]:
    if len(line) > MAX_HEADER_CONTENT_BYTES:
        raise _Refused("header_limit_exceeded", mate, record_index)
    if not line.startswith(b"@"):
        raise _Refused("invalid_header", mate, record_index)
    groups = line[1:].split(b" ")
    if len(groups) != 2:
        raise _Refused("invalid_header", mate, record_index)
    cluster, fields = groups[0].split(b":"), groups[1].split(b":")
    if len(cluster) != 7 or len(fields) != 4:
        raise _Refused("invalid_header", mate, record_index)
    instrument, run_token, flowcell, lane_token, tile, x, y = cluster
    if _HEADER_TOKEN.fullmatch(instrument) is None or _HEADER_TOKEN.fullmatch(flowcell) is None:
        raise _Refused("invalid_header", mate, record_index)
    run = _header_integer(run_token, 1, _MAX_INTEGER, mate, record_index)
    lane = _header_integer(lane_token, 1, 999, mate, record_index)
    _header_integer(tile, 1, _MAX_INTEGER, mate, record_index)
    _header_integer(x, _MIN_COORDINATE, _MAX_INTEGER, mate, record_index, signed=True)
    _header_integer(y, _MIN_COORDINATE, _MAX_INTEGER, mate, record_index, signed=True)
    read, filtered, control, index = fields
    if read not in (b"1", b"2") or filtered not in (b"Y", b"N") or control != b"0":
        raise _Refused("invalid_header", mate, record_index)
    if not 1 <= len(index) <= 65:
        raise _Refused("invalid_header", mate, record_index)
    if index.isdigit():
        if int(index) > _MAX_INTEGER:
            raise _Refused("invalid_header", mate, record_index)
    elif _INDEX_SEQUENCE.fullmatch(index) is None:
        raise _Refused("invalid_header", mate, record_index)
    if read != (b"1" if mate == "R1" else b"2"):
        raise _Refused("mate_mismatch", mate, record_index)
    if (instrument, run, flowcell, lane) != (
        profile.instrument, profile.run, profile.flowcell, profile.lane,
    ):
        raise _Refused("declaration_mismatch", mate, record_index)
    # Keep spelling for correspondence; numeric normalization is only for declarations.
    return tuple(cluster) + (filtered, control, index)


def _record(reader: _Reader, profile: _Profile, record_index: int) -> tuple[bytes, ...] | None:
    header = reader.line(record_index)
    if header is None:
        return None
    if record_index > MAX_PAIRS:
        raise _Refused("pair_limit_exceeded", reader.mate, record_index)
    identity = _header(header, profile, reader.mate, record_index)
    sequence = reader.line(record_index)
    if sequence is None:
        raise _Refused("truncated_record", reader.mate, record_index)
    if len(sequence) > MAX_READ_BASES:
        raise _Refused("read_length_exceeded", reader.mate, record_index)
    if not sequence or _SEQUENCE.fullmatch(sequence) is None:
        raise _Refused("invalid_sequence", reader.mate, record_index)
    separator = reader.line(record_index)
    if separator is None:
        raise _Refused("truncated_record", reader.mate, record_index)
    if separator != b"+":
        raise _Refused("invalid_separator", reader.mate, record_index)
    quality = reader.line(record_index)
    if quality is None:
        raise _Refused("truncated_record", reader.mate, record_index)
    if len(quality) != len(sequence) or _QUALITY.fullmatch(quality) is None:
        raise _Refused("invalid_quality", reader.mate, record_index)
    return identity


def _bounded_result(result: FastqIntakeResult) -> FastqIntakeResult:
    if len(_canonical_bytes(result.to_dict())) > MAX_RESULT_JSON_BYTES:
        return FastqIntakeResult(None, _Diagnostic("output_limit_exceeded"))
    return result


def validate_paired_fastq(
    r1: BinaryIO, r2: BinaryIO, input_profile: dict[str, object],
) -> FastqIntakeResult:
    """Validate one declared pair without retaining or exporting individual reads.

    Streams must return bytes for bounded positive read requests; a nonempty
    short read is not EOF. I/O exceptions become fixed diagnostics. The caller
    owns stream lifetimes and any deadline needed for blocking I/O.
    """
    try:
        profile = _snapshot_profile(input_profile)
        if r1 is r2:
            raise _Refused("same_stream")
        left = _Reader(_read_method(r1, "R1"), "R1", profile.r1_size)
        right = _Reader(_read_method(r2, "R2"), "R2", profile.r2_size)
        pairs = 0
        while True:
            ordinal = pairs + 1
            left_record = _record(left, profile, ordinal)
            right_record = _record(right, profile, ordinal)
            if left_record is None and right_record is None:
                break
            if left_record is None or right_record is None:
                raise _Refused("pair_count_mismatch", record_index=ordinal)
            if left_record != right_record:
                raise _Refused("pair_identity_mismatch", record_index=ordinal)
            pairs += 1
        if not pairs:
            raise _Refused("empty_input")
        for reader, expected_size, expected_digest in (
            (left, profile.r1_size, profile.r1_sha256),
            (right, profile.r2_size, profile.r2_sha256),
        ):
            if reader.size != expected_size:
                raise _Refused("size_mismatch", reader.mate)
            if reader.hasher.hexdigest() != expected_digest:
                raise _Refused("digest_mismatch", reader.mate)
        return _bounded_result(FastqIntakeResult(_AcceptedPair(
            profile.digest, pairs,
            left.hasher.hexdigest(), left.size,
            right.hasher.hexdigest(), right.size,
        ), None))
    except _Refused as error:
        return _bounded_result(FastqIntakeResult(None, error.diagnostic))
