from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import unittest

from omnigenis.capabilities.gvcf_intake import validate_gvcf_bytes
from test_foundation_schemas import validate_subset

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "gvcf_intake"
SCHEMA = ROOT / "schemas" / "gvcf-input-profile.v1.schema.json"
PROFILE = ROOT / "resources" / "input-profiles" / "gatk-haplotypecaller-4.6.2.0-vcf42-grch38-v1.json"
MANIFEST = ROOT / "capabilities" / "gvcf-intake.manifest.json"
PROFILE_ID = "gatk-haplotypecaller-4.6.2.0-vcf42-grch38-v1"
COLUMNS = ("chrom", "pos", "id", "ref", "alt", "qual", "filter", "info", "format", "sample")


class GvcfIntakeCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = (FIXTURES / "valid-single-sample.gvcf").read_bytes()
        self.profile = json.loads((FIXTURES / "valid-input-profile.json").read_text(encoding="utf-8"))

    def bound_profile(self, data: bytes) -> dict[str, object]:
        profile = copy.deepcopy(self.profile)
        profile["size_bytes"] = len(data)
        profile["content_sha256"] = hashlib.sha256(data).hexdigest()
        return profile

    def parse(self, data: bytes, profile: dict[str, object] | None = None):
        return validate_gvcf_bytes(data, self.bound_profile(data) if profile is None else profile)

    def changed_record(self, index: int, **changes: str) -> bytes:
        lines = self.data.decode("utf-8").splitlines()
        positions = [i for i, line in enumerate(lines) if not line.startswith("#")]
        fields = lines[positions[index]].split("\t")
        for key, value in changes.items():
            fields[COLUMNS.index(key)] = value
        lines[positions[index]] = "\t".join(fields)
        return ("\n".join(lines) + "\n").encode("utf-8")

    def with_records(self, records: list[str], extra_meta: list[str] | None = None) -> bytes:
        lines = self.data.decode("utf-8").splitlines()
        meta = [line for line in lines if line.startswith("##")]
        header = next(line for line in lines if line.startswith("#CHROM"))
        return ("\n".join(meta + (extra_meta or []) + [header] + records) + "\n").encode("utf-8")

    def assert_refused(self, data: bytes, profile: dict[str, object] | None = None):
        result = self.parse(data, profile)
        self.assertFalse(result.is_valid)
        self.assertTrue(result.errors)
        envelope = result.to_dict()
        self.assertEqual(envelope["canonical_status"], "INVALID")
        self.assertEqual(envelope["canonical_payload"]["records"], [])
        return result

    def test_synthetic_fixture_binds_identity_and_observation_counts(self) -> None:
        self.assertEqual(self.profile["content_sha256"], hashlib.sha256(self.data).hexdigest())
        self.assertEqual(self.profile["size_bytes"], len(self.data))
        result = validate_gvcf_bytes(self.data, self.profile)
        self.assertTrue(result.is_valid, result.errors)
        self.assertEqual(result.sample_id, "SYNTHETIC_GVCF")
        self.assertEqual(result.record_count, 4)
        self.assertEqual(result.reference_block_count, 2)
        self.assertEqual(result.variant_record_count, 2)
        self.assertEqual(result.missing_genotype_count, 2)
        self.assertEqual(result.content_sha256, self.profile["content_sha256"])
        self.assertEqual(result.size_bytes, len(self.data))

    def test_capability_envelope_is_structural_and_explicitly_limited(self) -> None:
        result = self.parse(self.data)
        self.assertTrue(result.is_valid, result.errors)
        envelope = result.to_dict()
        self.assertEqual(envelope["capability_id"], "gvcf-intake")
        self.assertEqual(envelope["capability_version"], "1.0.0")
        self.assertEqual(envelope["canonical_status"], "VALID")
        self.assertEqual(envelope["availability"], "AVAILABLE")
        self.assertEqual(envelope["canonical_payload"]["errors"], [])
        self.assertTrue(envelope["limitations"])
        self.assertIsNone(envelope["model_id"])
        self.assertEqual(set(envelope), {
            "capability_id", "capability_version", "canonical_status", "canonical_payload",
            "availability", "limitations", "executor_id", "executor_version",
            "adapter_version", "model_id", "resource_release", "reference_bundle",
            "execution_profile", "raw_artifact_refs", "provenance_refs",
        })

    def test_records_retain_source_order_text_line_number_and_digest(self) -> None:
        result = self.parse(self.data)
        self.assertTrue(result.is_valid, result.errors)
        observations = result.to_dict()["canonical_payload"]["records"]
        originals = [(i, line) for i, line in enumerate(self.data.decode("utf-8").splitlines(), 1)
                     if not line.startswith("#")]
        self.assertEqual(len(observations), len(originals))
        for observation, (number, original) in zip(observations, originals):
            self.assertEqual(observation["source_line_number"], number)
            self.assertEqual(observation["raw_record"], original)
            self.assertEqual(observation["record_sha256"], hashlib.sha256(original.encode("utf-8")).hexdigest())
        self.assertEqual([record["pos"] for record in observations], [1, 10, 20, 35])
        self.assertEqual(observations[1]["alts"], ["G", "<NON_REF>"])
        self.assertEqual(observations[1]["format_values"]["PL"], "70,0,80,90,100,110")

    def test_reference_block_end_and_gaps_are_not_expanded_or_imputed(self) -> None:
        result = self.parse(self.data)
        self.assertTrue(result.is_valid, result.errors)
        records = result.to_dict()["canonical_payload"]["records"]
        self.assertEqual([(r["kind"], r["pos"], r["end"]) for r in records], [
            ("REFERENCE_BLOCK", 1, 9), ("VARIANT", 10, 10),
            ("REFERENCE_BLOCK", 20, 29), ("VARIANT", 35, 35),
        ])
        self.assertEqual(len(records), 4)

    def test_missing_and_partial_genotypes_remain_unknown_with_source_phase(self) -> None:
        result = self.parse(self.data)
        self.assertTrue(result.is_valid, result.errors)
        records = result.to_dict()["canonical_payload"]["records"]
        self.assertEqual([r["genotype_status"] for r in records], ["CALLED", "CALLED", "MISSING", "PARTIAL"])
        self.assertEqual([r["gt"] for r in records], ["0/0", "0|1", "./.", "0/."])
        self.assertEqual([r["phase_separator"] for r in records], ["/", "|", "/", "/"])
        self.assertEqual(records[2]["format_values"]["DP"], ".")
        self.assertEqual(records[3]["format_values"]["PL"], ".")
        phased = self.parse(self.changed_record(0, sample="0|0:20:20:14:0,60,90"))
        self.assertTrue(phased.is_valid, phased.errors)
        observation = phased.to_dict()["canonical_payload"]["records"][0]
        self.assertEqual(observation["gt"], "0|0")
        self.assertEqual(observation["phase_separator"], "|")
        self.assertEqual(observation["genotype_status"], "CALLED")
        self.assertNotIn("phase_confidence", observation)

    def test_crlf_preserves_observations_and_binds_actual_input_bytes(self) -> None:
        data = self.data.replace(b"\n", b"\r\n")
        result = self.parse(data)
        self.assertTrue(result.is_valid, result.errors)
        self.assertEqual(result.content_sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(result.to_dict()["canonical_payload"]["records"],
                         self.parse(self.data).to_dict()["canonical_payload"]["records"])

    def test_quoted_metadata_and_unconsumed_meta_lines_are_retained(self) -> None:
        data = self.data.replace(b'Description="Read depth"', b'Description="Depth, with \\"quoted\\" text"')
        result = self.parse(data)
        self.assertTrue(result.is_valid, result.errors)
        self.assertEqual(result.to_dict()["canonical_payload"]["meta_lines"],
                         [line for line in data.decode("utf-8").splitlines() if line.startswith("##")])

    def test_declared_optional_phase_and_strand_fields_preserve_source_values(self) -> None:
        records = [line for line in self.data.decode("utf-8").splitlines() if not line.startswith("#")]
        fields = records[1].split("\t")
        fields[8] += ":SB:PGT:PID:PS"
        fields[9] = "1|0:20:30:12,8,0:70,0,80,90,100,110:4,3,2,1:0|1:synthetic_block:10"
        records[1] = "\t".join(fields)
        data = self.with_records(records, [
            '##FORMAT=<ID=SB,Number=4,Type=Integer,Description="Strand counts">',
            '##FORMAT=<ID=PGT,Number=1,Type=String,Description="Physical genotype">',
            '##FORMAT=<ID=PID,Number=1,Type=String,Description="Physical phase identifier">',
            '##FORMAT=<ID=PS,Number=1,Type=Integer,Description="Phase set">',
        ])
        result = self.parse(data)
        self.assertTrue(result.is_valid, result.errors)
        record = result.to_dict()["canonical_payload"]["records"][1]
        self.assertEqual(record["gt"], "1|0")
        self.assertEqual(record["format_values"]["PGT"], "0|1")
        self.assertEqual(record["format_values"]["PID"], "synthetic_block")
        self.assertEqual(record["format_values"]["SB"], "4,3,2,1")
        self.assertEqual(record["format_values"]["PS"], "10")

    def test_anchored_one_base_insertions_and_deletions_are_retained(self) -> None:
        for ref, alt, end in (("A", "AT,<NON_REF>", 10), ("AT", "A,<NON_REF>", 11)):
            with self.subTest(ref=ref, alt=alt):
                result = self.parse(self.changed_record(1, ref=ref, alt=alt))
                self.assertTrue(result.is_valid, result.errors)
                record = result.to_dict()["canonical_payload"]["records"][1]
                self.assertEqual(record["ref"], ref)
                self.assertEqual(record["alts"], alt.split(","))
                self.assertEqual(record["end"], end)

    def test_profile_requires_exact_declared_version_kind_and_reference(self) -> None:
        changes = {
            "schema_version": "2.0.0", "profile_id": "unregistered",
            "input_kind": "VCF", "media_type": "application/octet-stream",
            "caller_id": "other.Caller", "caller_version": "4.6.1.0",
            "reference_assembly": "GRCh37",
        }
        for key, value in changes.items():
            with self.subTest(field=key):
                profile = self.bound_profile(self.data)
                profile[key] = value
                self.assert_refused(self.data, profile)

    def test_profile_hash_size_and_sample_must_match_observed_input(self) -> None:
        for key, value in (("content_sha256", "0" * 64), ("size_bytes", len(self.data) + 1),
                           ("sample_id", "DIFFERENT_SAMPLE"), ("input_id", ""),
                           ("sample_id", "SYNTHETIC_GVCF\n"),
                           ("content_sha256", self.profile["content_sha256"] + "\n")):
            with self.subTest(field=key):
                profile = self.bound_profile(self.data)
                profile[key] = value
                self.assert_refused(self.data, profile)

    def test_profile_rejects_extra_properties_wrong_types_and_non_tokens(self) -> None:
        for key, value in (("unexpected", 1), ("size_bytes", True), ("size_bytes", str(len(self.data))),
                           ("sample_id", "has space"), ("sample_id", "bad\x7f"),
                           ("input_id", "x" * 257), ("sample_id", "x" * 257)):
            with self.subTest(field=key, value=repr(value)):
                profile = self.bound_profile(self.data)
                profile[key] = value
                self.assert_refused(self.data, profile)

        for sample in ("\u0080", "\u200b", "\u00e9"):
            with self.subTest(matched_non_ascii_sample=repr(sample)):
                data = self.data.replace(b"SYNTHETIC_GVCF", sample.encode("utf-8"), 1)
                profile = self.bound_profile(data)
                profile["sample_id"] = sample
                self.assert_refused(data, profile)

    def test_profile_missing_required_fields_is_refused(self) -> None:
        for key in self.profile:
            with self.subTest(field=key):
                profile = self.bound_profile(self.data)
                del profile[key]
                self.assert_refused(self.data, profile)

    def test_profile_schema_accepts_fixture_and_rejects_structural_mismatches(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertTrue(schema["$id"].startswith("urn:omnigenis:schema:"))
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(set(schema["required"]), set(self.profile))
        self.assertEqual(validate_subset(schema, self.profile), [])
        for key, value in (("size_bytes", True), ("unexpected", "value"),
                           ("content_sha256", "not-a-digest"), ("input_kind", "VCF"),
                           ("sample_id", "has space"), ("sample_id", "\u0080"),
                           ("sample_id", "\u200b"), ("sample_id", "\u00e9"),
                           ("sample_id", "SYNTHETIC_GVCF\n"),
                           ("content_sha256", self.profile["content_sha256"] + "\n")):
            with self.subTest(field=key):
                profile = copy.deepcopy(self.profile)
                profile[key] = value
                self.assertTrue(validate_subset(schema, profile))

    def test_declared_resource_profile_pins_identity_limits_and_disabled_state(self) -> None:
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        self.assertEqual(profile["profile_id"], PROFILE_ID)
        self.assertEqual(profile["caller"]["id"], "gatk.HaplotypeCaller")
        self.assertEqual(profile["caller"]["version"], "4.6.2.0")
        self.assertEqual(profile["caller"]["source_commit"], "76edc75c26504da94bbaee66584e107e76ee15de")
        self.assertEqual(profile["reference"]["assembly"], "GRCh38")
        self.assertIs(profile["default_enabled"], False)
        self.assertEqual(profile["limits"], {
            "max_bytes": 1048576, "max_records": 32, "max_line_bytes": 65536,
            "max_header_lines": 256, "max_numeric_digits": 10, "max_integer": 2147483647,
        })
        self.assertTrue(profile["sources"])
        self.assertTrue(profile["known_issues"])

    def test_manifest_has_activation_contract_without_default_pipeline_activation(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        required = {
            "schema_version", "capability_id", "capability_version", "real_use_case",
            "owner_priority", "upstream_status", "license", "supported_profile",
            "resource_budget", "input_fixture", "output_contract", "known_issues",
            "security", "disable_path",
        }
        self.assertTrue(required.issubset(manifest))
        self.assertEqual(manifest["capability_id"], "gvcf-intake")
        self.assertEqual(manifest["capability_version"], "1.0.0")
        self.assertEqual(manifest["input_fixture"], "tests/fixtures/gvcf_intake/valid-single-sample.gvcf")
        self.assertIn("default", manifest["disable_path"].lower())

    def test_caller_declarations_must_be_present_unique_and_exact(self) -> None:
        changes = (
            (b"##source=HaplotypeCaller\n", b""),
            (b"##source=HaplotypeCaller\n", b"##source=OtherCaller\n"),
            (b"##source=HaplotypeCaller\n", b"##source=HaplotypeCaller\n##source=HaplotypeCaller\n"),
            (b'Version="4.6.2.0"', b'Version="4.6.1.0"'),
            (b"ID=HaplotypeCaller,Version", b"ID=OtherCaller,Version"),
        )
        for before, after in changes:
            with self.subTest(before=before, after=after):
                self.assert_refused(self.data.replace(before, after, 1))

    def test_required_metadata_definitions_have_unique_ids_and_exact_shapes(self) -> None:
        lines = self.data.splitlines(keepends=True)
        for prefix in (b"##ALT=<ID=NON_REF,", b"##INFO=<ID=END,", b"##FORMAT=<ID=GT,",
                       b"##FORMAT=<ID=DP,", b"##FORMAT=<ID=GQ,", b"##FORMAT=<ID=MIN_DP,",
                       b"##FORMAT=<ID=PL,", b"##FORMAT=<ID=AD,"):
            definition = next(line for line in lines if line.startswith(prefix))
            with self.subTest(case="missing", prefix=prefix):
                self.assert_refused(self.data.replace(definition, b"", 1))
            with self.subTest(case="duplicate", prefix=prefix):
                self.assert_refused(self.data.replace(definition, definition * 2, 1))
        for before, after in ((b"ID=PL,Number=G,Type=Integer", b"ID=PL,Number=R,Type=Integer"),
                              (b"ID=END,Number=1,Type=Integer", b"ID=END,Number=1,Type=String"),
                              (b"ID=GT,Number=1,Type=String", b"ID=GT,Number=2,Type=String")):
            with self.subTest(shape=after):
                self.assert_refused(self.data.replace(before, after, 1))

    def test_band_declarations_match_encoded_ranges_without_overlap(self) -> None:
        band = b"##GVCFBlock0-60=minGQ=0(inclusive),maxGQ=60(exclusive)\n"
        for replacement in (
            b"##GVCFBlock0-60=minGQ=1(inclusive),maxGQ=60(exclusive)\n",
            b"##GVCFBlock0-101=minGQ=0(inclusive),maxGQ=101(exclusive)\n",
            band + b"##GVCFBlock30-60=minGQ=30(inclusive),maxGQ=60(exclusive)\n",
            band + band,
        ):
            with self.subTest(replacement=replacement):
                self.assert_refused(self.data.replace(band, replacement, 1))
        no_bands = b"\n".join(line for line in self.data.splitlines() if not line.startswith(b"##GVCFBlock")) + b"\n"
        self.assert_refused(no_bands)
        narrow = self.data.replace(band, b"##GVCFBlock0-10=minGQ=0(inclusive),maxGQ=10(exclusive)\n")
        self.assert_refused(narrow)

    def test_used_contigs_require_unique_grch38_declarations(self) -> None:
        declaration = b"##contig=<ID=1,length=1000,assembly=GRCh38>\n"
        for replacement in (b"", declaration * 2, declaration.replace(b"GRCh38", b"GRCh37"),
                            declaration.replace(b"length=1000", b"length=0")):
            with self.subTest(replacement=replacement):
                self.assert_refused(self.data.replace(declaration, replacement, 1))
        for chrom in ("chr1", "X", "MT", "0", "23", "2"):
            with self.subTest(chrom=chrom):
                self.assert_refused(self.changed_record(0, chrom=chrom))

    def test_malformed_structured_metadata_cannot_hide_duplicate_keys(self) -> None:
        for before, after in (
            (b"ID=END,Number=1,Type=Integer", b"ID=END,Number=1,Number=1,Type=Integer"),
            (b'ID=DP,Number=1,Type=Integer,Description="Read depth">',
             b'ID=DP,Number=1,Type=Integer,Description="Read depth>'),
            (b"ID=1,length=1000,assembly=GRCh38", b"ID=1,length=1000,length=1000,assembly=GRCh38"),
        ):
            with self.subTest(after=after):
                self.assert_refused(self.data.replace(before, after, 1))

    def test_invalid_encoding_controls_and_newline_separators_are_refused(self) -> None:
        cases = [b"\xef\xbb\xbf" + self.data, self.data + b"\xff",
                 self.data.replace(b"synthetic_snv", b"synthetic\x01snv"),
                 self.data.replace(b"\n", b"\r"),
                 self.data.replace(b"\n", "\u2028".encode("utf-8"), 1)]
        for data in cases:
            with self.subTest(prefix=data[:20]):
                self.assert_refused(data)

    def test_fileformat_and_single_sample_column_header_are_exact(self) -> None:
        header = next(line for line in self.data.splitlines(keepends=True) if line.startswith(b"#CHROM"))
        cases = [
            self.data.replace(b"VCFv4.2", b"VCFv4.5", 1),
            b"##fileformat=VCFv4.2\n" + self.data,
            self.data.replace(header, header * 2, 1),
            self.data.replace(header, header.rstrip(b"\n") + b"\tSECOND\n", 1),
            self.data.replace(header, header.replace(b"POS\tID", b"ID\tPOS"), 1),
            self.data.replace(header, header.replace(b"SYNTHETIC_GVCF", b""), 1),
        ]
        for data in cases:
            with self.subTest(header_case=data[:30]):
                self.assert_refused(data)

    def test_empty_header_only_late_header_and_unterminated_records_are_refused(self) -> None:
        header_only = b"\n".join(line for line in self.data.splitlines() if line.startswith(b"#")) + b"\n"
        for data in (b"", header_only, self.data.rstrip(b"\n"), self.data + b"\n",
                     self.data + b"##source=HaplotypeCaller\n"):
            with self.subTest(size=len(data)):
                self.assert_refused(data)

    def test_coordinates_and_reference_block_end_are_bounded(self) -> None:
        for changes in ({"pos": "0"}, {"pos": "-1"}, {"pos": "1001"},
                        {"info": "END=0"}, {"info": "END=1001"}, {"info": "END=1.5"},
                        {"info": "."}, {"pos": "10", "info": "END=9"}):
            with self.subTest(changes=changes):
                self.assert_refused(self.changed_record(0, **changes))
        self.assert_refused(self.changed_record(1, pos="1000", ref="AT", alt="A,<NON_REF>"))

    def test_order_duplicates_and_overlapping_spans_are_refused(self) -> None:
        records = [line for line in self.data.decode("utf-8").splitlines() if not line.startswith("#")]
        for changed in ([records[1], records[0]] + records[2:],
                        [records[0], records[0]] + records[1:]):
            with self.subTest(records=changed[:2]):
                self.assert_refused(self.with_records(changed))
        self.assert_refused(self.changed_record(1, pos="9"))
        self.assert_refused(self.changed_record(2, pos="10"))

    def test_identifiers_cannot_repeat_across_distinct_records(self) -> None:
        baseline = self.parse(self.data)
        self.assertTrue(baseline.is_valid, baseline.errors)
        original = [line for line in self.data.decode("utf-8").splitlines() if not line.startswith("#")]
        self.assertGreater(sum(line.split("\t")[2] == "." for line in original), 1)
        for first, second in (("duplicate_id", "duplicate_id"), ("first;shared", "second;shared")):
            with self.subTest(first=first, second=second):
                records = list(original)
                for index, identifier in ((1, first), (3, second)):
                    fields = records[index].split("\t")
                    fields[2] = identifier
                    records[index] = "\t".join(fields)
                self.assert_refused(self.with_records(records))

    def test_contigs_are_numeric_ascending_and_cannot_recur(self) -> None:
        block1 = "1\t1\t.\tA\t<NON_REF>\t.\tPASS\tEND=1\tGT:DP:GQ:MIN_DP:PL\t0/0:20:20:14:0,60,90"
        block2 = block1.replace("1\t1\t", "2\t1\t", 1)
        contig2 = ['##contig=<ID=2,length=1000,assembly=GRCh38>']
        good = self.parse(self.with_records([block1, block2], contig2))
        self.assertTrue(good.is_valid, good.errors)
        self.assert_refused(self.with_records([block2, block1], contig2))
        self.assert_refused(self.with_records([block1, block2, block1.replace("\t1\t", "\t3\t", 1).replace("END=1", "END=3")], contig2))

    def test_reference_blocks_require_single_base_and_nonreference_sentinel(self) -> None:
        for changes in ({"ref": "AT"}, {"ref": "N"}, {"ref": "a"},
                        {"alt": "<OTHER>"}, {"alt": "."},
                        {"sample": "0/1:20:20:14:0,60,90"}):
            with self.subTest(changes=changes):
                self.assert_refused(self.changed_record(0, **changes))

    def test_variants_reject_unsupported_alleles_and_block_end(self) -> None:
        for changes in ({"alt": "G"}, {"alt": "<NON_REF>,G"}, {"alt": "A,<NON_REF>"},
                        {"alt": "G,T,<NON_REF>"}, {"alt": "*,<NON_REF>"},
                        {"alt": "<DEL>,<NON_REF>"}, {"alt": "A]2:10],<NON_REF>"},
                        {"ref": "AC", "alt": "GT,<NON_REF>"},
                        {"ref": "A", "alt": "ATT,<NON_REF>"}, {"info": "END=10"}):
            with self.subTest(changes=changes):
                self.assert_refused(self.changed_record(1, **changes))

    def test_gt_requires_diploid_concrete_or_missing_alleles(self) -> None:
        for gt in ("0", "0/1/1", "0//1", "0/2", "2|2", "-1/0", "0\\1", "1.0/0"):
            with self.subTest(gt=gt):
                self.assert_refused(self.changed_record(1, sample=gt + ":20:30:12,8,0:70,0,80,90,100,110"))
        result = self.parse(self.changed_record(0, sample="0/.:20:20:14:0,60,90"))
        self.assertTrue(result.is_valid, result.errors)
        self.assertEqual(result.to_dict()["canonical_payload"]["records"][0]["genotype_status"], "PARTIAL")

    def test_format_requires_gt_first_unique_supported_tags_and_exact_fields(self) -> None:
        for changes in (
            {"format": "DP:GT:GQ:AD:PL", "sample": "20:0/1:30:12,8,0:70,0,80,90,100,110"},
            {"format": "GT:DP:GQ:AD:PL:DP", "sample": "0/1:20:30:12,8,0:70,0,80,90,100,110:20"},
            {"format": "GT:DP:GQ:AD:PL:UNKNOWN", "sample": "0/1:20:30:12,8,0:70,0,80,90,100,110:."},
            {"sample": "0/1:20:30:12,8,0"},
            {"sample": "0/1:20:30:12,8,0:70,0,80,90,100,110:extra"},
            {"sample": "0/1::30:12,8,0:70,0,80,90,100,110"},
            {"format": "GT:DP:GQ:PL", "sample": "0/1:20:30:70,0,80,90,100,110"},
        ):
            with self.subTest(changes=changes):
                self.assert_refused(self.changed_record(1, **changes))

    def test_pl_cardinality_counts_symbolic_allele_without_reordering(self) -> None:
        for index, sample in ((0, "0/0:20:20:14:0,60"),
                              (0, "0/0:20:20:14:0,60,90,100"),
                              (1, "0/1:20:30:12,8,0:70,0,80"),
                              (1, "0/1:20:30:12,8,0:70,0,80,90,100,-1")):
            with self.subTest(index=index, sample=sample):
                self.assert_refused(self.changed_record(index, sample=sample))

    def test_ad_and_strand_array_cardinality_and_values_are_checked(self) -> None:
        for ad in ("12,8", "12,8,0,0", "12,-8,0", "12,8.5,0"):
            with self.subTest(ad=ad):
                self.assert_refused(self.changed_record(1, sample=f"0/1:20:30:{ad}:70,0,80,90,100,110"))
        data = self.changed_record(0, format="GT:DP:GQ:MIN_DP:PL:AD", sample="0/0:20:20:14:0,60,90:20,0")
        result = self.parse(data)
        self.assertTrue(result.is_valid, result.errors)
        self.assert_refused(data.replace(b"20,0\n", b"20,0,0\n", 1))

    def test_numeric_observations_are_bounded_and_missing_is_explicit(self) -> None:
        for sample in ("0/0:-1:20:14:0,60,90", "0/0:20:100:14:0,60,90",
                       "0/0:20:20:21:0,60,90", "0/0:20:1.5:14:0,60,90",
                       "0/0:2147483648:20:14:0,60,90",
                       "0/0:" + "9" * 100 + ":20:14:0,60,90"):
            with self.subTest(sample=sample):
                self.assert_refused(self.changed_record(0, sample=sample))
        result = self.parse(self.changed_record(0, sample="0/0:2147483647:99:14:0,60,90"))
        self.assertTrue(result.is_valid, result.errors)

    def test_optional_format_fields_require_declarations_and_valid_values(self) -> None:
        for tag, value in (("SB", "1,2,3"), ("PGT", "0|2"), ("PS", "-1")):
            with self.subTest(tag=tag):
                self.assert_refused(self.changed_record(1, format=f"GT:DP:GQ:AD:PL:{tag}",
                    sample=f"0/1:20:30:12,8,0:70,0,80,90,100,110:{value}"))
        declarations = [
            '##FORMAT=<ID=SB,Number=4,Type=Integer,Description="Strand counts">',
            '##FORMAT=<ID=PGT,Number=1,Type=String,Description="Physical genotype">',
            '##FORMAT=<ID=PS,Number=1,Type=Integer,Description="Phase set">',
        ]
        for tag, value in (("SB", "1,2,3"), ("PGT", "0|2"), ("PS", "-1")):
            data = self.changed_record(1, format=f"GT:DP:GQ:AD:PL:{tag}",
                sample=f"0/1:20:30:12,8,0:70,0,80,90,100,110:{value}")
            rows = [line for line in data.decode("utf-8").splitlines() if not line.startswith("#")]
            with self.subTest(declared_tag=tag):
                self.assert_refused(self.with_records(rows, declarations))

    def test_pid_preserves_one_identifier_or_missing_and_refuses_multiple_values(self) -> None:
        declaration = ['##FORMAT=<ID=PID,Number=1,Type=String,Description="Physical phase identifier">']
        for value in ("block_a", "."):
            with self.subTest(valid_pid=value):
                data = self.changed_record(1, format="GT:DP:GQ:AD:PL:PID",
                    sample=f"0|1:20:30:12,8,0:70,0,80,90,100,110:{value}")
                records = [line for line in data.decode("utf-8").splitlines() if not line.startswith("#")]
                result = self.parse(self.with_records(records, declaration))
                self.assertTrue(result.is_valid, result.errors)
                self.assertEqual(result.to_dict()["canonical_payload"]["records"][1]["format_values"]["PID"], value)
        for value in ("block_a,block_b", "block_a,", ",block_a"):
            with self.subTest(invalid_pid=value):
                data = self.changed_record(1, format="GT:DP:GQ:AD:PL:PID",
                    sample=f"0|1:20:30:12,8,0:70,0,80,90,100,110:{value}")
                records = [line for line in data.decode("utf-8").splitlines() if not line.startswith("#")]
                self.assert_refused(self.with_records(records, declaration))

    def test_qual_must_be_missing_or_finite_nonnegative_numeric(self) -> None:
        result = self.parse(self.changed_record(1, qual="0.125"))
        self.assertTrue(result.is_valid, result.errors)
        for qual in ("NaN", "Inf", "-Inf", "-1", "1.2.3"):
            with self.subTest(qual=qual):
                self.assert_refused(self.changed_record(1, qual=qual))

    def test_filter_identifiers_are_declared_unique_and_retained(self) -> None:
        changed = self.changed_record(1, filter="LowDepth")
        records = [line for line in changed.decode("utf-8").splitlines() if not line.startswith("#")]
        meta = ['##FILTER=<ID=LowDepth,Description="Synthetic low depth">']
        data = self.with_records(records, meta)
        result = self.parse(data)
        self.assertTrue(result.is_valid, result.errors)
        self.assertIn("\tLowDepth\t", result.to_dict()["canonical_payload"]["records"][1]["raw_record"])
        self.assert_refused(changed)
        self.assert_refused(data.replace(b"\tLowDepth\t", b"\tLowDepth;LowDepth\t", 1))

    def test_info_retains_uninterpreted_annotations_and_rejects_ambiguous_fields(self) -> None:
        data = self.changed_record(1, info="NOTE=synthetic;FLAG")
        result = self.parse(data)
        self.assertTrue(result.is_valid, result.errors)
        self.assertIn("\tNOTE=synthetic;FLAG\t", result.to_dict()["canonical_payload"]["records"][1]["raw_record"])
        for info in ("NOTE=a;NOTE=b", "NOTE=", "=value", "NOTE=a;;FLAG"):
            with self.subTest(info=info):
                self.assert_refused(self.changed_record(1, info=info))
        self.assert_refused(self.changed_record(0, info="END=9;END=9"))

    def test_info_list_syntax_retains_values_missing_and_flags_but_refuses_empty_members(self) -> None:
        for info in ("NOTE=a,b", "NOTE=.", "FLAG"):
            with self.subTest(valid_info=info):
                result = self.parse(self.changed_record(1, info=info))
                self.assertTrue(result.is_valid, result.errors)
                record = result.to_dict()["canonical_payload"]["records"][1]
                self.assertEqual(record["raw_record"].split("\t")[7], info)
        for info in ("NOTE=a,,b", "NOTE=,a", "NOTE=a,"):
            with self.subTest(invalid_info=info):
                self.assert_refused(self.changed_record(1, info=info))

    def test_record_budget_accepts_limit_and_refuses_next_record(self) -> None:
        records = [f"1\t{pos}\t.\tA\t<NON_REF>\t.\tPASS\tEND={pos}\tGT:DP:GQ:MIN_DP:PL\t0/0:20:20:14:0,60,90"
                   for pos in range(1, 67, 2)]
        result = self.parse(self.with_records(records[:32]))
        self.assertTrue(result.is_valid, result.errors)
        self.assertEqual(result.record_count, 32)
        self.assert_refused(self.with_records(records))

    def test_header_budget_refuses_excess_unconsumed_metadata(self) -> None:
        records = [line for line in self.data.decode("utf-8").splitlines() if not line.startswith("#")]
        data = self.with_records(records, [f"##annotation_{i}=synthetic" for i in range(257)])
        self.assert_refused(data)

    def test_line_budget_refuses_oversized_well_formed_metadata_line(self) -> None:
        records = [line for line in self.data.decode("utf-8").splitlines() if not line.startswith("#")]
        data = self.with_records(records, ["##annotation=" + "a" * 65536])
        self.assert_refused(data)

    def test_total_byte_budget_refuses_many_individually_bounded_lines(self) -> None:
        records = [line for line in self.data.decode("utf-8").splitlines() if not line.startswith("#")]
        data = self.with_records(records, [f"##annotation_{i}=" + "a" * 60000 for i in range(18)])
        self.assertGreater(len(data), 1048576)
        self.assertLess(max(len(line) for line in data.splitlines()), 65536)
        self.assert_refused(data)

    def test_compressed_bytes_are_refused_without_decoding_as_plain_input(self) -> None:
        import gzip
        self.assert_refused(gzip.compress(self.data, mtime=0))

    def test_late_failure_exposes_no_partially_accepted_records_and_is_deterministic(self) -> None:
        data = self.changed_record(3, sample="0/2:12:.:5,7,0:.")
        first = self.assert_refused(data)
        second = self.assert_refused(data)
        self.assertEqual(first.errors, second.errors)
        self.assertEqual(first.to_dict(), second.to_dict())


if __name__ == "__main__":
    unittest.main()
