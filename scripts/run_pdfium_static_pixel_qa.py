#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium  # type: ignore[import-untyped]
from PIL import Image, ImageChops, ImageDraw, ImageOps
from pypdfium2 import raw  # type: ignore[import-untyped]

DPI = 200
SCALE = DPI / 72.0
COORDINATE_COMPILER = {
    "id": "pypdfium2-5.13.0-pdfium-genoma-v3",
    "manifest_sha256": "cb91138cfa38912569dc556e684f302752eebbf64b0f656e97ec0a0c889a8bd6",
    "compressed_detail_sha256": "2eea3539647d772f5dfd14640791cf773ff10a49ee92dd0589512dfaa50f5933",
}
EXPECTED = {
    "01": ("01_RELATORIO_GENOMA_CLINICO_v3.0.pdf", "812a7e9ff15a1f6b368d924e78dd0456145d0f22e0d24af5ea0a9faa9d458797", 10),
    "02": ("02_RELATORIO_ANCESTRALIDADE_GENEALOGIA_v3.0.pdf", "2ede76a59e74a6425f22be31f7b96fa6bc1b0ce3fb74b03fa15775e0e6dbe496", 10),
    "03": ("03_RELATORIO_REPRODUTIVO_INDIVIDUAL_CASAL_v3.0.pdf", "945007f2a9db1e6252e9fa0352e211ef6957383970913e7c7cb3f604955ab2dd", 10),
    "04": ("04_RELATORIO_NUTRIGENETICO_NUTRICAO_PRECISAO_v3.0.pdf", "32687216782de91619249b3fd9aef7975fcb69544c74cbbf3a54b58cb716a394", 10),
    "05": ("05_RELATORIO_TECNICO_METODOS_QC_LIMITACOES_v3.0.pdf", "fd9a2fbfd90534af2fc78b8a0a85c8b89262568d78b21f711dc66b0f0bf00f41", 11),
    "06": ("06_RELATORIO_FARMACOGENOMICA_CARTAO_ANESTESIA_v3.0.pdf", "eeff08f634b3f40f3a280b6e7eeb7c3e09e8f0a4ca080a2da2882d2c9ec58fbe", 9),
    "07": ("07_RELATORIO_LONGEVIDADE_PROTECAO_PREVENCAO_v3.0.pdf", "60a3b80cf47e40b5405c567586800d03b0e0d882118c06775119dcc22ebf4a0a", 9),
    "08": ("08_ATLAS_TRACOS_CURIOSIDADES_v3.0.pdf", "3ebc0a412f587b08823baaae9553f88f33f5a0b53d6b74c2710ea647d3c93dd5", 9),
    "09": ("09_GENOME_COMPLETENESS_BLIND_SPOTS_v3.0.pdf", "f1b706a0b8948f27dddbee825779d7c89c44e90ca9468813c76b647164d9dfdd", 9),
    "10": ("10_RESUMO_CLINICO_UMA_PAGINA_v3.0.pdf", "d0781af71f8855167311f9e7884c5e2d420ebe0b7da6d75f5a9cfed5b004ad59", 1),
    "11": ("11_GUIA_EDITORIAL_MATRIZ_PREENCHIMENTO_v3.0.pdf", "d68fce73aeb7fd7bb6e0eb27679b167a3899cce92eb20b64ad5bb15352031fea", 12),
}
CONTROLLED = (
    "MODELO REUTILIZÁVEL v3.0", "MODELO EDITÁVEL", "NÃO INSERIDOS",
    "MODELO — NÃO É RESULTADO GENÉTICO", "MODELO — NÃO É RESULTADO",
    "MODELO SEM DADOS PESSOAIS",
    "Campos em azul são placeholders obrigatórios ou condicionais; preencher com dado rastreável ou declarar NÃO DISPONÍVEL.",
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_hash(value: Any) -> str:
    raw_bytes = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw_bytes).hexdigest()


def union(boxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return min(x[0] for x in boxes), min(x[1] for x in boxes), max(x[2] for x in boxes), max(x[3] for x in boxes)


def char_box(textpage: Any, index: int, height: float) -> tuple[float, float, float, float]:
    left, bottom, right, top = map(float, textpage.get_charbox(index, loose=True))
    return left, height - top, right, height - bottom


def text_regions(page: Any) -> list[tuple[str, str, tuple[float, float, float, float]]]:
    textpage = page.get_textpage()
    try:
        full = textpage.get_text_range()
        height = float(page.get_height())
        regions: list[tuple[str, str, tuple[float, float, float, float]]] = []
        for match in re.finditer(r"\[\[.*?\]\]", full, re.DOTALL):
            token = re.sub(r"\s+", "", match.group(0))
            boxes = [char_box(textpage, i, height) for i in range(match.start(), match.end()) if full[i] not in "\r\n"]
            if boxes:
                regions.append(("field", token, union(boxes)))
        for text in CONTROLLED:
            offset = 0
            while True:
                start = full.find(text, offset)
                if start < 0:
                    break
                boxes = [char_box(textpage, i, height) for i in range(start, start + len(text)) if full[i] not in "\r\n"]
                if boxes:
                    regions.append(("control", text, union(boxes)))
                offset = start + 1
        return regions
    finally:
        textpage.close()


def path_rectangles(page: Any) -> list[tuple[float, float, float, float]]:
    height = float(page.get_height())
    result = []
    for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_PATH]):
        left, bottom, right, top = map(float, obj.get_bounds())
        rect = (left, height - top, right, height - bottom)
        if rect[2] > rect[0] and rect[3] > rect[1]:
            result.append(rect)
    return result


def enclosing_cell(box: tuple[float, float, float, float], paths: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float] | None:
    x0, y0, x1, y1 = box
    candidates = [r for r in paths if r[0] <= x0 + 1 and r[1] <= y0 + 1 and r[2] >= x1 - 1 and r[3] >= y1 - 1 and (r[2] - r[0]) < 580 and (r[3] - r[1]) < 180]
    return min(candidates, key=lambda r: (r[2] - r[0]) * (r[3] - r[1])) if candidates else None


def allowed_rectangles(page: Any) -> list[dict[str, Any]]:
    paths = path_rectangles(page)
    page_width = float(page.get_width())
    allowed = []
    for kind, text, box in text_regions(page):
        x0, y0, x1, y1 = box
        if kind == "control":
            mask = (x0 - 1.5, y0 - 1.5, x1 + 1.5, y1 + 1.5)
        else:
            cell = enclosing_cell(box, paths)
            if cell:
                mask = (min(x0 - 2, cell[0]), min(y0 - 2, cell[1]), max(x1 + 2, cell[2]), max(y1 + 12, cell[3]))
            else:
                mask = (x0 - 2, y0 - 2, max(x1 + 2, page_width - 30), y1 + 14)
        allowed.append({"kind": kind, "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "rect_pt": [round(v, 6) for v in mask]})
    return allowed


def render(page: Any) -> Image.Image:
    bitmap = page.render(scale=SCALE, rotation=0)
    try:
        image = bitmap.to_pil().convert("RGB").copy()
    finally:
        bitmap.close()
    return image


def changed_pixel_mask(source: Image.Image, candidate: Image.Image) -> Image.Image:
    """Return a binary mask of any RGB-channel change without grayscale rounding."""
    rgb_diff = ImageChops.difference(source, candidate)
    red, green, blue = rgb_diff.split()
    maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    return maximum.point(lambda value: 255 if value else 0)


def reproduction_command(
    *,
    candidate_pattern: str,
    output: Path,
    mask_manifest: Path,
    log: Path,
) -> str:
    """Return a shell command whose private-directory variables expand at execution time."""
    parts = [
        "python",
        "scripts/run_pdfium_static_pixel_qa.py",
        "--template-dir",
        '"$PRIVATE_TEMPLATE_DIR"',
        "--candidate-dir",
        '"$CANDIDATE_DIR"',
        "--candidate-pattern",
        shlex.quote(candidate_pattern),
        "--output",
        shlex.quote(str(output)),
        "--mask-manifest",
        shlex.quote(str(mask_manifest)),
        "--log",
        shlex.quote(str(log)),
    ]
    return " ".join(parts)


def compare_page(source_page: Any, candidate_page: Any, allowed: list[dict[str, Any]]) -> tuple[int, int]:
    source = render(source_page)
    candidate = render(candidate_page)
    if source.size != candidate.size:
        raise RuntimeError("source/candidate raster size mismatch")
    diff = changed_pixel_mask(source, candidate)
    mask = Image.new("L", source.size, 0)
    draw = ImageDraw.Draw(mask)
    sx = source.width / float(source_page.get_width())
    sy = source.height / float(source_page.get_height())
    for entry in allowed:
        x0, y0, x1, y1 = entry["rect_pt"]
        draw.rectangle((math.floor(x0 * sx), math.floor(y0 * sy), math.ceil(x1 * sx), math.ceil(y1 * sy)), fill=255)
    outside = ImageChops.multiply(diff, ImageOps.invert(mask))
    return outside.histogram()[255], diff.histogram()[255]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template-dir", type=Path, required=True)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--candidate-pattern", default="qa-{rid}.pdf")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mask-manifest", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    command = reproduction_command(
        candidate_pattern=args.candidate_pattern,
        output=args.output,
        mask_manifest=args.mask_manifest,
        log=args.log,
    )
    reports: dict[str, Any] = {}
    mask_payload: dict[str, Any] = {"schema": "omnigenis-pdfium-static-pixel-mask-v1", "dpi": DPI, "reports": {}}
    log_lines = []
    outside_total = changed_total = page_total = 0
    candidate_hashes: dict[str, str] = {}
    for rid, (filename, expected_hash, expected_pages) in EXPECTED.items():
        source_path = args.template_dir / filename
        candidate_path = args.candidate_dir / args.candidate_pattern.format(rid=rid)
        actual = sha256(source_path)
        if actual != expected_hash:
            raise RuntimeError(f"source template hash mismatch: {rid}")
        candidate_hashes[rid] = sha256(candidate_path)
        source_doc = pdfium.PdfDocument(str(source_path))
        candidate_doc = pdfium.PdfDocument(str(candidate_path))
        if len(source_doc) != expected_pages or len(candidate_doc) != expected_pages:
            raise RuntimeError(f"page count mismatch: {rid}")
        report_outside = report_changed = 0
        page_records = []
        mask_pages = []
        for page_index in range(expected_pages):
            allowed = allowed_rectangles(source_doc[page_index])
            outside, changed = compare_page(source_doc[page_index], candidate_doc[page_index], allowed)
            report_outside += outside
            report_changed += changed
            page_records.append({"page": page_index + 1, "outside_changed_pixels": outside, "changed_pixels": changed, "allowed_regions": len(allowed)})
            mask_pages.append({"page": page_index + 1, "allowed": allowed})
        reports[rid] = {"filename": filename, "sha256": actual, "candidate_sha256": candidate_hashes[rid], "pages": expected_pages, "outside_changed_pixels": report_outside, "changed_pixels": report_changed, "page_metrics": page_records}
        mask_payload["reports"][rid] = mask_pages
        outside_total += report_outside
        changed_total += report_changed
        page_total += expected_pages
        log_lines.append(f"{rid}\tpages={expected_pages}\toutside={report_outside}\tchanged={report_changed}\tsource={actual}\tcandidate={candidate_hashes[rid]}")
    args.mask_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.mask_manifest.write_text(json.dumps(mask_payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.log.write_text("\n".join(log_lines) + f"\nTOTAL\tpages={page_total}\toutside={outside_total}\tchanged={changed_total}\n", encoding="utf-8")
    producer_hash = sha256(Path(__file__))
    provenance = {"producer_sha256": producer_hash, "command": command, "log_sha256": sha256(args.log), "mask_manifest_sha256": sha256(args.mask_manifest), "candidate_set_sha256": canonical_hash(candidate_hashes)}
    producer = {"path": "scripts/run_pdfium_static_pixel_qa.py", **provenance}
    evidence = {
        "schema": "omnigenis-pdfium-static-pixel-qa-v2",
        "status": "VERIFICADO" if outside_total == 0 else "FALHOU",
        "scope": "Independent 200 DPI static-pixel QA for the PDFium Stage 2 coordinate candidate",
        "executed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dpi": DPI,
        "coordinate_compiler": COORDINATE_COMPILER,
        "private_templates": {
            "bytes_committed": False,
            "reports": len(reports),
            "pages": page_total,
            "sha256_matches_reference_manifest": len(reports),
        },
        "producer": producer,
        "aggregate": {
            "reports": len(reports),
            "reference_pages": page_total,
            "outside_changed_pixels": outside_total,
            "changed_pixels": changed_total,
            "result": "PASS" if outside_total == 0 else "FAIL",
        },
        "reports": reports,
        "limitations": [
            "QA is editorial and geometric only; it does not validate scientific interpretation.",
            "Private template bytes, candidate PDFs, mask manifest, and execution log are not committed; their SHA-256 identities are retained in this evidence.",
            "The producer derives masks from the SHA-pinned source PDFs, not from the pixel diff, and fails closed on source hash or page-count drift.",
        ],
    }
    evidence["evidence_sha256"] = canonical_hash({"provenance": producer, "aggregate": evidence["aggregate"], "reports": reports})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": evidence["status"], "outside_changed_pixels": outside_total, "evidence_sha256": evidence["evidence_sha256"], **provenance}, sort_keys=True))
    return 0 if outside_total == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
