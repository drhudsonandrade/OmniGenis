"""Pure report projection of a completed, source-bound small-variant run.

This boundary rechecks deterministic in-memory bindings; it does not rerun the
external normalizer, attest execution origin, infer ownership, or authorize a
clinical release. Callers must retain the actual runner result and obtain the
explicit report identity through authenticated upstream resolution. Source XML
and VCF bytes remain in the run, never in the reporting mapping.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
import hashlib
import json
import math
import xml.etree.ElementTree as ET

from omnigenis.capabilities.artifact_gate import verify_artifact_bytes
from omnigenis.capabilities.canonical_genomic_model import build_canonical_genomic_model
from omnigenis.capabilities.canonical_interpretation import build_canonical_interpretation_object
from omnigenis.capabilities.canonical_variant_identity import canonicalize_normalized_variants
from omnigenis.capabilities.evidence_snapshot import build_minimum_evidence_snapshot
from omnigenis.capabilities.gene_identity import GeneIdentityResult, resolve_gene_identity
from omnigenis.capabilities.vcf_intake import validate_vcf_bytes
from omnigenis.capabilities.vcf_qc import observe_vcf_qc
from omnigenis.small_variant_run import (
    EvidenceInputBinding,
    GeneResourceBinding,
    PROFILE_ID,
    SmallVariantRunResult,
    SourceGeneReference,
)
from reporting.bindings import ReportSourceIdentity


class ReportProjectionError(ValueError):
    """Fixed diagnostic without source payloads or identity values."""


def _plain(value):
    """Detach immutable runner JSON into ordinary JSON containers."""
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise ReportProjectionError("run_binding_invalid")
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ReportProjectionError("run_binding_invalid")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReportProjectionError("evidence_metadata_invalid")
        result[key] = value
    return result


def _constant(_value):
    raise ReportProjectionError("evidence_metadata_invalid")


def _equal(actual, expected, code):
    """Compare rebuilt structures without numeric or nested type coercion."""
    if type(actual) is not type(expected):
        raise ReportProjectionError(code)
    if is_dataclass(expected) and not isinstance(expected, type):
        for field in fields(expected):
            _equal(getattr(actual, field.name), getattr(expected, field.name), code)
    elif isinstance(expected, Mapping):
        if len(actual) != len(expected):
            raise ReportProjectionError(code)
        missing = object()
        for key, value in expected.items():
            actual_key = next((candidate for candidate in actual
                               if type(candidate) is type(key) and candidate == key), missing)
            if actual_key is missing:
                raise ReportProjectionError(code)
            _equal(actual_key, key, code)
            _equal(actual[actual_key], value, code)
    elif isinstance(expected, (tuple, list)):
        if len(actual) != len(expected):
            raise ReportProjectionError(code)
        for item, reference in zip(actual, expected, strict=True):
            _equal(item, reference, code)
    elif actual != expected:
        raise ReportProjectionError(code)


def _children(element, name):
    return [child for child in element if child.tag.rsplit("}", 1)[-1] == name]


def _source_genes(run):
    """Rebind every retained source gene to its original XML position and attributes."""
    expected = []
    by_variant = {binding.canonical_variant_id: binding for binding in run.evidence_inputs}
    for item in run.evidence_snapshot.items:
        binding = by_variant[item.canonical_variant_id]
        root = ET.fromstring(binding.xml_bytes)
        archives = [node for node in root.iter()
                    if node.tag.rsplit("}", 1)[-1] == "VariationArchive"]
        if len(archives) != 1:
            raise ReportProjectionError("source_gene_binding_mismatch")
        for archive in archives:
            for record in _children(archive, "ClassifiedRecord"):
                for allele in _children(record, "SimpleAllele"):
                    ordinal = 0
                    for genes in _children(allele, "GeneList"):
                        for gene in _children(genes, "Gene"):
                            ordinal += 1
                            attributes = tuple(sorted(gene.attrib.items()))
                            expected.append((binding.canonical_variant_id,
                                archive.attrib.get("Accession"),
                                int(archive.attrib["Version"]),
                                hashlib.sha256(binding.xml_bytes).hexdigest(),
                                ordinal, attributes))
    if len(expected) != len(run.source_gene_references):
        raise ReportProjectionError("source_gene_binding_mismatch")
    projected = []
    resolutions = {}
    for original, reference in zip(expected, run.source_gene_references, strict=True):
        if (type(reference) is not SourceGeneReference
                or type(reference.source_gene_ordinal) is not int
                or type(reference.source_vcv_version) is not int):
            raise ReportProjectionError("source_gene_binding_mismatch")
        actual = (reference.canonical_variant_id, reference.source_vcv_accession,
                  reference.source_vcv_version, reference.source_xml_sha256,
                  reference.source_gene_ordinal, tuple(sorted(reference.source_attributes)))
        if actual != original:
            raise ReportProjectionError("source_gene_binding_mismatch")
        attributes = dict(original[-1])
        if (reference.hgnc_id != attributes.get("HGNC_ID")
                or reference.source != attributes.get("Source")
                or reference.relationship_type != attributes.get("RelationshipType")):
            raise ReportProjectionError("source_gene_binding_mismatch")
        resolution = reference.resolution
        if reference.hgnc_id is None:
            if resolution is not None:
                raise ReportProjectionError("source_gene_identity_mismatch")
        else:
            resource = run.gene_resource_binding
            if (type(resource) is not GeneResourceBinding
                    or type(resolution) is not GeneIdentityResult or not resolution.passed):
                raise ReportProjectionError("source_gene_identity_mismatch")
            if reference.hgnc_id not in resolutions:
                resolutions[reference.hgnc_id] = resolve_gene_identity(
                    reference.hgnc_id, approved_tsv=resource.approved_tsv,
                    withdrawn_tsv=resource.withdrawn_tsv,
                    source_registry=_plain(run.source_registry),
                    resource_bom=_plain(resource.resource_bom))
            rebuilt = resolutions[reference.hgnc_id]
            if (not rebuilt.passed
                    or rebuilt.gene.canonical_gene_id != reference.hgnc_id):
                raise ReportProjectionError("source_gene_identity_mismatch")
            _equal(resolution, rebuilt, "source_gene_identity_mismatch")
        projected.append({
            "canonical_variant_id": reference.canonical_variant_id,
            "source_vcv_accession": reference.source_vcv_accession,
            "source_vcv_version": reference.source_vcv_version,
            "source_xml_sha256": reference.source_xml_sha256,
            "source_gene_ordinal": reference.source_gene_ordinal,
            "hgnc_id": reference.hgnc_id,
            "source": reference.source,
            "relationship_type": reference.relationship_type,
            "source_attributes": attributes,
            "resolution": resolution.to_dict() if resolution is not None else None,
        })
    return projected


def _rebind(run):
    if type(run.source_vcf) is not bytes:
        raise ReportProjectionError("source_binding_mismatch")
    input_profile = _plain(run.input_profile)
    manifest = _plain(run.source_artifact_manifest)
    execution = _plain(run.execution_profile)
    expected_execution = {
        "schema_version": "1.0.0", "execution_profile_id": PROFILE_ID,
        "executor_id": "omnigenis.local-direct", "executor_version": "1.0.0",
        "deterministic": True,
    }
    if (set(execution) != set(expected_execution)
            or any(type(execution[key]) is not type(value) or execution[key] != value
                   for key, value in expected_execution.items())
            or manifest.get("producer_execution_profile_id") != PROFILE_ID):
        raise ReportProjectionError("execution_profile_binding_mismatch")
    digest = hashlib.sha256(run.source_vcf).hexdigest()
    if (set(input_profile) != {"schema_version", "input_id", "input_kind", "media_type",
                              "content_sha256", "size_bytes"}
            or input_profile.get("schema_version") != "1.0.0"
            or type(input_profile.get("input_id")) is not str or not input_profile["input_id"]
            or input_profile.get("input_kind") != "VCF"
            or input_profile.get("media_type") != "text/vcf"
            or manifest.get("artifact_kind") != "RAW_INPUT"
            or manifest.get("media_type") != "text/vcf"
            or input_profile.get("content_sha256") != digest
            or type(input_profile.get("size_bytes")) is not int
            or input_profile["size_bytes"] != len(run.source_vcf)):
        raise ReportProjectionError("source_binding_mismatch")
    intake = validate_vcf_bytes(run.source_vcf)
    _equal(run.intake, intake, "intake_binding_mismatch")
    artifact = verify_artifact_bytes(manifest, run.source_vcf)
    _equal(run.artifact_gate, artifact, "artifact_binding_mismatch")
    qc = observe_vcf_qc(run.source_vcf)
    _equal(run.qc, qc, "qc_binding_mismatch")
    variants = canonicalize_normalized_variants(run.normalization, run.reference_identity)
    _equal(run.canonical_variants, variants, "canonical_variant_binding_mismatch")
    model = build_canonical_genomic_model(
        source_vcf=run.source_vcf, source_artifact_manifest=manifest,
        source_artifact_gate=artifact, source_qc=qc,
        reference_identity=run.reference_identity, normalization=run.normalization,
        canonical_variants=variants,
    )
    if not model.passed:
        raise ReportProjectionError("canonical_model_binding_mismatch")
    _equal(run.canonical_model, model, "canonical_model_binding_mismatch")
    inputs = []
    for binding in run.evidence_inputs:
        if (type(binding) is not EvidenceInputBinding
                or type(binding.metadata_json) is not bytes
                or type(binding.xml_bytes) is not bytes):
            raise ReportProjectionError("evidence_input_binding_mismatch")
        inputs.append({"canonical_variant_id": binding.canonical_variant_id,
                       "metadata": json.loads(binding.metadata_json, object_pairs_hook=_object,
                                              parse_constant=_constant),
                       "xml_bytes": binding.xml_bytes})
    snapshot = build_minimum_evidence_snapshot(canonical_model=model, evidence_items=inputs,
                                                source_registry=_plain(run.source_registry))
    if not snapshot.passed:
        raise ReportProjectionError("evidence_snapshot_binding_mismatch")
    _equal(run.evidence_snapshot, snapshot, "evidence_snapshot_binding_mismatch")
    interpretation = build_canonical_interpretation_object(
        canonical_model=model, evidence_snapshot=snapshot, as_of_date=run.as_of_date)
    if not interpretation.passed:
        raise ReportProjectionError("interpretation_binding_mismatch")
    _equal(run.interpretation, interpretation, "interpretation_binding_mismatch")
    return _source_genes(run)


def project_canonical_interpretation(
    *, run: SmallVariantRunResult, identity: ReportSourceIdentity,
) -> dict[str, object]:
    """Return the existing five-section compiler mapping after binding checks."""
    try:
        if type(run) is not SmallVariantRunResult or not run.passed:
            raise ReportProjectionError("completed_small_variant_run_required")
        if type(identity) is not ReportSourceIdentity:
            raise ReportProjectionError("report_source_identity_required")
        gene_references = _rebind(run)
        if identity.sample_id != run.canonical_model.sample_id:
            raise ReportProjectionError("report_sample_identity_mismatch")
        components = (run.qc, run.reference_identity, run.normalization,
                      run.canonical_variants, run.canonical_model,
                      run.evidence_snapshot, run.interpretation)
        if (type(run.limitations) is not tuple
                or any(type(detail) is not str for detail in run.limitations)):
            raise ReportProjectionError("run_binding_invalid")
        limitations = list(run.limitations)
        if not gene_references:
            limitations.append("No source gene entries are available in the validated evidence.")
        if any(reference["hgnc_id"] is None for reference in gene_references):
            limitations.append("An explicit HGNC identity is unavailable for one or more retained source gene entries.")
        for component in components:
            envelope = component.to_dict()
            limitations.extend(
                f"[{envelope['capability_id']}] {detail}"
                for detail in envelope["limitations"]
            )
        limitations.append(
            "Source GeneList relationships are retained as source attributions; "
            "local variant-to-gene interval mapping is not performed."
        )
        limitations.append(
            "Technical preview only; clinical assessment and release authorization "
            "are not established by this projection."
        )
        items = [item.to_dict() for item in run.interpretation.items]
        return {
            "identity": identity.to_dict(),
            "items": items,
            "conflicts": [item.to_dict() for item in run.interpretation.items
                          if item.source_conflict],
            "limitations": list(dict.fromkeys(limitations)),
            "provenance": [{
                "input_profile": _plain(run.input_profile),
                "execution_profile": _plain(run.execution_profile),
                "source_artifact_manifest": _plain(run.source_artifact_manifest),
                "as_of_date": run.as_of_date,
                "completed_stages": list(run.completed_stages),
                "qc": run.qc.to_dict(),
                "reference_identity": run.reference_identity.to_dict(),
                "normalization": run.normalization.to_dict(),
                "canonical_variants": run.canonical_variants.to_dict(),
                "canonical_model": run.canonical_model.to_dict(),
                "evidence_snapshot": run.evidence_snapshot.to_dict(),
                "interpretation": run.interpretation.to_dict(),
                "source_gene_references": gene_references,
            }],
        }
    except ReportProjectionError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError, ET.ParseError):
        raise ReportProjectionError("run_binding_invalid") from None
