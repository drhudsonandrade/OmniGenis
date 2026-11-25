"""Synthetic projection tests; normalization execution is outside this unit boundary."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from reporting.bindings import ReportSourceIdentity
from test_canonical_genomic_model_capability import normalization_result
from test_evidence_snapshot_capability import metadata

ROOT = Path(__file__).resolve().parents[1]


def load_json(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def load_projection(testcase):
    name = "reporting.canonical_interpretation_projection"
    if importlib.util.find_spec(name) is None:
        testcase.fail("canonical interpretation report projection is missing")
    return importlib.import_module(name)


def synthetic_run(*, empty=False, conflict=False, gene_count=0, multiple=False):
    """Run actual pure components around an explicit synthetic normalizer fixture."""
    runner = importlib.import_module("omnigenis.small_variant_run")
    source = (ROOT / "tests/fixtures/canonical_genomic_model/source-normalized.vcf").read_bytes()
    source = b"\n".join(source.splitlines()[:-1]) + b"\n"
    xml = (ROOT / ("tests/fixtures/evidence_snapshot/conflict-vcv.xml" if conflict
                   else "tests/fixtures/evidence_snapshot/simple-vcv.xml")).read_bytes()
    if conflict:
        source = source.replace(b"NC_000001.11", b"NC_000016.10")
        source = source.replace(b"10471\tidA\tC\tG", b"88738562\tidA\tG\tC")
    if empty:
        source = b"\n".join(line for line in source.splitlines() if line.startswith(b"#")) + b"\n"
    if gene_count:
        root = ET.fromstring(xml)
        allele = root.find("./VariationArchive/ClassifiedRecord/SimpleAllele")
        genes = ET.SubElement(allele, "GeneList")
        for ordinal in range(gene_count):
            ET.SubElement(genes, "Gene", {"Symbol": f"SYNTHETIC{ordinal}",
                "GeneID": str(1000 + ordinal), "Source": "submitted",
                "RelationshipType": "within multiple genes by overlap"})
        xml = ET.tostring(root, encoding="utf-8")
    if multiple:
        source = source.replace(b"##FORMAT=<ID=GT", b"##contig=<ID=NC_000016.10,length=90338345>\n##FORMAT=<ID=GT", 1)
        source += b"NC_000016.10\t88738562\tidB\tG\tC\t60\tPASS\t.\tGT:DP:GQ:AD\t0/1:30:60:15,15\n"
        other_root = ET.fromstring((ROOT / "tests/fixtures/evidence_snapshot/conflict-vcv.xml").read_bytes())
        other_allele = other_root.find("./VariationArchive/ClassifiedRecord/SimpleAllele")
        other_genes = ET.SubElement(other_allele, "GeneList")
        ET.SubElement(other_genes, "Gene", {"Symbol": "SECOND_SOURCE", "Source": "submitted"})
        other_xml = ET.tostring(other_root, encoding="utf-8")
    normalized = normalization_result(source)
    # This deliberate unit fixture is not a full-reference execution attestation.
    from omnigenis.capabilities.canonical_variant_identity import canonicalize_normalized_variants
    from test_canonical_genomic_model_capability import reference_identity
    variants = canonicalize_normalized_variants(normalized, reference_identity())
    evidence = [] if empty else [{
        "canonical_variant_id": variants.variants[0].canonical_variant_id,
        "metadata": metadata(xml, "VCV000002002.4" if conflict else "VCV000001001.3"),
        "xml_bytes": xml,
    }]
    if multiple:
        evidence.append({"canonical_variant_id": variants.variants[1].canonical_variant_id,
                         "metadata": metadata(other_xml, "VCV000002002.4"),
                         "xml_bytes": other_xml})
    digest = hashlib.sha256(source).hexdigest()
    input_profile = {"schema_version": "1.0.0", "input_id": "synthetic-input",
                     "input_kind": "VCF", "media_type": "text/vcf",
                     "content_sha256": digest, "size_bytes": len(source)}
    execution = {"schema_version": "1.0.0",
                 "execution_profile_id": "single-sample-small-variant-run-v1",
                 "executor_id": "omnigenis.local-direct", "executor_version": "1.0.0",
                 "deterministic": True}
    artifact = {"schema_version": "1.0.0", "artifact_id": "synthetic-artifact",
                "artifact_kind": "RAW_INPUT", "media_type": "text/vcf",
                "sha256": digest, "size_bytes": len(source),
                "producer_execution_profile_id": execution["execution_profile_id"],
                "parent_artifact_ids": []}
    with patch.object(runner, "normalize_small_variants", return_value=normalized):
        result = runner.run_small_variant_components(
            source_vcf=source, input_profile=input_profile, execution_profile=execution,
            source_artifact_manifest=artifact,
            reference_profile=load_json("resources/reference-profiles/grch38-p14-ncbi-refseq-autosomal-v1.json"),
            reference_resource_bom=load_json("resources/scientific-resource-bom/grch38-p14-ncbi-refseq-autosomal-v1.json"),
            reference_observation=load_json("tests/fixtures/reference_identity/ncbi-grch38-p14.observation.json"),
            source_registry=load_json("resources/source-registry.v1.json"),
            reference_fasta="synthetic-not-opened.fa",
            bcftools_executable="synthetic-not-invoked",
            expected_executor_sha256=normalized.executor_sha256,
            evidence_items=evidence, as_of_date="2026-09-24",
            hgnc_approved_tsv=b"", hgnc_withdrawn_tsv=b"",
            hgnc_resource_bom=load_json("resources/scientific-resource-bom/hgnc-gene-identity-2026-09-18-v1.json"),
        )
    if not result.passed:
        raise AssertionError((result.failed_stage, result.errors))
    return result


def source_identity(sample_id="SAMPLE_A"):
    return ReportSourceIdentity("synthetic", "case-1", "subject-1", sample_id,
                                "analysis-1", "1", None)


class CanonicalInterpretationProjectionTests(unittest.TestCase):
    def setUp(self):
        self.sut = load_projection(self)

    def project(self, run, identity=None):
        return self.sut.project_canonical_interpretation(
            run=run, identity=identity or source_identity())

    def test_preserves_complete_source_interpretation_and_technical_observations(self):
        run = synthetic_run()
        projection = self.project(run)
        self.assertEqual(set(projection), {"identity", "items", "conflicts", "limitations", "provenance"})
        self.assertEqual(projection["identity"], source_identity().to_dict())
        self.assertEqual(projection["items"], [item.to_dict() for item in run.interpretation.items])
        self.assertEqual(projection["conflicts"], [])
        record = projection["provenance"][0]
        self.assertEqual(record["canonical_model"], run.canonical_model.to_dict())
        self.assertEqual(record["evidence_snapshot"], run.evidence_snapshot.to_dict())
        self.assertEqual(record["qc"], run.qc.to_dict())
        self.assertEqual(record["normalization"], run.normalization.to_dict())
        self.assertEqual(record["interpretation"], run.interpretation.to_dict())
        self.assertEqual(record["as_of_date"], "2026-09-24")
        encoded = json.dumps(projection, sort_keys=True)
        self.assertNotIn("synthetic-not-opened", encoded)
        self.assertNotIn("<?xml", encoded)
        self.assertNotIn("##fileformat", encoded)

    def test_conflict_is_preserved_with_each_source_submission(self):
        run = synthetic_run(conflict=True)
        projection = self.project(run)
        self.assertEqual(projection["conflicts"], projection["items"])
        self.assertTrue(projection["items"][0]["source_conflict"])
        self.assertEqual(len(projection["provenance"][0]["evidence_snapshot"]
                             ["canonical_payload"]["items"][0]["submissions"]), 2)

    def test_missing_gene_ids_keep_all_original_source_entries(self):
        run = synthetic_run(gene_count=2)
        projection = self.project(run)
        references = projection["provenance"][0]["source_gene_references"]
        self.assertEqual(len(references), 2)
        self.assertEqual([ref["source_gene_ordinal"] for ref in references], [1, 2])
        self.assertEqual([ref["source_attributes"]["Symbol"] for ref in references],
                         ["SYNTHETIC0", "SYNTHETIC1"])
        self.assertTrue(all(ref["hgnc_id"] is None and ref["resolution"] is None
                            for ref in references))

    def test_source_gene_omission_and_reordering_are_rejected(self):
        run = synthetic_run(gene_count=2)
        for references in (run.source_gene_references[:1], tuple(reversed(run.source_gene_references))):
            with self.subTest(references=references):
                with self.assertRaises(self.sut.ReportProjectionError):
                    self.project(replace(run, source_gene_references=references))

    def test_source_gene_attribute_tampering_is_rejected(self):
        run = synthetic_run(gene_count=1)
        ref = replace(run.source_gene_references[0], source="calculated")
        with self.assertRaises(self.sut.ReportProjectionError):
            self.project(replace(run, source_gene_references=(ref,)))

    def test_empty_verified_run_retains_identity_provenance_and_limitations(self):
        projection = self.project(synthetic_run(empty=True))
        self.assertEqual(projection["items"], [])
        self.assertEqual(projection["conflicts"], [])
        self.assertTrue(projection["identity"])
        self.assertTrue(projection["provenance"])
        self.assertTrue(projection["limitations"])
        self.assertIn("callability", json.dumps(projection).lower())

    def test_sample_identity_mismatch_is_rejected(self):
        with self.assertRaises(self.sut.ReportProjectionError):
            self.project(synthetic_run(), source_identity("OTHER_SAMPLE"))

    def test_failed_run_is_not_projected(self):
        run = synthetic_run()
        with self.assertRaises(self.sut.ReportProjectionError):
            self.project(replace(run, status="FAILED", errors=("synthetic_failure",)))

    def test_replaced_source_bytes_are_rejected(self):
        run = synthetic_run()
        with self.assertRaises(self.sut.ReportProjectionError):
            self.project(replace(run, source_vcf=run.source_vcf + b"\n"))

    def test_replaced_input_digest_is_rejected(self):
        run = synthetic_run()
        altered = dict(run.input_profile)
        altered["content_sha256"] = "0" * 64
        with self.assertRaises(self.sut.ReportProjectionError):
            self.project(replace(run, input_profile=altered))

    def test_canonical_model_tampering_is_rejected(self):
        run = synthetic_run()
        variant = replace(run.canonical_model.variants[0], depth=999)
        with self.assertRaises(self.sut.ReportProjectionError):
            self.project(replace(run, canonical_model=replace(run.canonical_model, variants=(variant,))))

    def test_evidence_and_interpretation_tampering_are_rejected(self):
        run = synthetic_run()
        altered_evidence = replace(run.evidence_snapshot.items[0], aggregate_classification="Benign")
        altered_interpretation = replace(run.interpretation.items[0], source_classification="Benign")
        for changed in (
            replace(run, evidence_snapshot=replace(run.evidence_snapshot, items=(altered_evidence,))),
            replace(run, interpretation=replace(run.interpretation, items=(altered_interpretation,))),
            replace(run, as_of_date="2026-09-25"),
        ):
            with self.subTest(field=changed):
                with self.assertRaises(self.sut.ReportProjectionError):
                    self.project(changed)

    def test_replaced_evidence_bytes_are_rejected(self):
        run = synthetic_run()
        binding = replace(run.evidence_inputs[0], xml_bytes=run.evidence_inputs[0].xml_bytes + b"\n")
        with self.assertRaises(self.sut.ReportProjectionError):
            self.project(replace(run, evidence_inputs=(binding,)))

    def test_evidence_input_order_does_not_lose_or_reorder_source_genes(self):
        run = synthetic_run(gene_count=1, multiple=True)
        reordered = replace(run, evidence_inputs=tuple(reversed(run.evidence_inputs)))
        self.assertEqual(self.project(reordered), self.project(run))

    def test_execution_and_input_profile_tampering_are_rejected(self):
        run = synthetic_run()
        changed_profiles = [
            replace(run, execution_profile={"execution_profile_id": "foreign-profile", "deterministic": False}),
            replace(run, execution_profile={**dict(run.execution_profile), "deterministic": 1}),
            replace(run, input_profile={**dict(run.input_profile), "input_kind": "OTHER"}),
            replace(run, input_profile={**dict(run.input_profile), "schema_version": "99"}),
            replace(run, source_artifact_manifest={**dict(run.source_artifact_manifest),
                    "producer_execution_profile_id": "other-profile"}),
        ]
        for changed in changed_profiles:
            with self.subTest(changed=changed):
                with self.assertRaises(self.sut.ReportProjectionError):
                    self.project(changed)

    def test_incomplete_stage_record_is_rejected(self):
        run = synthetic_run()
        with self.assertRaises(self.sut.ReportProjectionError):
            self.project(replace(run, completed_stages=()))

    def test_missing_hgnc_limitation_is_derived_from_verified_source_entries(self):
        run = synthetic_run(gene_count=1)
        projected = self.project(replace(run, limitations=()))
        self.assertTrue(any("HGNC" in value and "unavailable" in value
                            for value in projected["limitations"]))

    def test_hgnc_metadata_is_rebound_to_actual_retained_resource_bytes(self):
        from test_small_variant_run import SyntheticRunFixture
        runner = importlib.import_module("omnigenis.small_variant_run")
        with SyntheticRunFixture() as fixture:
            run = runner.run_small_variant_components(**fixture.arguments())
            self.assertTrue(run.passed, (run.failed_stage, run.errors))
            identity = source_identity("SYNTHETIC-001")
            projected = self.project(run, identity)
            self.assertEqual(projected["provenance"][0]["source_gene_references"][0]
                             ["resolution"]["canonical_payload"]["gene"]["approved_symbol"], "GENEA")
            reference = run.source_gene_references[0]
            for changes in ({"resource_snapshot_id": "foreign-snapshot"},
                            {"resource_bundle_sha256": "0" * 64},
                            {"approved_symbol": "UNRELATED"},
                            {"approved_name": "Unrelated name"}):
                gene = replace(reference.resolution.gene, **changes)
                altered_reference = replace(reference, resolution=replace(reference.resolution, gene=gene))
                changed = replace(run, source_gene_references=(altered_reference,) + run.source_gene_references[1:])
                with self.subTest(changes=changes):
                    with self.assertRaises(self.sut.ReportProjectionError):
                        self.project(changed, identity)

    def test_hgnc_resource_removal_or_byte_changes_are_rejected(self):
        from test_small_variant_run import SyntheticRunFixture
        runner = importlib.import_module("omnigenis.small_variant_run")
        with SyntheticRunFixture() as fixture:
            run = runner.run_small_variant_components(**fixture.arguments())
            self.assertTrue(run.passed, (run.failed_stage, run.errors))
            binding = run.gene_resource_binding
            changed_bindings = (
                None,
                replace(binding, approved_tsv=binding.approved_tsv + b"\n"),
                replace(binding, withdrawn_tsv=binding.withdrawn_tsv + b"\n"),
                replace(binding, resource_bom={**dict(binding.resource_bom), "bundle_sha256": "0" * 64}),
            )
            for changed in changed_bindings:
                with self.subTest(binding=changed):
                    with self.assertRaises(self.sut.ReportProjectionError):
                        self.project(replace(run, gene_resource_binding=changed), source_identity("SYNTHETIC-001"))

    def test_projection_is_detached_and_deterministic(self):
        run = synthetic_run()
        first = self.project(run)
        second = self.project(run)
        self.assertEqual(first, second)
        first["items"][0]["source_classification"] = "changed"
        first["provenance"][0]["canonical_model"]["canonical_payload"]["variants"].clear()
        self.assertEqual(self.project(run), second)

    def test_compiler_and_primary_renderer_consume_actual_projection(self):
        from reporting.compiler import compile_report_pack
        from reporting.viewmodel import build_report_view_model_and_release_bundle
        from reporting.presentation import build_presentation_ir
        from reporting.renderer import render_primary
        projection = self.project(synthetic_run(conflict=True))
        compiled = compile_report_pack(
            report_id="e1a-small-variant-report",
            catalog=load_json("reporting/catalog.v1.json"),
            section_contracts=load_json("reporting/sections/e1a-small-variant-report.v1.json"),
            intended_use=load_json("reporting/intended-use/e1a-small-variant-report.v1.json"),
            report_capability_manifest=load_json("reporting/capabilities/e1a-small-variant-report.v1.json"),
            canonical_interpretation=projection,
        )
        bundle = build_report_view_model_and_release_bundle(
            compiled_report_pack=compiled, artifact_ids=["synthetic-interpretation"])
        presentation = build_presentation_ir(report_view_model=bundle.view_model)
        rendered = render_primary(presentation_ir=presentation.to_dict()["presentation_ir"],
                                  renderer_id="canonical-text-v1", lifecycle_profile="preview")
        self.assertTrue(rendered.passed, rendered.errors)
        self.assertIn("Conflicting classifications of pathogenicity", rendered.artifact_text)
        self.assertIn("SCV000002001", rendered.artifact_text)
        self.assertIn("SCV000002002", rendered.artifact_text)
        self.assertIn("NOT_PERFORMED", rendered.artifact_text)
        self.assertEqual(rendered.artifact_sha256,
                         hashlib.sha256(rendered.artifact_text.encode("utf-8")).hexdigest())

    def test_untyped_run_or_identity_is_rejected(self):
        with self.assertRaises(self.sut.ReportProjectionError):
            self.project({"status": "COMPLETED"})
        with self.assertRaises(self.sut.ReportProjectionError):
            self.sut.project_canonical_interpretation(run=synthetic_run(),
                                                      identity=source_identity().to_dict())


if __name__ == "__main__":
    unittest.main()
