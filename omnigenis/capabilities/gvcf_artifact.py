"""Opt-in, lossless preparation of a bounded reference-bound gVCF artifact.

The source is never normalized or serialized through the index executor.  A
successful result retains its exact bytes in BGZF and returns the CSI together
with explicit integrity, reference and indexed-selection evidence.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import struct
import subprocess
import tempfile
import time
from typing import BinaryIO
import zlib

try:
    import fcntl
except ImportError:
    fcntl = None

from .artifact_gate import verify_artifact_bytes
from .gvcf_intake import (
    MAX_BYTES, MAX_LINE_BYTES, MAX_RECORDS, PROFILE_ID as INPUT_PROFILE_ID,
    GvcfIntakeResult, GvcfRecordObservation, _structured, validate_gvcf_bytes,
)
from .reference_identity import ReferenceIdentityResult


CAPABILITY_ID = "gvcf-artifact"
CAPABILITY_VERSION = "1.0.0"
EXECUTION_PROFILE_ID = "single-sample-gvcf-artifact-v1"
ARTIFACT_PROFILE_ID = "gatk-haplotypecaller-4.6.2.0-vcf42-grch38-bgzf-v1"
EXECUTOR_VERSION = "1.24"
HTSLIB_VERSION = "1.24"
REFERENCE_PROFILE_ID = "grch38-p14-ncbi-refseq-autosomal-v1"
REFERENCE_ASSEMBLY = "GCF_000001405.40"
REFERENCE_BUNDLE_SHA256 = "1c34b839e1ae36102d003a217f76f1dd57cd1d10b0310cbd9e1d8078c8e88672"
REFERENCE_FASTA_CONTENT_SHA256 = "df6e4918316e05a9cc1fd29c352841d3678b607d7a436819cd43371b52c814c0"
REFERENCE_FASTA_CONTENT_SIZE_BYTES = 3_339_739_109
REFERENCE_ALIAS_PROFILE_ID = "grch38-p14-primary-autosomes-v1"
REFERENCE_ALIAS_SHA256 = "e1f5e6be843cc30e05a0e8d714f10e11a352b09a955b7925069225621aeae68a"

BGZF_CHUNK_BYTES = 32768
MAX_BGZF_BYTES = 2097152
MAX_CSI_BYTES = 1048576
MAX_STDOUT_BYTES = 1114112
MAX_STDERR_BYTES = 65536
MAX_EXECUTOR_SNAPSHOT_BYTES = 16777216
MAX_TIMEOUT_SECONDS = 600
_IO_CHUNK_BYTES = 1048576
_BGZF_HEADER = bytes.fromhex("1f8b08040000000000ff060042430200")
BGZF_EOF = bytes.fromhex("1f8b08040000000000ff0600424302001b0003000000000000000000")

RULE_STRICT_REPARSE = "STRICT_REPARSE"
RULE_CARDINALITY = "CARDINALITY"
RULE_PHASE = "PHASE_SEMANTICS"
RULE_ORDERING = "ORDERING"
RULE_INDEXABILITY = "INDEXABILITY"
RULE_REF_CONCORDANCE = "REF_CONCORDANCE"
RULE_RETENTION = "RETENTION_ACCOUNTING"
_RULE_IDS = (
    RULE_STRICT_REPARSE, RULE_CARDINALITY, RULE_PHASE, RULE_ORDERING,
    RULE_INDEXABILITY, RULE_REF_CONCORDANCE, RULE_RETENTION,
)
_EXECUTION_PROFILE = {
    "schema_version": "1.0.0",
    "execution_profile_id": EXECUTION_PROFILE_ID,
    "executor_id": "omnigenis.local-direct",
    "executor_version": "1.0.0",
    "deterministic": True,
}
_RecordKey = tuple[str, int, str, str, tuple[str, ...]]
_LIMITATIONS = (
    "Opt-in bounded artifact preservation only; no default pipeline activation.",
    "Executor launches require Linux memfd sealing and /proc/self/fd access; no pathname fallback.",
    "The 600-second maximum deadline is cooperative: blocking system I/O, process creation and cleanup may overrun it; expiry never permits artifact promotion.",
    "HaplotypeCaller identity is an input declaration, not observed caller execution.",
    "No normalization, likelihood transformation, genotype inference or joint genotyping.",
    "Reference-block concordance covers its REF anchor and bounds, not callability through END.",
    "Missing observations and gaps remain unknown; phase is preserved without confidence inference.",
    "No clinical interpretation, reporting, clinical activation or release authorization.",
)
_PROVENANCE_REFS = (
    "https://samtools.github.io/hts-specs/SAMv1.pdf",
    "https://github.com/samtools/hts-specs/tree/b5341fbb5181cc7811c10c741a682149ab0cccf8",
    "https://samtools.github.io/hts-specs/VCFv4.2.pdf",
    "https://github.com/samtools/bcftools/tree/fb9f0f783e0f67d734f6fa7fe4df9d230522f196",
)


@dataclass(frozen=True)
class GvcfArtifactRuleResult:
    rule_id: str
    status: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"rule_id": self.rule_id, "status": self.status, "detail": self.detail}


@dataclass(frozen=True, repr=False)
class PreparedGvcfArtifact:
    artifact_id: str
    artifact_kind: str
    media_type: str
    content: bytes
    sha256: str
    size_bytes: int
    producer_execution_profile_id: str
    parent_artifact_ids: tuple[str, ...]

    def to_manifest(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0", "artifact_id": self.artifact_id,
            "artifact_kind": self.artifact_kind, "media_type": self.media_type,
            "sha256": self.sha256, "size_bytes": self.size_bytes,
            "producer_execution_profile_id": self.producer_execution_profile_id,
            "parent_artifact_ids": list(self.parent_artifact_ids),
        }


@dataclass(frozen=True)
class GvcfRetentionRecord:
    source_record_ordinal: int
    source_record_sha256: str
    output_record_ordinal: int
    output_record_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "source_record_ordinal": self.source_record_ordinal,
            "source_record_sha256": self.source_record_sha256,
            "output_record_ordinal": self.output_record_ordinal,
            "output_record_sha256": self.output_record_sha256,
        }


def _key_dict(key: _RecordKey) -> dict[str, object]:
    return {"chrom": key[0], "pos": key[1], "id": key[2], "ref": key[3], "alts": list(key[4])}


@dataclass(frozen=True)
class GvcfIndexQueryEvidence:
    region: str
    expected_record_ordinals: tuple[int, ...]
    observed_record_ordinals: tuple[int, ...]
    expected_record_keys: tuple[_RecordKey, ...]
    observed_record_keys: tuple[_RecordKey, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "region": self.region,
            "expected_record_ordinals": list(self.expected_record_ordinals),
            "observed_record_ordinals": list(self.observed_record_ordinals),
            "expected_count": len(self.expected_record_keys),
            "observed_count": len(self.observed_record_keys),
            "expected_record_keys": [_key_dict(key) for key in self.expected_record_keys],
            "observed_record_keys": [_key_dict(key) for key in self.observed_record_keys],
        }


@dataclass(frozen=True, repr=False)
class GvcfArtifactResult:
    source_artifact_id: str | None
    source_sha256: str | None
    source_size_bytes: int
    input_record_count: int
    retained_record_count: int
    dropped_record_count: int
    reference_block_count: int
    variant_record_count: int
    missing_genotype_count: int
    partial_genotype_count: int
    caller_declaration_verified: bool
    drop_reasons: tuple[str, ...]
    execution_profile_id: str | None
    reference_sha256: str | None
    executor_sha256: str | None
    executor_version: str | None
    htslib_version: str | None
    zlib_compile_version: str
    zlib_runtime_version: str
    rules: tuple[GvcfArtifactRuleResult, ...]
    ledger: tuple[GvcfRetentionRecord, ...]
    index_queries: tuple[GvcfIndexQueryEvidence, ...]
    artifacts: tuple[PreparedGvcfArtifact, ...]
    errors: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return (
            not self.errors
            and len(self.rules) == len(_RULE_IDS)
            and {rule.rule_id for rule in self.rules} == set(_RULE_IDS)
            and all(rule.status == "PASS" for rule in self.rules)
            and len(self.artifacts) == 2
            and self.retained_record_count == self.input_record_count
            and self.dropped_record_count == 0 and not self.drop_reasons
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "capability_id": CAPABILITY_ID, "capability_version": CAPABILITY_VERSION,
            "canonical_status": "PASS" if self.passed else "FAIL",
            "canonical_payload": {
                "source_artifact_id": self.source_artifact_id,
                "source_sha256": self.source_sha256, "source_size_bytes": self.source_size_bytes,
                "input_profile_id": INPUT_PROFILE_ID,
                "input_profile_verification": "VALIDATED" if self.caller_declaration_verified else "NOT_VERIFIED",
                "caller_declaration": {
                    "id": "gatk.HaplotypeCaller" if self.caller_declaration_verified else None,
                    "version": "4.6.2.0" if self.caller_declaration_verified else None,
                    "verification": "DECLARED_ONLY" if self.caller_declaration_verified else "NOT_VERIFIED",
                },
                "execution_profile_id": self.execution_profile_id,
                "artifact_profile_id": ARTIFACT_PROFILE_ID,
                "input_record_count": self.input_record_count,
                "retained_record_count": self.retained_record_count,
                "dropped_record_count": self.dropped_record_count,
                "reference_block_count": self.reference_block_count,
                "variant_record_count": self.variant_record_count,
                "missing_genotype_count": self.missing_genotype_count,
                "partial_genotype_count": self.partial_genotype_count,
                "drop_reasons": list(self.drop_reasons),
                "reference": {
                    "profile_id": REFERENCE_PROFILE_ID,
                    "assembly_accession": REFERENCE_ASSEMBLY,
                    "bundle_sha256": REFERENCE_BUNDLE_SHA256,
                    "content_sha256": self.reference_sha256,
                    "content_size_bytes": REFERENCE_FASTA_CONTENT_SIZE_BYTES if self.reference_sha256 else None,
                    "alias_profile_id": REFERENCE_ALIAS_PROFILE_ID,
                    "alias_sha256": REFERENCE_ALIAS_SHA256,
                },
                "executor": {"id": "bcftools", "sha256": self.executor_sha256, "version": self.executor_version, "htslib_version": self.htslib_version},
                "zlib": {"compile_version": self.zlib_compile_version, "runtime_version": self.zlib_runtime_version},
                "rules": [rule.to_dict() for rule in self.rules],
                "ledger": [entry.to_dict() for entry in self.ledger],
                "index_queries": [query.to_dict() for query in self.index_queries],
                "artifacts": [artifact.to_manifest() for artifact in self.artifacts],
                "errors": list(self.errors),
            },
            "availability": "AVAILABLE" if self.passed else "BLOCKED",
            "limitations": list(_LIMITATIONS), "executor_id": "bcftools",
            "executor_version": self.executor_version, "adapter_version": CAPABILITY_VERSION,
            "model_id": None, "resource_release": REFERENCE_ASSEMBLY,
            "reference_bundle": REFERENCE_BUNDLE_SHA256,
            "execution_profile": self.execution_profile_id,
            "raw_artifact_refs": [artifact.artifact_id for artifact in self.artifacts],
            "provenance_refs": list(_PROVENANCE_REFS),
        }


class _Failure(ValueError):
    """A bounded diagnostic containing no paths, source bytes or subprocess text."""

    def __init__(self, stage: str, reason: str):
        self.stage, self.reason = stage, reason
        super().__init__(f"{stage}_{reason}")


def _remaining(deadline: float, stage: str) -> float:
    value = deadline - time.monotonic()
    if value <= 0:
        raise _Failure(stage, "timeout")
    return value


def _fingerprint(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _open_regular(path: Path, stage: str) -> BinaryIO:
    """Refuse special files before a blocking read can occur."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise _Failure(stage, "not_regular_file")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def _sha256_file(path: Path, deadline: float, stage: str) -> tuple[str, tuple[int, ...]]:
    digest, total = hashlib.sha256(), 0
    with _open_regular(path, stage) as stream:
        before = os.fstat(stream.fileno())
        if not 0 < before.st_size <= MAX_EXECUTOR_SNAPSHOT_BYTES:
            raise _Failure(stage, "byte_budget_exceeded")
        while True:
            _remaining(deadline, stage)
            chunk = stream.read(min(_IO_CHUNK_BYTES, MAX_EXECUTOR_SNAPSHOT_BYTES - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_EXECUTOR_SNAPSHOT_BYTES:
                raise _Failure(stage, "byte_budget_exceeded")
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    if (total != before.st_size or _fingerprint(before) != _fingerprint(after)
            or _fingerprint(after) != _fingerprint(path.stat())):
        raise _Failure(stage, "changed_during_read")
    return digest.hexdigest(), _fingerprint(after)


@contextmanager
def _sealed_executor(path: Path, expected_digest: str, deadline: float) -> Iterator[tuple[int, str]]:
    """Verify one bounded executable snapshot and seal it before any launch."""
    descriptor: int | None = None
    try:
        if (not hasattr(os, "memfd_create")
                or any(not hasattr(fcntl, name) for name in (
                    "F_ADD_SEALS", "F_GET_SEALS", "F_SEAL_WRITE", "F_SEAL_GROW", "F_SEAL_SHRINK", "F_SEAL_SEAL",
                ))):
            raise _Failure("executor", "sealed_snapshot_unavailable")
        _remaining(deadline, "executor")
        with _open_regular(path, "executor") as stream:
            before = os.fstat(stream.fileno())
            if not 0 < before.st_size <= MAX_EXECUTOR_SNAPSHOT_BYTES:
                raise _Failure("executor", "byte_budget_exceeded")
            if not before.st_mode & 0o111:
                raise _Failure("executor", "not_executable")
            descriptor = os.memfd_create("omnigenis-bcftools", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
            digest, total = hashlib.sha256(), 0
            while True:
                _remaining(deadline, "executor")
                chunk = stream.read(min(_IO_CHUNK_BYTES, MAX_EXECUTOR_SNAPSHOT_BYTES - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_EXECUTOR_SNAPSHOT_BYTES:
                    raise _Failure("executor", "byte_budget_exceeded")
                digest.update(chunk)
                pending = memoryview(chunk)
                while pending:
                    _remaining(deadline, "executor")
                    written = os.write(descriptor, pending)
                    if written <= 0:
                        raise _Failure("executor", "snapshot_write_failed")
                    pending = pending[written:]
            after = os.fstat(stream.fileno())
            if (total != before.st_size or _fingerprint(before) != _fingerprint(after)
                    or _fingerprint(after) != _fingerprint(path.stat())):
                raise _Failure("executor", "changed_during_read")
        observed_digest = digest.hexdigest()
        if observed_digest != expected_digest:
            raise _Failure("executor", "digest_mismatch")
        os.fchmod(descriptor, 0o500)
        seals = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
        fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, seals)
        if fcntl.fcntl(descriptor, fcntl.F_GET_SEALS) & seals != seals or os.fstat(descriptor).st_size != total:
            raise _Failure("executor", "snapshot_seal_failed")
        sealed_digest, offset = hashlib.sha256(), 0
        while offset < total:
            _remaining(deadline, "executor")
            chunk = os.pread(descriptor, min(_IO_CHUNK_BYTES, total - offset), offset)
            if not chunk:
                raise _Failure("executor", "snapshot_incomplete")
            sealed_digest.update(chunk)
            offset += len(chunk)
        if sealed_digest.hexdigest() != expected_digest:
            raise _Failure("executor", "snapshot_digest_mismatch")
        _remaining(deadline, "executor")
        yield descriptor, observed_digest
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _run(
    args: list[str], *, cwd: Path, deadline: float, stage: str, pass_fds: tuple[int, ...] = (),
) -> subprocess.CompletedProcess[bytes]:
    """Drain bounded pipes under a cooperative deadline and reap the process."""
    _remaining(deadline, stage)
    process: subprocess.Popen[bytes] | None = None
    completed = False
    output, errors = bytearray(), bytearray()
    try:
        process = subprocess.Popen(
            args, cwd=cwd, shell=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
            close_fds=True, start_new_session=True, pass_fds=pass_fds,
            env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin", "TZ": "UTC", "BCFTOOLS_PLUGINS": ""},
        )
        with selectors.DefaultSelector() as selector:
            for pipe, target, limit in ((process.stdout, output, MAX_STDOUT_BYTES), (process.stderr, errors, MAX_STDERR_BYTES)):
                assert pipe is not None
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, (target, limit))
            while selector.get_map():
                wait = min(_remaining(deadline, stage), 0.25)
                for key, _ in selector.select(wait):
                    try:
                        chunk = os.read(key.fd, 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    target, limit = key.data
                    if len(target) + len(chunk) > limit:
                        raise _Failure(stage, "stdout_limit" if target is output else "stderr_limit")
                    target.extend(chunk)
        try:
            returncode = process.wait(timeout=_remaining(deadline, stage))
        except subprocess.TimeoutExpired:
            raise _Failure(stage, "timeout") from None
        if returncode != 0:
            raise _Failure(stage, f"exit_{returncode}")
        completed = True
        return subprocess.CompletedProcess(args, returncode, bytes(output), bytes(errors))
    except OSError:
        raise _Failure(stage, "os_error") from None
    finally:
        if process is not None:
            if not completed:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait()
            for pipe in (process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()


def _reference_aliases(profile: Mapping[str, object], identity: ReferenceIdentityResult) -> dict[str, tuple[str, int]]:
    if not isinstance(profile, Mapping) or not isinstance(identity, ReferenceIdentityResult):
        raise _Failure("reference", "contract_invalid")
    alias = profile.get("contig_alias_profile")
    assembly, bom = profile.get("assembly"), profile.get("resource_bom_ref")
    if not isinstance(alias, Mapping) or not isinstance(assembly, Mapping) or not isinstance(bom, Mapping):
        raise _Failure("reference", "contract_invalid")
    contigs = alias.get("contigs")
    try:
        alias_hash = hashlib.sha256(json.dumps(contigs, ensure_ascii=True, separators=(",", ":"), sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()
    except (TypeError, ValueError):
        raise _Failure("reference", "alias_contract_invalid") from None
    if (profile.get("schema_version") != "1.0.0" or profile.get("profile_id") != REFERENCE_PROFILE_ID
            or assembly.get("refseq_accession") != REFERENCE_ASSEMBLY
            or bom.get("bundle_sha256") != REFERENCE_BUNDLE_SHA256
            or alias.get("profile_id") != REFERENCE_ALIAS_PROFILE_ID
            or alias.get("sha256") != REFERENCE_ALIAS_SHA256 or alias_hash != REFERENCE_ALIAS_SHA256
            or type(contigs) is not list or len(contigs) != 22):
        raise _Failure("reference", "profile_identity_mismatch")
    aliases: dict[str, tuple[str, int]] = {}
    for row in contigs:
        if not isinstance(row, Mapping):
            raise _Failure("reference", "alias_contract_invalid")
        molecule, accession, length = row.get("assigned_molecule"), row.get("refseq_accession"), row.get("sequence_length")
        if (type(molecule) is not str or molecule not in {str(i) for i in range(1, 23)}
                or molecule in aliases or type(accession) is not str or not accession
                or type(length) is not int or length <= 0):
            raise _Failure("reference", "alias_contract_invalid")
        aliases[molecule] = (accession, length)
    accessions = tuple(accession for accession, _ in aliases.values())
    if (len(set(accessions)) != 22 or not identity.passed
            or identity.profile_id != REFERENCE_PROFILE_ID or identity.assembly_accession != REFERENCE_ASSEMBLY
            or identity.bundle_sha256 != REFERENCE_BUNDLE_SHA256
            or identity.fasta_content_sha256 != REFERENCE_FASTA_CONTENT_SHA256
            or type(identity.fasta_content_size_bytes) is not int
            or identity.fasta_content_size_bytes != REFERENCE_FASTA_CONTENT_SIZE_BYTES
            or type(identity.autosomal_refseq_accessions) is not tuple
            or len(identity.autosomal_refseq_accessions) != 22
            or set(identity.autosomal_refseq_accessions) != set(accessions)):
        raise _Failure("reference", "metadata_identity_not_verified")
    return aliases


def _scan_fasta(
    stream: BinaryIO,
    requests: Mapping[str, tuple[tuple[int, int, bytes], ...]],
    expected_lengths: Mapping[str, int],
    deadline: float,
) -> tuple[str, int]:
    """Hash original bytes and collect bounded requested spans in one stream."""
    digest, total = hashlib.sha256(), 0
    header = bytearray()
    reading_header, line_start = True, True
    current: str | None = None
    position = 0
    seen: set[str] = set()
    captured = {name: [bytearray() for _ in spans] for name, spans in requests.items()}

    def finish_contig() -> None:
        if current in expected_lengths and position != expected_lengths[current]:
            raise _Failure("reference", "contig_length_mismatch")

    def consume(sequence: bytes) -> None:
        nonlocal position
        if current is None:
            raise _Failure("reference", "fasta_structure_invalid")
        if current not in expected_lengths:
            return
        bases = sequence.replace(b"\n", b"").replace(b"\r", b"")
        start, end = position + 1, position + len(bases)
        for index, (left, right, _) in enumerate(requests.get(current, ())):
            overlap_start, overlap_end = max(start, left), min(end, right)
            if overlap_start <= overlap_end:
                captured[current][index].extend(bases[overlap_start - start:overlap_end - start + 1])
        position = end

    while True:
        _remaining(deadline, "reference")
        chunk = stream.read(_IO_CHUNK_BYTES)
        if not chunk:
            break
        digest.update(chunk)
        total += len(chunk)
        if total > REFERENCE_FASTA_CONTENT_SIZE_BYTES:
            raise _Failure("reference", "content_size_mismatch")
        cursor = 0
        while cursor < len(chunk):
            if reading_header:
                end = chunk.find(b"\n", cursor)
                piece = chunk[cursor:] if end < 0 else chunk[cursor:end]
                if len(header) + len(piece) > MAX_LINE_BYTES:
                    raise _Failure("reference", "fasta_header_limit")
                header.extend(piece)
                if end < 0:
                    break
                finish_contig()
                if not header.startswith(b">") or not header[1:].strip():
                    raise _Failure("reference", "fasta_structure_invalid")
                try:
                    current = bytes(header[1:]).split()[0].decode("ascii")
                except UnicodeError:
                    raise _Failure("reference", "fasta_structure_invalid") from None
                if current in expected_lengths:
                    if current in seen:
                        raise _Failure("reference", "duplicate_contig")
                    seen.add(current)
                position = 0
                header.clear()
                reading_header, line_start = False, True
                cursor = end + 1
                continue
            if line_start and chunk[cursor:cursor + 1] == b">":
                reading_header = True
                continue
            end = chunk.find(b"\n>", cursor)
            segment = chunk[cursor:] if end < 0 else chunk[cursor:end + 1]
            consume(segment)
            line_start = segment.endswith(b"\n")
            cursor = len(chunk) if end < 0 else end + 1
            if end >= 0:
                reading_header = True
    if reading_header or header:
        raise _Failure("reference", "fasta_structure_invalid")
    finish_contig()
    if seen != set(expected_lengths):
        raise _Failure("reference", "contig_missing")
    for name, spans in requests.items():
        for index, (_, _, expected) in enumerate(spans):
            if bytes(captured[name][index]).upper() != expected.upper():
                raise _Failure("reference", "ref_mismatch")
    return digest.hexdigest(), total


def _verify_reference(path, identity, profile, intake, deadline) -> None:
    aliases = _reference_aliases(profile, identity)
    for line in intake.meta_lines:
        if line.startswith("##contig="):
            row = _structured(line.partition("=")[2])
            expected = aliases.get(row.get("ID"))
            if expected is None or row.get("assembly") != "GRCh38" or int(row.get("length", "0")) != expected[1]:
                raise _Failure("reference", "declared_contig_mismatch")
    requests: dict[str, list[tuple[int, int, bytes]]] = {}
    lengths: dict[str, int] = {}
    for record in intake.records:
        accession, length = aliases[record.chrom]
        if record.pos < 1 or record.end > length or record.pos + len(record.ref) - 1 > length:
            raise _Failure("reference", "record_out_of_bounds")
        lengths[accession] = length
        requests.setdefault(accession, []).append((record.pos, record.pos + len(record.ref) - 1, record.ref.encode("ascii")))
    reference = Path(path).resolve(strict=True)
    with _open_regular(reference, "reference") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size != REFERENCE_FASTA_CONTENT_SIZE_BYTES:
            raise _Failure("reference", "content_size_mismatch")
        actual_hash, actual_size = _scan_fasta(stream, {name: tuple(spans) for name, spans in requests.items()}, lengths, deadline)
        after = os.fstat(stream.fileno())
        if _fingerprint(before) != _fingerprint(after) or _fingerprint(after) != _fingerprint(reference.stat()):
            raise _Failure("reference", "changed_during_read")
    if actual_size != REFERENCE_FASTA_CONTENT_SIZE_BYTES or actual_hash != REFERENCE_FASTA_CONTENT_SHA256:
        raise _Failure("reference", "content_identity_mismatch")


def _encode_bgzf(data: bytes) -> bytes:
    members = []
    for start in range(0, len(data), BGZF_CHUNK_BYTES):
        chunk = data[start:start + BGZF_CHUNK_BYTES]
        compressor = zlib.compressobj(6, zlib.DEFLATED, -15)
        payload = compressor.compress(chunk) + compressor.flush()
        total_size = 18 + len(payload) + 8
        if total_size > 65536:
            raise _Failure("reparse", "bgzf_member_limit")
        members.append(_BGZF_HEADER + struct.pack("<H", total_size - 1) + payload + struct.pack("<II", zlib.crc32(chunk), len(chunk)))
    encoded = b"".join(members) + BGZF_EOF
    if len(encoded) > MAX_BGZF_BYTES:
        raise _Failure("reparse", "bgzf_size_limit")
    return encoded


def _decode_bgzf(data: bytes) -> bytes:
    if len(data) > MAX_BGZF_BYTES or not data.endswith(BGZF_EOF):
        raise _Failure("reparse", "bgzf_eof_or_size_invalid")
    cursor, end = 0, len(data) - len(BGZF_EOF)
    decoded = bytearray()
    try:
        while cursor < end:
            if data[cursor:cursor + 16] != _BGZF_HEADER or cursor + 18 > end:
                raise _Failure("reparse", "bgzf_header_invalid")
            length = struct.unpack_from("<H", data, cursor + 16)[0] + 1
            if not 26 <= length <= 65536 or cursor + length > end:
                raise _Failure("reparse", "bgzf_member_invalid")
            inflater = zlib.decompressobj(31)
            budget = min(BGZF_CHUNK_BYTES, MAX_BYTES - len(decoded))
            part = inflater.decompress(data[cursor:cursor + length], budget + 1)
            if not part or len(part) > budget or not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
                raise _Failure("reparse", "bgzf_decompression_limit_or_structure")
            decoded.extend(part)
            cursor += length
    except (zlib.error, struct.error):
        raise _Failure("reparse", "bgzf_decompression_invalid") from None
    if cursor != end:
        raise _Failure("reparse", "bgzf_member_boundary_invalid")
    return bytes(decoded)


def _record_key(record: GvcfRecordObservation) -> _RecordKey:
    return record.chrom, record.pos, record.raw_record.split("\t", 3)[2], record.ref, record.alts


def _query_points(records: tuple[GvcfRecordObservation, ...]) -> tuple[tuple[str, int], ...]:
    points = {(record.chrom, record.pos) for record in records}
    for record in records:
        if record.kind == "REFERENCE_BLOCK" and record.end > record.pos:
            points.add((record.chrom, record.pos + (record.end - record.pos + 1) // 2))
    for previous, current in zip(records, records[1:]):
        if previous.chrom == current.chrom and current.pos > previous.end + 1:
            points.add((current.chrom, (previous.end + current.pos) // 2))
    return tuple(sorted(points, key=lambda value: (int(value[0]), value[1])))


def _query_keys(output: bytes) -> tuple[_RecordKey, ...]:
    if len(output) > MAX_STDOUT_BYTES:
        raise _Failure("query", "stdout_limit")
    lines = output.splitlines()
    if len(lines) > MAX_RECORDS:
        raise _Failure("query", "record_limit")
    result = []
    try:
        for line in lines:
            if not line or line.startswith(b"#"):
                raise _Failure("query", "record_structure_invalid")
            values = line.decode("utf-8").split("\t")
            if len(values) != 10 or not values[1].isascii() or not values[1].isdigit() or len(values[1]) > 10:
                raise _Failure("query", "record_structure_invalid")
            result.append((values[0], int(values[1]), values[2], values[3], tuple(values[4].split(","))))
    except UnicodeError:
        raise _Failure("query", "record_encoding_invalid") from None
    return tuple(result)


def _read_output(path: Path, limit: int, stage: str) -> bytes:
    with _open_regular(path, stage) as stream:
        before = os.fstat(stream.fileno())
        if path.is_symlink() or not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
            raise _Failure(stage, "output_size_or_type_invalid")
        content = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if (len(content) != before.st_size or _fingerprint(before) != _fingerprint(after)
            or _fingerprint(after) != _fingerprint(path.stat())):
        raise _Failure(stage, "output_changed_or_limit")
    return content


def _artifact(content: bytes, kind: str, media: str, parent: str) -> PreparedGvcfArtifact:
    digest = hashlib.sha256(content).hexdigest()
    result = PreparedGvcfArtifact(f"{kind.lower()}:sha256:{digest}", kind, media, content, digest, len(content), EXECUTION_PROFILE_ID, (parent,))
    if not verify_artifact_bytes(result.to_manifest(), content).passed:
        raise _Failure("retention", "artifact_integrity_failed")
    return result


def prepare_gvcf_artifacts(
    data: bytes,
    *,
    input_profile: Mapping[str, object],
    source_artifact_manifest: Mapping[str, object],
    execution_profile: Mapping[str, object],
    reference_profile: Mapping[str, object],
    reference_identity: ReferenceIdentityResult,
    reference_fasta: str | os.PathLike[str],
    bcftools_executable: str | os.PathLike[str],
    expected_executor_sha256: str,
    timeout_seconds: int = 600,
) -> GvcfArtifactResult:
    """Prepare both artifacts atomically; unsupported input returns no artifacts."""
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    states = {rule_id: GvcfArtifactRuleResult(rule_id, "NOT_RUN", "prerequisite_not_satisfied") for rule_id in _RULE_IDS}
    facts = dict(
        source_artifact_id=None, source_sha256=hashlib.sha256(data).hexdigest() if len(data) <= MAX_BYTES else None,
        source_size_bytes=len(data),
        input_record_count=0, retained_record_count=0, dropped_record_count=0,
        reference_block_count=0, variant_record_count=0, missing_genotype_count=0,
        partial_genotype_count=0, caller_declaration_verified=False, drop_reasons=(), execution_profile_id=None,
        reference_sha256=None, executor_sha256=None, executor_version=None, htslib_version=None,
        zlib_compile_version=zlib.ZLIB_VERSION, zlib_runtime_version=zlib.ZLIB_RUNTIME_VERSION,
        ledger=(), index_queries=(), artifacts=(), errors=(),
    )

    def passed(rule_id: str, detail: str) -> None:
        states[rule_id] = GvcfArtifactRuleResult(rule_id, "PASS", detail)

    try:
        if len(data) > MAX_BYTES:
            raise _Failure("input", "byte_budget_exceeded")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise _Failure("input", "timeout_invalid")
        deadline = time.monotonic() + timeout_seconds
        intake = validate_gvcf_bytes(data, input_profile)
        if not intake.is_valid:
            raise _Failure("input", "gvcf_profile_invalid")
        facts["caller_declaration_verified"] = True
        facts.update(input_record_count=intake.record_count, reference_block_count=intake.reference_block_count,
                     variant_record_count=intake.variant_record_count,
                     missing_genotype_count=sum(record.genotype_status == "MISSING" for record in intake.records),
                     partial_genotype_count=sum(record.genotype_status == "PARTIAL" for record in intake.records))
        if (not isinstance(execution_profile, Mapping) or set(execution_profile) != set(_EXECUTION_PROFILE)
                or any(type(execution_profile[key]) is not type(value) or execution_profile[key] != value for key, value in _EXECUTION_PROFILE.items())):
            raise _Failure("input", "execution_profile_invalid")
        facts["execution_profile_id"] = EXECUTION_PROFILE_ID
        source_gate = verify_artifact_bytes(source_artifact_manifest, data)
        if (not source_gate.passed or source_artifact_manifest.get("artifact_kind") != "gVCF"
                or source_artifact_manifest.get("media_type") != "text/vcf"):
            raise _Failure("input", "source_artifact_invalid")
        facts["source_artifact_id"] = source_gate.artifact_id
        for record in intake.records:
            info = record.raw_record.split("\t")[7]
            if (record.kind == "REFERENCE_BLOCK" and (not info.startswith("END=") or ";" in info)) or (record.kind != "REFERENCE_BLOCK" and info != "."):
                raise _Failure("input", "present_info_unsupported")
        if (type(expected_executor_sha256) is not str or len(expected_executor_sha256) != 64
                or any(character not in "0123456789abcdef" for character in expected_executor_sha256)):
            raise _Failure("executor", "expected_digest_invalid")
        executable = Path(bcftools_executable).resolve(strict=True)
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise _Failure("executor", "not_executable")
        executor_stat = _fingerprint(executable.stat())
        with _sealed_executor(executable, expected_executor_sha256, deadline) as (descriptor, observed_digest):
            facts["executor_sha256"] = observed_digest
            launch_path = f"/proc/self/fd/{descriptor}"
            version = _run([launch_path, "--version"], cwd=executable.parent, deadline=min(deadline, time.monotonic() + 30), stage="version", pass_fds=(descriptor,))
            if version.stdout.splitlines()[:2] != [f"bcftools {EXECUTOR_VERSION}".encode(), f"Using htslib {HTSLIB_VERSION}".encode()]:
                raise _Failure("executor", "version_mismatch")
            facts.update(executor_version=EXECUTOR_VERSION, htslib_version=HTSLIB_VERSION)
            _verify_reference(reference_fasta, reference_identity, reference_profile, intake, deadline)
            facts["reference_sha256"] = REFERENCE_FASTA_CONTENT_SHA256
            passed(RULE_REF_CONCORDANCE, "actual_reference_bytes_alias_lengths_and_ref_spans_verified")

            compressed = _encode_bgzf(data)
            decoded = _decode_bgzf(compressed)
            if decoded != data:
                raise _Failure("reparse", "source_bytes_changed")
            reparsed = validate_gvcf_bytes(decoded, input_profile)
            if not reparsed.is_valid or reparsed != intake:
                raise _Failure("reparse", "strict_intake_mismatch")
            passed(RULE_STRICT_REPARSE, "bounded_bgzf_decoding_equals_source_and_strict_intake")
            if reparsed.record_count != intake.record_count or any(a.alts != b.alts for a, b in zip(intake.records, reparsed.records)):
                raise _Failure("cardinality", "record_or_allele_mismatch")
            passed(RULE_CARDINALITY, "record_allele_and_format_cardinalities_preserved")
            phase = lambda record: (record.gt, record.phase_separator, record.genotype_status, tuple(pair for pair in record.format_values if pair[0] in {"GT", "PGT", "PID", "PS"}))
            if any(phase(a) != phase(b) for a, b in zip(intake.records, reparsed.records)):
                raise _Failure("phase", "phase_observation_mismatch")
            passed(RULE_PHASE, "exact_gt_phase_and_missing_observations_preserved")
            if tuple((r.chrom, r.pos, r.end) for r in intake.records) != tuple((r.chrom, r.pos, r.end) for r in reparsed.records):
                raise _Failure("ordering", "record_order_mismatch")
            passed(RULE_ORDERING, "strict_source_order_and_nonoverlapping_spans_preserved")
            ledger = tuple(GvcfRetentionRecord(index, source.record_sha256, index, output.record_sha256) for index, (source, output) in enumerate(zip(intake.records, reparsed.records), 1))
            _remaining(deadline, "index")
            with tempfile.TemporaryDirectory(prefix="omnigenis-gvcf-artifact-") as temporary:
                workspace = Path(temporary)
                bgzf_path = workspace / "artifact.g.vcf.gz"
                csi_path = workspace / "artifact.g.vcf.gz.csi"
                bgzf_path.write_bytes(compressed)
                _run([launch_path, "index", "--csi", "--min-shift", "14", "--output", str(csi_path), str(bgzf_path)], cwd=workspace, deadline=deadline, stage="index", pass_fds=(descriptor,))
                index_bytes = _read_output(csi_path, MAX_CSI_BYTES, "index")
                keys = tuple(_record_key(record) for record in intake.records)
                ordinal_by_key = {key: index for index, key in enumerate(keys, 1)}
                queries = []
                for chrom, position in _query_points(intake.records):
                    region = f"{chrom}:{position}-{position}"
                    expected_ordinals = tuple(index for index, record in enumerate(intake.records, 1) if record.chrom == chrom and record.pos <= position <= record.end)
                    expected_keys = tuple(keys[index - 1] for index in expected_ordinals)
                    query = _run([launch_path, "view", "--no-version", "-H", "-Ov", "-I", "--regions-overlap", "1", "-r", region, str(bgzf_path)], cwd=workspace, deadline=deadline, stage="query", pass_fds=(descriptor,))
                    observed_keys = _query_keys(query.stdout)
                    if observed_keys != expected_keys:
                        raise _Failure("query", "indexed_selection_mismatch")
                    observed_ordinals = tuple(ordinal_by_key[key] for key in observed_keys)
                    queries.append(GvcfIndexQueryEvidence(region, expected_ordinals, observed_ordinals, expected_keys, observed_keys))
                if _read_output(bgzf_path, MAX_BGZF_BYTES, "index") != compressed or _read_output(csi_path, MAX_CSI_BYTES, "index") != index_bytes:
                    raise _Failure("index", "artifacts_changed_during_queries")
            passed(RULE_INDEXABILITY, "actual_csi_anchor_block_interior_and_gap_queries_match")
            digest_after, stat_after = _sha256_file(executable, deadline, "executor")
            if digest_after != expected_executor_sha256 or stat_after != executor_stat:
                raise _Failure("executor", "changed_during_execution")
        compressed_artifact = _artifact(compressed, "gVCF_BGZF", "application/gzip", source_gate.artifact_id)
        index_artifact = _artifact(index_bytes, "CSI", "application/octet-stream", compressed_artifact.artifact_id)
        if source_gate.artifact_id in {compressed_artifact.artifact_id, index_artifact.artifact_id}:
            raise _Failure("retention", "artifact_identity_conflict")
        if len(ledger) != intake.record_count or any(entry.source_record_sha256 != entry.output_record_sha256 for entry in ledger):
            raise _Failure("retention", "ledger_mismatch")
        _remaining(deadline, "retention")
        passed(RULE_RETENTION, "exact_source_bytes_one_to_one_ledger_zero_dropped_and_artifact_integrity")
        facts.update(retained_record_count=intake.record_count, ledger=ledger, index_queries=tuple(queries), artifacts=(compressed_artifact, index_artifact))
    except _Failure as error:
        rule = {"reference": RULE_REF_CONCORDANCE, "reparse": RULE_STRICT_REPARSE,
                "cardinality": RULE_CARDINALITY, "phase": RULE_PHASE, "ordering": RULE_ORDERING,
                "index": RULE_INDEXABILITY, "query": RULE_INDEXABILITY, "retention": RULE_RETENTION}.get(error.stage)
        if rule:
            states[rule] = GvcfArtifactRuleResult(rule, "FAIL", str(error))
        facts["errors"] = (str(error),)
    except (OSError, ValueError, TypeError):
        facts["errors"] = ("artifact_preparation_io_or_contract_error",)
    return GvcfArtifactResult(**facts, rules=tuple(states[rule_id] for rule_id in _RULE_IDS))
