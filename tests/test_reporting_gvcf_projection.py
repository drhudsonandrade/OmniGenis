"""Conformance of the bounded gVCF technical-preview reporting boundary.

The run factory exercises the public composition with explicit native/reference
test doubles. Native executor acceptance is separate from these unit fixtures.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from omnigenis.gvcf_preview_run import (
    GvcfPreviewBindingError,
    validate_gvcf_preview_export,
)
from reporting.bindings import ReportSourceIdentity
from reporting.compiler import compile_report_pack
from reporting.contracts import validate_rpt01_contracts
from reporting.gvcf_projection import (
    GvcfReportProjectionError,
    project_gvcf_interpretation,
)
from reporting.presentation import build_presentation_ir
from reporting.renderer import render_primary
from reporting.viewmodel import build_report_view_model_and_release_bundle


ROOT = Path(__file__).resolve().parents[1]
REPORT_ID = "gvcf-technical-preview"
E1A_REPORT_ID = "e1a-small-variant-report"
_FACTORY_MODULE = None


def _load(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _make_run(source_gvcf=None, **overrides):
    global _FACTORY_MODULE
    if _FACTORY_MODULE is None:
        spec = importlib.util.spec_from_file_location(
            "omnigenis_gvcf_preview_test_factory",
            ROOT / "tests" / "test_gvcf_preview_run.py",
        )
        if spec is None or spec.loader is None:
            raise AssertionError("gvcf_preview_test_factory_unavailable")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _FACTORY_MODULE = module
    return _FACTORY_MODULE.make_preview_run(source_gvcf, **overrides)


def _contracts(report_id=REPORT_ID):
    catalog = ("reporting/gvcf-catalog.v1.json" if report_id == REPORT_ID
               else "reporting/catalog.v1.json")
    return {
        "report_id": report_id,
        "catalog": _load(catalog),
        "section_contracts": _load(f"reporting/sections/{report_id}.v1.json"),
        "intended_use": _load(f"reporting/intended-use/{report_id}.v1.json"),
        "report_capability_manifest": _load(
            f"reporting/capabilities/{report_id}.v1.json"
        ),
    }


def _identity(run):
    return ReportSourceIdentity(
        namespace_id="synthetic", case_id="CASE-GVCF-001",
        subject_id="SUBJECT-GVCF-001", sample_id=run.canonical_model.sample_id,
        analysis_id="ANALYSIS-GVCF-001", analysis_revision="1", specimen_id=None,
    )


def _values(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _values(item)
    else:
        yield value


def _keys(value):
    if isinstance(value, dict):
        yield from value
        for item in value.values():
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


class GvcfReportContractTests(unittest.TestCase):
    def test_declared_resources_validate_against_existing_schemas(self):
        contracts = _contracts()
        pairs = [
            ("report-catalog-entry.v1.schema.json", contracts["catalog"]["reports"][0]),
            ("report-capability-manifest.v1.schema.json",
             contracts["report_capability_manifest"]),
            ("report-intended-use.v1.schema.json", contracts["intended_use"]),
            ("capability-pack-manifest.v1.schema.json",
             _load("capabilities/gvcf-technical-preview.manifest.json")),
        ]
        pairs.extend(
            ("report-section-contract.v1.schema.json", section)
            for section in contracts["section_contracts"]["sections"]
        )
        for name, value in pairs:
            with self.subTest(schema=name):
                schema = _load(f"schemas/{name}")
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema).validate(value)

    def test_contract_has_no_evaluated_scientific_classes(self):
        contracts = _contracts()
        result = validate_rpt01_contracts(**contracts)
        self.assertTrue(result.passed, result.errors)
        self.assertEqual(result.report_id, REPORT_ID)
        self.assertEqual(result.catalog_id, "gvcf-technical-preview-catalog-v1")
        self.assertEqual(result.required_capability_ids, ("gvcf-technical-preview",))
        self.assertEqual(result.evaluated_analysis_class_ids, ())
        self.assertEqual(len(result.sections), 5)
        self.assertTrue(all(section.required for section in result.sections))
        self.assertTrue(all(
            section.empty_state_policy == "EXPLICIT_NOT_AVAILABLE"
            for section in result.sections
        ))
        self.assertEqual(result.intended_use.clinical_use_status,
                         "NOT_CLINICALLY_PROMOTED")
        self.assertIn("No clinical or variant-interpretation analysis classes",
                      contracts["intended_use"]["summary"])

    def test_catalog_capability_and_intended_use_cannot_cross_profiles(self):
        cases = []
        values = _contracts()
        values["catalog"]["catalog_id"] = "e1a-report-catalog-v1"
        cases.append(values)
        values = _contracts()
        values["intended_use"]["intended_use_category"] = "TECHNICAL_E1A_VALIDATION"
        cases.append(values)
        values = _contracts()
        values["intended_use"]["intended_use_id"] = "e1a-technical-validation"
        cases.append(values)
        values = _contracts()
        values["report_capability_manifest"]["required_capability_ids"] = [
            "canonical-interpretation-object"
        ]
        cases.append(values)
        values = _contracts()
        values["catalog"]["reports"].append(deepcopy(values["catalog"]["reports"][0]))
        cases.append(values)
        for values in cases:
            with self.subTest(values=values["intended_use"]["intended_use_id"]):
                self.assertFalse(validate_rpt01_contracts(**values).passed)
        unknown = _contracts()
        unknown["report_id"] = "unregistered-technical-report"
        self.assertFalse(validate_rpt01_contracts(**unknown).passed)

    def test_intended_use_schema_pairs_each_named_report_and_category(self):
        validator = Draft202012Validator(_load("schemas/report-intended-use.v1.schema.json"))
        for report_id, category in (
            (REPORT_ID, "TECHNICAL_E1A_VALIDATION"),
            (E1A_REPORT_ID, "TECHNICAL_GVCF_VALIDATION"),
        ):
            value = _contracts(report_id)["intended_use"]
            validator.validate(value)
            value["intended_use_category"] = category
            with self.subTest(report_id=report_id):
                with self.assertRaises(ValidationError):
                    validator.validate(value)
        value = _contracts()["intended_use"]
        value["report_id"] = "unregistered-technical-report"
        with self.assertRaises(ValidationError):
            validator.validate(value)

    def test_scientific_class_lists_remain_specific_to_each_profile(self):
        preview = _contracts()
        preview["report_capability_manifest"]["evaluated_analysis_class_ids"] = [
            "germline-snv-small-indel"
        ]
        self.assertFalse(validate_rpt01_contracts(**preview).passed)
        e1a = _contracts(E1A_REPORT_ID)
        self.assertTrue(validate_rpt01_contracts(**e1a).passed)
        e1a["report_capability_manifest"]["evaluated_analysis_class_ids"] = []
        self.assertFalse(validate_rpt01_contracts(**e1a).passed)

    def test_sections_cannot_hide_missing_assessment_or_claim_no_conflicts(self):
        cases = []
        for empty_state in ("EXPLICIT_NONE", "EXPLICIT_NO_CONFLICTS"):
            values = _contracts()
            values["section_contracts"]["sections"][2]["empty_state_policy"] = empty_state
            cases.append(values)
        values = _contracts()
        values["section_contracts"]["sections"][2]["required"] = False
        cases.append(values)
        values = _contracts()
        values["section_contracts"]["sections"][2]["required_semantic_inputs"] = []
        cases.append(values)
        values = _contracts()
        values["section_contracts"]["sections"].pop(2)
        values["catalog"]["reports"][0]["sections"].pop(2)
        for index, section in enumerate(values["section_contracts"]["sections"], 1):
            section["order"] = index
        cases.append(values)
        for values in cases:
            with self.subTest(sections=values["section_contracts"]["sections"]):
                self.assertFalse(validate_rpt01_contracts(**values).passed)


class GvcfReportingProjectionTests(unittest.TestCase):
    def setUp(self):
        self.run = _make_run()
        self.assertTrue(self.run.passed, self.run.errors)
        self.identity = _identity(self.run)

    def project(self, run=None, identity=None):
        return project_gvcf_interpretation(
            run=self.run if run is None else run,
            identity=self.identity if identity is None else identity,
        )

    def render(self, projected=None):
        projected = self.project() if projected is None else projected
        compiled = compile_report_pack(
            **_contracts(), canonical_interpretation=projected,
        )
        self.assertTrue(compiled.passed, compiled.errors)
        view = build_report_view_model_and_release_bundle(
            compiled_report_pack=compiled,
            artifact_ids=[artifact.artifact_id for artifact in self.run.artifact.artifacts],
        )
        self.assertTrue(view.passed, view.errors)
        ir = build_presentation_ir(report_view_model=view.view_model)
        self.assertTrue(ir.passed, ir.errors)
        presentation = ir.to_dict()["presentation_ir"]
        rendered = render_primary(
            presentation_ir=presentation, renderer_id="canonical-text-v1",
            lifecycle_profile="preview",
        )
        self.assertTrue(rendered.passed, rendered.errors)
        return compiled, view, presentation, rendered

    def test_every_source_column_and_ordered_format_value_is_preserved(self):
        projected = self.project()
        expected = [
            (line_number, line)
            for line_number, line in enumerate(
                self.run.source_gvcf.decode("utf-8").splitlines(), 1
            )
            if line and not line.startswith("#")
        ]
        self.assertEqual(len(expected), 5)
        self.assertEqual(len(projected["items"]), len(expected))
        for ordinal, ((line_number, line), item) in enumerate(
            zip(expected, projected["items"]), 1
        ):
            fields = line.split("\t")
            observation = item["observation"]
            self.assertEqual(observation["source_record_ordinal"], ordinal)
            self.assertEqual(observation["source_line_number"], line_number)
            self.assertEqual(observation["record_sha256"],
                             hashlib.sha256(line.encode("utf-8")).hexdigest())
            self.assertEqual(observation["chrom"], fields[0])
            self.assertEqual(observation["source_position"], fields[1])
            self.assertEqual(observation["pos"], int(fields[1]))
            self.assertEqual(observation["source_id"], fields[2])
            self.assertEqual(observation["ref"], fields[3])
            self.assertEqual(observation["alts"], fields[4].split(","))
            self.assertEqual(observation["qual"], fields[5])
            self.assertEqual(observation["filter"], fields[6])
            self.assertEqual(observation["info"], fields[7])
            expected_format = [
                {"key": key, "value": value}
                for key, value in zip(fields[8].split(":"), fields[9].split(":"))
            ]
            self.assertEqual(observation["format_values"], expected_format)
            self.assertEqual(observation["source_gt"], fields[9].split(":")[0])
        phased = projected["items"][1]["observation"]
        format_values = {item["key"]: item["value"] for item in phased["format_values"]}
        self.assertEqual(phased["alts"], ["A", "<NON_REF>"])
        self.assertEqual(format_values["GT"], "0|1")
        self.assertEqual(format_values["PGT"], "1|0")
        self.assertEqual(len(format_values["AD"].split(",")), 3)
        self.assertEqual(len(format_values["PL"].split(",")), 6)
        self.assertEqual(projected["items"][2]["observation"]["source_gt"], "./.")
        self.assertEqual(projected["items"][3]["observation"]["source_gt"], "0/.")

    def test_clinical_axes_and_conflicts_are_explicitly_not_assessed(self):
        projected = self.project()
        self.assertEqual(projected["conflicts"]["assessment_status"], "NOT_ASSESSED")
        self.assertEqual(projected["conflicts"]["availability"], "NOT_MEASURED")
        self.assertTrue(projected["conflicts"]["reason"])
        for item in projected["items"]:
            self.assertEqual(item["assessment_status"], "NOT_ASSESSED")
            self.assertEqual(item["clinical_availability"], "NOT_MEASURED")
            self.assertTrue(item["clinical_axes"])
            self.assertEqual(set(item["clinical_axes"].values()), {"NOT_ASSESSED"})
        self.assertIn(
            "No clinical or variant-interpretation analysis classes were evaluated.",
            projected["limitations"],
        )

    def test_provenance_is_the_bound_safe_export_with_context_timestamp(self):
        projected = self.project()
        exported = self.run.to_dict()
        self.assertEqual(projected["provenance"], [exported])
        self.assertEqual(exported["observed_at_origin"], "CALLER_PROVIDED_CONTEXT")
        self.assertEqual(exported["policy_status"]["lifecycle_profile"], "preview")
        self.assertEqual(exported["policy_status"]["clinical_use_status"],
                         "NOT_CLINICALLY_PROMOTED")
        self.assertEqual(exported["policy_status"]["release_authorization"],
                         "NOT_ESTABLISHED")
        self.assertEqual(exported["policy_status"]["clinical_assessment_status"],
                         "NOT_ASSESSED")

    def test_identity_mismatch_and_untyped_inputs_refuse(self):
        foreign = replace(self.identity, sample_id="FOREIGN-SAMPLE")
        with self.assertRaisesRegex(GvcfReportProjectionError,
                                    "^report_sample_identity_mismatch$"):
            self.project(identity=foreign)
        for identity in (None, self.identity.to_dict(), object()):
            with self.subTest(identity_type=type(identity).__name__):
                with self.assertRaises(GvcfReportProjectionError):
                    project_gvcf_interpretation(run=self.run, identity=identity)
        for run in (None, self.run.to_dict(), object()):
            with self.subTest(run_type=type(run).__name__):
                with self.assertRaises(GvcfReportProjectionError):
                    project_gvcf_interpretation(run=run, identity=self.identity)

    def test_source_changes_missing_components_and_stage_omission_refuse(self):
        candidates = [
            replace(self.run, source_gvcf=self.run.source_gvcf + b"\n"),
            replace(self.run, intake=None),
            replace(self.run, artifact=None),
            replace(self.run, canonical_model=None),
            replace(self.run, evidence_snapshot=None),
            replace(self.run, interpretation=None),
            replace(self.run, completed_stages=self.run.completed_stages[:-1]),
        ]
        for index, run in enumerate(candidates):
            with self.subTest(case=index):
                with self.assertRaises(GvcfReportProjectionError):
                    self.project(run=run)

    def test_both_rebinding_and_schema_validation_are_required(self):
        for target in ("rebind_gvcf_preview_run", "validate_gvcf_preview_export"):
            with self.subTest(boundary=target):
                with patch(
                    f"reporting.gvcf_projection.{target}",
                    side_effect=GvcfPreviewBindingError("private-diagnostic-sentinel"),
                ) as guard:
                    with self.assertRaisesRegex(
                        GvcfReportProjectionError, "^gvcf_run_binding_invalid$"
                    ) as raised:
                        self.project()
                    guard.assert_called_once()
                    self.assertNotIn("private-diagnostic-sentinel", str(raised.exception))

    def test_safe_export_schema_refuses_raw_fields_types_and_promoted_policy(self):
        baseline = self.run.to_dict()
        validate_gvcf_preview_export(baseline)
        candidates = []
        value = deepcopy(baseline)
        value["raw_record"] = "forbidden source payload"
        candidates.append(value)
        value = deepcopy(baseline)
        value["interpretation"]["items"][0]["observation"]["pos"] = True
        candidates.append(value)
        value = deepcopy(baseline)
        value["interpretation"]["items"][0]["assessment_status"] = "NEGATIVE"
        candidates.append(value)
        value = deepcopy(baseline)
        value["policy_status"]["lifecycle_profile"] = "final"
        candidates.append(value)
        value = deepcopy(baseline)
        value["observed_at_origin"] = "OBSERVED_EXECUTION"
        candidates.append(value)
        value = deepcopy(baseline)
        value["interpretation"]["items"][0]["observation"]["pos"] = float("nan")
        candidates.append(value)
        for index, value in enumerate(candidates):
            with self.subTest(case=index):
                with self.assertRaises(GvcfPreviewBindingError):
                    validate_gvcf_preview_export(value)

    def test_raw_vcf_headers_and_path_arguments_never_enter_projection(self):
        projected = self.project()
        forbidden = {
            "raw_record", "meta_lines", "source_gvcf", "reference_fasta",
            "bcftools_executable", "source_artifact_manifest", "input_profile",
            "execution_profile", "reference_profile",
        }
        self.assertTrue(forbidden.isdisjoint(set(_keys(projected))))
        strings = [value for value in _values(projected) if isinstance(value, str)]
        for line in self.run.source_gvcf.decode("utf-8").splitlines():
            if line:
                self.assertNotIn(line, strings)
        self.assertNotIn(self.run.source_gvcf.decode("utf-8"), strings)

    def test_projection_is_detached_from_run_and_provenance(self):
        projected = self.project()
        before = self.run.to_dict()
        original_gt = projected["items"][0]["observation"]["source_gt"]
        projected["items"][0]["observation"]["source_gt"] = "1/1"
        projected["limitations"].clear()
        self.assertEqual(
            projected["provenance"][0]["interpretation"]["items"][0]["observation"]["source_gt"],
            original_gt,
        )
        projected["provenance"][0]["policy_status"]["lifecycle_profile"] = "final"
        self.assertEqual(self.run.to_dict(), before)
        self.assertEqual(self.project()["items"][0]["observation"]["source_gt"], original_gt)

    def test_reference_only_preview_does_not_claim_callability_or_negative_findings(self):
        lines = self.run.source_gvcf.splitlines(keepends=True)
        retained = [line for line in lines if line.startswith(b"#")]
        retained.append(next(line for line in lines if line and not line.startswith(b"#")))
        run = _make_run(b"".join(retained))
        self.assertTrue(run.passed, run.errors)
        projected = project_gvcf_interpretation(run=run, identity=_identity(run))
        self.assertEqual(len(projected["items"]), 1)
        item = projected["items"][0]
        self.assertEqual(item["observation"]["kind"], "REFERENCE_BLOCK")
        self.assertEqual(item["clinical_axes"]["biological_callability"], "NOT_ASSESSED")
        self.assertEqual(projected["conflicts"]["assessment_status"], "NOT_ASSESSED")
        compiled = compile_report_pack(**_contracts(), canonical_interpretation=projected)
        self.assertTrue(compiled.passed, compiled.errors)
        self.assertTrue(all(section["state"] == "PRESENT" for section in compiled.sections))

    def test_real_refusal_result_cannot_project_a_preview(self):
        source = self.run.source_gvcf.replace(
            b"END=10009\t", b"END=10009;UNSUPPORTED=1\t", 1
        )
        self.assertNotEqual(source, self.run.source_gvcf)
        refused = _make_run(source)
        self.assertFalse(refused.passed)
        with self.assertRaises(GvcfReportProjectionError):
            self.project(run=refused)

    def test_existing_compiler_viewmodel_ir_and_renderer_are_complete_and_deterministic(self):
        first = self.render()
        second = self.render()
        compiled, view, presentation, rendered = first
        self.assertEqual(len(compiled.sections), 5)
        self.assertTrue(all(section["state"] == "PRESENT" for section in compiled.sections))
        self.assertEqual(view.completeness_manifest["status"], "COMPLETE")
        self.assertEqual(view.completeness_manifest["required_section_count"], 5)
        self.assertEqual(view.completeness_manifest["present_section_count"], 5)
        self.assertEqual(list(view.completeness_manifest["missing_required_sections"]), [])
        self.assertEqual(presentation["report_id"], REPORT_ID)
        self.assertEqual(rendered.lifecycle_profile, "preview")
        self.assertEqual(rendered.artifact_sha256,
                         hashlib.sha256(rendered.artifact_text.encode("utf-8")).hexdigest())
        self.assertEqual(rendered.artifact_text, second[3].artifact_text)
        self.assertEqual(rendered.artifact_sha256, second[3].artifact_sha256)
        self.assertIn("NOT_ASSESSED", rendered.artifact_text)

    def test_primary_renderer_refuses_final_lifecycle(self):
        _, _, presentation, _ = self.render()
        for lifecycle in ("final", "FINAL"):
            with self.subTest(lifecycle=lifecycle):
                refused = render_primary(
                    presentation_ir=presentation, renderer_id="canonical-text-v1",
                    lifecycle_profile=lifecycle,
                )
                self.assertFalse(refused.passed)
                self.assertEqual(refused.errors, ("lifecycle_profile_not_supported",))
                self.assertIsNone(refused.artifact_text)


if __name__ == "__main__":
    unittest.main()
