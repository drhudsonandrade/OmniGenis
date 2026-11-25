from __future__ import annotations

from contextlib import contextmanager
import copy
from dataclasses import FrozenInstanceError, is_dataclass, replace
import gzip
import hashlib
import inspect
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zlib

from jsonschema import Draft202012Validator

import omnigenis.capabilities.gvcf_artifact as artifact
from omnigenis.capabilities.artifact_gate import verify_artifact_bytes
from omnigenis.capabilities.gvcf_artifact import (
    GvcfArtifactResult,
    PreparedGvcfArtifact,
    prepare_gvcf_artifacts,
)
from omnigenis.capabilities.gvcf_intake import validate_gvcf_bytes
from omnigenis.capabilities.reference_identity import verify_reference_identity

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "gvcf_artifact"
PROFILE_PATH = (
    ROOT / "resources" / "gvcf-artifact-profiles"
    / "gatk-haplotypecaller-4.6.2.0-vcf42-grch38-bgzf-v1.json"
)
REFERENCE_PROFILE_PATH = (
    ROOT / "resources" / "reference-profiles"
    / "grch38-p14-ncbi-refseq-autosomal-v1.json"
)
EXECUTION_PATH = (
    ROOT / "resources" / "execution-profiles" / "single-sample-gvcf-artifact-v1.json"
)
RULE_IDS = (
    "STRICT_REPARSE", "CARDINALITY", "PHASE_SEMANTICS", "ORDERING",
    "INDEXABILITY", "REF_CONCORDANCE", "RETENTION_ACCOUNTING",
)
BGZF_EOF = bytes.fromhex(
    "1f8b08040000000000ff0600424302001b0003000000000000000000"
)
COLUMNS = ("chrom", "pos", "id", "ref", "alt", "qual", "filter", "info", "format", "sample")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def record_lines(data: bytes) -> list[str]:
    return [line for line in data.decode("utf-8").splitlines() if not line.startswith("#")]


def change_record(data: bytes, ordinal: int, **changes: str) -> bytes:
    lines = data.decode("utf-8").splitlines()
    positions = [i for i, line in enumerate(lines) if not line.startswith("#")]
    fields = lines[positions[ordinal]].split("\t")
    for key, value in changes.items():
        fields[COLUMNS.index(key)] = value
    lines[positions[ordinal]] = "\t".join(fields)
    return ("\n".join(lines) + "\n").encode("utf-8")


def independent_selected_lines(data: bytes, region: str) -> list[str]:
    """Test oracle for overlap; does not call the product's query helpers."""
    chrom, interval = region.split(":")
    low, high = (int(value) for value in interval.split("-"))
    selected = []
    for line in record_lines(data):
        fields = line.split("\t")
        start = int(fields[1])
        info = dict(
            entry.split("=", 1) for entry in fields[7].split(";") if "=" in entry
        )
        end = int(info.get("END", start + len(fields[3]) - 1))
        if fields[0] == chrom and start <= high and low <= end:
            selected.append(line)
    return selected


class GvcfArtifactFixtureTests(unittest.TestCase):
    """Pipeline units use explicit native/reference doubles, not native acceptance."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="gvcf-artifact-unit-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.data = (FIXTURES / "valid-reference-bound.gvcf").read_bytes()
        self.input_profile = load_json(FIXTURES / "valid-input-profile.json")
        self.execution_profile = load_json(EXECUTION_PATH)
        self.reference_profile = load_json(REFERENCE_PROFILE_PATH)
        self.identity = verify_reference_identity(
            self.reference_profile,
            load_json(ROOT / "resources" / "source-registry.v1.json"),
            load_json(
                ROOT / "resources" / "scientific-resource-bom"
                / "grch38-p14-ncbi-refseq-autosomal-v1.json"
            ),
            load_json(
                ROOT / "tests" / "fixtures" / "reference_identity"
                / "ncbi-grch38-p14.observation.json"
            ),
        )
        self.assertTrue(self.identity.passed)
        self.executable = self.directory / "unit-executor"
        self.executable.write_bytes(b"Explicit unit native-executor double.\n")
        self.executable.chmod(0o700)
        self.executor_digest = digest(self.executable.read_bytes())
        self.calls: list[tuple[list[str], Path, float, str]] = []
        self.native_mode = "normal"

    def bound_profile(self, data: bytes) -> dict:
        profile = copy.deepcopy(self.input_profile)
        profile["size_bytes"] = len(data)
        profile["content_sha256"] = digest(data)
        return profile

    def manifest(self, data: bytes) -> dict:
        return {
            "schema_version": "1.0.0",
            "artifact_id": "synthetic-source-" + digest(data),
            "artifact_kind": "gVCF",
            "media_type": "text/vcf",
            "sha256": digest(data),
            "size_bytes": len(data),
            "producer_execution_profile_id": "synthetic-fixture-v1",
            "parent_artifact_ids": [],
        }

    def run_double(self, args, *, cwd, deadline, stage, pass_fds=()):
        """Models declared command outputs; never presented as real index evidence."""
        arguments = [str(value) for value in args]
        self.calls.append((arguments, Path(cwd), deadline, stage))
        if self.native_mode == "failure-" + stage:
            raise artifact._Failure(stage, "explicit_unit_failure")
        if stage == "version":
            output = b"bcftools 1.24\nUsing htslib 1.24\n"
            if self.native_mode == "wrong-version":
                output = b"bcftools 1.25\nUsing htslib 1.25\n"
            return subprocess.CompletedProcess(arguments, 0, output, b"")
        if stage == "index":
            destination = Path(arguments[arguments.index("--output") + 1])
            if self.native_mode != "missing-index":
                content = b"explicit unit CSI artifact"
                if self.native_mode == "empty-index":
                    content = b""
                elif self.native_mode == "oversized-index":
                    content = b"x" * (1048576 + 1)
                destination.write_bytes(content)
            if self.native_mode == "binary-drift":
                self.executable.write_bytes(b"Changed binary during execution.\n")
            return subprocess.CompletedProcess(arguments, 0, b"", b"")
        self.assertEqual(stage, "query")
        if self.native_mode == "corrupt-index":
            raise artifact._Failure(stage, "explicit_unit_corrupt_index")
        region = arguments[arguments.index("-r") + 1]
        preserved = gzip.decompress(Path(arguments[-1]).read_bytes())
        selected = independent_selected_lines(preserved, region)
        if self.native_mode == "wrong-query" and selected:
            selected[0] = selected[0].replace("\t10001\t", "\t10002\t", 1)
        elif self.native_mode == "missing-query" and selected:
            selected = selected[1:]
        elif self.native_mode == "duplicate-query" and selected:
            selected.append(selected[0])
        elif self.native_mode == "gap-query" and not selected:
            selected = record_lines(preserved)[:1]
        elif self.native_mode == "lexical-query":
            changed = []
            for line in selected:
                fields = line.split("\t")
                if fields[5] != ".":
                    fields[5] = str(float(fields[5]))
                changed.append("\t".join(fields))
            selected = changed
        output = ("\n".join(selected) + ("\n" if selected else "")).encode("utf-8")
        return subprocess.CompletedProcess(arguments, 0, output, b"")

    def prepare(self, data=None, **overrides):
        payload = self.data if data is None else data
        values = {
            "input_profile": self.bound_profile(payload),
            "source_artifact_manifest": self.manifest(payload),
            "execution_profile": copy.deepcopy(self.execution_profile),
            "reference_profile": copy.deepcopy(self.reference_profile),
            "reference_identity": self.identity,
            "reference_fasta": self.directory / "explicit-reference-double.fa",
            "bcftools_executable": self.executable,
            "expected_executor_sha256": self.executor_digest,
            "timeout_seconds": 10,
        }
        values.update(overrides)
        with (
            patch.object(artifact, "_verify_reference", return_value=None),
            patch.object(artifact, "_run", side_effect=self.run_double),
        ):
            return prepare_gvcf_artifacts(payload, **values)

    def assert_refused(self, result) -> None:
        self.assertFalse(result.passed)
        self.assertEqual(result.artifacts, ())
        self.assertTrue(result.errors)
        self.assertEqual(len(result.rules), 7)
        self.assertEqual({rule.rule_id for rule in result.rules}, set(RULE_IDS))
        self.assertTrue(all(rule.status in {"PASS", "FAIL", "NOT_RUN"} for rule in result.rules))
        self.assertNotEqual({rule.status for rule in result.rules}, {"PASS"})

    def test_public_api_has_only_explicit_scoped_inputs(self) -> None:
        signature = inspect.signature(prepare_gvcf_artifacts)
        self.assertEqual(
            tuple(signature.parameters),
            (
                "data", "input_profile", "source_artifact_manifest",
                "execution_profile", "reference_profile", "reference_identity",
                "reference_fasta", "bcftools_executable",
                "expected_executor_sha256", "timeout_seconds",
            ),
        )
        for name, parameter in list(signature.parameters.items())[1:]:
            with self.subTest(parameter=name):
                self.assertEqual(parameter.kind, inspect.Parameter.KEYWORD_ONLY)

    def test_public_fixtures_and_manifests_conform(self) -> None:
        pairs = (
            ("capability-pack-manifest.v1.schema.json",
             ROOT / "capabilities" / "gvcf-artifact.manifest.json"),
            ("execution-profile.v1.schema.json", EXECUTION_PATH),
            ("gvcf-input-profile.v1.schema.json", FIXTURES / "valid-input-profile.json"),
        )
        for schema_name, value_path in pairs:
            with self.subTest(value=value_path.name):
                validator = Draft202012Validator(load_json(ROOT / "schemas" / schema_name))
                self.assertEqual(list(validator.iter_errors(load_json(value_path))), [])
        intake = validate_gvcf_bytes(self.data, self.input_profile)
        self.assertTrue(intake.is_valid, intake.errors)
        self.assertEqual((intake.record_count, intake.reference_block_count,
                          intake.variant_record_count), (5, 2, 3))
        self.assertEqual(self.input_profile["content_sha256"], digest(self.data))
        self.assertEqual(self.input_profile["size_bytes"], len(self.data))

    def test_artifact_profile_pins_the_narrow_operation_and_sources(self) -> None:
        profile = load_json(PROFILE_PATH)
        self.assertEqual(profile["operation"], "LOSSLESS_BGZF_AND_CSI")
        self.assertIs(profile["default_enabled"], False)
        self.assertEqual(profile["required_predicates"], list(RULE_IDS))
        self.assertEqual(profile["supported_domain"]["info_fields"],
                         {"reference_block": ["END"], "variant": []})
        self.assertEqual(profile["limits"]["input_bytes"], 1048576)
        self.assertEqual(profile["limits"]["records"], 32)
        self.assertEqual(profile["limits"]["bgzf_uncompressed_chunk_bytes"], 32768)
        self.assertEqual(profile["limits"]["max_executor_snapshot_bytes"], 16777216)
        self.assertEqual(profile["index"]["executor_version"], "1.24")
        self.assertEqual(profile["index"]["htslib_version"], "1.24")
        self.assertEqual(profile["compression"]["compression_level"], 6)
        self.assertEqual(profile["compression"]["mtime"], 0)
        self.assertEqual(profile["reference"]["alias_sha256"],
                         self.reference_profile["contig_alias_profile"]["sha256"])
        self.assertTrue(all(source.get("url", "").startswith("https://")
                            for source in profile["sources"]))

    def test_all_predicates_bind_an_atomic_lossless_artifact_pair(self) -> None:
        result = self.prepare()
        self.assertTrue(result.passed, result.errors)
        self.assertEqual({rule.rule_id: rule.status for rule in result.rules},
                         {rule: "PASS" for rule in RULE_IDS})
        self.assertIsInstance(result.artifacts, tuple)
        self.assertEqual(len(result.artifacts), 2)
        bgzf, csi = result.artifacts
        self.assertEqual(gzip.decompress(bgzf.content), self.data)
        self.assertTrue(bgzf.content.endswith(BGZF_EOF))
        self.assertEqual((bgzf.artifact_kind, csi.artifact_kind), ("gVCF_BGZF", "CSI"))
        self.assertEqual((bgzf.media_type, csi.media_type),
                         ("application/gzip", "application/octet-stream"))
        self.assertEqual(bgzf.parent_artifact_ids, (self.manifest(self.data)["artifact_id"],))
        self.assertEqual(csi.parent_artifact_ids, (bgzf.artifact_id,))
        self.assertEqual(len({result.source_artifact_id, bgzf.artifact_id, csi.artifact_id}), 3)
        for prepared in result.artifacts:
            manifest = prepared.to_manifest()
            self.assertTrue(verify_artifact_bytes(manifest, prepared.content).passed)
            self.assertEqual(prepared.sha256, digest(prepared.content))
            self.assertEqual(prepared.size_bytes, len(prepared.content))
            self.assertEqual(manifest["producer_execution_profile_id"],
                             "single-sample-gvcf-artifact-v1")
        self.assertEqual(result.source_sha256, digest(self.data))
        self.assertEqual(result.source_size_bytes, len(self.data))
        self.assertEqual(result.executor_sha256, self.executor_digest)
        self.assertEqual((result.executor_version, result.htslib_version), ("1.24", "1.24"))
        self.assertEqual(
            (result.input_record_count, result.retained_record_count,
             result.dropped_record_count, result.reference_block_count,
             result.variant_record_count, result.missing_genotype_count,
             result.partial_genotype_count), (5, 5, 0, 2, 3, 1, 1),
        )
        self.assertEqual(result.drop_reasons, ())

    def test_source_record_ledger_and_phase_are_exact(self) -> None:
        result = self.prepare()
        self.assertTrue(result.passed, result.errors)
        self.assertIsInstance(result.ledger, tuple)
        self.assertEqual(len(result.ledger), 5)
        for number, (entry, line) in enumerate(zip(result.ledger, record_lines(self.data)), 1):
            self.assertEqual((entry.source_record_ordinal, entry.output_record_ordinal),
                             (number, number))
            self.assertEqual(entry.source_record_sha256, digest(line.encode("utf-8")))
            self.assertEqual(entry.output_record_sha256, entry.source_record_sha256)
        reparsed = validate_gvcf_bytes(gzip.decompress(result.artifacts[0].content),
                                      self.input_profile)
        self.assertEqual([record.gt for record in reparsed.records],
                         ["0/0", "0|1", "./.", "0/.", "0/1"])
        self.assertEqual(dict(reparsed.records[1].format_values)["PGT"], "1|0")
        self.assertEqual(dict(reparsed.records[1].format_values)["PID"], "10010_C_A")
        self.assertEqual(dict(reparsed.records[1].format_values)["PS"], "10010")
        self.assertNotIn("phase_confidence", reparsed.records[1].to_dict())

    def test_index_evidence_includes_anchors_interiors_and_empty_gaps(self) -> None:
        result = self.prepare()
        self.assertTrue(result.passed, result.errors)
        evidence = {item.region: item for item in result.index_queries}
        for position in (10001, 10010, 10020, 10035, 10045):
            self.assertIn(f"1:{position}-{position}", evidence)
        for low, high in ((10001, 10009), (10020, 10029)):
            self.assertTrue(any(
                low < int(item.region.split(":")[1].split("-")[0]) <= high
                and item.expected_record_ordinals
                for item in evidence.values()
            ))
        self.assertTrue(any(not item.expected_record_ordinals for item in evidence.values()))
        for item in evidence.values():
            self.assertEqual(item.expected_record_ordinals, item.observed_record_ordinals)
            self.assertEqual(item.expected_record_keys, item.observed_record_keys)
        for args, _, _, stage in self.calls:
            if stage == "query":
                self.assertIn("--no-version", args)
                self.assertIn("-I", args)
                self.assertEqual(args[args.index("--regions-overlap") + 1], "1")

    def test_native_lexical_formatting_does_not_replace_source_bytes(self) -> None:
        self.native_mode = "lexical-query"
        result = self.prepare()
        self.assertTrue(result.passed, result.errors)
        self.assertEqual(gzip.decompress(result.artifacts[0].content), self.data)

    def test_rendered_query_can_grow_beyond_source_line_boundary(self) -> None:
        payload = change_record(self.data, 1, id="x", qual=".5")
        source_line = record_lines(payload)[1]
        identifier = "x" * (65536 - len(source_line.encode("utf-8")) + 1)
        payload = change_record(payload, 1, id=identifier)
        self.assertEqual(len(record_lines(payload)[1].encode("utf-8")), 65536)
        intake = validate_gvcf_bytes(payload, self.bound_profile(payload))
        self.assertTrue(intake.is_valid)
        self.native_mode = "lexical-query"
        result = self.prepare(payload)
        self.assertTrue(result.passed, result.errors)
        self.assertEqual(gzip.decompress(result.artifacts[0].content), payload)
        self.assertTrue(any(
            item.observed_record_keys and item.observed_record_keys[0][2] == identifier
            for item in result.index_queries
        ))

    def test_crlf_and_unused_metadata_are_preserved_byte_for_byte(self) -> None:
        payload = self.data.replace(
            b"##fixture=", b'##INFO=<ID=UNUSED,Number=A,Type=String,Description="Unused">\n##fixture='
        ).replace(b"\n", b"\r\n")
        result = self.prepare(payload)
        self.assertTrue(result.passed, result.errors)
        self.assertEqual(gzip.decompress(result.artifacts[0].content), payload)

    def test_results_nested_records_and_artifacts_are_immutable(self) -> None:
        result = self.prepare()
        for value in (result, result.artifacts[0], result.rules[0],
                      result.ledger[0], result.index_queries[0]):
            self.assertTrue(is_dataclass(value))
            self.assertTrue(type(value).__dataclass_params__.frozen)
            first_field = next(iter(type(value).__dataclass_fields__))
            with self.assertRaises(FrozenInstanceError):
                setattr(value, first_field, None)
        self.assertIsInstance(result.artifacts[0].parent_artifact_ids, tuple)
        baseline = copy.deepcopy(result.to_dict())
        detached = result.to_dict()
        detached["canonical_payload"]["test_only_change"] = ["not retained"]
        detached["limitations"].append("test only")
        self.assertEqual(result.to_dict(), baseline)
        manifest = result.artifacts[0].to_manifest()
        manifest["parent_artifact_ids"].append("not retained")
        self.assertEqual(result.artifacts[0].parent_artifact_ids,
                         (result.source_artifact_id,))

    def test_passed_requires_exact_rules_artifacts_and_no_errors(self) -> None:
        result = self.prepare()
        self.assertTrue(result.passed)
        variants = (
            replace(result, rules=result.rules[:-1]),
            replace(result, rules=result.rules[:-1] + (result.rules[0],)),
            replace(result, rules=result.rules + (result.rules[0],)),
            replace(result, rules=(replace(result.rules[0], status="NOT_RUN"),) + result.rules[1:]),
            replace(result, artifacts=()),
            replace(result, artifacts=result.artifacts[:1]),
            replace(result, errors=("explicit_unit_error",)),
        )
        for value in variants:
            self.assertFalse(value.passed)

    def test_detached_envelope_is_serializable_and_redacts_runtime_paths(self) -> None:
        result = self.prepare()
        envelope = result.to_dict()
        self.assertEqual(envelope["capability_id"], "gvcf-artifact")
        self.assertEqual(envelope["canonical_status"], "PASS")
        text = json.dumps(envelope, sort_keys=True)
        self.assertNotIn(str(self.directory), text)
        self.assertIn(zlib.ZLIB_VERSION, text)
        self.assertIn(zlib.ZLIB_RUNTIME_VERSION, text)
        self.assertIn(self.identity.fasta_content_sha256, text)
        self.assertIn("4.6.2.0", text)

    def test_invalid_execution_profiles_and_timeout_bounds_refuse_before_execution(self) -> None:
        invalid = []
        for key, value in (
            ("deterministic", 1), ("executor_version", "2.0.0"),
            ("execution_profile_id", "other"), ("schema_version", "2.0.0"),
        ):
            profile = copy.deepcopy(self.execution_profile)
            profile[key] = value
            invalid.append(profile)
        invalid.append({**self.execution_profile, "unapproved": True})
        invalid.append({key: value for key, value in self.execution_profile.items()
                        if key != "executor_id"})
        for profile in invalid:
            self.calls.clear()
            self.assert_refused(self.prepare(execution_profile=profile))
            self.assertEqual(self.calls, [])
        for timeout in (0, -1, 601, True, 1.5, "10"):
            self.calls.clear()
            self.assert_refused(self.prepare(timeout_seconds=timeout))
            self.assertEqual(self.calls, [])

    def test_source_manifest_identity_type_digest_and_size_are_required(self) -> None:
        for key, value in (
            ("artifact_kind", "VCF"), ("media_type", "application/gzip"),
            ("artifact_id", ""), ("sha256", "0" * 64), ("size_bytes", len(self.data) + 1),
        ):
            manifest = self.manifest(self.data)
            manifest[key] = value
            self.calls.clear()
            self.assert_refused(self.prepare(source_artifact_manifest=manifest))
            self.assertEqual(self.calls, [])

    def test_source_profile_and_input_budgets_fail_before_execution(self) -> None:
        wrong_profile = self.bound_profile(self.data)
        wrong_profile["sample_id"] = "ANOTHER_SAMPLE"
        self.assert_refused(self.prepare(input_profile=wrong_profile))
        for data in (b"", b"x" * (1048576 + 1), self.data[:-1]):
            self.calls.clear()
            self.assert_refused(self.prepare(data))
            self.assertEqual(self.calls, [])
        # Match the existing typed bytes API: programmer misuse is a TypeError.
        with patch.object(artifact, "_run", side_effect=self.run_double):
            with self.assertRaises(TypeError):
                prepare_gvcf_artifacts(
                    "not bytes", input_profile=self.input_profile,
                    source_artifact_manifest=self.manifest(self.data),
                    execution_profile=self.execution_profile,
                    reference_profile=self.reference_profile,
                    reference_identity=self.identity,
                    reference_fasta=self.directory / "unused.fa",
                    bcftools_executable=self.executable,
                    expected_executor_sha256=self.executor_digest,
                )
        self.assertEqual(self.calls, [])

    def test_present_info_outside_end_domain_refuses_before_execution(self) -> None:
        for ordinal, info in ((0, "END=10009;DP=20"), (1, "NOTE=synthetic"), (1, "FLAG")):
            self.calls.clear()
            self.assert_refused(self.prepare(change_record(self.data, ordinal, info=info)))
            self.assertEqual(self.calls, [])

    def test_bad_cardinality_and_symbolic_genotype_are_refused(self) -> None:
        variants = (
            change_record(self.data, 1, alt="<NON_REF>,A"),
            change_record(self.data, 1, alt="A,G,<NON_REF>"),
            change_record(self.data, 4, sample="0/1:18:25:10,8:60,0,70,80,90,100"),
            change_record(self.data, 4, sample="0/1:18:25:10,8,0:60,0,70"),
            change_record(self.data, 4, sample="0/2:18:25:10,8,0:60,0,70,80,90,100"),
        )
        for payload in variants:
            self.calls.clear()
            self.assert_refused(self.prepare(payload))
            self.assertEqual(self.calls, [])

    def test_reordered_overlapping_and_duplicate_records_are_refused(self) -> None:
        lines = self.data.decode().splitlines()
        headers = [line for line in lines if line.startswith("#")]
        records = record_lines(self.data)
        for altered in (
            [records[1], records[0], *records[2:]],
            [records[0], records[0], *records[1:]],
        ):
            self.assert_refused(self.prepare(("\n".join(headers + altered) + "\n").encode()))
        self.assert_refused(self.prepare(change_record(self.data, 0, info="END=10010")))

    def test_wrong_executor_digest_version_and_drift_produce_no_artifacts(self) -> None:
        self.assert_refused(self.prepare(expected_executor_sha256="0" * 64))
        self.assertEqual(self.calls, [])
        for mode in ("wrong-version", "binary-drift"):
            self.native_mode = mode
            self.assert_refused(self.prepare())

    def test_executor_replacement_cannot_change_any_verified_launch(self) -> None:
        native_double = self.run_double
        stages = []
        descriptors = set()

        def replace_before_launch(args, *, cwd, deadline, stage, pass_fds=()):
            if not stages:
                replacement = self.directory / "replacement-executor"
                replacement.write_bytes(b"Unverified replacement; never execute.\n")
                replacement.chmod(0o700)
                replacement.replace(self.executable)
            stages.append(stage)
            self.assertEqual(digest(Path(args[0]).read_bytes()), self.executor_digest)
            self.assertTrue(pass_fds)
            for descriptor in pass_fds:
                descriptors.add(descriptor)
                with self.assertRaises(OSError):
                    os.write(descriptor, b"changed")
                with self.assertRaises(OSError):
                    os.ftruncate(descriptor, 0)
            return native_double(args, cwd=cwd, deadline=deadline, stage=stage,
                                 pass_fds=pass_fds)

        with patch.object(self, "run_double", side_effect=replace_before_launch):
            result = self.prepare()
        # Executed bytes stay verified; original-source drift still refuses promotion.
        self.assert_refused(result)
        self.assertEqual(set(stages), {"version", "index", "query"})
        self.assertEqual(len(descriptors), 1)
        for descriptor in descriptors:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_executor_snapshot_is_closed_after_native_failure(self) -> None:
        native_double = self.run_double
        for failed_stage in ("version", "index", "query"):
            descriptors = set()

            def observe_launch(args, *, cwd, deadline, stage, pass_fds=()):
                self.assertTrue(pass_fds)
                descriptors.update(pass_fds)
                return native_double(args, cwd=cwd, deadline=deadline, stage=stage,
                                     pass_fds=pass_fds)

            self.native_mode = "failure-" + failed_stage
            with patch.object(self, "run_double", side_effect=observe_launch):
                self.assert_refused(self.prepare())
            self.assertEqual(len(descriptors), 1)
            for descriptor in descriptors:
                with self.assertRaises(OSError):
                    os.fstat(descriptor)

    def test_oversized_executor_snapshot_is_refused_before_launch(self) -> None:
        with self.executable.open("wb") as stream:
            stream.truncate(16 * 1024 * 1024 + 1)
        self.executor_digest = digest(self.executable.read_bytes())
        self.assert_refused(self.prepare())
        self.assertEqual(self.calls, [])

    def test_native_failure_at_each_stage_discards_all_artifacts(self) -> None:
        for stage in ("version", "index", "query"):
            self.native_mode = "failure-" + stage
            result = self.prepare()
            self.assert_refused(result)
            self.assertNotIn(str(self.directory), json.dumps(result.to_dict()))

    def test_missing_empty_oversized_and_unreadable_indexes_are_refused(self) -> None:
        for mode in ("missing-index", "empty-index", "oversized-index", "corrupt-index"):
            self.native_mode = mode
            self.assert_refused(self.prepare())

    def test_index_queries_must_match_identity_count_and_empty_gaps(self) -> None:
        for mode in ("wrong-query", "missing-query", "duplicate-query", "gap-query"):
            self.native_mode = mode
            self.assert_refused(self.prepare())

    def test_late_reference_failure_has_no_promotable_partial_pair(self) -> None:
        with (
            patch.object(artifact, "_verify_reference",
                         side_effect=artifact._Failure("reference", "explicit_unit_reference_failure")),
            patch.object(artifact, "_run", side_effect=self.run_double),
        ):
            result = prepare_gvcf_artifacts(
                self.data, input_profile=self.input_profile,
                source_artifact_manifest=self.manifest(self.data),
                execution_profile=self.execution_profile,
                reference_profile=self.reference_profile,
                reference_identity=self.identity,
                reference_fasta=self.directory / "reference-double.fa",
                bcftools_executable=self.executable,
                expected_executor_sha256=self.executor_digest,
            )
        self.assert_refused(result)

    def test_bgzf_corruption_or_changed_source_is_not_promoted(self) -> None:
        encode = artifact._encode_bgzf
        valid = encode(self.data)
        crc_changed = bytearray(valid)
        first_size = struct.unpack_from("<H", crc_changed, 16)[0] + 1
        crc_changed[first_size - 8] ^= 1
        candidates = (
            valid[:-1], valid[:-28], bytes(crc_changed), valid + b"trailing",
            encode(self.data.replace(b"fixture_snv", b"changed_snv")),
        )
        for candidate in candidates:
            with patch.object(artifact, "_encode_bgzf", return_value=candidate):
                self.assert_refused(self.prepare())

    def test_repeated_preparation_is_deterministic_and_cleans_private_work(self) -> None:
        first = self.prepare()
        directories = {cwd for _, cwd, _, _ in self.calls if cwd != self.executable.parent}
        second = self.prepare()
        self.assertTrue(first.passed and second.passed)
        self.assertEqual(first.artifacts, second.artifacts)
        self.assertEqual(first.ledger, second.ledger)
        for directory in directories:
            self.assertFalse(directory.exists())

    def test_oversized_input_is_refused_before_hashing_or_strict_parsing(self) -> None:
        oversized = b"x" * (1048576 + 1)
        profile = self.bound_profile(oversized)
        manifest = self.manifest(oversized)
        original_hash = hashlib.sha256

        def bounded_hash(data=b"", *args, **kwargs):
            if data is oversized:
                raise AssertionError("oversized source must be refused before hashing")
            return original_hash(data, *args, **kwargs)

        with (
            patch.object(artifact.hashlib, "sha256", side_effect=bounded_hash),
            patch.object(artifact, "validate_gvcf_bytes",
                         side_effect=AssertionError("oversized source reached parser")),
            patch.object(artifact, "_run",
                         side_effect=AssertionError("oversized source reached executor")),
        ):
            result = prepare_gvcf_artifacts(
                oversized, input_profile=profile, source_artifact_manifest=manifest,
                execution_profile=self.execution_profile,
                reference_profile=self.reference_profile,
                reference_identity=self.identity,
                reference_fasta=self.directory / "unused.fa",
                bcftools_executable=self.executable,
                expected_executor_sha256=self.executor_digest,
            )
        self.assert_refused(result)
        self.assertIsNone(result.source_sha256)

    def test_zlib_provenance_is_captured_before_export(self) -> None:
        result = self.prepare()
        self.assertTrue(result.passed, result.errors)
        observed = copy.deepcopy(result.to_dict()["canonical_payload"]["zlib"])
        self.assertEqual(observed, {
            "compile_version": zlib.ZLIB_VERSION,
            "runtime_version": zlib.ZLIB_RUNTIME_VERSION,
        })
        with (
            patch.object(artifact.zlib, "ZLIB_VERSION", "changed-after-run"),
            patch.object(artifact.zlib, "ZLIB_RUNTIME_VERSION", "changed-after-run"),
        ):
            self.assertEqual(result.to_dict()["canonical_payload"]["zlib"], observed)

    def test_failed_strict_intake_does_not_assert_a_verified_caller_declaration(self) -> None:
        invalid = self.data.replace(b"##source=HaplotypeCaller", b"##source=UnsupportedCaller")
        result = self.prepare(invalid)
        self.assert_refused(result)
        declaration = result.to_dict()["canonical_payload"]["caller_declaration"]
        self.assertEqual(declaration["verification"], "NOT_VERIFIED")
        valid = self.prepare()
        self.assertEqual(valid.to_dict()["canonical_payload"]["caller_declaration"]["verification"],
                         "DECLARED_ONLY")


class GvcfBgzfEncodingTests(unittest.TestCase):
    def test_boundary_vectors_are_lossless_deterministic_valid_members(self) -> None:
        sizes = (0, 1, 32767, 32768, 32769, 65536, 1048576)
        for size in sizes:
            with self.subTest(size=size):
                source = bytes((index * 131 + index // 251) % 256 for index in range(size))
                encoded = artifact._encode_bgzf(source)
                self.assertEqual(encoded, artifact._encode_bgzf(source))
                self.assertTrue(encoded.endswith(BGZF_EOF))
                self.assertEqual(gzip.decompress(encoded), source)
                offset = 0
                decoded = []
                while offset < len(encoded):
                    self.assertEqual(encoded[offset:offset + 4], b"\x1f\x8b\x08\x04")
                    self.assertEqual(encoded[offset + 4:offset + 8], b"\x00" * 4)
                    self.assertEqual(encoded[offset + 10:offset + 16], b"\x06\x00BC\x02\x00")
                    size_bytes = struct.unpack_from("<H", encoded, offset + 16)[0] + 1
                    self.assertLessEqual(size_bytes, 65536)
                    member = encoded[offset:offset + size_bytes]
                    plain = zlib.decompress(member[18:-8], wbits=-15)
                    crc, isize = struct.unpack("<II", member[-8:])
                    self.assertEqual(crc, zlib.crc32(plain) & 0xffffffff)
                    self.assertEqual(isize, len(plain))
                    self.assertLessEqual(len(plain), 32768)
                    decoded.append(plain)
                    offset += size_bytes
                self.assertEqual(offset, len(encoded))
                self.assertEqual(b"".join(decoded), source)

    def test_incompressible_chunks_still_fit_bgzf_member_limit(self) -> None:
        source = b"".join(hashlib.sha256(str(i).encode()).digest() for i in range(4096))
        encoded = artifact._encode_bgzf(source)
        self.assertEqual(gzip.decompress(encoded), source)
        offset = 0
        while offset < len(encoded):
            member_size = struct.unpack_from("<H", encoded, offset + 16)[0] + 1
            self.assertLessEqual(member_size, 65536)
            offset += member_size
        self.assertEqual(offset, len(encoded))


class GvcfBoundedRunnerTests(unittest.TestCase):
    """Exercise real short Python children; these are not bcftools acceptance."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="gvcf-runner-unit-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def run_python(self, script: str, *, seconds: float = 5):
        return artifact._run(
            [sys.executable, "-I", "-c", script],
            cwd=self.directory, deadline=time.monotonic() + seconds, stage="unit",
        )

    def test_success_captures_bytes_and_closes_stdin(self) -> None:
        result = self.run_python(
            "import sys; value=sys.stdin.buffer.read(); "
            "sys.stdout.buffer.write(b'out:'+value); sys.stderr.buffer.write(b'err')"
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"out:")
        self.assertEqual(result.stderr, b"err")

    def test_nonzero_exit_is_a_sanitized_failure(self) -> None:
        stderr_marker = "synthetic stderr marker"
        with self.assertRaises(artifact._Failure) as caught:
            self.run_python("import sys;sys.stderr.write(" + repr(stderr_marker) + ");sys.exit(7)")
        self.assertNotIn(stderr_marker, str(caught.exception))
        self.assertNotIn(str(self.directory), str(caught.exception))

    def test_timeout_kills_and_reaps_child(self) -> None:
        pid_file = self.directory / "child.pid"
        script = (
            "import os,pathlib,time;time.sleep(0.75);pathlib.Path(" + repr(str(pid_file))
            + ").write_text(str(os.getpid()));time.sleep(10)"
        )
        real_clock = time.monotonic
        started = real_clock()
        ready_at = None

        def clock_after_readiness():
            nonlocal ready_at
            now = real_clock()
            if now - started > 8:
                raise AssertionError("Child-reaping test exceeded its independent watchdog")
            if ready_at is None:
                if not pid_file.exists():
                    if now - started > 5:
                        raise AssertionError("Child did not signal readiness before startup watchdog")
                    return started
                ready_at = now
            return started + now - ready_at

        # The child deliberately starts later than the tested 0.5-second interval.
        with patch.object(artifact.time, "monotonic", side_effect=clock_after_readiness):
            with self.assertRaises(artifact._Failure):
                self.run_python(script, seconds=0.5)
        self.assertIsNotNone(ready_at)
        self.assertLess(real_clock() - started, 8)
        self.assertTrue(pid_file.exists())
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)

    def test_stdout_and_stderr_overflow_are_bounded_failures(self) -> None:
        for stream, count in (("stdout", 1114113), ("stderr", 65537)):
            with self.subTest(stream=stream), self.assertRaises(artifact._Failure):
                self.run_python(
                    "import sys;sys." + stream + ".buffer.write(b'x'*" + str(count) + ")"
                )

    def test_expired_total_deadline_does_not_start_child(self) -> None:
        marker = self.directory / "should-not-exist"
        with self.assertRaises(artifact._Failure):
            artifact._run(
                [sys.executable, "-c", "open(" + repr(str(marker)) + ",'w').close()"],
                cwd=self.directory, deadline=time.monotonic() - 1, stage="unit",
            )
        self.assertFalse(marker.exists())

    def test_fixed_environment_does_not_inherit_plugin_or_python_injection(self) -> None:
        script = (
            "import os,json;print(json.dumps({name:os.environ.get(name) for name in "
            "['BCFTOOLS_PLUGINS','LD_PRELOAD','PYTHONPATH','UNIT_SECRET_SENTINEL']}))"
        )
        with patch.dict(os.environ, {
            "BCFTOOLS_PLUGINS": "/unit/untrusted/plugin",
            "LD_PRELOAD": "/unit/untrusted/library",
            "PYTHONPATH": "/unit/untrusted/python",
            "UNIT_SECRET_SENTINEL": "do-not-inherit",
        }):
            result = self.run_python(script)
        observed = json.loads(result.stdout)
        self.assertIn(observed.pop("BCFTOOLS_PLUGINS"), (None, ""))
        self.assertEqual(observed, {name: None for name in observed})


class GvcfReferenceStreamTests(unittest.TestCase):
    """Only these tiny-reference adapter tests substitute fixed expected pins.

    They do not qualify the production reference. The unchanged production
    identities and real reference/executor are exercised by native acceptance.
    """

    setUp = GvcfArtifactFixtureTests.setUp
    bound_profile = GvcfArtifactFixtureTests.bound_profile

    @contextmanager
    def tiny_reference(self, *, actual_length=10080, declared_length=10080):
        sequence = bytearray(b"n" * actual_length)
        for position, base in ((10001, "t"), (10010, "c"), (10020, "a"),
                               (10035, "c"), (10045, "a"), (10046, "c")):
            if position <= actual_length:
                sequence[position - 1] = ord(base)
        raw = b">NC_000001.11 synthetic adapter reference\n" + bytes(sequence) + b"\n"
        path = self.directory / "tiny-reference.fa"
        path.write_bytes(raw)
        profile = copy.deepcopy(self.reference_profile)
        contigs = profile["contig_alias_profile"]["contigs"]
        contigs[0]["sequence_length"] = declared_length
        alias_digest = digest(json.dumps(contigs, sort_keys=True, separators=(",", ":")).encode())
        profile["contig_alias_profile"]["sha256"] = alias_digest
        identity = replace(self.identity, fasta_content_sha256=digest(raw),
                           fasta_content_size_bytes=len(raw))
        data = self.data.replace(b"length=248956422", ("length=" + str(declared_length)).encode())
        intake = validate_gvcf_bytes(data, self.bound_profile(data))
        self.assertTrue(intake.is_valid, intake.errors)
        with patch.multiple(
            artifact, REFERENCE_FASTA_CONTENT_SHA256=digest(raw),
            REFERENCE_FASTA_CONTENT_SIZE_BYTES=len(raw), REFERENCE_ALIAS_SHA256=alias_digest,
        ):
            yield path, identity, profile, intake, data

    def verify_tiny(self, context) -> None:
        path, identity, profile, intake, _ = context
        artifact._verify_reference(path, identity, profile, intake, time.monotonic() + 5)

    def test_stream_reads_case_insensitively_and_ignores_adjacent_indexes(self) -> None:
        with self.tiny_reference() as context:
            path = context[0]
            Path(str(path) + ".fai").write_text("wrong\t999\t0\t1\t1\n")
            Path(str(path) + ".gzi").write_bytes(b"not an index")
            self.verify_tiny(context)

    def test_stream_checks_actual_full_reference_digest_after_requested_positions(self) -> None:
        with self.tiny_reference() as context:
            path = context[0]
            payload = bytearray(path.read_bytes())
            payload[-2] = ord("a")
            path.write_bytes(payload)
            with self.assertRaises(artifact._Failure):
                self.verify_tiny(context)

    def test_stream_rejects_wrong_used_contig_length(self) -> None:
        with self.tiny_reference(actual_length=10079, declared_length=10080) as context:
            with self.assertRaises(artifact._Failure):
                self.verify_tiny(context)

    def test_stream_checks_every_ref_base_of_a_deletion(self) -> None:
        with self.tiny_reference() as context:
            path, identity, profile, _, data = context
            good = change_record(data, 4, ref="AC", alt="A,<NON_REF>")
            good_intake = validate_gvcf_bytes(good, self.bound_profile(good))
            self.assertTrue(good_intake.is_valid)
            artifact._verify_reference(path, identity, profile, good_intake, time.monotonic() + 5)
            bad = change_record(good, 4, ref="AG")
            bad_intake = validate_gvcf_bytes(bad, self.bound_profile(bad))
            self.assertTrue(bad_intake.is_valid)
            with self.assertRaises(artifact._Failure):
                artifact._verify_reference(path, identity, profile, bad_intake, time.monotonic() + 5)

    def test_alias_and_reference_identity_mismatches_are_not_passes(self) -> None:
        with self.tiny_reference() as context:
            path, identity, profile, intake, _ = context
            altered_profiles = []
            wrong_hash = copy.deepcopy(profile)
            wrong_hash["contig_alias_profile"]["sha256"] = "0" * 64
            altered_profiles.append(wrong_hash)
            wrong_mapping = copy.deepcopy(profile)
            wrong_mapping["contig_alias_profile"]["contigs"][0]["refseq_accession"] = "NC_000002.12"
            altered_profiles.append(wrong_mapping)
            for altered in altered_profiles:
                with self.assertRaises(artifact._Failure):
                    artifact._verify_reference(path, identity, altered, intake, time.monotonic() + 5)
            for altered in (
                replace(identity, assembly_accession="GCF_000001405.25"),
                replace(identity, rules=identity.rules[:-1]),
                replace(identity, bundle_sha256="0" * 64),
            ):
                with self.assertRaises(artifact._Failure):
                    artifact._verify_reference(path, altered, profile, intake, time.monotonic() + 5)

    def test_gvcf_declared_contig_length_must_match_verified_alias(self) -> None:
        with self.tiny_reference() as context:
            path, identity, profile, _, data = context
            altered = data.replace(b"length=10080", b"length=10081")
            intake = validate_gvcf_bytes(altered, self.bound_profile(altered))
            self.assertTrue(intake.is_valid)
            with self.assertRaises(artifact._Failure):
                artifact._verify_reference(path, identity, profile, intake, time.monotonic() + 5)

    def test_fifo_reference_and_index_output_refuse_without_waiting_for_writer(self) -> None:
        for operation in ("reference", "output"):
            with self.subTest(operation=operation):
                fifo_path = self.directory / (operation + ".fifo")
                os.mkfifo(fifo_path)
                script = (
                    "import sys,time;from pathlib import Path\n"
                    "sys.path[:0]=" + repr([str(ROOT), str(ROOT / "tests")]) + "\n"
                    "import omnigenis.capabilities.gvcf_artifact as artifact\n"
                    "from test_gvcf_artifact_capability import GvcfArtifactFixtureTests\n"
                    "from omnigenis.capabilities.gvcf_intake import validate_gvcf_bytes\n"
                    "case=GvcfArtifactFixtureTests();case.setUp()\n"
                    "try:\n"
                    " try:\n"
                    + (
                        "  artifact._read_output(Path(" + repr(str(fifo_path)) + "),1048576,'unit')\n"
                        if operation == "output" else
                        "  artifact._verify_reference(Path(" + repr(str(fifo_path))
                        + "),case.identity,case.reference_profile,"
                        "validate_gvcf_bytes(case.data,case.input_profile),time.monotonic()+1)\n"
                    )
                    + " except artifact._Failure:\n"
                    "  print('REFUSED')\n"
                    " else:\n"
                    "  raise AssertionError('FIFO was accepted')\n"
                    "finally:\n"
                    " case.doCleanups()\n"
                )
                try:
                    completed = subprocess.run(
                        [sys.executable, "-B", "-I", "-c", script],
                        capture_output=True, text=True, timeout=2,
                        env={**os.environ, "TMPDIR": str(self.directory)},
                    )
                except subprocess.TimeoutExpired:
                    self.fail("FIFO open waited for a writer outside the bounded execution deadline")
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout.strip(), "REFUSED")


if __name__ == "__main__":
    unittest.main()
