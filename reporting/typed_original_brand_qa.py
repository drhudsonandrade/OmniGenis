"""Opt-in same-byte typed/original-brand QA; no visual, human or release authority.

The new native profile accounts for every admitted node and drawing before a
named text projection is matched. It never impersonates the older text-only
observation and never changes the candidate PDF or an existing PDF profile.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

from reporting import pdf_typed_observation as prior
from reporting._pdf_native_launch_budget import require_no_active_scope
from reporting._pdf_process_lifecycle import wait_owned_process_group
from reporting.brand_assets import BoundBrandAssets, REQUIRED_CREDIT_EN
from reporting.page_windows import plan_pdf_page_windows, bind_pdf_page_window_evidence
from reporting.pdf_document_metadata import inspect_pdf_document_metadata
from reporting.pdf_raster_windows import inspect_pdf_raster_window
from reporting.typed_html import capture_typed_document
from reporting.typed_original_brand_pdf import prepare_typed_original_brand_pdf
from reporting.typed_pdf_source import prepare_typed_pdf_source
from reporting.typed_pdf_association import _Matcher, _canonical as association_bytes

PROFILE_ID = "typed-original-brand-qa-v1"
STATUS = "TYPED_ORIGINAL_BRAND_CANDIDATE_INSPECTED"
RASTER_PAGES_PER_WINDOW = 4
TABLE_IDENTIFIER_POLICY = "PYPDF_TEXTSTRINGOBJECT_ONLY_ORIGINAL_BYTES_1_TO_128"
ID_TREE_POLICY = "FLAT_NAMES_EXACT_ORIGINAL_BYTE_ID_MAP"
# Figure: 506 drawing + 6 color + 6 paint + 7 gs + 5 cm + 2 q/Q + 2 marks.
# The two nonidentity source group transforms require the q/Q pair.
# Add the 6-operation DNA Artifact and 3-operation Form.
MIN_FIGURE_OPERATIONS = 534
DNA_ARTIFACT_OPERATIONS = 6
MIN_FIXED_BRAND_OPERATIONS = 543
ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas/report-typed-original-brand-qa.v1.schema.json"
MANIFEST = ROOT / "reporting/pdf-typed-original-brand-qa.v1.json"
BRAND_TEXT = ("Original DNA illustration", "Visual asset credits", REQUIRED_CREDIT_EN)
LOGO_ALT = "OmniGenis horizontal lockup master"
DNA_RGB_SHA256 = "91c9216f603d9f9f70b9a92e8f6a3b26b5bc1ebd18a0bb3a1191449b314c1a28"
DISABLE_FLAGS = (
    "OMNIGENIS_TYPED_ORIGINAL_BRAND_QA_DISABLED",
    "OMNIGENIS_TYPED_PDF_OBSERVATION_DISABLED",
    "OMNIGENIS_PDF_DOCUMENT_METADATA_DISABLED",
    "OMNIGENIS_PDF_RASTER_WINDOWS_DISABLED",
)
SOURCE_FILES = (
    "reporting/pdf-typed-brand-render.v1.json",
    "reporting/pdf_typed_brand_render.py",
    "reporting/_pdf_typed_brand_emission.py",
    "reporting/_pdf_typed_brand_render_worker.py",
    "reporting/typed_original_brand_qa.py",
    "reporting/_pdf_typed_original_brand_qa_worker.py",
    "schemas/report-typed-original-brand-qa.v1.schema.json",
    "tests/test_reporting_typed_original_brand_qa.py",
    "reporting/typed_original_brand_pdf.py",
    "reporting/pdf-typed-observation.v1.json",
    "reporting/brand_assets.py",
    "reporting/pdf_original_brand_inclusion.py",
    "reporting/_pdf_original_dna_png_admission.py",
    "reporting/editorial_typed_pdf.py",
    "reporting/typed_pdf_source.py",
    "reporting/typed_pdf_association.py",
    "reporting/_pdf_original_svg_layout_worker.py",
    "reporting/assets/omnigenis_logo.svg",
    "reporting/assets/dna_column.png",
    "reporting/assets/brand-permission.md",
)
NONCLAIMS = (
    "visual_semantic_parity_verified", "human_assistive_accessibility_verified",
    "whole_job_resources_qualified", "pdf_ua_certified",
    "source_asset_bytes_authenticated_from_pdf", "release_authorized",
    "worker_results_authenticated", "scientific_equivalence_verified",
    "all_file_revisions_inspected", "default_pipeline_activated",
)


class TypedOriginalBrandQAError(ValueError):
    """A fixed refusal with no report text or partial success."""


def require(value: bool) -> None:
    if not value:
        raise ValueError("typed original-brand QA refused")


def canonical(value: object) -> bytes:
    return prior._canonical(value)


def profile_sha256() -> str:
    """Bind the separate contract and its inherited unchanged source closure."""
    prior._profile()
    require(not MANIFEST.is_symlink() and MANIFEST.stat().st_size <= 16384)
    raw = MANIFEST.read_bytes()
    data = prior._loads(raw.decode("ascii"))
    require(type(data) is dict and set(data) == {
        "schema_version", "profile_id", "sources", "max_pages", "max_pdf_bytes",
        "default_pipeline_activated", "release_authorized", "table_identifier_policy",
        "structure_id_tree_policy"})
    require(data["schema_version"] == "1.0.0" and data["profile_id"] == PROFILE_ID
            and type(data["max_pages"]) is int and data["max_pages"] == 64
            and type(data["max_pdf_bytes"]) is int
            and data["max_pdf_bytes"] == prior.MAX_PDF_BYTES
            and data["default_pipeline_activated"] is False
            and data["release_authorized"] is False
            and type(data["table_identifier_policy"]) is str
            and data["table_identifier_policy"] == TABLE_IDENTIFIER_POLICY
            and type(data["structure_id_tree_policy"]) is str
            and data["structure_id_tree_policy"] == ID_TREE_POLICY
            and type(data["sources"]) is dict and set(data["sources"]) == set(SOURCE_FILES))
    for name, expected in data["sources"].items():
        require(type(expected) is str and prior._DIGEST.fullmatch(expected) is not None)
        path = ROOT / name
        require(not path.is_symlink() and path.resolve().is_relative_to(ROOT)
                and sha256(path.read_bytes()).hexdigest() == expected)
    return sha256(raw).hexdigest()


def _admit(expected: object) -> str:
    require_no_active_scope()
    require(sys.platform == "linux"
            and os.environ.get("OMNIGENIS_PDF_QUALIFICATION_RECEIPT_MODE") == "qualification-only"
            and not any(os.environ.get(k) == "1" for k in DISABLE_FLAGS)
            and type(expected) is str and prior._DIGEST.fullmatch(expected) is not None)
    actual = profile_sha256()
    require(actual == expected)
    return actual


def _schema(value: object, definition: str | None = None) -> None:
    from jsonschema import Draft202012Validator

    require(not SCHEMA.is_symlink() and SCHEMA.stat().st_size <= 262144)
    schema = json.loads(SCHEMA.read_text(encoding="ascii"))
    if definition is not None:
        schema = {"$schema": schema["$schema"], "$defs": schema["$defs"],
                  "$ref": "#/$defs/" + definition}
    Draft202012Validator(schema).validate(value)


def project_text_trace(trace: dict) -> tuple[dict, dict]:
    """Validate the full graph, then account for the exact nontext wrappers.

    Real references and MCIDs survive. The result is a projection, not an older
    TypedPdfObservation or a rewritten PDF object graph.
    """
    ref = prior._reference
    nodes = {ref(n["reference"]): n for n in trace["nodes"]}
    pages = {ref(p["reference"]): p for p in trace["pages"]}
    root = ref(trace["root_reference"])
    require(len(nodes) == len(trace["nodes"]) and len(pages) == len(trace["pages"]))
    require(root not in nodes and root not in pages and not set(nodes).intersection(pages))
    require(len(trace["root_children"]) == 1)
    document = nodes[ref(trace["root_children"][0])]
    require(document["role"] == "/Document" and document["page_reference"] is None)
    seen, visited, owners, headers, cells = [], set(), {}, {}, []
    visits = 0
    roles = {
        **prior._CHILD_ROLES,
        "/Document": prior._CHILD_ROLES["/Document"] | {"/Div", "/Sect"},
        "/Div": {"/Figure"}, "/Sect": {"/H2", "/P", "/Div"}, "/Figure": set(),
    }

    def walk(key, parent, page, table, depth):
        nonlocal visits
        require(key in nodes and key not in visited and depth <= prior.MAX_DEPTH)
        visited.add(key)
        seen.append(key)
        node = nodes[key]
        require(ref(node["parent"]) == parent and node["role"] in roles)
        role = node["role"]
        current = None if node["page_reference"] is None else ref(node["page_reference"])
        if parent == root:
            require(role == "/Document" and current is None)
        else:
            require(current in pages and role in roles[nodes[parent]["role"]]
                    and (page is None or page == current))
        if role == "/Table":
            table = key
        kids = node["children"]
        visits += 1 + len(kids)
        require(visits <= prior.MAX_VISITS)
        require(node["children_form"] in {"array", "reference", "mcid"})
        if node["children_form"] != "array":
            require(len(kids) == 1 and kids[0]["kind"] ==
                    ("node" if node["children_form"] == "reference" else "mcid"))
        if not kids:
            require(role in {"/TBody", "/Div"} and node["children_form"] == "array")
        if role in {"/TH", "/TD"}:
            require(node["table_attribute"] is True and table is not None)
            row = nodes[parent]
            group = nodes[ref(row["parent"])]
            require(row["role"] == "/TR" and group["role"] ==
                    ("/THead" if role == "/TH" else "/TBody"))
            if role == "/TH":
                require(node["native_id"] is not None and node["native_id"] not in headers
                        and not node["headers"])
                headers[node["native_id"]] = (table, current)
            else:
                require(node["native_id"] is None and len(node["headers"]) == 1)
                cells.append((node["headers"][0], table, current))
        else:
            require(node["table_attribute"] is False and node["native_id"] is None
                    and not node["headers"])
        if role == "/Figure":
            require(node["alternate_text"] == LOGO_ALT and len(node["figure_bbox"]) == 4)
        else:
            require(node["alternate_text"] is None and node["figure_bbox"] is None)
        if role in {"/Span", "/Figure"}:
            require(len(kids) == 1 and kids[0]["kind"] == "mcid")
        for ordinal, child in enumerate(kids):
            require(child["ordinal"] == ordinal)
            if child["kind"] == "node":
                walk(ref(child["reference"]), key, current, table, depth + 1)
            else:
                require(child["kind"] == "mcid" and role in {"/Span", "/Figure"}
                        and type(child["value"]) is int and 0 <= child["value"] < prior.MAX_VISITS)
                slot = current, child["value"]
                require(slot not in owners)
                owners[slot] = key

    walk(ref(document["reference"]), root, None, None, 1)
    require(seen == [ref(n["reference"]) for n in trace["nodes"]])
    require(sum(len(p["mcid_owners"]) for p in trace["pages"]) <= prior.MAX_VISITS)
    reverse = {}
    for index, page in enumerate(trace["pages"]):
        require(page["index"] == index)
        for mcid, owner in enumerate(page["mcid_owners"]):
            require(owner["mcid"] == mcid)
            reverse[(ref(page["reference"]), mcid)] = ref(owner["structure_reference"])
    require(owners == reverse and bool(owners)
            and len({p["struct_parents"] for p in trace["pages"]}) == len(pages))
    require(all(headers.get(h) == (table, page) for h, table, page in cells))

    def children(node):
        require(all(k["kind"] == "node" for k in node["children"]))
        return [nodes[ref(k["reference"])] for k in node["children"]]

    top = children(document)
    require(len(top) >= 4 and top[0]["role"] == "/Div"
            and [n["role"] for n in top[-2:]] == ["/Sect", "/Sect"])
    logo = children(top[0])
    dna = children(top[-2])
    credit = children(top[-1])
    require(len(logo) == 1 and logo[0]["role"] == "/Figure"
            and [n["role"] for n in dna] == ["/H2", "/Div"]
            and not dna[1]["children"]
            and [n["role"] for n in credit] == ["/H2", "/P"])
    require(ref(logo[0]["page_reference"]) == ref(trace["pages"][0]["reference"])
            and ref(top[-2]["page_reference"]) == ref(trace["pages"][-1]["reference"])
            and ref(top[-1]["page_reference"]) == ref(trace["pages"][-1]["reference"]))
    require(all(n["role"] in prior._CHILD_ROLES["/Document"] for n in top[1:-2]))
    removed = {ref(n["reference"]) for n in (top[0], logo[0], top[-2], dna[1], top[-1])}
    require(len(removed) == 5 and sum(n["role"] == "/Figure" for n in nodes.values()) == 1)
    projection = deepcopy(trace)
    projection["nodes"] = [n for n in projection["nodes"] if ref(n["reference"]) not in removed]
    projected = {ref(n["reference"]): n for n in projection["nodes"]}
    flattened = top[1:-2] + [dna[0]] + credit
    projected[ref(document["reference"])]["children"] = [
        {"ordinal": i, "kind": "node", "reference": n["reference"]}
        for i, n in enumerate(flattened)]
    projected[ref(document["reference"])]["children_form"] = "array"
    for n in flattened:
        projected[ref(n["reference"])]["parent"] = document["reference"]
    for page in projection["pages"]:
        page["mcid_owners"] = [m for m in page["mcid_owners"]
                              if ref(m["structure_reference"]) not in removed]
    return projection, {
        "native_nodes": len(nodes), "projected_nodes": len(projected),
        "accounted_brand_wrapper_nodes": len(removed), "native_mcids": len(owners),
        "text_mcids": len(owners) - 1, "figure_count": 1,
    }


def _validate_native(record: dict, pdf: bytes, profile: str) -> tuple[dict, dict]:
    _schema(record, "native")
    require(record["profile_sha256"] == profile
            and record["pdf_sha256"] == sha256(pdf).hexdigest()
            and record["pdf_byte_count"] == len(pdf)
            and record["page_count"] == len(record["trace"]["pages"]))
    prior._reference_consistency(record["trace"])
    shared_contents = {}
    for page in record["trace"]["pages"]:
        key = prior._reference(page["contents_reference"])
        facts = canonical({name: page[name] for name in (
            "original_operator_count", "projected_operator_ordinals",
            "outside_operator_ordinals", "graphic_blocks")})
        require(key not in shared_contents or shared_contents[key] == facts)
        shared_contents[key] = facts
    trace, counts = project_text_trace(record["trace"])
    figure = next(n for n in record["trace"]["nodes"] if n["role"] == "/Figure")
    figure_slot = (prior._reference(figure["page_reference"]),
                   figure["children"][0]["value"])
    # The validated graph binds the DNA section to the final page.
    dna_page = record["trace"]["pages"][-1]["reference"]
    fonts = {prior._reference(f["reference"]): f for f in trace["fonts"]}
    require(len(fonts) == len(trace["fonts"]))
    for font in fonts.values():
        mapping = dict(font["mapping"])
        require(len(mapping) == len(font["mapping"])
                and len(mapping) <= font["explicit_cid_width_count"])
        for text in mapping.values():
            require(1 <= len(text.encode("utf-16-be", "strict")) // 2 <= 16)
    require(sum(font["cmap_decoded_bytes"] for font in fonts.values())
            <= prior.MAX_CMAP_TOTAL
            and sum(len(font["mapping"]) for font in fonts.values())
            <= prior.MAX_CMAP_RECORDS)
    require(sum(page["decoded_stream_bytes"] for page in trace["pages"])
            <= prior.MAX_STREAM_BYTES)
    operations = text_bytes = figures = artifacts = dna_paints = 0
    for original, page in zip(record["trace"]["pages"], trace["pages"], strict=True):
        for state in page["graphics_states"]:
            alpha = [value for value in (state["fill_alpha"], state["stroke_alpha"])
                     if value is not None]
            require(len(alpha) == 1 and prior._num(alpha[0]) == 1)
        mapped = original["projected_operator_ordinals"]
        require(len(mapped) == len(page["events"]) and mapped == sorted(set(mapped)))
        in_span = False
        for index, event in enumerate(page["events"]):
            require(in_span or event["operator"] == "BDC")
            if in_span:
                require(mapped[index] == mapped[index - 1] + 1)
            if event["operator"] == "BDC":
                in_span = True
            elif event["operator"] == "EMC":
                in_span = False
        classified = set(mapped)
        for block in original["graphic_blocks"]:
            block_operations = block["last_operator"] - block["first_operator"] + 1
            if block["kind"] == "figure":
                require(block_operations >= MIN_FIGURE_OPERATIONS
                        and block["dna_paints"] == 0 and type(block["mcid"]) is int
                        and (prior._reference(original["reference"]), block["mcid"])
                        == figure_slot)
            else:
                require(block["kind"] == "artifact" and block["mcid"] is None
                        and (block["dna_paints"] == 0
                             or (original["reference"] == dna_page
                                 and block_operations == DNA_ARTIFACT_OPERATIONS)))
            span = set(range(block["first_operator"], block["last_operator"] + 1))
            require(block["first_operator"] < block["last_operator"]
                    and not span.intersection(classified))
            classified.update(span)
            figures += block["kind"] == "figure"
            artifacts += block["kind"] == "artifact"
            dna_paints += block["dna_paints"]
        outside = original["outside_operator_ordinals"]
        require(outside == sorted(set(outside)) and not set(outside).intersection(classified))
        classified.update(outside)
        require(classified == set(range(original["original_operator_count"])))
        replayed = prior._replay(page, fonts)
        require(replayed == page["runs"])
        operations += original["original_operator_count"]
        text_bytes += sum(r["utf8_bytes"] for r in replayed)
    operations += record["graphics"]["form_operations"]
    require(operations <= prior.MAX_OPERATIONS and text_bytes <= prior.MAX_TEXT_BYTES
            and figures == 1 and dna_paints == 1 and artifacts > 0
            and record["graphics"]["dna_rgb_sha256"] == DNA_RGB_SHA256
            and record["graphics"]["figure_count"] == figures
            and record["graphics"]["artifact_count"] == artifacts
            and record["graphics"]["dna_paints"] == dna_paints
            and record["graphics"]["operations"] == operations)
    counts["operations"] = operations
    return trace, counts


def _run_worker(pdf: bytes, digest: str) -> bytes:
    """Own one fixed native worker and reap its group before interpreting a result."""
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as output:
        fds = source.fileno(), output.fileno()
        infos = [os.fstat(fd) for fd in fds]
        require(all(stat.S_ISREG(x.st_mode) and x.st_nlink == 0
                    and x.st_uid == os.geteuid() for x in infos)
                and (infos[0].st_dev, infos[0].st_ino) != (infos[1].st_dev, infos[1].st_ino))
        source.write(b"1 " + digest.encode("ascii") + b"\n" + pdf)
        source.flush()
        command = [sys.executable, "-I", "-B",
                   str(Path(__file__).with_name("_pdf_typed_original_brand_qa_worker.py")),
                   str(fds[0]), str(fds[1]), str(os.getpid())]
        process = subprocess.Popen(
            command, cwd=ROOT,
            env={"PATH": os.defpath, "LANG": "C.UTF-8",
                 "OMNIGENIS_PDF_QUALIFICATION_RECEIPT_MODE": "qualification-only"},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, pass_fds=fds, start_new_session=True)
        code = wait_owned_process_group(process, wall_seconds=10, cleanup_seconds=2)
        require(code == 0 and 0 < os.fstat(fds[1]).st_size <= prior.RESULT_BYTES)
        output.seek(0)
        return output.read(prior.RESULT_BYTES + 1)


def _associate(manifest: dict, physical: dict) -> dict:
    """Match the original units plus separately declared fixed brand text."""
    source = deepcopy(manifest)
    require(len(source["units"]) + 3 <= 20000)
    for i, text in enumerate(BRAND_TEXT):
        source["units"].append({
            "ordinal": len(source["units"]),
            "kind": "paragraph" if i == 2 else "section_heading",
            "component_ordinal": manifest["counts"]["components"] + i,
            "component_id": "fixed-brand-" + str(i), "section_id": "fixed-brand",
            "row_ordinal": None, "column_ordinal": None, "finding_id": None, "column_id": None,
            "utf8_bytes": len(text.encode("utf-8")), "scalars": len(text),
            "text_sha256": sha256(text.encode("utf-8")).hexdigest()})
    source["counts"]["components"] += 3
    matcher = _Matcher(source, physical)
    matcher.match()
    result = {"counts": matcher.counts(), "units": matcher.units,
              "table_segments": matcher.table_segments, "row_segments": matcher.row_segments}
    association_bytes(result)
    return result


def _validate_receipt(data: dict) -> None:
    _schema(data)
    windows = (data["page_count"] + RASTER_PAGES_PER_WINDOW - 1) // RASTER_PAGES_PER_WINDOW
    coverage = data["coverage"]
    # The connected native tree charges N visits, N-1 node edges and M MCID edges.
    require(2 * coverage["native_nodes"] - 1 + coverage["native_mcids"] <= prior.MAX_VISITS)
    require(data["raster_inspected_pages"] == data["page_count"]
            and data["raster_window_count"] == windows
            and coverage["native_nodes"] == (coverage["projected_nodes"]
                                              + coverage["accounted_brand_wrapper_nodes"])
            and coverage["native_mcids"] == coverage["text_mcids"] + coverage["figure_count"])
    findings = data["represented_findings"]
    text_units = data["source_units"] + data["brand_text_units"]
    # Each finding needs a body TR, plus Table/THead/TBody/header TR.
    table_nodes = findings + 4 if findings else 0
    # Matched grammar has at most five non-root nodes per Span, plus one
    # potentially empty TBody per table; every matched table has a Span.
    require(data["source_units"] >= (findings + 3 if findings else 1)
            and data["pdf_byte_count"] >= len(b"%PDF-1.7")
            and coverage["text_mcids"] >= text_units
            and coverage["projected_nodes"] >= (coverage["text_mcids"] + text_units
                                                + 1 + table_nodes)
            and coverage["projected_nodes"] <= 1 + 6 * coverage["text_mcids"]
            and coverage["text_mcids"] >= data["page_count"]
            and coverage["operations"] >= 7 * coverage["text_mcids"] + MIN_FIXED_BRAND_OPERATIONS)


@dataclass(frozen=True, slots=True, repr=False)
class TypedOriginalBrandQA:
    """Immutable checksum-bound result; never a worker signature or release token."""
    _payload: bytes = field(repr=False)
    _sealed_sha256: str = field(init=False, repr=False)

    def __post_init__(self):
        require(type(self._payload) is bytes and 0 < len(self._payload) <= 65536)
        data = prior._loads(self._payload.decode("ascii"))
        _validate_receipt(data)
        object.__setattr__(self, "_sealed_sha256", sha256(self._payload).hexdigest())

    def to_dict(self) -> dict:
        require(type(self._payload) is bytes and 0 < len(self._payload) <= 65536
                and sha256(self._payload).hexdigest() == self._sealed_sha256)
        data = prior._loads(self._payload.decode("ascii"))
        _validate_receipt(data)
        return data

    def __repr__(self):
        return "TypedOriginalBrandQA()"


def validate_typed_original_brand_candidate(
    *, presentation_ir: object, expected_ir_sha256: object,
    expected_source_expectation_sha256: object, assets: BoundBrandAssets,
    expected_brand_bundle_sha256: object, pdf_bytes: object,
    expected_pdf_sha256: object, expected_profile_sha256: object,
) -> TypedOriginalBrandQA:
    """Inspect the exact caller candidate; never rerender or accept caller PASS flags."""
    try:
        profile = _admit(expected_profile_sha256)
        require(type(expected_source_expectation_sha256) is str
                and prior._DIGEST.fullmatch(expected_source_expectation_sha256) is not None)
        require(type(pdf_bytes) is bytes and pdf_bytes.startswith(b"%PDF-1.7")
                and 0 < len(pdf_bytes) <= prior.MAX_PDF_BYTES
                and type(expected_pdf_sha256) is str
                and prior._DIGEST.fullmatch(expected_pdf_sha256) is not None
                and sha256(pdf_bytes).hexdigest() == expected_pdf_sha256)
        doc = capture_typed_document(presentation_ir, expected_ir_sha256)
        source = prepare_typed_pdf_source(presentation_ir=doc, expected_ir_sha256=expected_ir_sha256)
        require(source.expectation_sha256 == expected_source_expectation_sha256)
        _, _, render_source = prepare_typed_original_brand_pdf(
            presentation_ir=doc, expected_ir_sha256=expected_ir_sha256,
            assets=assets, expected_brand_bundle_sha256=expected_brand_bundle_sha256)
        raw = _run_worker(pdf_bytes, expected_pdf_sha256)
        require(type(raw) is bytes and 0 < len(raw) <= prior.RESULT_BYTES)
        native = prior._loads(raw.decode("ascii"))
        trace, coverage = _validate_native(native, pdf_bytes, profile)
        associated = _associate(source.to_dict(),
                                {"trace": trace, "page_count": native["page_count"]})
        plan = plan_pdf_page_windows(
            pdf_sha256=expected_pdf_sha256, ir_sha256=expected_ir_sha256,
            page_count=native["page_count"], admitted_page_budget=64,
            pages_per_window=RASTER_PAGES_PER_WINDOW,
            inspector_profile_id="generated-pdf-raster-window-v1")
        metadata = inspect_pdf_document_metadata(
            pdf_bytes=pdf_bytes, plan=plan, expected_plan_sha256=plan.plan_sha256,
            expected_language="en-US")
        require(metadata.pdf_sha256 == expected_pdf_sha256
                and metadata.ir_sha256 == expected_ir_sha256
                and metadata.plan_sha256 == plan.plan_sha256
                and metadata.page_count == native["page_count"])
        receipts = []
        for window in plan.windows:
            _admit(profile)
            receipt = inspect_pdf_raster_window(
                pdf_bytes=pdf_bytes, plan=plan, window_index=window.index,
                expected_plan_sha256=plan.plan_sha256)
            require(receipt.pdf_sha256 == expected_pdf_sha256
                    and receipt.ir_sha256 == expected_ir_sha256
                    and receipt.plan_sha256 == plan.plan_sha256)
            receipts.append(receipt)
        ledger = bind_pdf_page_window_evidence(
            plan=plan, observations=tuple(r.to_observation() for r in receipts))
        inspected = sum(r.inspected_pages for r in receipts)
        require(inspected == native["page_count"] and _admit(profile) == profile)
        _, _, again = prepare_typed_original_brand_pdf(
            presentation_ir=doc, expected_ir_sha256=expected_ir_sha256,
            assets=assets, expected_brand_bundle_sha256=expected_brand_bundle_sha256)
        require(again == render_source)
        result = {
            "schema_version": "1.0.0", "profile_id": PROFILE_ID, "status": STATUS,
            "profile_sha256": profile, "pdf_sha256": expected_pdf_sha256,
            "pdf_byte_count": len(pdf_bytes), "page_count": native["page_count"],
            "ir_sha256": expected_ir_sha256,
            "source_expectation_sha256": source.expectation_sha256,
            "brand_bundle_sha256": expected_brand_bundle_sha256,
            "html_sha256": render_source["html_sha256"],
            "native_observation_sha256": sha256(raw).hexdigest(),
            "association_sha256": sha256(association_bytes(associated)).hexdigest(),
            "metadata_sha256": metadata.inspection_sha256,
            "raster_plan_sha256": plan.plan_sha256,
            "raster_ledger_sha256": sha256(canonical(ledger.to_dict())).hexdigest(),
            "represented_findings": source.to_dict()["counts"]["represented_findings"],
            "source_units": len(source.to_dict()["units"]), "brand_text_units": 3,
            "figure_count": 1, "dna_image_count": 1, "original_credit_count": 1,
            "raster_inspected_pages": inspected, "raster_window_count": len(receipts),
            "coverage": coverage, "stages": {
                "source_identity": "PASS", "native_structure_and_graphics": "PASS",
                "literal_source_association": "PASS", "controlled_metadata_and_catalog": "PASS",
                "all_page_raster": "PASS", "postflight_identity": "PASS"},
            "complete_literal_source_accounting": True,
            "complete_native_structure_accounting": True,
            "original_logo_source_geometry_matched": True, "original_dna_pixels_matched": True,
            **{key: False for key in NONCLAIMS},
        }
        for key in ("request_sha256", "source_binding_sha256", "plan_sha256", "ledger_sha256",
                    "content_sha256", "section_states_sha256"):
            result[key] = source.to_dict()[key]
        return TypedOriginalBrandQA(canonical(result))
    except Exception:
        raise TypedOriginalBrandQAError("typed_original_brand_qa_refused") from None
