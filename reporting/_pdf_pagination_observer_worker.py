"""Guarded separate observer for literal PDF 1.7 /Pagination /Footer artifacts.

The inherited typed observer still validates every *semantic* operator, text
mapping, MCID, ParentTree, page and passive object. This child additionally
accounts for every removed artifact event and decodes its literal page number.
It never edits or rewrites the PDF and does not qualify reading order or PDF/UA.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reporting import _pdf_typed_observation_worker as typed
from reporting import _pdf_pagination_core as pagination
from reporting import pdf_typed_observation as contract
from reporting._pdf_process_sandbox import enforce_pdf_landlock, enforce_pdf_network_filter
from reporting._pdf_render_worker import _write_bytes

PROFILE_ID = "typed-pdf-pagination-artifact-observation-v1"
PROTOCOL_VERSION = 2
_LIMITS = dict(contract.LIMITS)
_OBSERVER_SOURCE_PROFILE = contract._profile_contract()


def observer_profile() -> dict:
    return {
        "schema_version": "1.0.0", "profile_id": PROFILE_ID,
        "protocol_version": PROTOCOL_VERSION, "parser": "pypdf:7.1.0",
        "typed_parent_profile": contract.PROFILE_ID,
        "typed_parent_policy": _OBSERVER_SOURCE_PROFILE,
        "pagination_type": "Pagination", "pagination_subtype": "Footer",
        "pagination_sequence": ["BDC", "gs", "rg", "BT", "Tm", "Tf", "TJ", "ET", "EMC"],
        "page_labels": "EXPLICIT_DECIMAL_ONE_BASED",
        "pages": contract.MAX_PAGES, "pdf_bytes": contract.MAX_PDF_BYTES,
        "result_bytes": contract.RESULT_BYTES, "limits": dict(_LIMITS),
        "default_pipeline_activated": False,
        "release_authorized": False,
        "aggregate_job_resources_qualified": False,
    }


def observer_profile_sha256() -> str:
    return sha256(json.dumps(observer_profile(), sort_keys=True,
                           separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()


def _require(value: object) -> None:
    if not value:
        raise ValueError("pagination artifact observation refused")


def _observe_folio(pdf: bytes, digest: str, limits: dict) -> dict:
    from pypdf import PdfReader, generic
    from io import BytesIO

    original_content = generic.ContentStream
    observed = []
    total_operators = []
    validated_artifacts = []

    class PaginationContentStream(original_content):
        def __init__(self, source, reader, *args, **kwargs):
            super().__init__(source, reader, *args, **kwargs)
            operations = list(self.operations)
            _require(0 < len(operations) <= contract.MAX_OPERATIONS)
            remaining, fragments = [], []
            semantic = False
            index = 0
            while index < len(operations):
                arguments, operator = operations[index]
                if operator == b"BDC":
                    _require(len(arguments) == 2)
                    if arguments[0] == "/Artifact":
                        _require(not semantic and not fragments
                                 and type(arguments[1]) is generic.DictionaryObject
                                 and set(arguments[1]) == {"/Type", "/Subtype"}
                                 and arguments[1].raw_get("/Type") == "/Pagination"
                                 and arguments[1].raw_get("/Subtype") == "/Footer")
                        items = operations[index + 1:index + 9]
                        _require(len(items) == 8 and [op for _, op in items] == [
                            b"gs", b"rg", b"BT", b"Tm", b"Tf", b"TJ", b"ET", b"EMC"])
                        _require(len(items[0][0]) == 1
                                 and type(items[0][0][0]) is generic.NameObject
                                 and len(items[1][0]) == 3
                                 and all(type(x) in (generic.NumberObject, generic.FloatObject)
                                         and 0 <= float(x) <= 1 for x in items[1][0])
                                 and not items[2][0] and len(items[3][0]) == 6
                                 and len(items[4][0]) == 2
                                 and type(items[4][0][0]) is generic.NameObject
                                 and type(items[4][0][1]) in (
                                     generic.NumberObject, generic.FloatObject)
                                 and float(items[4][0][1]) > 0
                                 and len(items[5][0]) == 1
                                 and type(items[5][0][0]) is generic.ArrayObject
                                 and 1 <= len(items[5][0][0]) <= contract.MAX_OPERATIONS
                                 and not items[6][0] and not items[7][0])
                        for coordinate in items[3][0]:
                            _require(type(coordinate) in (
                                generic.NumberObject, generic.FloatObject))
                            typed._number(coordinate)
                        for value in items[5][0][0]:
                            if type(value) in (generic.NumberObject, generic.FloatObject):
                                typed._number(value)
                            else:
                                _require(type(value) is generic.ByteStringObject
                                         and 0 < len(bytes(value)) <= contract.MAX_TEXT_BYTES)
                        fragments.append(items)
                        index += 9
                        continue
                    _require(arguments[0] == "/Span" and not semantic)
                    semantic = True
                elif operator == b"EMC":
                    _require(semantic)
                    semantic = False
                remaining.append((arguments, operator))
                index += 1
            _require(not semantic and len(fragments) == 1)
            observed.append(fragments[0])
            total_operators.append(len(operations))
            _require(sum(total_operators) <= contract.MAX_OPERATIONS)
            self.operations = remaining

    # Reader page labels are defaults if PageLabels is missing: require catalog.
    reader = PdfReader(BytesIO(pdf), strict=True)
    _require(not reader.is_encrypted and 1 <= len(reader.pages) <= contract.MAX_PAGES)
    root = reader.root_object
    _require("/PageLabels" in root)
    labels_obj = typed._dictionary(root.raw_get("/PageLabels"), reader)
    _require(set(labels_obj) == {"/Nums"})
    pairs = labels_obj.raw_get("/Nums")
    _require(type(pairs) is generic.ArrayObject and len(pairs) == 2
             and type(pairs[0]) is generic.NumberObject and int(pairs[0]) == 0
             and type(pairs[1]) is generic.DictionaryObject
             and set(pairs[1]) == {"/S", "/St"}
             and type(pairs[1].raw_get("/S")) is generic.NameObject
             and pairs[1].raw_get("/S") == "/D"
             and type(pairs[1].raw_get("/St")) is generic.NumberObject
             and int(pairs[1].raw_get("/St")) == 1)
    labels = reader.page_labels
    _require(labels == [str(i) for i in range(1, len(reader.pages) + 1)])

    generic.ContentStream = PaginationContentStream
    try:
        record = typed._inspect(pdf, digest, limits)
    finally:
        generic.ContentStream = original_content

    _require(len(observed) == len(labels) == record["page_count"])
    fonts = {tuple(font["reference"]): dict(font["mapping"])
             for font in record["trace"]["fonts"]}
    folia = []
    for number, (page, sequence) in enumerate(zip(
            record["trace"]["pages"], observed, strict=True), start=1):
        state = str(sequence[0][0][0])
        _require(state in {value["name"] for value in page["graphics_states"]})
        resource = str(sequence[4][0][0])
        matched = [tuple(ref["reference"]) for ref in page["font_resources"]
                   if ref["name"] == resource]
        _require(len(matched) == 1 and matched[0] in fonts)
        cmap = fonts[matched[0]]
        code_array = sequence[5][0][0]
        result = []
        for value in code_array:
            if type(value) is generic.ByteStringObject:
                encoded = bytes(value).hex()
                _require(encoded and len(encoded) % 4 == 0)
                for pos in range(0, len(encoded), 4):
                    code = encoded[pos:pos + 4]
                    _require(code in cmap)
                    result.append(cmap[code])
            else:
                _require(type(value) in (generic.FloatObject, generic.NumberObject))
        text = "".join(result)
        _require(text == str(number))
        folia.append(text)
    _require(folia == labels)
    return {
        "protocol_version": PROTOCOL_VERSION, "profile_id": PROFILE_ID,
        "profile_sha256": observer_profile_sha256(),
        "pdf_sha256": digest, "status": "OBSERVED_NATIVE_PAGINATION_ARTIFACTS",
        "page_count": len(labels), "page_labels": labels,
        "literal_artifact_numbers": folia,
        "total_physical_operators": sum(total_operators),
        "semantic_observation": record,
        "limits": limits, "release_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    if (sys.platform != "linux" or not sys.flags.isolated or not sys.dont_write_bytecode
            or type(argv) is not list or len(argv) != 4
            or any(type(item) is not str or not item.isascii() or not item.isdecimal()
                   for item in argv[1:])):
        return 64
    try:
        source, output, parent = map(int, argv[1:])
        _require(min(source, output) >= 3 and source != output and parent >= 2)
        info = [os.fstat(fd) for fd in (source, output)]
        _require(all(stat.S_ISREG(item.st_mode) and item.st_nlink == 0
                     and item.st_uid == os.geteuid() for item in info)
                 and info[0].st_ino != info[1].st_ino and info[1].st_size == 0)
        limits = typed._limits()
        typed._lifetime(parent)
        enforce_pdf_landlock(Path(__file__))
        enforce_pdf_network_filter()
        pagination.require_source_manifest()
        digest, pdf = typed._read_request(source)
        observation = _observe_folio(pdf, digest, limits)
        encoded = contract._canonical(observation)
        _require(0 < len(encoded) <= contract.RESULT_BYTES)
        _write_bytes(output, encoded)
        return 0
    except Exception:
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
