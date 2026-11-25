"""Bounded, opt-in structural intake for a declared HaplotypeCaller gVCF profile.

The parser preserves observations without calling, normalizing, interpreting or
expanding them. Caller and reference metadata are declarations, not verification
of a generating executable or reference sequence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import math
import re


PROFILE_ID = "gatk-haplotypecaller-4.6.2.0-vcf42-grch38-v1"
MAX_BYTES = 1048576
MAX_RECORDS = 32
MAX_LINE_BYTES = 65536
MAX_HEADER_LINES = 256
MAX_NUMERIC_DIGITS = 10
MAX_INTEGER = 2147483647

_PROFILE_CONSTANTS = {
    "schema_version": "1.0.0",
    "profile_id": PROFILE_ID,
    "input_kind": "gVCF",
    "media_type": "text/vcf",
    "caller_id": "gatk.HaplotypeCaller",
    "caller_version": "4.6.2.0",
    "reference_assembly": "GRCh38",
}
_PROFILE_FIELDS = frozenset(_PROFILE_CONSTANTS) | {
    "input_id", "content_sha256", "size_bytes", "sample_id",
}
_COLUMNS = ("#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT")
_CHROMS = frozenset(str(value) for value in range(1, 23))
_KEY = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\Z")
_UINT = re.compile(r"[0-9]+\Z")
_SAMPLE = re.compile(r"[!-~]{1,256}\Z")
_QUAL = re.compile(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_GT = re.compile(r"([0-9]+|\.)([/|])([0-9]+|\.)\Z")
_BAND = re.compile(
    r"GVCFBlock([0-9]+)-([0-9]+)=minGQ=([0-9]+)\(inclusive\),"
    r"maxGQ=([0-9]+)\(exclusive\)\Z"
)
_FORMAT_SHAPES = {
    "GT": ("1", "String"),
    "DP": ("1", "Integer"),
    "GQ": ("1", "Integer"),
    "MIN_DP": ("1", "Integer"),
    "PL": ("G", "Integer"),
    "AD": ("R", "Integer"),
    "SB": ("4", "Integer"),
    "PGT": ("1", "String"),
    "PID": ("1", "String"),
    "PS": ("1", "Integer"),
}
_REQUIRED_FORMATS = frozenset(("GT", "DP", "GQ", "MIN_DP", "PL", "AD"))
_BLOCK_FORMATS = frozenset(("GT", "DP", "GQ", "MIN_DP", "PL"))
_VARIANT_FORMATS = frozenset(("GT", "DP", "GQ", "AD", "PL"))
_DECLARATION_KINDS = ("ALT", "INFO", "FORMAT", "FILTER", "contig", "GATKCommandLine")
_PROVENANCE_REFS = (
    "https://samtools.github.io/hts-specs/VCFv4.2.pdf",
    "https://github.com/broadinstitute/gatk/tree/76edc75c26504da94bbaee66584e107e76ee15de",
    "https://gatk.broadinstitute.org/hc/en-us/articles/360035531812-GVCF-Genomic-Variant-Call-Format",
)
_LIMITATIONS = (
    "Structural validation of a bounded single-sample, diploid autosomal subset only; "
    "no full HaplotypeCaller output compatibility or actual caller execution is established.",
    "Caller, assembly and contig length are input declarations; reference sequence "
    "identity and REF concordance are not verified.",
    "Only SNVs, leading-base-anchored one-base indels and explicit nonoverlapping "
    "reference blocks are supported; compressed input and other domains are refused.",
    "Missing observations and gaps remain unknown. No callability, negative findings, "
    "normalization, genotype inference, PL transformation or joint genotyping is performed.",
    "Phase fields are preserved without phase confidence, haplotype or cis/trans inference.",
    "INFO annotations other than END are retained after syntax checks without semantic validation.",
    "This opt-in intake does not perform clinical interpretation, reporting or default pipeline activation.",
)


@dataclass(frozen=True)
class GvcfRecordObservation:
    source_line_number: int
    raw_record: str
    record_sha256: str
    chrom: str
    pos: int
    end: int
    kind: str
    ref: str
    alts: tuple[str, ...]
    gt: str
    phase_separator: str
    genotype_status: str
    format_values: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict[str, object]:
        """Return detached JSON-compatible observations."""
        return {
            "source_line_number": self.source_line_number,
            "raw_record": self.raw_record,
            "record_sha256": self.record_sha256,
            "chrom": self.chrom,
            "pos": self.pos,
            "end": self.end,
            "kind": self.kind,
            "ref": self.ref,
            "alts": list(self.alts),
            "gt": self.gt,
            "phase_separator": self.phase_separator,
            "genotype_status": self.genotype_status,
            "format_values": dict(self.format_values),
        }


@dataclass(frozen=True)
class GvcfIntakeResult:
    is_valid: bool
    sample_id: str | None
    record_count: int
    reference_block_count: int
    variant_record_count: int
    missing_genotype_count: int
    size_bytes: int
    content_sha256: str
    errors: tuple[str, ...]
    records: tuple[GvcfRecordObservation, ...]
    meta_lines: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "capability_id": "gvcf-intake",
            "capability_version": "1.0.0",
            "canonical_status": "VALID" if self.is_valid else "INVALID",
            "canonical_payload": {
                "profile_id": PROFILE_ID,
                "sample_id": self.sample_id,
                "record_count": self.record_count,
                "reference_block_count": self.reference_block_count,
                "variant_record_count": self.variant_record_count,
                "missing_genotype_count": self.missing_genotype_count,
                "size_bytes": self.size_bytes,
                "content_sha256": self.content_sha256,
                "errors": list(self.errors),
                "meta_lines": list(self.meta_lines),
                "records": [record.to_dict() for record in self.records] if self.is_valid else [],
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


class _Invalid(ValueError):
    """A deterministic refusal of input outside the supported profile."""


@dataclass(frozen=True)
class _Header:
    formats: frozenset[str]
    filters: frozenset[str]
    contigs: dict[str, dict[str, str]]
    bands: tuple[tuple[int, int], ...]


def _integer(value: str, field: str, *, positive: bool = False) -> int:
    if not _UINT.fullmatch(value) or len(value) > MAX_NUMERIC_DIGITS:
        raise _Invalid(f"{field}: expected a bounded unsigned decimal integer")
    number = int(value)
    if number > MAX_INTEGER or (positive and number == 0):
        raise _Invalid(f"{field}: integer outside the supported range")
    return number


def _profile_sample(profile: Mapping[str, object], size: int, digest: str) -> str:
    if not isinstance(profile, Mapping) or set(profile) != _PROFILE_FIELDS:
        raise _Invalid("input_profile: exact profile fields are required")
    for key, expected in _PROFILE_CONSTANTS.items():
        if type(profile[key]) is not str or profile[key] != expected:
            raise _Invalid(f"input_profile.{key}: unsupported value")
    input_id = profile["input_id"]
    if type(input_id) is not str or not 1 <= len(input_id) <= 256:
        raise _Invalid("input_profile.input_id: expected 1 to 256 characters")
    sample = profile["sample_id"]
    if type(sample) is not str or not _SAMPLE.fullmatch(sample):
        raise _Invalid("input_profile.sample_id: expected a printable ASCII token of 1 to 256 characters")
    supplied_digest = profile["content_sha256"]
    if (
        type(supplied_digest) is not str
        or not re.fullmatch(r"[0-9a-f]{64}", supplied_digest)
        or supplied_digest != digest
    ):
        raise _Invalid("input_profile.content_sha256: input bytes do not match")
    if type(profile["size_bytes"]) is not int or profile["size_bytes"] != size:
        raise _Invalid("input_profile.size_bytes: input bytes do not match")
    return sample


def _lines(data: bytes) -> list[str]:
    if not data or len(data) > MAX_BYTES:
        raise _Invalid("input: byte budget requires 1 to 1048576 bytes")
    if data.startswith(b"\xef\xbb\xbf"):
        raise _Invalid("input: UTF-8 BOM is unsupported")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise _Invalid("input: expected uncompressed UTF-8 text") from None
    if not text.endswith("\n"):
        raise _Invalid("input: final record must be terminated by LF or CRLF")
    for index, character in enumerate(text):
        code = ord(character)
        if (code < 32 and character not in "\t\r\n") or 127 <= code <= 159 or character in "\u2028\u2029":
            raise _Invalid("input: disallowed control or newline separator")
        if character == "\r" and (index + 1 == len(text) or text[index + 1] != "\n"):
            raise _Invalid("input: lone carriage return is unsupported")
    lines = [line[:-1] if line.endswith("\r") else line for line in text.split("\n")[:-1]]
    if any(not line for line in lines):
        raise _Invalid("input: empty lines are unsupported")
    if any(len(line.encode("utf-8")) > MAX_LINE_BYTES for line in lines):
        raise _Invalid("input: line byte budget exceeded")
    if not lines or lines[0] != "##fileformat=VCFv4.2":
        raise _Invalid("input: first line must be ##fileformat=VCFv4.2")
    return lines


def _quoted(text: str, start: int) -> tuple[str, int]:
    """Parse one quoted value, allowing only escaped quotes and backslashes."""
    output: list[str] = []
    index = start + 1
    while index < len(text):
        character = text[index]
        if character == '"':
            return "".join(output), index + 1
        if character == "\\":
            index += 1
            if index == len(text) or text[index] not in ('"', "\\"):
                raise _Invalid("metadata: invalid quoted escape")
            character = text[index]
        output.append(character)
        index += 1
    raise _Invalid("metadata: unterminated quoted value")


def _structured(value: str) -> dict[str, str]:
    if not value.startswith("<") or not value.endswith(">"):
        raise _Invalid("metadata: expected a structured declaration")
    body = value[1:-1]
    attributes: dict[str, str] = {}
    index = 0
    while index < len(body):
        equals = body.find("=", index)
        if equals < 0:
            raise _Invalid("metadata: attribute requires a value")
        key = body[index:equals]
        if not _KEY.fullmatch(key) or key in attributes:
            raise _Invalid("metadata: invalid or duplicate attribute key")
        index = equals + 1
        if index == len(body):
            raise _Invalid("metadata: empty unquoted attribute")
        quoted = body[index] == '"'
        if quoted:
            item, index = _quoted(body, index)
        else:
            end = body.find(",", index)
            if end < 0:
                end = len(body)
            item = body[index:end]
            index = end
            if not item or any(char.isspace() or char in '"\\<>' for char in item):
                raise _Invalid("metadata: invalid unquoted attribute")
        if key == "Description" and not quoted:
            raise _Invalid("metadata: Description must be quoted")
        attributes[key] = item
        if index < len(body):
            if body[index] != "," or index + 1 == len(body):
                raise _Invalid("metadata: malformed attribute separator")
            index += 1
    if not attributes:
        raise _Invalid("metadata: empty structured declaration")
    return attributes


def _declaration_shape(kind: str, attrs: dict[str, str]) -> None:
    if "ID" not in attrs or not _KEY.fullmatch(attrs["ID"]):
        raise _Invalid(f"metadata {kind}: a valid ID is required")
    if kind in ("ALT", "INFO", "FORMAT", "FILTER") and "Description" not in attrs:
        raise _Invalid(f"metadata {kind}: Description is required")
    if kind not in ("INFO", "FORMAT"):
        return
    number, value_type = attrs.get("Number"), attrs.get("Type")
    if number is None or value_type not in ("Integer", "Float", "Flag", "Character", "String"):
        raise _Invalid(f"metadata {kind}: Number and supported Type are required")
    if number not in ("A", "R", "G", "."):
        _integer(number, f"metadata {kind} Number")
    if value_type == "Flag" and (number != "0" or kind == "FORMAT"):
        raise _Invalid(f"metadata {kind}: invalid Flag declaration")


def _metadata(lines: tuple[str, ...]) -> _Header:
    declarations: dict[str, dict[str, dict[str, str]]] = {kind: {} for kind in _DECLARATION_KINDS}
    ordinary: dict[str, str] = {}
    bands: list[tuple[int, int]] = []
    for number, line in enumerate(lines, 1):
        try:
            key, separator, value = line[2:].partition("=")
            if not separator or not _KEY.fullmatch(key) or not value or "\t" in value:
                raise _Invalid("metadata: malformed key/value line")
            if key in declarations:
                attributes = _structured(value)
                _declaration_shape(key, attributes)
                identifier = attributes["ID"]
                if identifier in declarations[key]:
                    raise _Invalid(f"metadata {key}: duplicate ID")
                declarations[key][identifier] = attributes
                continue
            if key in ordinary:
                raise _Invalid("metadata: duplicate key")
            ordinary[key] = value
            if key.startswith("GVCFBlock"):
                match = _BAND.fullmatch(key + "=" + value)
                if match is None:
                    raise _Invalid("metadata: malformed GVCFBlock declaration")
                low, high, declared_low, declared_high = (
                    _integer(part, "GVCFBlock bound") for part in match.groups()
                )
                if low != declared_low or high != declared_high or not 0 <= low < high <= 100:
                    raise _Invalid("metadata: inconsistent or unsupported GVCFBlock range")
                bands.append((low, high))
            elif value.startswith("<"):
                _structured(value)
            elif value.startswith('"'):
                _, end = _quoted(value, 0)
                if end != len(value):
                    raise _Invalid("metadata: text follows a quoted value")
        except _Invalid as error:
            raise _Invalid(f"line {number}: {error}") from None

    if ordinary.get("fileformat") != "VCFv4.2" or ordinary.get("source") != "HaplotypeCaller":
        raise _Invalid("metadata: exact fileformat and HaplotypeCaller source are required")
    commands = declarations["GATKCommandLine"]
    if len(commands) != 1 or commands.get("HaplotypeCaller", {}).get("Version") != "4.6.2.0":
        raise _Invalid("metadata: one HaplotypeCaller 4.6.2.0 command declaration is required")
    if "NON_REF" not in declarations["ALT"]:
        raise _Invalid("metadata: NON_REF ALT declaration is required")
    end = declarations["INFO"].get("END", {})
    if (end.get("Number"), end.get("Type")) != ("1", "Integer"):
        raise _Invalid("metadata: END must declare Number=1 and Type=Integer")
    formats = declarations["FORMAT"]
    if not _REQUIRED_FORMATS.issubset(formats):
        raise _Invalid("metadata: required FORMAT declarations are missing")
    for name, attrs in formats.items():
        if name in _FORMAT_SHAPES and (attrs.get("Number"), attrs.get("Type")) != _FORMAT_SHAPES[name]:
            raise _Invalid(f"metadata FORMAT {name}: unsupported Number/Type")
    if not bands:
        raise _Invalid("metadata: at least one GVCFBlock range is required")
    ordered_bands = sorted(bands)
    if any(current[0] < previous[1] for previous, current in zip(ordered_bands, ordered_bands[1:])):
        raise _Invalid("metadata: GVCFBlock ranges overlap")
    return _Header(
        formats=frozenset(formats),
        filters=frozenset(declarations["FILTER"]),
        contigs=declarations["contig"],
        bands=tuple(ordered_bands),
    )


def _info(value: str) -> dict[str, str | None]:
    if value == ".":
        return {}
    annotations: dict[str, str | None] = {}
    for entry in value.split(";"):
        key, separator, item = entry.partition("=")
        if not _KEY.fullmatch(key) or key in annotations:
            raise _Invalid("INFO: invalid or duplicate key")
        if separator and (not item or "=" in item or "" in item.split(",")):
            raise _Invalid("INFO: ambiguous or empty value")
        annotations[key] = item if separator else None
    return annotations


def _genotype(value: str, max_concrete_index: int, field: str) -> tuple[str, str]:
    match = _GT.fullmatch(value)
    if match is None:
        raise _Invalid(f"{field}: expected two allele tokens separated by / or |")
    first, separator, second = match.groups()
    missing = 0
    for allele in (first, second):
        if allele == ".":
            missing += 1
        elif _integer(allele, field) > max_concrete_index:
            raise _Invalid(f"{field}: symbolic or unsupported allele index")
    status = "MISSING" if missing == 2 else "PARTIAL" if missing else "CALLED"
    return separator, status


def _array(value: str, count: int, field: str) -> None:
    if value == ".":
        return
    items = value.split(",")
    if len(items) != count:
        raise _Invalid(f"{field}: unsupported array cardinality")
    for item in items:
        _integer(item, field)


def _format(
    field: str, sample: str, kind: str, header: _Header
) -> tuple[tuple[tuple[str, str], ...], str, str]:
    tags, values = field.split(":"), sample.split(":")
    if (
        not tags or tags[0] != "GT" or len(set(tags)) != len(tags)
        or len(tags) != len(values) or any(not value for value in values)
        or any(tag not in _FORMAT_SHAPES or tag not in header.formats for tag in tags)
    ):
        raise _Invalid("FORMAT: require GT first, unique declared supported tags and exact nonempty values")
    required = _BLOCK_FORMATS if kind == "REFERENCE_BLOCK" else _VARIANT_FORMATS
    if not required.issubset(tags):
        raise _Invalid("FORMAT: required observation fields are missing")
    observed = dict(zip(tags, values))
    maximum = 0 if kind == "REFERENCE_BLOCK" else 1
    separator, status = _genotype(observed["GT"], maximum, "GT")
    integers: dict[str, int] = {}
    for tag in ("DP", "GQ", "MIN_DP", "PS"):
        if tag in observed and observed[tag] != ".":
            integers[tag] = _integer(observed[tag], tag)
    if "GQ" in integers and integers["GQ"] > 99:
        raise _Invalid("GQ: maximum supported value is 99")
    if "MIN_DP" in integers and "DP" in integers and integers["MIN_DP"] > integers["DP"]:
        raise _Invalid("MIN_DP: cannot exceed observed DP")
    if kind == "REFERENCE_BLOCK" and "GQ" in integers:
        if not any(low <= integers["GQ"] < high for low, high in header.bands):
            raise _Invalid("GQ: reference block value is outside declared bands")
    allele_count = 2 if kind == "REFERENCE_BLOCK" else 3
    _array(observed["PL"], allele_count * (allele_count + 1) // 2, "PL")
    if "AD" in observed:
        _array(observed["AD"], allele_count, "AD")
    if "SB" in observed:
        _array(observed["SB"], 4, "SB")
    if "PGT" in observed and observed["PGT"] != ".":
        _genotype(observed["PGT"], maximum, "PGT")
    if "PID" in observed and "," in observed["PID"]:
        raise _Invalid("PID: require one string value or missing dot")
    return tuple(zip(tags, values)), separator, status


def _record(line: str, number: int, header: _Header) -> GvcfRecordObservation:
    fields = line.split("\t")
    if len(fields) != 10 or any(not field or any(char.isspace() for char in field) for field in fields):
        raise _Invalid("record: expected ten nonempty whitespace-free fields")
    chrom, raw_pos, identifier, ref, raw_alt, qual, filters, raw_info, formats, sample = fields
    if chrom not in _CHROMS or chrom not in header.contigs:
        raise _Invalid("CHROM: require a declared literal autosome 1 to 22")
    contig = header.contigs[chrom]
    if contig.get("assembly") != "GRCh38" or "length" not in contig:
        raise _Invalid("contig: require declared GRCh38 assembly and length")
    contig_length = _integer(contig["length"], "contig length", positive=True)
    pos = _integer(raw_pos, "POS", positive=True)
    if pos > contig_length:
        raise _Invalid("POS: exceeds declared contig length")
    if identifier != ".":
        ids = identifier.split(";")
        if any(not item or item == "." for item in ids) or len(set(ids)) != len(ids):
            raise _Invalid("ID: empty, missing or duplicate identifiers")
    if not ref or any(base not in "ACGT" for base in ref):
        raise _Invalid("REF: only uppercase A/C/G/T bases are supported")
    alts = tuple(raw_alt.split(","))
    annotations = _info(raw_info)
    if alts == ("<NON_REF>",):
        kind = "REFERENCE_BLOCK"
        if len(ref) != 1 or annotations.get("END") is None:
            raise _Invalid("reference block: require one-base REF and explicit END")
        end = _integer(annotations["END"], "END", positive=True)
    else:
        kind = "VARIANT"
        if len(alts) != 2 or alts[1] != "<NON_REF>":
            raise _Invalid("variant: require one concrete ALT followed by <NON_REF>")
        alt = alts[0]
        if not alt or alt == ref or any(base not in "ACGT" for base in alt):
            raise _Invalid("ALT: unsupported concrete allele")
        if not (
            len(ref) == len(alt) == 1
            or ((len(ref), len(alt)) in ((1, 2), (2, 1)) and ref[0] == alt[0])
        ):
            raise _Invalid("variant: only SNVs and leading-base-anchored one-base indels are supported")
        if "END" in annotations:
            raise _Invalid("variant: END is unsupported")
        end = pos + len(ref) - 1
    if end < pos or end > contig_length:
        raise _Invalid("record span: outside POS and declared contig length")
    if qual != ".":
        if not _QUAL.fullmatch(qual) or not math.isfinite(float(qual)):
            raise _Invalid("QUAL: require a finite nonnegative decimal or missing dot")
    if filters not in (".", "PASS"):
        tags = filters.split(";")
        if (
            len(set(tags)) != len(tags)
            or any(tag in (".", "PASS", "0") or tag not in header.filters for tag in tags)
        ):
            raise _Invalid("FILTER: require unique declared filter IDs")
    observations, separator, status = _format(formats, sample, kind, header)
    return GvcfRecordObservation(
        source_line_number=number,
        raw_record=line,
        record_sha256=hashlib.sha256(line.encode("utf-8")).hexdigest(),
        chrom=chrom,
        pos=pos,
        end=end,
        kind=kind,
        ref=ref,
        alts=alts,
        gt=observations[0][1],
        phase_separator=separator,
        genotype_status=status,
        format_values=observations,
    )


def validate_gvcf_bytes(data: bytes, input_profile: Mapping[str, object]) -> GvcfIntakeResult:
    """Validate the registered subset without I/O or modifying input observations.

    Input outside the supported profile returns INVALID with no accepted records.
    Programmer misuse of the bytes API raises TypeError. Validation errors report
    the first deterministic refusal; absent observations are never imputed.
    """
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    size, digest = len(data), hashlib.sha256(data).hexdigest()
    try:
        sample_id = _profile_sample(input_profile, size, digest)
        lines = _lines(data)
        column_index = next((index for index, line in enumerate(lines) if not line.startswith("##")), len(lines))
        if column_index == len(lines):
            raise _Invalid("header: missing #CHROM columns and records")
        if column_index + 1 > MAX_HEADER_LINES:
            raise _Invalid("header: line budget exceeded")
        columns = lines[column_index].split("\t")
        if len(columns) != 10 or tuple(columns[:9]) != _COLUMNS or columns[9] != sample_id:
            raise _Invalid("header: require exact single-sample columns matching input_profile")
        meta_lines = tuple(lines[:column_index])
        header = _metadata(meta_lines)
        raw_records = lines[column_index + 1:]
        if not 1 <= len(raw_records) <= MAX_RECORDS:
            raise _Invalid("records: require 1 to 32 records")
        records: list[GvcfRecordObservation] = []
        seen_ids: set[str] = set()
        for number, line in enumerate(raw_records, column_index + 2):
            try:
                if line.startswith("#"):
                    raise _Invalid("record: late or duplicate header is unsupported")
                record = _record(line, number, header)
                if records:
                    previous = records[-1]
                    if int(record.chrom) < int(previous.chrom):
                        raise _Invalid("record order: contigs must be contiguous and numerically ascending")
                    if record.chrom == previous.chrom and record.pos <= previous.end:
                        raise _Invalid("record order: duplicate, descending or overlapping spans")
                identifiers = line.split("\t", 3)[2].split(";")
                if identifiers != ["."]:
                    if seen_ids.intersection(identifiers):
                        raise _Invalid("ID: duplicate identifier across records")
                    seen_ids.update(identifiers)
                records.append(record)
            except _Invalid as error:
                raise _Invalid(f"line {number}: {error}") from None
        block_count = sum(record.kind == "REFERENCE_BLOCK" for record in records)
        return GvcfIntakeResult(
            is_valid=True,
            sample_id=sample_id,
            record_count=len(records),
            reference_block_count=block_count,
            variant_record_count=len(records) - block_count,
            missing_genotype_count=sum(record.genotype_status != "CALLED" for record in records),
            size_bytes=size,
            content_sha256=digest,
            errors=(),
            records=tuple(records),
            meta_lines=meta_lines,
        )
    except _Invalid as error:
        return GvcfIntakeResult(
            is_valid=False,
            sample_id=None,
            record_count=0,
            reference_block_count=0,
            variant_record_count=0,
            missing_genotype_count=0,
            size_bytes=size,
            content_sha256=digest,
            errors=(str(error),),
            records=(),
            meta_lines=(),
        )
