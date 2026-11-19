"""Finite pre-serialization structure emission for the typed original-brand route."""
from decimal import Decimal, localcontext
from hashlib import sha256
from importlib.metadata import distribution
import json
import os
import stat
from math import isfinite
from pathlib import Path
import re
import sys

PROFILE_ID = "typed-original-brand-render-v1"
MODE = "native-typed-original-brand-v1"
ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "reporting/pdf-typed-brand-render.v1.json"
LEGACY_HASH = "5391bdae627341ce95c351093bfc2a6dc2575e48203f9ee4bd4581c6ec1eb0de"
SOURCES = (
    "reporting/_pdf_typed_brand_emission.py", "reporting/_pdf_typed_brand_render_worker.py",
    "reporting/pdf_typed_brand_render.py", "reporting/typed_original_brand_pdf.py",
    "reporting/pdf-raster-render.v1.json", "reporting/pdf_font_selection.py",
    "reporting/pdf_raster_render.py", "reporting/pdf_raster_resources.py",
    "reporting/_pdf_render_worker.py", "reporting/_pdf_process_sandbox.py",
    "reporting/_pdf_process_lifecycle.py", "reporting/adapters.py", "reporting/png_inspection.py",
    "reporting/_pdf_native_launch_budget.py", "reporting/_pdf_document_byte_budget.py",
    "reporting/page_windows.py", "reporting/bindings.py", "reporting/contracts.py",
    "reporting/_pdf_original_dna_png_admission.py", "requirements-reporting.lock",
)
ENGINES = {
    "weasyprint/pdf/tags.py": "fae488b773686ea7fb685967c78a5b011854b88b61377f426ffb4e9ec00d76d2",
    "weasyprint/pdf/__init__.py": "426e0855db0473121bf70666b3c6c5fbd9ebfe832741d31a3689e8b10a14281f",
    "weasyprint/document.py": "f08f91af64563f5fb27596828891fb326262511a56e4ace714e5a960898024c8",
    "pydyf/__init__.py": "f0cdb619c0de7cd40e04fce1927411d98fc2186b509db6cfa93a142c755a08fd",
}
CONTRACT = {
    "schema_version": "1.0.0", "profile_id": PROFILE_ID, "mode": MODE, "protocol_version": 3,
    "inherited_profile_sha256": LEGACY_HASH, "versions": {"weasyprint": "70.0", "pydyf": "0.12.1"},
    "engine_sources": ENGINES, "qualification_only": True, "release_authorized": False,
    "default_pipeline_activated": False, "aggregate_job_resources_qualified": False,
    "limits": {"memory_bytes": 1073741824, "cpu_seconds": 20, "wall_seconds": 30,
               "open_files": 128, "html_bytes": 67108864, "pdf_bytes": 16777216,
               "pages": 64, "response_bytes": 65536, "structure_depth": 64,
               "structure_charge": 20000, "ids": 10000, "id_bytes": 128, "figures": 1},
}


def require(condition):
    if not condition:
        raise ValueError("typed brand emission refused")


def _bounded_file(path, limit, *, retain=False):
    """Hash one bounded regular descriptor and reject changes across the read."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    fd = os.open(path, flags)
    try:
        first = os.fstat(fd)
        require(stat.S_ISREG(first.st_mode) and 0 < first.st_size <= limit)
        digest = sha256()
        content = bytearray()
        consumed = 0
        while consumed < first.st_size:
            chunk = os.read(fd, min(8192, first.st_size - consumed))
            require(bool(chunk))
            consumed += len(chunk)
            digest.update(chunk)
            if retain:
                content.extend(chunk)
        last = os.fstat(fd)
        named = os.stat(path, follow_symlinks=False)
        def identity(info):
            return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
                    info.st_mtime_ns, info.st_ctime_ns)
        require(identity(first) == identity(last) == identity(named))
        return bytes(content), digest.hexdigest()
    finally:
        os.close(fd)


def _manifest():
    from reporting.pdf_raster_render import _strict
    raw, manifest_digest = _bounded_file(MANIFEST, 16384, retain=True)
    data = _strict(raw.decode("ascii"))
    require(type(data) is dict and set(data) == set(CONTRACT) | {"sources"})
    fixed = {key: data[key] for key in CONTRACT}
    require(json.dumps(fixed, sort_keys=True) == json.dumps(CONTRACT, sort_keys=True))
    require(type(data["sources"]) is dict and set(data["sources"]) == set(SOURCES))
    for name in SOURCES:
        path = ROOT / name
        require(not path.is_symlink() and path.resolve().is_relative_to(ROOT)
                and _bounded_file(path, 65536)[1] == data["sources"][name])
    for package, version in CONTRACT["versions"].items():
        dist = distribution(package)
        require(dist.version == version)
        for name, expected in ENGINES.items():
            if name.startswith(package + "/"):
                require(_bounded_file(Path(dist.locate_file(name)), 65536)[1] == expected)
    return data, manifest_digest


def require_source_manifest() -> dict:
    return _manifest()[0]


def profile_sha256() -> str:
    return _manifest()[1]


def _owned(pdf, ref, cls):
    require(type(ref) is bytes and len(ref) <= 20 and re.fullmatch(rb"[1-9][0-9]* 0 R", ref))
    index = int(ref.split()[0])
    require(index < len(pdf.objects))
    obj = pdf.objects[index]
    require(isinstance(obj, cls) and obj.number == index and obj.free == "n"
            and obj.generation == 0 and obj.reference == ref)
    return obj


def _numeric(value):
    import pydyf
    require(type(value) in (int, float) and -10**12 <= value <= 10**12 and isfinite(value))
    return Decimal(pydyf._to_bytes(value).decode("ascii"))


def _page_sizes(document, pdf):
    import pydyf
    require(_owned(pdf, pdf.catalog.reference, pydyf.Dictionary) is pdf.catalog
            and pdf.catalog.get("Type") == "/Catalog")
    pages = pdf.pages
    count = pages["Count"]
    require(_owned(pdf, pdf.catalog["Pages"], pydyf.Dictionary) is pages
            and pages["Type"] == "/Pages" and type(count) is int
            and 1 <= count <= 64 and count == len(document.pages)
            and type(pages["Kids"]) is pydyf.Array and len(pages["Kids"]) == count * 3
            and all(key not in pages for key in ("Rotate", "UserUnit", "CropBox")))
    sizes = {}
    for i, layout in enumerate(document.pages):
        n, generation, marker = pages["Kids"][i * 3:i * 3 + 3]
        require(type(n) is int and 0 < n < len(pdf.objects) and type(generation) is int
                and generation == 0 and marker == "R")
        ref = f"{n} 0 R".encode("ascii")
        page = _owned(pdf, ref, pydyf.Dictionary)
        require(ref not in sizes and page["Type"] == "/Page" and page["Parent"] == pages.reference
                and all(key not in page for key in ("Rotate", "UserUnit", "CropBox")))
        require(len(layout.bleed) == 4 and all(key in layout.bleed for key in ("top", "bottom", "left", "right"))
                and all(type(v) in (int, float) and v == 0 for v in layout.bleed.values()))
        _numeric(layout.width)
        _numeric(layout.height)
        width, height = _numeric(layout.width * 0.75), _numeric(layout.height * 0.75)
        require(width > 0 and height > 0)
        for key in ("MediaBox", "TrimBox", "BleedBox"):
            box = page[key]
            require(type(box) is pydyf.Array and len(box) == 4
                    and tuple(map(_numeric, box)) == (0, 0, width, height))
        stream = _owned(pdf, page["Contents"], pydyf.Stream)
        h = pydyf._to_bytes(layout.height * 0.75)
        require(stream.stream[:3] == [b"1 0 0 -1 0 " + h + b" cm", b"q", b"0.75 0 0 0.75 0 0 cm"])
        sizes[ref] = width, height
    return sizes


def _box(attr, size):
    import pydyf
    require(type(attr) is pydyf.Dictionary and attr.number is None
            and len(attr) == 2 and "BBox" in attr and attr.get("O") == "/Layout")
    box = attr["BBox"]
    require(type(box) is pydyf.Array and len(box) == 4)
    x0, y0, x1, y1 = map(_numeric, box)
    w, h = size
    with localcontext() as ctx:
        ctx.prec = 48
        s = Decimal("0.75")
        result = (s*x0, h-s*y1, s*x1, h-s*y0)
    require(0 <= result[0] < result[2] <= w and 0 <= result[1] < result[3] <= h)
    return pydyf.Array([format(number, "f").encode("ascii") for number in result])


def _identifier(obj):
    import pydyf
    require(type(obj) is pydyf.String and type(obj.string) is str and 1 <= len(obj.string) <= 128
            and re.fullmatch(r"[1-9][0-9]*-(?:0|[1-9][0-9]*)-(?:0|[1-9][0-9]*)", obj.string))
    return obj.string.encode("ascii")


def emit_structure(document, pdf) -> dict:
    import pydyf
    require(type(pdf) is pydyf.PDF)
    sizes = _page_sizes(document, pdf)
    root = _owned(pdf, pdf.catalog["StructTreeRoot"], pydyf.Dictionary)
    require(root["Type"] == "/StructTreeRoot" and "IDTree" not in root and
            type(root["K"]) is pydyf.Array and len(root["K"]) == 1)
    parent_tree = _owned(pdf, root["ParentTree"], pydyf.Dictionary)
    require(parent_tree is not root and len(parent_tree) == 1 and "Nums" in parent_tree)
    nums = parent_tree["Nums"]
    require(type(nums) is pydyf.Array and len(nums) == 2 * len(sizes))
    owner_count = 0
    for page_index in range(len(sizes)):
        key, owners = nums[2 * page_index:2 * page_index + 2]
        require(type(key) is int and key == page_index and type(owners) is pydyf.Array)
        owner_count += len(owners)
        require(owner_count <= 20000)
        for reference in owners:
            owner = _owned(pdf, reference, pydyf.Dictionary)
            require(owner.get("Type") == "/StructElem")
    pending = [(root["K"][0], root.reference, 1, None)]
    seen, ids, headers = set(), {}, []
    figure = None
    charge = 0
    while pending:
        ref, parent, depth, table = pending.pop()
        node = _owned(pdf, ref, pydyf.Dictionary)
        require(node is not parent_tree and depth <= 64 and ref not in seen and node["Type"] == "/StructElem" and node["P"] == parent)
        seen.add(ref)
        role = node["S"]
        if depth == 1:
            require(role == "/Document" and "Pg" not in node)
        else:
            require(role != "/Document" and node["Pg"] in sizes)
        if role == "/Table":
            table = node.number
        if role == "/Figure":
            require(figure is None)
            converted = _box(node["A"], sizes[node["Pg"]])
            figure = node, pydyf.Dictionary({"O": "/Layout", "BBox": converted})
        if role == "/TH" or "ID" in node:
            require(role == "/TH" and table is not None and "ID" in node)
            identifier = _identifier(node["ID"])
            require(identifier.startswith(f"{table}-".encode()) and identifier not in ids
                    and len(ids) < 10000)
            ids[identifier] = node["ID"], ref
        attr = node.get("A")
        if role in ("/TH", "/TD"):
            require(type(attr) is pydyf.Dictionary and attr.get("O") == "/Table"
                    and ("Headers" in attr) == (role == "/TD"))
        if attr is not None:
            require(role in ("/TH", "/TD", "/Figure") and type(attr) is pydyf.Dictionary)
            if "Headers" in attr:
                values = attr["Headers"]
                require(role == "/TD" and table is not None and attr.get("O") == "/Table"
                        and type(values) is pydyf.Array and 0 < len(values)
                        and len(headers) + len(values) <= 20000)
                headers.extend((_identifier(value), value, table) for value in values)
        kids = node["K"]
        require(type(kids) is pydyf.Array)
        charge += 1 + len(kids)
        require(charge <= 20000)
        for child in kids:
            if type(child) is int:
                require(0 <= child < 20000)
            else:
                require(type(child) is bytes and depth < 64)
                pending.append((child, ref, depth + 1, table))
    require(figure is not None)
    for key, value, table in headers:
        require(key in ids and ids[key][0] is value and key.startswith(f"{table}-".encode()))
    if ids:
        names = pydyf.Array([item for key in sorted(ids) for item in ids[key]])
        index = pydyf.Dictionary({"Names": names})
        pdf.add_object(index)
        root["IDTree"] = index.reference
    figure_node, layout = figure
    figure_node["A"] = layout
    return {"figure_count": 1, "id_count": len(ids)}


def _compose_finisher(original):
    used = False

    def finisher(selection, document, pdf):
        nonlocal used
        require(not used)
        used = True
        original(selection, document, pdf)
        emit_structure(document, pdf)

    finisher._typed_brand = True
    return finisher


def install_in_isolated_render_worker() -> None:
    from reporting.pdf_font_selection import LatoFontSelection
    require(sys.flags.isolated and sys.dont_write_bytecode and
            Path(sys.argv[0]).resolve() == Path(__file__).with_name("_pdf_typed_brand_render_worker.py")
            and not getattr(LatoFontSelection.finisher, "_typed_brand", False))
    LatoFontSelection.finisher = _compose_finisher(LatoFontSelection.finisher)
