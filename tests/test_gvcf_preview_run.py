from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError, replace
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from omnigenis import gvcf_preview_run as preview
from omnigenis.capabilities import gvcf_artifact as artifact
from omnigenis.capabilities.reference_identity import verify_reference_identity

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "gvcf_artifact"
OBSERVED_AT = "2026-11-26T17:00:00Z"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _record_lines(data: bytes) -> list[str]:
    return [line for line in data.decode("utf-8").splitlines() if not line.startswith("#")]


def _change_record(data: bytes, ordinal: int, column: int, value: str) -> bytes:
    lines = data.decode("utf-8").splitlines()
    indexes = [index for index, line in enumerate(lines) if not line.startswith("#")]
    fields = lines[indexes[ordinal]].split("\t")
    fields[column] = value
    lines[indexes[ordinal]] = "\t".join(fields)
    return ("\n".join(lines) + "\n").encode("utf-8")


def _reference_inputs() -> tuple[dict, object]:
    profile = _json(
        ROOT / "resources" / "reference-profiles" / "grch38-p14-ncbi-refseq-autosomal-v1.json"
    )
    identity = verify_reference_identity(
        profile,
        _json(ROOT / "resources" / "source-registry.v1.json"),
        _json(ROOT / "resources" / "scientific-resource-bom" / "grch38-p14-ncbi-refseq-autosomal-v1.json"),
        _json(ROOT / "tests" / "fixtures" / "reference_identity" / "ncbi-grch38-p14.observation.json"),
    )
    return profile, identity


def _selected_lines(data: bytes, region: str) -> list[str]:
    """Independent test overlap oracle, not a product query helper."""
    chrom, interval = region.split(":")
    left, right = (int(value) for value in interval.split("-"))
    result = []
    for line in _record_lines(data):
        columns = line.split("\t")
        position = int(columns[1])
        info = dict(
            entry.split("=", 1) for entry in columns[7].split(";") if "=" in entry
        )
        end = int(info.get("END", position + len(columns[3]) - 1))
        if columns[0] == chrom and position <= right and left <= end:
            result.append(line)
    return result


def make_preview_run(source_gvcf=None, **overrides):
    """Public composition with explicit native/reference doubles, not native acceptance.

    Other reporting tests may use this finite factory. It retains real B2/B3
    component objects and source-byte bindings; native qualification is separate.
    """
    source = (FIXTURE / "valid-reference-bound.gvcf").read_bytes() if source_gvcf is None else source_gvcf
    input_profile = _json(FIXTURE / "valid-input-profile.json")
    if type(source) is bytes:
        input_profile.update(content_sha256=_digest(source), size_bytes=len(source))
    reference_profile, identity = _reference_inputs()
    reference_failure = overrides.pop("_reference_failure", False)
    with tempfile.TemporaryDirectory(prefix="gvcf-preview-unit-") as directory:
        directory = Path(directory)
        executable = directory / "explicit-native-double"
        executable.write_bytes(b"Explicit native-executor test double; no caller execution.\n")
        executable.chmod(0o700)

        def native_double(args, *, cwd, deadline, stage, pass_fds=()):
            arguments = [str(argument) for argument in args]
            if stage == "version":
                return subprocess.CompletedProcess(arguments, 0, b"bcftools 1.24\nUsing htslib 1.24\n", b"")
            if stage == "index":
                path = Path(arguments[arguments.index("--output") + 1])
                path.write_bytes(b"Explicit CSI test double; no native index qualification.\n")
                return subprocess.CompletedProcess(arguments, 0, b"", b"")
            if stage != "query":
                raise AssertionError("unexpected native-double stage")
            region = arguments[arguments.index("-r") + 1]
            decoded = gzip.decompress(Path(arguments[-1]).read_bytes())
            lines = _selected_lines(decoded, region)
            output = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
            return subprocess.CompletedProcess(arguments, 0, output, b"")

        values = {
            "source_gvcf": source,
            "input_profile": input_profile,
            "source_artifact_manifest": {
                "schema_version": "1.0.0", "artifact_id": "synthetic-source-" + input_profile["content_sha256"],
                "artifact_kind": "gVCF", "media_type": "text/vcf",
                "sha256": input_profile["content_sha256"], "size_bytes": input_profile["size_bytes"],
                "producer_execution_profile_id": "synthetic-fixture-v1", "parent_artifact_ids": [],
            },
            "execution_profile": _json(
                ROOT / "resources" / "execution-profiles" / "single-sample-gvcf-artifact-v1.json"
            ),
            "reference_profile": reference_profile, "reference_identity": identity,
            "reference_fasta": directory / "explicit-reference-double.fa",
            "bcftools_executable": executable, "expected_executor_sha256": _digest(executable.read_bytes()),
            "observed_at": OBSERVED_AT, "timeout_seconds": 10,
        }
        values.update(overrides)
        with (
            patch.object(
                artifact, "_verify_reference",
                side_effect=artifact._Failure("reference", "ref_mismatch") if reference_failure else None,
            ),
            patch.object(artifact, "_run", side_effect=native_double),
        ):
            return preview.run_gvcf_preview_components(**values)


class GvcfPreviewRunTests(unittest.TestCase):
    def setUp(self):
        self.source = (FIXTURE / "valid-reference-bound.gvcf").read_bytes()

    def assert_failed(self, result):
        self.assertFalse(result.passed)
        self.assertEqual(result.status, "FAIL")
        self.assertTrue(result.errors)
        self.assertIsNone(result.canonical_model)
        self.assertIsNone(result.evidence_snapshot)
        self.assertIsNone(result.interpretation)
        with self.assertRaisesRegex(preview.GvcfPreviewBindingError, "^run_not_passed$"):
            result.to_dict()

    def test_composition_preserves_every_source_column_and_record(self):
        run = make_preview_run()
        self.assertTrue(run.passed, run.errors)
        preview.rebind_gvcf_preview_run(run)
        payload = run.to_dict()
        records = _record_lines(self.source)
        observations = payload["canonical_model"]["observations"]
        self.assertEqual(run.canonical_model.record_count, len(records))
        self.assertEqual(len(observations), len(records))
        self.assertEqual(len(run.interpretation.items), len(records))
        for ordinal, (line, observation, item) in enumerate(
            zip(records, observations, payload["interpretation"]["items"], strict=True), 1
        ):
            with self.subTest(ordinal=ordinal):
                values = line.split("\t")
                self.assertEqual(observation["source_record_ordinal"], ordinal)
                self.assertEqual(observation["record_sha256"], _digest(line.encode("utf-8")))
                self.assertEqual(observation["chrom"], values[0])
                self.assertEqual(observation["source_position"], values[1])
                self.assertEqual(observation["pos"], int(values[1]))
                self.assertEqual(observation["source_id"], values[2])
                self.assertEqual(observation["ref"], values[3])
                self.assertEqual(observation["alts"], values[4].split(","))
                self.assertEqual([observation["qual"], observation["filter"], observation["info"]], values[5:8])
                self.assertEqual(
                    observation["format_values"],
                    [{"key": key, "value": value} for key, value in zip(values[8].split(":"), values[9].split(":"), strict=True)],
                )
                self.assertEqual(observation["source_gt"], values[9].split(":")[0])
                self.assertEqual(item["observation"], observation)
                self.assertTrue(observation["observation_id"].startswith("gvcf-observation:sha256:"))
        self.assertEqual(run.completed_stages, preview.COMPLETED_STAGES)

    def test_exact_genotype_phase_and_likelihood_semantics_are_retained(self):
        run = make_preview_run()
        self.assertTrue(run.passed, run.errors)
        statuses = set()
        for original, record in zip(run.intake.records, run.canonical_model.observations, strict=True):
            self.assertEqual(record.source_gt, original.gt)
            self.assertEqual(record.phase_separator, original.phase_separator)
            self.assertEqual(record.format_values, original.format_values)
            self.assertEqual(record.alts, original.alts)
            self.assertEqual(record.source_genotype_status, original.genotype_status)
            statuses.add(record.source_genotype_status)
            values = dict(record.format_values)
            if values["PL"] != ".":
                self.assertEqual(len(values["PL"].split(",")), 3 if record.kind == "REFERENCE_BLOCK" else 6)
            if "AD" in values and values["AD"] != ".":
                self.assertEqual(len(values["AD"].split(",")), 2 if record.kind == "REFERENCE_BLOCK" else 3)
        self.assertTrue({"CALLED", "MISSING", "PARTIAL"}.issubset(statuses))
        self.assertTrue(any(record.phase_separator == "|" for record in run.canonical_model.observations))

    def test_all_clinical_axes_remain_not_assessed_and_preview_only(self):
        run = make_preview_run()
        self.assertTrue(run.passed, run.errors)
        payload = run.to_dict()
        interpretation = payload["interpretation"]
        self.assertEqual(interpretation["evaluated_analysis_class_ids"], [])
        self.assertEqual(interpretation["assessment_status"], "NOT_ASSESSED")
        self.assertEqual(interpretation["normalization_status"], "NOT_ASSESSED")
        self.assertEqual(interpretation["conflict_assessment_status"], "NOT_ASSESSED")
        for item in interpretation["items"]:
            self.assertEqual(item["clinical_availability"], "NOT_MEASURED")
            self.assertEqual(set(item["clinical_axes"].values()), {"NOT_ASSESSED"})
        self.assertEqual(payload["policy_status"]["lifecycle_profile"], "preview")
        self.assertEqual(payload["policy_status"]["clinical_use_status"], "NOT_CLINICALLY_PROMOTED")
        self.assertEqual(payload["policy_status"]["release_authorization"], "NOT_ESTABLISHED")

    def test_safe_export_has_bound_real_technical_evidence_without_raw_inputs(self):
        decorated = self.source.replace(
            b"##fileformat=VCFv4.2\n",
            b"##fileformat=VCFv4.2\n##private_note=/private/not-a-report-input\n",
        )
        run = make_preview_run(decorated)
        self.assertTrue(run.passed, run.errors)
        payload = run.to_dict()
        preview.validate_gvcf_preview_export(payload)
        forbidden = {"raw_record", "meta_lines", "source_gvcf", "reference_fasta", "bcftools_executable"}
        stack = [payload]
        while stack:
            value = stack.pop()
            if type(value) is dict:
                self.assertFalse(forbidden.intersection(value))
                stack.extend(value.values())
            elif type(value) is list:
                stack.extend(value)
        serialized = json.dumps(payload)
        self.assertNotIn("/private/not-a-report-input", serialized)
        self.assertNotIn("explicit-reference-double.fa", serialized)
        self.assertNotIn("explicit-native-double", serialized)
        snapshot = payload["evidence_snapshot"]
        self.assertEqual(snapshot["canonical_model_sha256"], payload["canonical_model"]["content_sha256"])
        self.assertEqual(
            payload["interpretation"]["evidence_snapshot_sha256"], snapshot["content_sha256"]
        )
        self.assertEqual(len(snapshot["sources"]), 3)
        self.assertEqual(len(snapshot["artifact_evidence"]["rules"]), 7)
        self.assertEqual(snapshot["artifact_evidence"]["caller_declaration"]["verification"], "DECLARED_ONLY")

    def test_context_timestamp_is_not_an_execution_timestamp(self):
        first = make_preview_run()
        second = make_preview_run(observed_at="2025-01-01T10:01:02+02:00")
        self.assertTrue(first.passed, first.errors)
        self.assertTrue(second.passed, second.errors)
        self.assertEqual(first.canonical_model, second.canonical_model)
        self.assertNotEqual(first.evidence_snapshot.content_sha256, second.evidence_snapshot.content_sha256)
        payload = second.to_dict()
        self.assertEqual(payload["observed_at_origin"], "CALLER_PROVIDED_CONTEXT")
        self.assertEqual(payload["observed_at"], "2025-01-01T10:01:02+02:00")
        self.assertNotIn("executed_at", json.dumps(payload))

    def test_repeated_identical_inputs_produce_identical_safe_exports(self):
        first = make_preview_run()
        second = make_preview_run()
        self.assertTrue(first.passed, first.errors)
        self.assertTrue(second.passed, second.errors)
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_captures_and_returned_serializations_cannot_mutate_the_run(self):
        profile = _json(FIXTURE / "valid-input-profile.json")
        run = make_preview_run(input_profile=profile)
        self.assertTrue(run.passed, run.errors)
        before = run.to_dict()
        profile["sample_id"] = "CHANGED"
        detached = run.to_dict()
        detached["canonical_model"]["observations"][0]["source_gt"] = "1/1"
        detached["interpretation"]["items"].clear()
        self.assertEqual(run.to_dict(), before)
        with self.assertRaises(FrozenInstanceError):
            run.status = "FAIL"
        with self.assertRaises(FrozenInstanceError):
            run.canonical_model.observations[0].source_gt = "1/1"

    def test_reference_only_input_keeps_real_observation_and_unassessed_scope(self):
        lines = self.source.decode("utf-8").splitlines()
        block = next(line for line in lines if not line.startswith("#") and line.split("\t")[4] == "<NON_REF>")
        source = ("\n".join([line for line in lines if line.startswith("#")] + [block]) + "\n").encode("utf-8")
        run = make_preview_run(source)
        self.assertTrue(run.passed, run.errors)
        self.assertEqual(run.canonical_model.record_count, 1)
        self.assertEqual(run.canonical_model.observations[0].kind, "REFERENCE_BLOCK")
        self.assertEqual(len(run.interpretation.items), 1)
        self.assertEqual(run.to_dict()["interpretation"]["evaluated_analysis_class_ids"], [])

    def test_original_pos_id_qual_filter_and_info_are_not_reserialized(self):
        lines = _record_lines(self.source)
        variant_index = next(index for index, line in enumerate(lines) if line.split("\t")[4] != "<NON_REF>")
        changed = _change_record(self.source, variant_index, 2, "synthetic_id")
        changed = _change_record(changed, variant_index, 5, "30.500")
        run = make_preview_run(changed)
        self.assertTrue(run.passed, run.errors)
        observation = run.canonical_model.observations[variant_index]
        self.assertEqual(observation.source_id, "synthetic_id")
        self.assertEqual(observation.qual, "30.500")

    def test_unsupported_present_info_is_refused_before_native_preparation(self):
        changed = self.source.replace(b"END=10009", b"END=10009;UNSUPPORTED=1", 1)
        self.assertNotEqual(changed, self.source)
        with patch.object(preview, "prepare_gvcf_artifacts") as prepare:
            result = make_preview_run(changed)
        self.assert_failed(result)
        prepare.assert_not_called()

    def test_actual_component_failure_retains_diagnostics_without_promotable_objects(self):
        run = make_preview_run(_reference_failure=True)
        self.assert_failed(run)
        self.assertIsNotNone(run.artifact)
        self.assertEqual(run.artifact.artifacts, ())
        self.assertEqual(
            next(rule.status for rule in run.artifact.rules if rule.rule_id == "REF_CONCORDANCE"),
            "FAIL",
        )
        self.assertNotIn("canonical_model", run.completed_stages)

    def test_invalid_timestamp_and_timeout_refuse_before_native_execution(self):
        cases = [
            {"observed_at": "2026-02-30T00:00:00Z"},
            {"observed_at": "2026-11-26T17:00:00"},
            {"observed_at": "2026-11-26 17:00:00Z"},
            {"observed_at": True},
            {"timeout_seconds": True},
            {"timeout_seconds": 0},
            {"timeout_seconds": 601},
        ]
        for values in cases:
            with self.subTest(values=values), patch.object(preview, "prepare_gvcf_artifacts") as prepare:
                self.assert_failed(make_preview_run(**values))
                prepare.assert_not_called()

    def test_invalid_timezone_components_refuse_before_native_execution(self):
        for offset in ("+00:60", "-00:60", "+00:99", "-12:99", "+24:00", "-24:00"):
            with self.subTest(offset=offset), patch.object(preview, "prepare_gvcf_artifacts") as prepare:
                result = make_preview_run(observed_at="2026-11-26T18:00:00" + offset)
                self.assert_failed(result)
                self.assertEqual(result.errors, ("observed_at_invalid",))
                prepare.assert_not_called()

    def test_rebind_and_export_refuse_overflowing_timezone_components(self):
        run = make_preview_run()
        self.assertTrue(run.passed, run.errors)
        original = run.to_dict()
        for offset in ("+00:60", "-00:60", "+00:99", "-12:99"):
            timestamp = "2026-11-26T18:00:00" + offset
            with self.subTest(offset=offset):
                with self.assertRaisesRegex(preview.GvcfPreviewBindingError, "^observed_at_invalid$"):
                    preview.rebind_gvcf_preview_run(replace(run, observed_at=timestamp))
                payload = copy.deepcopy(original)
                payload["observed_at"] = timestamp
                with self.assertRaisesRegex(preview.GvcfPreviewBindingError, "^observed_at_invalid$"):
                    preview.validate_gvcf_preview_export(payload)

    def test_valid_timezone_boundaries_preserve_caller_context(self):
        for offset in ("+00:59", "-00:59", "+23:59", "-23:59"):
            timestamp = "2026-11-26T18:00:00.123456" + offset
            with self.subTest(offset=offset):
                run = make_preview_run(observed_at=timestamp)
                self.assertTrue(run.passed, run.errors)
                payload = run.to_dict()
                preview.validate_gvcf_preview_export(payload)
                self.assertEqual(payload["observed_at"], timestamp)
                self.assertEqual(payload["evidence_snapshot"]["observed_at"], timestamp)

    def test_empty_nonbytes_and_oversized_sources_refuse_without_native_execution(self):
        for source in (b"", "not bytes", b"x" * (preview.MAX_INPUT_BYTES + 1)):
            with self.subTest(kind=type(source).__name__), patch.object(preview, "prepare_gvcf_artifacts") as prepare:
                self.assert_failed(make_preview_run(source))
                prepare.assert_not_called()

    def test_strict_numeric_types_and_execution_profile_refuse_coercion(self):
        profile = _json(FIXTURE / "valid-input-profile.json")
        profile["size_bytes"] = True
        execution = _json(ROOT / "resources" / "execution-profiles" / "single-sample-gvcf-artifact-v1.json")
        execution["deterministic"] = 1
        good = make_preview_run()
        self.assertTrue(good.passed, good.errors)
        manifest = json.loads(good.source_artifact_manifest_json)
        manifest["size_bytes"] = float(manifest["size_bytes"])
        for values in (
            {"input_profile": profile},
            {"execution_profile": execution},
            {"source_artifact_manifest": manifest},
        ):
            with self.subTest(values=list(values)), patch.object(preview, "prepare_gvcf_artifacts") as prepare:
                self.assert_failed(make_preview_run(**values))
                prepare.assert_not_called()

    def test_capture_budgets_cycles_and_nonfinite_values_fail_before_serialization(self):
        cycle = {}
        cycle["self"] = cycle
        deep = {}
        current = deep
        for _ in range(20):
            current["next"] = {}
            current = current["next"]
        profiles = [
            cycle, deep, {"oversized": "x" * 65_537},
            {"too_many": [0] * (preview.MAX_JSON_NODES + 1)},
            {"nonfinite": float("nan")}, {"nonfinite": float("inf")},
            {"invalid_unicode": "\ud800"},
        ]
        for profile in profiles:
            with self.subTest(kind=next(iter(profile))), patch.object(preview, "prepare_gvcf_artifacts") as prepare:
                self.assert_failed(make_preview_run(input_profile=profile))
                prepare.assert_not_called()

    def test_reference_identity_rejects_non_native_fields_without_custom_equality(self):
        class EqualAnything:
            calls = 0

            def __eq__(self, other):
                self.calls += 1
                return True

        class StringAlias(str):
            pass

        run = make_preview_run()
        self.assertTrue(run.passed, run.errors)
        identity = run.reference_identity
        for field in (
            "profile_id", "assembly_accession", "bundle_sha256",
            "fasta_content_sha256", "fasta_content_size_bytes",
        ):
            impostor = EqualAnything()
            with self.subTest(field=field), patch.object(preview, "prepare_gvcf_artifacts") as prepare:
                forged = replace(identity, **{field: impostor})
                self.assert_failed(make_preview_run(reference_identity=forged))
                prepare.assert_not_called()
                with self.assertRaises(preview.GvcfPreviewBindingError):
                    preview.rebind_gvcf_preview_run(replace(run, reference_identity=forged))
                self.assertEqual(impostor.calls, 0)
        for forged in (
            replace(identity, profile_id=StringAlias(identity.profile_id)),
            replace(identity, fasta_content_size_bytes=float(identity.fasta_content_size_bytes)),
            replace(identity, autosomal_refseq_accessions=(
                StringAlias(identity.autosomal_refseq_accessions[0]),
                *identity.autosomal_refseq_accessions[1:],
            )),
            replace(identity, rules=(
                replace(identity.rules[0], detail=StringAlias(identity.rules[0].detail)),
                *identity.rules[1:],
            )),
        ):
            with self.assertRaises(preview.GvcfPreviewBindingError):
                preview.rebind_gvcf_preview_run(replace(run, reference_identity=forged))

    def test_rebind_refuses_partial_objects_stage_claims_and_changed_source(self):
        run = make_preview_run()
        self.assertTrue(run.passed, run.errors)
        changed_source = run.source_gvcf + b"\n"
        cases = [
            replace(run, source_gvcf=changed_source),
            replace(run, canonical_model=None),
            replace(run, evidence_snapshot=None),
            replace(run, interpretation=None),
            replace(run, completed_stages=run.completed_stages[:-1]),
            replace(run, errors=("unresolved",)),
            replace(run, limitations=()),
            replace(run, reference_identity=replace(run.reference_identity, fasta_content_sha256="0" * 64)),
            replace(run, expected_executor_sha256="0" * 64),
        ]
        for changed in cases:
            with self.subTest(field=changed.status):
                with self.assertRaises(preview.GvcfPreviewBindingError):
                    preview.rebind_gvcf_preview_run(changed)
                with self.assertRaises(preview.GvcfPreviewBindingError):
                    changed.to_dict()

    def test_passed_refuses_non_native_status_and_errors_without_user_methods(self):
        class EqualStatus:
            def __init__(self):
                self.calls = 0

            def __eq__(self, other):
                self.calls += 1
                return True

        class StatusAlias(str):
            def __new__(cls):
                value = super().__new__(cls, "PASS")
                value.calls = 0
                return value

            def __eq__(self, other):
                self.calls += 1
                return True

        class FalseErrors:
            def __init__(self):
                self.calls = 0

            def __bool__(self):
                self.calls += 1
                return False

        class ErrorsAlias(tuple):
            def __new__(cls):
                value = super().__new__(cls, ())
                value.calls = 0
                return value

            def __bool__(self):
                self.calls += 1
                return False

        run = make_preview_run()
        self.assertTrue(run.passed, run.errors)
        for field, value in (
            ("status", EqualStatus()), ("status", StatusAlias()),
            ("errors", FalseErrors()), ("errors", ErrorsAlias()),
        ):
            with self.subTest(field=field, value_type=type(value).__name__):
                forged = replace(run, **{field: value})
                self.assertFalse(forged.passed)
                with self.assertRaisesRegex(preview.GvcfPreviewBindingError, "^run_not_passed$"):
                    forged.to_dict()
                self.assertEqual(value.calls, 0)

    def test_rebind_binds_all_captured_profile_and_source_manifest_metadata(self):
        run = make_preview_run()
        self.assertTrue(run.passed, run.errors)
        profile = json.loads(run.input_profile_json)
        profile["input_id"] = "different-input-identity"
        manifest = json.loads(run.source_artifact_manifest_json)
        manifest["producer_execution_profile_id"] = "different-source-producer"
        reference = json.loads(run.reference_profile_json)
        reference["source_observation_note"] = "different-retained-metadata"
        execution = json.loads(run.execution_profile_json)
        # All execution fields are fixed; there is no accepted alternate metadata value.
        execution["executor_version"] = "1.0.1"
        for changed in (
            replace(run, input_profile_json=_canonical(profile)),
            replace(run, source_artifact_manifest_json=_canonical(manifest)),
            replace(run, reference_profile_json=_canonical(reference)),
            replace(run, execution_profile_json=_canonical(execution)),
        ):
            with self.assertRaises(preview.GvcfPreviewBindingError):
                preview.rebind_gvcf_preview_run(changed)
        bindings = run.to_dict()["canonical_model"]["input_bindings"]
        self.assertEqual(bindings["input_profile_sha256"], _digest(run.input_profile_json))
        self.assertEqual(bindings["source_artifact_manifest_sha256"], _digest(run.source_artifact_manifest_json))
        self.assertEqual(bindings["execution_profile_sha256"], _digest(run.execution_profile_json))
        self.assertEqual(bindings["reference_profile_sha256"], _digest(run.reference_profile_json))

    def test_rebind_recomputes_model_evidence_interpretation_and_nested_numeric_types(self):
        run = make_preview_run()
        self.assertTrue(run.passed, run.errors)
        model = run.canonical_model
        record = model.observations[0]
        cases = [
            replace(run, canonical_model=replace(model, record_count=True)),
            replace(run, canonical_model=replace(model, sample_id="another-sample")),
            replace(run, canonical_model=replace(model, observations=(
                replace(record, source_record_ordinal=True), *model.observations[1:],
            ))),
            replace(run, canonical_model=replace(model, payload_json=b"{}")),
            replace(run, evidence_snapshot=replace(run.evidence_snapshot, content_sha256="0" * 64)),
            replace(run, evidence_snapshot=replace(run.evidence_snapshot, payload_json=b"{}")),
            replace(run, interpretation=replace(run.interpretation, items=run.interpretation.items[:-1])),
            replace(run, interpretation=replace(run.interpretation, payload_json=b"{}")),
        ]
        for changed in cases:
            with self.subTest(model_hash=changed.canonical_model.content_sha256):
                with self.assertRaises(preview.GvcfPreviewBindingError):
                    preview.rebind_gvcf_preview_run(changed)

    def test_rebind_checks_retention_and_complete_query_evidence_not_only_pass_flags(self):
        run = make_preview_run()
        self.assertTrue(run.passed, run.errors)
        source_artifact = run.artifact
        query = source_artifact.index_queries[0]
        ledger = source_artifact.ledger[0]
        artifacts = [
            replace(source_artifact, missing_genotype_count=True),
            replace(source_artifact, ledger=(replace(ledger, source_record_ordinal=True), *source_artifact.ledger[1:])),
            replace(source_artifact, ledger=source_artifact.ledger[:-1]),
            replace(source_artifact, index_queries=source_artifact.index_queries[:-1]),
            replace(source_artifact, index_queries=(replace(query, region="1:1-1"), *source_artifact.index_queries[1:])),
            replace(source_artifact, index_queries=(replace(query, observed_record_keys=()), *source_artifact.index_queries[1:])),
            replace(source_artifact, rules=(
                replace(source_artifact.rules[0], detail="/private/unsupported-detail"),
                *source_artifact.rules[1:],
            )),
        ]
        for changed_artifact in artifacts:
            with self.subTest(records=changed_artifact.retained_record_count):
                with self.assertRaises(preview.GvcfPreviewBindingError):
                    preview.rebind_gvcf_preview_run(replace(run, artifact=changed_artifact))

    def test_rebind_checks_artifact_content_and_parent_binding(self):
        run = make_preview_run()
        self.assertTrue(run.passed, run.errors)
        bgzf, csi = run.artifact.artifacts
        variants = [
            (replace(bgzf, content=bgzf.content + b"x"), csi),
            (replace(bgzf, parent_artifact_ids=("other-source",)), csi),
            (bgzf, replace(csi, content=csi.content + b"x")),
            (bgzf, replace(csi, parent_artifact_ids=("other-bgzf",))),
            (bgzf, replace(csi, size_bytes=True)),
        ]
        for artifacts in variants:
            with self.subTest(bg_size=artifacts[0].size_bytes):
                changed = replace(run, artifact=replace(run.artifact, artifacts=artifacts))
                with self.assertRaises(preview.GvcfPreviewBindingError):
                    preview.rebind_gvcf_preview_run(changed)

    def test_schema_is_closed_versioned_and_pinned_with_local_refs(self):
        path = ROOT / "schemas" / "gvcf-preview-run.v1.schema.json"
        content = path.read_bytes()
        self.assertEqual(_digest(content), preview.SCHEMA_SHA256)
        schema = json.loads(content)
        self.assertEqual(schema["$id"], preview.SCHEMA_ID)
        Draft202012Validator.check_schema(schema)
        self.assertIs(schema["additionalProperties"], False)
        stack = [schema]
        while stack:
            value = stack.pop()
            if type(value) is dict:
                if "$ref" in value:
                    self.assertTrue(value["$ref"].startswith("#/$defs/"))
                stack.extend(value.values())
            elif type(value) is list:
                stack.extend(value)
        run = make_preview_run()
        self.assertTrue(run.passed, run.errors)
        preview.validate_gvcf_preview_export(run.to_dict())

    def test_schema_refuses_unknown_fields_clinical_promotion_and_numeric_coercion(self):
        run = make_preview_run()
        self.assertTrue(run.passed, run.errors)
        original = run.to_dict()
        mutations = (
            lambda value: value.update(raw_record="not an allowed export"),
            lambda value: value["policy_status"].update(lifecycle_profile="FINAL"),
            lambda value: value["policy_status"].update(clinical_use_status="VALIDATED"),
            lambda value: value["interpretation"].update(evaluated_analysis_class_ids=["germline-snv-small-indel"]),
            lambda value: value["interpretation"]["items"][0].update(assessment_status="NEGATIVE"),
            lambda value: value["canonical_model"]["observations"][0].update(pos=True),
            lambda value: value["canonical_model"]["observations"][0].update(pos=1.0),
            lambda value: value["canonical_model"]["observations"][0].update(source_gt="2/2"),
            lambda value: value["interpretation"].update(items=[]),
        )
        for mutation in mutations:
            value = copy.deepcopy(original)
            mutation(value)
            with self.subTest(mutation=mutations.index(mutation)):
                with self.assertRaises(preview.GvcfPreviewBindingError):
                    preview.validate_gvcf_preview_export(value)

    def test_export_byte_budget_refuses_without_truncation(self):
        run = make_preview_run()
        self.assertTrue(run.passed, run.errors)
        value = run.to_dict()
        value["over_budget"] = ["x" * 32_768] * 257
        with patch.object(preview, "_SCHEMA_PATH") as schema_path:
            with self.assertRaisesRegex(preview.GvcfPreviewBindingError, "^json_budget_exceeded$"):
                preview.validate_gvcf_preview_export(value)
            schema_path.open.assert_not_called()
        self.assertEqual(len(value["over_budget"]), 257)
        self.assertEqual(len(value["over_budget"][0]), 32_768)

    def test_changed_or_missing_schema_fails_with_sanitized_diagnostic(self):
        run = make_preview_run()
        self.assertTrue(run.passed, run.errors)
        payload = run.to_dict()
        with tempfile.TemporaryDirectory(prefix="gvcf-preview-schema-unit-") as directory:
            path = Path(directory) / "private-schema-name.json"
            path.write_text('{"$ref":"https://invalid.example/remote-schema"}', encoding="utf-8")
            with patch.object(preview, "_SCHEMA_PATH", path):
                with self.assertRaises(preview.GvcfPreviewBindingError) as caught:
                    preview.validate_gvcf_preview_export(payload)
                self.assertNotIn(str(path), str(caught.exception))
                self.assertNotIn("https:", str(caught.exception))
            path.unlink()
            with patch.object(preview, "_SCHEMA_PATH", path):
                with self.assertRaises(preview.GvcfPreviewBindingError) as caught:
                    preview.validate_gvcf_preview_export(payload)
                self.assertNotIn(str(path), str(caught.exception))

    def test_schema_failure_during_composition_cannot_promote_partial_results(self):
        with patch.object(
            preview, "validate_gvcf_preview_export",
            side_effect=preview.GvcfPreviewBindingError("preview_schema_invalid"),
        ):
            run = make_preview_run()
        self.assert_failed(run)
        self.assertIsNotNone(run.artifact)
        self.assertTrue(run.artifact.passed)
        self.assertNotIn("output_schema", run.completed_stages)


if __name__ == "__main__":
    unittest.main()
