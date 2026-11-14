"""Opt-in same-PDF typed text and complete raster-geometry candidate.

This operation owns generation and every inspection stage. It returns private
PDF bytes only after their exact source association and all raster windows pass.
The unsigned metadata does not authenticate execution or establish visual
semantics, glyph correctness, accessibility, aggregate resources or release.
"""
from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path
from time import monotonic_ns

from jsonschema import Draft202012Validator

from reporting._pdf_native_launch_budget import require_no_active_scope
from reporting._pdf_final_document_observation import (
    MAX_OBSERVATION_BYTES as MAX_PROCESS_OBSERVATION_BYTES,
)
from reporting._pdf_raster_child_observation import (
    validate_observation as _validate_raster_children,
)
from reporting.adapters import (
    FIXED_FONT_STYLE_ID, GENERATED_PDF_AUTHOR, GENERATED_PDF_PRODUCT,
    _FIXED_SEMANTIC_CSS,
)
from reporting.page_windows import (
    BoundPageWindowLedger, bind_pdf_page_window_evidence, plan_pdf_page_windows,
)
from reporting.pdf_generated_source_job import (
    PdfRenderedCandidate, RENDER_PROFILE_SHA256, _render_profile,
    _render_pdf_candidate, render_pdf_candidate,
)
from reporting.pdf_raster_windows import (
    IDENTITY_FIELDS as _RASTER_IDENTITY_FIELDS, PROTOCOL_VERSION as _RASTER_PROTOCOL,
    PdfRasterWindowInspection, inspect_pdf_raster_window,
    inspect_process_observed_pdf_raster_window,
)
from reporting.pdf_typed_observation import (
    RESULT_BYTES as MAX_OBSERVATION_BYTES, TypedPdfObservation,
    _DIGEST, _loads, _profile as _observer_profile,
    observe_typed_pdf, observe_typed_pdf_with_processes,
)
from reporting.typed_html import (
    TypedHtmlValidation, capture_typed_document, prepare_typed_html,
)
from reporting.typed_pdf_association import TypedPdfAssociation, associate_typed_pdf
from reporting.typed_pdf_source import TypedPdfSourceExpectation, prepare_typed_pdf_source

PROFILE_ID = 'typed-pdf-candidate-v1'
STATUS = 'TYPED_CANDIDATE_INSPECTED'
MAX_PDF_BYTES = 16777216
MAX_PAGES = 64
MAX_EVIDENCE_BYTES = 65536
RASTER_WIDTH = 4
RASTER_PROFILE_ID = 'generated-pdf-raster-window-v1'
OBSERVER_PROFILE_SHA256 = 'bb3993a3eaaca7cacf291d62c9068fa68b96cb4c0b637cdc6041f7d34018c92e'
PASSIVE_POLICY = 'EFFECTIVE_GRAPH_DENIALS_WITH_VERIFIED_TABLE_ATTRIBUTES_ONLY'
ROOT = Path(__file__).resolve().parents[1]
_PROFILE_FILE = ROOT / 'reporting/pdf-typed-candidate.v1.json'
_SCHEMA_FILE = ROOT / 'schemas/report-typed-pdf-candidate.v1.schema.json'
_RASTER_FIELDS = ('pdf_sha256', 'ir_sha256', 'plan_sha256', 'page_count',
                  'window_index', 'first_page', 'last_page', 'raster_sha256',
                  'total_pixels', 'min_edge_margin_px')
_SOURCE_FILES = (
    'reporting/pdf-typed-observation.v1.json',
    'reporting/typed_pdf_association.py',
    'reporting/typed_pdf_candidate.py',
    'reporting/typed_pdf_source.py',
    'schemas/report-typed-pdf-association.v1.schema.json',
    'schemas/report-typed-pdf-candidate.v1.schema.json',
    'schemas/report-typed-pdf-source.v1.schema.json',
)
_DISABLE_FLAGS = (
    'OMNIGENIS_TYPED_PDF_CANDIDATE_DISABLED',
    'OMNIGENIS_PDF_RENDERER_DISABLED',
    'OMNIGENIS_TYPED_PDF_OBSERVATION_DISABLED',
    'OMNIGENIS_PDF_RASTER_WINDOWS_DISABLED',
)
_NONCLAIMS = (
    'source_authenticity_verified', 'rendered_glyphs_verified',
    'visual_semantics_verified', 'reading_order_verified',
    'accessibility_qualified', 'scientific_equivalence_verified',
    'all_file_revisions_inspected', 'opaque_stream_payloads_inspected',
    'whole_pdf_safety_qualified', 'aggregate_job_resources_qualified',
    'worker_results_authenticated', 'default_pipeline_activated',
    'release_authorized',
)

_PROCESS_BASE_FIELDS = {
    'kind', 'worker', 'worker_source_sha256', 'parent_pid', 'pid',
    'spawn_monotonic_ns', 'exit_monotonic_ns', 'exit_code',
}
_PROCESS_WORKERS = {
    'render': '_pdf_render_worker.py',
    'typed_observation': '_pdf_typed_observation_worker.py',
    'raster_windows': '_pdf_raster_window_worker.py',
}
_PROCESS_BINDING_FIELDS = (
    'ir_sha256', 'request_sha256', 'source_binding_sha256', 'plan_sha256',
    'ledger_sha256', 'content_sha256', 'section_states_sha256',
    'source_expectation_sha256', 'typed_html_sha256', 'html_sha256',
    'render_result_sha256', 'observer_profile_sha256', 'association_sha256',
    'pdf_sha256', 'pdf_byte_count', 'page_count', 'raster_window_plan_sha256',
    'raster_ledger_sha256', 'raster_window_count', 'raster_inspected_pages',
)
_PROCESS_NONCLAIMS = (
    *_NONCLAIMS, 'authenticated_worker_execution', 'arbitrary_descendants_observed',
    'whole_graph_observed', 'native_profile_qualified', 'external_observer_measured',
    'supported_execution', 'hard_job_deadline_enforced', 'durable_observation_storage',
)
_PROCESS_ENVELOPE_FIELDS = {
    'schema', 'state', 'parent_pid', 'started_monotonic_ns', 'verified_monotonic_ns',
    'render_process', 'typed_observation_process', 'raster_windows',
    'observed_direct_spawns', 'observed_direct_exits', 'observed_nested_spawns',
    'observed_nested_exits', 'observed_total_spawns', 'observed_total_exits',
    'declared_retries', 'timer_helper_processes_added', 'timing_scope',
    'observation_sha256',
}
_PROCESS_TIMING = 'Parent launch observations, including raster launch lower bounds, and post-reap observations; not kernel event timestamps.'


class TypedPdfCandidateError(ValueError):
    """Fixed content-free refusal; no partial PDF is an accepted candidate."""


def _require(condition: bool) -> None:
    """Reject invalid conjunctions without exposing report contents."""
    if not condition:
        raise ValueError('typed candidate boundary refused')


def _canonical(value: object) -> bytes:
    """Encode bounded native metadata with a fixed ASCII JSON representation."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def _digest(value: object) -> None:
    """Require canonical native SHA-256 strings without coercion."""
    _require(type(value) is str and _DIGEST.fullmatch(value) is not None)


def _profile_contract() -> dict:
    """Closed composition, inheriting unchanged worker and runtime contracts."""
    return {
        'schema_version': '1.0.0', 'profile_id': PROFILE_ID,
        'input_profile_id': 'typed-report-projection-v1', 'locale_id': 'en-US',
        'source_profile_id': 'typed-report-pdf-source-v1',
        'association_profile_id': 'typed-report-pdf-association-v1',
        'observer_profile_sha256': OBSERVER_PROFILE_SHA256,
        'render_profile_sha256': RENDER_PROFILE_SHA256,
        'raster_profile_id': RASTER_PROFILE_ID,
        'font_style_id': FIXED_FONT_STYLE_ID,
        'html_wrapper_id': 'fixed-lato-typed-prewrap-v1',
        'literal_text_policy': 'SOURCE_LITERAL_UTF8_NO_NORMALIZATION',
        'passive_policy': PASSIVE_POLICY,
        'raster_scope': 'ALL_PHYSICAL_PAGES_NONBLANK_GEOMETRY_AND_EDGE_MARGIN_ONLY',
        'process_schedule': 'DECLARED_2_PLUS_2_WINDOWS_PLUS_PAGES',
        'limits': {'pdf_bytes': MAX_PDF_BYTES, 'pages': MAX_PAGES,
                   'source_units': 20000, 'evidence_bytes': MAX_EVIDENCE_BYTES,
                   'raster_pages_per_window': RASTER_WIDTH, 'raster_windows': 16,
                   'native_retries': 0},
        'disable_flags': list(_DISABLE_FLAGS),
        'default_pipeline_activated': False,
        'aggregate_job_resources_qualified': False,
        'release_authorized': False,
    }


def _profile(expected_sha256: object) -> str:
    """Check the closed manifest and both source closures before exposing bytes."""
    _digest(expected_sha256)
    _require(not any(os.environ.get(key) == '1' for key in _DISABLE_FLAGS))
    _require(not _PROFILE_FILE.is_symlink() and _PROFILE_FILE.stat().st_size <= 65536)
    with _PROFILE_FILE.open('rb') as stream:
        raw = stream.read(65537)
    _require(0 < len(raw) <= 65536)
    profile = _loads(raw.decode('ascii'))
    contract = _profile_contract()
    _require(type(profile) is dict and set(profile) == set(contract) | {'source_sha256'})
    sources = profile['source_sha256']
    _require(type(sources) is dict and set(sources) == set(_SOURCE_FILES))
    _require(_canonical({k: v for k, v in profile.items() if k != 'source_sha256'})
             == _canonical(contract))
    for name, expected in sources.items():
        _digest(expected)
        path = ROOT / name
        _require(not path.is_symlink() and path.resolve().is_relative_to(ROOT)
                 and sha256(path.read_bytes()).hexdigest() == expected)
    _require(_observer_profile() == OBSERVER_PROFILE_SHA256)
    _render_profile()
    actual = sha256(_canonical(profile)).hexdigest()
    _require(actual == expected_sha256)
    return actual


def _render_html(typed: str) -> str:
    """Use only the already validated inert body and the existing fixed wrapper."""
    prefix = ('<!doctype html><html lang="en-US"><head><meta charset="utf-8">'
              '<title>OmniGenis</title></head><body>')
    suffix = '</body></html>'
    _require(type(typed) is str and typed.startswith(prefix) and typed.endswith(suffix))
    body = typed[len(prefix):-len(suffix)]
    _require('<body' not in body and '</body' not in body)
    html = ('<!doctype html><html lang="en-US"><head><meta charset="utf-8">'
            f'<title>{GENERATED_PDF_PRODUCT}</title>'
            f'<meta name="author" content="{GENERATED_PDF_AUTHOR}">'
            f'<meta name="generator" content="{GENERATED_PDF_PRODUCT}">'
            f'<meta name="omnigenis-font-style" content="{FIXED_FONT_STYLE_ID}">'
            f'<style>{_FIXED_SEMANTIC_CSS}'
            'h1,h2,p,caption,th,td{white-space:pre-wrap}</style></head><body>'
            + body + suffix)
    _require(len(html) <= 67108864 and len(html.encode('utf-8')) <= 67108864)
    return html


def _raster_snapshot(row: dict) -> PdfRasterWindowInspection:
    """Own primitive receipt fields before another producer can alter its result."""
    result = PdfRasterWindowInspection(**{key: row[key] for key in _RASTER_FIELDS})
    _require(result.to_dict() == row)
    return result


def _validate_evidence(payload: bytes, pdf: bytes) -> dict:
    """Re-admit closed metadata and complete coverage; never authenticate execution."""
    _require(type(payload) is bytes and 0 < len(payload) <= MAX_EVIDENCE_BYTES)
    _require(type(pdf) is bytes and 0 < len(pdf) <= MAX_PDF_BYTES
             and pdf.startswith(b'%PDF-1.7'))
    value = _loads(payload.decode('ascii'))
    _require(type(value) is dict and _canonical(value) == payload)
    _profile(value['profile_sha256'])
    schema = _loads(_SCHEMA_FILE.read_text(encoding='ascii'))
    Draft202012Validator(schema).validate(value)
    _require(value['pdf_sha256'] == sha256(pdf).hexdigest()
             and value['pdf_byte_count'] == len(pdf)
             and value['observer_profile_sha256'] == OBSERVER_PROFILE_SHA256
             and all(type(flag) is bool for flag in value['claims'].values()))
    pages = value['page_count']
    plan = plan_pdf_page_windows(
        pdf_sha256=value['pdf_sha256'], ir_sha256=value['ir_sha256'],
        page_count=pages, admitted_page_budget=pages,
        pages_per_window=RASTER_WIDTH, inspector_profile_id=RASTER_PROFILE_ID)
    _require(plan.plan_sha256 == value['raster_window_plan_sha256']
             and plan.window_count == value['raster_window_count']
             and len(value['raster_windows']) == plan.window_count)
    receipts = []
    for window, row in zip(plan.windows, value['raster_windows'], strict=True):
        receipt = _raster_snapshot(row)
        expected = {
            'pdf_sha256': value['pdf_sha256'], 'ir_sha256': value['ir_sha256'],
            'plan_sha256': plan.plan_sha256, 'page_count': pages,
            'window_index': window.index, 'first_page': window.first_page,
            'last_page': window.last_page, 'profile_id': RASTER_PROFILE_ID,
        }
        _require(all(row[key] == item for key, item in expected.items()))
        receipts.append(receipt)
    ledger = bind_pdf_page_window_evidence(
        plan=plan, observations=tuple(item.to_observation() for item in receipts))
    _require(sha256(_canonical(ledger.to_dict())).hexdigest() == value['raster_ledger_sha256']
             and sum(item.inspected_pages for item in receipts)
             == value['raster_inspected_pages'] == pages)
    windows = plan.window_count
    _require(value['declared_process_schedule'] == {
        'renderer': 1, 'typed_observer': 1, 'raster_workers': windows,
        'raster_version_probes': windows, 'raster_page_executors': pages,
        'total': 2 + 2 * windows + pages, 'execution_authenticated': False,
    })
    return value



def _process_integer(value, minimum=0, maximum=(1 << 63) - 1) -> int:
    """Refuse coerced, boolean or unbounded process counters and local clocks."""
    _require(type(value) is int and minimum <= value <= maximum)
    return value


def _process_metadata(value, limit: int) -> dict:
    """Detach bounded canonical process metadata without retaining producer aliases."""
    _require(type(value) is dict)
    raw = _canonical(value)
    _require(0 < len(raw) <= limit)
    return json.loads(raw)


def _checked_process(record, kind: str, expected: dict) -> dict:
    """Recheck a closed unsigned row whose ownership was observed before reap."""
    _require(type(record) is dict and kind in _PROCESS_WORKERS
             and set(record) == _PROCESS_BASE_FIELDS | set(expected) | {'observation_sha256'})
    value = _process_metadata(record, 2048)
    worker = Path(__file__).with_name(_PROCESS_WORKERS[kind])
    fixed = {
        'kind': kind, 'worker': worker.name,
        'worker_source_sha256': sha256(worker.read_bytes()).hexdigest(),
        'parent_pid': os.getpid(), 'exit_code': 0, **expected,
    }
    _require(_canonical({key: value[key] for key in fixed}) == _canonical(fixed))
    _process_integer(value['parent_pid'], 2, 2147483647)
    _require(_process_integer(value['pid'], 2, 2147483647) != value['parent_pid'])
    _require(_process_integer(value['spawn_monotonic_ns'])
             <= _process_integer(value['exit_monotonic_ns']))
    _digest(value['observation_sha256'])
    _require(value['observation_sha256'] == sha256(_canonical({
        key: item for key, item in value.items() if key != 'observation_sha256'
    })).hexdigest())
    return value


def _checked_render_process(record, metadata: dict) -> dict:
    """Join the existing observed renderer record to its exact source and PDF."""
    expected = {
        'request_header_sha256': sha256(
            f"2 {metadata['html_sha256']}\n".encode('ascii')).hexdigest(),
        'html_sha256': metadata['html_sha256'], 'pdf_sha256': metadata['pdf_sha256'],
        'page_count': metadata['page_count'], 'render_sha256': metadata['render_result_sha256'],
        'profile_sha256': RENDER_PROFILE_SHA256,
    }
    return _checked_process(record, 'render', expected)


def _checked_typed_process(record, metadata: dict) -> dict:
    """Bind the physical observation to the sole owned typed-worker request."""
    expected = {
        'request_header_sha256': sha256(
            f"1 {metadata['pdf_sha256']}\n".encode('ascii')).hexdigest(),
        'pdf_sha256': metadata['pdf_sha256'], 'pdf_byte_count': metadata['pdf_byte_count'],
        'page_count': metadata['page_count'],
        'typed_observation_sha256': metadata['observation_sha256'],
        'profile_sha256': metadata['observer_profile_sha256'],
    }
    return _checked_process(record, 'typed_observation', expected)


def _checked_raster_processes(direct, children, receipt: dict) -> dict:
    """Rejoin a complete nested record to the same geometry and direct worker."""
    _require(type(direct) is dict and 'pid' in direct)
    pid = _process_integer(direct['pid'], 2, 2147483647)
    identity = {key: receipt[key] for key in _RASTER_IDENTITY_FIELDS}
    inspection = {key: receipt[key] for key in (
        'status', 'raster_sha256', 'total_pixels', 'min_edge_margin_px', 'inspected_pages')}
    nested = _validate_raster_children(children, identity, inspection, pid)
    header = ' '.join([str(_RASTER_PROTOCOL),
                       *(str(identity[key]) for key in _RASTER_IDENTITY_FIELDS)]) + '\n'
    expected = {
        'request_header_sha256': sha256(header.encode('ascii')).hexdigest(),
        'identity': identity, 'inspection_sha256': sha256(_canonical(receipt)).hexdigest(),
        'nested_observation_sha256': sha256(_canonical(nested)).hexdigest(),
    }
    worker = _checked_process(direct, 'raster_windows', expected)
    return {'window_index': identity['window_index'],
            'direct_process': worker, 'child_observation': nested}


def _process_order(record: dict, previous: int, pids: set, children=()) -> int:
    """Require sequential direct lifetimes and unique contained nested children."""
    _require(record['pid'] not in pids
             and previous <= record['spawn_monotonic_ns'] <= record['exit_monotonic_ns'])
    pids.add(record['pid'])
    for child in children:
        _require(child['pid'] not in pids
                 and child['parent_pid'] == record['pid']
                 and record['spawn_monotonic_ns'] <= child['observed_spawn_ns']
                 <= child['observed_exit_ns'] <= record['exit_monotonic_ns'])
        pids.add(child['pid'])
    return record['exit_monotonic_ns']


def _process_context(metadata: dict, candidate_sha256: str) -> dict:
    """Bind content-free source and result identities without changing the candidate."""
    _digest(candidate_sha256)
    return {
        'candidate_sha256': candidate_sha256,
        'candidate_profile_sha256': metadata['profile_sha256'],
        'typed_observation_sha256': metadata['observation_sha256'],
        **{key: metadata[key] for key in _PROCESS_BINDING_FIELDS},
    }


def _build_process_observation(metadata: dict, candidate_sha256: str, render,
                               typed, windows, started_ns, verified_ns) -> dict:
    """Reconcile every fixed stage and child with one complete same-PDF result."""
    started = _process_integer(started_ns)
    verified = _process_integer(verified_ns)
    _require(started <= verified and type(windows) is list
             and len(windows) == metadata['raster_window_count']
             and 1 <= len(windows) <= 16)
    render = _checked_render_process(render, metadata)
    typed = _checked_typed_process(typed, metadata)
    pids = {os.getpid()}
    previous = _process_order(render, started, pids)
    previous = _process_order(typed, previous, pids)
    checked_windows = []
    nested_count = 0
    for index, (entry, receipt) in enumerate(zip(
            windows, metadata['raster_windows'], strict=True)):
        _require(type(entry) is dict and set(entry) == {
            'window_index', 'direct_process', 'child_observation'}
            and _process_integer(entry['window_index']) == index)
        checked = _checked_raster_processes(
            entry['direct_process'], entry['child_observation'], receipt)
        children = checked['child_observation']['processes']
        previous = _process_order(checked['direct_process'], previous, pids, children)
        nested_count += len(children)
        checked_windows.append(checked)
    direct_count = 2 + len(checked_windows)
    total_count = len(pids) - 1
    _require(previous <= verified and nested_count == len(windows) + metadata['page_count']
             and total_count == direct_count + nested_count
             and total_count == 2 + 2 * len(windows) + metadata['page_count'])
    result = {
        'schema': 'typed-pdf-candidate-process-observation/v1',
        'state': 'COMPLETE_FIXED_TYPED_CANDIDATE_PROCESSES_OBSERVED',
        **_process_context(metadata, candidate_sha256),
        'parent_pid': os.getpid(), 'started_monotonic_ns': started,
        'verified_monotonic_ns': verified,
        'render_process': render, 'typed_observation_process': typed,
        'raster_windows': checked_windows,
        'observed_direct_spawns': direct_count, 'observed_direct_exits': direct_count,
        'observed_nested_spawns': nested_count, 'observed_nested_exits': nested_count,
        'observed_total_spawns': total_count, 'observed_total_exits': total_count,
        'declared_retries': 0, 'timer_helper_processes_added': 0,
        'timing_scope': _PROCESS_TIMING,
        **{key: False for key in _PROCESS_NONCLAIMS},
    }
    result['observation_sha256'] = sha256(_canonical(result)).hexdigest()
    return _process_metadata(result, MAX_PROCESS_OBSERVATION_BYTES)


def _validate_process_observation(value, payload: bytes, pdf: bytes) -> dict:
    """Refuse rehashed changes, extra fields or expanded claims in the optional sidecar."""
    metadata = _validate_evidence(payload, pdf)
    candidate_sha = sha256(payload).hexdigest()
    keys = (set(_process_context(metadata, candidate_sha))
            | _PROCESS_ENVELOPE_FIELDS | set(_PROCESS_NONCLAIMS))
    _require(type(value) is dict and set(value) == keys)
    snapshot = _process_metadata(value, MAX_PROCESS_OBSERVATION_BYTES)
    rebuilt = _build_process_observation(
        metadata, candidate_sha, snapshot['render_process'],
        snapshot['typed_observation_process'], snapshot['raster_windows'],
        snapshot['started_monotonic_ns'], snapshot['verified_monotonic_ns'])
    _require(_canonical(snapshot) == _canonical(rebuilt))
    return rebuilt


def _execute(presentation_ir, expected_ir_sha256,
             expected_source_expectation_sha256, expected_profile_sha256, *,
             observe_processes: bool = False) -> tuple:
    """Capture once and own every fixed stage, with optional private process evidence."""
    _require(type(observe_processes) is bool)
    started_ns = monotonic_ns() if observe_processes else None
    process_pids = {os.getpid()} if observe_processes else None
    previous_exit = started_ns
    raster_processes = []
    require_no_active_scope()
    profile_sha = _profile(expected_profile_sha256)
    _digest(expected_ir_sha256)
    _digest(expected_source_expectation_sha256)
    doc = capture_typed_document(presentation_ir, expected_ir_sha256)
    source = prepare_typed_pdf_source(
        presentation_ir=doc, expected_ir_sha256=expected_ir_sha256)
    _require(type(source) is TypedPdfSourceExpectation
             and source.expectation_sha256 == expected_source_expectation_sha256)
    manifest = source.to_dict()
    _require(0 < len(manifest['units']) <= 20000)
    typed, html_proof = prepare_typed_html(
        presentation_ir=doc, expected_ir_sha256=expected_ir_sha256)
    _require(type(html_proof) is TypedHtmlValidation
             and html_proof.ir_sha256 == expected_ir_sha256
             and html_proof.html_sha256 == sha256(typed.encode('utf-8')).hexdigest())
    typed_html_sha = html_proof.html_sha256
    html = _render_html(typed)
    html_sha = sha256(html.encode('utf-8')).hexdigest()

    require_no_active_scope()
    if observe_processes:
        rendered, render_process = _render_pdf_candidate(
            html, html_sha, observe_processes=True)
    else:
        rendered = render_pdf_candidate(html, html_sha)
    _require(type(rendered) is PdfRenderedCandidate)
    pdf = object.__getattribute__(rendered, '_pdf_bytes')
    _require(type(pdf) is bytes and 0 < len(pdf) <= MAX_PDF_BYTES
             and pdf.startswith(b'%PDF-1.7'))
    pdf_sha = sha256(pdf).hexdigest()
    render_data = rendered.to_dict()
    _require(render_data['html_sha256'] == html_sha
             and render_data['pdf_sha256'] == pdf_sha
             and render_data['pdf_byte_count'] == len(pdf)
             and render_data['profile_sha256'] == RENDER_PROFILE_SHA256)
    pages = render_data['page_count']
    render_sha = sha256(_canonical(render_data)).hexdigest()
    if observe_processes:
        render_process = _checked_render_process(
            render_process, {**render_data, 'render_result_sha256': render_sha})
        previous_exit = _process_order(render_process, started_ns, process_pids)

    require_no_active_scope()
    if observe_processes:
        observed, typed_process = observe_typed_pdf_with_processes(
            pdf_bytes=pdf, expected_pdf_sha256=pdf_sha)
    else:
        observed = observe_typed_pdf(pdf_bytes=pdf, expected_pdf_sha256=pdf_sha)
    _require(type(observed) is TypedPdfObservation)
    payload = object.__getattribute__(observed, '_payload')
    _require(type(payload) is bytes and 0 < len(payload) <= MAX_OBSERVATION_BYTES)
    # Reconstruct the closed bounded trace, including its reverse structure and
    # decoded text, before using either its counts or declared digests.
    admitted = TypedPdfObservation(payload)
    _require(observed.observation_sha256 == admitted.observation_sha256)
    physical = admitted.summary()
    observation_sha = admitted.observation_sha256
    _require(physical['pdf_sha256'] == pdf_sha
             and physical['pdf_byte_count'] == len(pdf)
             and physical['page_count'] == pages
             and type(pages) is int and 1 <= pages <= MAX_PAGES
             and physical['profile_sha256'] == OBSERVER_PROFILE_SHA256)
    if observe_processes:
        typed_process = _checked_typed_process(typed_process, {
            'pdf_sha256': pdf_sha, 'pdf_byte_count': len(pdf), 'page_count': pages,
            'observation_sha256': observation_sha,
            'observer_profile_sha256': OBSERVER_PROFILE_SHA256})
        previous_exit = _process_order(typed_process, previous_exit, process_pids)

    associated = associate_typed_pdf(
        presentation_ir=doc, expected_ir_sha256=expected_ir_sha256,
        expected_source_expectation_sha256=expected_source_expectation_sha256,
        observation=admitted, expected_observation_sha256=observation_sha,
        expected_pdf_sha256=pdf_sha)
    _require(type(associated) is TypedPdfAssociation)
    association_bytes = object.__getattribute__(associated, '_serialized')
    _require(type(association_bytes) is bytes and 0 < len(association_bytes) <= 4194304)
    _require(sha256(association_bytes).hexdigest() == associated.association_sha256)
    association_sha = sha256(association_bytes).hexdigest()
    association = associated.summary()
    bindings = ('ir_sha256', 'request_sha256', 'source_binding_sha256',
                'plan_sha256', 'ledger_sha256', 'content_sha256', 'section_states_sha256')
    _require(all(association[key] == manifest[key] for key in bindings)
             and association['source_expectation_sha256'] == expected_source_expectation_sha256
             and association['observation_sha256'] == observation_sha
             and association['observer_profile_sha256'] == OBSERVER_PROFILE_SHA256
             and association['pdf_sha256'] == pdf_sha
             and association['pdf_byte_count'] == len(pdf)
             and association['page_count'] == pages
             and association['source_counts'] == manifest['counts'])
    _require({k for k, value in association['claims'].items() if value is True}
             == {'literal_hashes_matched', 'complete_physical_coverage',
                 'unique_mapping_within_profile'}
             and all(type(value) is bool for value in association['claims'].values()))

    plan = plan_pdf_page_windows(
        pdf_sha256=pdf_sha, ir_sha256=expected_ir_sha256, page_count=pages,
        admitted_page_budget=pages, pages_per_window=RASTER_WIDTH,
        inspector_profile_id=RASTER_PROFILE_ID)
    _require(1 <= plan.window_count <= 16)
    plan_sha = plan.plan_sha256
    inspections = []
    for window in plan.windows:
        plan.__post_init__()
        _require(plan.plan_sha256 == plan_sha)
        require_no_active_scope()
        if observe_processes:
            receipt, child_processes, raster_process = inspect_process_observed_pdf_raster_window(
                pdf_bytes=pdf, plan=plan, window_index=window.index,
                expected_plan_sha256=plan_sha)
        else:
            receipt = inspect_pdf_raster_window(
                pdf_bytes=pdf, plan=plan, window_index=window.index,
                expected_plan_sha256=plan_sha)
        _require(type(receipt) is PdfRasterWindowInspection)
        row = receipt.to_dict()
        expected = {
            'pdf_sha256': pdf_sha, 'ir_sha256': expected_ir_sha256,
            'plan_sha256': plan.plan_sha256, 'page_count': pages,
            'window_index': window.index, 'first_page': window.first_page,
            'last_page': window.last_page, 'profile_id': RASTER_PROFILE_ID,
        }
        # The generic ledger omits IR and total page count from each observation.
        # Compare the complete native receipt before that narrower projection.
        _require(all(row[key] == value for key, value in expected.items()))
        inspections.append(_raster_snapshot(row))
        if observe_processes:
            entry = _checked_raster_processes(raster_process, child_processes, row)
            previous_exit = _process_order(entry['direct_process'], previous_exit,
                process_pids, entry['child_observation']['processes'])
            raster_processes.append(entry)
    ledger = bind_pdf_page_window_evidence(
        plan=plan, observations=tuple(item.to_observation() for item in inspections))
    _require(type(ledger) is BoundPageWindowLedger and ledger.plan is plan)
    ledger_data = ledger.to_dict()
    windows = [item.to_dict() for item in inspections]
    inspected_pages = sum(item.inspected_pages for item in inspections)
    _require(inspected_pages == pages and len(windows) == plan.window_count)

    result = {
        'schema_version': '1.0.0', 'profile_id': PROFILE_ID, 'status': STATUS,
        'conformance_scope': 'EXACT_TYPED_TEXT_AND_ALL_PAGE_RASTER_GEOMETRY_ONLY',
        'profile_sha256': profile_sha, **{key: manifest[key] for key in bindings},
        'source_expectation_sha256': expected_source_expectation_sha256,
        'typed_html_sha256': typed_html_sha, 'html_sha256': html_sha,
        'render_result_sha256': render_sha,
        'observer_profile_sha256': OBSERVER_PROFILE_SHA256,
        'observation_sha256': observation_sha,
        'association_sha256': association_sha,
        'pdf_sha256': pdf_sha, 'pdf_byte_count': len(pdf), 'page_count': pages,
        'source_counts': manifest['counts'], 'passive_policy': PASSIVE_POLICY,
        'raster_window_plan_sha256': plan.plan_sha256,
        'raster_ledger_sha256': sha256(_canonical(ledger_data)).hexdigest(),
        'raster_window_count': len(windows), 'raster_inspected_pages': inspected_pages,
        'raster_windows': windows,
        'declared_process_schedule': {
            'renderer': 1, 'typed_observer': 1, 'raster_workers': len(windows),
            'raster_version_probes': len(windows), 'raster_page_executors': pages,
            'total': 2 + 2 * len(windows) + pages, 'execution_authenticated': False,
        },
        'claims': {
            'literal_source_associated': True,
            'effective_typed_graph_policy_checked': True,
            'all_pages_raster_geometry_inspected': True,
            **{key: False for key in _NONCLAIMS},
        },
        'release_authorization': 'NOT_ESTABLISHED',
    }
    serialized = _canonical(result)
    _require(len(serialized) <= MAX_EVIDENCE_BYTES)
    require_no_active_scope()
    _require(_validate_evidence(serialized, pdf)['profile_sha256'] == profile_sha)
    if observe_processes:
        process_observation = _build_process_observation(
            result, sha256(serialized).hexdigest(), render_process, typed_process,
            raster_processes, started_ns, monotonic_ns())
        return pdf, serialized, process_observation
    return pdf, serialized


@dataclass(frozen=True, slots=True, repr=False)
class TypedPdfCandidate:
    """Immutable private candidate; direct construction runs the entire boundary."""

    presentation_ir: InitVar[object]
    expected_ir_sha256: InitVar[object]
    expected_source_expectation_sha256: InitVar[object]
    expected_profile_sha256: InitVar[object]
    _pdf_bytes: bytes = field(init=False, repr=False)
    _payload: bytes = field(init=False, repr=False)
    _sealed_sha256: str = field(init=False, repr=False)

    def __post_init__(self, presentation_ir, expected_ir_sha256,
                      expected_source_expectation_sha256, expected_profile_sha256):
        """Execute admission instead of accepting caller-provided success records."""
        try:
            _require(not hasattr(self, '_payload'))
            pdf, payload = _execute(presentation_ir, expected_ir_sha256,
                                    expected_source_expectation_sha256,
                                    expected_profile_sha256)
            object.__setattr__(self, '_pdf_bytes', pdf)
            object.__setattr__(self, '_payload', payload)
            object.__setattr__(self, '_sealed_sha256', sha256(payload).hexdigest())
        except Exception:
            raise TypedPdfCandidateError('typed_pdf_candidate_refused') from None

    def _checked(self) -> bytes:
        """Check bounded retained bytes and source/profile pins without native replay."""
        try:
            _require(type(self._pdf_bytes) is bytes
                     and 0 < len(self._pdf_bytes) <= MAX_PDF_BYTES
                     and self._pdf_bytes.startswith(b'%PDF-1.7'))
            _require(type(self._payload) is bytes and 0 < len(self._payload) <= MAX_EVIDENCE_BYTES)
            _digest(self._sealed_sha256)
            _require(sha256(self._payload).hexdigest() == self._sealed_sha256)
            _validate_evidence(self._payload, self._pdf_bytes)
            return self._payload
        except Exception:
            raise TypedPdfCandidateError('typed_pdf_candidate_refused') from None

    @property
    def pdf_bytes(self) -> bytes:
        """Return private content only after complete construction and retained checks."""
        self._checked()
        return self._pdf_bytes

    def to_bytes(self) -> bytes:
        """Return bounded content-free canonical metadata."""
        return self._checked()

    def to_dict(self) -> dict:
        """Return detached unsigned metadata, never PDF content or the private trace."""
        return json.loads(self._checked())

    @property
    def candidate_sha256(self) -> str:
        """Identify local evidence; a digest never authenticates native execution."""
        return sha256(self._checked()).hexdigest()

    def __repr__(self) -> str:
        """Keep private content and source identifiers out of implicit formatting."""
        return 'TypedPdfCandidate()'


def prepare_typed_pdf_candidate(
    *, presentation_ir: object, expected_ir_sha256: object,
    expected_source_expectation_sha256: object, expected_profile_sha256: object,
) -> TypedPdfCandidate:
    """Run the fixed complete composition without caller artifacts or PASS flags."""
    return TypedPdfCandidate(presentation_ir, expected_ir_sha256,
                             expected_source_expectation_sha256, expected_profile_sha256)


def prepare_observed_typed_pdf_candidate(
    *, presentation_ir: object, expected_ir_sha256: object,
    expected_source_expectation_sha256: object, expected_profile_sha256: object,
) -> tuple[TypedPdfCandidate, dict]:
    """Return the unchanged candidate and bounded actual fixed-process observations.

    The private composition executes once. It observes the controlled renderer,
    typed inspector and each raster worker with its probe/page children. The
    unsigned sidecar does not authenticate execution, measure aggregate resources,
    observe arbitrary descendants or grant visual, accessibility or release claims.
    No caller process, executor, callback, success record or budget is accepted.
    """
    try:
        pdf, payload, observation = _execute(
            presentation_ir, expected_ir_sha256, expected_source_expectation_sha256,
            expected_profile_sha256, observe_processes=True)
        candidate = object.__new__(TypedPdfCandidate)
        object.__setattr__(candidate, '_pdf_bytes', pdf)
        object.__setattr__(candidate, '_payload', payload)
        object.__setattr__(candidate, '_sealed_sha256', sha256(payload).hexdigest())
        checked = candidate.to_bytes()
        observation = _validate_process_observation(observation, checked, pdf)
        require_no_active_scope()
        _require(candidate.to_bytes() == checked
                 and observation['candidate_sha256'] == sha256(checked).hexdigest())
        return candidate, observation
    except Exception:
        raise TypedPdfCandidateError('typed_pdf_candidate_refused') from None
