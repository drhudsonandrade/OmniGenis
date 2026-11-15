"""Bounded PDF object inspection for one inert SVG figure layout attribute.

This is an opt-in successor to the passive v1 policy, not an action allowlist.
The existing v1 worker and its default prohibition of every /A remain intact.
"""
from __future__ import annotations

from hashlib import sha256
from importlib.metadata import version
from io import BytesIO
import json
import math
from pathlib import Path
import signal
import sys

_PDF_LIMIT = 8 * 1024 * 1024
_PAGE_LIMIT = 32
_VISIT_LIMIT = 20000
_DEPTH_LIMIT = 64
_MEMORY_LIMIT = 256 * 1024 * 1024
_CPU_LIMIT = 5
_WALL_LIMIT = 10
_PYPDF_VERSION = "7.1.0"
_ERRORS = frozenset({
    "pdf_svg_layout_missing", "pdf_svg_layout_invalid",
    "pdf_svg_layout_multiple", "pdf_svg_layout_v1_denial_missing",
    "pdf_safety_structure_invalid", "encrypted_pdf_forbidden",
    "pdf_safety_budget_exceeded", "pdf_safety_parser_version_mismatch",
    "pdf_safety_parser_unavailable", "pdf_safety_worker_unavailable",
    "pdf_actions_forbidden", "pdf_embedded_files_forbidden",
    "pdf_forms_forbidden", "pdf_interactive_media_forbidden",
    "pdf_external_stream_forbidden",
})


class _Refusal(ValueError):
    """Only internal fixed error codes are allowed in an observation."""


def _blocked(error: str, digest: str | None = None) -> dict:
    return {
        "status": "BLOCKED", "pdf_sha256": digest, "page_count": None,
        "object_count": None, "figure_layout_count": None,
        "errors": [error if error in _ERRORS else "pdf_safety_structure_invalid"],
    }


def _inspect_figure(obj: object, page_refs: set[tuple[int, int]]) -> None:
    """Require a StructElem Figure's inert Layout/BBox dictionary verbatim.

    The /Pg page must be one of this PDF's own pages. No link, annotation action,
    arbitrary structure attribute owner or unbounded number is admitted.
    """
    from pypdf.generic import (
        ArrayObject, DictionaryObject, FloatObject, IndirectObject, NameObject,
        NumberObject, TextStringObject,
    )
    if not isinstance(obj, DictionaryObject):
        raise _Refusal("pdf_svg_layout_invalid")
    if (obj.get("/Type") != "/StructElem" or obj.get("/S") != "/Figure"
            or not all(key in obj for key in ("/P", "/Pg", "/K", "/Alt"))):
        raise _Refusal("pdf_svg_layout_invalid")
    alt = obj.get("/Alt")
    if not isinstance(alt, TextStringObject) or not 1 <= len(str(alt)) <= 256:
        raise _Refusal("pdf_svg_layout_invalid")
    page = obj.raw_get("/Pg")
    if not isinstance(page, IndirectObject) or (page.idnum, page.generation) not in page_refs:
        raise _Refusal("pdf_svg_layout_invalid")
    page_obj = page.get_object()
    if not isinstance(page_obj, DictionaryObject) or page_obj.get("/Type") != "/Page":
        raise _Refusal("pdf_svg_layout_invalid")
    attr = obj.raw_get("/A")
    if not isinstance(attr, DictionaryObject) or set(attr) != {
        NameObject("/O"), NameObject("/BBox"),
    } or type(attr.raw_get("/O")) is not NameObject or attr.raw_get("/O") != NameObject("/Layout"):
        raise _Refusal("pdf_svg_layout_invalid")
    bbox = attr.raw_get("/BBox")
    box = page_obj.get("/MediaBox")
    if not isinstance(bbox, ArrayObject) or not isinstance(box, ArrayObject):
        raise _Refusal("pdf_svg_layout_invalid")
    if len(bbox) != 4 or len(box) != 4:
        raise _Refusal("pdf_svg_layout_invalid")
    allowed = (NumberObject, FloatObject)
    if any(type(n) not in allowed for n in (*bbox, *box)):
        raise _Refusal("pdf_svg_layout_invalid")
    coordinates = tuple(float(n) for n in bbox)
    page_box = tuple(float(n) for n in box)
    if not all(map(math.isfinite, coordinates + page_box)):
        raise _Refusal("pdf_svg_layout_invalid")
    x0, y0, x1, y1 = coordinates
    px0, py0, px1, py1 = page_box
    if not (px0 <= x0 < x1 <= px1 and py0 <= y0 < y1 <= py1):
        raise _Refusal("pdf_svg_layout_invalid")


def _find_exact_layout(trailer: object, page_refs: set[tuple[int, int]]):
    """Scan all effective objects, refusing any second or wrong /A attribute."""
    from pypdf.generic import (
        ArrayObject, DictionaryObject, IndirectObject, NameObject, PdfObject,
    )
    pending = [(trailer, 0)]
    refs: set[tuple[int, int]] = set()
    containers: set[int] = set()
    accepted = None
    visits = 0
    while pending:
        obj, depth = pending.pop()
        if isinstance(obj, IndirectObject):
            identity = (obj.idnum, obj.generation)
            if identity in refs:
                continue
            refs.add(identity)
            obj = obj.get_object()
        if not isinstance(obj, PdfObject) or isinstance(obj, IndirectObject):
            raise _Refusal("pdf_safety_structure_invalid")
        if isinstance(obj, (DictionaryObject, ArrayObject)):
            if id(obj) in containers:
                continue
            containers.add(id(obj))
        visits += 1
        if visits > _VISIT_LIMIT or depth > _DEPTH_LIMIT:
            raise _Refusal("pdf_safety_budget_exceeded")
        children = ()
        if isinstance(obj, DictionaryObject):
            if any(not isinstance(key, NameObject) for key in obj):
                raise _Refusal("pdf_safety_structure_invalid")
            if "/A" in obj:
                if accepted is not None:
                    raise _Refusal("pdf_svg_layout_multiple")
                _inspect_figure(obj, page_refs)
                accepted = obj
            children = obj.values()
        elif isinstance(obj, ArrayObject):
            children = obj
        for child in children:
            if len(pending) >= _VISIT_LIMIT:
                raise _Refusal("pdf_safety_budget_exceeded")
            pending.append((child, depth + 1))
    if accepted is None:
        raise _Refusal("pdf_svg_layout_missing")
    return accepted


def inspect_document(pdf: bytes) -> dict:
    """Preserve original PDF bytes and apply existing v1 policy after A-only proof."""
    if len(pdf) > _PDF_LIMIT:
        return _blocked("pdf_safety_budget_exceeded")
    digest = sha256(pdf).hexdigest()
    if not pdf.startswith(b"%PDF-"):
        return _blocked("pdf_safety_structure_invalid", digest)
    if version("pypdf") != _PYPDF_VERSION:
        return _blocked("pdf_safety_parser_version_mismatch", digest)
    try:
        from pypdf import PdfReader
        from pypdf.generic import DictionaryObject, NameObject
        if __name__ == "__main__":
            import _pdf_safety_worker as v1
        else:
            from reporting import _pdf_safety_worker as v1
    except (ImportError, OSError):
        return _blocked("pdf_safety_parser_unavailable", digest)
    try:
        if (v1.MAX_PDF_BYTES != _PDF_LIMIT or v1.MAX_PAGES != _PAGE_LIMIT
                or v1.MAX_VISITS != _VISIT_LIMIT or v1.MAX_DEPTH != _DEPTH_LIMIT):
            raise _Refusal("pdf_safety_structure_invalid")
        reader = PdfReader(BytesIO(pdf), strict=True)
        if reader.is_encrypted:
            raise _Refusal("encrypted_pdf_forbidden")
        root = reader.root_object
        if (not isinstance(root, DictionaryObject) or
                root.get("/Type") != NameObject("/Catalog")):
            raise _Refusal("pdf_safety_structure_invalid")
        pages = len(reader.pages)
        if not 1 <= pages <= _PAGE_LIMIT:
            raise _Refusal("pdf_safety_budget_exceeded")
        page_refs = set()
        for page in reader.pages:
            ref = page.indirect_reference
            if ref is None:
                raise _Refusal("pdf_safety_structure_invalid")
            page_refs.add((ref.idnum, ref.generation))
        permitted = _find_exact_layout(reader.trailer, page_refs)
        _, original_findings = v1.inspect_graph(reader.trailer)
        if "pdf_actions_forbidden" not in original_findings:
            raise _Refusal("pdf_svg_layout_v1_denial_missing")
        for finding in original_findings:
            if finding != "pdf_actions_forbidden":
                raise _Refusal(finding)
        # Only the parser's private, ephemeral graph changes. The input PDF
        # bytes are never rewritten and the v1 worker is not modified.
        del permitted[NameObject("/A")]
        count, remaining = v1.inspect_graph(reader.trailer)
        if remaining:
            raise _Refusal(remaining[0])
        return {
            "status": "PASS", "pdf_sha256": digest, "page_count": pages,
            "object_count": count, "figure_layout_count": 1, "errors": [],
        }
    except _Refusal as error:
        return _blocked(str(error), digest)
    except (MemoryError, OverflowError):
        return _blocked("pdf_safety_budget_exceeded", digest)
    except Exception:
        return _blocked("pdf_safety_structure_invalid", digest)


def main(parent_pid: int | None = None) -> None:
    """Apply the v1 resource, lifecycle, filesystem and network guards."""
    try:
        if type(parent_pid) is not int or parent_pid <= 1 or sys.platform != "linux":
            raise ValueError("invalid worker parent")
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (_MEMORY_LIMIT, _MEMORY_LIMIT))
        resource.setrlimit(resource.RLIMIT_CPU, (_CPU_LIMIT, _CPU_LIMIT))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if __name__ == "__main__":
            sys.path.insert(0, str(Path(__file__).resolve(strict=True).parent))
            from _pdf_process_sandbox import enforce_pdf_landlock, enforce_pdf_network_filter
            from _pdf_render_worker import _arm_worker_lifetime
        else:
            from reporting._pdf_process_sandbox import enforce_pdf_landlock, enforce_pdf_network_filter
            from reporting._pdf_render_worker import _arm_worker_lifetime
        _arm_worker_lifetime(parent_pid)
        signal.setitimer(signal.ITIMER_REAL, _WALL_LIMIT)
        enforce_pdf_landlock(Path(__file__))
        enforce_pdf_network_filter()
    except (ImportError, OSError, ValueError, RuntimeError):
        response = _blocked("pdf_safety_worker_unavailable")
    else:
        try:
            response = inspect_document(sys.stdin.buffer.read(_PDF_LIMIT + 1))
        except Exception:
            response = _blocked("pdf_safety_structure_invalid")
    sys.stdout.write(json.dumps(response, sort_keys=True) + "\n")


if __name__ == "__main__":
    try:
        expected_parent = int(sys.argv[1]) if len(sys.argv) == 2 else None
    except ValueError:
        expected_parent = None
    main(expected_parent)
