"""Exact admitted SVG path/paint geometry observed in a synthetic native PDF.

This independent opt-in regression checks decoded vector drawing operators,
not a screenshot, generic SVG input, PDF/UA, release or clinical suitability.
The existing renderer, asset admission, and all default paths remain unchanged.
"""
from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import os
import re
import unittest
import xml.etree.ElementTree as ET

from pypdf import PdfReader
from pypdf.generic import ContentStream

from reporting.brand_assets import BoundBrandAssets
from reporting.pdf_original_brand_pagination_composite import (
    render_original_brand_paginated_candidate,
)
from tests.test_reporting_pdf_bound_candidate import bound_request
from tests.test_reporting_pdf_original_brand_pagination_composite import long_bound_request

ASSETS = Path(__file__).resolve().parents[1] / "reporting/assets"
SVG = "{http://www.w3.org/2000/svg}"
TOKEN = re.compile(r"[MLZ]|[+-]?(?:\d+(?:\.\d+)?|\.\d+)", re.ASCII)
TRANSFORM = re.compile(
    r"translate\(([+-]?\d+(?:\.\d+)?) ([+-]?\d+(?:\.\d+)?)\) "
    r"scale\(([+-]?\d+(?:\.\d+)?)\)\Z", re.ASCII,
)
ZERO = Decimal(0)


def _source_geometry(svg: bytes):
    root = ET.fromstring(svg)
    assert root.tag == SVG + "svg" and root.attrib["viewBox"] == "0 0 1054 340"
    parents = {child: parent for parent in root.iter() for child in parent}
    paths = [item for item in root.iter() if item.tag == SVG + "path"]
    assert len(paths) == 6
    drawing = []
    paints = []
    fills = []
    for path in paths:
        commands = path.attrib["d"]
        tokens = TOKEN.findall(commands)
        assert "".join(tokens) == re.sub(r"[\s,]", "", commands)
        drawing.append((b"m", (ZERO, ZERO)))  # WeasyPrint path start.
        cursor = 0
        while cursor < len(tokens):
            action = tokens[cursor]
            assert action in {"M", "L", "Z"}
            if action == "Z":
                drawing.append((b"h", ()))
                cursor += 1
                continue
            assert cursor + 2 < len(tokens)
            drawing.append((b"m" if action == "M" else b"l", (
                Decimal(tokens[cursor + 1]), Decimal(tokens[cursor + 2]))))
            cursor += 3
        hex_color = path.attrib["fill"]
        assert re.fullmatch(r"#[0-9A-Fa-f]{6}", hex_color)
        paints.append(tuple((Decimal(int(hex_color[start:start + 2], 16)) / 255).quantize(
            Decimal("0.000001")) for start in (1, 3, 5)))
        rule = path.attrib.get("fill-rule", parents[path].attrib.get("fill-rule", "nonzero"))
        assert rule in ("evenodd", "nonzero")
        fills.append(b"f*" if rule == "evenodd" else b"f")
    groups = [item for item in root.iter() if item.tag == SVG + "g"]
    assert len(groups) == 2
    matrices = []
    for group in groups:
        transform = TRANSFORM.fullmatch(group.attrib["transform"])
        assert transform is not None
        x, y, scale = map(Decimal, transform.groups())
        matrices.append((scale, ZERO, ZERO, scale, x, y))
    assert len(drawing) == 506 and len(paints) == len(fills) == 6
    return drawing, paints, fills, matrices


def _native_figure(reader: PdfReader):
    located = []
    for page_index, page in enumerate(reader.pages):
        operations = ContentStream(page.get_contents(), reader, forced_encoding="bytes").operations
        figure = None
        for args, op in operations:
            if op == b"BDC" and args and str(args[0]) == "/Figure":
                assert figure is None
                figure = []
                continue
            if figure is not None:
                if op == b"EMC":
                    located.append((page_index, figure))
                    figure = None
                else:
                    assert op not in (b"BDC", b"BMC")
                    figure.append((op, args))
        assert figure is None
    assert len(located) == 1
    return located[0]


def _vector_parts(operations):
    geometry = [(op, tuple(Decimal(str(n)) for n in args))
                for op, args in operations if op in (b"m", b"l", b"h")]
    colors = [tuple(Decimal(str(n)) for n in args)
              for op, args in operations if op == b"rg"]
    fills = [op for op, _ in operations if op in (b"f", b"f*")]
    matrices = [tuple(Decimal(str(n)) for n in args)
                for op, args in operations if op == b"cm"]
    forbidden = {b"c", b"v", b"y", b"re", b"S", b"s", b"B", b"B*", b"b", b"b*", b"W", b"W*"}
    assert not any(op in forbidden for op, _ in operations)
    return geometry, colors, fills, matrices


def _assert_source_native_match(svg, native_operations):
    expected_geometry, expected_paints, expected_fills, group_matrices = _source_geometry(svg)
    geometry, colors, fills, matrices = _vector_parts(native_operations)
    assert geometry == expected_geometry, "original SVG paths do not match native PDF paths"
    assert colors == expected_paints, "original SVG fills do not match native PDF paints"
    assert fills == expected_fills, "original SVG fill rules do not match PDF operators"
    assert len(matrices) == 5 and matrices[-2:] == group_matrices, "original SVG transforms changed"


class OriginalLogoNativeVectorGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assets = BoundBrandAssets(
            logo_svg=(ASSETS / "omnigenis_logo.svg").read_bytes(),
            dna_png=(ASSETS / "dna_column.png").read_bytes(),
            permission_notice=(ASSETS / "brand-permission.md").read_bytes())
        cls.bundle_sha = cls.assets.to_dict()["bundle_sha256"]

    def test_source_geometry_reaches_native_pdf_and_mutation_is_rejected(self):
        with patch.dict(os.environ, {"OMNIGENIS_PDF_QUALIFICATION_RECEIPT_MODE": "qualification-only"}):
            candidate = render_original_brand_paginated_candidate(
                bound_request=bound_request(), assets=self.assets,
                expected_brand_bundle_sha256=self.bundle_sha)
        reader = PdfReader(BytesIO(candidate.pdf_bytes), strict=True)
        page_index, figure = _native_figure(reader)
        self.assertGreater(page_index, 0)
        _assert_source_native_match(self.assets.logo_svg, figure)
        self.assertIs(candidate.summary()["release_authorized"], False)
        changed = list(figure)
        for index, (op, args) in enumerate(changed):
            if op == b"l":
                replacement = list(args)
                replacement[0] = Decimal(str(replacement[0])) + 1
                changed[index] = (op, replacement)
                break
        else:
            self.fail("expected original vector line segments")
        with self.assertRaisesRegex(AssertionError, "paths do not match"):
            _assert_source_native_match(self.assets.logo_svg, changed)

    def test_long_source_preserves_exact_native_logo_geometry(self):
        request, _ = long_bound_request()
        with patch.dict(os.environ, {"OMNIGENIS_PDF_QUALIFICATION_RECEIPT_MODE": "qualification-only"}):
            candidate = render_original_brand_paginated_candidate(
                bound_request=request, assets=self.assets,
                expected_brand_bundle_sha256=self.bundle_sha)
        self.assertGreater(candidate.page_count, 32)
        reader = PdfReader(BytesIO(candidate.pdf_bytes), strict=True)
        _, figure = _native_figure(reader)
        _assert_source_native_match(self.assets.logo_svg, figure)
        self.assertIs(candidate.summary()["human_assistive_accessibility_verified"], False)


if __name__ == "__main__":
    unittest.main()
