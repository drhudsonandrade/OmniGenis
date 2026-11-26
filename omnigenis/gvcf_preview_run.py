"""Opt-in source-preserving gVCF technical preview.

A passing result establishes the bounded technical composition only. Source
genotypes are observations, clinical interpretation is NOT_ASSESSED, and the
caller-provided context timestamp is not an execution attestation. Rebinding
checks retained content in memory; it does not rerun or attest the native tool.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import zlib

from .capabilities import gvcf_artifact as artifact_capability
from .capabilities.artifact_gate import verify_artifact_bytes
from .capabilities.gvcf_artifact import (
    GvcfArtifactResult, GvcfArtifactRuleResult, GvcfIndexQueryEvidence,
    GvcfRetentionRecord, PreparedGvcfArtifact, prepare_gvcf_artifacts,
)
from .capabilities.gvcf_intake import (
    GvcfIntakeResult, GvcfRecordObservation, validate_gvcf_bytes,
)
from .capabilities.reference_identity import ReferenceIdentityResult, ReferenceRuleResult

CAPABILITY_ID = "gvcf-technical-preview"
CAPABILITY_VERSION = "1.0.0"
PROFILE_ID = "single-sample-gvcf-technical-preview-v1"
INPUT_PROFILE_ID = "gatk-haplotypecaller-4.6.2.0-vcf42-grch38-v1"
MAX_INPUT_BYTES = 1_048_576
MAX_INPUT_RECORDS = 32
MAX_CAPTURE_BYTES = 65_536
MAX_PREVIEW_EXPORT_BYTES = 8_388_608
MAX_JSON_DEPTH = 16
MAX_JSON_NODES = 16_384
SCHEMA_ID = "urn:omnigenis:schema:gvcf-preview-run:1.0.0"
SCHEMA_SHA256 = "4b453dd4052fa56572e5126deaf7a15a332d27a3f23aabee6ef063712db66460"
_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "gvcf-preview-run.v1.schema.json"
COMPLETED_STAGES = (
    "bounded_inputs", "profiles", "intake", "artifact", "canonical_model",
    "evidence_snapshot", "interpretation", "output_schema",
)
_LIMITATIONS = (
    "This opt-in result is a technical preview, not a clinical report or final-release authorization.",
    "Source records and genotype fields are preserved; they are not newly called or clinically confirmed variants.",
    "No clinical or variant-interpretation analysis classes were evaluated; all clinical assessments are NOT_ASSESSED.",
    "Reference-block checks cover REF anchors and bounds, not clinical callability or negative findings through END.",
    "Missing and partial genotypes, gaps and phase confidence remain unknown; no imputation or phase inference is performed.",
    "No normalization, allele projection, likelihood transformation, joint genotyping or HGVS identity is performed.",
    "HaplotypeCaller identity is declared in the source; observed_at is caller-provided context, not caller execution or an execution timestamp.",
    "Qualification is bounded to one profile; the inherited native deadline is cooperative and execution origin requires separate retained evidence.",
)
_CLINICAL_AXES = (
    "variant_classification", "gene_disease_validity", "actionability",
    "diagnostic_implication", "clinical_confirmation", "biological_callability",
    "phase_confidence", "cross_source_conflicts",
)
_RULE_DETAILS = (
    ("STRICT_REPARSE", "bounded_bgzf_decoding_equals_source_and_strict_intake"),
    ("CARDINALITY", "record_allele_and_format_cardinalities_preserved"),
    ("PHASE_SEMANTICS", "exact_gt_phase_and_missing_observations_preserved"),
    ("ORDERING", "strict_source_order_and_nonoverlapping_spans_preserved"),
    ("INDEXABILITY", "actual_csi_anchor_block_interior_and_gap_queries_match"),
    ("REF_CONCORDANCE", "actual_reference_bytes_alias_lengths_and_ref_spans_verified"),
    ("RETENTION_ACCOUNTING", "exact_source_bytes_one_to_one_ledger_zero_dropped_and_artifact_integrity"),
)
_REFERENCE_RULE_IDS = frozenset({
    "REFERENCE_PROFILE_CONTRACT", "REFERENCE_ASSEMBLY_IDENTITY",
    "REFERENCE_FASTA_IDENTITY", "REFERENCE_ASSEMBLY_REPORT_IDENTITY",
    "REFERENCE_BUNDLE_IDENTITY", "REFERENCE_SOURCE_REGISTRY",
    "REFERENCE_USE_RIGHTS", "REFERENCE_CONTIG_COMPATIBILITY",
})
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})\Z"
)


class GvcfPreviewBindingError(ValueError):
    """A fixed diagnostic without source values, paths or native stderr."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise GvcfPreviewBindingError(code)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _bounded_json(value: object, *, limit: int) -> bytes:
    """Check a finite native JSON tree before encoding, then cap encoded bytes."""
    stack = [(value, 0, False)]
    active: set[int] = set()
    nodes = 0
    text_bytes = 0
    while stack:
        current, depth, leaving = stack.pop()
        if leaving:
            active.remove(id(current))
            continue
        nodes += 1
        _require(nodes <= MAX_JSON_NODES and depth <= MAX_JSON_DEPTH, "json_budget_exceeded")
        kind = type(current)
        if kind is str:
            _require(len(current) <= min(limit, 65_536), "json_string_budget_exceeded")
            try:
                text_bytes += len(current.encode("utf-8"))
            except UnicodeError:
                raise GvcfPreviewBindingError("json_encoding_invalid") from None
            _require(text_bytes <= limit, "json_budget_exceeded")
        elif kind is int:
            _require(current.bit_length() <= 64, "json_integer_invalid")
        elif kind is bool or current is None:
            pass
        elif kind is dict or kind is list:
            _require(len(current) <= MAX_JSON_NODES, "json_budget_exceeded")
            _require(id(current) not in active, "json_cycle_invalid")
            active.add(id(current))
            stack.append((current, depth, True))
            if kind is dict:
                _require(all(type(key) is str for key in current), "json_key_invalid")
                for key, item in current.items():
                    stack.append((key, depth + 1, False))
                    stack.append((item, depth + 1, False))
            else:
                stack.extend((item, depth + 1, False) for item in current)
        else:
            raise GvcfPreviewBindingError("json_value_invalid")
    encoded = bytearray()
    try:
        encoder = json.JSONEncoder(
            sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        )
        for part in encoder.iterencode(value):
            chunk = part.encode("utf-8")
            _require(len(encoded) + len(chunk) <= limit, "json_byte_budget_exceeded")
            encoded.extend(chunk)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise GvcfPreviewBindingError("json_encoding_invalid") from None
    return bytes(encoded)


def _capture(value: object) -> bytes:
    _require(type(value) is dict, "native_profile_mapping_required")
    return _bounded_json(value, limit=MAX_CAPTURE_BYTES)


def _load_capture(content: bytes) -> dict:
    _require(type(content) is bytes and len(content) <= MAX_CAPTURE_BYTES, "profile_capture_invalid")
    try:
        value = json.loads(content)
    except (UnicodeError, ValueError, RecursionError):
        raise GvcfPreviewBindingError("profile_capture_invalid") from None
    _require(type(value) is dict, "profile_capture_invalid")
    _require(_capture(value) == content, "profile_capture_invalid")
    return value


def _equal(actual: object, expected: object) -> None:
    """Strict finite comparison: numeric/container coercion is never equality."""
    _require(type(actual) is type(expected), "run_binding_invalid")
    if is_dataclass(expected) and not isinstance(expected, type):
        for field in fields(expected):
            _equal(getattr(actual, field.name), getattr(expected, field.name))
    elif type(expected) is tuple:
        _require(len(actual) == len(expected), "run_binding_invalid")
        for item, reference in zip(actual, expected, strict=True):
            _equal(item, reference)
    elif type(expected) is dict:
        _require(actual.keys() == expected.keys(), "run_binding_invalid")
        _require(all(type(key) is str for key in actual), "run_binding_invalid")
        for key in expected:
            _equal(actual[key], expected[key])
    elif type(expected) is list:
        _require(len(actual) == len(expected), "run_binding_invalid")
        for item, reference in zip(actual, expected, strict=True):
            _equal(item, reference)
    else:
        _require(actual == expected, "run_binding_invalid")


def _timestamp(value: object) -> str:
    _require(type(value) is str and 20 <= len(value) <= 35, "observed_at_invalid")
    _require(_TIMESTAMP.fullmatch(value) is not None, "observed_at_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        _require(parsed.utcoffset() is not None, "observed_at_invalid")
    except ValueError:
        raise GvcfPreviewBindingError("observed_at_invalid") from None
    return value


def _policy() -> dict:
    return {
        "policy_id": "gvcf-technical-preview-policy-v1",
        "lifecycle_profile": "preview",
        "intended_use_category": "TECHNICAL_GVCF_VALIDATION",
        "clinical_use_status": "NOT_CLINICALLY_PROMOTED",
        "release_authorization": "NOT_ESTABLISHED",
        "clinical_assessment_status": "NOT_ASSESSED",
    }


@dataclass(frozen=True, slots=True)
class GvcfCanonicalObservation:
    observation_id: str
    source_record_ordinal: int
    source_line_number: int
    record_sha256: str
    kind: str
    chrom: str
    pos: int
    source_position: str
    end: int
    source_id: str
    ref: str
    alts: tuple[str, ...]
    qual: str
    filter: str
    info: str
    format_values: tuple[tuple[str, str], ...]
    source_gt: str
    phase_separator: str
    source_genotype_status: str

    def to_dict(self) -> dict:
        return {
            "observation_id": self.observation_id,
            "source_record_ordinal": self.source_record_ordinal,
            "source_line_number": self.source_line_number,
            "record_sha256": self.record_sha256,
            "kind": self.kind, "chrom": self.chrom, "pos": self.pos,
            "source_position": self.source_position, "end": self.end,
            "source_id": self.source_id, "ref": self.ref, "alts": list(self.alts),
            "qual": self.qual, "filter": self.filter, "info": self.info,
            "format_values": [{"key": key, "value": value} for key, value in self.format_values],
            "source_gt": self.source_gt, "phase_separator": self.phase_separator,
            "source_genotype_status": self.source_genotype_status,
        }


@dataclass(frozen=True, slots=True, repr=False)
class GvcfCanonicalObservationModel:
    sample_id: str
    record_count: int
    observations: tuple[GvcfCanonicalObservation, ...]
    payload_json: bytes
    content_sha256: str

    def to_dict(self) -> dict:
        return {
            **json.loads(self.payload_json),
            "model_id": "gvcf-model:sha256:" + self.content_sha256,
            "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True, slots=True, repr=False)
class GvcfTechnicalEvidenceSnapshot:
    canonical_model_sha256: str
    payload_json: bytes
    content_sha256: str

    def to_dict(self) -> dict:
        return {
            **json.loads(self.payload_json),
            "snapshot_id": "gvcf-technical-evidence:sha256:" + self.content_sha256,
            "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True, slots=True)
class GvcfTechnicalInterpretationItem:
    observation: GvcfCanonicalObservation
    technical_evidence_sha256: str

    def to_dict(self) -> dict:
        return {
            "observation": self.observation.to_dict(),
            "technical_evidence_sha256": self.technical_evidence_sha256,
            "assessment_status": "NOT_ASSESSED",
            "clinical_availability": "NOT_MEASURED",
            "clinical_axes": {name: "NOT_ASSESSED" for name in _CLINICAL_AXES},
            "reason": "Clinical interpretation was not performed; this item preserves a source gVCF observation.",
        }


@dataclass(frozen=True, slots=True, repr=False)
class GvcfTechnicalInterpretationObject:
    items: tuple[GvcfTechnicalInterpretationItem, ...]
    payload_json: bytes
    content_sha256: str

    def to_dict(self) -> dict:
        return {
            **json.loads(self.payload_json),
            "interpretation_id": "gvcf-technical-interpretation:sha256:" + self.content_sha256,
            "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True, slots=True, repr=False)
class GvcfPreviewRunResult:
    status: str = "FAIL"
    errors: tuple[str, ...] = ()
    completed_stages: tuple[str, ...] = ()
    source_gvcf: bytes = b""
    input_profile_json: bytes = b""
    source_artifact_manifest_json: bytes = b""
    execution_profile_json: bytes = b""
    reference_profile_json: bytes = b""
    reference_identity: ReferenceIdentityResult | None = None
    expected_executor_sha256: str = ""
    observed_at: str = ""
    intake: GvcfIntakeResult | None = None
    artifact: GvcfArtifactResult | None = None
    canonical_model: GvcfCanonicalObservationModel | None = None
    evidence_snapshot: GvcfTechnicalEvidenceSnapshot | None = None
    interpretation: GvcfTechnicalInterpretationObject | None = None
    limitations: tuple[str, ...] = _LIMITATIONS

    @property
    def passed(self) -> bool:
        return (
            type(self.status) is str and self.status == "PASS"
            and type(self.errors) is tuple and not self.errors
            and type(self.canonical_model) is GvcfCanonicalObservationModel
            and type(self.evidence_snapshot) is GvcfTechnicalEvidenceSnapshot
            and type(self.interpretation) is GvcfTechnicalInterpretationObject
        )

    def to_dict(self) -> dict:
        """Export only a completed, rebound result; failure has no partial export."""
        rebind_gvcf_preview_run(self)
        return _export(self)


def _check_reference(profile: dict, identity: ReferenceIdentityResult) -> None:
    _require(type(identity) is ReferenceIdentityResult, "reference_binding_invalid")
    for name, expected in {
        "profile_id": artifact_capability.REFERENCE_PROFILE_ID,
        "assembly_accession": artifact_capability.REFERENCE_ASSEMBLY,
        "bundle_sha256": artifact_capability.REFERENCE_BUNDLE_SHA256,
        "fasta_content_sha256": artifact_capability.REFERENCE_FASTA_CONTENT_SHA256,
        "fasta_content_size_bytes": artifact_capability.REFERENCE_FASTA_CONTENT_SIZE_BYTES,
    }.items():
        _equal(getattr(identity, name), expected)
    _require(type(identity.rules) is tuple and len(identity.rules) == 8, "reference_binding_invalid")
    _require(type(identity.autosomal_refseq_accessions) is tuple and len(identity.autosomal_refseq_accessions) == 22, "reference_binding_invalid")
    for accession in identity.autosomal_refseq_accessions:
        _require(type(accession) is str and 1 <= len(accession) <= 32, "reference_binding_invalid")
    names = set()
    for rule in identity.rules:
        _require(type(rule) is ReferenceRuleResult, "reference_binding_invalid")
        _require(type(rule.rule_id) is str and rule.rule_id in _REFERENCE_RULE_IDS, "reference_binding_invalid")
        _require(type(rule.status) is str and rule.status == "PASS", "reference_binding_invalid")
        _require(type(rule.detail) is str and 1 <= len(rule.detail) <= 256, "reference_binding_invalid")
        names.add(rule.rule_id)
    _require(names == _REFERENCE_RULE_IDS, "reference_binding_invalid")
    try:
        artifact_capability._reference_aliases(profile, identity)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise GvcfPreviewBindingError("reference_binding_invalid") from None


def _profiles(run: GvcfPreviewRunResult) -> tuple[dict, dict, dict, dict]:
    input_profile = _load_capture(run.input_profile_json)
    manifest = _load_capture(run.source_artifact_manifest_json)
    execution = _load_capture(run.execution_profile_json)
    reference = _load_capture(run.reference_profile_json)
    _equal(execution, {
        "schema_version": "1.0.0",
        "execution_profile_id": "single-sample-gvcf-artifact-v1",
        "executor_id": "omnigenis.local-direct", "executor_version": "1.0.0",
        "deterministic": True,
    })
    _require(type(manifest.get("size_bytes")) is int, "source_artifact_invalid")
    _require(
        manifest.get("artifact_kind") == "gVCF"
        and manifest.get("media_type") == "text/vcf"
        and verify_artifact_bytes(manifest, run.source_gvcf).passed,
        "source_artifact_invalid",
    )
    _require(
        type(run.expected_executor_sha256) is str
        and _SHA256.fullmatch(run.expected_executor_sha256) is not None,
        "executor_binding_invalid",
    )
    _check_reference(reference, run.reference_identity)
    return input_profile, manifest, execution, reference


def _check_intake(source: bytes, profile: dict, actual: GvcfIntakeResult) -> GvcfIntakeResult:
    expected = validate_gvcf_bytes(source, profile)
    _require(type(actual) is GvcfIntakeResult and expected.is_valid, "intake_binding_invalid")
    _equal(actual, expected)
    for record in expected.records:
        info = record.raw_record.split("\t")[7]
        _require(
            (record.kind == "REFERENCE_BLOCK" and info.startswith("END=") and ";" not in info)
            or (record.kind == "VARIANT" and info == "."),
            "present_info_unsupported",
        )
    return expected


def _check_artifact(
    result: GvcfArtifactResult, source: bytes, intake: GvcfIntakeResult,
    manifest: dict, expected_executor_sha256: str,
) -> None:
    _require(type(result) is GvcfArtifactResult, "artifact_binding_invalid")
    _require(type(result.rules) is tuple and len(result.rules) == 7, "artifact_binding_invalid")
    for actual, (rule_id, detail) in zip(result.rules, _RULE_DETAILS, strict=True):
        _equal(actual, GvcfArtifactRuleResult(rule_id, "PASS", detail))
    expected_facts = {
        "source_artifact_id": manifest["artifact_id"],
        "source_sha256": _digest(source), "source_size_bytes": len(source),
        "input_record_count": intake.record_count, "retained_record_count": intake.record_count,
        "dropped_record_count": 0, "reference_block_count": intake.reference_block_count,
        "variant_record_count": intake.variant_record_count,
        "missing_genotype_count": sum(record.genotype_status == "MISSING" for record in intake.records),
        "partial_genotype_count": sum(record.genotype_status == "PARTIAL" for record in intake.records),
        "caller_declaration_verified": True, "drop_reasons": (),
        "execution_profile_id": "single-sample-gvcf-artifact-v1",
        "reference_sha256": artifact_capability.REFERENCE_FASTA_CONTENT_SHA256,
        "executor_sha256": expected_executor_sha256, "executor_version": "1.24",
        "htslib_version": "1.24", "zlib_compile_version": zlib.ZLIB_VERSION,
        "zlib_runtime_version": zlib.ZLIB_RUNTIME_VERSION, "errors": (),
    }
    for name, expected in expected_facts.items():
        _equal(getattr(result, name), expected)
    _require(type(result.artifacts) is tuple and len(result.artifacts) == 2, "artifact_binding_invalid")
    parent = manifest["artifact_id"]
    for actual, kind, media, limit in zip(
        result.artifacts, ("gVCF_BGZF", "CSI"),
        ("application/gzip", "application/octet-stream"),
        (artifact_capability.MAX_BGZF_BYTES, artifact_capability.MAX_CSI_BYTES), strict=True,
    ):
        _require(type(actual) is PreparedGvcfArtifact, "artifact_binding_invalid")
        _require(type(actual.content) is bytes and 0 < len(actual.content) <= limit, "artifact_binding_invalid")
        expected = artifact_capability._artifact(actual.content, kind, media, parent)
        _equal(actual, expected)
        _require(actual.artifact_id != manifest["artifact_id"], "artifact_binding_invalid")
        parent = actual.artifact_id
    _require(
        artifact_capability._decode_bgzf(result.artifacts[0].content) == source,
        "artifact_source_bytes_mismatch",
    )
    expected_ledger = tuple(
        GvcfRetentionRecord(ordinal, record.record_sha256, ordinal, record.record_sha256)
        for ordinal, record in enumerate(intake.records, 1)
    )
    _equal(result.ledger, expected_ledger)
    keys = tuple(artifact_capability._record_key(record) for record in intake.records)
    expected_queries = []
    for chrom, position in artifact_capability._query_points(intake.records):
        ordinals = tuple(
            ordinal for ordinal, record in enumerate(intake.records, 1)
            if record.chrom == chrom and record.pos <= position <= record.end
        )
        selected_keys = tuple(keys[ordinal - 1] for ordinal in ordinals)
        expected_queries.append(GvcfIndexQueryEvidence(
            f"{chrom}:{position}-{position}", ordinals, ordinals, selected_keys, selected_keys,
        ))
    _equal(result.index_queries, tuple(expected_queries))


def _observation(source_sha256: str, ordinal: int, record: GvcfRecordObservation) -> GvcfCanonicalObservation:
    columns = record.raw_record.split("\t")
    _require(len(columns) == 10, "source_columns_invalid")
    identity = _bounded_json({
        "source_sha256": source_sha256, "source_record_ordinal": ordinal,
        "record_sha256": record.record_sha256,
    }, limit=MAX_CAPTURE_BYTES)
    return GvcfCanonicalObservation(
        "gvcf-observation:sha256:" + _digest(identity), ordinal,
        record.source_line_number, record.record_sha256, record.kind,
        record.chrom, record.pos, columns[1], record.end, columns[2], record.ref,
        record.alts, columns[5], columns[6], columns[7], record.format_values,
        record.gt, record.phase_separator, record.genotype_status,
    )


def _input_bindings(run: GvcfPreviewRunResult) -> dict:
    return {
        "input_profile_sha256": _digest(run.input_profile_json),
        "source_artifact_manifest_sha256": _digest(run.source_artifact_manifest_json),
        "execution_profile_sha256": _digest(run.execution_profile_json),
        "reference_profile_sha256": _digest(run.reference_profile_json),
    }


def _model(
    intake: GvcfIntakeResult, artifact: GvcfArtifactResult, input_bindings: dict,
) -> GvcfCanonicalObservationModel:
    observations = tuple(
        _observation(intake.content_sha256, ordinal, record)
        for ordinal, record in enumerate(intake.records, 1)
    )
    artifact_payload = artifact.to_dict()["canonical_payload"]
    body = {
        "schema_version": "1.0.0", "source_artifact_id": artifact.source_artifact_id,
        "source_sha256": intake.content_sha256, "source_size_bytes": intake.size_bytes,
        "input_profile_id": INPUT_PROFILE_ID, "sample_id": intake.sample_id,
        "input_bindings": input_bindings,
        "caller_declaration": artifact_payload["caller_declaration"],
        "reference": artifact_payload["reference"],
        "record_count": intake.record_count, "reference_block_count": intake.reference_block_count,
        "variant_record_count": intake.variant_record_count,
        "missing_genotype_count": artifact.missing_genotype_count,
        "partial_genotype_count": artifact.partial_genotype_count,
        "observations": [record.to_dict() for record in observations],
    }
    encoded = _bounded_json(body, limit=MAX_PREVIEW_EXPORT_BYTES)
    return GvcfCanonicalObservationModel(
        intake.sample_id, intake.record_count, observations, encoded, _digest(encoded),
    )


def _evidence(
    model: GvcfCanonicalObservationModel, artifact: GvcfArtifactResult, observed_at: str,
) -> GvcfTechnicalEvidenceSnapshot:
    sources = []
    for source_id, version, version_kind, digest in (
        ("source-gvcf", artifact.source_sha256, "CONTENT_SHA256", artifact.source_sha256),
        ("reference-fasta", artifact_capability.REFERENCE_ASSEMBLY, "PINNED_VERSION", artifact.reference_sha256),
        ("artifact-executor", artifact.executor_version, "PINNED_VERSION", artifact.executor_sha256),
    ):
        sources.append({
            "source_id": source_id, "source_version": version, "version_kind": version_kind,
            "locator": "urn:sha256:" + digest,
            "retrieval_method": "LOCAL_BOUND_TECHNICAL_EVIDENCE",
            "result_sha256": digest, "mutable": False,
        })
    body = {
        "schema_version": "1.0.0", "canonical_model_sha256": model.content_sha256,
        "source_sha256": artifact.source_sha256, "observed_at": observed_at,
        "observed_at_origin": "CALLER_PROVIDED_CONTEXT",
        "source_registry_id": "source-registry-gvcf-technical-v1", "sources": sources,
        "artifact_evidence": artifact.to_dict()["canonical_payload"],
    }
    encoded = _bounded_json(body, limit=MAX_PREVIEW_EXPORT_BYTES)
    return GvcfTechnicalEvidenceSnapshot(model.content_sha256, encoded, _digest(encoded))


def _interpretation(
    model: GvcfCanonicalObservationModel, evidence: GvcfTechnicalEvidenceSnapshot,
) -> GvcfTechnicalInterpretationObject:
    items = tuple(
        GvcfTechnicalInterpretationItem(observation, evidence.content_sha256)
        for observation in model.observations
    )
    body = {
        "schema_version": "1.0.0", "canonical_model_sha256": model.content_sha256,
        "evidence_snapshot_sha256": evidence.content_sha256,
        "assessment_status": "NOT_ASSESSED", "evaluated_analysis_class_ids": [],
        "normalization_status": "NOT_ASSESSED", "conflict_assessment_status": "NOT_ASSESSED",
        "items": [item.to_dict() for item in items],
    }
    encoded = _bounded_json(body, limit=MAX_PREVIEW_EXPORT_BYTES)
    return GvcfTechnicalInterpretationObject(items, encoded, _digest(encoded))


def _export(run: GvcfPreviewRunResult) -> dict:
    payload = {
        "schema_version": "1.0.0", "capability_id": CAPABILITY_ID,
        "capability_version": CAPABILITY_VERSION, "profile_id": PROFILE_ID,
        "status": run.status, "completed_stages": list(run.completed_stages),
        "observed_at": run.observed_at, "observed_at_origin": "CALLER_PROVIDED_CONTEXT",
        "canonical_model": run.canonical_model.to_dict(),
        "evidence_snapshot": run.evidence_snapshot.to_dict(),
        "interpretation": run.interpretation.to_dict(),
        "policy_status": _policy(), "limitations": list(run.limitations),
    }
    _bounded_json(payload, limit=MAX_PREVIEW_EXPORT_BYTES)
    return payload


def rebind_gvcf_preview_run(run: GvcfPreviewRunResult) -> None:
    """Rebuild retained in-memory semantics without native I/O or origin claims."""
    try:
        _require(type(run) is GvcfPreviewRunResult and run.passed, "run_not_passed")
        _equal(run.status, "PASS")
        _equal(run.errors, ())
        _equal(run.completed_stages, COMPLETED_STAGES)
        _equal(run.limitations, _LIMITATIONS)
        _require(type(run.source_gvcf) is bytes and 0 < len(run.source_gvcf) <= MAX_INPUT_BYTES, "source_bytes_invalid")
        _timestamp(run.observed_at)
        profile, manifest, _, _ = _profiles(run)
        intake = _check_intake(run.source_gvcf, profile, run.intake)
        _check_artifact(run.artifact, run.source_gvcf, intake, manifest, run.expected_executor_sha256)
        model = _model(intake, run.artifact, _input_bindings(run))
        evidence = _evidence(model, run.artifact, run.observed_at)
        interpretation = _interpretation(model, evidence)
        _equal(run.canonical_model, model)
        _equal(run.evidence_snapshot, evidence)
        _equal(run.interpretation, interpretation)
    except GvcfPreviewBindingError:
        raise
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, RecursionError):
        raise GvcfPreviewBindingError("run_binding_invalid") from None


def validate_gvcf_preview_export(payload: dict) -> None:
    """Validate the fixed local schema; caller-selected or remote schemas are absent."""
    try:
        _require(type(payload) is dict, "preview_export_invalid")
        _bounded_json(payload, limit=MAX_PREVIEW_EXPORT_BYTES)
        with _SCHEMA_PATH.open("rb") as stream:
            schema_bytes = stream.read(MAX_CAPTURE_BYTES + 1)
        _require(len(schema_bytes) <= MAX_CAPTURE_BYTES and _digest(schema_bytes) == SCHEMA_SHA256, "preview_schema_identity_invalid")
        schema = json.loads(schema_bytes)
        _require(schema.get("$id") == SCHEMA_ID, "preview_schema_identity_invalid")
        nodes = [schema]
        while nodes:
            node = nodes.pop()
            if type(node) is dict:
                if "$ref" in node:
                    _require(type(node["$ref"]) is str and node["$ref"].startswith("#/$defs/"), "preview_schema_reference_invalid")
                nodes.extend(node.values())
            elif type(node) is list:
                nodes.extend(node)
        from jsonschema import Draft202012Validator
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(payload)
        _timestamp(payload["observed_at"])
    except GvcfPreviewBindingError:
        raise
    except Exception:
        raise GvcfPreviewBindingError("preview_schema_invalid") from None


def run_gvcf_preview_components(
    *,
    source_gvcf: bytes,
    input_profile,
    source_artifact_manifest,
    execution_profile,
    reference_profile,
    reference_identity: ReferenceIdentityResult,
    reference_fasta,
    bcftools_executable,
    expected_executor_sha256: str,
    observed_at: str,
    timeout_seconds: int = 600,
) -> GvcfPreviewRunResult:
    """Compose actual intake/preparation; refuse all partial canonical promotion."""
    result = GvcfPreviewRunResult()
    stages: list[str] = []
    try:
        _require(type(source_gvcf) is bytes and 0 < len(source_gvcf) <= MAX_INPUT_BYTES, "source_bytes_invalid")
        _require(type(timeout_seconds) is int and 1 <= timeout_seconds <= 600, "timeout_invalid")
        timestamp = _timestamp(observed_at)
        result = replace(
            result, source_gvcf=source_gvcf,
            input_profile_json=_capture(input_profile),
            source_artifact_manifest_json=_capture(source_artifact_manifest),
            execution_profile_json=_capture(execution_profile),
            reference_profile_json=_capture(reference_profile),
            reference_identity=reference_identity, expected_executor_sha256=expected_executor_sha256,
            observed_at=timestamp,
        )
        stages.append("bounded_inputs")
        profile, manifest, execution, reference = _profiles(result)
        stages.append("profiles")
        intake = validate_gvcf_bytes(source_gvcf, profile)
        result = replace(result, intake=intake)
        _check_intake(source_gvcf, profile, intake)
        stages.append("intake")
        artifact = prepare_gvcf_artifacts(
            source_gvcf, input_profile=profile, source_artifact_manifest=manifest,
            execution_profile=execution, reference_profile=reference,
            reference_identity=reference_identity, reference_fasta=reference_fasta,
            bcftools_executable=bcftools_executable,
            expected_executor_sha256=expected_executor_sha256, timeout_seconds=timeout_seconds,
        )
        result = replace(result, artifact=artifact)
        _require(type(artifact) is GvcfArtifactResult and artifact.passed, "artifact_preparation_failed")
        _check_artifact(artifact, source_gvcf, intake, manifest, expected_executor_sha256)
        stages.append("artifact")
        model = _model(intake, artifact, _input_bindings(result))
        stages.append("canonical_model")
        evidence = _evidence(model, artifact, timestamp)
        stages.append("evidence_snapshot")
        interpretation = _interpretation(model, evidence)
        stages.append("interpretation")
        candidate = replace(
            result, status="PASS", completed_stages=COMPLETED_STAGES,
            canonical_model=model, evidence_snapshot=evidence, interpretation=interpretation,
        )
        validate_gvcf_preview_export(candidate.to_dict())
        return candidate
    except GvcfPreviewBindingError as error:
        return replace(
            result, status="FAIL", errors=(str(error),), completed_stages=tuple(stages),
            canonical_model=None, evidence_snapshot=None, interpretation=None,
        )
    except (OSError, ValueError, TypeError, RecursionError):
        return replace(
            result, status="FAIL", errors=("preview_composition_failed",),
            completed_stages=tuple(stages),
            canonical_model=None, evidence_snapshot=None, interpretation=None,
        )
