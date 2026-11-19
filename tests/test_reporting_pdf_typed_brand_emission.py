"""Independent synthetic pydyf structures for the opt-in typed-brand finisher."""
from types import SimpleNamespace
import unittest

import pydyf


def fixture(with_ids=True):
    pdf = pydyf.PDF()
    pages = []
    for height in (100, 200):
        stream = pydyf.Stream([f"1 0 0 -1 0 {height * 3 // 4} cm".encode(),
                               b"q", b"0.75 0 0 0.75 0 0 cm"])
        pdf.add_object(stream)
        rectangle = (0, 0, 75, height * 3 // 4)
        page = pydyf.Dictionary({"Type": "/Page", "Parent": pdf.pages.reference,
                                 "Contents": stream.reference})
        for key in ("MediaBox", "TrimBox", "BleedBox"):
            page[key] = pydyf.Array(rectangle)
        pdf.add_page(page)
        pages.append(SimpleNamespace(width=100, height=height,
                     bleed=dict.fromkeys(("left", "right", "top", "bottom"), 0)))
    pdf.info["Title"] = pydyf.String("Synthetic metadata")
    tree = pydyf.Dictionary({"Nums": pydyf.Array([0, pydyf.Array(), 1, pydyf.Array()])})
    pdf.add_object(tree)
    root = pydyf.Dictionary({"Type": "/StructTreeRoot", "ParentTree": tree.reference,
                             "K": pydyf.Array()})
    pdf.add_object(root)
    pdf.catalog["StructTreeRoot"] = root.reference

    def node(role, parent):
        obj = pydyf.Dictionary({"Type": "/StructElem", "S": role, "K": pydyf.Array(),
                                "P": parent.reference})
        if role != "/Document":
            obj["Pg"] = pdf.page_references[1]
        pdf.add_object(obj)
        parent["K"].append(obj.reference)
        return obj

    document = node("/Document", root)
    figure = node("/Figure", document)
    figure["A"] = pydyf.Dictionary({"O": "/Layout", "BBox": pydyf.Array(
        [1.000001, 2.000001, 3.000001, 4.000001])})
    tree["Nums"][3].append(figure.reference)
    headers = []
    if with_ids:
        table = node("/Table", document)
        row = node("/TR", table)
        for value in ("2", "10"):
            header = node("/TH", row)
            header["A"] = pydyf.Dictionary({"O": "/Table"})
            header["ID"] = pydyf.String(f"{table.number}-0-{value}")
            headers.append(header)
        cell = node("/TD", row)
        cell["A"] = pydyf.Dictionary({"O": "/Table", "Headers": pydyf.Array(
            [header["ID"] for header in headers])})
    return SimpleNamespace(pages=pages), pdf, root, figure, headers


class TypedBrandEmissionTests(unittest.TestCase):
    def test_fractional_box_on_second_page_and_byte_sorted_original_ids(self):
        from reporting._pdf_typed_brand_emission import emit_structure
        document, pdf, root, figure, headers = fixture()
        objects = tuple(pdf.objects)
        parent_tree = root["ParentTree"]
        parent_object = pdf.objects[int(parent_tree.split()[0])]
        parent_content = parent_object.data
        metadata = pdf.info.data
        streams = [(obj, obj.data) for obj in pdf.objects if isinstance(obj, pydyf.Stream)]
        cell_headers = pdf.objects[-1]["A"]["Headers"]
        header_objects = tuple(cell_headers)
        header_data = cell_headers.data
        emit_structure(document, pdf)
        self.assertEqual(figure["A"]["BBox"], [b"0.75000075", b"146.99999925",
                                               b"2.25000075", b"148.49999925"])
        names = pdf.objects[-1]["Names"]
        self.assertIs(names[0], headers[1]["ID"])
        self.assertIs(names[2], headers[0]["ID"])
        self.assertEqual(names[1::2], [headers[1].reference, headers[0].reference])
        self.assertEqual(root["IDTree"], pdf.objects[-1].reference)
        self.assertEqual(parent_object.data, parent_content)
        self.assertEqual(pdf.info.data, metadata)
        self.assertEqual([obj.data for obj, _ in streams], [data for _, data in streams])
        self.assertIs(objects[-1]["A"]["Headers"], cell_headers)
        self.assertEqual(cell_headers.data, header_data)
        self.assertEqual(len(cell_headers), len(header_objects))
        for actual, original in zip(cell_headers, header_objects):
            self.assertIs(actual, original)
        self.assertEqual(parent_tree, root["ParentTree"])
        self.assertTrue(all(a is b for a, b in zip(objects, pdf.objects)))
        with self.assertRaises(ValueError):
            emit_structure(document, pdf)

    def test_class_map_alias_retains_original_layout_rectangle(self):
        from reporting._pdf_typed_brand_emission import emit_structure
        document, pdf, root, figure, _ = fixture()
        shared = figure["A"]
        original_box = shared["BBox"]
        original_bytes = shared.data
        root["ClassMap"] = pydyf.Dictionary({"LogoLayout": shared})
        emit_structure(document, pdf)
        self.assertIs(root["ClassMap"]["LogoLayout"], shared)
        self.assertIs(shared["BBox"], original_box)
        self.assertEqual(shared.data, original_bytes)
        self.assertIsNot(figure["A"], shared)
        self.assertEqual(figure["A"]["BBox"][0], b"0.75000075")

    def test_parent_tree_cannot_overlap_structure_or_use_foreign_shape(self):
        from reporting._pdf_typed_brand_emission import emit_structure
        for case in ("root", "element", "extra-key", "not-array", "missing-page-pair"):
            with self.subTest(case=case):
                document, pdf, root, figure, _ = fixture()
                tree = pdf.objects[int(root["ParentTree"].split()[0])]
                if case == "root":
                    root["ParentTree"] = root.reference
                elif case == "element":
                    root["ParentTree"] = figure.reference
                elif case == "extra-key":
                    tree["Extra"] = 0
                elif case == "not-array":
                    tree["Nums"] = pydyf.Dictionary()
                else:
                    del tree["Nums"][-2:]
                before = (figure["A"].data, root.data, tree.data, len(pdf.objects))
                with self.assertRaises(ValueError):
                    emit_structure(document, pdf)
                self.assertEqual(before, (figure["A"].data, root.data, tree.data, len(pdf.objects)))
                self.assertNotIn("IDTree", root)

    def test_owner_arrays_require_bounded_indirect_structure_references(self):
        from reporting._pdf_typed_brand_emission import emit_structure
        for case in ("inline-figure", "nested-array", "wrong-reference", "foreign", "budget"):
            with self.subTest(case=case):
                document, pdf, root, figure, _ = fixture()
                tree = pdf.objects[int(root["ParentTree"].split()[0])]
                owners = tree["Nums"][3]
                if case == "inline-figure":
                    owners[0] = figure
                elif case == "nested-array":
                    owners[0] = pydyf.Array([figure.reference])
                elif case == "wrong-reference":
                    owners[0] = pdf.pages.reference
                elif case == "foreign":
                    owners[0] = b"999999 0 R"
                else:
                    tree["Nums"][1].extend([figure.reference] * 10000)
                    owners.extend([figure.reference] * 10000)
                before = (figure.data, root.data, tree.data, len(pdf.objects))
                with self.assertRaises(ValueError):
                    emit_structure(document, pdf)
                self.assertEqual(before, (figure.data, root.data, tree.data, len(pdf.objects)))

    def test_zero_ids_does_not_create_index_and_cannot_emit_twice(self):
        from reporting._pdf_typed_brand_emission import emit_structure
        document, pdf, root, figure, _ = fixture(False)
        count = len(pdf.objects)
        emit_structure(document, pdf)
        self.assertNotIn("IDTree", root)
        self.assertEqual(len(pdf.objects), count)
        with self.assertRaises(ValueError):
            emit_structure(document, pdf)

    def test_invalid_graphs_refuse_before_mutating_structure(self):
        from reporting._pdf_typed_brand_emission import emit_structure
        for case in ("cycle", "shared", "foreign", "duplicate", "missing-id", "unicode",
                     "index", "zero-figures", "wrong-page", "rotation", "bleed",
                     "reversed", "nonfinite", "traversal", "depth", "two-figures",
                     "long-id", "bad-mcid", "missing-headers", "foreign-header", "transform",
                     "owned-attribute", "shared-attribute", "wide-reference-traversal-budget"):
            with self.subTest(case=case):
                document, pdf, root, figure, headers = fixture()
                if case == "cycle":
                    figure["K"].append(figure.reference)
                elif case == "shared":
                    root["K"].append(root["K"][0])
                elif case == "foreign":
                    figure["K"].append(b"9999999 0 R")
                elif case == "duplicate":
                    headers[0]["ID"] = headers[1]["ID"]
                elif case == "missing-id":
                    del headers[1]["ID"]
                elif case == "unicode":
                    headers[1]["ID"] = pydyf.String("é")
                elif case == "index":
                    root["IDTree"] = root["ParentTree"]
                elif case == "zero-figures":
                    figure["S"] = "/Div"
                elif case == "wrong-page":
                    figure["Pg"] = root.reference
                elif case == "rotation":
                    pdf.objects[4]["Rotate"] = 90
                elif case == "bleed":
                    document.pages[0].bleed["left"] = 1
                elif case == "reversed":
                    figure["A"]["BBox"][0] = 4
                elif case == "nonfinite":
                    figure["A"]["BBox"][0] = float("inf")
                elif case == "owned-attribute":
                    pdf.add_object(figure["A"])
                elif case == "shared-attribute":
                    doc = pdf.objects[int(figure["P"].split()[0])]
                    doc["A"] = figure["A"]
                elif case == "wide-reference-traversal-budget":
                    row = pdf.objects[int(headers[0]["P"].split()[0])]
                    table_num = row["P"].split()[0].decode()
                    for i in range(10001):
                        item = pydyf.Dictionary({"Type": "/StructElem", "S": "/TH",
                            "P": row.reference, "Pg": figure["Pg"], "K": pydyf.Array(),
                            "A": pydyf.Dictionary({"O": "/Table"}),
                            "ID": pydyf.String(f"{table_num}-1-{i}")})
                        pdf.add_object(item)
                        row["K"].append(item.reference)
                elif case == "depth":
                    parent = figure
                    for _ in range(64):
                        child = pydyf.Dictionary({"Type": "/StructElem", "S": "/Div",
                            "K": pydyf.Array(), "P": parent.reference, "Pg": figure["Pg"]})
                        pdf.add_object(child)
                        parent["K"].append(child.reference)
                        parent = child
                elif case == "two-figures":
                    other = pydyf.Dictionary(figure)
                    pdf.add_object(other)
                    pdf.objects[int(figure["P"].split()[0])]["K"].append(other.reference)
                elif case == "long-id":
                    headers[1]["ID"] = pydyf.String("1" * 129)
                elif case == "bad-mcid":
                    figure["K"].append(True)
                elif case == "missing-headers":
                    del pdf.objects[-1]["A"]["Headers"]
                elif case == "foreign-header":
                    value = pdf.objects[-1]["A"]["Headers"][0]
                    pdf.objects[-1]["A"]["Headers"][0] = pydyf.String(value.string)
                elif case == "transform":
                    pdf.objects[3].stream[2] = b"1 0 0 1 0 0 cm"
                else:
                    figure["K"].extend([1] * 20000)
                box = figure["A"]["BBox"]
                count = len(pdf.objects)
                with self.assertRaises(ValueError):
                    emit_structure(document, pdf)
                if case == "wide-reference-traversal-budget":
                    self.assertNotIn("IDTree", root)
                self.assertIs(figure["A"]["BBox"], box)
                self.assertEqual(len(pdf.objects), count)


    def test_composed_finisher_validates_fonts_once_before_emission(self):
        from reporting._pdf_typed_brand_emission import _compose_finisher
        document, pdf, root, figure, _ = fixture()
        called = []
        selection = object()
        def original(owner, doc, candidate):
            self.assertIs(owner, selection)
            self.assertIs(doc, document)
            self.assertIs(candidate, pdf)
            self.assertNotIn("IDTree", root)
            called.append(True)
        wrapped = _compose_finisher(original)
        wrapped(selection, document, pdf)
        self.assertIn("IDTree", root)
        with self.assertRaises(ValueError):
            wrapped(selection, document, pdf)
        self.assertEqual(called, [True])


class TypedBrandRouteTests(unittest.TestCase):
    def setUp(self):
        from hashlib import sha256
        from unittest.mock import patch
        from reporting import pdf_typed_brand_render as route
        from reporting.pdf_raster_resources import RasterAsset, RasterBundle
        from test_reporting_pdf_raster_resources import png
        self.route = route
        raw = png()
        self.bundle = RasterBundle((RasterAsset("synthetic", raw, sha256(raw).hexdigest(),
                                                2, 2, "a" * 64, "b" * 64),))
        self.args = dict(html="synthetic", expected_html_sha256=sha256(b"synthetic").hexdigest(),
                         resources=self.bundle, expected_resources_sha256=self.bundle.bundle_sha256)
        self.enterContext(patch.dict("os.environ", {
            **dict.fromkeys(route.DISABLE, ""),
            "OMNIGENIS_PDF_QUALIFICATION_RECEIPT_MODE": "qualification-only"}))
        self.profile = route.require_policy()
        self.pdf = b"%PDF-1.7 synthetic transport fixture"
        self.record = dict(protocol_version=3, profile_id="typed-original-brand-render-v1",
            profile_sha256=self.profile, status="PASS", pdf_bytes=len(self.pdf),
            pdf_sha256=sha256(self.pdf).hexdigest(), limits={
                "memory_bytes": 1073741824, "cpu_seconds": 20, "file_bytes": 16777216,
                "open_files": 128}, html_sha256=self.args["expected_html_sha256"],
            page_count=1, resources_sha256=self.bundle.bundle_sha256, resource_count=1)

    def scope(self):
        from reporting._pdf_native_launch_budget import NativeLaunchScope
        from reporting.page_windows import plan_pdf_page_windows
        plan = plan_pdf_page_windows(pdf_sha256="a"*64, ir_sha256="b"*64,
                 page_count=1, admitted_page_budget=1, pages_per_window=16,
                 inspector_profile_id="generated-pdf-text-window-v1")
        return NativeLaunchScope(plan, len(self.pdf))

    def test_active_owner_and_foreign_thread_refuse_before_input_or_descriptors(self):
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch
        for foreign in (False, True):
            with self.subTest(foreign=foreign), self.scope() as scope, patch(
                    "tempfile.TemporaryFile") as descriptor:
                def enter():
                    with self.assertRaisesRegex(ValueError, "^typed_brand_render_refused$"):
                        self.route.render_typed_brand_pdf(**(self.args | {"html": None}))
                if foreign:
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        pool.submit(enter).result()
                else:
                    enter()
                descriptor.assert_not_called()
                self.assertTrue(scope._invalid)
                with self.assertRaises(ValueError):
                    scope.require_complete()

    def test_missing_disabled_and_drifted_manifest_refuse_before_launch(self):
        import tempfile
        from pathlib import Path
        import json
        from unittest.mock import patch
        from reporting import adapters
        from reporting import _pdf_typed_brand_emission as core
        with tempfile.TemporaryDirectory() as directory, patch.object(
                adapters, "_start_pdf_render_worker") as launch:
            path = Path(directory) / "profile.json"
            data = core.require_source_manifest()
            self.assertIn("reporting/_pdf_original_dna_png_admission.py", data["sources"])
            for case in ("absent", "source", "engine", "flag"):
                with self.subTest(case=case):
                    if case == "source":
                        data["sources"]["reporting/adapters.py"] = "0" * 64
                        path.write_text(json.dumps(data))
                    if case == "engine":
                        data = core.require_source_manifest()
                        data["engine_sources"]["weasyprint/document.py"] = "0" * 64
                        path.write_text(json.dumps(data))
                    with (patch.object(core, "MANIFEST", core.MANIFEST if case == "flag" else path),
                          patch.dict("os.environ", {"OMNIGENIS_TYPED_BRAND_RENDER_DISABLED":
                                                    "1" if case == "flag" else ""}),
                          self.assertRaisesRegex(ValueError, "^typed_brand_render_refused$")):
                        self.route.render_typed_brand_pdf(**self.args)
            launch.assert_not_called()

    def test_closed_response_exact_anonymous_output_and_retained_seal(self):
        import json
        import tempfile
        from copy import deepcopy
        with tempfile.TemporaryFile() as output:
            output.write(self.pdf)
            output.flush()
            def decode(raw):
                return self.route._decode(raw, output.fileno(), self.args["expected_html_sha256"],
                                          self.bundle.bundle_sha256, 1, self.profile)
            candidate = decode(json.dumps(self.record).encode())
            self.assertEqual(candidate.pdf_bytes, self.pdf)
            self.assertIs(candidate.to_dict()["default_pipeline_activated"], False)
            for key, value in (("profile_id", "preloaded-png-render-v1"), ("status", "SKIP"),
                               ("protocol_version", 3.0), ("pdf_sha256", "a"*64),
                               ("page_count", True), ("page_count", 65), ("pdf_bytes", 1),
                               ("html_sha256", "a"*64), ("resources_sha256", "b"*64),
                               ("resource_count", True), ("extra", True)):
                with self.subTest(field=key, value=value), self.assertRaises(ValueError):
                    decode(json.dumps(self.record | {key:value}).encode())
            invalid = deepcopy(self.record)
            invalid["limits"]["cpu_seconds"] = 20.0
            for raw in (json.dumps(invalid).encode(), b'{"x":0,"x":1}', b'{"x":NaN}',
                        b" " * 65537, b"\xff", b"{}"):
                with self.assertRaises(ValueError):
                    decode(raw)
            object.__setattr__(candidate, "_pdf", self.pdf + b"changed")
            with self.assertRaises(ValueError):
                candidate.to_dict()
        with tempfile.NamedTemporaryFile() as linked:
            linked.write(self.pdf)
            linked.flush()
            with self.assertRaises(ValueError):
                self.route._decode(json.dumps(self.record).encode(), linked.fileno(),
                    self.args["expected_html_sha256"], self.bundle.bundle_sha256, 1, self.profile)

    def test_child_failure_or_late_change_closes_all_four_descriptors(self):
        from contextlib import ExitStack
        import json
        import os
        from pathlib import Path
        from unittest.mock import patch
        from reporting import adapters
        for case in ("failed", "late-disable", "active-scope", "resources"):
            with self.subTest(case=case), ExitStack() as stack:
                fds = []
                marker = object()
                def start(command, *, pass_fds, env, before_spawn):
                    before_spawn()
                    fds.extend(pass_fds)
                    self.assertEqual(len(set(fds)), 4)
                    self.assertEqual(command[1:3], ["-I", "-B"])
                    self.assertEqual(Path(command[3]).name, "_pdf_typed_brand_render_worker.py")
                    self.assertEqual(command[-2], "native-typed-original-brand-v1")
                    self.assertEqual(env["OMNIGENIS_PDF_QUALIFICATION_RECEIPT_MODE"], "qualification-only")
                    os.pwrite(fds[1], self.pdf, 0)
                    os.pwrite(fds[2], json.dumps(self.record).encode(), 0)
                    return marker
                scope = self.scope()
                if case == "active-scope":
                    decode = self.route._decode
                    def activate_after_wait(*args):
                        stack.enter_context(scope)
                        return decode(*args)
                    stack.enter_context(patch.object(self.route, "_decode", side_effect=activate_after_wait))
                waited = []
                def wait(process, *, wall_seconds, cleanup_observer):
                    waited.append(process)
                    self.assertIs(process, marker)
                    self.assertEqual(wall_seconds, 30)
                    if case == "late-disable":
                        stack.enter_context(patch.dict("os.environ", {"OMNIGENIS_TYPED_BRAND_RENDER_DISABLED":"1"}))
                    elif case == "resources":
                        original = self.bundle.assets
                        stack.callback(object.__setattr__, self.bundle, "assets", original)
                        object.__setattr__(self.bundle, "assets", ())
                    status = 1 if case == "failed" else 0
                    cleanup_observer(status)
                    return status
                stack.enter_context(patch.object(adapters, "_start_pdf_render_worker", side_effect=start))
                stack.enter_context(patch.object(adapters, "_wait_pdf_render_worker", new=wait))
                with self.assertRaisesRegex(ValueError, "^typed_brand_render_refused$"):
                    self.route.render_typed_brand_pdf(**self.args)
                self.assertEqual(waited, [marker])
                if case == "active-scope":
                    self.assertTrue(scope._invalid)
                self.assertEqual(len(fds), 4)
                for fd in fds:
                    with self.assertRaises(OSError):
                        os.fstat(fd)

    def test_disable_or_mode_change_during_profile_hash_refuses(self):
        import os
        from unittest.mock import patch
        from reporting import _pdf_typed_brand_emission as core
        for key in (*self.route.DISABLE, "OMNIGENIS_PDF_QUALIFICATION_RECEIPT_MODE"):
            with self.subTest(key=key), patch.dict(os.environ):
                def changed():
                    os.environ[key] = "1"
                    return self.profile
                with patch.object(core, "profile_sha256", side_effect=changed), self.assertRaises(ValueError):
                    self.route.require_policy()

    def test_typed_wait_excludes_native_activation(self):
        import threading
        from unittest.mock import patch
        from reporting import adapters
        observed = []
        def check(phase):
            results = []
            def challenger():
                scope = self.scope()
                try:
                    with scope:
                        pass
                except ValueError:
                    results.append(scope._invalid)
                else:
                    results.append(False)
            thread = threading.Thread(target=challenger, daemon=True)
            thread.start()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(results, [True])
            observed.append(phase)
        def start(*a, **k):
            k["before_spawn"]()
            check("launch")
            return object()
        def wait(process, *, wall_seconds, cleanup_observer):
            check("owned wait and cleanup")
            cleanup_observer(1)
            return 1
        with (patch.object(adapters, "_start_pdf_render_worker", side_effect=start),
              patch.object(adapters, "_wait_pdf_render_worker", new=wait),
              self.assertRaises(ValueError)):
            self.route.render_typed_brand_pdf(**self.args)
        self.assertEqual(observed, ["launch", "owned wait and cleanup"])
        with self.scope():
            pass

    def test_profile_sources_use_bounded_descriptor_reads(self):
        from pathlib import Path
        from unittest.mock import patch
        from reporting import _pdf_typed_brand_emission as core
        with patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded path read")):
            self.assertEqual(core.profile_sha256(), self.profile)

    def test_bounded_file_refuses_nonregular_oversized_and_changed_descriptors(self):
        import os
        from pathlib import Path
        import tempfile
        from unittest.mock import patch
        from reporting import _pdf_typed_brand_emission as core
        real_open, real_read = os.open, os.read
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile"
            for case in ("fifo", "oversized", "grow", "truncate", "replace", "symlink"):
                with self.subTest(case=case):
                    if case == "fifo":
                        os.mkfifo(path)
                    elif case == "symlink":
                        path.symlink_to(Path(tmp) / "unavailable")
                    else:
                        path.write_bytes(b"abcde" if case == "oversized" else b"abcd")
                    captured, sizes = [], []
                    def open_regular(name, flags):
                        required = os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
                        self.assertEqual(flags & required, required)
                        fd = real_open(name, flags)
                        captured.append(fd)
                        return fd
                    def read(fd, size):
                        sizes.append(size)
                        if case == "grow":
                            with path.open("ab") as changed:
                                changed.write(b"e")
                        elif case == "truncate":
                            with path.open("wb"):
                                pass
                        elif case == "replace":
                            path.unlink()
                            path.write_bytes(b"abcd")
                        else:
                            self.fail("read crossed initial type or size refusal")
                        return real_read(fd, size)
                    with (patch.object(os, "open", side_effect=open_regular),
                          patch.object(os, "read", side_effect=read),
                          self.assertRaises((OSError, ValueError))):
                        core._bounded_file(path, 4, retain=True)
                    self.assertLessEqual(sum(sizes), 4)
                    for fd in captured:
                        with self.assertRaises(OSError):
                            os.fstat(fd)
                    path.unlink()

    def test_profiles_bind_each_file_category_to_its_descriptor_budget(self):
        from unittest.mock import patch
        from reporting import _pdf_typed_brand_emission as core
        with patch.object(core, "_bounded_file", wraps=core._bounded_file) as bounded:
            self.assertEqual(core.profile_sha256(), self.profile)
        calls = bounded.call_args_list
        self.assertEqual(calls[0].args, (core.MANIFEST, 16384))
        self.assertEqual(calls[0].kwargs, {"retain": True})
        self.assertEqual([call.args for call in calls[1:21]],
                         [(core.ROOT / name, 65536) for name in core.SOURCES])
        self.assertEqual(len(calls[21:]), 4)
        for name, call in zip(core.ENGINES, calls[21:]):
            self.assertTrue(str(call.args[0]).endswith(name))
            self.assertEqual(call.args[1], 65536)

    def test_actual_worker_entry_orders_audit_and_finisher_after_startup_guards(self):
        import json
        from pathlib import Path
        import subprocess
        import sys
        root = Path(self.route.__file__).resolve().parents[1]
        # Run module-global child adaptations only in a disposable isolated interpreter.
        program = """
import json, os, runpy, sys, tempfile
from contextlib import ExitStack
from pathlib import Path
sys.path.insert(0, sys.argv[1])
case = sys.argv[2]
from reporting import _pdf_render_worker as base, _pdf_process_sandbox as sandbox
from reporting import _pdf_typed_brand_emission as core, pdf_typed_brand_render as route
from reporting import pdf_raster_render as inherited
events = []
def stage(label):
    def run(*a, **k):
        events.append(label)
        if label == case or label == "request":
            raise ValueError("fixture stop")
        return {}
    return run
base._apply_resource_limits = stage("limits")
base._arm_worker_lifetime = stage("lifetime")
sandbox.enforce_pdf_landlock = stage("landlock")
sandbox.enforce_pdf_network_filter = stage("network")
route.require_policy = stage("audit")
core.install_in_isolated_render_worker = stage("finisher")
inherited.read_raster_inputs = stage("request")
base._failure = lambda *a, **k: 1
with ExitStack() as stack:
    fds = [stack.enter_context(tempfile.TemporaryFile()).fileno() for _ in range(4)]
    if case == "descriptor":
        fds[1] = fds[0]
    script = Path(core.__file__).with_name("_pdf_typed_brand_render_worker.py")
    sys.argv = [str(script), *map(str, fds[:3]), str(os.getpid()), core.MODE, str(fds[3])]
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit:
        pass
print(json.dumps(events))
"""
        order = ["limits", "lifetime", "landlock", "network", "audit", "finisher", "request"]
        for case in ("descriptor", "network", "audit", "request"):
            with self.subTest(case=case):
                completed = subprocess.run([sys.executable, "-I", "-B", "-c", program,
                                            str(root), case], capture_output=True,
                                           check=True, text=True, timeout=10)
                expected = [] if case == "descriptor" else order[:order.index(case) + 1]
                self.assertEqual(json.loads(completed.stdout), expected)

    def test_postflight_hash_disable_withholds_result_after_settled_child(self):
        from contextlib import ExitStack
        import os
        from unittest.mock import patch
        from reporting import adapters, _pdf_typed_brand_emission as core
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ))
            def stale():
                os.environ["OMNIGENIS_TYPED_BRAND_RENDER_DISABLED"] = "1"
                return self.profile
            def decode(*args):
                stack.enter_context(patch.object(core, "profile_sha256", side_effect=stale))
                return object()
            stack.enter_context(patch.object(self.route, "_decode", side_effect=decode))
            def launched(*args, **kwargs):
                kwargs["before_spawn"]()
                return object()
            start = stack.enter_context(patch.object(adapters, "_start_pdf_render_worker", side_effect=launched))
            waits = []
            def wait(process, *, wall_seconds, cleanup_observer):
                waits.append(wall_seconds)
                cleanup_observer(0)
                return 0
            stack.enter_context(patch.object(adapters, "_wait_pdf_render_worker", new=wait))
            with self.assertRaisesRegex(ValueError, "^typed_brand_render_refused$"):
                self.route.render_typed_brand_pdf(**self.args)
            start.assert_called_once()
            self.assertEqual(waits, [30])
            with self.scope():
                pass

    def test_ambiguous_launch_wait_and_handoff_preserve_custody(self):
        from contextlib import ExitStack
        import dis
        import inspect
        import sys
        from unittest.mock import patch
        from reporting import adapters, _pdf_native_launch_budget as budget
        fn = self.route._run_owned_child
        lines, first = inspect.getsourcelines(fn)
        handoff = first + next(i for i, line in enumerate(lines)
                               if line.strip() == "status = waiter(process, wall_seconds=wall, cleanup_observer=observer)")
        site = {op.opname: op.offset for op in dis.get_instructions(fn)
                if op.positions.lineno == handoff}
        for case in ("uncertain-start", "no-handle", "wait-error", "wait-noninteger",
                     "handoff-settled", "handoff-unsettled", "call-boundary",
                     "post-return", "classifier-failure"):
            with self.subTest(case=case), ExitStack() as stack:
                self.assertIsNone(budget._TYPED_RENDER)
                stack.enter_context(patch.object(budget, "_TYPED_RENDER", None))
                handle = SimpleNamespace(returncode=None)
                calls = []
                interruption = KeyboardInterrupt("interrupted known handle")
                def start(*a, **k):
                    k["before_spawn"]()
                    if case == "uncertain-start":
                        raise KeyboardInterrupt("uncertain launch")
                    return None if case == "no-handle" else handle
                def wait(child, *, wall_seconds, cleanup_observer):
                    self.assertIs(child, handle)
                    calls.append(wall_seconds)
                    if case in ("wait-error", "handoff-unsettled"):
                        raise RuntimeError("cleanup unconfirmed")
                    if case == "wait-noninteger":
                        cleanup_observer(True)
                        return True
                    cleanup_observer(0)
                    return 0
                stack.enter_context(patch.object(adapters, "_start_pdf_render_worker", side_effect=start))
                stack.enter_context(patch.object(adapters, "_wait_pdf_render_worker", new=wait))
                if case == "classifier-failure":
                    stack.enter_context(patch.object(self.route, "_confirmed_pre_wait", side_effect=SystemExit))
                injected = case.startswith("handoff") or case in ("call-boundary", "post-return", "classifier-failure")
                if injected:
                    def interrupt(frame, event, arg):
                        if frame.f_code is fn.__code__:
                            frame.f_trace_opcodes = case in ("call-boundary", "post-return")
                            if frame.f_trace_opcodes:
                                trap = site["CALL"] if case == "call-boundary" else site["STORE_FAST"]
                                if event == "opcode" and frame.f_lasti == trap:
                                    raise interruption
                            elif event == "line" and frame.f_lineno == handoff:
                                raise interruption
                        return interrupt
                    # CPython 3.12 enables opcode tracing when a frame requests
                    # opcode events before the trace function is installed.
                    test_frame = sys._getframe()
                    stack.callback(setattr, test_frame, "f_trace_opcodes", test_frame.f_trace_opcodes)
                    test_frame.f_trace_opcodes = case in ("call-boundary", "post-return")
                    stack.callback(sys.settrace, sys.gettrace())
                    sys.settrace(interrupt)
                expected_error = (KeyboardInterrupt if injected or case == "uncertain-start" else
                                  ValueError if case in ("wait-noninteger", "no-handle") else RuntimeError)
                with self.assertRaises(expected_error) as error:
                    fn([], {}, ())
                if injected:
                    self.assertIs(error.exception, interruption)
                expected_waits = ([0] if case.startswith("handoff") else
                                  [30] if case.startswith("wait-") or case == "post-return" else [])
                self.assertEqual(calls, expected_waits)
                if case in ("handoff-settled", "post-return"):
                    with self.scope():
                        pass
                else:
                    self.assertEqual(budget._TYPED_RENDER.phase, "quarantined")
                    with self.assertRaises(ValueError), self.scope():
                        pass
                    with self.assertRaises(ValueError), budget.reserve_standalone_typed_render():
                        pass

    def test_native_activation_during_final_policy_refuses_before_spawn(self):
        from contextlib import ExitStack
        from unittest.mock import patch
        from reporting import adapters
        from test_reporting_pdf_native_launch_budget import launch_one, launch_specs
        from reporting._pdf_native_launch_budget import NativeLaunchScope
        from reporting.page_windows import plan_pdf_page_windows
        from hashlib import sha256
        plan = plan_pdf_page_windows(pdf_sha256=sha256(self.pdf).hexdigest(), ir_sha256="b"*64,
            page_count=1, admitted_page_budget=1, pages_per_window=16,
            inspector_profile_id="generated-pdf-text-window-v1")
        scope = NativeLaunchScope(plan, len(self.pdf))
        with ExitStack() as stack:
            active = []
            policy = self.route.require_policy
            def enter_before_reserve():
                digest = policy()
                if active:
                    stack.enter_context(scope)
                active.append(True)
                return digest
            stack.enter_context(patch.object(self.route, "require_policy", side_effect=enter_before_reserve))
            start = stack.enter_context(patch.object(adapters, "_start_pdf_render_worker"))
            with self.assertRaisesRegex(ValueError, "^typed_brand_render_refused$"):
                self.route.render_typed_brand_pdf(**self.args)
            self.assertEqual(len(active), 2)
            start.assert_not_called()
            with self.assertRaises(ValueError):
                launch_one(launch_specs(plan)[0], self.pdf)

    def test_uncertified_wait_shape_refuses_before_launch(self):
        from unittest.mock import patch
        from reporting import adapters
        with (patch.object(self.route.dis, "get_instructions", return_value=()),
              patch.object(adapters, "_start_pdf_render_worker") as start):
            with self.assertRaises(ValueError):
                self.route._run_owned_child([], {}, ())
            start.assert_not_called()
            with self.scope():
                pass

    def test_uncertain_traceback_evidence_never_authorizes_cleanup(self):
        controller, waiter, other = object(), object(), object()
        def traceback_record(code, offset, following=None):
            return SimpleNamespace(tb_frame=SimpleNamespace(f_code=code), tb_lasti=offset, tb_next=following)
        pre = traceback_record(controller, 24)
        entered = traceback_record(waiter, 0)
        deep = pre
        for _ in range(32):
            deep = traceback_record(other, 24, deep)
        for tb in (None, traceback_record(other, 24), traceback_record(controller, 26),
                   traceback_record(controller, 24, pre), traceback_record(controller, 24, entered), deep):
            with self.subTest(traceback=tb):
                error = SimpleNamespace(__traceback__=tb)
                self.assertFalse(self.route._confirmed_pre_wait(error, controller, waiter, {24}))


    def test_child_audit_failure_writes_bounded_fail_without_profile_reaudit(self):
        import json
        from pathlib import Path
        import subprocess
        import sys
        root = Path(self.route.__file__).resolve().parents[1]
        program = """
import json, os, runpy, sys, tempfile
from contextlib import ExitStack
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from reporting import _pdf_render_worker as base, _pdf_process_sandbox as sandbox
from reporting import _pdf_typed_brand_emission as core, pdf_typed_brand_render as route
from reporting import pdf_raster_render as inherited
events = []
limits = {"memory_bytes":1073741824,"cpu_seconds":20,"file_bytes":16777216,"open_files":128}
def stage(name, result=None):
    def run(*args, **kwargs):
        events.append(name)
        return result
    return run
base._apply_resource_limits = stage("limits", limits)
base._arm_worker_lifetime = stage("lifetime")
sandbox.enforce_pdf_landlock = stage("landlock")
sandbox.enforce_pdf_network_filter = stage("network")
def forbidden(*args, **kwargs):
    raise AssertionError("failure repeated an audit or crossed input admission")
scenario = sys.argv[2]
audit = route.require_policy
def checked_audit():
    events.append("audit")
    if scenario == "audit":
        raise ValueError("PRIVATE source drift detail")
    digest = audit()
    core.profile_sha256 = forbidden
    return digest
def failed_reader(*args, **kwargs):
    events.append("reader")
    raise ValueError("PRIVATE input detail")
route.require_policy = checked_audit
base.native_render_profile_sha256 = forbidden
if scenario == "audit":
    core.profile_sha256 = forbidden
inherited.read_raster_inputs = failed_reader
core.install_in_isolated_render_worker = stage("finisher")
base._render = forbidden
with ExitStack() as stack:
    files = [stack.enter_context(tempfile.TemporaryFile()) for _ in range(4)]
    fds = [file.fileno() for file in files]
    script = Path(core.__file__).with_name("_pdf_typed_brand_render_worker.py")
    sys.argv = [str(script), *map(str, fds[:3]), str(os.getpid()), core.MODE, str(fds[3])]
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as stopped:
        code = stopped.code
    raw = os.pread(fds[2], 65537, 0)
    print(json.dumps({"code":code,"record":json.loads(raw),"bytes":len(raw),
                      "pdf_bytes":os.fstat(fds[1]).st_size,"events":events}))
"""
        import tempfile
        for scenario in ("audit", "reader"):
            with self.subTest(scenario=scenario):
                result = subprocess.run([sys.executable, "-I", "-B", "-c", program, str(root), scenario],
                                        capture_output=True, text=True, check=True, timeout=10)
                observed = json.loads(result.stdout)
                self.assertEqual(observed["code"], 0)
                stages = ["limits", "lifetime", "landlock", "network", "audit"]
                if scenario == "reader":
                    stages += ["finisher", "reader"]
                self.assertEqual(observed["events"], stages)
                self.assertEqual(observed["pdf_bytes"], 0)
                self.assertLessEqual(observed["bytes"], 65536)
                expected = {"protocol_version": 1, "status": "FAIL",
                    "error": "pdf_render_protocol_invalid",
                    "limits": {"memory_bytes": 1073741824, "cpu_seconds": 20,
                               "file_bytes": 16777216, "open_files": 128}}
                if scenario == "reader":
                    expected.update(protocol_version=3, profile_id="typed-original-brand-render-v1",
                                    profile_sha256=self.profile)
                self.assertEqual(observed["record"], expected)
                self.assertNotIn("PRIVATE", result.stdout)
                with tempfile.TemporaryFile() as output, self.assertRaises(ValueError):
                    self.route._decode(json.dumps(observed["record"]).encode(), output.fileno(),
                        self.args["expected_html_sha256"], self.bundle.bundle_sha256, 1, self.profile)

    def test_prelaunch_preparation_failure_releases_reserved_custody(self):
        import errno
        import os
        from contextlib import ExitStack
        from unittest.mock import patch
        from reporting import adapters, _pdf_native_launch_budget as budget
        self.enterContext(patch.object(budget, "_TYPED_RENDER", None))
        failed = OSError(errno.EMFILE, "descriptor capacity")
        for stage in ("stdio", "cwd"):
            with self.subTest(stage=stage), ExitStack() as stack:
                if stage == "stdio":
                    preparation = stack.enter_context(patch("builtins.open", side_effect=failed))
                else:
                    preparation = stack.enter_context(patch.object(adapters.Path, "resolve", side_effect=failed))
                spawn = stack.enter_context(patch.object(adapters.subprocess, "Popen", side_effect=failed))
                with self.assertRaises(OSError) as caught:
                    self.route._run_owned_child([], {}, ())
                self.assertIs(caught.exception, failed)
                if stage == "stdio":
                    preparation.assert_called_once_with(os.devnull, "r+b", buffering=0)
                else:
                    preparation.assert_called_once_with()
                spawn.assert_not_called()
            self.assertIsNone(budget._TYPED_RENDER)
            with self.scope():
                pass
            with budget.reserve_standalone_typed_render():
                pass

    def test_confirmed_timeout_cleanup_releases_custody_without_a_result(self):
        import subprocess
        from unittest.mock import patch
        from reporting import adapters, _pdf_native_launch_budget as budget
        from test_reporting_pdf_process_lifecycle import real_child
        self.enterContext(patch.object(budget, "_TYPED_RENDER", None))
        original = adapters._wait_pdf_render_worker
        with real_child("import time; time.sleep(30)") as child:
            def launch(*args, **kwargs):
                if "before_spawn" in kwargs:
                    kwargs["before_spawn"]()
                return child
            def wait(process, *, wall_seconds, cleanup_observer=None):
                options = {} if cleanup_observer is None else {"cleanup_observer": cleanup_observer}
                return original(process, wall_seconds=0.02, **options)
            with (patch.object(adapters, "_start_pdf_render_worker", side_effect=launch),
                  patch.object(adapters, "_wait_pdf_render_worker", new=wait),
                  self.assertRaisesRegex(RuntimeError, "^pdf_render_failed$") as caught):
                self.route._run_owned_child([], {}, ())
            self.assertIsInstance(caught.exception.__cause__, subprocess.TimeoutExpired)
            self.assertEqual(child.returncode, -9)
        self.assertIsNone(budget._TYPED_RENDER)
        with self.scope():
            pass

    def test_proved_pre_wait_timeout_cleanup_preserves_original_interruption(self):
        import inspect
        import sys
        from contextlib import ExitStack
        from unittest.mock import patch
        from reporting import adapters, _pdf_native_launch_budget as budget
        from test_reporting_pdf_process_lifecycle import real_child
        self.enterContext(patch.object(budget, "_TYPED_RENDER", None))
        fn = self.route._run_owned_child
        lines, first = inspect.getsourcelines(fn)
        handoff = first + next(i for i, line in enumerate(lines)
            if line.strip().startswith("status = waiter(process, wall_seconds=wall"))
        interruption = KeyboardInterrupt("known pre-wait boundary")
        original = adapters._wait_pdf_render_worker
        calls = []
        with real_child("import time; time.sleep(30)") as child, ExitStack() as stack:
            def launch(*args, **kwargs):
                if "before_spawn" in kwargs:
                    kwargs["before_spawn"]()
                return child
            def wait(process, *, wall_seconds, cleanup_observer=None):
                calls.append(wall_seconds)
                options = {} if cleanup_observer is None else {"cleanup_observer": cleanup_observer}
                return original(process, wall_seconds=wall_seconds, **options)
            def interrupt(frame, event, arg):
                if frame.f_code is fn.__code__ and event == "line" and frame.f_lineno == handoff:
                    raise interruption
                return interrupt
            stack.enter_context(patch.object(adapters, "_start_pdf_render_worker", side_effect=launch))
            stack.enter_context(patch.object(adapters, "_wait_pdf_render_worker", new=wait))
            stack.callback(sys.settrace, sys.gettrace())
            sys.settrace(interrupt)
            with self.assertRaises(KeyboardInterrupt) as caught:
                fn([], {}, ())
            self.assertIs(caught.exception, interruption)
            self.assertEqual(calls, [0])
            self.assertEqual(child.returncode, -9)
        self.assertIsNone(budget._TYPED_RENDER)
        with self.scope():
            pass

    def test_failed_group_cleanup_keeps_reaped_child_custody_quarantined(self):
        from unittest.mock import patch
        from reporting import adapters, _pdf_native_launch_budget as budget
        from test_reporting_pdf_process_lifecycle import real_child, signal_observer
        original = adapters._wait_pdf_render_worker
        with patch.object(budget, "_TYPED_RENDER", None):
            with real_child("import time; time.sleep(30)") as child:
                def launch(*args, **kwargs):
                    if "before_spawn" in kwargs:
                        kwargs["before_spawn"]()
                    return child
                def wait(process, *, wall_seconds, cleanup_observer=None):
                    options = {} if cleanup_observer is None else {"cleanup_observer": cleanup_observer}
                    return original(process, wall_seconds=0.02, **options)
                with (patch.object(adapters, "_start_pdf_render_worker", side_effect=launch),
                      patch.object(adapters, "_wait_pdf_render_worker", new=wait),
                      signal_observer(child, [], group_error=PermissionError("group refused")),
                      self.assertRaises(RuntimeError)):
                    self.route._run_owned_child([], {}, ())
                self.assertEqual(child.returncode, -9)
            self.assertEqual(budget._TYPED_RENDER.phase, "quarantined")
            with self.assertRaises(ValueError), self.scope():
                pass

    def test_integer_wait_without_cleanup_receipt_cannot_return_a_result(self):
        from unittest.mock import patch
        from reporting import adapters, _pdf_native_launch_budget as budget
        def launch(*args, **kwargs):
            if "before_spawn" in kwargs:
                kwargs["before_spawn"]()
            return object()
        def wait(process, *, wall_seconds, cleanup_observer=None):
            return 0
        with patch.object(budget, "_TYPED_RENDER", None):
            with (patch.object(adapters, "_start_pdf_render_worker", side_effect=launch),
                  patch.object(adapters, "_wait_pdf_render_worker", new=wait),
                  self.assertRaises(ValueError)):
                self.route._run_owned_child([], {}, ())
            self.assertEqual(budget._TYPED_RENDER.phase, "quarantined")
