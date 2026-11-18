"""Same-artifact combined typed/original-brand QA behavior, using synthetic inputs."""
from __future__ import annotations

from copy import deepcopy
from contextlib import ExitStack
from hashlib import sha256
from io import BytesIO
import importlib
import importlib.util
import json
import os
import re
from pathlib import Path
import unittest
import tempfile
from types import SimpleNamespace
import zlib
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, ContentStream, DecodedStreamObject, DictionaryObject,
                           FloatObject, NameObject, NumberObject, TextStringObject)
from reporting.brand_assets import BoundBrandAssets
from reporting.typed_original_brand_pdf import render_typed_original_brand_pdf
from reporting.typed_pdf_source import prepare_typed_pdf_source
from test_reporting_typed_pdf_source import document, digest

ROOT = Path(__file__).resolve().parents[1]
FLAGS = {
    "OMNIGENIS_PDF_QUALIFICATION_RECEIPT_MODE": "qualification-only",
    "OMNIGENIS_TYPED_ORIGINAL_BRAND_QA_DISABLED": "",
    "OMNIGENIS_TYPED_ORIGINAL_BRAND_PDF_DISABLED": "",
    "OMNIGENIS_TYPED_EDITORIAL_PDF_DISABLED": "",
    "OMNIGENIS_PDF_PRELOADED_RASTER_DISABLED": "",
    "OMNIGENIS_PDF_QUALIFICATION_RECEIPT_DISABLED": "",
    "OMNIGENIS_PDF_RENDERER_DISABLED": "",
    "OMNIGENIS_TYPED_PDF_OBSERVATION_DISABLED": "",
    "OMNIGENIS_PDF_RASTER_WINDOWS_DISABLED": "",
}


class TypedOriginalBrandQATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        asset = ROOT / "reporting/assets"
        cls.assets = BoundBrandAssets(
            logo_svg=(asset / "omnigenis_logo.svg").read_bytes(),
            dna_png=(asset / "dna_column.png").read_bytes(),
            permission_notice=(asset / "brand-permission.md").read_bytes())
        cls.bundle = cls.assets.to_dict()["bundle_sha256"]
        cls.fixtures = {}

    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("reporting.typed_original_brand_qa"),
                             "combined typed/original-brand same-PDF QA is missing")
        self.m = importlib.import_module("reporting.typed_original_brand_qa")
        mode = patch.dict(os.environ, FLAGS)
        mode.start()
        self.addCleanup(mode.stop)

    def fixture(self, count=2):
        if count not in self.fixtures:
            source = document(count)
            rendered, _ = render_typed_original_brand_pdf(
                presentation_ir=source, expected_ir_sha256=digest(source),
                assets=self.assets, expected_brand_bundle_sha256=self.bundle)
            self.fixtures[count] = source, rendered.pdf_bytes
        source, pdf = self.fixtures[count]
        return deepcopy(source), pdf

    def arguments(self, count=2):
        source, pdf = self.fixture(count)
        expectation = prepare_typed_pdf_source(
            presentation_ir=source, expected_ir_sha256=digest(source))
        return dict(
            presentation_ir=source, expected_ir_sha256=digest(source),
            expected_source_expectation_sha256=expectation.expectation_sha256,
            assets=self.assets, expected_brand_bundle_sha256=self.bundle,
            pdf_bytes=pdf, expected_pdf_sha256=sha256(pdf).hexdigest(),
            expected_profile_sha256=self.m.profile_sha256())

    def run_qa(self, args=None):
        return self.m.validate_typed_original_brand_candidate(
            **(self.arguments() if args is None else args))

    def test_full_multipage_source_brand_and_every_page_bind_to_exact_original_bytes(self):
        args = self.arguments(96)
        before = bytes(args["pdf_bytes"])
        result = self.run_qa(args).to_dict()
        self.assertEqual(result["status"], "TYPED_ORIGINAL_BRAND_CANDIDATE_INSPECTED")
        self.assertEqual(result["pdf_sha256"], sha256(before).hexdigest())
        self.assertEqual(args["pdf_bytes"], before)
        self.assertEqual(result["ir_sha256"], args["expected_ir_sha256"])
        self.assertEqual(result["represented_findings"], 96)
        self.assertEqual(result["source_units"], 198)
        self.assertEqual(result["brand_text_units"], 3)
        self.assertEqual(result["figure_count"], 1)
        self.assertEqual(result["dna_image_count"], 1)
        self.assertEqual(result["original_credit_count"], 1)
        self.assertGreaterEqual(result["page_count"], 3)
        self.assertEqual(result["raster_inspected_pages"], result["page_count"])
        self.assertEqual(set(result["stages"].values()), {"PASS"})
        self.assertTrue(result["complete_literal_source_accounting"])
        self.assertTrue(result["complete_native_structure_accounting"])
        for key in ("visual_semantic_parity_verified", "human_assistive_accessibility_verified",
                    "whole_job_resources_qualified", "pdf_ua_certified",
                    "source_asset_bytes_authenticated_from_pdf", "release_authorized"):
            self.assertIs(result[key], False)

    def refuse(self, args):
        with self.assertRaisesRegex(self.m.TypedOriginalBrandQAError,
                                    "^typed_original_brand_qa_refused$"):
            self.run_qa(args)

    def rewritten(self, mutate=lambda writer: None):
        args = self.arguments()
        writer = PdfWriter(clone_from=PdfReader(BytesIO(args["pdf_bytes"]), strict=True))
        writer.pdf_header = b"%PDF-1.7"
        mutate(writer)
        output = BytesIO()
        writer.write(output)
        args["pdf_bytes"] = output.getvalue()
        args["expected_pdf_sha256"] = sha256(args["pdf_bytes"]).hexdigest()
        return args

    @staticmethod
    def nodes(writer):
        result = []
        def visit(value):
            value = value.get_object()
            if isinstance(value, DictionaryObject):
                if value.get("/Type") == "/StructElem":
                    result.append(value)
                if "/K" in value:
                    visit(value.raw_get("/K"))
            elif isinstance(value, ArrayObject):
                for child in value:
                    visit(child)
        visit(writer._root_object.raw_get("/StructTreeRoot"))
        return result

    @staticmethod
    def edit_page(writer, index, mutate):
        page = writer.pages[index]
        content = ContentStream(page.raw_get("/Contents"), writer, forced_encoding="bytes")
        mutate(content.operations)
        replacement = DecodedStreamObject()
        replacement.set_data(content.get_data())
        page[NameObject("/Contents")] = writer._add_object(replacement)

    def native_record(self):
        if not hasattr(self, "_native_record"):
            args = self.arguments()
            raw = self.m._run_worker(args["pdf_bytes"], args["expected_pdf_sha256"])
            self._native_record = self.m.prior._loads(raw.decode("ascii"))
        return deepcopy(self._native_record)

    def test_valid_rewritten_control_and_receipt_remain_immutable_and_scoped(self):
        args = self.rewritten()
        receipt = self.run_qa(args)
        result = receipt.to_dict()
        self.assertEqual(result["pdf_sha256"], args["expected_pdf_sha256"])
        self.assertEqual(result["source_units"], 10)
        self.assertEqual(result["represented_findings"], 2)
        self.assertEqual(result["raster_inspected_pages"], result["page_count"])
        result["stages"]["source_identity"] = "FAIL"
        self.assertEqual(receipt.to_dict()["stages"]["source_identity"], "PASS")
        for key in self.m.NONCLAIMS:
            self.assertIs(receipt.to_dict()[key], False)
            bad = receipt.to_dict()
            bad[key] = True
            with self.subTest(claim=key), self.assertRaises(Exception):
                self.m.TypedOriginalBrandQA(self.m.canonical(bad))
        bad = receipt.to_dict()
        bad["status"] = "FINAL"
        with self.assertRaises(Exception):
            self.m.TypedOriginalBrandQA(self.m.canonical(bad))

    def test_wrong_identity_and_disabled_mode_refuse_before_native_launch(self):
        for name in ("expected_ir_sha256", "expected_source_expectation_sha256",
                     "expected_brand_bundle_sha256", "expected_pdf_sha256",
                     "expected_profile_sha256"):
            args = self.arguments()
            args[name] = "0" * 64
            with self.subTest(identity=name), patch.object(self.m, "_run_worker") as launch:
                self.refuse(args)
                launch.assert_not_called()
        for name, value in (("OMNIGENIS_PDF_QUALIFICATION_RECEIPT_MODE", ""),
                            ("OMNIGENIS_TYPED_ORIGINAL_BRAND_QA_DISABLED", "1")):
            with self.subTest(flag=name), patch.dict(os.environ, {name: value}), \
                    patch.object(self.m, "_run_worker") as launch:
                self.refuse(self.arguments())
                launch.assert_not_called()

    def test_freshly_sealed_changed_literal_and_missing_or_extra_findings_refuse(self):
        for source in (document(2, paragraph="SYNTHETIC-SENTINEL-REJECT"),
                       document(1), document(3)):
            args = self.arguments()
            args["presentation_ir"] = source
            args["expected_ir_sha256"] = digest(source)
            args["expected_source_expectation_sha256"] = prepare_typed_pdf_source(
                presentation_ir=source, expected_ir_sha256=digest(source)).expectation_sha256
            with self.subTest(source_sha256=args["expected_ir_sha256"]):
                self.refuse(args)

    def test_native_table_owner_and_figure_attribute_surface_refuse(self):
        def wrong_header(writer):
            cell = next(n for n in self.nodes(writer) if n["/S"] == "/TD")
            cell["/A"][NameObject("/Headers")] = ArrayObject([TextStringObject("foreign-header")])
        def duplicate_owner(writer):
            nums = writer._root_object["/StructTreeRoot"]["/ParentTree"]["/Nums"]
            owners = nums[1]
            owners[-1] = owners[0]
        def action_attribute(writer):
            figure = next(n for n in self.nodes(writer) if n["/S"] == "/Figure")
            figure["/A"][NameObject("/S")] = NameObject("/JavaScript")
        def catalog_action(writer):
            writer._root_object[NameObject("/OpenAction")] = DictionaryObject({
                NameObject("/S"): NameObject("/JavaScript"),
                NameObject("/JS"): TextStringObject("SYNTHETIC-SENTINEL-REJECT")})
        for mutate in (wrong_header, duplicate_owner, action_attribute, catalog_action):
            with self.subTest(mutation=mutate.__name__):
                self.refuse(self.rewritten(mutate))

    def test_logo_alternate_text_and_native_path_substitution_refuse(self):
        def wrong_alt(writer):
            figure = next(n for n in self.nodes(writer) if n["/S"] == "/Figure")
            figure[NameObject("/Alt")] = TextStringObject("SYNTHETIC-SENTINEL-REJECT")
        def wrong_path(writer):
            def alter(ops):
                inside = False
                for args, op in ops:
                    if op == b"BDC" and args[0] == "/Figure":
                        inside = True
                    elif inside and op == b"m":
                        args[0] = FloatObject(float(args[0]) + 1)
                        return
                self.fail("fixture logo path absent")
            self.edit_page(writer, 0, alter)
        for mutate in (wrong_alt, wrong_path):
            with self.subTest(mutation=mutate.__name__):
                self.refuse(self.rewritten(mutate))

    def test_changed_dna_pixel_and_missing_or_duplicate_paint_refuse(self):
        def pixel(writer):
            form = next(iter(writer.pages[0]["/Resources"]["/XObject"].values())).get_object()
            image = next(iter(form["/Resources"]["/XObject"].values())).get_object()
            data = bytearray(zlib.decompress(image._data))
            data[1] ^= 1
            image._data = zlib.compress(data)
        self.refuse(self.rewritten(pixel))
        for duplicate in (False, True):
            def paint(writer):
                def alter(ops):
                    index = next(i for i, (_, op) in enumerate(ops) if op == b"Do")
                    if duplicate:
                        ops.insert(index, deepcopy(ops[index]))
                    else:
                        del ops[index]
                self.edit_page(writer, -1, alter)
            with self.subTest(duplicate=duplicate):
                self.refuse(self.rewritten(paint))

    def test_missing_or_duplicated_required_credit_refuses(self):
        for duplicate in (False, True):
            def credit(writer):
                def alter(ops):
                    index = max(i for i, (_, op) in enumerate(ops) if op == b"TJ")
                    if duplicate:
                        ops.insert(index, deepcopy(ops[index]))
                    else:
                        del ops[index]
                self.edit_page(writer, -1, alter)
            with self.subTest(duplicate=duplicate):
                self.refuse(self.rewritten(credit))

    def test_unknown_operators_in_every_dispatch_context_refuse(self):
        for context in ("outside", "/Span", "/Figure", "/Artifact"):
            def unsupported(writer):
                def alter(ops):
                    if context == "outside":
                        ops.insert(0, ([], b"BX"))
                        return
                    for i, (args, op) in enumerate(ops):
                        if op in {b"BMC", b"BDC"} and args[0] == context:
                            ops.insert(i + 1, ([], b"BX"))
                            return
                    self.fail("fixture dispatch context absent")
                self.edit_page(writer, 0, alter)
            with self.subTest(context=context):
                self.refuse(self.rewritten(unsupported))

    def test_native_operator_accounting_holes_and_overlaps_refuse(self):
        for overlap in (False, True):
            native = self.native_record()
            page = native["trace"]["pages"][0]
            if overlap:
                page["graphic_blocks"][0]["first_operator"] = page["projected_operator_ordinals"][0]
            else:
                del page["outside_operator_ordinals"][0]
            with self.subTest(overlap=overlap), patch.object(
                    self.m, "_run_worker", return_value=self.m.canonical(native)):
                self.refuse(self.arguments())

    def test_wrong_language_and_cross_plan_metadata_refuse(self):
        def wrong_language(writer):
            writer._root_object[NameObject("/Lang")] = TextStringObject("pt-BR")
        self.refuse(self.rewritten(wrong_language))
        original = self.m.inspect_pdf_document_metadata
        def wrong_plan(**kwargs):
            receipt = original(**kwargs)
            return type(receipt)(pdf_sha256=receipt.pdf_sha256, ir_sha256=receipt.ir_sha256,
                                 plan_sha256="0" * 64, page_count=receipt.page_count)
        with patch.object(self.m, "inspect_pdf_document_metadata", side_effect=wrong_plan):
            self.refuse(self.arguments())

    def test_late_raster_failure_returns_no_partial_success(self):
        original = self.m.inspect_pdf_raster_window
        seen = []
        def fail_second(**kwargs):
            seen.append(kwargs["window_index"])
            if len(seen) == 2:
                raise ValueError("SYNTHETIC-SENTINEL-REJECT")
            return original(**kwargs)
        with patch.object(self.m, "inspect_pdf_raster_window", side_effect=fail_second):
            self.refuse(self.arguments(96))
        self.assertEqual(seen, [0, 1])

    def test_worker_refusal_and_malformed_result_are_content_free(self):
        for response in (b'{"status":"PASS"}', b'{"private":"SYNTHETIC-SENTINEL-REJECT"'):
            with self.subTest(response=response), patch.object(self.m, "_run_worker", return_value=response):
                self.refuse(self.arguments())
        with patch.object(self.m, "wait_owned_process_group", return_value=70), \
                patch.object(self.m.subprocess, "Popen"):
            self.refuse(self.arguments())

    def test_guard_failure_precedes_request_read_and_output(self):
        from reporting import _pdf_typed_original_brand_qa_worker as worker
        for guard in ("enforce_pdf_landlock", "enforce_pdf_network_filter"):
            with self.subTest(guard=guard), tempfile.TemporaryFile() as source, \
                    tempfile.TemporaryFile() as output, \
                    patch.object(worker.sys, "flags", SimpleNamespace(isolated=1)), \
                    patch.object(worker.sys, "dont_write_bytecode", True), \
                    patch.object(worker.native, "_limits", return_value={}), \
                    patch.object(worker, "_lifetime"), \
                    patch.object(worker, "enforce_pdf_landlock") as landlock, \
                    patch.object(worker, "enforce_pdf_network_filter") as network, \
                    patch.object(worker.native, "_read_request") as read, \
                    patch.object(worker, "_write_bytes") as write:
                (landlock if guard == "enforce_pdf_landlock" else network).side_effect = ValueError("guard refused")
                code = worker.main(["worker", str(source.fileno()), str(output.fileno()), str(os.getpid())])
                self.assertEqual(code, 70)
                read.assert_not_called()
                write.assert_not_called()
                if guard == "enforce_pdf_landlock":
                    network.assert_not_called()

    def test_changed_postflight_brand_contract_refuses(self):
        original = self.m.prepare_typed_original_brand_pdf
        calls = []
        def changed(**kwargs):
            html, bundle, record = original(**kwargs)
            calls.append(True)
            if len(calls) == 2:
                record = {**record, "html_sha256": "0" * 64}
            return html, bundle, record
        with patch.object(self.m, "prepare_typed_original_brand_pdf", side_effect=changed):
            self.refuse(self.arguments())
        self.assertEqual(len(calls), 2)

    def test_unfinished_graphic_paths_cannot_enter_the_logo_figure(self):
        for context in ("artifact-path", "artifact-clip", "outside"):
            def leak(writer):
                def alter(ops):
                    index = next(i for i, (args, op) in enumerate(ops)
                                 if op == b"BDC" and args[0] == "/Figure")
                    rect = ([NumberObject(0), NumberObject(0), NumberObject(100), NumberObject(100)], b"re")
                    if context == "outside":
                        payload = [rect]
                    else:
                        payload = [([NameObject("/Artifact")], b"BMC"), rect]
                        if context == "artifact-clip":
                            payload.append(([], b"W"))
                        payload.append(([], b"EMC"))
                    ops[index:index] = payload
                self.edit_page(writer, 0, alter)
            with self.subTest(context=context):
                self.refuse(self.rewritten(leak))

    def test_form_color_space_requires_a_native_name(self):
        def malformed(writer):
            form = next(iter(writer.pages[0]["/Resources"]["/XObject"].values())).get_object()
            form["/Group"][NameObject("/CS")] = TextStringObject("/DeviceRGB")
        self.refuse(self.rewritten(malformed))

    def test_new_profile_pins_the_executed_original_brand_dependencies(self):
        read_bytes = Path.read_bytes
        for name in ("reporting/brand_assets.py", "reporting/pdf_original_brand_inclusion.py",
                     "reporting/_pdf_original_dna_png_admission.py"):
            target = ROOT / name
            def changed(path):
                data = read_bytes(path)
                return data + b"\n" if path == target else data
            with self.subTest(source=name), patch.object(Path, "read_bytes", changed):
                with self.assertRaises(ValueError):
                    self.m.profile_sha256()

    def test_validly_resealed_inherited_profile_cannot_keep_the_combined_identity(self):
        before = self.m.profile_sha256()
        inherited = self.m.prior._profile()
        profile = self.m.prior._PROFILE_FILE
        source = ROOT / "reporting/pdf_document_metadata.py"
        changed = source.read_bytes() + b"\n# virtual dependency change\n"
        manifest = json.loads(profile.read_bytes())
        manifest["source_sha256"]["reporting/pdf_document_metadata.py"] = sha256(changed).hexdigest()
        replacement = json.dumps(manifest).encode("ascii")
        read_bytes = Path.read_bytes
        def virtual(path):
            if path == source:
                return changed
            if path == profile:
                return replacement
            return read_bytes(path)
        with patch.object(Path, "read_bytes", virtual):
            self.assertNotEqual(self.m.prior._profile(), inherited)
            with self.assertRaises(ValueError):
                self.m._admit(before)

    def test_native_logo_coordinate_precision_is_not_rounded_away(self):
        def changed_coordinate(writer):
            page = writer.pages[0]
            data = page["/Contents"].get_data()
            start = data.index(b"/Figure")
            end = data.index(b"EMC", start)
            block = data[start:end]
            pattern = rb"(?<![0-9.])354\.1(?=\s)"
            self.assertIsNotNone(re.search(pattern, block))
            changed = re.sub(pattern, b"354.100000001", block, count=1)
            self.assertNotEqual(changed, block)
            replacement = DecodedStreamObject()
            replacement.set_data(data[:start] + changed + data[end:])
            page[NameObject("/Contents")] = writer._add_object(replacement)
        self.refuse(self.rewritten(changed_coordinate))

    def test_new_worker_rejects_invalid_descriptor_relationships_before_input(self):
        from reporting import _pdf_typed_original_brand_qa_worker as worker
        for case in ("same", "alias", "named-input", "named-output", "populated-output"):
            with self.subTest(case=case), ExitStack() as stack:
                source = stack.enter_context(tempfile.TemporaryFile())
                output = stack.enter_context(tempfile.TemporaryFile())
                source_fd, output_fd = source.fileno(), output.fileno()
                if case == "same":
                    output_fd = source_fd
                elif case == "alias":
                    output_fd = os.dup(source_fd)
                    stack.callback(os.close, output_fd)
                elif case == "named-input":
                    source_fd = stack.enter_context(tempfile.NamedTemporaryFile()).fileno()
                elif case == "named-output":
                    output_fd = stack.enter_context(tempfile.NamedTemporaryFile()).fileno()
                else:
                    output.write(b"existing")
                    output.flush()
                with patch.object(worker.sys, "flags", SimpleNamespace(isolated=1)), \
                        patch.object(worker.sys, "dont_write_bytecode", True), \
                        patch.object(worker.native, "_limits") as limits, \
                        patch.object(worker.native, "_read_request") as read, \
                        patch.object(worker, "enforce_pdf_landlock") as landlock, \
                        patch.object(worker, "_write_bytes") as write:
                    code = worker.main(["worker", str(source_fd), str(output_fd), str(os.getpid())])
                    self.assertEqual(code, 70)
                    limits.assert_not_called()
                    read.assert_not_called()
                    landlock.assert_not_called()
                    write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
