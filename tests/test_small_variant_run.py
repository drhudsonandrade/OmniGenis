"""Tests for explicit local composition using synthetic resource substitutions.

The tiny reference and HGNC pins below exercise actual capability functions.
They do not qualify the production reference or establish a product release.
"""
from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PROFILE_ID = "single-sample-small-variant-run-v1"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def load_json(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def load_sut(testcase):
    name = "omnigenis.small_variant_run"
    if importlib.util.find_spec(name) is None:
        testcase.fail("The opt-in small-variant runner is not implemented")
    module = importlib.import_module(name)
    for attr in ("run_small_variant_components", "SmallVariantRunResult",
                 "EvidenceInputBinding", "SourceGeneReference"):
        testcase.assertTrue(hasattr(module, attr), attr + " is not implemented")
    return module


class SyntheticRunFixture:
    """Exercise the real stages under explicit, test-only tiny resource pins."""

    def __init__(self):
        self.stack = ExitStack()

    def __enter__(self):
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory(
            prefix="small-variant-unit-", dir=os.environ.get("TMPDIR"))))
        self.reference = self.directory / "reference.fa"
        self.reference_bytes = b">NC_000001.11\n" + b"N" * 10470 + b"C" + b"A" * 29 + b"\n"
        self.reference.write_bytes(self.reference_bytes)
        self.registry = load_json("resources/source-registry.v1.json")
        self.profile = load_json("resources/reference-profiles/grch38-p14-ncbi-refseq-autosomal-v1.json")
        self.bom = load_json("resources/scientific-resource-bom/grch38-p14-ncbi-refseq-autosomal-v1.json")
        self.observation = load_json("tests/fixtures/reference_identity/ncbi-grch38-p14.observation.json")
        reference_sha = digest(self.reference_bytes)
        for resource in self.bom["resources"]:
            if resource["role"] == "reference_fasta_transport":
                resource["content_sha256"] = reference_sha
                resource["content_size_bytes"] = len(self.reference_bytes)
        self.bom["bundle_descriptor"]["fasta_content_sha256"] = reference_sha
        self.bundle_sha = digest(canonical(self.bom["bundle_descriptor"]))
        self.bom["bundle_sha256"] = self.bundle_sha
        self.profile["resource_bom_ref"]["bundle_sha256"] = self.bundle_sha
        self.observation["fasta_content_sha256"] = reference_sha
        self.observation["fasta_content_size_bytes"] = len(self.reference_bytes)
        for name in ("variant_normalization", "canonical_variant_identity"):
            module = importlib.import_module("omnigenis.capabilities." + name)
            for field, value in {
                "REFERENCE_FASTA_CONTENT_SHA256": reference_sha,
                "REFERENCE_FASTA_CONTENT_SIZE_BYTES": len(self.reference_bytes),
                "REFERENCE_BUNDLE_SHA256": self.bundle_sha,
            }.items():
                self.stack.enter_context(patch.object(module, field, value))

        self.approved = (ROOT / "tests/fixtures/gene_identity/hgnc-approved-mini.tsv").read_bytes()
        self.withdrawn = (ROOT / "tests/fixtures/gene_identity/hgnc-withdrawn-mini.tsv").read_bytes()
        spec = importlib.util.spec_from_file_location(
            "_runner_hgnc_test_contract", ROOT / "tests/test_gene_identity_capability.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        gene_registry, self.gene_bom, gene_bundle = helper.synthetic_contract(
            self.approved, self.withdrawn)
        self.registry["sources"] = [
            source for source in self.registry["sources"]
            if source["source_id"] != "hgnc-gene-nomenclature"
        ] + gene_registry["sources"]
        gene_module = importlib.import_module("omnigenis.capabilities.gene_identity")
        for field, value in {
            "HGNC_APPROVED_SHA256": digest(self.approved),
            "HGNC_APPROVED_SIZE_BYTES": len(self.approved),
            "HGNC_APPROVED_ROW_COUNT": 3,
            "HGNC_WITHDRAWN_SHA256": digest(self.withdrawn),
            "HGNC_WITHDRAWN_SIZE_BYTES": len(self.withdrawn),
            "HGNC_WITHDRAWN_ROW_COUNT": 3,
            "HGNC_BUNDLE_SHA256": gene_bundle,
        }.items():
            self.stack.enter_context(patch.object(gene_module, field, value))
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def source(self, *, ref="C", alt="G", gt="0/1", records=1):
        header = (
            "##fileformat=VCFv4.5\n"
            "##contig=<ID=NC_000001.11,length=10500>\n"
            "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Genotype\">\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSYNTHETIC-001\n"
        )
        return (header + "".join(
            f"NC_000001.11\t10471\t.\t{ref}\t{alt}\t.\tPASS\t.\tGT\t{gt}\n"
            for _ in range(records))).encode()

    def evidence(self, *, ref="C", alt="G", genes=None):
        root = ET.fromstring((ROOT / "tests/fixtures/evidence_snapshot/simple-vcv.xml").read_bytes())
        allele = root.find("VariationArchive/ClassifiedRecord/SimpleAllele")
        position = 10471
        location = allele.find("Location/SequenceLocation")
        location.set("referenceAlleleVCF", ref)
        location.set("alternateAlleleVCF", alt)
        if ref == "C" and alt == "CT":
            spdi = "NC_000001.11:10471::T"
        elif ref == "CA" and alt == "C":
            spdi = "NC_000001.11:10471:A:"
        else:
            spdi = f"NC_000001.11:10470:{ref}:{alt}"
        allele.find("CanonicalSPDI").text = spdi
        if genes is None:
            genes = [
                {"HGNC_ID": "HGNC:1001", "Symbol": "SOURCEA", "Source": "submitted",
                 "RelationshipType": "within multiple genes by overlap"},
                {"HGNC_ID": "HGNC:1002", "Symbol": "SOURCEB", "Source": "calculated",
                 "RelationshipType": "within multiple genes by overlap"},
            ]
        if genes:
            gene_list = ET.SubElement(allele, "GeneList")
            for attributes in genes:
                ET.SubElement(gene_list, "Gene", attributes)
        xml = ET.tostring(root, encoding="utf-8")
        identity = {
            "alt": alt, "assembly_accession": "GCF_000001405.40",
            "contig_accession": "NC_000001.11", "identity_version": "1.0.0",
            "position_1_based": position, "ref": ref,
            "reference_bundle_sha256": self.bundle_sha,
        }
        variant_id = "omnigenis:small-variant:v1:sha256:" + digest(canonical(identity))
        return {
            "canonical_variant_id": variant_id,
            "xml_bytes": xml,
            "metadata": {
                "schema_version": "1.0.0", "snapshot_id": "synthetic-vcv-1001",
                "source_registry_id": "source-registry-minimal-e1a-v1",
                "source_version": "VCV000001001.3", "version_kind": "accession.version",
                "checked_at": "2026-09-24T18:00:00Z",
                "locator": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=clinvar&rettype=vcv&id=VCV000001001.3",
                "retrieval_method": "NCBI_EUTILS_HTTPS_EFETCH_VCV",
                "result_sha256": digest(xml), "mutable": True,
            },
        }

    def arguments(self, *, source=None, evidence=None):
        data = self.source() if source is None else source
        items = [self.evidence()] if evidence is None else evidence
        return {
            "source_vcf": data,
            "input_profile": {
                "schema_version": "1.0.0", "input_id": "synthetic-source",
                "input_kind": "VCF", "media_type": "text/vcf",
                "content_sha256": digest(data), "size_bytes": len(data),
            },
            "execution_profile": {
                "schema_version": "1.0.0", "execution_profile_id": PROFILE_ID,
                "executor_id": "omnigenis.local-direct", "executor_version": "1.0.0",
                "deterministic": True,
            },
            "source_artifact_manifest": {
                "schema_version": "1.0.0", "artifact_id": "synthetic-source",
                "artifact_kind": "RAW_INPUT", "media_type": "text/vcf",
                "sha256": digest(data), "size_bytes": len(data),
                "producer_execution_profile_id": PROFILE_ID, "parent_artifact_ids": [],
            },
            "reference_profile": deepcopy(self.profile),
            "reference_resource_bom": deepcopy(self.bom),
            "reference_observation": deepcopy(self.observation),
            "source_registry": deepcopy(self.registry),
            "reference_fasta": self.reference,
            "bcftools_executable": os.environ.get("OMNIGENIS_BCFTOOLS", ""),
            "expected_executor_sha256": os.environ.get("OMNIGENIS_BCFTOOLS_SHA256", ""),
            "evidence_items": items,
            "as_of_date": "2026-11-25",
            "hgnc_approved_tsv": self.approved,
            "hgnc_withdrawn_tsv": self.withdrawn,
            "hgnc_resource_bom": deepcopy(self.gene_bom),
            "timeout_seconds": 30,
        }


class SmallVariantRunTests(unittest.TestCase):
    def setUp(self):
        self.sut = load_sut(self)
        self.fixture = self.enterContext(SyntheticRunFixture())

    def run_case(self, **changes):
        args = self.fixture.arguments()
        args.update(changes)
        return self.sut.run_small_variant_components(**args)

    def test_real_stages_retain_original_input_and_actual_results(self):
        result = self.run_case()
        self.assertTrue(result.passed, (result.failed_stage, result.errors))
        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(result.canonical_model.sample_id, "SYNTHETIC-001")
        self.assertEqual(result.canonical_model.source_vcf_sha256, digest(self.fixture.source()))
        self.assertEqual(result.normalization.input_sha256, digest(self.fixture.source()))
        self.assertNotEqual(result.normalization.normalized_vcf, self.fixture.source())
        self.assertEqual(result.canonical_model.variants[0].position_1_based, 10471)
        self.assertEqual(result.interpretation.items[0].source_classification, "Likely pathogenic")
        self.assertEqual(result.interpretation.items[0].local_classification_status, "NOT_PERFORMED")
        self.assertEqual(result.as_of_date, "2026-11-25")

    def test_input_digest_mismatch_stops_before_intake_or_executor(self):
        args = self.fixture.arguments()
        args["input_profile"]["content_sha256"] = "0" * 64
        result = self.sut.run_small_variant_components(**args)
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_stage, "input_profile")
        self.assertIsNone(result.intake)
        self.assertIsNone(result.normalization)
        self.assertIsNone(result.interpretation)

    def test_distinct_input_and_artifact_identity_roles_remain_valid(self):
        args = self.fixture.arguments()
        args["input_profile"]["input_id"] = "synthetic-input"
        args["source_artifact_manifest"]["artifact_id"] = "synthetic-artifact"
        result = self.sut.run_small_variant_components(**args)
        self.assertTrue(result.passed, (result.failed_stage, result.errors))
        self.assertEqual(result.input_profile["input_id"], "synthetic-input")
        self.assertEqual(result.canonical_model.source_artifact_id, "synthetic-artifact")

    def test_manifest_producer_is_bound_to_the_declared_execution_profile(self):
        args = self.fixture.arguments()
        args["source_artifact_manifest"]["producer_execution_profile_id"] = "other-profile"
        result = self.sut.run_small_variant_components(**args)
        self.assertFalse(result.passed)
        self.assertIsNone(result.normalization)

    def test_execution_profile_rejects_boolean_coercion_and_extra_fields(self):
        for change in ({"deterministic": 1}, {"executor_version": "2.0.0"}, {"extra": "ignored"}):
            with self.subTest(change=change):
                args = self.fixture.arguments()
                args["execution_profile"].update(change)
                result = self.sut.run_small_variant_components(**args)
                self.assertEqual(result.failed_stage, "execution_profile")
                self.assertIsNone(result.intake)

    def test_invalid_date_is_refused_before_expensive_stages(self):
        for value in ("20261125", "2026-02-30", None):
            with self.subTest(value=value):
                result = self.run_case(as_of_date=value)
                self.assertFalse(result.passed)
                self.assertIsNone(result.normalization)

    def test_bad_vcf_preserves_actual_intake_refusal(self):
        args = self.fixture.arguments(source=b"not a VCF\n", evidence=[])
        result = self.sut.run_small_variant_components(**args)
        self.assertEqual(result.failed_stage, "intake")
        self.assertFalse(result.intake.is_valid)
        self.assertIsNone(result.qc)
        self.assertIsNone(result.reference_identity)

    def test_input_and_record_budgets_refuse_whole_input(self):
        large = b"x" * (1048576 + 1)
        result = self.sut.run_small_variant_components(**self.fixture.arguments(source=large))
        self.assertFalse(result.passed)
        self.assertIsNone(result.intake)
        args = self.fixture.arguments(source=self.fixture.source(records=33), evidence=[])
        result = self.sut.run_small_variant_components(**args)
        self.assertEqual(result.status, "UNSUPPORTED_PROFILE")
        self.assertIsNone(result.normalization)

    def test_evidence_budget_is_checked_before_executor(self):
        item = self.fixture.evidence()
        item["xml_bytes"] = b"x" * (20971520 + 1)
        result = self.run_case(evidence_items=[item])
        self.assertFalse(result.passed)
        self.assertIsNone(result.normalization)

    def test_missing_diploid_alleles_are_retained_without_imputation(self):
        for gt in ("0/.", "./.", ".|1"):
            with self.subTest(gt=gt):
                result = self.sut.run_small_variant_components(
                    **self.fixture.arguments(source=self.fixture.source(gt=gt)))
                self.assertTrue(result.passed, (result.failed_stage, result.errors))
                self.assertEqual(result.canonical_model.variants[0].genotype, gt)
                self.assertIsNone(result.canonical_model.variants[0].zygosity)

    def test_other_ploidies_are_unsupported_without_downstream_execution(self):
        for gt in ("1", ".", "0/1/1"):
            with self.subTest(gt=gt):
                result = self.sut.run_small_variant_components(
                    **self.fixture.arguments(source=self.fixture.source(gt=gt)))
                self.assertEqual(result.status, "UNSUPPORTED_PROFILE")
                self.assertEqual(result.failed_stage, "supported_profile")
                self.assertIsNone(result.normalization)

    def test_valid_longer_indels_and_mnvs_are_unsupported_not_reclassified(self):
        for ref, alt in (("CAA", "C"), ("C", "CTT"), ("CA", "GT"), ("CA", "CTA")):
            with self.subTest(ref=ref, alt=alt):
                result = self.sut.run_small_variant_components(
                    **self.fixture.arguments(source=self.fixture.source(ref=ref, alt=alt)))
                self.assertEqual(result.status, "UNSUPPORTED_PROFILE")
                self.assertIsNone(result.normalization)

    def test_original_single_base_insertions_and_deletions_reach_real_stages(self):
        for ref, alt in (("C", "CT"), ("CA", "C")):
            with self.subTest(ref=ref, alt=alt):
                result = self.sut.run_small_variant_components(**self.fixture.arguments(
                    source=self.fixture.source(ref=ref, alt=alt),
                    evidence=[self.fixture.evidence(ref=ref, alt=alt)]))
                self.assertTrue(result.passed, (result.failed_stage, result.errors))
                self.assertEqual(result.normalization.input_record_count, 1)
                self.assertEqual(result.canonical_model.variants[0].ref, ref)
                self.assertEqual(result.canonical_model.variants[0].alt, alt)

    def test_reference_mismatch_retains_normalizer_failure_without_model(self):
        result = self.sut.run_small_variant_components(**self.fixture.arguments(
            source=self.fixture.source(ref="A", alt="G")))
        self.assertEqual(result.failed_stage, "normalization")
        self.assertFalse(result.normalization.passed)
        self.assertIsNone(result.canonical_model)
        self.assertIsNone(result.evidence_snapshot)
        self.assertIsNone(result.interpretation)

    def test_missing_duplicate_and_foreign_evidence_are_refused(self):
        item = self.fixture.evidence()
        foreign = deepcopy(item)
        foreign["canonical_variant_id"] = "omnigenis:small-variant:v1:sha256:" + "0" * 64
        for items in ([], [item, deepcopy(item)], [foreign]):
            with self.subTest(count=len(items)):
                result = self.run_case(evidence_items=items)
                self.assertEqual(result.failed_stage, "evidence")
                self.assertFalse(result.evidence_snapshot.passed)
                self.assertIsNone(result.interpretation)

    def test_all_source_gene_entries_keep_source_order_and_exact_byte_binding(self):
        result = self.run_case()
        self.assertTrue(result.passed, (result.failed_stage, result.errors))
        refs = result.source_gene_references
        self.assertEqual([r.source_gene_ordinal for r in refs], [1, 2])
        self.assertEqual([r.hgnc_id for r in refs], ["HGNC:1001", "HGNC:1002"])
        self.assertEqual([r.source for r in refs], ["submitted", "calculated"])
        self.assertEqual([r.resolution.gene.canonical_gene_id for r in refs],
                         ["HGNC:1001", "HGNC:1002"])
        self.assertEqual(dict(refs[0].source_attributes)["Symbol"], "SOURCEA")
        self.assertEqual(refs[0].source_xml_sha256, digest(self.fixture.evidence()["xml_bytes"]))
        self.assertEqual(refs[0].source_vcv_accession, "VCV000001001")
        self.assertEqual(refs[0].source_vcv_version, 3)

    def test_missing_hgnc_id_preserves_entry_without_symbol_resolution(self):
        item = self.fixture.evidence(genes=[{"Symbol": "GENEA", "Source": "submitted"}])
        result = self.run_case(evidence_items=[item])
        self.assertTrue(result.passed, (result.failed_stage, result.errors))
        self.assertEqual(len(result.source_gene_references), 1)
        self.assertIsNone(result.source_gene_references[0].hgnc_id)
        self.assertIsNone(result.source_gene_references[0].resolution)

    def test_unresolved_explicit_hgnc_id_blocks_interpretation_without_dropping_entries(self):
        item = self.fixture.evidence(genes=[
            {"HGNC_ID": "HGNC:999999", "Source": "submitted"},
            {"HGNC_ID": "HGNC:1001", "Source": "calculated"},
        ])
        result = self.run_case(evidence_items=[item])
        self.assertEqual(result.failed_stage, "source_genes")
        self.assertEqual(len(result.source_gene_references), 2)
        self.assertFalse(result.source_gene_references[0].resolution.passed)
        self.assertIsNone(result.source_gene_references[1].resolution)
        self.assertIsNone(result.interpretation)

    def test_source_gene_budget_rejects_without_partial_interpretation(self):
        item = self.fixture.evidence(genes=[{"HGNC_ID": "HGNC:1001"}] * 65)
        result = self.run_case(evidence_items=[item])
        self.assertEqual(result.status, "UNSUPPORTED_PROFILE")
        self.assertEqual(result.failed_stage, "source_genes")
        self.assertIsNone(result.interpretation)

    def test_empty_verified_run_is_distinct_from_missing_evidence(self):
        args = self.fixture.arguments(source=self.fixture.source(records=0), evidence=[])
        result = self.sut.run_small_variant_components(**args)
        self.assertTrue(result.passed, (result.failed_stage, result.errors))
        self.assertEqual(result.canonical_model.variants, ())
        self.assertEqual(result.evidence_snapshot.items, ())
        self.assertEqual(result.interpretation.items, ())
        self.assertEqual(result.source_gene_references, ())

    def test_frozen_bindings_do_not_track_mutable_caller_metadata(self):
        args = self.fixture.arguments()
        result = self.sut.run_small_variant_components(**args)
        self.assertTrue(result.passed, (result.failed_stage, result.errors))
        original_metadata = result.evidence_inputs[0].metadata_json
        args["input_profile"]["input_id"] = "mutated"
        args["source_registry"]["sources"][0]["source_id"] = "mutated"
        args["evidence_items"][0]["metadata"]["snapshot_id"] = "mutated"
        self.assertEqual(result.input_profile["input_id"], "synthetic-source")
        self.assertEqual(result.source_registry["sources"][0]["source_id"], "ncbi-refseq-grch38-p14")
        self.assertEqual(result.evidence_inputs[0].metadata_json, original_metadata)
        with self.assertRaises(TypeError):
            result.input_profile["input_id"] = "mutable"
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            result.evidence_inputs[0].xml_bytes = b"changed"
        self.assertNotIn(str(self.fixture.directory), repr(result))

    def test_published_execution_profile_is_consumed_without_widening_schema(self):
        profile = load_json("resources/execution-profiles/single-sample-small-variant-run-v1.json")
        result = self.run_case(execution_profile=profile)
        self.assertTrue(result.passed, (result.failed_stage, result.errors))

    def test_timeout_budget_refuses_invalid_values_before_normalizer_execution(self):
        for timeout in (0, 601, True, 1.0):
            with self.subTest(timeout=timeout):
                with patch.object(self.sut, "normalize_small_variants",
                                  side_effect=AssertionError("normalizer must not execute")):
                    result = self.run_case(timeout_seconds=timeout)
                self.assertEqual(result.status, "UNSUPPORTED_PROFILE")
                self.assertEqual(result.failed_stage, "runtime_profile")
                self.assertIsNone(result.normalization)

    def test_namespaced_validated_xml_preserves_all_source_gene_entries(self):
        item = self.fixture.evidence()
        root = ET.fromstring(item["xml_bytes"])
        for element in root.iter():
            element.tag = "{urn:synthetic-clinvar}" + element.tag
        item["xml_bytes"] = ET.tostring(root, encoding="utf-8")
        item["metadata"]["result_sha256"] = digest(item["xml_bytes"])
        result = self.run_case(evidence_items=[item])
        self.assertTrue(result.passed, (result.failed_stage, result.errors))
        self.assertEqual([entry.hgnc_id for entry in result.source_gene_references],
                         ["HGNC:1001", "HGNC:1002"])
        self.assertEqual([entry.source_gene_ordinal for entry in result.source_gene_references],
                         [1, 2])
        self.assertEqual(result.source_gene_references[1].source, "calculated")
        self.assertEqual(result.source_gene_references[1].source_xml_sha256,
                         item["metadata"]["result_sha256"])


    def test_completed_stage_sequence_cannot_be_removed_reordered_or_extended(self):
        result = self.run_case()
        self.assertTrue(result.passed, (result.failed_stage, result.errors))
        for stages in ((), result.completed_stages[:-1],
                       tuple(reversed(result.completed_stages)),
                       result.completed_stages + ("unexecuted_stage",)):
            with self.subTest(stages=stages):
                self.assertFalse(replace(result, completed_stages=stages).passed)
        expected = (
            "input_profile", "execution_profile", "as_of_date", "runtime_profile",
            "evidence_inputs", "resource_metadata", "intake", "artifact", "qc",
            "supported_profile", "reference", "normalization", "canonical_variants",
            "canonical_model", "evidence", "source_genes", "interpretation",
        )
        self.assertEqual(result.completed_stages, expected)
        self.assertEqual(getattr(self.sut, "COMPLETED_STAGES", None), expected)

    def test_explicit_hgnc_resolution_retains_immutable_resource_binding(self):
        args = self.fixture.arguments()
        result = self.sut.run_small_variant_components(**args)
        self.assertTrue(result.passed, (result.failed_stage, result.errors))
        self.assertTrue(hasattr(result, "gene_resource_binding"),
                        "Exact HGNC source resources must remain revalidatable")
        binding = result.gene_resource_binding
        self.assertIsInstance(binding, self.sut.GeneResourceBinding)
        self.assertIs(binding.approved_tsv, args["hgnc_approved_tsv"])
        self.assertIs(binding.withdrawn_tsv, args["hgnc_withdrawn_tsv"])
        original_digest = args["hgnc_resource_bom"]["bundle_sha256"]
        original_role = args["hgnc_resource_bom"]["resources"][0]["role"]
        args["hgnc_resource_bom"]["bundle_sha256"] = "0" * 64
        args["hgnc_resource_bom"]["resources"][0]["role"] = "mutated"
        self.assertEqual(binding.resource_bom["bundle_sha256"], original_digest)
        self.assertEqual(binding.resource_bom["resources"][0]["role"], original_role)
        with self.assertRaises(TypeError):
            binding.resource_bom["resources"][0]["role"] = "mutable"
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            binding.approved_tsv = b"changed"
        self.assertFalse(replace(result, gene_resource_binding=None).passed)
        self.assertNotIn(self.fixture.approved.decode("utf-8"), repr(binding))

    def test_hgnc_resource_binding_is_absent_when_no_explicit_id_is_resolved(self):
        for genes in ([], [{"Symbol": "GENEA", "Source": "submitted"}]):
            with self.subTest(genes=genes):
                item = self.fixture.evidence(genes=genes)
                with patch.object(self.sut, "resolve_gene_identity",
                                  side_effect=AssertionError("No explicit ID to resolve")):
                    result = self.run_case(evidence_items=[item])
                self.assertTrue(result.passed, (result.failed_stage, result.errors))
                self.assertTrue(hasattr(result, "gene_resource_binding"),
                                "The conditional source binding must be explicit")
                self.assertIsNone(result.gene_resource_binding)

    def test_failed_explicit_hgnc_resolution_preserves_the_attempted_resource_binding(self):
        item = self.fixture.evidence(genes=[{"HGNC_ID": "HGNC:999999"}])
        result = self.run_case(evidence_items=[item])
        self.assertEqual(result.failed_stage, "source_genes")
        self.assertIsNone(result.interpretation)
        self.assertTrue(hasattr(result, "gene_resource_binding"),
                        "Failed explicit resolution must retain its source binding")
        self.assertIsInstance(result.gene_resource_binding, self.sut.GeneResourceBinding)
        self.assertEqual(result.gene_resource_binding.approved_tsv, self.fixture.approved)
        self.assertFalse(result.source_gene_references[0].resolution.passed)
