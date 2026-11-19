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
                     "owned-attribute", "shared-attribute", "id-budget"):
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
                elif case == "id-budget":
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
                def start(command, *, pass_fds, env):
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
                def wait(process, *, wall_seconds):
                    self.assertIs(process, marker)
                    self.assertEqual(wall_seconds, 30)
                    if case == "late-disable":
                        stack.enter_context(patch.dict("os.environ", {"OMNIGENIS_TYPED_BRAND_RENDER_DISABLED":"1"}))
                    elif case == "active-scope":
                        stack.enter_context(scope)
                    elif case == "resources":
                        original = self.bundle.assets
                        stack.callback(object.__setattr__, self.bundle, "assets", original)
                        object.__setattr__(self.bundle, "assets", ())
                    return 1 if case == "failed" else 0
                stack.enter_context(patch.object(adapters, "_start_pdf_render_worker", side_effect=start))
                waited = stack.enter_context(patch.object(adapters, "_wait_pdf_render_worker", side_effect=wait))
                with self.assertRaisesRegex(ValueError, "^typed_brand_render_refused$"):
                    self.route.render_typed_brand_pdf(**self.args)
                waited.assert_called_once()
                if case == "active-scope":
                    self.assertTrue(scope._invalid)
                self.assertEqual(len(fds), 4)
                for fd in fds:
                    with self.assertRaises(OSError):
                        os.fstat(fd)
