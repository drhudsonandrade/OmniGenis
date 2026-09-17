#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import binascii
import gzip
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from scripts import pdfium_backend as fitz

COMPILER_ID = "pypdfium2-5.13.0-pdfium-genoma-v3"
TOKEN_RE = re.compile(r"\[\[.*?\]\]", re.S)
RULESET_CONTROL_RE = re.compile(r"GENOMA-RULESET-v\d+(?:\.\d+)+")
RULESET_CONTROL_PREFIX = "GENOMA-RULESET-v"
CANONICAL_RULESET_CONTROL = "GENOMA-RULESET-v3.4"
LEGACY_RULESET_CONTROL_SHA256 = "e9d2e43c9c775b9bef05e7d33cd18ada3ef9e1e4bbfeda4db2617de1ab75cd97"
LEGACY_RULESET_CONTROL_LENGTH = 26
LEGACY_REPORT_01_PAGE_06_COMBINED_SHA256 = "62c0f06abd1ef8e375e57f76ca0981f1f67f32de11f9fc40572ef24cf06b8e84"
LEGACY_REPORT_01_PAGE_06_CALIBRATION_SHA256 = "cd2d915aa51fde7e065b0985f0dec759bfc9f1000430a4f841143694c9496578"
LEGACY_REPORT_01_PAGE_06_CALIBRATION_LENGTH = 29
LEGACY_FIELD_ALIAS_REGISTRY = (
    Path(__file__).resolve().parents[1] / "reporting" / "legacy_field_aliases.json"
)
LEGACY_FIELD_ALIAS_SCHEMA = "omnigenis-legacy-template-field-aliases-v1"
CONTROLLED = [
    "MODELO REUTILIZÁVEL v3.0",
    "MODELO EDITÁVEL",
    "NÃO INSERIDOS",
    "MODELO — NÃO É RESULTADO GENÉTICO",
    "MODELO — NÃO É RESULTADO",
    "MODELO SEM DADOS PESSOAIS",
    "Campos em azul são placeholders obrigatórios ou condicionais; preencher com dado rastreável ou declarar NÃO DISPONÍVEL.",
]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _rgbhex(color: int) -> str:
    return f"#{color & 0xFFFFFF:06X}"


def _load_legacy_field_aliases(path: Path = LEGACY_FIELD_ALIAS_REGISTRY) -> dict[str, tuple[str, ...]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("legacy field alias registry is unavailable or invalid") from exc
    if payload.get("schema") != LEGACY_FIELD_ALIAS_SCHEMA:
        raise RuntimeError("legacy field alias registry schema mismatch")
    records = payload.get("aliases")
    if not isinstance(records, list):
        raise RuntimeError("legacy field alias registry aliases must be a list")
    result: dict[str, tuple[str, ...]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise RuntimeError("legacy field alias registry entry must be an object")
        current_digest = record.get("current_field_id_sha256")
        legacy_digest = record.get("legacy_field_id_sha256")
        encoded = record.get("legacy_field_id_utf8_b64")
        if not isinstance(current_digest, str):
            raise RuntimeError("legacy field alias registry entry fields must be strings")
        if not isinstance(legacy_digest, str):
            raise RuntimeError("legacy field alias registry entry fields must be strings")
        if not isinstance(encoded, str):
            raise RuntimeError("legacy field alias registry entry fields must be strings")
        try:
            if len(bytes.fromhex(current_digest)) != 32 or len(bytes.fromhex(legacy_digest)) != 32:
                raise ValueError
        except ValueError as exc:
            raise RuntimeError("legacy field alias registry digest invalid") from exc
        try:
            legacy_bytes = base64.b64decode(encoded, validate=True)
            legacy_field_id = legacy_bytes.decode("utf-8")
        except (ValueError, binascii.Error) as exc:
            raise RuntimeError("legacy field alias registry payload encoding invalid") from exc
        if hashlib.sha256(legacy_bytes).hexdigest() != legacy_digest:
            raise RuntimeError("legacy field alias registry payload digest mismatch")
        if current_digest in result:
            raise RuntimeError("duplicate current field digest in legacy alias registry")
        result[current_digest] = (legacy_field_id,)
    return result


def _spans(page: fitz.Page) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    data = page.get_text("dict", sort=True)
    for bi, block in enumerate(data["blocks"]):
        for li, line in enumerate(block.get("lines", [])):
            for si, span in enumerate(line.get("spans", [])):
                out.append(
                    {
                        "text": span["text"],
                        "bbox": fitz.Rect(span["bbox"]),
                        "size": float(span["size"]),
                        "font": span["font"],
                        "color": int(span["color"]),
                        "block": bi,
                        "line": li,
                        "span": si,
                    }
                )
    return out


def _union(rects: list[fitz.Rect]) -> fitz.Rect:
    return fitz.Rect(
        min(r.x0 for r in rects),
        min(r.y0 for r in rects),
        max(r.x1 for r in rects),
        max(r.y1 for r in rects),
    )


def _background(page: fitz.Page, rect: fitz.Rect) -> str:
    pix = page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False, colorspace=fitz.csRGB)
    points = [
        (rect.x0 - 2, rect.y0 - 2),
        (rect.x1 + 2, rect.y0 - 2),
        (rect.x0 - 2, rect.y1 + 2),
        (rect.x1 + 2, rect.y1 + 2),
        (rect.x0 - 3, (rect.y0 + rect.y1) / 2),
        (rect.x1 + 3, (rect.y0 + rect.y1) / 2),
    ]
    values: list[tuple[int, int, int]] = []
    for x, y in points:
        xi = max(0, min(pix.width - 1, int(round(x))))
        yi = max(0, min(pix.height - 1, int(round(y))))
        offset = (yi * pix.width + xi) * pix.n
        values.append(tuple(pix.samples[offset : offset + 3]))  # type: ignore[arg-type]
    r, g, b = Counter(values).most_common(1)[0][0]
    return f"#{r:02X}{g:02X}{b:02X}"


def _cell(page: fitz.Page, rect: fitz.Rect) -> list[float] | None:
    center = fitz.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
    candidates: list[fitz.Rect] = []
    for drawing in page.get_drawings():
        candidate = drawing.get("rect")
        if not candidate:
            continue
        if (
            candidate.contains(center)
            and candidate.width >= rect.width + 2
            and candidate.height >= rect.height + 2
            and candidate.width < page.rect.width * 0.98
            and candidate.height < 180
        ):
            candidates.append(candidate)
    if not candidates:
        return None
    best = min(candidates, key=lambda r: r.width * r.height)
    return [round(best.x0, 3), round(best.y0, 3), round(best.x1, 3), round(best.y1, 3)]


def _tokens(page: fitz.Page) -> list[tuple[str, fitz.Rect, dict[str, Any]]]:
    spans = _spans(page)
    text = ""
    owners: list[int | None] = []
    for index, span in enumerate(spans):
        if text:
            text += "\n"
            owners.append(None)
        text += span["text"]
        owners.extend([index] * len(span["text"]))
    found: list[tuple[str, fitz.Rect, dict[str, Any]]] = []
    for match in TOKEN_RE.finditer(text):
        token = re.sub(r"\s+", "", match.group(0))
        index_set: set[int] = set()
        for pos in range(match.start(), match.end()):
            owner = owners[pos]
            if owner is not None:
                index_set.add(owner)
        indices = sorted(index_set)
        if not indices:
            continue
        rect = _union([spans[index]["bbox"] for index in indices])
        found.append((token, rect, spans[indices[0]]))
    return found


def _compact_layout_spans(
    page: fitz.Page,
) -> tuple[list[dict[str, Any]], str, list[int | None]]:
    """Compact whitespace while preserving geometric discontinuities as sentinels."""
    spans = _spans(page)
    compact_chars: list[str] = []
    owners: list[int | None] = []
    previous_index: int | None = None
    for index, span in enumerate(spans):
        visible_chars = [char for char in str(span["text"]) if not char.isspace()]
        if not visible_chars:
            continue
        if (
            previous_index is not None
            and not _spans_are_geometrically_contiguous(spans[previous_index], span)
        ):
            compact_chars.append("\0")
            owners.append(None)
        compact_chars.extend(visible_chars)
        owners.extend([index] * len(visible_chars))
        previous_index = index
    return spans, "".join(compact_chars), owners


def _layout_token(
    page: fitz.Page, token: str
) -> tuple[str, fitz.Rect, dict[str, Any]]:
    """Locate one split marker using only geometrically contiguous layout spans."""
    spans, compact, owners = _compact_layout_spans(page)
    starts: list[int] = []
    offset = 0
    while True:
        start = compact.find(token, offset)
        if start < 0:
            break
        starts.append(start)
        offset = start + 1
    if len(starts) != 1:
        raise RuntimeError(f"expected one geometrically contiguous template marker: {token}")
    start = starts[0]
    end = start + len(token)
    indices = sorted({owner for owner in owners[start:end] if owner is not None})
    if not indices:
        raise RuntimeError(f"template marker has no layout span: {token}")
    rect = _union([spans[index]["bbox"] for index in indices])
    return token, rect, spans[indices[0]]


def _layout_token_by_digest(
    page: fitz.Page, digest: str, length: int
) -> tuple[str, fitz.Rect, dict[str, Any]]:
    """Locate one immutable legacy marker by digest without storing its plaintext."""
    spans, compact, owners = _compact_layout_spans(page)
    matches: list[tuple[int, str]] = []
    for start in range(0, max(0, len(compact) - length + 1)):
        candidate = compact[start : start + length]
        if "\0" in candidate:
            continue
        if hashlib.sha256(candidate.encode("utf-8")).hexdigest() == digest:
            matches.append((start, candidate))
    if len(matches) != 1:
        raise RuntimeError("expected one digest-pinned legacy template marker")
    start, token = matches[0]
    end = start + length
    indices = sorted({owner for owner in owners[start:end] if owner is not None})
    if not indices:
        raise RuntimeError("digest-pinned legacy template marker has no layout span")
    rect = _union([spans[index]["bbox"] for index in indices])
    return token, rect, spans[indices[0]]


def _matches_token_digest(token: str, digest: str) -> bool:
    return hashlib.sha256(token.encode("utf-8")).hexdigest() == digest


def _preserve_legacy_field_semantics(
    report_id: str,
    page_number: int,
    page: fitz.Page,
    tokens: list[tuple[str, fitz.Rect, dict[str, Any]]],
) -> list[tuple[str, fitz.Rect, dict[str, Any]]]:
    """Preserve two v3.0 field-inventory quirks during the backend-only migration.

    The immutable templates contain two split markers whose historical MuPDF ordering
    changed which field was inventory-visible. Stage 2 is a licensing migration, not a
    template semantics migration, so the same fillable-field boundary is retained.
    """
    if (report_id, page_number) == ("01", 6):
        combined_digest = LEGACY_REPORT_01_PAGE_06_COMBINED_SHA256
        rebuilt: list[tuple[str, fitz.Rect, dict[str, Any]]] = []
        replaced = 0
        dropped = 0
        calibration = _layout_token_by_digest(
            page,
            LEGACY_REPORT_01_PAGE_06_CALIBRATION_SHA256,
            LEGACY_REPORT_01_PAGE_06_CALIBRATION_LENGTH,
        )
        impact = _layout_token(page, "[[IMPACTO_CLINICO]]")
        for item in tokens:
            if _matches_token_digest(item[0], combined_digest):
                rebuilt.append(impact)
                replaced += 1
                continue
            if item[0] == "[[INDICACAO]]":
                dropped += 1
                continue
            rebuilt.append(item)
            if item[0] == "[[STATUS]]":
                rebuilt.append(calibration)
        if replaced != 1 or dropped != 1 or len(rebuilt) != len(tokens):
            raise RuntimeError("report 01 page 6 legacy field compatibility mismatch")
        return rebuilt
    if (report_id, page_number) == ("07", 5):
        combined = "[[MONOGENICO_PRS_ASSOCIACAO]][[IDENTIFICADOR]]"
        identifier = _layout_token(page, "[[IDENTIFICADOR]]")
        rebuilt = [identifier if item[0] == combined else item for item in tokens]
        if sum(item[0] == combined for item in tokens) != 1:
            raise RuntimeError("report 07 page 5 legacy field compatibility mismatch")
        return rebuilt
    return tokens


def _legacy_ruleset_control_ranges(text: str) -> list[tuple[int, int]]:
    """Locate the immutable legacy marker by digest without persisting its plaintext."""
    ranges: list[tuple[int, int]] = []
    length = LEGACY_RULESET_CONTROL_LENGTH
    if len(text) < length:
        return ranges
    for start in range(0, len(text) - length + 1):
        end = start + length
        candidate = text[start:end]
        digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
        if digest != LEGACY_RULESET_CONTROL_SHA256:
            continue
        before = text[start - 1] if start else ""
        after = text[end] if end < len(text) else ""
        if before and (before.isalnum() or before in "_.-"):
            raise RuntimeError("malformed pinned legacy ruleset control marker")
        if after and (after.isalnum() or after in "_.-"):
            raise RuntimeError("malformed pinned legacy ruleset control marker")
        ranges.append((start, end))
    return ranges


def _ruleset_control_sources(text: str) -> list[str]:
    sources: list[str] = []
    offset = 0
    while True:
        start = text.find(RULESET_CONTROL_PREFIX, offset)
        if start < 0:
            break
        match = RULESET_CONTROL_RE.match(text, start)
        if match is None:
            raise RuntimeError("malformed GENOMA ruleset control marker")
        marker = match.group(0)
        before = text[start - 1] if start else ""
        after = text[match.end()] if match.end() < len(text) else ""
        if before and (before.isalnum() or before in "_-"):
            raise RuntimeError(f"malformed GENOMA ruleset control marker: {marker!r}")
        if after and (after.isalnum() or after in "._-"):
            raise RuntimeError(f"malformed GENOMA ruleset control marker: {marker + after!r}")
        if marker != CANONICAL_RULESET_CONTROL:
            raise RuntimeError(f"noncanonical GENOMA ruleset control marker: {marker}")
        if marker not in sources:
            sources.append(marker)
        offset = match.end()
    if _legacy_ruleset_control_ranges(text) and CANONICAL_RULESET_CONTROL not in sources:
        sources.append(CANONICAL_RULESET_CONTROL)
    return sources


def _spans_are_geometrically_contiguous(
    previous: dict[str, Any], current: dict[str, Any]
) -> bool:
    """Allow marker compaction only across physically adjacent text fragments."""
    left = fitz.Rect(previous["bbox"])
    right = fitz.Rect(current["bbox"])
    scale = max(float(previous["size"]), float(current["size"]), 1.0)

    vertical_overlap = min(left.y1, right.y1) - max(left.y0, right.y0)
    if vertical_overlap >= 0:
        horizontal_gap = right.x0 - left.x1
        return -scale <= horizontal_gap <= max(18.0, 2.0 * scale)

    line_gap = right.y0 - left.y1
    aligned_left_edge = abs(right.x0 - left.x0) <= max(36.0, 3.0 * scale)
    return 0 <= line_gap <= max(12.0, 1.5 * scale) and aligned_left_edge


def _ruleset_control_occurrences(page: fitz.Page) -> list[tuple[str, fitz.Rect, dict[str, Any]]]:
    """Locate every ruleset marker from layout spans, including line-split markers.

    ``Page.search_for`` does not reliably match a marker whose text is split across
    separate PDF lines. Whitespace is compacted only while adjacent spans remain
    geometrically continuous; a sentinel boundary prevents unrelated page regions from
    being concatenated into a synthetic marker or oversized controlled span.
    """
    spans = _spans(page)
    compact_chars: list[str] = []
    owners: list[int | None] = []
    previous_index: int | None = None
    for index, span in enumerate(spans):
        visible_chars = [char for char in str(span["text"]) if not char.isspace()]
        if not visible_chars:
            continue
        if (
            previous_index is not None
            and not _spans_are_geometrically_contiguous(spans[previous_index], span)
        ):
            compact_chars.append("\0")
            owners.append(None)
        compact_chars.extend(visible_chars)
        owners.extend([index] * len(visible_chars))
        previous_index = index

    compact = "".join(compact_chars)
    found: list[tuple[str, fitz.Rect, dict[str, Any]]] = []
    offset = 0
    while True:
        start = compact.find(RULESET_CONTROL_PREFIX, offset)
        if start < 0:
            break
        match = RULESET_CONTROL_RE.match(compact, start)
        if match is None:
            raise RuntimeError("malformed GENOMA ruleset control marker")
        marker = match.group(0)
        before = compact[start - 1] if start else ""
        after = compact[match.end()] if match.end() < len(compact) else ""
        if before and (before.isalnum() or before in "_-"):
            raise RuntimeError(f"malformed GENOMA ruleset control marker: {marker!r}")
        if after and (after.isalnum() or after in "._-"):
            raise RuntimeError(f"malformed GENOMA ruleset control marker: {marker + after!r}")
        if marker != CANONICAL_RULESET_CONTROL:
            raise RuntimeError(f"noncanonical GENOMA ruleset control marker: {marker}")
        indices = sorted(
            {
                owner
                for owner in owners[start:match.end()]
                if owner is not None
            }
        )
        if not indices:
            raise RuntimeError("canonical GENOMA ruleset control marker has no layout span")
        rect = _union([spans[index]["bbox"] for index in indices])
        found.append((marker, rect, spans[indices[0]]))
        offset = match.end()

    for start, end in _legacy_ruleset_control_ranges(compact):
        indices = sorted(
            {
                owner
                for owner in owners[start:end]
                if owner is not None
            }
        )
        if not indices:
            raise RuntimeError("pinned legacy ruleset control marker has no layout span")
        rect = _union([spans[index]["bbox"] for index in indices])
        found.append((CANONICAL_RULESET_CONTROL, rect, spans[indices[0]]))
    return found


def _control_signature(rect: fitz.Rect) -> tuple[float, float, float, float]:
    return (
        round(rect.x0, 3),
        round(rect.y0, 3),
        round(rect.x1, 3),
        round(rect.y1, 3),
    )


def _controls(page: fitz.Page) -> list[tuple[str, fitz.Rect, dict[str, Any]]]:
    spans = _spans(page)
    result: list[tuple[str, fitz.Rect, dict[str, Any]]] = []
    for source in CONTROLLED:
        for rect in page.search_for(source):
            first = next((s for s in spans if (s["bbox"] & rect).get_area() > 0), None)
            result.append((source, rect, first or {"size": 7.0, "font": "DejaVuSans", "color": 0}))
    result.extend(_ruleset_control_occurrences(page))
    return result


def compile_pack(template_dir: Path, reference_index: Path) -> dict[str, Any]:
    index = json.loads(reference_index.read_text(encoding="utf-8"))
    legacy_aliases = _load_legacy_field_aliases()
    reports: dict[str, Any] = {}
    for report_id in sorted(index["reports"]):
        expected = index["reports"][report_id]
        source = template_dir / expected["filename"]
        if not source.is_file():
            raise RuntimeError(f"missing v3 reference PDF: {source}")
        actual = _sha256(source)
        if actual != expected["sha256"]:
            raise RuntimeError(f"reference PDF SHA-256 mismatch for {report_id}: {actual}")
        doc = fitz.open_document(source)
        if len(doc) != int(expected["page_count"]):
            raise RuntimeError(f"reference PDF page-count mismatch for {report_id}")
        occurrence: dict[str, int] = {}
        fields: list[dict[str, Any]] = []
        controls: list[dict[str, Any]] = []
        for page_number, page in enumerate(doc, 1):
            page_tokens = _preserve_legacy_field_semantics(
                report_id, page_number, page, _tokens(page)
            )
            for token, rect, span in page_tokens:
                occurrence[token] = occurrence.get(token, 0) + 1
                field_id = (
                    f"{report_id}:P{page_number:02d}:{token[2:-2]}:{occurrence[token]:03d}"
                )
                field = {
                    "field_id": field_id,
                    "token": token,
                    "occurrence": occurrence[token],
                    "page": page_number,
                    "bbox": [round(rect.x0, 3), round(rect.y0, 3), round(rect.x1, 3), round(rect.y1, 3)],
                    "cell_bbox": _cell(page, rect),
                    "font_size_pt": round(float(span["size"]), 3),
                    "font": span["font"],
                    "color": _rgbhex(int(span["color"])),
                    "background": _background(page, rect),
                    "guidance_only": token == "[[CAMPO]]",
                }
                aliases = legacy_aliases.get(hashlib.sha256(field_id.encode("utf-8")).hexdigest())
                if aliases:
                    field["legacy_field_ids"] = list(aliases)
                fields.append(field)
            expected_ruleset_controls = Counter(
                _control_signature(rect)
                for _, rect, _ in _ruleset_control_occurrences(page)
            )
            page_controls = _controls(page)
            actual_ruleset_controls = Counter(
                _control_signature(rect)
                for source_text, rect, _ in page_controls
                if source_text == CANONICAL_RULESET_CONTROL
            )
            if actual_ruleset_controls != expected_ruleset_controls:
                raise RuntimeError(
                    f"canonical ruleset controlled-span mismatch on report {report_id} page {page_number}: "
                    f"expected {sum(expected_ruleset_controls.values())}, got {sum(actual_ruleset_controls.values())}"
                )
            for source_text, rect, span in page_controls:
                controls.append(
                    {
                        "source_text": source_text,
                        "page": page_number,
                        "bbox": [round(rect.x0, 3), round(rect.y0, 3), round(rect.x1, 3), round(rect.y1, 3)],
                        "font_size_pt": round(float(span["size"]), 3),
                        "font": span["font"],
                        "color": _rgbhex(int(span["color"])),
                        "background": _background(page, rect),
                    }
                )
        if len(fields) != int(expected["placeholder_count"]):
            doc.close()
            raise RuntimeError(
                f"placeholder inventory mismatch for {report_id}: {len(fields)} != {expected['placeholder_count']}"
            )
        doc.close()
        reports[report_id] = {
            **{
                k: expected[k]
                for k in (
                    "filename",
                    "sha256",
                    "size_bytes",
                    "page_count",
                    "page_size_pt",
                    "placeholder_count",
                )
            },
            "fields": fields,
            "controlled_spans": controls,
        }
    return {
        "schema": "genoma-editorial-v3-reference-manifest-v2",
        "reference_suite": "GENOMA v3.0",
        "coordinate_compiler": COMPILER_ID,
        "reports": reports,
    }


def write_pack(payload: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = output_dir / "GENOMA_V3_TEMPLATE_MANIFEST.v2.json"
    detail = output_dir / "reference_v3_manifest.v2.json.gz.b64"
    raw = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    manifest.write_bytes(raw)
    compressed = gzip.compress(raw, mtime=0)
    detail.write_text(base64.b64encode(compressed).decode("ascii") + "\n", encoding="ascii")
    return {
        "schema": "genoma-editorial-coordinate-build-v2",
        "status": "VERIFICADO",
        "compiler": COMPILER_ID,
        "manifest": {
            "path": str(manifest),
            "sha256": _sha256(manifest),
            "size_bytes": manifest.stat().st_size,
        },
        "detail": {
            "path": str(detail),
            "sha256": _sha256(detail),
            "size_bytes": detail.stat().st_size,
        },
        "reports": len(payload["reports"]),
        "pages": sum(int(x["page_count"]) for x in payload["reports"].values()),
        "placeholders": sum(len(x["fields"]) for x in payload["reports"].values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template-dir", required=True)
    parser.add_argument("--reference-index", default="reporting/reference_v3_manifest.json")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--evidence")
    args = parser.parse_args()
    result = write_pack(
        compile_pack(Path(args.template_dir), Path(args.reference_index)),
        Path(args.output_dir),
    )
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.evidence:
        path = Path(args.evidence)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
