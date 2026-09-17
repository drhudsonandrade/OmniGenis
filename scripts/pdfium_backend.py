"""Small PDFium facade for the editorial coordinate compiler.

The coordinate compiler previously consumed a narrow subset of PyMuPDF. This module
implements only that subset on top of pypdfium2/PDFium so the compiler algorithm can
remain stable while the AGPL/commercial MuPDF runtime dependency is removed.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
from typing import Any, Iterable, Iterator

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw

_BASE14_MUPDF_METRICS: dict[str, tuple[float, float]] = {
    "Helvetica": (1.075, -0.299),
    "Helvetica-Bold": (1.070, -0.307),
    "Helvetica-Oblique": (1.070, -0.284),
    "Helvetica-BoldOblique": (1.073, -0.309),
    "Times-Roman": (1.053, -0.281),
    "Times-Bold": (1.044, -0.341),
    "Times-Italic": (0.951, -0.270),
    "Times-BoldItalic": (0.972, -0.324),
    "Courier": (0.932, -0.317),
    "Courier-Bold": (1.007, -0.393),
    "Courier-Oblique": (0.920, -0.317),
    "Courier-BoldOblique": (0.997, -0.393),
    "ZapfDingbats": (0.819, -0.144),
}

@dataclass(frozen=True)
class Point:
    x: float
    y: float


class Rect:
    __slots__ = ("x0", "y0", "x1", "y1")

    def __init__(self, *args: object) -> None:
        if len(args) == 1:
            value = args[0]
            if isinstance(value, Rect):
                coords = (value.x0, value.y0, value.x1, value.y1)
            elif all(hasattr(value, name) for name in ("x0", "y0", "x1", "y1")):
                coords = tuple(float(getattr(value, name)) for name in ("x0", "y0", "x1", "y1"))
            else:
                coords = tuple(float(item) for item in value)  # type: ignore[arg-type]
        elif len(args) == 4:
            coords = tuple(float(item) for item in args)
        else:
            raise TypeError("Rect expects one four-value object or four coordinates")
        if len(coords) != 4:
            raise ValueError("Rect requires four coordinates")
        self.x0, self.y0, self.x1, self.y1 = coords

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def contains(self, point: Point) -> bool:
        return self.x0 <= point.x <= self.x1 and self.y0 <= point.y <= self.y1

    def get_area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    def __and__(self, other: object) -> "Rect":
        right = Rect(other)
        return Rect(
            max(self.x0, right.x0),
            max(self.y0, right.y0),
            min(self.x1, right.x1),
            min(self.y1, right.y1),
        )

    def __iter__(self) -> Iterator[float]:
        return iter((self.x0, self.y0, self.x1, self.y1))


@dataclass(frozen=True)
class Matrix:
    x: float
    y: float


class _RgbColorSpace:
    pass


csRGB = _RgbColorSpace()


@dataclass(frozen=True)
class Pixmap:
    width: int
    height: int
    n: int
    samples: bytes


def _pointer_value(text_object: Any) -> int:
    return int(ctypes.cast(text_object.raw, ctypes.c_void_p).value or 0)


def _top_left_rect(box: Iterable[float], page_height: float) -> Rect:
    left, bottom, right, top = (float(value) for value in box)
    return Rect(left, page_height - top, right, page_height - bottom)


def _union(rects: list[Rect]) -> Rect:
    return Rect(
        min(rect.x0 for rect in rects),
        min(rect.y0 for rect in rects),
        max(rect.x1 for rect in rects),
        max(rect.y1 for rect in rects),
    )


def _legacy_char_rect(text_page: Any, index: int, page_height: float) -> Rect:
    text_object = text_page.get_textobj(index)
    rect = _top_left_rect(text_page.get_charbox(index, loose=True), page_height)
    if text_object is None:
        return rect
    font_name = text_object.get_font().get_base_name()
    metrics = _BASE14_MUPDF_METRICS.get(font_name)
    if metrics is None:
        return rect
    matrix = text_object.get_matrix()
    if abs(float(matrix.b)) > 1e-6 or abs(float(matrix.c)) > 1e-6:
        return rect
    size = float(text_object.get_font_size())
    baseline = page_height - float(matrix.f)
    ascender, descender = metrics
    return Rect(rect.x0, baseline - size * ascender, rect.x1, baseline - size * descender)


def _path_geometry_rect(page_object: Any, page_height: float) -> Rect:
    matrix = page_object.get_matrix()
    points: list[tuple[float, float]] = []
    count = pdfium_raw.FPDFPath_CountSegments(page_object.raw)
    for index in range(count):
        segment = pdfium_raw.FPDFPath_GetPathSegment(page_object.raw, index)
        if not segment:
            continue
        x = ctypes.c_float()
        y = ctypes.c_float()
        if not pdfium_raw.FPDFPathSegment_GetPoint(segment, ctypes.byref(x), ctypes.byref(y)):
            continue
        transformed_x = float(matrix.a) * x.value + float(matrix.c) * y.value + float(matrix.e)
        transformed_y = float(matrix.b) * x.value + float(matrix.d) * y.value + float(matrix.f)
        points.append((transformed_x, transformed_y))
    if not points:
        return _top_left_rect(page_object.get_bounds(), page_height)
    return Rect(
        min(x for x, _ in points),
        page_height - max(y for _, y in points),
        max(x for x, _ in points),
        page_height - min(y for _, y in points),
    )


def _fill_color(text_object: Any) -> int:
    red = ctypes.c_uint()
    green = ctypes.c_uint()
    blue = ctypes.c_uint()
    alpha = ctypes.c_uint()
    ok = pdfium_raw.FPDFPageObj_GetFillColor(
        text_object.raw,
        ctypes.byref(red),
        ctypes.byref(green),
        ctypes.byref(blue),
        ctypes.byref(alpha),
    )
    if not ok:
        return 0
    return (red.value << 16) | (green.value << 8) | blue.value


def _text_spans(page: Any) -> list[dict[str, Any]]:
    """Return content-order text spans using PDFium loose character boxes.

    Loose boxes match the historical MuPDF span geometry to sub-millipoint precision on
    the SHA-pinned v3.0 template suite. A span is split when one PDF text object moves to
    another visual line/column or contains a large internal gap.
    """
    text_page = page.get_textpage()
    page_height = float(page.get_height())
    out: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    previous_box: tuple[float, float, float, float] | None = None
    previous_pointer: int | None = None
    previous_size: float | None = None

    def flush() -> None:
        nonlocal current
        if current and current["chars"] and current["boxes"]:
            text_object = current["object"]
            out.append(
                {
                    "text": "".join(current["chars"]),
                    "bbox": _union(
                        [_top_left_rect(box, page_height) for box in current["boxes"]]
                    ),
                    "size": float(text_object.get_font_size()),
                    "font": text_object.get_font().get_base_name(),
                    "color": _fill_color(text_object),
                }
            )
        current = None

    try:
        for index in range(text_page.count_chars()):
            text_object = text_page.get_textobj(index)
            character = text_page.get_text_range(index, 1)
            if text_object is None or character in ("\r", "\n"):
                flush()
                previous_box = None
                previous_pointer = None
                previous_size = None
                continue
            try:
                legacy_rect = _legacy_char_rect(text_page, index, page_height)
                box = (legacy_rect.x0, page_height - legacy_rect.y1, legacy_rect.x1, page_height - legacy_rect.y0)
            except Exception:
                continue
            pointer = _pointer_value(text_object)
            size = float(text_object.get_font_size())
            discontinuity = False
            if current is not None:
                if pointer != previous_pointer:
                    discontinuity = True
                elif previous_box is not None:
                    previous_left, previous_bottom, previous_right, previous_top = previous_box
                    left, bottom, right, top = box
                    vertical_center_delta = abs(
                        ((bottom + top) - (previous_bottom + previous_top)) / 2.0
                    )
                    horizontal_gap = left - previous_right
                    backward_jump = previous_left - left
                    tolerance = max(size, previous_size or size, 1.0)
                    if (
                        vertical_center_delta > 0.55 * tolerance
                        or horizontal_gap > max(4.0, 0.8 * tolerance)
                        or backward_jump > 0.5 * tolerance
                    ):
                        discontinuity = True
            if discontinuity:
                flush()
            if current is None:
                current = {"object": text_object, "chars": [], "boxes": []}
            current["chars"].append(character)
            current["boxes"].append(box)
            previous_box = box
            previous_pointer = pointer
            previous_size = size
        flush()
    finally:
        text_page.close()
    return out


class Page:
    def __init__(self, page: Any) -> None:
        self._page = page

    @property
    def rect(self) -> Rect:
        width, height = self._page.get_size()
        return Rect(0.0, 0.0, float(width), float(height))

    def get_text(self, mode: str, *, sort: bool = False) -> dict[str, Any]:
        if mode != "dict":
            raise ValueError("PDFium facade supports only dictionary text extraction")
        del sort
        spans = _text_spans(self._page)
        return {
            "blocks": [
                {"lines": [{"spans": [{**span, "bbox": tuple(span["bbox"])}]}]}
                for span in spans
            ]
        }

    def get_pixmap(
        self,
        *,
        matrix: Matrix,
        alpha: bool = False,
        colorspace: object = csRGB,
    ) -> Pixmap:
        if alpha or colorspace is not csRGB or matrix.x != matrix.y:
            raise ValueError("unsupported PDFium facade pixmap request")
        bitmap = self._page.render(scale=float(matrix.x), rotation=0, rev_byteorder=True)
        image = bitmap.to_pil().convert("RGB")
        return Pixmap(image.width, image.height, 3, image.tobytes())

    def get_drawings(self) -> list[dict[str, Rect]]:
        page_height = float(self._page.get_height())
        drawings: list[dict[str, Rect]] = []
        for page_object in self._page.get_objects(filter=[pdfium_raw.FPDF_PAGEOBJ_PATH]):
            try:
                drawings.append({"rect": _path_geometry_rect(page_object, page_height)})
            except Exception:
                continue
        return drawings

    def search_for(self, text: str) -> list[Rect]:
        text_page = self._page.get_textpage()
        page_height = float(self._page.get_height())
        result: list[Rect] = []
        try:
            searcher = text_page.search(
                text,
                match_case=True,
                match_whole_word=False,
                consecutive=True,
            )
            while True:
                hit = searcher.get_next()
                if not hit:
                    break
                start, count = hit
                boxes: list[Rect] = []
                for index in range(start, start + count):
                    try:
                        boxes.append(
                            _legacy_char_rect(text_page, index, page_height)
                        )
                    except Exception:
                        continue
                if boxes:
                    result.append(_union(boxes))
        finally:
            text_page.close()
        return result

    def close(self) -> None:
        self._page.close()


class Document:
    def __init__(self, source: object) -> None:
        self._document = pdfium.PdfDocument(source)

    def __len__(self) -> int:
        return len(self._document)

    def __iter__(self) -> Iterator[Page]:
        for index in range(len(self._document)):
            yield Page(self._document[index])

    def __getitem__(self, index: int) -> Page:
        return Page(self._document[index])

    def close(self) -> None:
        self._document.close()


def open(source: object) -> Document:
    return Document(source)
