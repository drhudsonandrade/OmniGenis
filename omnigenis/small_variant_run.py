"""Opt-in deterministic composition for one narrowly qualified VCF profile.

This module retains actual capability results and exact supplied evidence bytes.
A completed run establishes this composition only; it activates no default
pipeline, clinical workflow, product release, or inferred gene annotation.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date
import hashlib
import json
import os
import re
from types import MappingProxyType
import xml.etree.ElementTree as ET

from .capabilities.artifact_gate import ArtifactGateResult, verify_artifact_bytes
from .capabilities.canonical_genomic_model import CanonicalGenomicModelResult, build_canonical_genomic_model
from .capabilities.canonical_interpretation import CanonicalInterpretationObjectResult, build_canonical_interpretation_object
from .capabilities.canonical_variant_identity import CanonicalVariantIdentityResult, canonicalize_normalized_variants
from .capabilities.evidence_snapshot import MinimumEvidenceSnapshotResult, build_minimum_evidence_snapshot
from .capabilities.gene_identity import GeneIdentityResult, resolve_gene_identity
from .capabilities.reference_identity import ReferenceIdentityResult, verify_reference_identity
from .capabilities.variant_normalization import (
    REFERENCE_AUTOSOMAL_REFSEQ_ACCESSIONS, VariantNormalizationResult,
    normalize_small_variants,
)
from .capabilities.vcf_intake import VcfIntakeResult, validate_vcf_bytes
from .capabilities.vcf_qc import VcfQcObservationResult, observe_vcf_qc

PROFILE_ID = "single-sample-small-variant-run-v1"
MAX_INPUT_BYTES = 1_048_576
MAX_INPUT_RECORDS = 32
MAX_EVIDENCE_BYTES = 20_971_520
MAX_SOURCE_GENE_REFERENCES = 64
MAX_NORMALIZATION_TIMEOUT_SECONDS = 600
COMPLETED_STAGES = (
    "input_profile", "execution_profile", "as_of_date", "runtime_profile",
    "evidence_inputs", "resource_metadata", "intake", "artifact", "qc",
    "supported_profile", "reference", "normalization", "canonical_variants",
    "canonical_model", "evidence", "source_genes", "interpretation",
)
_INPUT_KEYS = frozenset({
    "schema_version", "input_id", "input_kind", "media_type",
    "content_sha256", "size_bytes",
})
_EXECUTION_PROFILE = {
    "schema_version": "1.0.0",
    "execution_profile_id": PROFILE_ID,
    "executor_id": "omnigenis.local-direct",
    "executor_version": "1.0.0",
    "deterministic": True,
}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DIPLOID_GT = re.compile(r"(?:[0-9]+|\.)[/|](?:[0-9]+|\.)\Z")
_SEQUENCE = re.compile(r"[ACGT]+\Z")
_LIMITATIONS = (
    "This opt-in composition does not activate a default pipeline, clinical workflow, or product release.",
    "The qualified original-representation domain is diploid autosomal SNVs and anchored single-base insertions/deletions; other representations remain unsupported.",
    "Input, record, evidence, and source-gene budgets are engineering qualification limits, not scientific variant definitions.",
    "Source gene references are retained associations from ClinVar; no interval annotation, transcript consequence, or gene selection is inferred.",
)


def _freeze(value: object) -> object:
    if type(value) is dict:
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze(item) for item in value)
    return value


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _capture_mapping(value: object) -> dict:
    if type(value) is not dict:
        raise ValueError("native_mapping_required")
    return json.loads(_json_bytes(value))


@dataclass(frozen=True, slots=True, repr=False)
class EvidenceInputBinding:
    """An immutable capture of a supplied record; capture is not verification."""

    canonical_variant_id: str
    metadata_json: bytes
    xml_bytes: bytes


@dataclass(frozen=True, slots=True, repr=False)
class GeneResourceBinding:
    """Exact immutable resources used for explicit source HGNC resolution."""

    approved_tsv: bytes
    withdrawn_tsv: bytes
    resource_bom: Mapping[str, object]


@dataclass(frozen=True, slots=True, repr=False)
class SourceGeneReference:
    """One source entry, with a one-based ordinal within that VCV GeneList."""

    canonical_variant_id: str
    source_vcv_accession: str
    source_vcv_version: int
    source_xml_sha256: str
    source_gene_ordinal: int
    hgnc_id: str | None
    source: str | None
    relationship_type: str | None
    source_attributes: tuple[tuple[str, str], ...]
    resolution: GeneIdentityResult | None = None


@dataclass(frozen=True, slots=True, repr=False)
class SmallVariantRunResult:
    """Actual stage results and immutable request bindings, without runtime paths."""

    status: str
    failed_stage: str | None
    errors: tuple[str, ...]
    completed_stages: tuple[str, ...] = ()
    input_profile: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    execution_profile: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    source_artifact_manifest: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    source_registry: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    source_vcf: bytes | None = None
    as_of_date: str | None = None
    intake: VcfIntakeResult | None = None
    artifact_gate: ArtifactGateResult | None = None
    qc: VcfQcObservationResult | None = None
    reference_identity: ReferenceIdentityResult | None = None
    normalization: VariantNormalizationResult | None = None
    canonical_variants: CanonicalVariantIdentityResult | None = None
    canonical_model: CanonicalGenomicModelResult | None = None
    evidence_snapshot: MinimumEvidenceSnapshotResult | None = None
    interpretation: CanonicalInterpretationObjectResult | None = None
    evidence_inputs: tuple[EvidenceInputBinding, ...] = ()
    source_gene_references: tuple[SourceGeneReference, ...] = ()
    gene_resource_binding: GeneResourceBinding | None = None
    limitations: tuple[str, ...] = _LIMITATIONS

    @property
    def passed(self) -> bool:
        return (
            self.status == "COMPLETED" and self.failed_stage is None and not self.errors
            and self.completed_stages == COMPLETED_STAGES
            and type(self.intake) is VcfIntakeResult and self.intake.is_valid
            and type(self.artifact_gate) is ArtifactGateResult and self.artifact_gate.passed
            and type(self.qc) is VcfQcObservationResult and self.qc.input_valid and not self.qc.errors
            and type(self.reference_identity) is ReferenceIdentityResult and self.reference_identity.passed
            and type(self.normalization) is VariantNormalizationResult and self.normalization.passed
            and type(self.canonical_variants) is CanonicalVariantIdentityResult and self.canonical_variants.passed
            and type(self.canonical_model) is CanonicalGenomicModelResult and self.canonical_model.passed
            and type(self.evidence_snapshot) is MinimumEvidenceSnapshotResult and self.evidence_snapshot.passed
            and type(self.interpretation) is CanonicalInterpretationObjectResult and self.interpretation.passed
            and (type(self.gene_resource_binding) is GeneResourceBinding
                 if any(reference.hgnc_id is not None for reference in self.source_gene_references)
                 else self.gene_resource_binding is None)
            and all(reference.hgnc_id is None or (
                type(reference.resolution) is GeneIdentityResult and reference.resolution.passed
            ) for reference in self.source_gene_references)
        )


def _profile_refusal(data: bytes, record_count: int) -> str | None:
    if record_count > MAX_INPUT_RECORDS:
        return "input_record_budget_exceeded"
    allowed_contigs = frozenset(REFERENCE_AUTOSOMAL_REFSEQ_ACCESSIONS)
    for line in data.decode("utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if fields[0] not in allowed_contigs:
            return "contig_outside_supported_profile"
        keys, values = fields[8].split(":"), fields[9].split(":")
        if "GT" not in keys:
            return "diploid_gt_not_available"
        index = keys.index("GT")
        if index >= len(values) or _DIPLOID_GT.fullmatch(values[index]) is None:
            return "genotype_outside_diploid_profile"
        ref = fields[3]
        for alt in fields[4].split(","):
            if _SEQUENCE.fullmatch(ref) is None or _SEQUENCE.fullmatch(alt) is None:
                return "allele_outside_supported_profile"
            if ref == alt:
                return "allele_outside_supported_profile"
            if len(ref) == len(alt) == 1:
                continue
            if (len(ref), len(alt)) in {(1, 2), (2, 1)} and ref[0] == alt[0]:
                continue
            return "allele_outside_supported_profile"
    return None


def _source_gene_entries(
    snapshot: MinimumEvidenceSnapshotResult,
    bindings: tuple[EvidenceInputBinding, ...],
) -> tuple[SourceGeneReference, ...]:
    """Read only already validated, byte-bound ClinVar source XML."""
    by_variant = {item.canonical_variant_id: item for item in bindings}
    references: list[SourceGeneReference] = []
    for item in snapshot.items:
        binding = by_variant[item.canonical_variant_id]
        if hashlib.sha256(binding.xml_bytes).hexdigest() != item.metadata.result_sha256:
            raise ValueError("source_gene_xml_binding_mismatch")
        root = ET.fromstring(binding.xml_bytes)
        archives = [
            element for element in root.iter()
            if element.tag.rsplit("}", 1)[-1] == "VariationArchive"
        ]
        if len(archives) != 1:
            raise ValueError("source_gene_archive_not_unique")
        archive = archives[0]
        if (archive.get("Accession") != item.vcv_accession
                or archive.get("Version") != str(item.vcv_version)):
            raise ValueError("source_gene_archive_binding_mismatch")
        entries = [archive]
        for local_name in ("ClassifiedRecord", "SimpleAllele", "GeneList", "Gene"):
            entries = [
                child for parent in entries for child in parent
                if child.tag.rsplit("}", 1)[-1] == local_name
            ]
        if len(references) + len(entries) > MAX_SOURCE_GENE_REFERENCES:
            raise ValueError("source_gene_budget_exceeded")
        for ordinal, entry in enumerate(entries, start=1):
            references.append(SourceGeneReference(
                canonical_variant_id=item.canonical_variant_id,
                source_vcv_accession=item.vcv_accession,
                source_vcv_version=item.vcv_version,
                source_xml_sha256=item.metadata.result_sha256,
                source_gene_ordinal=ordinal,
                hgnc_id=entry.get("HGNC_ID"),
                source=entry.get("Source"),
                relationship_type=entry.get("RelationshipType"),
                source_attributes=tuple(sorted(entry.attrib.items())),
            ))
    return tuple(references)


def run_small_variant_components(
    *,
    source_vcf: bytes,
    input_profile: dict,
    execution_profile: dict,
    source_artifact_manifest: dict,
    reference_profile: dict,
    reference_resource_bom: dict,
    reference_observation: dict,
    source_registry: dict,
    reference_fasta: str | os.PathLike[str],
    bcftools_executable: str | os.PathLike[str],
    expected_executor_sha256: str,
    evidence_items: list | tuple,
    as_of_date: str,
    hgnc_approved_tsv: bytes,
    hgnc_withdrawn_tsv: bytes,
    hgnc_resource_bom: dict,
    timeout_seconds: int = 600,
) -> SmallVariantRunResult:
    """Run existing capabilities once, stopping at the first refusal.

    Runtime locations are consumed only by the existing normalizer and are not
    retained in the returned aggregate. Source metadata and evidence are captured
    before execution; a capture alone never represents a scientific attestation.
    """
    state: dict[str, object] = {}
    completed: list[str] = []
    stage = "input_profile"

    def finish(status: str, failed_stage: str | None, errors=()):
        return SmallVariantRunResult(
            status=status, failed_stage=failed_stage, errors=tuple(errors),
            completed_stages=tuple(completed), **state,
        )

    def refused(stage_result, fallback: str):
        errors = getattr(stage_result, "errors", ())
        if not errors:
            errors = tuple(
                rule.detail for rule in getattr(stage_result, "rules", ())
                if rule.status != "PASS"
            )
        return finish("BLOCKED", stage, errors or (fallback,))

    try:
        if type(source_vcf) is not bytes:
            return finish("INVALID_INPUT", stage, ("immutable_vcf_bytes_required",))
        if len(source_vcf) > MAX_INPUT_BYTES:
            return finish("UNSUPPORTED_PROFILE", stage, ("input_byte_budget_exceeded",))
        state["source_vcf"] = source_vcf
        captured_input = _capture_mapping(input_profile)
        captured_manifest = _capture_mapping(source_artifact_manifest)
        state["input_profile"] = _freeze(captured_input)
        state["source_artifact_manifest"] = _freeze(captured_manifest)
        if (
            frozenset(captured_input) != _INPUT_KEYS
            or captured_input.get("schema_version") != "1.0.0"
            or captured_input.get("input_kind") != "VCF"
            or captured_input.get("media_type") != "text/vcf"
            or type(captured_input.get("input_id")) is not str
            or not captured_input["input_id"]
            or type(captured_input.get("size_bytes")) is not int
            or captured_input["size_bytes"] != len(source_vcf)
            or type(captured_input.get("content_sha256")) is not str
            or _SHA256.fullmatch(captured_input["content_sha256"]) is None
            or captured_input["content_sha256"] != hashlib.sha256(source_vcf).hexdigest()
            or captured_manifest.get("artifact_kind") != "RAW_INPUT"
            or captured_manifest.get("media_type") != "text/vcf"
            or captured_manifest.get("sha256") != captured_input["content_sha256"]
            or type(captured_manifest.get("size_bytes")) is not int
            or captured_manifest["size_bytes"] != captured_input["size_bytes"]
            or captured_manifest.get("producer_execution_profile_id") != PROFILE_ID
        ):
            return finish("INVALID_INPUT", stage, ("input_artifact_profile_binding_invalid",))
        completed.append(stage)

        stage = "execution_profile"
        captured_execution = _capture_mapping(execution_profile)
        state["execution_profile"] = _freeze(captured_execution)
        if (
            frozenset(captured_execution) != frozenset(_EXECUTION_PROFILE)
            or any(type(captured_execution[key]) is not type(expected)
                   or captured_execution[key] != expected
                   for key, expected in _EXECUTION_PROFILE.items())
        ):
            return finish("UNSUPPORTED_PROFILE", stage, ("execution_profile_not_supported",))
        completed.append(stage)

        stage = "as_of_date"
        if type(as_of_date) is not str or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", as_of_date) is None:
            return finish("INVALID_INPUT", stage, ("as_of_date_invalid",))
        try:
            observed_date = date.fromisoformat(as_of_date)
        except ValueError:
            return finish("INVALID_INPUT", stage, ("as_of_date_invalid",))
        state["as_of_date"] = observed_date.isoformat()
        completed.append(stage)

        stage = "runtime_profile"
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= MAX_NORMALIZATION_TIMEOUT_SECONDS:
            return finish("UNSUPPORTED_PROFILE", stage, ("normalizer_timeout_outside_supported_profile",))
        completed.append(stage)

        stage = "evidence_inputs"
        if type(evidence_items) not in (list, tuple):
            return finish("INVALID_INPUT", stage, ("evidence_inputs_invalid",))
        bindings: list[EvidenceInputBinding] = []
        evidence_bytes = 0
        for item in evidence_items:
            if (
                type(item) is not dict
                or frozenset(item) != {"canonical_variant_id", "metadata", "xml_bytes"}
                or type(item["canonical_variant_id"]) is not str
                or not item["canonical_variant_id"]
                or type(item["xml_bytes"]) is not bytes
                or type(item["metadata"]) is not dict
            ):
                return finish("INVALID_INPUT", stage, ("evidence_inputs_invalid",))
            evidence_bytes += len(item["xml_bytes"])
            if evidence_bytes > MAX_EVIDENCE_BYTES:
                return finish("UNSUPPORTED_PROFILE", stage, ("evidence_byte_budget_exceeded",))
            bindings.append(EvidenceInputBinding(
                item["canonical_variant_id"], _json_bytes(item["metadata"]), item["xml_bytes"]))
        state["evidence_inputs"] = tuple(bindings)
        completed.append(stage)

        stage = "resource_metadata"
        registry = _capture_mapping(source_registry)
        state["source_registry"] = _freeze(registry)
        profile = _capture_mapping(reference_profile)
        reference_bom = _capture_mapping(reference_resource_bom)
        observation = _capture_mapping(reference_observation)
        gene_bom = _capture_mapping(hgnc_resource_bom)
        completed.append(stage)

        stage = "intake"
        state["intake"] = intake = validate_vcf_bytes(source_vcf)
        if not intake.is_valid:
            return refused(intake, "vcf_intake_refused")
        completed.append(stage)

        stage = "artifact"
        state["artifact_gate"] = artifact = verify_artifact_bytes(captured_manifest, source_vcf)
        if not artifact.passed:
            return refused(artifact, "source_artifact_refused")
        completed.append(stage)

        stage = "qc"
        state["qc"] = qc = observe_vcf_qc(source_vcf)
        if not qc.input_valid or qc.errors:
            return refused(qc, "source_qc_refused")
        completed.append(stage)

        stage = "supported_profile"
        profile_error = _profile_refusal(source_vcf, intake.record_count)
        if profile_error is not None:
            return finish("UNSUPPORTED_PROFILE", stage, (profile_error,))
        completed.append(stage)

        stage = "reference"
        state["reference_identity"] = reference = verify_reference_identity(
            profile, registry, reference_bom, observation)
        if not reference.passed:
            return refused(reference, "reference_identity_refused")
        completed.append(stage)

        stage = "normalization"
        state["normalization"] = normalization = normalize_small_variants(
            source_vcf, reference_fasta=reference_fasta, reference_identity=reference,
            bcftools_executable=bcftools_executable,
            expected_executor_sha256=expected_executor_sha256,
            timeout_seconds=timeout_seconds)
        if not normalization.passed:
            return refused(normalization, "normalization_refused")
        completed.append(stage)

        stage = "canonical_variants"
        state["canonical_variants"] = variants = canonicalize_normalized_variants(
            normalization, reference)
        if not variants.passed:
            return refused(variants, "canonical_variants_refused")
        completed.append(stage)

        stage = "canonical_model"
        state["canonical_model"] = model = build_canonical_genomic_model(
            source_vcf=source_vcf, source_artifact_manifest=captured_manifest,
            source_artifact_gate=artifact, source_qc=qc, reference_identity=reference,
            normalization=normalization, canonical_variants=variants)
        if not model.passed:
            return refused(model, "canonical_model_refused")
        completed.append(stage)

        stage = "evidence"
        state["evidence_snapshot"] = snapshot = build_minimum_evidence_snapshot(
            canonical_model=model,
            evidence_items=[{
                "canonical_variant_id": item.canonical_variant_id,
                "metadata": json.loads(item.metadata_json),
                "xml_bytes": item.xml_bytes,
            } for item in bindings],
            source_registry=registry)
        if not snapshot.passed:
            return refused(snapshot, "evidence_snapshot_refused")
        completed.append(stage)

        stage = "source_genes"
        try:
            references = list(_source_gene_entries(snapshot, tuple(bindings)))
        except ValueError as exc:
            if str(exc) == "source_gene_budget_exceeded":
                return finish("UNSUPPORTED_PROFILE", stage, ("source_gene_budget_exceeded",))
            return finish("BLOCKED", stage, ("source_gene_binding_invalid",))
        state["source_gene_references"] = tuple(references)
        limitations = list(_LIMITATIONS)
        if not references:
            limitations.append("No source gene entries are available in the validated evidence.")
        if any(reference.hgnc_id is None for reference in references):
            limitations.append("An explicit HGNC identity is unavailable for one or more retained source gene entries.")
        state["limitations"] = tuple(limitations)
        for index, reference_entry in enumerate(references):
            if reference_entry.hgnc_id is None:
                continue
            if ("gene_resource_binding" not in state
                    and isinstance(hgnc_approved_tsv, bytes)
                    and isinstance(hgnc_withdrawn_tsv, bytes)):
                state["gene_resource_binding"] = GeneResourceBinding(
                    approved_tsv=hgnc_approved_tsv,
                    withdrawn_tsv=hgnc_withdrawn_tsv,
                    resource_bom=_freeze(gene_bom),
                )
            resolution = resolve_gene_identity(
                reference_entry.hgnc_id, approved_tsv=hgnc_approved_tsv,
                withdrawn_tsv=hgnc_withdrawn_tsv, source_registry=registry,
                resource_bom=gene_bom)
            references[index] = replace(reference_entry, resolution=resolution)
            state["source_gene_references"] = tuple(references)
            if not resolution.passed:
                return refused(resolution, "explicit_hgnc_identity_not_verified")
        completed.append(stage)

        stage = "interpretation"
        state["interpretation"] = interpretation = build_canonical_interpretation_object(
            canonical_model=model, evidence_snapshot=snapshot, as_of_date=state["as_of_date"])
        if not interpretation.passed:
            return refused(interpretation, "interpretation_refused")
        completed.append(stage)
        return finish("COMPLETED", None)
    except Exception:
        # Preserve the failed stage without retaining exception payloads or paths.
        return finish("BLOCKED", stage, ("stage_execution_error",))
