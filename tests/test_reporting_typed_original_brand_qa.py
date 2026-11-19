"""Same-artifact combined typed/original-brand QA behavior, using synthetic inputs."""
from __future__ import annotations

from copy import deepcopy
from contextlib import ExitStack
from decimal import Decimal
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
from pypdf.generic import (ArrayObject, ByteStringObject, ContentStream, DecodedStreamObject,
                           DictionaryObject, FloatObject, NameObject, NumberObject, TextStringObject)
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
    "OMNIGENIS_PDF_DOCUMENT_METADATA_DISABLED": "",
}


def corrected_bbox_control(pdf_bytes: bytes) -> bytes:
    """Create a separate synthetic validator control; never repair a producer or validator."""
    def require_fixture(condition):
        if not condition:
            raise ValueError("synthetic_bbox_control_refused")
    def number(value):
        require_fixture(type(value) in {NumberObject, FloatObject})
        result = Decimal(int(value)) if type(value) is NumberObject else Decimal(repr(float(value)))
        require_fixture(result.is_finite())
        return result
    require_fixture(type(pdf_bytes) is bytes and pdf_bytes.startswith(b"%PDF-1.7"))
    reader = PdfReader(BytesIO(pdf_bytes), strict=True)
    page_content = tuple(page["/Contents"].get_data() for page in reader.pages)
    writer = PdfWriter(clone_from=reader)
    writer.pdf_header = b"%PDF-1.7"
    figures = []
    def visit(value):
        value = value.get_object()
        if isinstance(value, DictionaryObject):
            if value.get("/S") == "/Figure":
                figures.append(value)
            if "/K" in value:
                visit(value.raw_get("/K"))
        elif isinstance(value, ArrayObject):
            for child in value:
                visit(child)
    visit(writer._root_object.raw_get("/StructTreeRoot"))
    require_fixture(len(figures) == 1)
    figure = figures[0]
    pages = [page for page in writer.pages
             if page.indirect_reference == figure.raw_get("/Pg")]
    require_fixture(len(pages) == 1 and pages[0] is writer.pages[0])
    page = pages[0]
    operations = ContentStream(
        page.raw_get("/Contents"), writer, forced_encoding="bytes").operations
    require_fixture([op for _, op in operations[:3]] == [b"cm", b"q", b"cm"]
                    and not operations[1][0])
    first = tuple(number(v) for v in operations[0][0])
    second = tuple(number(v) for v in operations[2][0])
    require_fixture(len(first) == 6 and first[:5] == (1, 0, 0, -1, 0) and first[5] > 0
                    and second == (Decimal("0.75"), 0, 0, Decimal("0.75"), 0, 0))
    box = figure["/A"]["/BBox"]
    require_fixture(type(box) is ArrayObject and len(box) == 4)
    x0, y0, x1, y1 = (number(v) for v in box)
    require_fixture(x0 < x1 and y0 < y1)
    scale, page_y = second[0], first[5]
    corrected = (scale * x0, page_y - scale * y1, scale * x1, page_y - scale * y0)
    figure["/A"][NameObject("/BBox")] = ArrayObject([FloatObject(str(v)) for v in corrected])
    output = BytesIO()
    writer.write(output)
    candidate = output.getvalue()
    reparsed = PdfReader(BytesIO(candidate), strict=True)
    require_fixture(candidate != pdf_bytes and
                    tuple(page["/Contents"].get_data() for page in reparsed.pages) == page_content)
    return candidate


def _install_flat_id_tree(writer):
    """Index only an explicitly prepared fixture, preserving each key's original bytes."""
    pairs = []
    def visit(value):
        value = value.get_object()
        if isinstance(value, DictionaryObject):
            if value.get("/Type") == "/StructElem" and "/ID" in value:
                identifier = value.raw_get("/ID")
                if type(identifier) is TextStringObject:
                    original = identifier.original_bytes
                elif type(identifier) is ByteStringObject:
                    original = bytes(identifier)
                else:
                    raise ValueError("synthetic_id_tree_control_refused")
                pairs.append((original, identifier, value.indirect_reference))
            if "/K" in value:
                visit(value.raw_get("/K"))
        elif isinstance(value, ArrayObject):
            for child in value:
                visit(child)
    root = writer._root_object["/StructTreeRoot"]
    visit(root)
    pairs.sort(key=lambda row: row[0])
    if len({original for original, _, _ in pairs}) != len(pairs):
        raise ValueError("synthetic_id_tree_control_refused")
    names = ArrayObject([value for _, identifier, reference in pairs
                         for value in (identifier, reference)])
    root[NameObject("/IDTree")] = writer._add_object(
        DictionaryObject({NameObject("/Names"): names}))


def corrected_id_tree_control(pdf_bytes: bytes) -> bytes:
    """Create a separate index-only control; never repair the producer or caller bytes."""
    def snapshot(reader):
        boxes = []
        def visit(value):
            value = value.get_object()
            if isinstance(value, DictionaryObject):
                if value.get("/S") == "/Figure":
                    boxes.append(tuple(float(v) for v in value["/A"]["/BBox"]))
                if "/K" in value:
                    visit(value.raw_get("/K"))
            elif isinstance(value, ArrayObject):
                for child in value:
                    visit(child)
        visit(reader.root_object.raw_get("/StructTreeRoot"))
        information = tuple(sorted((str(key), value.original_bytes)
                                   for key, value in reader.trailer["/Info"].items()))
        metadata = reader.root_object["/Metadata"].get_data()
        return (tuple(page["/Contents"].get_data() for page in reader.pages),
                information, metadata, tuple(boxes))
    if type(pdf_bytes) is not bytes or not pdf_bytes.startswith(b"%PDF-1.7"):
        raise ValueError("synthetic_id_tree_control_refused")
    reader = PdfReader(BytesIO(pdf_bytes), strict=True)
    original = snapshot(reader)
    writer = PdfWriter(clone_from=reader)
    writer.pdf_header = b"%PDF-1.7"
    _install_flat_id_tree(writer)
    output = BytesIO()
    writer.write(output)
    candidate = output.getvalue()
    if candidate == pdf_bytes or snapshot(PdfReader(BytesIO(candidate), strict=True)) != original:
        raise ValueError("synthetic_id_tree_control_refused")
    return candidate


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
        cls.pristine_fixtures = {}

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
            pristine = rendered.pdf_bytes
            self.pristine_fixtures[count] = source, pristine
            self.fixtures[count] = source, corrected_id_tree_control(corrected_bbox_control(pristine))
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

    def pristine_arguments(self, count=2):
        args = self.arguments(count)
        _, pristine = self.pristine_fixtures[count]
        args["pdf_bytes"] = pristine
        args["expected_pdf_sha256"] = sha256(pristine).hexdigest()
        return args

    def run_qa(self, args=None):
        return self.m.validate_typed_original_brand_candidate(
            **(self.arguments() if args is None else args))

    def test_full_multipage_source_brand_and_every_page_bind_to_exact_caller_bytes(self):
        args = self.arguments(96)
        before = bytes(args["pdf_bytes"])
        self.assertNotEqual(before, self.pristine_fixtures[96][1])
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

    def rewritten(self, mutate=lambda writer: None, count=2):
        args = self.arguments(count)
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

    def native_record(self, count=2):
        if not hasattr(self, "_native_records"):
            self._native_records = {}
        if count not in self._native_records:
            args = self.arguments(count)
            raw = self.m._run_worker(args["pdf_bytes"], args["expected_pdf_sha256"])
            self._native_records[count] = self.m.prior._loads(raw.decode("ascii"))
        return deepcopy(self._native_records[count])

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

    def test_source_expectation_digest_requires_exact_string_and_value(self):
        args = self.arguments()
        expected = args["expected_source_expectation_sha256"]
        self.assertIs(type(expected), str)
        self.assertEqual(self.run_qa(args).to_dict()["source_expectation_sha256"], expected)
        comparisons = []
        class EqualToAnyDigest:
            def __eq__(self, other):
                comparisons.append(other)
                return True
        class DigestString(str):
            pass
        wrong = ("0" if expected[0] != "0" else "1") + expected[1:]
        for case, value in (
                ("equality-spoof", EqualToAnyDigest()), ("str-subclass", DigestString(expected)),
                ("malformed", "not-a-digest"), ("wrong-value", wrong)):
            candidate = dict(args, expected_source_expectation_sha256=value)
            with self.subTest(case=case), patch.object(
                    self.m, "_run_worker", wraps=self.m._run_worker) as launch:
                self.refuse(candidate)
                launch.assert_not_called()
                if case == "equality-spoof":
                    self.assertEqual(comparisons, [])

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

    def test_native_parent_tree_requires_ascending_key_pairs(self):
        control = self.rewritten(count=96)
        self.assertEqual(self.run_qa(control).to_dict()["represented_findings"], 96)
        def reorder(writer):
            nums = writer._root_object["/StructTreeRoot"]["/ParentTree"]["/Nums"]
            self.assertGreaterEqual(len(nums), 4)
            keys = [int(nums[i]) for i in range(0, len(nums), 2)]
            self.assertEqual(keys, sorted(keys))
            first = list(nums[0:4])
            nums[0:4] = [first[2], first[3], first[0], first[1]]
            self.assertEqual([int(nums[0]), int(nums[2])], [keys[1], keys[0]])
        self.refuse(self.rewritten(reorder, count=96))

    def test_native_id_tree_matches_declared_table_identifiers(self):
        from pypdf.generic import IndirectObject
        from reporting import _pdf_typed_original_brand_qa_worker as worker
        def declared(reader):
            nodes = self.nodes(SimpleNamespace(_root_object=reader.root_object))
            return {node["/ID"].original_bytes:
                    (node.indirect_reference.idnum, node.indirect_reference.generation)
                    for node in nodes if "/ID" in node}
        def parse_structure(args):
            reader = PdfReader(BytesIO(args["pdf_bytes"]), strict=True)
            trace, _, _ = worker._structure(reader, worker.native._page_tree(reader))
            self.assertEqual({node["native_id"].encode("latin-1"): tuple(node["reference"])
                              for node in trace["nodes"] if node["native_id"] is not None},
                             declared(reader))
            return reader
        for indirect in (False, True):
            def container(writer):
                root = writer._root_object["/StructTreeRoot"]
                index = root["/IDTree"]
                root[NameObject("/IDTree")] = writer._add_object(index) if indirect else index
            with self.subTest(valid_index_indirect=indirect):
                args = self.rewritten(container)
                reader = parse_structure(args)
                self.assertIs(type(reader.root_object["/StructTreeRoot"].raw_get("/IDTree")),
                              IndirectObject if indirect else DictionaryObject)
                self.assertEqual(self.run_qa(args).to_dict()["represented_findings"], 2)

        baseline = PdfReader(BytesIO(self.arguments()["pdf_bytes"]), strict=True)
        expected = declared(baseline)
        self.assertEqual(len(expected), 2)
        content = tuple(page["/Contents"].get_data() for page in baseline.pages)
        index = baseline.root_object["/StructTreeRoot"].raw_get("/IDTree")
        worker._structure_id_tree(baseline, DictionaryObject({NameObject("/Names"): ArrayObject()}), {})
        with patch.object(worker.contract, "MAX_VISITS", 2 * len(expected) - 1):
            with patch.object(worker, "_identifier", side_effect=AssertionError("length guard must precede keys")):
                with self.assertRaisesRegex(ValueError, "^typed original-brand QA refused$"):
                    worker._structure_id_tree(baseline, index, expected)
        foreign = PdfReader(BytesIO(self.arguments()["pdf_bytes"]), strict=True)
        with self.assertRaises(ValueError):
            worker._structure_id_tree(
                baseline, foreign.root_object["/StructTreeRoot"].raw_get("/IDTree"), expected)

        def corrupt(writer, mode):
            root = writer._root_object["/StructTreeRoot"]
            index = root["/IDTree"]
            names = index["/Names"]
            if mode == "missing":
                del root["/IDTree"]
            elif mode == "empty":
                names.clear()
            elif mode == "short":
                del names[-2:]
            elif mode == "extra":
                names.extend([TextStringObject("unknown-index-id"), names[1]])
            elif mode == "duplicate":
                names[2:4] = names[0:2]
            elif mode == "unsorted":
                names[0:4] = names[2:4] + names[0:2]
            elif mode == "wrong-target":
                names[1], names[3] = names[3], names[1]
            elif mode == "page-target":
                names[1] = writer.pages[0].indirect_reference
            elif mode == "direct-target":
                names[1] = names[1].get_object()
            elif mode == "raw-byte-mismatch":
                names[0] = ByteStringObject(b"\xfe\xff" + str(names[0]).encode("utf-16-be"))
                pairs = sorted(zip(names[::2], names[1::2]),
                               key=lambda pair: bytes(pair[0]) if type(pair[0]) is ByteStringObject
                               else pair[0].original_bytes)
                names[:] = [value for pair in pairs for value in pair]
            elif mode == "names-type":
                index[NameObject("/Names")] = DictionaryObject()
            elif mode == "indirect-names":
                index[NameObject("/Names")] = writer._add_object(names)
            elif mode == "kids-outside-profile":
                index[NameObject("/Limits")] = ArrayObject([names[0], names[-2]])
                root[NameObject("/IDTree")] = DictionaryObject({
                    NameObject("/Kids"): ArrayObject([root.raw_get("/IDTree")])})
            elif mode == "limits-outside-profile":
                index[NameObject("/Limits")] = ArrayObject([names[0], names[-2]])
            else:
                self.fail("unknown fixture index mutation")
        for mode in ("missing", "empty", "short", "extra", "duplicate", "unsorted",
                     "wrong-target", "page-target", "direct-target", "raw-byte-mismatch",
                     "names-type", "indirect-names", "kids-outside-profile", "limits-outside-profile"):
            with self.subTest(invalid_index=mode):
                args = self.rewritten(lambda writer: corrupt(writer, mode))
                reader = PdfReader(BytesIO(args["pdf_bytes"]), strict=True)
                self.assertEqual(declared(reader), expected)
                self.assertEqual(tuple(page["/Contents"].get_data() for page in reader.pages), content)
                with self.assertRaises(ValueError):
                    worker._structure(reader, worker.native._page_tree(reader))
                if mode in {"missing", "wrong-target"}:
                    self.refuse(args)

        def mixed_encodings(writer):
            nodes = self.nodes(writer)
            headers = [node for node in nodes if node["/S"] == "/TH"]
            self.assertEqual(len(headers), 2)
            replacements = {str(header["/ID"]): identifier for header, identifier in zip(
                headers, (ByteStringObject(b"z"), ByteStringObject(b"\xfe\xff\x00a")), strict=True)}
            for node in nodes:
                if node["/S"] == "/TH":
                    node[NameObject("/ID")] = replacements[str(node["/ID"])]
                elif node["/S"] == "/TD":
                    references = node["/A"]["/Headers"]
                    references[0] = replacements[str(references[0])]
            _install_flat_id_tree(writer)
        mixed = self.rewritten(mixed_encodings)
        reader = parse_structure(mixed)
        keys = reader.root_object["/StructTreeRoot"]["/IDTree"]["/Names"][::2]
        self.assertEqual([key.original_bytes for key in keys], [b"z", b"\xfe\xff\x00a"])
        self.assertEqual([str(key) for key in keys], ["z", "a"])
        self.assertNotEqual([str(key) for key in keys], sorted(str(key) for key in keys))
        self.assertEqual(self.run_qa(mixed).to_dict()["represented_findings"], 2)

    def test_table_header_identifiers_match_original_bytes(self):
        raw_identifier = b"\x00raw-header"
        def encoded(value):
            return ByteStringObject(b"\xfe\xff" + str(value).encode("utf-16-be"))
        def change(writer, mode):
            nodes = self.nodes(writer)
            if mode == "headers-only":
                cell = next(n for n in nodes if n["/S"] == "/TD")
                cell["/A"]["/Headers"][0] = encoded(cell["/A"]["/Headers"][0])
            elif mode == "ids-only":
                header = next(n for n in nodes if n["/S"] == "/TH")
                header[NameObject("/ID")] = encoded(header["/ID"])
            elif mode == "matching-utf16":
                for node in nodes:
                    if node["/S"] == "/TH":
                        node[NameObject("/ID")] = encoded(str(node["/ID"]) + "\u0100")
                    elif node["/S"] == "/TD":
                        node["/A"]["/Headers"][0] = encoded(
                            str(node["/A"]["/Headers"][0]) + "\u0100")
            elif mode == "matching-nontext-outside-profile":
                header = next(n for n in nodes if n["/S"] == "/TH")
                original_id = str(header["/ID"])
                header[NameObject("/ID")] = ByteStringObject(raw_identifier)
                for node in nodes:
                    if node["/S"] == "/TD":
                        references = node["/A"]["/Headers"]
                        for index, reference in enumerate(references):
                            if str(reference) == original_id:
                                references[index] = ByteStringObject(raw_identifier)
            _install_flat_id_tree(writer)
        for mode in ("ascii", "matching-utf16", "headers-only", "ids-only",
                     "matching-nontext-outside-profile"):
            args = self.rewritten(lambda writer: change(writer, mode))
            reader = PdfReader(BytesIO(args["pdf_bytes"]), strict=True)
            nodes = self.nodes(SimpleNamespace(_root_object=reader.root_object))
            headers = {str(n["/ID"]): n["/ID"] for n in nodes if n["/S"] == "/TH"}
            mismatches = []
            nontext_pairs = 0
            for cell in (n for n in nodes if n["/S"] == "/TD"):
                reference = cell["/A"]["/Headers"][0]
                header = headers[str(reference)]
                if type(reference) is ByteStringObject:
                    self.assertEqual(mode, "matching-nontext-outside-profile")
                    self.assertIs(type(header), ByteStringObject)
                    self.assertEqual(bytes(reference), raw_identifier)
                    self.assertEqual(bytes(header), raw_identifier)
                    nontext_pairs += 1
                    mismatches.append(False)
                else:
                    self.assertIs(type(reference), TextStringObject)
                    self.assertIs(type(header), TextStringObject)
                    self.assertEqual(str(reference), str(header))
                    if mode == "matching-utf16":
                        self.assertIn("\u0100", str(reference))
                        self.assertTrue(reference.original_bytes.startswith(b"\xfe\xff"))
                        self.assertTrue(reference.original_bytes.endswith(b"\x01\x00"))
                    mismatches.append(reference.original_bytes != header.original_bytes)
            with self.subTest(mode=mode):
                if mode in {"ascii", "matching-utf16"}:
                    self.assertFalse(any(mismatches))
                    self.assertEqual(self.run_qa(args).to_dict()["represented_findings"], 2)
                elif mode == "matching-nontext-outside-profile":
                    self.assertGreater(nontext_pairs, 0)
                    self.assertFalse(any(mismatches))
                    self.refuse(args)
                else:
                    self.assertTrue(any(mismatches))
                    self.refuse(args)

        from jsonschema.exceptions import ValidationError
        args = self.arguments()
        native = self.native_record()
        header = next(n for n in native["trace"]["nodes"] if n["role"] == "/TH")
        previous = header["native_id"]
        header["native_id"] = "\u0100"
        matched = 0
        for node in native["trace"]["nodes"]:
            for index, identifier in enumerate(node["headers"]):
                if identifier == previous:
                    node["headers"][index] = "\u0100"
                    matched += 1
        self.assertGreater(matched, 0)
        self.m.project_text_trace(native["trace"])
        for boundary in ("schema", "parent"):
            with self.subTest(forged_native_identifier=boundary):
                with self.assertRaises(ValidationError):
                    if boundary == "schema":
                        self.m._schema(native, "native")
                    else:
                        self.m._validate_native(
                            native, args["pdf_bytes"], args["expected_profile_sha256"])

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

    def test_figure_bbox_contains_the_actual_logo_geometry(self):
        pristine = self.pristine_arguments()
        original_bytes = pristine["pdf_bytes"]
        reader = PdfReader(BytesIO(original_bytes), strict=True)
        original_figure = next(n for n in self.nodes(
            SimpleNamespace(_root_object=reader.root_object)) if n["/S"] == "/Figure")
        producer_box = [float(v) for v in original_figure["/A"]["/BBox"]]
        with self.subTest(candidate="pristine-producer"):
            self.refuse(pristine)
        self.assertEqual(pristine["pdf_bytes"], original_bytes)
        self.assertEqual(self.pristine_fixtures[2][1], original_bytes)
        self.assertNotEqual(self.arguments()["pdf_bytes"], original_bytes)
        from reporting import _pdf_typed_original_brand_qa_worker as worker
        index_only = dict(pristine)
        index_only["pdf_bytes"] = corrected_id_tree_control(original_bytes)
        index_only["expected_pdf_sha256"] = sha256(index_only["pdf_bytes"]).hexdigest()
        reader = PdfReader(BytesIO(index_only["pdf_bytes"]), strict=True)
        pages = worker.native._page_tree(reader)
        trace, attributes, figures = worker._structure(reader, pages)
        worker._passive(reader, attributes, figures)
        roles = {tuple(node["reference"]): node["role"] for node in trace["nodes"]}
        figure = next(node for node in trace["nodes"] if node["role"] == "/Figure")
        self.assertEqual([float(v) for v in figure["figure_bbox"]], producer_box)
        x0, y0, x1, y1 = map(Decimal, figure["figure_bbox"])
        declared_box = worker._rectangle((x0, y0, x1 - x0, y1 - y0), worker.IDENTITY)
        with patch.object(worker, "_contains", wraps=worker._contains) as contains:
            with self.assertRaisesRegex(ValueError, "^typed original-brand QA refused$"):
                worker._page(trace["pages"][0], pages[0][1], pages[0][2], reader,
                             {}, {}, roles, worker.contract.MAX_STREAM_BYTES)
            self.assertGreaterEqual(contains.call_count, 1)
            self.assertEqual(contains.call_args.args[0], declared_box)
        self.assertTrue(all(worker._contains(*call.args) for call in contains.call_args_list[:-1]))
        self.assertFalse(worker._contains(*contains.call_args.args))
        with self.subTest(candidate="id-tree-only-original-bbox"):
            self.refuse(index_only)
        def box(writer, mode):
            figure = next(n for n in self.nodes(writer) if n["/S"] == "/Figure")
            corrected = [float(v) for v in figure["/A"]["/BBox"]]
            if mode == "full-page":
                values = [float(v) for v in writer.pages[0].mediabox]
            elif mode == "reintroduced-producer":
                values = producer_box
            elif mode == "disjoint":
                values = [0, 0, 1, 1]
            elif mode == "partial":
                values = list(corrected)
                values[2] = (values[0] + values[2]) / 2
            else:
                return
            self.assertLess(values[0], values[2])
            self.assertLess(values[1], values[3])
            figure["/A"][NameObject("/BBox")] = ArrayObject([FloatObject(v) for v in values])
        for mode in ("corrected-synthetic", "full-page"):
            with self.subTest(control=mode):
                args = self.rewritten(lambda writer: box(writer, mode))
                self.assertEqual(self.run_qa(args).to_dict()["figure_count"], 1)
        for mode in ("reintroduced-producer", "disjoint", "partial"):
            with self.subTest(mode=mode):
                self.refuse(self.rewritten(lambda writer: box(writer, mode)))

    def test_logo_paint_requires_containment_in_active_clips_and_page(self):
        def paint(writer, case):
            def alter(ops):
                start = next(i for i, (args, op) in enumerate(ops)
                             if op == b"BDC" and args[0] == "/Figure")
                end = next(i for i in range(start + 1, len(ops)) if ops[i][1] == b"EMC")
                if case == "translated-outside-page":
                    matrix = next(args for args, op in ops[start + 1:end] if op == b"cm")
                    matrix[4] = NumberObject(1000)
                    return
                payload = [([], b"q")]
                if case == "unknown-active-clip":
                    payload.extend([
                        ([NameObject("/Artifact")], b"BMC"),
                        ([NumberObject(0), NumberObject(0)], b"m"),
                        ([NumberObject(1), NumberObject(0)], b"l"),
                        ([NumberObject(0), NumberObject(1)], b"l"),
                        ([], b"W"), ([], b"n"), ([], b"EMC")])
                else:
                    rectangle = (0, 0, 1200, 1600) if case == "containing" else (0, 0, 1, 1)
                    payload.extend([
                        ([NumberObject(v) for v in rectangle], b"re"), ([], b"W"), ([], b"n")])
                if case == "restored":
                    payload.append(([], b"Q"))
                else:
                    ops.insert(end + 1, ([], b"Q"))
                ops[start:start] = payload
            self.edit_page(writer, 0, alter)
        for case in ("containing", "restored"):
            with self.subTest(control=case):
                args = self.rewritten(lambda writer: paint(writer, case))
                self.assertEqual(self.run_qa(args).to_dict()["figure_count"], 1)
        for case in ("excluding-active-clip", "unknown-active-clip", "translated-outside-page"):
            with self.subTest(case=case):
                self.refuse(self.rewritten(lambda writer: paint(writer, case)))

    def test_inherited_cropbox_uses_nearest_explicit_page_tree_value(self):
        from reporting import _pdf_typed_original_brand_qa_worker as worker
        def change(writer, case):
            leaves = list(writer.pages)
            root_ref = writer._root_object.raw_get("/Pages")
            root = root_ref.get_object()
            for page in leaves:
                page[NameObject("/CropBox")] = deepcopy(page.raw_get("/MediaBox"))
            first = leaves[0]
            full = deepcopy(first.raw_get("/MediaBox"))
            tiny = ArrayObject([NumberObject(v) for v in (0, 0, 1, 1)])
            del first["/CropBox"]
            root[NameObject("/CropBox")] = deepcopy(
                tiny if case in {"root-excludes", "leaf-overrides-root",
                                 "nearest-overrides-root"} else full)
            if case == "leaf-overrides-root":
                first[NameObject("/CropBox")] = full
            elif case in {"nearest-overrides-root", "nearest-excludes"}:
                self.assertEqual(first.raw_get("/Parent"), root_ref)
                first_ref = first.indirect_reference
                nearest = DictionaryObject({
                    NameObject("/Type"): NameObject("/Pages"),
                    NameObject("/Parent"): root_ref,
                    NameObject("/Kids"): ArrayObject([first_ref]),
                    NameObject("/Count"): NumberObject(1),
                    NameObject("/CropBox"): deepcopy(
                        full if case == "nearest-overrides-root" else tiny),
                })
                nearest_ref = writer._add_object(nearest)
                kids = root.raw_get("/Kids")
                position = next(i for i, child in enumerate(kids) if child == first_ref)
                kids[position] = nearest_ref
                first[NameObject("/Parent")] = nearest_ref
        for case in ("root-contains", "leaf-overrides-root", "nearest-overrides-root",
                     "root-excludes", "nearest-excludes"):
            args = self.rewritten(lambda writer: change(writer, case))
            reader = PdfReader(BytesIO(args["pdf_bytes"]), strict=True)
            pages = worker.native._page_tree(reader)
            leaf = pages[0][1]
            self.assertIn("/MediaBox", leaf)
            self.assertEqual("/CropBox" in leaf, case == "leaf-overrides-root")
            parent = leaf.raw_get("/Parent").get_object()
            self.assertIn("/CropBox", parent)
            full = list(leaf.raw_get("/MediaBox"))
            effective = leaf.raw_get("/CropBox") if "/CropBox" in leaf else parent.raw_get("/CropBox")
            self.assertEqual(list(effective),
                             [0, 0, 1, 1] if case in {"root-excludes", "nearest-excludes"} else full)
            if case == "leaf-overrides-root":
                self.assertEqual(list(parent.raw_get("/CropBox")), [0, 0, 1, 1])
            if case.startswith("nearest-"):
                self.assertEqual(parent["/Count"], 1)
                self.assertEqual(len(parent["/Kids"]), 1)
                ancestor = parent.raw_get("/Parent").get_object()
                self.assertEqual(list(ancestor.raw_get("/CropBox")),
                                 [0, 0, 1, 1] if case == "nearest-overrides-root" else full)
            with self.subTest(case=case):
                if case in {"root-excludes", "nearest-excludes"}:
                    with self.assertRaises(ValueError):
                        self.m._run_worker(args["pdf_bytes"], args["expected_pdf_sha256"])
                else:
                    raw = self.m._run_worker(args["pdf_bytes"], args["expected_pdf_sha256"])
                    native = self.m.prior._loads(raw.decode("ascii"))
                    self.assertEqual(native["page_count"], len(pages))
                    self.m._validate_native(
                        native, args["pdf_bytes"], args["expected_profile_sha256"])

    def test_singular_logo_wrapper_and_outer_transforms_refuse(self):
        for context in ("wrapper-0", "wrapper-1", "wrapper-2", "outer"):
            def collapse(writer):
                def alter(ops):
                    start = next(i for i, (args, op) in enumerate(ops)
                                 if op == b"BDC" and args[0] == "/Figure")
                    end = next(i for i in range(start + 1, len(ops)) if ops[i][1] == b"EMC")
                    if context == "outer":
                        ops.insert(end + 1, ([], b"Q"))
                        ops[start:start] = [
                            ([], b"q"),
                            ([NumberObject(v) for v in (0, 0, 0, 1, 0, 0)], b"cm")]
                    else:
                        matrices = [args for args, op in ops[start + 1:end] if op == b"cm"]
                        self.assertEqual(len(matrices), 5)
                        matrix = matrices[int(context[-1])]
                        matrix[0] = NumberObject(0)
                        matrix[1] = NumberObject(0)
                self.edit_page(writer, 0, alter)
            with self.subTest(context=context):
                self.refuse(self.rewritten(collapse))

    @staticmethod
    def dna_block_range(ops):
        paint = next(i for i, (_, op) in enumerate(ops) if op == b"Do")
        start = max(i for i in range(paint) if ops[i][1] == b"BMC"
                    and ops[i][0] == ["/Artifact"])
        end = next(i for i in range(paint + 1, len(ops)) if ops[i][1] == b"EMC")
        return start, end

    def test_dna_paint_requires_containing_local_inherited_and_form_clips(self):
        def inherited(writer, rectangle, restored=False):
            def alter(ops):
                start, end = self.dna_block_range(ops)
                payload = [
                    ([], b"q"), ([NumberObject(v) for v in rectangle], b"re"),
                    ([], b"W"), ([], b"n")]
                if restored:
                    payload.append(([], b"Q"))
                else:
                    ops.insert(end + 1, ([], b"Q"))
                ops[start:start] = payload
            self.edit_page(writer, -1, alter)

        for restored in (False, True):
            rectangle = (0, 0, 1, 1) if restored else (0, 0, 1200, 1600)
            with self.subTest(control_restored=restored):
                control = self.rewritten(lambda writer: inherited(writer, rectangle, restored))
                self.assertEqual(self.run_qa(control).to_dict()["dna_image_count"], 1)
        for case in ("empty-local", "excluding-local", "excluding-inherited",
                     "unknown-inherited", "clipping-form-bbox", "shifted-form-image"):
            def clip(writer):
                if case == "excluding-inherited":
                    inherited(writer, (0, 0, 1, 1))
                elif case == "unknown-inherited":
                    def alter(ops):
                        start, end = self.dna_block_range(ops)
                        ops.insert(end + 1, ([], b"Q"))
                        ops[start:start] = [
                            ([], b"q"), ([NameObject("/Artifact")], b"BMC"),
                            ([NumberObject(0), NumberObject(0)], b"m"),
                            ([NumberObject(1), NumberObject(0)], b"l"),
                            ([NumberObject(0), NumberObject(1)], b"l"),
                            ([], b"W"), ([], b"n"), ([], b"EMC")]
                    self.edit_page(writer, -1, alter)
                elif case in {"clipping-form-bbox", "shifted-form-image"}:
                    form = next(iter(writer.pages[-1]["/Resources"]["/XObject"].values())).get_object()
                    if case == "clipping-form-bbox":
                        form[NameObject("/BBox")] = ArrayObject(
                            [NumberObject(v) for v in (0, 0, 1, 1)])
                    else:
                        content = ContentStream(form, writer, forced_encoding="bytes")
                        self.assertEqual([op for _, op in content.operations], [b"cm", b"cm", b"Do"])
                        content.operations[0][0][4] = NumberObject(1000)
                        form._data = zlib.compress(content.get_data())
                else:
                    def alter(ops):
                        start, end = self.dna_block_range(ops)
                        index = next(i for i in range(start + 1, end) if ops[i][1] == b"re")
                        rectangle = (0, 0, 0, 0) if case == "empty-local" else (0, 0, 1, 1)
                        ops[index] = ([NumberObject(v) for v in rectangle], b"re")
                    self.edit_page(writer, -1, alter)
            with self.subTest(case=case):
                self.refuse(self.rewritten(clip))

    def test_dna_paint_must_remain_on_its_structural_page(self):
        def relocate(writer):
            self.assertGreater(len(writer.pages), 1)
            moved = []
            def remove(ops):
                start, end = self.dna_block_range(ops)
                moved.extend(deepcopy(ops[start:end + 1]))
                del ops[start:end + 1]
            self.edit_page(writer, -1, remove)
            self.assertTrue(moved)
            def insert(ops):
                self.assertEqual(ops[-1][1], b"Q")
                ops[-1:-1] = [([], b"q"), *moved, ([], b"Q")]
            self.edit_page(writer, 0, insert)
        args = self.rewritten(relocate, count=96)
        from reporting import _pdf_typed_original_brand_qa_worker as worker
        reader = PdfReader(BytesIO(args["pdf_bytes"]), strict=True)
        pages = worker.native._page_tree(reader)
        trace, attributes, figures = worker._structure(reader, pages)
        worker._passive(reader, attributes, figures)
        nodes = {tuple(node["reference"]): node for node in trace["nodes"]}
        roles = {key: node["role"] for key, node in nodes.items()}
        document_node = nodes[tuple(trace["root_children"][0])]
        dna_page = nodes[tuple(document_node["children"][-2]["reference"])]["page_reference"]
        fonts, forms, decoded = {}, {}, 0
        for page, (_, obj, resources) in zip(trace["pages"], pages, strict=True):
            worker._page(page, obj, resources, reader, fonts, forms, roles,
                         worker.contract.MAX_STREAM_BYTES - decoded)
            decoded += page["decoded_stream_bytes"]
        paints = [(page["reference"], block["dna_paints"])
                  for page in trace["pages"] for block in page["graphic_blocks"]
                  if block["dna_paints"]]
        self.assertEqual(len(trace["pages"]), len(pages))
        self.assertEqual(sum(count for _, count in paints), 1)
        self.assertEqual(paints[0][0], trace["pages"][0]["reference"])
        self.assertEqual(dna_page, trace["pages"][-1]["reference"])
        self.assertTrue(all(reference != dna_page for reference, _ in paints))
        self.refuse(args)

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
        args = self.arguments()
        original = self.native_record()
        self.m._validate_native(original, args["pdf_bytes"], args["expected_profile_sha256"])
        for overlap in (False, True):
            native = deepcopy(original)
            page = native["trace"]["pages"][0]
            if overlap:
                mapped = set(page["projected_operator_ordinals"])
                _, ordinal, index = min(
                    (max(block["first_operator"] - ordinal, ordinal - block["last_operator"], 0),
                     ordinal, index)
                    for index, block in enumerate(page["graphic_blocks"]) for ordinal in mapped)
                block = page["graphic_blocks"][index]
                block["first_operator"] = min(block["first_operator"], ordinal)
                block["last_operator"] = max(block["last_operator"], ordinal)
                span = set(range(block["first_operator"], block["last_operator"] + 1))
                self.assertLess(block["first_operator"], block["last_operator"])
                self.assertEqual(span.intersection(mapped), {ordinal})
                self.assertTrue(all(
                    not span.intersection(range(other["first_operator"], other["last_operator"] + 1))
                    for other_index, other in enumerate(page["graphic_blocks"]) if other_index != index))
                page["outside_operator_ordinals"] = [
                    value for value in page["outside_operator_ordinals"] if value not in span]
                covered = mapped | set(page["outside_operator_ordinals"])
                for graphic in page["graphic_blocks"]:
                    covered.update(range(graphic["first_operator"], graphic["last_operator"] + 1))
                self.assertEqual(covered, set(range(page["original_operator_count"])))
            else:
                del page["outside_operator_ordinals"][0]
            self.assertEqual(native["trace"]["nodes"], original["trace"]["nodes"])
            self.assertEqual(page["mcid_owners"], original["trace"]["pages"][0]["mcid_owners"])
            self.m._schema(native, "native")
            self.m.project_text_trace(native["trace"])
            with self.subTest(overlap=overlap), patch.object(
                    self.m, "_run_worker", return_value=self.m.canonical(native)):
                self.refuse(args)

    def test_native_decoded_page_stream_bytes_obey_aggregate_budget(self):
        args = self.arguments(96)
        original = self.native_record(96)
        self.m._validate_native(original, args["pdf_bytes"], args["expected_profile_sha256"])
        boundary = deepcopy(original)
        pages = boundary["trace"]["pages"]
        self.assertGreater(len(pages), 1)
        self.assertEqual(len({tuple(page["contents_reference"]) for page in pages}), len(pages))
        limit = self.m.prior.MAX_STREAM_BYTES
        pages[0]["decoded_stream_bytes"] = limit - sum(
            page["decoded_stream_bytes"] for page in pages[1:])
        self.assertTrue(all(0 < page["decoded_stream_bytes"] <= limit for page in pages))
        self.assertEqual(sum(page["decoded_stream_bytes"] for page in pages), limit)
        self.m._schema(boundary, "native")
        self.m._validate_native(boundary, args["pdf_bytes"], args["expected_profile_sha256"])
        bad = deepcopy(boundary)
        bad["trace"]["pages"][0]["decoded_stream_bytes"] += 1
        self.assertEqual(sum(page["decoded_stream_bytes"] for page in bad["trace"]["pages"]),
                         limit + 1)
        for observed, source in zip(bad["trace"]["pages"], original["trace"]["pages"], strict=True):
            self.assertTrue(0 < observed["decoded_stream_bytes"] <= limit)
            self.assertEqual({key: value for key, value in observed.items()
                              if key != "decoded_stream_bytes"},
                             {key: value for key, value in source.items()
                              if key != "decoded_stream_bytes"})
        self.m._schema(bad, "native")
        self.m.prior._reference_consistency(bad["trace"])
        projected, _ = self.m.project_text_trace(bad["trace"])
        fonts = {self.m.prior._reference(font["reference"]): font for font in projected["fonts"]}
        for page in projected["pages"]:
            self.assertEqual(self.m.prior._replay(page, fonts), page["runs"])
        with self.assertRaises(ValueError):
            self.m._validate_native(bad, args["pdf_bytes"], args["expected_profile_sha256"])
        with patch.object(self.m, "_run_worker", return_value=self.m.canonical(bad)):
            self.refuse(args)

    def test_native_reference_categories_remain_disjoint_with_valid_sharing(self):
        def shared_font(writer):
            fonts = writer.pages[0]["/Resources"]["/Font"]
            original_font = next(iter(fonts.values())).get_object()
            duplicate = DictionaryObject(dict(original_font))
            fonts[NameObject("/SharedFontControl")] = writer._add_object(duplicate)
        args = self.rewritten(shared_font)
        raw = self.m._run_worker(args["pdf_bytes"], args["expected_pdf_sha256"])
        original = self.m.prior._loads(raw.decode("ascii"))
        fonts = original["trace"]["fonts"]
        self.assertLess(len({tuple(f["cid_reference"]) for f in fonts}), len(fonts))
        self.assertLess(len({tuple(f["cmap_reference"]) for f in fonts}), len(fonts))
        self.m._validate_native(original, args["pdf_bytes"], args["expected_profile_sha256"])
        trace = original["trace"]
        figure = next(n for n in trace["nodes"] if n["role"] == "/Figure")
        aliases = {
            "root": trace["root_reference"],
            "removed-figure-node": figure["reference"],
            "page": trace["pages"][0]["reference"],
            "contents": trace["pages"][0]["contents_reference"],
            "type0-font": fonts[0]["reference"],
            "cmap": fonts[0]["cmap_reference"],
        }
        for category, reference in aliases.items():
            native = deepcopy(original)
            native["trace"]["fonts"][0]["cid_reference"] = list(reference)
            with self.subTest(category=category):
                self.m._schema(native, "native")
                self.m.project_text_trace(native["trace"])
                with self.assertRaises(ValueError):
                    self.m._validate_native(
                        native, args["pdf_bytes"], args["expected_profile_sha256"])
        groups = {}
        for font in fonts:
            groups.setdefault(tuple(font["cmap_reference"]), []).append(font)
        unshared = next(group for group in groups.values() if len(group) == 1)
        shared = next(group for group in groups.values() if len(group) > 1)
        for mode, group in (("unshared-record", unshared), ("all-shared-records", shared)):
            native = deepcopy(original)
            target = tuple(group[0]["cmap_reference"])
            affected = []
            for font in native["trace"]["fonts"]:
                if tuple(font["cmap_reference"]) == target:
                    mapping = dict(font["mapping"])
                    font["mapping"].append(deepcopy(font["mapping"][0]))
                    self.assertEqual(dict(font["mapping"]), mapping)
                    self.assertEqual(len(font["mapping"]), len(mapping) + 1)
                    affected.append(font["reference"])
            self.assertEqual(len(affected), len(group))
            self.m._schema(native, "native")
            self.m.prior._reference_consistency(native["trace"])
            projected, _ = self.m.project_text_trace(native["trace"])
            bound = {self.m.prior._reference(font["reference"]): font
                     for font in projected["fonts"]}
            for page in projected["pages"]:
                self.assertEqual(self.m.prior._replay(page, bound), page["runs"])
            with self.subTest(duplicate_cmap=mode, boundary="parent"):
                with self.assertRaises(ValueError):
                    self.m._validate_native(
                        native, args["pdf_bytes"], args["expected_profile_sha256"])
            with self.subTest(duplicate_cmap=mode, boundary="public"), patch.object(
                    self.m, "_run_worker", return_value=self.m.canonical(native)):
                self.refuse(args)

    def test_projected_span_ordinals_are_contiguous_only_within_spans(self):
        def with_gap(writer):
            def alter(ops):
                starts = [i for i, (args, op) in enumerate(ops)
                          if op == b"BDC" and args[0] == "/Span"]
                self.assertGreaterEqual(len(starts), 2)
                ops[starts[1]:starts[1]] = [([], b"q"), ([], b"Q")]
            self.edit_page(writer, 0, alter)
        args = self.rewritten(with_gap)
        raw = self.m._run_worker(args["pdf_bytes"], args["expected_pdf_sha256"])
        original = self.m.prior._loads(raw.decode("ascii"))
        self.m._validate_native(original, args["pdf_bytes"], args["expected_profile_sha256"])
        native = deepcopy(original)
        page = native["trace"]["pages"][0]
        mapped = page["projected_operator_ordinals"]
        starts = [i for i, event in enumerate(page["events"]) if event["operator"] == "BDC"]
        ends = [i for i, event in enumerate(page["events"]) if event["operator"] == "EMC"]
        self.assertEqual(len(starts), len(ends))
        self.assertGreaterEqual(len(starts), 2)
        self.assertGreater(mapped[starts[1]], mapped[ends[0]] + 1)
        start = starts[1]
        lower = mapped[start - 1]
        earlier = [ordinal for ordinal in page["outside_operator_ordinals"]
                   if lower < ordinal < mapped[start]]
        self.assertTrue(earlier)
        outside = max(earlier)
        previous = mapped[start]
        mapped[start] = outside
        page["outside_operator_ordinals"].remove(outside)
        page["outside_operator_ordinals"].append(previous)
        page["outside_operator_ordinals"].sort()
        self.assertEqual(mapped, sorted(set(mapped)))
        self.assertEqual(page["outside_operator_ordinals"],
                         sorted(set(page["outside_operator_ordinals"])))
        self.assertGreater(mapped[start + 1], mapped[start] + 1)
        covered = set(mapped)
        self.assertFalse(covered.intersection(page["outside_operator_ordinals"]))
        covered.update(page["outside_operator_ordinals"])
        for block in page["graphic_blocks"]:
            span = set(range(block["first_operator"], block["last_operator"] + 1))
            self.assertFalse(covered.intersection(span))
            covered.update(span)
        self.assertEqual(covered, set(range(page["original_operator_count"])))
        self.assertEqual(page["events"], original["trace"]["pages"][0]["events"])
        self.assertEqual(page["runs"], original["trace"]["pages"][0]["runs"])
        self.m._schema(native, "native")
        self.m.project_text_trace(native["trace"])
        with self.assertRaises(ValueError):
            self.m._validate_native(native, args["pdf_bytes"], args["expected_profile_sha256"])

    def test_graphic_blocks_must_match_figure_page_mcid_and_unowned_artifacts(self):
        args = self.arguments(96)
        original = self.native_record(96)
        _, counts = self.m._validate_native(
            original, args["pdf_bytes"], args["expected_profile_sha256"])
        self.assertEqual(counts["figure_count"], 1)
        self.assertGreater(len(original["trace"]["pages"]), 1)
        for case in ("figure-null", "figure-text-owner", "figure-wrong-page", "artifact-owned"):
            native = deepcopy(original)
            pages = native["trace"]["pages"]
            figure = next(b for b in pages[0]["graphic_blocks"] if b["kind"] == "figure")
            figure_mcid = figure["mcid"]
            if case == "figure-null":
                figure["mcid"] = None
            elif case == "figure-text-owner":
                figure["mcid"] = next(
                    owner["mcid"] for owner in pages[0]["mcid_owners"]
                    if owner["mcid"] != figure_mcid)
            elif case == "figure-wrong-page":
                other = next(b for b in pages[1]["graphic_blocks"]
                             if b["kind"] == "artifact" and b["dna_paints"] == 0)
                figure["kind"], figure["mcid"] = "artifact", None
                other["kind"], other["mcid"] = "figure", figure_mcid
            else:
                artifact = next(b for b in pages[0]["graphic_blocks"] if b["kind"] == "artifact")
                artifact["mcid"] = figure_mcid
            with self.subTest(case=case):
                self.m._schema(native, "native")
                self.m.project_text_trace(native["trace"])
                with self.assertRaises(ValueError):
                    self.m._validate_native(
                        native, args["pdf_bytes"], args["expected_profile_sha256"])

    def test_native_graphic_intervals_match_the_admitted_grammar(self):
        args = self.arguments(96)
        original = self.native_record(96)
        self.m._validate_native(original, args["pdf_bytes"], args["expected_profile_sha256"])
        for case in ("dna-five", "dna-seven", "figure-533"):
            native = deepcopy(original)
            page = native["trace"]["pages"][0 if case == "figure-533" else -1]
            block = next(b for b in page["graphic_blocks"]
                         if (b["kind"] == "figure" if case == "figure-533"
                             else b["dna_paints"] == 1))
            outside = page["outside_operator_ordinals"]
            length = block["last_operator"] - block["first_operator"] + 1
            if case == "figure-533":
                self.assertGreaterEqual(length, 534)
                last = block["first_operator"] + 532
                outside.extend(range(last + 1, block["last_operator"] + 1))
                block["last_operator"] = last
            else:
                self.assertEqual(length, 6)
                if case == "dna-five":
                    outside.append(block["first_operator"])
                    block["first_operator"] += 1
                else:
                    adjacent = [ordinal for ordinal in
                                (block["first_operator"] - 1, block["last_operator"] + 1)
                                if ordinal in outside]
                    self.assertTrue(adjacent)
                    ordinal = adjacent[0]
                    outside.remove(ordinal)
                    if ordinal < block["first_operator"]:
                        block["first_operator"] = ordinal
                    else:
                        block["last_operator"] = ordinal
            outside.sort()
            self.assertEqual(block["last_operator"] - block["first_operator"] + 1,
                             {"dna-five": 5, "dna-seven": 7, "figure-533": 533}[case])
            for current in native["trace"]["pages"]:
                covered = set(current["projected_operator_ordinals"])
                for graphic in current["graphic_blocks"]:
                    interval = set(range(graphic["first_operator"],
                                         graphic["last_operator"] + 1))
                    self.assertFalse(covered.intersection(interval))
                    covered.update(interval)
                remaining = current["outside_operator_ordinals"]
                self.assertEqual(remaining, sorted(set(remaining)))
                self.assertFalse(covered.intersection(remaining))
                covered.update(remaining)
                self.assertEqual(covered, set(range(current["original_operator_count"])))
            self.assertEqual(native["graphics"], original["graphics"])
            self.assertEqual(native["trace"]["nodes"], original["trace"]["nodes"])
            self.m._schema(native, "native")
            self.m.project_text_trace(native["trace"])
            with self.subTest(case=case):
                with self.assertRaises(ValueError):
                    self.m._validate_native(
                        native, args["pdf_bytes"], args["expected_profile_sha256"])

    def test_native_dna_paint_requires_its_artifact_page(self):
        args = self.arguments(96)
        original = self.native_record(96)
        self.m._validate_native(original, args["pdf_bytes"], args["expected_profile_sha256"])
        self.assertGreater(len(original["trace"]["pages"]), 1)
        for case in ("figure", "earlier-page-artifact"):
            native = deepcopy(original)
            pages = native["trace"]["pages"]
            donor = next(b for b in pages[-1]["graphic_blocks"] if b["dna_paints"] == 1)
            target_kind = "figure" if case == "figure" else "artifact"
            receiver = next(b for b in pages[0]["graphic_blocks"]
                            if b["kind"] == target_kind and b["dna_paints"] == 0)
            donor["dna_paints"], receiver["dna_paints"] = 0, 1
            with self.subTest(receiver=case):
                self.assertEqual(sum(b["dna_paints"] for p in pages for b in p["graphic_blocks"]),
                                 native["graphics"]["dna_paints"])
                self.m._schema(native, "native")
                self.m.project_text_trace(native["trace"])
                with self.assertRaises(ValueError):
                    self.m._validate_native(
                        native, args["pdf_bytes"], args["expected_profile_sha256"])

    def test_receipt_requires_every_page_rasterized_even_after_resealing(self):
        receipt = self.run_qa()
        bad = receipt.to_dict()
        bad["page_count"] = 2
        bad["raster_inspected_pages"] = 1
        self.assertEqual(set(bad["stages"].values()), {"PASS"})
        self.m._schema(bad)
        payload = self.m.canonical(bad)
        with self.assertRaises(ValueError):
            self.m.TypedOriginalBrandQA(payload)
        object.__setattr__(receipt, "_payload", payload)
        object.__setattr__(receipt, "_sealed_sha256", sha256(payload).hexdigest())
        with self.assertRaises(ValueError):
            receipt.to_dict()

    def test_receipt_source_units_cover_title_and_represented_findings(self):
        source = self.run_qa().to_dict()
        for findings, actual_units in ((0, 6), (1, 8), (96, 198)):
            presentation = document(findings)
            produced = prepare_typed_pdf_source(
                presentation_ir=presentation, expected_ir_sha256=digest(presentation)).to_dict()
            with self.subTest(producer_findings=findings):
                self.assertEqual(produced["counts"]["represented_findings"], findings)
                self.assertEqual(len(produced["units"]), actual_units)
                self.assertGreaterEqual(actual_units, 1 if findings == 0 else findings + 3)
        for case in ("missing-title-or-findings", "missing-table-units", "short-pdf",
                     "too-few-text-mcids", "too-few-projected-nodes", "too-many-pages",
                     "too-few-operations"):
            bad = deepcopy(source)
            coverage = bad["coverage"]
            if case == "missing-title-or-findings":
                bad["source_units"] = 1
            elif case == "missing-table-units":
                bad["source_units"] = bad["represented_findings"] + 1
            elif case == "short-pdf":
                bad["pdf_byte_count"] = 7
            elif case == "too-few-text-mcids":
                coverage["text_mcids"] = bad["source_units"] + bad["brand_text_units"] - 1
                coverage["native_mcids"] = coverage["text_mcids"] + coverage["figure_count"]
            elif case == "too-few-projected-nodes":
                coverage["projected_nodes"] = (coverage["text_mcids"]
                                               + bad["source_units"] + bad["brand_text_units"])
                coverage["native_nodes"] = (coverage["projected_nodes"]
                                            + coverage["accounted_brand_wrapper_nodes"])
            elif case == "too-many-pages":
                bad["page_count"] = coverage["text_mcids"] + 1
                bad["raster_inspected_pages"] = bad["page_count"]
                window = self.m.RASTER_PAGES_PER_WINDOW
                bad["raster_window_count"] = (bad["page_count"] + window - 1) // window
            else:
                coverage["operations"] = 7 * coverage["text_mcids"] + 543 - 1
            self.m._schema(bad)
            payload = self.m.canonical(bad)
            with self.subTest(case=case, operation="constructor"):
                with self.assertRaises(ValueError):
                    self.m.TypedOriginalBrandQA(payload)
            with self.subTest(case=case, operation="resealed-to-dict"):
                receipt = self.m.TypedOriginalBrandQA(self.m.canonical(source))
                object.__setattr__(receipt, "_payload", payload)
                object.__setattr__(receipt, "_sealed_sha256", sha256(payload).hexdigest())
                with self.assertRaises(ValueError):
                    receipt.to_dict()

        boundary = deepcopy(source)
        coverage = boundary["coverage"]
        limit = self.m.prior.MAX_VISITS
        coverage["native_nodes"] = (limit + 1 - coverage["native_mcids"]) // 2
        coverage["projected_nodes"] = (coverage["native_nodes"]
                                       - coverage["accounted_brand_wrapper_nodes"])
        charge = 2 * coverage["native_nodes"] - 1 + coverage["native_mcids"]
        adjustment = limit - charge
        self.assertIn(adjustment, (0, 1))
        coverage["native_mcids"] += adjustment
        coverage["text_mcids"] += adjustment
        self.assertEqual(2 * coverage["native_nodes"] - 1 + coverage["native_mcids"], limit)
        self.m._schema(boundary)
        self.assertEqual(self.m.TypedOriginalBrandQA(
            self.m.canonical(boundary)).to_dict(), boundary)
        bad = deepcopy(boundary)
        coverage = bad["coverage"]
        coverage["native_mcids"] += 1
        coverage["text_mcids"] += 1
        self.assertEqual(2 * coverage["native_nodes"] - 1 + coverage["native_mcids"], limit + 1)
        self.assertEqual(coverage["native_nodes"], coverage["projected_nodes"]
                         + coverage["accounted_brand_wrapper_nodes"])
        self.assertEqual(coverage["native_mcids"], coverage["text_mcids"]
                         + coverage["figure_count"])
        text_units = bad["source_units"] + bad["brand_text_units"]
        self.assertGreaterEqual(bad["source_units"],
                                bad["represented_findings"] + 3 if bad["represented_findings"] else 1)
        self.assertGreaterEqual(bad["pdf_byte_count"], 8)
        self.assertGreaterEqual(coverage["text_mcids"], text_units)
        self.assertGreaterEqual(coverage["projected_nodes"],
                                coverage["text_mcids"] + text_units + 1)
        self.assertGreaterEqual(coverage["text_mcids"], bad["page_count"])
        self.assertGreaterEqual(coverage["operations"], 7 * coverage["text_mcids"] + 543)
        self.m._schema(bad)
        payload = self.m.canonical(bad)
        with self.subTest(graph_visit_budget="constructor"):
            with self.assertRaises(ValueError):
                self.m.TypedOriginalBrandQA(payload)
        with self.subTest(graph_visit_budget="resealed-to-dict"):
            receipt = self.m.TypedOriginalBrandQA(self.m.canonical(boundary))
            object.__setattr__(receipt, "_payload", payload)
            object.__setattr__(receipt, "_sealed_sha256", sha256(payload).hexdigest())
            with self.assertRaises(ValueError):
                receipt.to_dict()
        self.assertGreaterEqual(
            bad["coverage"]["projected_nodes"],
            bad["coverage"]["text_mcids"] + text_units + 1
            + (bad["represented_findings"] + 4 if bad["represented_findings"] else 0))
        for findings in (0, 1, 2, 96):
            exact = deepcopy(source)
            exact["represented_findings"] = findings
            exact["source_units"] = findings + 3 if findings else 1
            exact["page_count"] = exact["raster_inspected_pages"] = exact["raster_window_count"] = 1
            coverage = exact["coverage"]
            text_units = exact["source_units"] + exact["brand_text_units"]
            coverage["text_mcids"] = text_units
            coverage["native_mcids"] = text_units + coverage["figure_count"]
            # Each source/brand unit has a text container and at least one Span;
            # findings additionally require their body TRs and four table wrappers.
            base_nodes = coverage["text_mcids"] + text_units + 1
            table_nodes = findings + 4 if findings else 0
            coverage["projected_nodes"] = base_nodes + table_nodes
            coverage["native_nodes"] = (coverage["projected_nodes"]
                                        + coverage["accounted_brand_wrapper_nodes"])
            coverage["operations"] = 7 * coverage["text_mcids"] + self.m.MIN_FIXED_BRAND_OPERATIONS
            self.m._schema(exact)
            with self.subTest(findings=findings, node_bound="exact-minimum"):
                self.assertEqual(self.m.TypedOriginalBrandQA(
                    self.m.canonical(exact)).to_dict(), exact)
            lower = [("one-below-minimum", base_nodes + table_nodes - 1)]
            if findings:
                lower.append(("four-table-wrappers-without-finding-rows", base_nodes + 4))
            for mode, projected_nodes in lower:
                bad = deepcopy(exact)
                coverage = bad["coverage"]
                coverage["projected_nodes"] = projected_nodes
                coverage["native_nodes"] = (projected_nodes
                                            + coverage["accounted_brand_wrapper_nodes"])
                self.assertLess(projected_nodes, base_nodes + table_nodes)
                self.assertEqual(coverage["native_mcids"], coverage["text_mcids"] + 1)
                self.assertEqual(coverage["operations"],
                                 7 * coverage["text_mcids"] + self.m.MIN_FIXED_BRAND_OPERATIONS)
                self.assertLessEqual(2 * coverage["native_nodes"] - 1
                                     + coverage["native_mcids"], self.m.prior.MAX_VISITS)
                self.m._schema(bad)
                payload = self.m.canonical(bad)
                with self.subTest(findings=findings, node_bound=mode, boundary="constructor"):
                    with self.assertRaises(ValueError):
                        self.m.TypedOriginalBrandQA(payload)
                with self.subTest(findings=findings, node_bound=mode, boundary="resealed-to-dict"):
                    receipt = self.m.TypedOriginalBrandQA(self.m.canonical(exact))
                    object.__setattr__(receipt, "_payload", payload)
                    object.__setattr__(receipt, "_sealed_sha256", sha256(payload).hexdigest())
                    with self.assertRaises(ValueError):
                        receipt.to_dict()

    def test_receipt_window_count_matches_four_page_boundaries(self):
        source = self.run_qa().to_dict()
        for pages, windows, wrong_windows in ((4, 1, 2), (5, 2, 1)):
            valid = dict(source, page_count=pages, raster_inspected_pages=pages,
                         raster_window_count=windows)
            with self.subTest(page_count=pages, operation="valid"):
                self.assertEqual(self.m.TypedOriginalBrandQA(
                    self.m.canonical(valid)).to_dict()["raster_window_count"], windows)
            bad = dict(valid, raster_window_count=wrong_windows)
            self.m._schema(bad)
            self.assertEqual(bad["raster_inspected_pages"], bad["page_count"])
            self.assertEqual(bad["stages"]["all_page_raster"], "PASS")
            payload = self.m.canonical(bad)
            with self.subTest(page_count=pages, operation="constructor"):
                with self.assertRaises(ValueError):
                    self.m.TypedOriginalBrandQA(payload)
            with self.subTest(page_count=pages, operation="resealed-to-dict"):
                receipt = self.m.TypedOriginalBrandQA(self.m.canonical(valid))
                object.__setattr__(receipt, "_payload", payload)
                object.__setattr__(receipt, "_sealed_sha256", sha256(payload).hexdigest())
                with self.assertRaises(ValueError):
                    receipt.to_dict()
        for counter in ("native_nodes", "native_mcids"):
            bad = deepcopy(source)
            bad["coverage"][counter] += 1
            self.m._schema(bad)
            payload = self.m.canonical(bad)
            with self.subTest(coverage=counter, operation="constructor"):
                with self.assertRaises(ValueError):
                    self.m.TypedOriginalBrandQA(payload)
            with self.subTest(coverage=counter, operation="resealed-to-dict"):
                receipt = self.m.TypedOriginalBrandQA(self.m.canonical(source))
                object.__setattr__(receipt, "_payload", payload)
                object.__setattr__(receipt, "_sealed_sha256", sha256(payload).hexdigest())
                with self.assertRaises(ValueError):
                    receipt.to_dict()

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
        args = self.arguments()
        response = self.m.canonical(self.native_record())
        self.m._schema(self.m.prior._loads(response.decode("ascii")), "native")
        process = object()
        def complete_output(command, **kwargs):
            output_fd = kwargs["pass_fds"][1]
            self.assertEqual(command[-2], str(output_fd))
            self.assertEqual(os.write(output_fd, response), len(response))
            return process
        for code in (0, 70):
            with self.subTest(exit_code=code), \
                    patch.object(self.m.subprocess, "Popen", side_effect=complete_output) as launch, \
                    patch.object(self.m, "wait_owned_process_group", return_value=code) as wait:
                if code == 0:
                    self.assertEqual(self.m._run_worker(args["pdf_bytes"], args["expected_pdf_sha256"]),
                                     response)
                else:
                    with self.assertRaises(ValueError):
                        self.m._run_worker(args["pdf_bytes"], args["expected_pdf_sha256"])
                launch.assert_called_once()
                wait.assert_called_once_with(process, wall_seconds=10, cleanup_seconds=2)

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
                order = []
                landlock.side_effect = lambda *_args, **_kwargs: order.append("landlock")
                network.side_effect = lambda *_args, **_kwargs: order.append("network")
                selected = landlock if guard == "enforce_pdf_landlock" else network
                def refused(*_args, **_kwargs):
                    order.append("landlock" if guard == "enforce_pdf_landlock" else "network")
                    raise ValueError("guard refused")
                selected.side_effect = refused
                code = worker.main(["worker", str(source.fileno()), str(output.fileno()), str(os.getpid())])
                self.assertEqual(code, 70)
                selected.assert_called_once()
                read.assert_not_called()
                write.assert_not_called()
                if guard == "enforce_pdf_landlock":
                    network.assert_not_called()
                    self.assertEqual(order, ["landlock"])
                else:
                    landlock.assert_called_once()
                    self.assertEqual(order, ["landlock", "network"])

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

    def test_graphics_state_changes_precede_open_paths(self):
        def change(writer, context, after_path):
            def alter(ops):
                self.assertEqual(ops[-1][1], b"Q")
                rectangle = ([NumberObject(v) for v in (100, 200, 10, 10)], b"re")
                state = (([NumberObject(1)], b"w") if context == "artifact" else
                         ([NumberObject(v) for v in (1, 0, 0, 1, 0, 0)], b"cm"))
                body = [rectangle, state] if after_path else [state, rectangle]
                if context == "artifact":
                    payload = [
                        ([NameObject("/Artifact")], b"BMC"), ([], b"q"),
                        *body, ([], b"S"), ([], b"Q"), ([], b"EMC")]
                else:
                    payload = [([], b"q"), *body, ([], b"n"), ([], b"Q")]
                ops[-1:-1] = payload
            self.edit_page(writer, 0, alter)
        for context in ("artifact", "outside"):
            with self.subTest(context=context, position="before-path"):
                control = self.rewritten(lambda writer: change(writer, context, False))
                self.assertEqual(self.run_qa(control).to_dict()["figure_count"], 1)
            with self.subTest(context=context, position="after-path"):
                self.refuse(self.rewritten(lambda writer: change(writer, context, True)))

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
        target = ROOT / "reporting/pdf-typed-original-brand-qa.v1.json"
        manifest = json.loads(read_bytes(target).decode("ascii"))
        for case in ("missing", "wrong"):
            invalid = deepcopy(manifest)
            if case == "missing":
                invalid.pop("table_identifier_policy", None)
            else:
                invalid["table_identifier_policy"] = "UNSUPPORTED_IDENTIFIER_POLICY"
            replacement = json.dumps(invalid, sort_keys=True).encode("ascii")
            def changed_manifest(path):
                return replacement if path == target else read_bytes(path)
            with self.subTest(identifier_policy=case), patch.object(
                    Path, "read_bytes", changed_manifest):
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


    def test_native_cmap_aggregate_budgets_count_shared_font_records(self):
        def shared_fonts(writer):
            resources = writer.pages[0]["/Resources"]["/Font"]
            source = next(iter(resources.values())).get_object()
            for index in range(4):
                name = NameObject("/SharedBudgetFont" + str(index))
                self.assertNotIn(name, resources)
                resources[name] = writer._add_object(DictionaryObject(dict(source)))
        args = self.rewritten(shared_fonts)
        raw = self.m._run_worker(args["pdf_bytes"], args["expected_pdf_sha256"])
        original = self.m.prior._loads(raw.decode("ascii"))
        self.m._validate_native(original, args["pdf_bytes"], args["expected_profile_sha256"])

        def groups(native, key):
            result = {}
            for font in native["trace"]["fonts"]:
                result.setdefault(tuple(font[key]), []).append(font)
            return result

        initial = groups(original, "cmap_reference")
        shared = next(key for key, members in initial.items() if len(members) >= 5)
        single = next(key for key, members in initial.items() if len(members) == 1)
        self.assertLess(len(initial), len(original["trace"]["fonts"]))

        def pad_mapping(members, size):
            mapping = deepcopy(members[0]["mapping"])
            used = {code for code, _ in mapping}
            self.assertTrue(len(mapping) <= size <= 4096)
            for number in range(65536):
                if len(mapping) == size:
                    break
                code = format(number, "04x")
                if code not in used:
                    mapping.append([code, "Z"])
                    used.add(code)
            self.assertEqual(len(mapping), size)
            for font in members:
                font["mapping"] = deepcopy(mapping)

        def cover_widths(native):
            for members in groups(native, "cid_reference").values():
                width_count = max(max(font["explicit_cid_width_count"], len(font["mapping"]))
                                  for font in members)
                self.assertLessEqual(width_count, 4096)
                for font in members:
                    font["explicit_cid_width_count"] = width_count

        def unchanged_replay(native):
            self.assertEqual(native["trace"]["pages"], original["trace"]["pages"])
            self.assertEqual(native["trace"]["nodes"], original["trace"]["nodes"])
            self.assertEqual(native["graphics"], original["graphics"])
            self.m._schema(native, "native")
            self.m.prior._reference_consistency(native["trace"])
            projected, _ = self.m.project_text_trace(native["trace"])
            fonts = {self.m.prior._reference(font["reference"]): font
                     for font in projected["fonts"]}
            for font in fonts.values():
                self.assertEqual(len(dict(font["mapping"])), len(font["mapping"]))
                self.assertLessEqual(len(font["mapping"]), font["explicit_cid_width_count"])
                self.assertTrue(0 < font["cmap_decoded_bytes"] <= self.m.prior.MAX_CMAP_BYTES)
            for page in projected["pages"]:
                self.assertEqual(self.m.prior._replay(page, fonts), page["runs"])

        for budget, limit in (("bytes", self.m.prior.MAX_CMAP_TOTAL),
                              ("records", self.m.prior.MAX_CMAP_RECORDS)):
            with self.subTest(budget=budget):
                boundary = deepcopy(original)
                by_cmap = groups(boundary, "cmap_reference")
                measure = (lambda font: font["cmap_decoded_bytes"]) if budget == "bytes" else (
                    lambda font: len(font["mapping"]))
                other = sum(measure(font) for key, members in by_cmap.items()
                            if key != shared for font in members)
                amount, remainder = divmod(limit - other, len(by_cmap[shared]))
                if budget == "bytes":
                    for font in by_cmap[shared]:
                        font["cmap_decoded_bytes"] = amount
                    by_cmap[single][0]["cmap_decoded_bytes"] += remainder
                else:
                    pad_mapping(by_cmap[shared], amount)
                    pad_mapping(by_cmap[single], measure(by_cmap[single][0]) + remainder)
                    cover_widths(boundary)
                self.assertEqual(sum(measure(font) for font in boundary["trace"]["fonts"]), limit)
                unchanged_replay(boundary)
                self.m._validate_native(boundary, args["pdf_bytes"], args["expected_profile_sha256"])

                bad = deepcopy(boundary)
                by_cmap = groups(bad, "cmap_reference")
                if budget == "bytes":
                    by_cmap[single][0]["cmap_decoded_bytes"] += 1
                else:
                    pad_mapping(by_cmap[single], measure(by_cmap[single][0]) + 1)
                    cover_widths(bad)
                self.assertEqual(sum(measure(font) for font in bad["trace"]["fonts"]), limit + 1)
                # Counting shared objects only once would incorrectly admit this packet.
                self.assertLessEqual(sum(measure(members[0]) for members in by_cmap.values()), limit)
                if budget == "bytes":
                    self.assertLessEqual(sum(len(font["mapping"]) for font in bad["trace"]["fonts"]),
                                         self.m.prior.MAX_CMAP_RECORDS)
                else:
                    self.assertLessEqual(sum(font["cmap_decoded_bytes"]
                                             for font in bad["trace"]["fonts"]),
                                         self.m.prior.MAX_CMAP_TOTAL)
                unchanged_replay(bad)
                with self.assertRaises(ValueError):
                    self.m._validate_native(bad, args["pdf_bytes"], args["expected_profile_sha256"])
                payload = self.m.canonical(bad)
                self.assertLessEqual(len(payload), self.m.prior.RESULT_BYTES)
                with patch.object(self.m, "_run_worker", return_value=payload) as worker, patch.object(
                        self.m, "inspect_pdf_document_metadata") as metadata:
                    self.refuse(args)
                    worker.assert_called_once_with(args["pdf_bytes"], args["expected_pdf_sha256"])
                    metadata.assert_not_called()

    def test_native_font_mapping_requires_widths_and_utf16_units(self):
        args = self.arguments()
        original = self.native_record()
        self.m._validate_native(original, args["pdf_bytes"], args["expected_profile_sha256"])

        def unchanged_replay(native):
            self.assertEqual(native["trace"]["pages"], original["trace"]["pages"])
            self.assertEqual(native["trace"]["nodes"], original["trace"]["nodes"])
            self.assertEqual(native["graphics"], original["graphics"])
            self.m._schema(native, "native")
            self.m.prior._reference_consistency(native["trace"])
            projected, _ = self.m.project_text_trace(native["trace"])
            fonts = {self.m.prior._reference(font["reference"]): font
                     for font in projected["fonts"]}
            self.assertLessEqual(sum(len(font["mapping"]) for font in fonts.values()),
                                 self.m.prior.MAX_CMAP_RECORDS)
            self.assertLessEqual(sum(font["cmap_decoded_bytes"] for font in fonts.values()),
                                 self.m.prior.MAX_CMAP_TOTAL)
            for font in fonts.values():
                self.assertEqual(len(dict(font["mapping"])), len(font["mapping"]))
            for page in projected["pages"]:
                self.assertEqual(self.m.prior._replay(page, fonts), page["runs"])

        def refuse_native(native):
            unchanged_replay(native)
            with self.assertRaises(ValueError):
                self.m._validate_native(native, args["pdf_bytes"], args["expected_profile_sha256"])
            with patch.object(self.m, "_run_worker", return_value=self.m.canonical(native)) as worker, (
                    patch.object(self.m, "inspect_pdf_document_metadata")) as metadata:
                self.refuse(args)
                worker.assert_called_once_with(args["pdf_bytes"], args["expected_pdf_sha256"])
                metadata.assert_not_called()

        largest = max(original["trace"]["fonts"], key=lambda font: len(font["mapping"]))
        cid = largest["cid_reference"]
        width_count = len(largest["mapping"])
        self.assertGreater(width_count, 1)
        boundary = deepcopy(original)
        for font in boundary["trace"]["fonts"]:
            if font["cid_reference"] == cid:
                font["explicit_cid_width_count"] = width_count
        unchanged_replay(boundary)
        self.m._validate_native(boundary, args["pdf_bytes"], args["expected_profile_sha256"])
        bad = deepcopy(boundary)
        for font in bad["trace"]["fonts"]:
            if font["cid_reference"] == cid:
                font["explicit_cid_width_count"] = width_count - 1
        with self.subTest(mapping="width-count-one-below"):
            refuse_native(bad)

        cmap = largest["cmap_reference"]
        known = {code for font in original["trace"]["fonts"] if font["cmap_reference"] == cmap
                 for code, _ in font["mapping"]}
        unused = next(format(number, "04x") for number in range(65536)
                      if format(number, "04x") not in known)
        affected = {tuple(font["reference"]) for font in original["trace"]["fonts"]
                    if font["cmap_reference"] == cmap}
        used = {value[offset:offset + 4] for page in original["trace"]["pages"]
                for run in page["runs"] if tuple(run["font_reference"]) in affected
                for kind, value in run["parts"] if kind == "codes"
                for offset in range(0, len(value), 4)}
        self.assertNotIn(unused, used)

        def unused_mapping(text):
            native = deepcopy(original)
            cid_groups = {}
            for font in native["trace"]["fonts"]:
                if font["cmap_reference"] == cmap:
                    font["mapping"].append([unused, text])
                cid_groups.setdefault(tuple(font["cid_reference"]), []).append(font)
            for members in cid_groups.values():
                width = max(max(font["explicit_cid_width_count"], len(font["mapping"]))
                            for font in members)
                self.assertLessEqual(width, 4096)
                for font in members:
                    font["explicit_cid_width_count"] = width
                    self.assertLessEqual(len(font["mapping"]), width)
            return native

        for text in ("Z", "a" * 16, "\U0001f600" * 8):
            with self.subTest(valid_utf16_units=len(text.encode("utf-16-be")) // 2):
                native = unused_mapping(text)
                unchanged_replay(native)
                self.m._validate_native(native, args["pdf_bytes"], args["expected_profile_sha256"])
        overflow = "\U0001f600" * 8 + "a"
        self.assertEqual(len(overflow.encode("utf-16-be")) // 2, 17)
        for label, text in (("seventeen-utf16-units", overflow), ("unpaired-surrogate", "\ud800")):
            self.assertLessEqual(len(text), 16)
            with self.subTest(invalid_unused_mapping=label):
                refuse_native(unused_mapping(text))

    def test_projected_events_must_stay_inside_native_spans(self):
        pairs = {}
        def outside_pairs(writer):
            def alter(operations):
                starts = [index for index, (operands, op) in enumerate(operations)
                          if op == b"BDC" and operands[0] == "/Span"]
                self.assertGreaterEqual(len(starts), 2)
                count = len(operations)
                pairs.update(prefix=0, between=starts[1] + 2, suffix=count + 4)
                for index in (count, starts[1], 0):
                    operations[index:index] = [([], b"q"), ([], b"Q")]
            self.edit_page(writer, 0, alter)
        args = self.rewritten(outside_pairs)
        raw = self.m._run_worker(args["pdf_bytes"], args["expected_pdf_sha256"])
        original = self.m.prior._loads(raw.decode("ascii"))
        self.m._validate_native(original, args["pdf_bytes"], args["expected_profile_sha256"])
        original_page = original["trace"]["pages"][0]
        for first in pairs.values():
            self.assertTrue({first, first + 1}.issubset(original_page["outside_operator_ordinals"]))
        for position, first in pairs.items():
            with self.subTest(projected_outside_pair=position):
                native = deepcopy(original)
                page = native["trace"]["pages"][0]
                index = sum(ordinal < first for ordinal in page["projected_operator_ordinals"])
                if index:
                    self.assertEqual(page["events"][index - 1]["operator"], "EMC")
                if index < len(page["events"]):
                    self.assertEqual(page["events"][index]["operator"], "BDC")
                page["events"][index:index] = [
                    {"ordinal": 0, "operator": "q", "operands": []},
                    {"ordinal": 0, "operator": "Q", "operands": []}]
                for ordinal, event in enumerate(page["events"]):
                    event["ordinal"] = ordinal
                page["projected_operator_ordinals"][index:index] = [first, first + 1]
                page["outside_operator_ordinals"] = [ordinal for ordinal in page["outside_operator_ordinals"]
                                                    if ordinal not in {first, first + 1}]
                for run in page["runs"]:
                    if run["operator_ordinal"] >= index:
                        run["operator_ordinal"] += 2
                mapped = page["projected_operator_ordinals"]
                self.assertEqual(mapped, sorted(set(mapped)))
                starts = [i for i, event in enumerate(page["events"]) if event["operator"] == "BDC"]
                ends = [i for i, event in enumerate(page["events"]) if event["operator"] == "EMC"]
                self.assertEqual(len(starts), len(ends))
                for begin, end in zip(starts, ends, strict=True):
                    self.assertEqual(mapped[begin:end + 1], list(range(mapped[begin], mapped[end] + 1)))
                covered = set(mapped)
                self.assertFalse(covered.intersection(page["outside_operator_ordinals"]))
                covered.update(page["outside_operator_ordinals"])
                for block in page["graphic_blocks"]:
                    interval = set(range(block["first_operator"], block["last_operator"] + 1))
                    self.assertFalse(covered.intersection(interval))
                    covered.update(interval)
                self.assertEqual(covered, set(range(page["original_operator_count"])))
                self.assertEqual(native["trace"]["nodes"], original["trace"]["nodes"])
                self.assertEqual(native["trace"]["fonts"], original["trace"]["fonts"])
                self.assertEqual(native["graphics"], original["graphics"])
                self.assertEqual(page["graphic_blocks"], original_page["graphic_blocks"])
                self.assertEqual(page["mcid_owners"], original_page["mcid_owners"])
                for observed, source in zip(page["runs"], original_page["runs"], strict=True):
                    self.assertEqual({key: value for key, value in observed.items() if key != "operator_ordinal"},
                                     {key: value for key, value in source.items() if key != "operator_ordinal"})
                self.m._schema(native, "native")
                self.m.prior._reference_consistency(native["trace"])
                projected, _ = self.m.project_text_trace(native["trace"])
                fonts = {self.m.prior._reference(font["reference"]): font for font in projected["fonts"]}
                for projected_page in projected["pages"]:
                    self.assertEqual(self.m.prior._replay(projected_page, fonts), projected_page["runs"])
                with self.assertRaises(ValueError):
                    self.m._validate_native(native, args["pdf_bytes"], args["expected_profile_sha256"])
                with patch.object(self.m, "_run_worker", return_value=self.m.canonical(native)) as worker, (
                        patch.object(self.m, "inspect_pdf_document_metadata")) as metadata:
                    self.refuse(args)
                    worker.assert_called_once_with(args["pdf_bytes"], args["expected_pdf_sha256"])
                    metadata.assert_not_called()

    def test_native_graphics_states_require_bounded_opaque_inventory(self):
        from jsonschema.exceptions import ValidationError

        args = self.arguments()
        original = self.native_record()
        self.m._validate_native(original, args["pdf_bytes"], args["expected_profile_sha256"])
        states = [
            {"name": "/InventoryState0", "fill_alpha": "1.0", "stroke_alpha": None},
            {"name": "/InventoryState1", "fill_alpha": None, "stroke_alpha": "1.000"},
            {"name": "/InventoryState2", "fill_alpha": "1", "stroke_alpha": None},
            {"name": "/InventoryState3", "fill_alpha": None, "stroke_alpha": "1"}]

        def inventory(count):
            native = deepcopy(original)
            page = native["trace"]["pages"][0]
            page["graphics_states"] = deepcopy(states[:count])
            for event in page["events"]:
                if event["operator"] == "gs":
                    event["operands"] = [states[0]["name"]]
            return native

        def unchanged_replay(native):
            self.m.prior._reference_consistency(native["trace"])
            projected, _ = self.m.project_text_trace(native["trace"])
            fonts = {self.m.prior._reference(font["reference"]): font for font in projected["fonts"]}
            for page, before in zip(projected["pages"], original["trace"]["pages"], strict=True):
                self.assertEqual(page["runs"], before["runs"])
                self.assertEqual(self.m.prior._replay(page, fonts), page["runs"])
                self.assertEqual(page["projected_operator_ordinals"], before["projected_operator_ordinals"])
                self.assertEqual(page["outside_operator_ordinals"], before["outside_operator_ordinals"])
                self.assertEqual(page["graphic_blocks"], before["graphic_blocks"])
            self.assertEqual(native["trace"]["nodes"], original["trace"]["nodes"])
            self.assertEqual(native["trace"]["fonts"], original["trace"]["fonts"])
            self.assertEqual(native["graphics"], original["graphics"])

        def public_refusal(native):
            with patch.object(self.m, "_run_worker", return_value=self.m.canonical(native)) as worker, (
                    patch.object(self.m, "inspect_pdf_document_metadata")) as metadata:
                self.refuse(args)
                worker.assert_called_once_with(args["pdf_bytes"], args["expected_pdf_sha256"])
                metadata.assert_not_called()

        for count in (1, 3):
            with self.subTest(valid_inventory=count):
                native = inventory(count)
                self.m._schema(native, "native")
                unchanged_replay(native)
                self.m._validate_native(native, args["pdf_bytes"], args["expected_profile_sha256"])
        for count, validator in ((0, "minItems"), (4, "maxItems")):
            with self.subTest(invalid_inventory=count):
                native = inventory(count)
                with self.assertRaises(ValidationError) as caught:
                    self.m._schema(native, "native")
                self.assertEqual(caught.exception.validator, validator)
                self.assertEqual(list(caught.exception.absolute_path),
                                 ["trace", "pages", 0, "graphics_states"])
                with self.assertRaises(ValidationError):
                    self.m._validate_native(native, args["pdf_bytes"], args["expected_profile_sha256"])
                public_refusal(native)
        for label, fill, stroke in (("fractional-fill", "0.5", None),
                                    ("fractional-stroke", None, ".5"),
                                    ("both-alphas-present", "1", "1")):
            with self.subTest(invalid_unused_state=label):
                native = inventory(3)
                unused = native["trace"]["pages"][0]["graphics_states"][2]
                self.assertFalse(any(event["operator"] == "gs" and event["operands"] == [unused["name"]]
                                     for event in native["trace"]["pages"][0]["events"]))
                unused["fill_alpha"], unused["stroke_alpha"] = fill, stroke
                self.m._schema(native, "native")
                unchanged_replay(native)
                with self.assertRaises(ValueError):
                    self.m._validate_native(native, args["pdf_bytes"], args["expected_profile_sha256"])
                public_refusal(native)


if __name__ == "__main__":
    unittest.main()
