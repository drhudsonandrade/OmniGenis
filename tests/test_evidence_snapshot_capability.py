from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

from jsonschema import Draft202012Validator

from omnigenis.capabilities.canonical_genomic_model import (
    CanonicalGenomicModelResult,
    CanonicalGenomicModelRuleResult,
    CanonicalGenomicVariant,
    RULE_CANONICAL_VARIANTS,
    RULE_MODEL,
    RULE_NORMALIZATION,
    RULE_NORMALIZED_QC,
    RULE_REFERENCE,
    RULE_SAMPLE,
    RULE_SOURCE_ARTIFACT,
    RULE_SOURCE_QC,
)

ROOT = Path(__file__).resolve().parents[1]
SIMPLE_XML = ROOT / "tests" / "fixtures" / "evidence_snapshot" / "simple-vcv.xml"
CONFLICT_XML = ROOT / "tests" / "fixtures" / "evidence_snapshot" / "conflict-vcv.xml"
CAPABILITY_MANIFEST = ROOT / "capabilities" / "minimum-evidence-snapshot.manifest.json"
CAPABILITY_SCHEMA = ROOT / "schemas" / "capability-pack-manifest.v1.schema.json"
METADATA_SCHEMA = ROOT / "schemas" / "evidence-snapshot-metadata.v1.schema.json"
SOURCE_REGISTRY = ROOT / "resources" / "source-registry.v1.json"

MODEL_RULE_IDS = (
    RULE_SOURCE_ARTIFACT,
    RULE_SOURCE_QC,
    RULE_REFERENCE,
    RULE_NORMALIZATION,
    RULE_CANONICAL_VARIANTS,
    RULE_SAMPLE,
    RULE_NORMALIZED_QC,
    RULE_MODEL,
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_id(seed: str) -> str:
    return "omnigenis:small-variant:v1:sha256:" + hashlib.sha256(seed.encode()).hexdigest()


def model_for_variant(
    *,
    contig: str,
    position: int,
    ref: str,
    alt: str,
    seed: str,
) -> CanonicalGenomicModelResult:
    variant = CanonicalGenomicVariant(
        canonical_variant_id=canonical_id(seed),
        contig_accession=contig,
        position_1_based=position,
        ref=ref,
        alt=alt,
        genotype="0/1",
        zygosity="HETEROZYGOUS",
        phase_semantics="UNPHASED",
        depth=30,
        genotype_quality=60,
        allele_balance=0.5,
        filter_status="PASS",
        normalized_record_ordinal=1,
        normalized_record_sha256="1" * 64,
        source_record_sha256s=("2" * 64,),
        normalization_transformation="UNCHANGED",
    )
    return CanonicalGenomicModelResult(
        sample_id="SAMPLE_A",
        source_artifact_id="source-vcf",
        source_vcf_sha256="3" * 64,
        source_vcf_size_bytes=1234,
        reference_profile_id="grch38-p14-ncbi-refseq-autosomal-v1",
        reference_assembly_accession="GCF_000001405.40",
        reference_bundle_sha256="1c34b839e1ae36102d003a217f76f1dd57cd1d10b0310cbd9e1d8078c8e88672",
        normalization_output_sha256="4" * 64,
        variants=(variant,),
        rules=tuple(
            CanonicalGenomicModelRuleResult(rule_id, "PASS", "fixture")
            for rule_id in MODEL_RULE_IDS
        ),
        errors=(),
    )


def empty_model() -> CanonicalGenomicModelResult:
    model = model_for_variant(
        contig="NC_000001.11",
        position=10471,
        ref="C",
        alt="G",
        seed="empty-template",
    )
    return replace(model, variants=())


def metadata(xml_bytes: bytes, source_version: str, checked_at: str = "2026-09-24T18:00:00Z"):
    digest = sha256_bytes(xml_bytes)
    return {
        "schema_version": "1.0.0",
        "snapshot_id": f"clinvar-vcv:{source_version}:sha256:{digest[:16]}",
        "source_registry_id": "source-registry-minimal-e1a-v1",
        "source_version": source_version,
        "version_kind": "accession.version",
        "checked_at": checked_at,
        "locator": (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            f"?db=clinvar&rettype=vcv&id={source_version}"
        ),
        "retrieval_method": "NCBI_EUTILS_HTTPS_EFETCH_VCV",
        "result_sha256": digest,
        "mutable": True,
    }
def source_registry():
    return {
        "registry_id": "source-registry-minimal-e1a-v1",
        "schema_version": "1.0.0",
        "sources": [
            {
                "source_id": "ncbi-clinvar-vcv",
                "provider": "NCBI ClinVar",
                "version_release": "Per-record VCV accession.version snapshots",
                "retrieval_date": "2026-09-24",
                "source_url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                "terms_url": "https://www.ncbi.nlm.nih.gov/clinvar/docs/maintenance_use/",
                "license": "ClinVar data are freely available for any use; attribution to ClinVar requested.",
                "rights_status": "UNRESTRICTED_USE_WITH_ATTRIBUTION_REQUESTED",
                "local_reference_use": "ALLOWED",
                "redistribution": "ALLOWED_WITH_ATTRIBUTION_REQUESTED",
                "lifecycle": "MUTABLE_PER_RECORD_SNAPSHOT",
                "limitations": "NIH does not independently verify submitted information; not intended for direct diagnostic use without genetics professional review.",
                "known_issues": "ClinVar is continuously updated and classifications may conflict; every EvidenceSnapshot must pin accession.version, retrieval time, and content digest.",
                "replacement_path": "Retrieve the required VCV accession.version, record retrieval time and SHA-256, and create a new EvidenceSnapshot rather than mutating an existing one.",
                "attribution": "CLINVAR_ATTRIBUTION_REQUESTED",
            }
        ],
    }


def item(model: CanonicalGenomicModelResult, xml_bytes: bytes, source_version: str):
    return {
        "canonical_variant_id": model.variants[0].canonical_variant_id,
        "metadata": metadata(xml_bytes, source_version),
        "xml_bytes": xml_bytes,
    }


def variant_xml(
    template: bytes,
    *,
    spdi: str,
    position: int,
    ref: str,
    alt: str,
    location_overrides: dict[str, str] | None = None,
) -> bytes:
    """Build synthetic source evidence with independently supplied representations."""
    root = ET.fromstring(template)
    allele = root.find("./VariationArchive/ClassifiedRecord/SimpleAllele")
    assert allele is not None
    spdi_node = allele.find("CanonicalSPDI")
    location = allele.find("./Location/SequenceLocation")
    assert spdi_node is not None and location is not None
    spdi_node.text = spdi
    location.set("positionVCF", str(position))
    location.set("referenceAlleleVCF", ref)
    location.set("alternateAlleleVCF", alt)
    for key, value in (location_overrides or {}).items():
        location.set(key, value)
    return ET.tostring(root, encoding="utf-8")


def load_sut(testcase: unittest.TestCase):
    name = "omnigenis.capabilities.evidence_snapshot"
    if importlib.util.find_spec(name) is None:
        testcase.fail("evidence_snapshot capability module is missing")
    module = importlib.import_module(name)
    for attr in ("build_minimum_evidence_snapshot", "MinimumEvidenceSnapshotResult"):
        if not hasattr(module, attr):
            testcase.fail(f"{attr} is missing")
    return module


class MinimumEvidenceSnapshotCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.simple_xml = SIMPLE_XML.read_bytes()
        self.conflict_xml = CONFLICT_XML.read_bytes()
        self.simple_model = model_for_variant(
            contig="NC_000001.11",
            position=10471,
            ref="C",
            alt="G",
            seed="simple",
        )
        self.conflict_model = model_for_variant(
            contig="NC_000016.10",
            position=88738562,
            ref="G",
            alt="C",
            seed="conflict",
        )
        self.registry = source_registry()

    def build(self, model, evidence_items, registry=None):
        sut = load_sut(self)
        return sut.build_minimum_evidence_snapshot(
            canonical_model=model,
            evidence_items=evidence_items,
            source_registry=self.registry if registry is None else registry,
        )

    def test_simple_vcv_snapshot_preserves_aggregate_and_submission_evidence(self) -> None:
        result = self.build(
            self.simple_model,
            [item(self.simple_model, self.simple_xml, "VCV000001001.3")],
        )
        self.assertTrue(result.passed, result.errors)
        self.assertEqual(len(result.items), 1)
        evidence = result.items[0]
        self.assertEqual(evidence.canonical_variant_id, self.simple_model.variants[0].canonical_variant_id)
        self.assertEqual(evidence.vcv_accession, "VCV000001001")
        self.assertEqual(evidence.vcv_version, 3)
        self.assertEqual(evidence.variation_id, 1001)
        self.assertEqual(evidence.aggregate_classification, "Likely pathogenic")
        self.assertEqual(evidence.aggregate_review_status, "criteria provided, single submitter")
        self.assertEqual(evidence.condition_names, ("Synthetic condition A",))
        self.assertFalse(evidence.conflict)
        self.assertEqual(len(evidence.submissions), 1)
        submission = evidence.submissions[0]
        self.assertEqual(submission.accession, "SCV000001001")
        self.assertEqual(submission.version, 2)
        self.assertEqual(submission.submitter_name, "Synthetic Laboratory")
        self.assertEqual(submission.classification, "Likely pathogenic")
        self.assertEqual(submission.review_status, "criteria provided, single submitter")
        self.assertEqual(submission.condition_names, ("Synthetic condition A",))
    def test_no_conflicts_review_status_is_not_marked_as_conflict(self) -> None:
        xml_bytes = self.simple_xml.replace(
            b"criteria provided, single submitter",
            b"criteria provided, multiple submitters, no conflicts",
            1,
        )
        result = self.build(
            self.simple_model,
            [item(self.simple_model, xml_bytes, "VCV000001001.3")],
        )
        self.assertTrue(result.passed, result.errors)
        self.assertFalse(result.items[0].conflict)

    def test_conflicting_vcv_preserves_all_scv_classifications_without_winner(self) -> None:
        result = self.build(
            self.conflict_model,
            [item(self.conflict_model, self.conflict_xml, "VCV000002002.4")],
        )
        self.assertTrue(result.passed, result.errors)
        evidence = result.items[0]
        self.assertTrue(evidence.conflict)
        self.assertEqual(
            evidence.aggregate_classification,
            "Conflicting classifications of pathogenicity",
        )
        self.assertEqual(
            {submission.classification for submission in evidence.submissions},
            {"Likely benign", "Uncertain significance"},
        )
        item_payload = result.to_dict()["canonical_payload"]["items"][0]
        forbidden_decision_keys = {"winner", "consensus", "vote", "selected_classification"}
        self.assertTrue(forbidden_decision_keys.isdisjoint(item_payload))
        for submission_payload in item_payload["submissions"]:
            self.assertTrue(
                forbidden_decision_keys.isdisjoint(submission_payload)
            )

    def test_included_submission_name_preserves_original_type_and_text(self) -> None:
        root = ET.fromstring(self.simple_xml)
        name = root.find("./VariationArchive/ClassifiedRecord/ClinicalAssertion/TraitSet/Trait/Name/ElementValue")
        self.assertIsNotNone(name)
        name.set("Type", "included")
        name.text = "  Synthetic included condition A  "
        xml_bytes = ET.tostring(root, encoding="utf-8")
        result = self.build(
            self.simple_model,
            [item(self.simple_model, xml_bytes, "VCV000001001.3")],
        )
        self.assertTrue(result.passed, result.errors)
        evidence = result.items[0]
        submission = evidence.submissions[0]
        self.assertEqual(submission.condition_names, ("Synthetic included condition A",))
        self.assertEqual(submission.condition_references, ())
        self.assertEqual(submission.to_dict().get("condition_name_observations"), [
            {"value": "  Synthetic included condition A  ", "source_type": "included"},
        ])
        self.assertEqual(evidence.to_dict().get("condition_name_observations"), [
            {"value": "Synthetic condition A", "source_type": "Preferred"},
        ])
        self.assertEqual(evidence.metadata.result_sha256, sha256_bytes(xml_bytes))
        self.assertEqual(submission.classification, "Likely pathogenic")
        self.assertEqual(evidence.aggregate_classification, "Likely pathogenic")
        self.assertFalse(evidence.conflict)
        self.assertIsInstance(submission.condition_name_observations, tuple)
        with self.assertRaises(FrozenInstanceError):
            submission.condition_name_observations[0].value = "changed"

    def test_included_aggregate_name_preserves_type_in_direct_and_nested_contexts(self) -> None:
        for placement in ("direct", "aggregate"):
            with self.subTest(placement=placement):
                root = ET.fromstring(self.simple_xml)
                classified = root.find("./VariationArchive/ClassifiedRecord")
                traitset = classified.find("TraitSet")
                name = traitset.find("./Trait/Name/ElementValue")
                name.set("Type", "included")
                name.text = "Synthetic aggregate condition"
                if placement == "aggregate":
                    classified.remove(traitset)
                    classified.find("GermlineClassification").append(traitset)
                xml_bytes = ET.tostring(root, encoding="utf-8")
                result = self.build(
                    self.simple_model,
                    [item(self.simple_model, xml_bytes, "VCV000001001.3")],
                )
                self.assertTrue(result.passed, result.errors)
                evidence = result.items[0]
                self.assertEqual(evidence.condition_names, ("Synthetic aggregate condition",))
                self.assertEqual(evidence.to_dict().get("condition_name_observations"), [
                    {"value": "Synthetic aggregate condition", "source_type": "included"},
                ])
                self.assertEqual(evidence.submissions[0].condition_names, ("Synthetic condition A",))
                self.assertEqual(evidence.submissions[0].to_dict().get("condition_name_observations"), [
                    {"value": "Synthetic condition A", "source_type": "Preferred"},
                ])

    def test_condition_name_observations_preserve_source_type_order_and_duplicates(self) -> None:
        root = ET.fromstring(self.simple_xml)
        name = root.find("./VariationArchive/ClassifiedRecord/ClinicalAssertion/TraitSet/Trait/Name")
        for _ in range(2):
            ET.SubElement(name, "ElementValue", {"Type": "included"}).text = "Synthetic condition A"
        xml_bytes = ET.tostring(root, encoding="utf-8")
        result = self.build(
            self.simple_model,
            [item(self.simple_model, xml_bytes, "VCV000001001.3")],
        )
        self.assertTrue(result.passed, result.errors)
        submission = result.items[0].submissions[0]
        self.assertEqual(submission.condition_names, ("Synthetic condition A",))
        self.assertEqual(submission.to_dict().get("condition_name_observations"), [
            {"value": "Synthetic condition A", "source_type": "Preferred"},
            {"value": "Synthetic condition A", "source_type": "included"},
            {"value": "Synthetic condition A", "source_type": "included"},
        ])

    def test_unsupported_or_missing_condition_names_do_not_fabricate_context(self) -> None:
        for source_type, source_text, container in (
            ("Alternate", "Synthetic condition A", "Name"),
            ("Included", "Synthetic condition A", "Name"),
            ("", "Synthetic condition A", "Name"),
            (None, "Synthetic condition A", "Name"),
            ("included", "   ", "Name"),
            ("included", "SYNTHETIC_SYMBOL", "Symbol"),
            ("Preferred", "SYNTHETIC_SYMBOL", "Symbol"),
        ):
            with self.subTest(source_type=source_type, container=container):
                root = ET.fromstring(self.simple_xml)
                name = root.find("./VariationArchive/ClassifiedRecord/ClinicalAssertion/TraitSet/Trait/Name")
                name.tag = container
                value = name.find("ElementValue")
                value.attrib.clear()
                if source_type is not None:
                    value.set("Type", source_type)
                value.text = source_text
                xml_bytes = ET.tostring(root, encoding="utf-8")
                result = self.build(
                    self.simple_model,
                    [item(self.simple_model, xml_bytes, "VCV000001001.3")],
                )
                self.assertFalse(result.passed)
                self.assertIn("clinvar_submission_invalid", result.errors)

    def test_included_name_keeps_every_conflicting_submission(self) -> None:
        root = ET.fromstring(self.conflict_xml)
        name = root.find("./VariationArchive/ClassifiedRecord/ClinicalAssertion/TraitSet/Trait/Name/ElementValue")
        name.set("Type", "included")
        name.text = "Synthetic included condition"
        xml_bytes = ET.tostring(root, encoding="utf-8")
        result = self.build(
            self.conflict_model,
            [item(self.conflict_model, xml_bytes, "VCV000002002.4")],
        )
        self.assertTrue(result.passed, result.errors)
        evidence = result.items[0]
        self.assertTrue(evidence.conflict)
        self.assertEqual(evidence.aggregate_classification, "Conflicting classifications of pathogenicity")
        self.assertEqual(
            [(submission.accession, submission.classification) for submission in evidence.submissions],
            [("SCV000002001", "Likely benign"), ("SCV000002002", "Uncertain significance")],
        )
        self.assertEqual(evidence.submissions[0].to_dict().get("condition_name_observations"), [
            {"value": "Synthetic included condition", "source_type": "included"},
        ])
        self.assertEqual(evidence.submissions[1].to_dict().get("condition_name_observations"), [
            {"value": "Synthetic condition B", "source_type": "Preferred"},
        ])

    def test_legacy_condition_evidence_constructors_default_to_no_name_observations(self) -> None:
        result = self.build(
            self.simple_model,
            [item(self.simple_model, self.simple_xml, "VCV000001001.3")],
        )
        self.assertTrue(result.passed, result.errors)
        evidence = result.items[0]
        for value in (evidence, evidence.submissions[0]):
            with self.subTest(type=type(value).__name__):
                legacy_arguments = {
                    field.name: getattr(value, field.name)
                    for field in fields(value)
                    if field.name != "condition_name_observations"
                }
                legacy = type(value)(**legacy_arguments)
                self.assertEqual(legacy.to_dict().get("condition_name_observations"), [])

    def test_metadata_digest_version_locator_and_retrieval_contract_fail_closed(self) -> None:
        base = item(self.simple_model, self.simple_xml, "VCV000001001.3")
        mutations = []
        bad = json.loads(json.dumps(base["metadata"]))
        bad["result_sha256"] = "0" * 64
        mutations.append((bad, "evidence_metadata_digest_mismatch"))
        bad = json.loads(json.dumps(base["metadata"]))
        bad["source_version"] = "VCV000001001.2"
        bad["locator"] = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            "?db=clinvar&rettype=vcv&id=VCV000001001.2"
        )
        mutations.append((bad, "clinvar_version_binding_mismatch"))
        bad = json.loads(json.dumps(base["metadata"]))
        bad["locator"] = "https://example.invalid/clinvar"
        mutations.append((bad, "evidence_metadata_locator_invalid"))
        bad = json.loads(json.dumps(base["metadata"]))
        bad["retrieval_method"] = "OTHER"
        mutations.append((bad, "evidence_metadata_retrieval_invalid"))
        for metadata_value, expected_error in mutations:
            with self.subTest(expected_error=expected_error):
                forged = dict(base)
                forged["metadata"] = metadata_value
                result = self.build(self.simple_model, [forged])
                self.assertFalse(result.passed)
                self.assertIn(expected_error, result.errors)

    def test_repeat_expanded_deletion_preserves_raw_source_spdi(self) -> None:
        model = model_for_variant(
            contig="NC_000001.11", position=10471,
            ref="AC", alt="A", seed="expanded-deletion",
        )
        for spdi in (
            "NC_000001.11:10471:CCCCCC:CCCCC",
            "NC_000001.11:10469:GACCCC:GACCC",
            "NC_000001.11:10471:C:",
        ):
            with self.subTest(spdi=spdi):
                xml_bytes = variant_xml(
                    self.simple_xml, spdi=spdi,
                    position=10471, ref="AC", alt="A",
                )
                result = self.build(
                    model, [item(model, xml_bytes, "VCV000001001.3")],
                )
                self.assertTrue(result.passed, result.errors)
                self.assertEqual(result.items[0].canonical_spdi, spdi)
                self.assertEqual(
                    result.items[0].metadata.result_sha256,
                    hashlib.sha256(xml_bytes).hexdigest(),
                )

    def test_repeat_expanded_insertion_preserves_raw_source_spdi(self) -> None:
        model = model_for_variant(
            contig="NC_000001.11", position=10471,
            ref="A", alt="AC", seed="expanded-insertion",
        )
        for spdi in (
            "NC_000001.11:10471:CCCCC:CCCCCC",
            "NC_000001.11:10469:GACCC:GACCCC",
            "NC_000001.11:10471::C",
        ):
            with self.subTest(spdi=spdi):
                xml_bytes = variant_xml(
                    self.simple_xml, spdi=spdi,
                    position=10471, ref="A", alt="AC",
                )
                result = self.build(
                    model, [item(model, xml_bytes, "VCV000001001.3")],
                )
                self.assertTrue(result.passed, result.errors)
                self.assertEqual(result.items[0].canonical_spdi, spdi)

    def test_literal_spdi_common_flanks_preserve_the_same_snv(self) -> None:
        spdi = "NC_000001.11:10468:AACGG:AAGGG"
        xml_bytes = variant_xml(
            self.simple_xml, spdi=spdi,
            position=10471, ref="C", alt="G",
        )
        result = self.build(
            self.simple_model,
            [item(self.simple_model, xml_bytes, "VCV000001001.3")],
        )
        self.assertTrue(result.passed, result.errors)
        self.assertEqual(result.items[0].canonical_spdi, spdi)

    def test_expanded_spdi_refuses_non_equivalent_or_foreign_edits(self) -> None:
        model = model_for_variant(
            contig="NC_000001.11", position=10471,
            ref="AC", alt="A", seed="expanded-deletion",
        )
        for spdi in (
            "NC_000002.12:10471:CCCCCC:CCCCC",
            "NC_000001.10:10471:CCCCCC:CCCCC",
            "NC_000001.11:10472:CCCCCC:CCCCC",
            "NC_000001.11:10471:GGGGGG:GGGGG",
            "NC_000001.11:10471:CCCCCC:CCCC",
            "NC_000001.11:10471:CCCCC:CCCCCC",
            "NC_000001.11:10471:CCCCCC:CCCCG",
            "NC_000001.11:10471:CCCCCC:CCCCCC",
        ):
            with self.subTest(spdi=spdi):
                xml_bytes = variant_xml(
                    self.simple_xml, spdi=spdi,
                    position=10471, ref="AC", alt="A",
                )
                result = self.build(
                    model, [item(model, xml_bytes, "VCV000001001.3")],
                )
                self.assertFalse(result.passed)
                self.assertIn("clinvar_variant_binding_mismatch", result.errors)

    def test_literal_spdi_refuses_malformed_or_unsupported_fields(self) -> None:
        cases = (
            ("missing_field", "NC_000001.11:10470:C"),
            ("extra_field", "NC_000001.11:10470:C:G:A"),
            ("missing_reference", ":10470:C:G"),
            ("negative_position", "NC_000001.11:-1:C:G"),
            ("signed_position", "NC_000001.11:+10470:C:G"),
            ("noncanonical_integer", "NC_000001.11:010470:C:G"),
            ("unicode_integer", "NC_000001.11:１０４７０:C:G"),
            ("oversized_integer", "NC_000001.11:" + "9" * 5000 + ":C:G"),
            ("integer_deletion", "NC_000001.11:10470:1:G"),
            ("ambiguous_base", "NC_000001.11:10470:N:G"),
            ("lowercase_base", "NC_000001.11:10470:c:G"),
            ("internal_space", "NC_000001.11:10470:C :G"),
            ("nonliteral_insertion", "NC_000001.11:10470:C:<DEL>"),
            ("empty_edit", "NC_000001.11:10470::"),
        )
        for label, spdi in cases:
            with self.subTest(case=label):
                xml_bytes = variant_xml(
                    self.simple_xml, spdi=spdi,
                    position=10471, ref="C", alt="G",
                )
                result = self.build(
                    self.simple_model,
                    [item(self.simple_model, xml_bytes, "VCV000001001.3")],
                )
                self.assertFalse(result.passed)
                self.assertIn("clinvar_variant_binding_mismatch", result.errors)

    def test_matching_expanded_spdi_does_not_bypass_source_vcf_binding(self) -> None:
        model = model_for_variant(
            contig="NC_000001.11", position=10471,
            ref="AC", alt="A", seed="expanded-deletion",
        )
        for overrides in (
            {"Accession": "NC_000002.12"},
            {"positionVCF": "10472"},
            {"referenceAlleleVCF": "GC"},
            {"alternateAlleleVCF": "G"},
        ):
            with self.subTest(overrides=overrides):
                xml_bytes = variant_xml(
                    self.simple_xml,
                    spdi="NC_000001.11:10471:CCCCCC:CCCCC",
                    position=10471, ref="AC", alt="A",
                    location_overrides=overrides,
                )
                result = self.build(
                    model, [item(model, xml_bytes, "VCV000001001.3")],
                )
                self.assertFalse(result.passed)
                self.assertIn("clinvar_variant_binding_mismatch", result.errors)

    def test_long_literal_spdi_flanks_preserve_one_base_deletion(self) -> None:
        model = model_for_variant(
            contig="NC_000001.11", position=10471,
            ref="AC", alt="A", seed="long-expanded-deletion",
        )
        spdi = "NC_000001.11:10471:" + "C" * 100001 + ":" + "C" * 100000
        xml_bytes = variant_xml(
            self.simple_xml, spdi=spdi,
            position=10471, ref="AC", alt="A",
        )
        result = self.build(
            model, [item(model, xml_bytes, "VCV000001001.3")],
        )
        self.assertTrue(result.passed, result.errors)
        self.assertEqual(result.items[0].canonical_spdi, spdi)

    def test_variant_coordinate_and_allele_binding_fail_closed(self) -> None:
        cases = (
            replace(
                self.simple_model,
                variants=(replace(self.simple_model.variants[0], position_1_based=10472),),
            ),
            replace(
                self.simple_model,
                variants=(replace(self.simple_model.variants[0], alt="T"),),
            ),
        )
        for forged_model in cases:
            with self.subTest(variant=forged_model.variants[0]):
                evidence_item = {
                    "canonical_variant_id": forged_model.variants[0].canonical_variant_id,
                    "metadata": metadata(self.simple_xml, "VCV000001001.3"),
                    "xml_bytes": self.simple_xml,
                }
                result = self.build(forged_model, [evidence_item])
                self.assertFalse(result.passed)
                self.assertIn("clinvar_variant_binding_mismatch", result.errors)

    def test_missing_duplicate_and_unknown_variant_items_fail_closed(self) -> None:
        good = item(self.simple_model, self.simple_xml, "VCV000001001.3")
        result = self.build(self.simple_model, [])
        self.assertFalse(result.passed)
        self.assertIn("evidence_cardinality_mismatch", result.errors)

        result = self.build(self.simple_model, [good, dict(good)])
        self.assertFalse(result.passed)
        self.assertIn("duplicate_evidence_variant_id", result.errors)

        unknown = dict(good)
        unknown["canonical_variant_id"] = canonical_id("other")
        result = self.build(self.simple_model, [unknown])
        self.assertFalse(result.passed)
        self.assertIn("evidence_variant_not_in_model", result.errors)

    def test_empty_canonical_model_yields_empty_snapshot(self) -> None:
        result = self.build(empty_model(), [])
        self.assertTrue(result.passed, result.errors)
        self.assertEqual(result.items, ())
        self.assertEqual(result.to_dict()["canonical_payload"]["evidence_item_count"], 0)
    def test_malformed_unsafe_and_wrong_root_xml_fail_closed(self) -> None:
        utf16_unsafe = (
            '<?xml version="1.0" encoding="UTF-16"?>'
            '<!DOCTYPE x [<!ENTITY a "x">]>'
            '<ClinVarResult-Set>&a;</ClinVarResult-Set>'
        ).encode("utf-16")
        cases = (
            (b"<not-xml", "clinvar_xml_invalid"),
            (
                b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "x">]><ClinVarResult-Set>&a;</ClinVarResult-Set>',
                "clinvar_xml_unsafe",
            ),
            (utf16_unsafe, "clinvar_xml_unsafe"),
            (b'<?xml version="1.0"?><Other/>', "clinvar_root_invalid"),
        )
        for xml_bytes, expected_error in cases:
            with self.subTest(expected_error=expected_error):
                evidence_item = {
                    "canonical_variant_id": self.simple_model.variants[0].canonical_variant_id,
                    "metadata": metadata(xml_bytes, "VCV000001001.3"),
                    "xml_bytes": xml_bytes,
                }
                result = self.build(self.simple_model, [evidence_item])
                self.assertFalse(result.passed)
                self.assertIn(expected_error, result.errors)

    def test_submission_condition_reference_without_name_is_preserved(self) -> None:
        xml_bytes = self.simple_xml.replace(
            b'<Name><ElementValue Type="Preferred">Synthetic condition A</ElementValue></Name>',
            b'<XRef DB="MedGen" ID="C123456" Type="CUI"/>',
        )
        result = self.build(
            self.simple_model,
            [item(self.simple_model, xml_bytes, "VCV000001001.3")],
        )
        self.assertTrue(result.passed, result.errors)
        evidence = result.items[0]
        self.assertEqual(evidence.condition_names, ())
        self.assertEqual(
            evidence.condition_references,
            ("MedGen:C123456",),
        )
        submission = evidence.submissions[0]
        self.assertEqual(submission.condition_names, ())
        self.assertEqual(
            submission.condition_references,
            ("MedGen:C123456",),
        )
        self.assertEqual(evidence.to_dict().get("condition_name_observations"), [])
        self.assertEqual(submission.to_dict().get("condition_name_observations"), [])

    def test_malformed_model_and_source_registry_fail_closed(self) -> None:
        forged_model = replace(self.simple_model, rules=(None,))
        try:
            result = self.build(
                forged_model,
                [item(self.simple_model, self.simple_xml, "VCV000001001.3")],
            )
        except (AttributeError, TypeError, ValueError) as exc:
            self.fail(f"Malformed model escaped as {type(exc).__name__}")
        self.assertFalse(result.passed)
        self.assertIn("canonical_model_not_verified", result.errors)

        forged_registry = source_registry()
        forged_registry["sources"][0]["license"] = "unknown"
        result = self.build(
            self.simple_model,
            [item(self.simple_model, self.simple_xml, "VCV000001001.3")],
            registry=forged_registry,
        )
        self.assertFalse(result.passed)
        self.assertIn("clinvar_source_contract_not_verified", result.errors)

    def test_non_ascii_integer_attributes_fail_closed_without_exception(self) -> None:
        cases = (
            (
                self.simple_xml.replace(b'Version="3"', 'Version="²"'.encode("utf-8"), 1),
                "clinvar_archive_invalid",
            ),
            (
                self.simple_xml.replace(b'VariationID="1001"', 'VariationID="²"'.encode("utf-8"), 1),
                "clinvar_archive_invalid",
            ),
            (
                self.simple_xml.replace(b'Version="2"', 'Version="²"'.encode("utf-8"), 1),
                "clinvar_submission_invalid",
            ),
            (
                self.simple_xml.replace(
                    b'NumberOfSubmissions="1"',
                    'NumberOfSubmissions="²"'.encode("utf-8"),
                    1,
                ),
                "clinvar_submission_count_mismatch",
            ),
        )
        for xml_bytes, expected_error in cases:
            with self.subTest(expected_error=expected_error):
                try:
                    result = self.build(
                        self.simple_model,
                        [item(self.simple_model, xml_bytes, "VCV000001001.3")],
                    )
                except (TypeError, ValueError) as exc:
                    self.fail(
                        f"Unicode numeric attribute escaped as {type(exc).__name__}"
                    )
                self.assertFalse(result.passed)
                self.assertIn(expected_error, result.errors)

    def test_clinvar_no_conflicts_aggregate_is_authoritative_over_differing_scvs(self) -> None:
        xml_bytes = self.conflict_xml
        xml_bytes = xml_bytes.replace(
            b"criteria provided, conflicting classifications",
            b"criteria provided, multiple submitters, no conflicts",
            1,
        )
        xml_bytes = xml_bytes.replace(
            b"Conflicting classifications of pathogenicity",
            b"Pathogenic/Likely pathogenic",
            1,
        )
        xml_bytes = xml_bytes.replace(b"Likely benign", b"Pathogenic", 1)
        xml_bytes = xml_bytes.replace(
            b"Uncertain significance",
            b"Likely pathogenic",
            1,
        )
        result = self.build(
            self.conflict_model,
            [item(self.conflict_model, xml_bytes, "VCV000002002.4")],
        )
        self.assertTrue(result.passed, result.errors)
        self.assertFalse(result.items[0].conflict)
        self.assertEqual(
            {submission.classification for submission in result.items[0].submissions},
            {"Pathogenic", "Likely pathogenic"},
        )

    def test_metadata_schema_manifest_and_public_source_registry_contract(self) -> None:
        metadata_value = metadata(self.simple_xml, "VCV000001001.3")
        Draft202012Validator(json.loads(METADATA_SCHEMA.read_text())).validate(metadata_value)

        manifest = json.loads(CAPABILITY_MANIFEST.read_text())
        Draft202012Validator(json.loads(CAPABILITY_SCHEMA.read_text())).validate(manifest)
        self.assertEqual(manifest["capability_id"], "minimum-evidence-snapshot")
        self.assertIn("conflict", manifest["known_issues"].lower())

        registry = json.loads(SOURCE_REGISTRY.read_text())
        source = next(
            source
            for source in registry["sources"]
            if source["source_id"] == "ncbi-clinvar-vcv"
        )
        self.assertEqual(
            source["rights_status"],
            "UNRESTRICTED_USE_WITH_ATTRIBUTION_REQUESTED",
        )
        self.assertEqual(source["lifecycle"], "MUTABLE_PER_RECORD_SNAPSHOT")

    def test_tool_neutral_output_contains_no_clinical_reclassification(self) -> None:
        result = self.build(
            self.conflict_model,
            [item(self.conflict_model, self.conflict_xml, "VCV000002002.4")],
        )
        payload = result.to_dict()
        self.assertEqual(payload["capability_id"], "minimum-evidence-snapshot")
        self.assertEqual(payload["canonical_status"], "SNAPSHOT_VERIFIED")
        self.assertEqual(payload["canonical_payload"]["artifact_kind"], "EVIDENCE_SNAPSHOT")
        self.assertEqual(payload["executor_id"], "omnigenis.python-stdlib")
        serialized = json.dumps(payload, sort_keys=True).lower()
        self.assertNotIn("acmg_classification", serialized)
        self.assertNotIn("project_priority", serialized)
        self.assertNotIn("clinical_interpretation", serialized)


if __name__ == "__main__":
    unittest.main()
