"""Child-only opt-in pagination patch for one pinned WeasyPrint implementation.

Never import or install these patches in the default rendering process.
This profile is not an accessibility, PDF/UA or release qualification.
"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from importlib.metadata import distribution, version
import json
from pathlib import Path

PROFILE_ID = "generated-pagination-artifact-render-v1"
MODE = "pagination-artifact-v1"
CSS = ("@page{size:595.27559pt 841.88976pt;margin:44pt;"
       "@bottom-right{content:counter(page);"
       "font-family:Lato;font-size:8.6pt;color:#455D73}}")
BASE_CSS = "@page{size:595.27559pt 841.88976pt;margin:44pt}"
PINNED = {
    "weasyprint/pdf/tags.py": "fae488b773686ea7fb685967c78a5b011854b88b61377f426ffb4e9ec00d76d2",
    "weasyprint/pdf/stream.py": "4faf9169d1970d4688b9cf41f733659ca6b1c4c098707c64f67c47a07309e81e",
    "weasyprint/document.py": "f08f91af64563f5fb27596828891fb326262511a56e4ace714e5a960898024c8",
    "weasyprint/draw/__init__.py": "fb6fcee79a80059c72a3142ef941d4968e3aeeddc8fbbcac2e8519e8be55e348",
}
PYDYF_SOURCE_SHA256 = "f0cdb619c0de7cd40e04fce1927411d98fc2186b509db6cfa93a142c755a08fd"
_PAGES = []
_INSTALLED = False


def renderer_profile() -> dict:
    return {
        "schema_version": "1.0.0", "profile_id": PROFILE_ID,
        "protocol_version": 2, "mode": MODE,
        "renderer": "weasyprint:70.0", "pydyf": "0.12.1", "page_parser": "pypdf:7.1.0",
        "renderer_pins": dict(PINNED), "pydyf_source_sha256": PYDYF_SOURCE_SHA256,
        "html_bytes": 67108864,
        "pdf_bytes": 16777216, "max_pages": 64, "result_bytes": 65536,
        "memory_bytes": 1073741824, "cpu_seconds": 20,
        "wall_seconds": 30, "open_files": 128,
        "page_numbers": "DECIMAL_ONE_BASED",
        "artifacts": {"Type": "Pagination", "Subtype": "Footer"},
        "page_labels": "DECIMAL_ONE_BASED",
        "default_pipeline_activated": False, "release_authorized": False,
        "aggregate_job_resources_qualified": False,
    }


def renderer_profile_sha256() -> str:
    return sha256(json.dumps(renderer_profile(), sort_keys=True,
                           separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()


def inject_pagination_css(html: str) -> str:
    """Admit one original typed-report wrapper, never mutate the default wrapper."""
    if (type(html) is not str or html.count(BASE_CSS) != 1
            or CSS in html or "@bottom-right" in html):
        raise ValueError("pagination wrapper not admitted")
    return html.replace(BASE_CSS, CSS)


def require_folio_html(html: str) -> None:
    if (type(html) is not str or html.count(CSS) != 1
            or BASE_CSS in html or html.count("@bottom-right") != 1):
        raise ValueError("pagination source not admitted")


def _pin_engine() -> None:
    if any(version(name) != expected for name, expected in (
            ("weasyprint", "70.0"), ("pydyf", "0.12.1"), ("pypdf", "7.1.0"))):
        raise ValueError("pagination engine version changed")
    dist = distribution("weasyprint")
    for relative, expected in PINNED.items():
        path = dist.locate_file(relative)
        if path.is_symlink() or sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("pagination engine source changed")
    pydyf_source = distribution("pydyf").locate_file("pydyf/__init__.py")
    if (pydyf_source.is_symlink()
            or sha256(pydyf_source.read_bytes()).hexdigest() != PYDYF_SOURCE_SHA256):
        raise ValueError("pagination content stream source changed")


def _install_after_font_bootstrap() -> None:
    """Install bounded child-only drawing hooks after approved fonts are selected."""
    global _INSTALLED
    if _INSTALLED:
        raise ValueError("pagination hook already installed")
    _pin_engine()
    import pydyf
    import weasyprint.document as document_module
    import weasyprint.pdf.tags as tag_module
    import weasyprint.pdf.stream as stream_module
    from weasyprint.formatting_structure import boxes

    original_draw = document_module.draw_page
    original_build = tag_module._build_box_tree
    original_marked = stream_module.Stream.marked

    def draw_page(page_box, stream):
        selected, values = set(), []
        for child in page_box.children:
            if isinstance(child, boxes.MarginBox):
                descendants = [box for box in child.descendants()
                               if isinstance(box, boxes.TextBox)]
                if descendants:
                    if child.at_keyword != "@bottom-right":
                        raise ValueError("unadmitted margin box")
                    selected.update(id(box) for box in descendants)
                    values.extend(box.text for box in descendants)
        if len(values) != 1 or stream._tags is None or len(_PAGES) >= 64:
            raise ValueError("pagination page layout changed")
        stream._pagination_ids = selected
        stream._pagination_seen = set()
        _PAGES.append(values[0])
        original_draw(page_box, stream)
        if stream._pagination_seen != selected:
            raise ValueError("pagination drawing incomplete")

    def build_box_tree(box, parent, pdf, page_number, nums, annotations, tags):
        if isinstance(box, boxes.MarginBox):
            if any(text in tags for text in box.descendants()
                   if isinstance(text, boxes.TextBox)):
                raise ValueError("pagination content has semantic MCIDs")
            return
        yield from original_build(box, parent, pdf, page_number, nums, annotations, tags)

    @contextmanager
    def marked(self, box, tag):
        if id(box) not in getattr(self, "_pagination_ids", frozenset()):
            with original_marked(self, box, tag):
                yield
            return
        if (tag != "Span" or self._tags is None or box in self._tags
                or id(box) in self._pagination_seen):
            raise ValueError("unexpected pagination text")
        self._pagination_seen.add(id(box))
        pydyf.Stream.begin_marked_content(
            self, "Artifact", pydyf.Dictionary({
                "Type": "/Pagination", "Subtype": "/Footer"}))
        try:
            yield
        finally:
            pydyf.Stream.end_marked_content(self)

    document_module.draw_page = draw_page
    tag_module._build_box_tree = build_box_tree
    stream_module.Stream.marked = marked
    _INSTALLED = True


def install_in_isolated_render_worker() -> None:
    """Patch only this one-use child, preserving all existing default workers."""
    from reporting.pdf_font_selection import LatoFontSelection

    original_enter = LatoFontSelection.__enter__
    original_finisher = LatoFontSelection.finisher

    def enter(self):
        active = original_enter(self)
        _install_after_font_bootstrap()
        return active

    def finisher(self, document, pdf):
        original_finisher(self, document, pdf)
        if (len(_PAGES) != len(document.pages)
                or not 1 <= len(_PAGES) <= 64
                or _PAGES != [str(i) for i in range(1, len(_PAGES) + 1)]):
            raise ValueError("pagination counters not consecutive")
        import pydyf
        pdf.catalog["PageLabels"] = pydyf.Dictionary({
            "Nums": pydyf.Array([0, pydyf.Dictionary({"S": "/D", "St": 1})])})

    LatoFontSelection.__enter__ = enter
    LatoFontSelection.finisher = finisher


_SOURCE_FILES = (
    "reporting/_pdf_pagination_core.py",
    "reporting/_pdf_pagination_render_worker.py",
    "reporting/_pdf_pagination_observer_worker.py",
    "reporting/pdf_pagination_artifact.py",
)


def require_source_manifest() -> None:
    """Bind the four opt-in source files without changing any old manifests."""
    root = Path(__file__).resolve().parents[1]
    path = root / "reporting/pdf-pagination-artifact.v1.json"
    if path.is_symlink():
        raise ValueError("pagination source manifest symlink")
    raw = path.read_bytes()
    if not 0 < len(raw) <= 8192:
        raise ValueError("pagination source manifest budget")
    record = json.loads(raw.decode("ascii"))
    if (type(record) is not dict
            or set(record) != {"schema_version", "profile_id", "sources", "default_pipeline_activated"}
            or record["schema_version"] != "1.0.0"
            or record["profile_id"] != PROFILE_ID
            or record["default_pipeline_activated"] is not False
            or type(record["sources"]) is not dict
            or set(record["sources"]) != set(_SOURCE_FILES)):
        raise ValueError("pagination source manifest contract")
    for name in _SOURCE_FILES:
        source = root / name
        expected = record["sources"][name]
        if (type(expected) is not str or len(expected) != 64
                or any(char not in "0123456789abcdef" for char in expected)
                or source.is_symlink() or not source.resolve().is_relative_to(root)
                or sha256(source.read_bytes()).hexdigest() != expected):
            raise ValueError("pagination source drift")
