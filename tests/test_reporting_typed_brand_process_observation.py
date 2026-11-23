"""Optional fixed render observations retain the original candidate and custody contract."""
from contextlib import ExitStack, contextmanager
from hashlib import sha256
import inspect
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from reporting import adapters, pdf_typed_brand_render as route
from reporting import typed_original_brand_pdf as brand
from reporting import _pdf_native_launch_budget as budget
from reporting.brand_assets import BoundBrandAssets
from reporting.page_windows import plan_pdf_page_windows
from reporting.pdf_raster_resources import RasterAsset, RasterBundle, _canonical
import test_reporting_pdf_raster_resources as raster_fixtures
import test_reporting_typed_pdf_source as source_fixtures


class TypedBrandProcessObservationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNone(budget._TYPED_RENDER)
        self.enterContext(patch.dict(os.environ, {
            **dict.fromkeys(route.DISABLE, ""),
            "OMNIGENIS_PDF_QUALIFICATION_RECEIPT_MODE": "qualification-only",
        }))
        self.profile = route.require_policy()
        png = raster_fixtures.png()
        self.bundle = RasterBundle((RasterAsset(
            "synthetic", png, sha256(png).hexdigest(), 2, 2, "a" * 64, "b" * 64),))
        self.args = {
            "html": "synthetic", "expected_html_sha256": sha256(b"synthetic").hexdigest(),
            "resources": self.bundle, "expected_resources_sha256": self.bundle.bundle_sha256,
        }
        self.pdf = b"%PDF-1.7 synthetic process transport"
        self.record = {
            "protocol_version": 3, "profile_id": route.emission.PROFILE_ID,
            "profile_sha256": self.profile, "status": "PASS", "pdf_bytes": len(self.pdf),
            "pdf_sha256": sha256(self.pdf).hexdigest(), "limits": {
                "memory_bytes": 1073741824, "cpu_seconds": 20,
                "file_bytes": 16777216, "open_files": 128,
            },
            "html_sha256": self.args["expected_html_sha256"], "page_count": 1,
            "resources_sha256": self.bundle.bundle_sha256, "resource_count": 1,
        }

    def _scope(self):
        plan = plan_pdf_page_windows(
            pdf_sha256=sha256(self.pdf).hexdigest(), ir_sha256="b" * 64,
            page_count=1, admitted_page_budget=1, pages_per_window=16,
            inspector_profile_id="generated-pdf-text-window-v1")
        return budget.NativeLaunchScope(plan, len(self.pdf))

    @contextmanager
    def _transport(self, *, after_start=None, after_wait=None, status=0):
        """Exercise the real parent transport using explicitly synthetic child controls."""
        state = {"processes": [], "waits": [], "fds": []}
        def start(command, *, env, pass_fds, before_spawn):
            before_spawn()
            process = object.__new__(route._Process)
            process._child_created = False
            process.pid = 2147483000
            process.args = command
            process.returncode = None
            state["processes"].append(process)
            state["fds"].extend(pass_fds)
            os.pwrite(pass_fds[1], self.pdf, 0)
            os.pwrite(pass_fds[2], json.dumps(self.record).encode("ascii"), 0)
            if after_start is not None:
                after_start(process, command, pass_fds)
            return process
        def wait(process, *, wall_seconds, cleanup_observer):
            state["waits"].append(wall_seconds)
            self.assertIs(process, state["processes"][-1])
            process.returncode = status
            cleanup_observer(status)
            if after_wait is not None:
                after_wait(process)
            return status
        with ExitStack() as stack:
            stack.enter_context(patch.object(adapters, "_start_pdf_render_worker", new=start))
            stack.enter_context(patch.object(adapters, "_wait_pdf_render_worker", new=wait))
            state["ownership"] = stack.enter_context(patch.object(route, "_observe_owned"))
            state["group"] = stack.enter_context(patch.object(route.os, "getpgid",
                                                               return_value=2147483000))
            yield state
        for fd in state["fds"]:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def _brand_arguments(self):
        root = Path(brand.__file__).parent / "assets"
        assets = BoundBrandAssets(
            logo_svg=(root / "omnigenis_logo.svg").read_bytes(),
            dna_png=(root / "dna_column.png").read_bytes(),
            permission_notice=(root / "brand-permission.md").read_bytes())
        source = source_fixtures.document(2)
        return {
            "presentation_ir": source, "expected_ir_sha256": source_fixtures.digest(source),
            "assets": assets, "expected_brand_bundle_sha256": assets.to_dict()["bundle_sha256"],
        }

    def test_observed_transport_preserves_default_candidate_and_detached_closed_row(self):
        with self._transport() as default_state, patch.object(
                route, "_observe_typed_render_spawn") as unused:
            default = route.render_typed_brand_pdf(**self.args)
            unused.assert_not_called()
            self.assertEqual(default_state["waits"], [30])
        captured = []
        original = route._observe_typed_render_spawn
        def observe(*args):
            value = original(*args)
            captured.append(value)
            return value
        with self._transport() as state, patch.object(
                route, "_observe_typed_render_spawn", side_effect=observe):
            candidate, row = route.render_observed_typed_brand_pdf(**self.args)
            self.assertEqual(state["waits"], [30])
            self.assertEqual(len(state["processes"]), 1)
            self.assertEqual(row["pid"], state["processes"][0].pid)
            state["ownership"].assert_called_once_with(state["processes"][0])
        self.assertEqual(candidate.pdf_bytes, default.pdf_bytes)
        self.assertEqual(candidate.to_dict(), default.to_dict())
        self.assertEqual(set(row), route._PROCESS_FIELDS | {
            "html_sha256", "resources_sha256", "pdf_sha256", "pdf_byte_count",
            "page_count", "resource_count", "render_sha256", "profile_sha256",
            "observation_sha256"})
        self.assertEqual(row["render_sha256"], sha256(_canonical(candidate.to_dict())).hexdigest())
        self.assertEqual(row["request_header_sha256"], sha256(
            f"3 {candidate.html_sha256} {candidate.resources_sha256}\n".encode()).hexdigest())
        self.assertEqual(row["observation_sha256"], sha256(_canonical({
            key: value for key, value in row.items() if key != "observation_sha256"})).hexdigest())
        self.assertLessEqual(len(_canonical(row)), 2048)
        captured[0]["pid"] = 2
        self.assertNotEqual(row["pid"], 2)
        row["pdf_sha256"] = "0" * 64
        self.assertEqual(candidate.pdf_sha256, default.pdf_sha256)
        for key in ("aggregate_job_resources_qualified", "release_authorized",
                    "accessibility_qualified", "default_pipeline_activated"):
            self.assertIs(candidate.to_dict()[key], False)

    def test_actual_original_brand_observation_records_one_owned_reaped_child(self):
        arguments = self._brand_arguments()
        default, source_metadata = brand.render_typed_original_brand_pdf(**arguments)
        launched = []
        start = adapters._start_pdf_render_worker
        def capture(*args, **kwargs):
            process = start(*args, **kwargs)
            launched.append(process)
            return process
        with patch.object(adapters, "_start_pdf_render_worker", new=capture):
            candidate, metadata, row = brand.render_observed_typed_original_brand_pdf(**arguments)
        self.assertEqual(len(launched), 1)
        self.assertEqual(candidate.pdf_bytes, default.pdf_bytes)
        self.assertEqual(candidate.to_dict(), default.to_dict())
        self.assertEqual(metadata, source_metadata)
        self.assertEqual(row["pid"], launched[0].pid)
        self.assertEqual(row["parent_pid"], os.getpid())
        self.assertEqual(row["exit_code"], launched[0].returncode)
        self.assertEqual(row["exit_code"], 0)
        with self.assertRaises(ChildProcessError):
            os.waitpid(launched[0].pid, os.WNOHANG)
        self.assertEqual(row["worker"], "_pdf_typed_brand_render_worker.py")
        self.assertEqual(row["worker_source_sha256"], sha256(
            Path(route.__file__).with_name(row["worker"]).read_bytes()).hexdigest())
        self.assertEqual(row["pdf_sha256"], candidate.pdf_sha256)
        self.assertEqual(row["profile_sha256"], candidate.to_dict()["profile_sha256"])
        self.assertLessEqual(row["spawn_monotonic_ns"], row["exit_monotonic_ns"])
        self.assertIsNone(budget._TYPED_RENDER)

    def test_observed_admission_guards_precede_descriptors_and_source_preparation(self):
        for key in (*route.DISABLE, "OMNIGENIS_PDF_QUALIFICATION_RECEIPT_MODE"):
            with self.subTest(flag=key), patch.dict(os.environ, {key: "1"}), patch.object(
                    adapters, "_start_pdf_render_worker") as start, patch.object(
                    route.tempfile, "TemporaryFile") as descriptors:
                with self.assertRaisesRegex(ValueError, "^typed_brand_render_refused$"):
                    route.render_observed_typed_brand_pdf(**self.args)
                start.assert_not_called()
                descriptors.assert_not_called()
        with self._scope() as scope, patch.object(
                brand, "prepare_typed_original_brand_pdf") as prepare, patch.object(
                brand, "render_observed_typed_brand_pdf") as render:
            with self.assertRaises(brand.TypedOriginalBrandError):
                brand.render_observed_typed_original_brand_pdf(
                    presentation_ir=None, expected_ir_sha256=None,
                    assets=None, expected_brand_bundle_sha256=None)
            prepare.assert_not_called()
            render.assert_not_called()
            self.assertTrue(scope._invalid)
        with self._scope() as scope, patch.object(route.tempfile, "TemporaryFile") as descriptors:
            with self.assertRaises(ValueError):
                route.render_observed_typed_brand_pdf(**self.args)
            descriptors.assert_not_called()
            self.assertTrue(scope._invalid)

    def test_observed_child_identity_header_and_source_mismatches_withhold_result(self):
        for case in ("parent_pid", "command", "header", "worker_source", "extra_claim"):
            with self.subTest(case=case):
                def changed(process, command, fds):
                    if case == "parent_pid":
                        process.pid = os.getpid()
                    elif case == "command":
                        process.args = ["unselected-worker"]
                    elif case == "header":
                        os.pwrite(fds[0], b"4", 0)
                original = route._observe_typed_render_spawn
                def observe(*args):
                    value = original(*args)
                    if case == "worker_source":
                        value["worker_source_sha256"] = "0" * 64
                    elif case == "extra_claim":
                        value["aggregate_job_resources_qualified"] = True
                    return value
                with self._transport(after_start=changed) as state, patch.object(
                        route, "_observe_typed_render_spawn", side_effect=observe):
                    with self.assertRaisesRegex(ValueError, "^typed_brand_render_refused$"):
                        route.render_observed_typed_brand_pdf(**self.args)
                    self.assertEqual(state["waits"], [0] if case in ("parent_pid", "command") else [30])
                self.assertIsNone(budget._TYPED_RENDER)

    def test_observed_exit_clock_and_command_mutation_never_retry_settled_cleanup(self):
        for case in ("exit_boolean", "exit_mismatch", "clock_reversed", "command_mutated", "nonzero"):
            with self.subTest(case=case), ExitStack() as stack:
                def changed(process):
                    if case == "exit_boolean":
                        process.returncode = True
                    elif case == "exit_mismatch":
                        process.returncode = 9
                    elif case == "command_mutated":
                        process.args[3] = "unselected-worker"
                state = stack.enter_context(self._transport(
                    after_wait=changed, status=1 if case == "nonzero" else 0))
                if case == "clock_reversed":
                    stack.enter_context(patch.object(route, "monotonic_ns", side_effect=[20, 19]))
                with self.assertRaises(ValueError):
                    route.render_observed_typed_brand_pdf(**self.args)
                self.assertEqual(state["waits"], [30])
            self.assertIsNone(budget._TYPED_RENDER)

    def test_observation_cancellation_uses_proven_pre_wait_cleanup_once(self):
        interruption = KeyboardInterrupt("synthetic optional capture cancellation")
        with self._transport() as state, patch.object(
                route, "_observe_typed_render_spawn", side_effect=interruption):
            with self.assertRaises(KeyboardInterrupt) as error:
                route.render_observed_typed_brand_pdf(**self.args)
            self.assertIs(error.exception, interruption)
            self.assertEqual(state["waits"], [0])
        self.assertIsNone(budget._TYPED_RENDER)
        with self._scope():
            pass

    def test_observed_identity_loss_quarantines_without_another_pid_lookup(self):
        for case in ("ownership_lost", "already_reaped"):
            with self.subTest(case=case), patch.object(budget, "_TYPED_RENDER", None):
                def changed(process, command, fds):
                    if case == "already_reaped":
                        process.returncode = 0
                with self._transport(after_start=changed) as state:
                    state["ownership"].side_effect = ChildProcessError("synthetic ownership loss")
                    with self.assertRaisesRegex(ValueError, "^typed_brand_render_refused$"):
                        route.render_observed_typed_brand_pdf(**self.args)
                    self.assertEqual(state["waits"], [])
                    self.assertEqual(state["ownership"].call_count, int(case == "ownership_lost"))
                    state["group"].assert_not_called()
                    self.assertEqual(budget._TYPED_RENDER.phase, "quarantined")
                    with self.assertRaises(ValueError), self._scope():
                        pass
                    with self.assertRaises(ValueError), budget.reserve_standalone_typed_render():
                        pass

    def test_default_pre_wait_child_error_retains_original_cleanup_semantics(self):
        fn = route._run_owned_child
        lines, first = inspect.getsourcelines(fn)
        handoff = first + next(index for index, line in enumerate(lines)
            if line.strip() == "status = waiter(process, wall_seconds=wall, cleanup_observer=observer)")
        interruption = ChildProcessError("synthetic default pre-wait interruption")
        def interrupt(frame, event, argument):
            if frame.f_code is fn.__code__ and event == "line" and frame.f_lineno == handoff:
                raise interruption
            return interrupt
        with self._transport() as state, patch.object(route, "_observe_typed_render_spawn") as unused:
            previous_trace = sys.gettrace()
            try:
                sys.settrace(interrupt)
                with self.assertRaisesRegex(ValueError, "^typed_brand_render_refused$"):
                    route.render_typed_brand_pdf(**self.args)
            finally:
                sys.settrace(previous_trace)
            unused.assert_not_called()
            self.assertEqual(state["waits"], [0])
        self.assertIsNone(budget._TYPED_RENDER)

    def test_post_reap_observation_cancellation_withholds_pair_without_cleanup_retry(self):
        interruption = KeyboardInterrupt("synthetic post-reap capture cancellation")
        with self._transport() as state, patch.object(
                route, "_finish_typed_render_observation", side_effect=interruption):
            with self.assertRaises(KeyboardInterrupt) as error:
                route.render_observed_typed_brand_pdf(**self.args)
            self.assertIs(error.exception, interruption)
            self.assertEqual(state["waits"], [30])
        self.assertIsNone(budget._TYPED_RENDER)
        with self._scope():
            pass

    def test_observed_profile_disable_and_source_postflight_withhold_pair(self):
        for case in ("disable", "profile", "source"):
            with self.subTest(case=case), ExitStack() as stack:
                def changed(process):
                    if case == "disable":
                        stack.enter_context(patch.dict(os.environ, {
                            "OMNIGENIS_TYPED_BRAND_RENDER_DISABLED": "1"}))
                    elif case == "profile":
                        stack.enter_context(patch.object(route, "require_policy", return_value="0" * 64))
                    else:
                        original = route.emission._bounded_file
                        path = Path(route.__file__).with_name("_pdf_typed_brand_render_worker.py")
                        def drifted(name, limit, **options):
                            result = original(name, limit, **options)
                            return (result[0], "0" * 64) if Path(name) == path else result
                        stack.enter_context(patch.object(route.emission, "_bounded_file", new=drifted))
                state = stack.enter_context(self._transport(after_wait=changed))
                with self.assertRaisesRegex(ValueError, "^typed_brand_render_refused$"):
                    route.render_observed_typed_brand_pdf(**self.args)
                self.assertEqual(state["waits"], [30])
            self.assertIsNone(budget._TYPED_RENDER)

    def test_original_brand_observed_source_postflight_remains_binding(self):
        arguments = self._brand_arguments()
        for case in ("source", "profile", "cancel"):
            with self.subTest(case=case):
                calls = []
                original = brand.prepare_typed_original_brand_pdf
                def prepare(**kwargs):
                    value = original(**kwargs)
                    calls.append(True)
                    if len(calls) == 2:
                        if case == "source":
                            html, resources, metadata = value
                            return html, resources, {**metadata, "html_sha256": "0" * 64}
                        if case == "cancel":
                            raise KeyboardInterrupt("synthetic source postflight cancellation")
                    return value
                def render(**kwargs):
                    return route.TypedBrandPdfCandidate(
                        self.pdf, kwargs["expected_html_sha256"],
                        kwargs["expected_resources_sha256"], 1,
                        len(kwargs["resources"].assets), self.profile), {}
                policy = brand.require_render_policy
                def checked_policy():
                    return "0" * 64 if case == "profile" and len(calls) == 2 else policy()
                with patch.object(brand, "prepare_typed_original_brand_pdf", new=prepare), patch.object(
                        brand, "render_observed_typed_brand_pdf", side_effect=render) as rendered, patch.object(
                        brand, "require_render_policy", new=checked_policy):
                    with self.assertRaises(KeyboardInterrupt if case == "cancel" else brand.TypedOriginalBrandError):
                        brand.render_observed_typed_original_brand_pdf(**arguments)
                    rendered.assert_called_once()
                    self.assertEqual(len(calls), 2)

    def test_observed_wait_preserves_custody_and_single_certified_handoff(self):
        offsets, call_offsets = route._pre_wait_offsets(route._run_owned_child.__code__)
        self.assertTrue(offsets)
        self.assertTrue(call_offsets)
        self.assertLessEqual(len(route._run_owned_child.__code__.co_code), 4096)
        phases = []
        def checked(process):
            with self.assertRaises(ValueError), self._scope():
                pass
            phases.append("settled custody remains exclusive until controller exit")
        with self._transport(after_wait=checked) as state:
            route.render_observed_typed_brand_pdf(**self.args)
            self.assertEqual(state["waits"], [30])
        self.assertEqual(len(phases), 1)
        with self._scope():
            pass

    def test_observed_direct_row_ceiling_refuses_before_export(self):
        canonical = route._canonical
        def oversized(value):
            return b" " * 2049 if "observation_sha256" in value else canonical(value)
        with self._transport() as state, patch.object(route, "_canonical", new=oversized):
            with self.assertRaisesRegex(ValueError, "^typed_brand_render_refused$"):
                route.render_observed_typed_brand_pdf(**self.args)
            self.assertEqual(state["waits"], [30])
        self.assertIsNone(budget._TYPED_RENDER)


if __name__ == "__main__":
    unittest.main()
