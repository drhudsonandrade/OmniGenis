"""Optional actual-child QA observations with synthetic inputs and unchanged receipts."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from reporting import pdf_document_metadata as metadata
from reporting import typed_original_brand_qa as qa
from reporting.brand_assets import BoundBrandAssets
from reporting.page_windows import plan_pdf_page_windows
from reporting.typed_original_brand_pdf import render_typed_original_brand_pdf
from reporting.typed_pdf_source import prepare_typed_pdf_source
import test_reporting_typed_original_brand_qa as fixtures


def sealed(value):
    """Rehash deliberate negative-control changes without hiding their invalid identity."""
    value["observation_sha256"] = sha256(qa.canonical({
        key: item for key, item in value.items() if key != "observation_sha256"
    })).hexdigest()
    return value


class TypedOriginalBrandProcessObservationTests(unittest.TestCase):
    """Exercise one fixed synthetic report without importing another TestCase family."""

    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        assets = root / "reporting/assets"
        cls.assets = BoundBrandAssets(
            logo_svg=(assets / "omnigenis_logo.svg").read_bytes(),
            dna_png=(assets / "dna_column.png").read_bytes(),
            permission_notice=(assets / "brand-permission.md").read_bytes())
        cls.source = fixtures.document(2)
        cls.ir_sha = fixtures.digest(cls.source)
        cls.bundle_sha = cls.assets.to_dict()["bundle_sha256"]
        with patch.dict(os.environ, fixtures.FLAGS):
            rendered, unused = render_typed_original_brand_pdf(
                presentation_ir=cls.source, expected_ir_sha256=cls.ir_sha,
                assets=cls.assets, expected_brand_bundle_sha256=cls.bundle_sha)
            cls.pdf = rendered.pdf_bytes
            expectation = prepare_typed_pdf_source(
                presentation_ir=cls.source, expected_ir_sha256=cls.ir_sha)
            cls.arguments = {
                "presentation_ir": cls.source, "expected_ir_sha256": cls.ir_sha,
                "expected_source_expectation_sha256": expectation.expectation_sha256,
                "assets": cls.assets, "expected_brand_bundle_sha256": cls.bundle_sha,
                "pdf_bytes": cls.pdf, "expected_pdf_sha256": sha256(cls.pdf).hexdigest(),
                "expected_profile_sha256": qa.profile_sha256(),
            }
            original_worker = qa._run_worker

            def capture_native(*args, **kwargs):
                result = original_worker(*args, **kwargs)
                cls.native_raw, cls.native_process = result
                return result

            with patch.object(qa, "_run_worker", side_effect=capture_native):
                cls.receipt, cls.observation = qa.validate_observed_typed_original_brand_candidate(
                    **cls.arguments)
            value = cls.receipt.to_dict()
            cls.plan = plan_pdf_page_windows(
                pdf_sha256=value["pdf_sha256"], ir_sha256=value["ir_sha256"],
                page_count=value["page_count"], admitted_page_budget=64,
                pages_per_window=4, inspector_profile_id="generated-pdf-raster-window-v1")
            cls.metadata_receipt = metadata.PdfDocumentMetadataInspection(
                pdf_sha256=value["pdf_sha256"], ir_sha256=value["ir_sha256"],
                plan_sha256=cls.plan.plan_sha256, page_count=value["page_count"])
            cls.raster_results = []
            for row, window in zip(cls.observation["raster_windows"], cls.plan.windows, strict=True):
                receipt, checked = qa._checked_process_window(row, cls.plan, window)
                cls.raster_results.append(
                    (receipt, checked["child_observation"], checked["direct_process"]))

    def setUp(self):
        mode = patch.dict(os.environ, fixtures.FLAGS)
        mode.start()
        self.addCleanup(mode.stop)

    def args(self, **changes):
        result = dict(self.arguments, presentation_ir=deepcopy(self.source))
        result.update(changes)
        return result

    def metadata_args(self):
        return dict(pdf_bytes=self.pdf, plan=self.plan,
                    expected_plan_sha256=self.plan.plan_sha256, expected_language="en-US")

    def reject_bundle(self, value):
        sealed(value)
        with self.assertRaises(ValueError):
            qa._validate_process_observation(value, self.receipt)

    def assert_reaped(self, process):
        self.assertIs(type(process.returncode), int)
        with self.assertRaises(ChildProcessError):
            os.waitpid(process.pid, os.WNOHANG)

    def test_real_observed_qa_has_complete_owned_sequence_and_fixed_counts(self):
        value = self.receipt.to_dict()
        result = qa._validate_process_observation(self.observation, self.receipt)
        self.assertEqual(result, self.observation)
        self.assertEqual(result["qa_sha256"], sha256(qa.canonical(value)).hexdigest())
        windows, pages = value["raster_window_count"], value["page_count"]
        self.assertEqual(result["observed_direct_spawns"], 2 + windows)
        self.assertEqual(result["observed_direct_exits"], 2 + windows)
        self.assertEqual(result["observed_nested_spawns"], pages + windows)
        self.assertEqual(result["observed_nested_exits"], pages + windows)
        self.assertEqual(result["observed_total_spawns"], pages + 2 * windows + 2)
        self.assertEqual(result["observed_total_exits"], result["observed_total_spawns"])
        direct = [result["native_qa_process"], result["metadata_process"],
                  *(row["direct_process"] for row in result["raster_windows"])]
        self.assertEqual([row["kind"] for row in direct[:2]],
                         ["typed_original_brand_qa", "document_metadata"])
        self.assertNotIn("render_process", result)
        for row in direct:
            self.assertEqual(row["parent_pid"], os.getpid())
            self.assertEqual(row["exit_code"], 0)
            self.assertLessEqual(len(qa.canonical(row)), 2048)
            with self.assertRaises(ChildProcessError):
                os.waitpid(row["pid"], os.WNOHANG)
        self.assertLessEqual(len(qa.canonical(result)), 65536)

    def test_default_qa_and_metadata_receipts_remain_identical(self):
        ordinary = qa.validate_typed_original_brand_candidate(**self.args())
        self.assertIs(type(ordinary), qa.TypedOriginalBrandQA)
        self.assertEqual(qa.canonical(ordinary.to_dict()), qa.canonical(self.receipt.to_dict()))
        ordinary_metadata = metadata.inspect_pdf_document_metadata(**self.metadata_args())
        observed_metadata, process = metadata.inspect_observed_pdf_document_metadata(
            **self.metadata_args())
        self.assertEqual(ordinary_metadata.to_dict(), observed_metadata.to_dict())
        self.assertEqual(observed_metadata.inspection_sha256, self.metadata_receipt.inspection_sha256)
        self.assertEqual(metadata._validate_process_observation(process, observed_metadata), process)
        self.assertLessEqual(len(qa.canonical(process)), 2048)

    def test_sidecars_are_detached_bounded_and_make_no_expanded_claims(self):
        checked = qa._validate_process_observation(self.observation, self.receipt)
        checked["raster_windows"][0]["geometry"]["total_pixels"] += 1
        self.assertNotEqual(checked, self.observation)
        for key in qa._PROCESS_NONCLAIMS:
            self.assertIs(self.observation[key], False)
        huge = deepcopy(self.observation)
        huge["schema"] = "x" * 65536
        self.reject_bundle(huge)
        huge_row = deepcopy(self.observation["metadata_process"])
        huge_row["worker"] = "x" * 2048
        sealed(huge_row)
        with self.assertRaises(ValueError):
            metadata._validate_process_observation(huge_row, self.metadata_receipt)
        expanded = deepcopy(self.observation)
        expanded["authenticated_worker_execution"] = True
        self.reject_bundle(expanded)

    def test_rehashed_direct_source_request_result_and_type_changes_are_refused(self):
        replacements = (
            ("worker_source_sha256", "0" * 64), ("request_header_sha256", "0" * 64),
            ("native_observation_sha256", "0" * 64), ("profile_sha256", "0" * 64),
            ("pdf_byte_count", len(self.pdf) + 1), ("pid", True), ("exit_code", False),
            ("parent_pid", os.getpid() + 1), ("extra_field", 0),
        )
        for key, replacement in replacements:
            changed = deepcopy(self.observation)
            changed["native_qa_process"][key] = replacement
            sealed(changed["native_qa_process"])
            with self.subTest(field=key):
                self.reject_bundle(changed)
        for key in ("inspection_sha256", "request_header_sha256", "worker_source_sha256"):
            changed = deepcopy(self.observation)
            changed["metadata_process"][key] = "0" * 64
            sealed(changed["metadata_process"])
            with self.subTest(metadata_field=key):
                self.reject_bundle(changed)

    def test_rehashed_window_geometry_identity_and_complete_coverage_are_required(self):
        for mode in ("missing", "extra", "wrong_index", "wrong_geometry", "wrong_parent"):
            changed = deepcopy(self.observation)
            if mode == "missing":
                changed["raster_windows"].pop()
            elif mode == "extra":
                changed["raster_windows"].append(deepcopy(changed["raster_windows"][0]))
            elif mode == "wrong_index":
                changed["raster_windows"][0]["window_index"] += 1
            elif mode == "wrong_geometry":
                changed["raster_windows"][0]["geometry"]["raster_sha256"] = "0" * 64
            else:
                row = changed["raster_windows"][0]["direct_process"]
                row["parent_pid"] += 1
                sealed(row)
            with self.subTest(case=mode):
                self.reject_bundle(changed)

    def test_rehashed_cross_stage_pid_reuse_and_lifetime_escape_are_refused(self):
        changed = deepcopy(self.observation)
        window = changed["raster_windows"][0]
        pid = changed["metadata_process"]["pid"]
        window["direct_process"]["pid"] = pid
        nested = window["child_observation"]
        nested["worker_pid"] = pid
        for child in nested["processes"]:
            child["parent_pid"] = pid
        sealed(nested)
        window["direct_process"]["nested_observation_sha256"] = sha256(qa.canonical(nested)).hexdigest()
        sealed(window["direct_process"])
        self.reject_bundle(changed)
        for mode in ("direct_overlap", "nested_escape", "nested_nonzero"):
            changed = deepcopy(self.observation)
            if mode == "direct_overlap":
                row = changed["metadata_process"]
                row["spawn_monotonic_ns"] = changed["native_qa_process"]["spawn_monotonic_ns"] - 1
                sealed(row)
            else:
                window = changed["raster_windows"][0]
                nested = window["child_observation"]
                child = nested["processes"][-1]
                if mode == "nested_escape":
                    child["observed_exit_ns"] = window["direct_process"]["exit_monotonic_ns"] + 1
                else:
                    child["exit_code"] = 1
                sealed(nested)
                window["direct_process"]["nested_observation_sha256"] = sha256(qa.canonical(nested)).hexdigest()
                sealed(window["direct_process"])
            with self.subTest(case=mode):
                self.reject_bundle(changed)

    def test_observed_standalone_guards_preserve_default_metadata_admission(self):
        identity = self.metadata_receipt._identity()
        raw = metadata._canonical({
            "protocol_version": metadata.PROTOCOL_VERSION, "profile_id": metadata.PROFILE_ID,
            "parser_version": metadata.PARSER_VERSION, "identity": identity,
            "limits": metadata._LIMITS, "inspection": metadata._INSPECTION,
        })
        with (
            patch.object(metadata, "require_no_active_scope", side_effect=ValueError("active")),
            patch.object(metadata, "_run_worker", return_value=raw) as worker,
        ):
            ordinary = metadata.inspect_pdf_document_metadata(**self.metadata_args())
            self.assertEqual(ordinary.to_dict(), self.metadata_receipt.to_dict())
            worker.reset_mock()
            with self.assertRaises(metadata.PdfDocumentMetadataError):
                metadata.inspect_observed_pdf_document_metadata(**self.metadata_args())
            worker.assert_not_called()
        with (
            patch.object(qa, "require_no_active_scope", side_effect=ValueError("active")),
            patch.object(qa, "_run_worker") as worker,
        ):
            with self.assertRaises(qa.TypedOriginalBrandQAError):
                qa.validate_observed_typed_original_brand_candidate(**self.args())
            worker.assert_not_called()

    def test_disable_and_identity_refusals_happen_before_observed_child_launch(self):
        for flag in qa.DISABLE_FLAGS:
            with (
                self.subTest(disable=flag), patch.dict(os.environ, {flag: "1"}),
                patch.object(qa, "_run_worker") as worker,
            ):
                with self.assertRaises(qa.TypedOriginalBrandQAError):
                    qa.validate_observed_typed_original_brand_candidate(**self.args())
                worker.assert_not_called()
        for key in ("expected_pdf_sha256", "expected_profile_sha256", "expected_source_expectation_sha256"):
            with self.subTest(identity=key), patch.object(qa, "_run_worker") as worker:
                with self.assertRaises(qa.TypedOriginalBrandQAError):
                    qa.validate_observed_typed_original_brand_candidate(**self.args(**{key: "0" * 64}))
                worker.assert_not_called()

    def test_later_stage_and_postflight_failures_never_return_partial_observations(self):
        # Replayed stage fixtures are used only to reach explicit negative paths.
        native = (self.native_raw, deepcopy(self.native_process))
        metadata_result = (self.metadata_receipt, deepcopy(self.observation["metadata_process"]))
        with (
            patch.object(qa, "_run_worker", return_value=native),
            patch.object(qa, "inspect_observed_pdf_document_metadata",
                         side_effect=metadata.PdfDocumentMetadataError("refused")),
            patch.object(qa, "inspect_process_observed_pdf_raster_window") as raster,
        ):
            with self.assertRaises(qa.TypedOriginalBrandQAError):
                qa.validate_observed_typed_original_brand_candidate(**self.args())
            raster.assert_not_called()
        with (
            patch.object(qa, "_run_worker", return_value=native),
            patch.object(qa, "inspect_observed_pdf_document_metadata", return_value=metadata_result),
            patch.object(qa, "inspect_process_observed_pdf_raster_window", side_effect=ValueError("refused")),
        ):
            with self.assertRaises(qa.TypedOriginalBrandQAError):
                qa.validate_observed_typed_original_brand_candidate(**self.args())
        original_prepare = qa.prepare_typed_original_brand_pdf
        calls = []

        def changed_postflight(**kwargs):
            result = original_prepare(**kwargs)
            calls.append(None)
            if len(calls) == 2:
                return result[0], result[1], {**result[2], "html_sha256": "0" * 64}
            return result

        with (
            patch.object(qa, "_run_worker", return_value=native),
            patch.object(qa, "inspect_observed_pdf_document_metadata", return_value=metadata_result),
            patch.object(qa, "inspect_process_observed_pdf_raster_window",
                         side_effect=deepcopy(self.raster_results)),
            patch.object(qa, "prepare_typed_original_brand_pdf", side_effect=changed_postflight),
        ):
            with self.assertRaises(qa.TypedOriginalBrandQAError):
                qa.validate_observed_typed_original_brand_candidate(**self.args())
        self.assertEqual(len(calls), 2)

    def test_actual_owned_children_are_reaped_after_observation_registration_failure(self):
        captured = []

        def fail_registration(process, command, started):
            captured.append(process)
            raise ValueError("synthetic registration refusal")

        with (
            patch.object(qa, "_observe_qa_spawn", side_effect=fail_registration),
            patch.object(qa, "wait_owned_process_group", wraps=qa.wait_owned_process_group) as waiter,
        ):
            with self.assertRaisesRegex(ValueError, "synthetic registration refusal"):
                qa._run_worker(self.pdf, sha256(self.pdf).hexdigest(), observe_processes=True)
            self.assertEqual(waiter.call_count, 1)
            self.assertEqual(waiter.call_args.kwargs, {"wall_seconds": 0, "cleanup_seconds": 2})
        self.assert_reaped(captured.pop())
        with (
            patch.object(metadata, "_observe_metadata_spawn", side_effect=fail_registration),
            patch.object(metadata, "_cleanup_owned", wraps=metadata._cleanup_owned) as cleanup,
            patch.object(metadata, "_wait") as waiter,
        ):
            with self.assertRaisesRegex(ValueError, "synthetic registration refusal"):
                metadata._run_worker(self.pdf, self.metadata_receipt._identity(), observe_processes=True)
            self.assertEqual(cleanup.call_count, 1)
            self.assertEqual(cleanup.call_args.kwargs, {"wall_seconds": 0})
            waiter.assert_not_called()
        self.assert_reaped(captured.pop())

    def test_lost_waitable_identity_never_retries_numeric_cleanup(self):
        captured = []
        original_qa_wait = qa.wait_owned_process_group
        original_metadata_wait = metadata._wait

        def qa_reaped_elsewhere(process, command, started):
            original_qa_wait(process, wall_seconds=qa.WALL_SECONDS, cleanup_seconds=qa.CLEANUP_SECONDS)
            captured.append(process)
            raise ChildProcessError("synthetic observed ownership loss")

        def metadata_reaped_elsewhere(process, command, started):
            original_metadata_wait(process)
            captured.append(process)
            raise ChildProcessError("synthetic observed ownership loss")

        with (
            patch.object(qa, "_observe_qa_spawn", side_effect=qa_reaped_elsewhere),
            patch.object(qa, "wait_owned_process_group") as waiter,
        ):
            with self.assertRaises(ChildProcessError):
                qa._run_worker(self.pdf, sha256(self.pdf).hexdigest(), observe_processes=True)
            waiter.assert_not_called()
        self.assert_reaped(captured.pop())
        with (
            patch.object(metadata, "_observe_metadata_spawn", side_effect=metadata_reaped_elsewhere),
            patch.object(metadata, "_cleanup_owned") as cleanup,
            patch.object(metadata, "_wait") as waiter,
        ):
            with self.assertRaises(ChildProcessError):
                metadata._run_worker(self.pdf, self.metadata_receipt._identity(), observe_processes=True)
            cleanup.assert_not_called()
            waiter.assert_not_called()
        self.assert_reaped(captured.pop())

    def test_observed_clock_regression_refuses_after_actual_final_reap(self):
        captured = []
        original = qa._observe_qa_spawn

        def capture(process, command, started):
            captured.append(process)
            return original(process, command, started)

        with (
            patch.object(qa, "monotonic_ns", side_effect=[100, 99]),
            patch.object(qa, "_observe_qa_spawn", side_effect=capture),
        ):
            with self.assertRaises(ValueError):
                qa._run_worker(self.pdf, sha256(self.pdf).hexdigest(), observe_processes=True)
        self.assert_reaped(captured.pop())
