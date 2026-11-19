"""Physical PDF observations with synthetic source and real confined parser fixtures."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from hashlib import sha256
from io import BytesIO
import importlib
import importlib.util
import json
import os
import shutil
import tempfile
from pathlib import Path
from time import monotonic_ns
import unittest
from unittest.mock import patch
import zlib

from jsonschema import Draft202012Validator
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject, DictionaryObject, NameObject, NumberObject,
    DecodedStreamObject, TextStringObject,
)
from reporting.typed_html import prepare_typed_html
from reporting.adapters import (
    FIXED_FONT_STYLE_ID, _FIXED_SEMANTIC_CSS,
    GENERATED_PDF_AUTHOR, GENERATED_PDF_PRODUCT,
)
from reporting.pdf_generated_source_job import render_pdf_candidate
from test_reporting_typed_pdf_source import document, digest

ROOT = Path(__file__).resolve().parents[1]
_LARGE_RESPONSE_CASE = None
_LARGE_RESPONSE_ATTEMPTED = False
_LARGE_RESPONSE_INPUT = None
_OPERATION_BUDGET_ATTEMPTED = False
_OPERATION_BUDGET_INPUT = None


def encoded(value):
    """Use the declared canonical JSON encoding independently of the observer."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def native_fixture(rows, wrapped=False):
    """Render inert typed body through the existing confined native renderer."""
    options = {}
    if wrapped:
        options = {
            'paragraph': 'Literal A  B\tC\r\nD\nE. ' + 'wrap ' * 80,
            'cells': ('AB  CD EF GH IJ KL MN OP QR ST UV WX YZ ' * 8,
                      '0.12345678901234567890'),
        }
    return rendered_fixture(document(rows, **options)).pdf_bytes


def rendered_fixture(source):
    """Retain the real renderer receipt for one sealed public synthetic source."""
    typed, _ = prepare_typed_html(presentation_ir=source,
                                 expected_ir_sha256=digest(source))
    body = typed.split('<body>', 1)[1].split('</body>', 1)[0]
    html = ('<!doctype html><html lang="en-US"><head><meta charset="utf-8">'
            f'<title>{GENERATED_PDF_PRODUCT}</title>'
            f'<meta name="author" content="{GENERATED_PDF_AUTHOR}">'
            f'<meta name="generator" content="{GENERATED_PDF_PRODUCT}">'
            f'<meta name="omnigenis-font-style" content="{FIXED_FONT_STYLE_ID}">'
            f'<style>{_FIXED_SEMANTIC_CSS}'
            'h1,h2,p,caption,th,td{white-space:pre-wrap}</style></head><body>'
            + body + '</body></html>')
    return render_pdf_candidate(html, sha256(html.encode()).hexdigest())


def large_response_fixture():
    """Generate the fixed 96-by-499 positive once; retain inputs before observation."""
    global _LARGE_RESPONSE_CASE, _LARGE_RESPONSE_ATTEMPTED, _LARGE_RESPONSE_INPUT
    if _LARGE_RESPONSE_CASE is None:
        if _LARGE_RESPONSE_ATTEMPTED:
            raise RuntimeError('fixed large response fixture already failed')
        _LARGE_RESPONSE_ATTEMPTED = True
        cell = ' '.join(f'C{i:03d}' for i in range(100))
        source = document(96, paragraph='Synthetic dense table response regression.',
                          labels=('Column', 'Column'), cells=(cell, cell))
        source_bytes = encoded(source)
        _LARGE_RESPONSE_INPUT = (source_bytes, None)
        rendered = rendered_fixture(source)
        _LARGE_RESPONSE_INPUT = (source_bytes, rendered)
        observer = importlib.import_module('reporting.pdf_typed_observation')
        observed = observer.observe_typed_pdf(
            pdf_bytes=rendered.pdf_bytes,
            expected_pdf_sha256=sha256(rendered.pdf_bytes).hexdigest())
        _LARGE_RESPONSE_CASE = (source_bytes, rendered, observed)
    source_bytes, rendered, observed = _LARGE_RESPONSE_CASE
    return json.loads(source_bytes), rendered, observed


def operation_budget_fixture():
    """Attempt the unchanged 96-by-599 negative once through the native observer."""
    global _OPERATION_BUDGET_ATTEMPTED, _OPERATION_BUDGET_INPUT
    if _OPERATION_BUDGET_ATTEMPTED:
        raise RuntimeError('fixed operation-budget fixture already attempted')
    _OPERATION_BUDGET_ATTEMPTED = True
    cell = ' '.join(f'C{i:03d}' for i in range(120))
    source = document(96, paragraph='Synthetic dense table response regression.',
                      labels=('Column', 'Column'), cells=(cell, cell))
    source_bytes = encoded(source)
    _OPERATION_BUDGET_INPUT = (source_bytes, None)
    rendered = rendered_fixture(source)
    _OPERATION_BUDGET_INPUT = (source_bytes, rendered)
    observer = importlib.import_module('reporting.pdf_typed_observation')
    return observer.observe_typed_pdf(
        pdf_bytes=rendered.pdf_bytes,
        expected_pdf_sha256=sha256(rendered.pdf_bytes).hexdigest())


def changed(pdf, change):
    """Alter only a synthetic PDF while preserving a valid rewritten control."""
    writer = PdfWriter(clone_from=BytesIO(pdf))
    writer.pdf_header = b'%PDF-1.7'
    change(writer)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def change_stream(writer, transform):
    """Replace one actual page stream in a controlled synthetic candidate."""
    page = writer.pages[0]
    raw = page.get_contents().get_data()
    stream = DecodedStreamObject()
    stream.set_data(transform(raw))
    page[NameObject('/Contents')] = writer._add_object(stream)


def native_document(writer):
    """Locate the real root document instead of assuming an object number."""
    root = writer.root_object['/StructTreeRoot'].get_object()
    return root, root['/K'][0].get_object()


class TypedPdfObservationTests(unittest.TestCase):
    """Encoded text observations never imply source, visual or release approval."""

    fixtures = None
    observations = None

    def setUp(self):
        self.assertIsNotNone(
            importlib.util.find_spec('reporting.pdf_typed_observation'),
            'bounded physical typed PDF observer is missing')
        self.m = importlib.import_module('reporting.pdf_typed_observation')
        self.w = importlib.import_module('reporting._pdf_typed_observation_worker')
        if self.__class__.fixtures is None:
            self.__class__.fixtures = {
                'empty': native_fixture(0),
                'wrapped': native_fixture(1, True),
                'continuation': native_fixture(96),
            }
            self.__class__.observations = {}

    def observe(self, pdf=None, **changes):
        pdf = self.fixtures['wrapped'] if pdf is None else pdf
        return self.m.observe_typed_pdf(
            **{'pdf_bytes': pdf, 'expected_pdf_sha256': sha256(pdf).hexdigest(),
               **changes})

    def data(self, name='wrapped'):
        if name not in self.observations:
            self.observations[name] = self.observe(self.fixtures[name])
        return self.observations[name].to_dict()

    def frame(self, data=None):
        return encoded({'protocol_version': 1, 'observation': data or self.data()})

    def decode(self, frame):
        pdf = self.fixtures['wrapped']
        return self.m._decode(frame, sha256(pdf).hexdigest(), len(pdf))

    def reject_change(self, change):
        pdf = changed(self.fixtures['wrapped'], change)
        with self.assertRaises(self.m.TypedPdfObservationError):
            self.observe(pdf)

    def test_large_native_response_preserves_closed_trace_and_replay(self):
        source, rendered, observed = large_response_fixture()
        payload = observed.to_bytes()
        self.assertEqual(source['counts']['table_rows'], 96)
        self.assertGreater(len(payload), 4 * 1024 * 1024)
        self.assertLessEqual(len(payload), 11 * 1024 * 1024)
        data = observed.to_dict()
        self.assertLessEqual(data['counts']['operations'], 20000)
        self.assertEqual(encoded(data), payload)
        self.assertEqual(data['pdf_sha256'], sha256(rendered.pdf_bytes).hexdigest())
        self.assertEqual(data['page_count'], rendered.page_count)
        self.assertEqual(data['limits'], {
            'memory_bytes': 268435456, 'cpu_seconds': 5,
            'file_bytes': 11 * 1024 * 1024, 'open_files': 128})
        self.assertLessEqual(
            len(encoded({'protocol_version': 1, 'observation': data})),
            11 * 1024 * 1024)

        # A larger admitted trace still has to match replayed glyph text.
        data['trace']['pages'][0]['runs'][0]['text'] += 'invented'
        with self.assertRaises(self.m.TypedPdfObservationError):
            self.m._decode(
                encoded({'protocol_version': 1, 'observation': data}),
                sha256(rendered.pdf_bytes).hexdigest(), len(rendered.pdf_bytes))

    def test_operation_budget_refuses_original_dense_fixture_before_decode(self):
        with patch.object(self.m, '_run_worker', wraps=self.m._run_worker) as worker, \
                patch.object(self.m, '_decode', wraps=self.m._decode) as decode:
            with self.assertRaisesRegex(self.m.TypedPdfObservationError,
                                        '^typed_pdf_observation_execution_failed$'):
                operation_budget_fixture()
        worker.assert_called_once()
        decode.assert_not_called()

    def test_exact_response_byte_ceiling_and_preparse_oversize_refusal(self):
        maximum = 11 * 1024 * 1024
        self.assertEqual(self.m.RESULT_BYTES, maximum)
        frame = self.frame()
        self.assertLess(len(frame), maximum)
        exact = frame + b' ' * (maximum - len(frame))
        self.assertEqual(self.decode(exact).to_dict(), self.data())
        oversized = exact + b' '

        # Profile JSON is admitted separately; only response parsing is spied on.
        profile_sha = self.m._profile()
        with patch.object(self.m, '_profile', return_value=profile_sha), \
                patch.object(self.m, '_loads') as parse:
            with self.assertRaises(self.m.TypedPdfObservationError):
                self.decode(oversized)
            parse.assert_not_called()
        with patch.object(self.m, '_loads') as parse:
            with self.assertRaises(self.m.TypedPdfObservationError):
                self.m.TypedPdfObservation(oversized)
            parse.assert_not_called()

    def test_empty_table_retains_an_explicit_empty_body(self):
        data = self.data('empty')
        self.assertEqual(data['page_count'], 1)
        nodes = data['trace']['nodes']
        empty = [n for n in nodes if not n['children']]
        self.assertEqual([(n['role'], n['children_form']) for n in empty],
                         [('/TBody', 'array')])
        self.assertEqual(data['counts']['structure_nodes'], 19)
        self.assertEqual(data['counts']['mcids'], 8)

    def test_continuation_preserves_three_physical_tables_and_page_local_headers(self):
        data = self.data('continuation')
        self.assertEqual(data['page_count'], 3)
        self.assertEqual(data['counts']['structure_nodes'], 513)
        self.assertEqual(data['counts']['mcids'], 202)
        nodes = data['trace']['nodes']
        pages = data['trace']['pages']
        self.assertEqual([len(p['mcid_owners']) for p in pages], [72, 82, 48])
        self.assertEqual([sum(n['role'] == '/TD' and
            n['page_reference'] == p['reference'] for n in nodes) for p in pages],
            [66, 80, 46])
        headers = [n for n in nodes if n['role'] == '/TH']
        self.assertEqual(len({n['native_id'] for n in headers}), 6)
        for page in pages:
            ids = {n['native_id'] for n in headers
                   if n['page_reference'] == page['reference']}
            for cell in (n for n in nodes if n['role'] == '/TD'
                         and n['page_reference'] == page['reference']):
                self.assertEqual(len(cell['headers']), 1)
                self.assertIn(cell['headers'][0], ids)

    def test_exact_child_order_and_reverse_mcid_ownership_are_exported(self):
        data = self.data()
        nodes = {tuple(n['reference']): n for n in data['trace']['nodes']}
        doc = next(n for n in nodes.values() if n['role'] == '/Document')
        self.assertEqual([nodes[tuple(c['reference'])]['role']
            for c in doc['children']], ['/H1', '/H2', '/P', '/Table'])
        for page in data['trace']['pages']:
            owners = {m['mcid']: m['structure_reference']
                      for m in page['mcid_owners']}
            self.assertEqual(set(owners), set(range(len(owners))))
            for run in page['runs']:
                self.assertEqual(run['structure_reference'], owners[run['mcid']])
                leaf = nodes[tuple(run['structure_reference'])]
                self.assertEqual(leaf['page_reference'], page['reference'])
                self.assertEqual(leaf['children'],
                    [{'ordinal': 0, 'kind': 'mcid', 'value': run['mcid']}])

    def test_literal_spaces_ligatures_and_positioning_are_not_normalized(self):
        data = self.data()
        runs = [r for p in data['trace']['pages'] for r in p['runs']]
        text = [r['text'] for r in runs]
        self.assertIn('Literal A  BC', text)
        self.assertIn('0.12345678901234567890', text)
        self.assertTrue(any('AB  CD' in s for s in text))
        self.assertFalse(any(c in ''.join(text) for c in '\t\r\n'))
        self.assertTrue(any(part == ['codes', '']
                            for r in runs for part in r['parts']))
        self.assertTrue(any(part == ['adjust', '-1379.3754534875761']
                            for r in runs for part in r['parts']))
        mappings = [value for font in data['trace']['fonts']
                    for _, value in font['mapping']]
        self.assertIn('fi', mappings)
        self.assertIn('ti', mappings)

    def test_each_fragment_retains_exact_font_codes_and_operator_location(self):
        data = self.data()
        fonts = {tuple(f['reference']): dict(f['mapping'])
                 for f in data['trace']['fonts']}
        for page in data['trace']['pages']:
            events = {e['ordinal']: e for e in page['events']}
            for run in page['runs']:
                self.assertEqual(events[run['operator_ordinal']]['operator'], 'TJ')
                cmap = fonts[tuple(run['font_reference'])]
                literal = ''.join(cmap[piece[i:i + 4]]
                    for kind, piece in run['parts'] if kind == 'codes'
                    for i in range(0, len(piece), 4))
                self.assertEqual(run['text'], literal)
                self.assertEqual(run['utf8_bytes'], len(literal.encode('utf-8')))
                self.assertEqual(run['scalars'], len(literal))

    def test_summary_is_content_free_and_all_unestablished_claims_remain_false(self):
        result = self.observations.get('wrapped') or self.observe()
        summary = result.summary()
        self.assertEqual(summary['status'], 'PHYSICAL_TEXT_OBSERVED')
        self.assertEqual(summary['source_association'], 'NOT_ESTABLISHED')
        self.assertNotIn('trace', summary)
        self.assertNotIn('Literal A', json.dumps(summary) + repr(result))
        self.assertFalse(hasattr(result, 'passed'))
        for name in ['source_text_equivalence_verified', 'rendered_glyphs_verified',
                     'reading_order_verified', 'accessibility_qualified',
                     'scientific_equivalence_verified', 'aggregate_job_resources_qualified',
                     'worker_results_authenticated', 'release_authorized']:
            self.assertIs(result.to_dict()[name], False)

    def test_result_is_immutable_and_returns_detached_trace_copies(self):
        result = self.observe()
        original = result.to_bytes()
        data = result.to_dict()
        data['trace']['pages'][0]['runs'][0]['text'] = 'replacement'
        self.assertEqual(result.to_bytes(), original)
        self.assertEqual(result.observation_sha256, sha256(original).hexdigest())
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            result.observation_sha256 = '0' * 64

    def test_rewritten_supported_pdf_is_observed_as_an_independent_control(self):
        rewritten = changed(self.fixtures['wrapped'], lambda _writer: None)
        data = self.observe(rewritten).to_dict()
        self.assertEqual(data['pdf_sha256'], sha256(rewritten).hexdigest())
        self.assertEqual(data['counts']['mcids'], 18)
        self.assertIn('Literal A  BC',
                      [r['text'] for p in data['trace']['pages'] for r in p['runs']])

    def test_changed_native_child_order_is_preserved_without_claiming_source_order(self):
        def reorder(writer):
            _, doc = native_document(writer)
            children = doc['/K']
            children[0], children[1] = children[1], children[0]
        data = self.observe(changed(self.fixtures['wrapped'], reorder)).to_dict()
        nodes = {tuple(n['reference']): n for n in data['trace']['nodes']}
        doc = next(n for n in nodes.values() if n['role'] == '/Document')
        self.assertEqual([nodes[tuple(c['reference'])]['role']
                         for c in doc['children']][:2], ['/H2', '/H1'])
        self.assertIs(data['reading_order_verified'], False)

    def test_input_domain_digest_and_oversize_refuse_before_launch(self):
        pdf = self.fixtures['wrapped']
        for changes in [
            {'pdf_bytes': bytearray(pdf)}, {'pdf_bytes': b'not a PDF'},
            {'expected_pdf_sha256': 'f' * 64},
            {'expected_pdf_sha256': True},
            {'expected_pdf_sha256': sha256(pdf).hexdigest().upper()},
            {'pdf_bytes': b'%PDF-1.7\n' + b'x' * (16 * 1024 * 1024)},
        ]:
            with self.subTest(changes=list(changes)), patch.object(self.m, '_run_worker') as launch:
                with self.assertRaises(self.m.TypedPdfObservationError):
                    self.observe(**changes)
                launch.assert_not_called()

    def test_disabled_observer_refuses_before_launch(self):
        with patch.dict(os.environ, {'OMNIGENIS_TYPED_PDF_OBSERVATION_DISABLED': '1'}):
            with patch.object(self.m, '_run_worker') as launch:
                with self.assertRaises(self.m.TypedPdfObservationError):
                    self.observe()
                launch.assert_not_called()

    def test_disable_changed_during_execution_withholds_result(self):
        response = self.frame()
        def altered(*_args):
            os.environ['OMNIGENIS_TYPED_PDF_OBSERVATION_DISABLED'] = '1'
            return response
        with patch.dict(os.environ, {}, clear=False), patch.object(
                self.m, '_run_worker', side_effect=altered):
            with self.assertRaises(self.m.TypedPdfObservationError):
                self.observe()

    def test_active_qualified_scope_refusal_prevents_native_launch(self):
        with patch.object(self.m, 'require_no_active_scope',
                          side_effect=ValueError('active scope')):
            with patch.object(self.m, '_run_worker') as launch:
                with self.assertRaises(self.m.TypedPdfObservationError):
                    self.observe()
                launch.assert_not_called()

    def test_worker_failure_is_content_free_and_never_returns_partial_evidence(self):
        with patch.object(self.m, '_run_worker',
                          side_effect=RuntimeError('SECRET_SYNTHETIC_CONTENT')):
            with self.assertRaises(self.m.TypedPdfObservationError) as error:
                self.observe()
        self.assertNotIn('SECRET_SYNTHETIC_CONTENT', str(error.exception))
        self.assertEqual(str(error.exception), 'typed_pdf_observation_execution_failed')

    def test_protocol_rejects_duplicate_keys_extra_fields_and_bad_versions(self):
        good = self.frame()
        samples = [good.replace(b'"protocol_version":1',
                               b'"protocol_version":1,"protocol_version":1'),
                   encoded({'protocol_version': 2, 'observation': self.data()}),
                   encoded({'protocol_version': True, 'observation': self.data()}),
                   encoded({'protocol_version': 1, 'observation': self.data(), 'extra': 0}),
                   b'{}', good[:-1], b' ' * (11 * 1024 * 1024 + 1)]
        for sample in samples:
            with self.subTest(size=len(sample)):
                with self.assertRaises(self.m.TypedPdfObservationError):
                    self.decode(sample)

    def test_protocol_rejects_foreign_identity_counts_and_acceptance_claims(self):
        for key, value in [('pdf_sha256', 'f' * 64), ('pdf_byte_count', 1),
                           ('page_count', 65), ('page_count', True),
                           ('parser_version', 'future'), ('source_association', 'PASS'),
                           ('release_authorized', True), ('profile_sha256', 'f' * 64)]:
            data = self.data()
            data[key] = value
            with self.subTest(key=key), self.assertRaises(self.m.TypedPdfObservationError):
                self.decode(self.frame(data))
        data = self.data()
        data['counts']['mcids'] += 1
        with self.assertRaises(self.m.TypedPdfObservationError):
            self.decode(self.frame(data))

    def test_protocol_rechecks_code_to_text_mapping_and_native_owners(self):
        for change in [
            lambda d: d['trace']['pages'][0]['runs'][0].update(text='invented'),
            lambda d: d['trace']['pages'][0]['runs'][0].update(mcid=99999),
            lambda d: d['trace']['pages'][0]['runs'][0].update(font_reference=[99999, 0]),
            lambda d: d['trace']['nodes'][1].update(parent=[99999, 0]),
        ]:
            data = self.data()
            change(data)
            with self.assertRaises(self.m.TypedPdfObservationError):
                self.decode(self.frame(data))

    def test_unknown_operator_and_form_invocation_refuse_whole_candidate(self):
        for token in [b'xyz\n', b'/Foreign Do\n', b'BT (outside) Tj ET\n']:
            with self.subTest(token=token):
                self.reject_change(lambda w: change_stream(w, lambda raw: raw + token))

    def test_unowned_duplicate_and_unbalanced_mcid_sequences_refuse(self):
        transforms = [
            lambda b: b.replace(b'/Span\n<</MCID 0>>\nBDC', b'', 1),
            lambda b: b.replace(b'/MCID 1', b'/MCID 0', 1),
            lambda b: b.replace(b'EMC', b'', 1),
            lambda b: b.replace(b'BT', b'BT BT', 1),
            lambda b: b.replace(b'ET', b'EMC ET', 1),
        ]
        for transform in transforms:
            with self.subTest(transform=transforms.index(transform)):
                original = PdfReader(BytesIO(self.fixtures['wrapped'])).pages[0].get_contents().get_data()
                altered = transform(original)
                self.assertNotEqual(altered, original)
                self.reject_change(lambda w: change_stream(w, transform))

    def test_text_substitution_and_unknown_structure_roles_refuse(self):
        def substitution(writer):
            _, doc = native_document(writer)
            doc[NameObject('/ActualText')] = TextStringObject('replacement')
        def role(writer):
            _, doc = native_document(writer)
            doc['/K'][0].get_object()[NameObject('/S')] = NameObject('/Unknown')
        for change in [substitution, role]:
            with self.subTest(change=change.__name__):
                self.reject_change(change)

    def test_wrong_reverse_owner_and_graph_cycles_refuse(self):
        def reverse(writer):
            root, _ = native_document(writer)
            values = root['/ParentTree']['/Nums'][1]
            values[0] = values[1]
        def cycle(writer):
            root, doc = native_document(writer)
            doc['/K'].append(root['/K'][0])
        for change in [reverse, cycle]:
            with self.subTest(change=change.__name__):
                self.reject_change(change)

    def test_odd_or_unmapped_character_codes_refuse(self):
        import re
        for replacement in [b'<ff>', b'<fff>', b'<ffff>']:
            def transform(raw, replacement=replacement):
                return re.sub(rb'<[0-9a-fA-F]+>', replacement, raw, count=1)
            self.reject_change(lambda w: change_stream(w, transform))

    def test_wrong_active_font_and_graphics_font_substitution_refuse(self):
        def font(writer):
            change_stream(writer, lambda b: b.replace(b'/XAGEFI ', b'/Missing ', 1))
        def state(writer):
            states = writer.pages[0]['/Resources']['/ExtGState']
            item = next(iter(states.values())).get_object()
            item[NameObject('/Font')] = ArrayObject([NumberObject(1), NumberObject(12)])
        for change in [font, state]:
            with self.subTest(change=change.__name__):
                self.reject_change(change)

    def test_duplicate_cmap_source_and_invalid_utf16_target_refuse(self):
        import re
        for duplicate in [True, False]:
            def mutate(writer, duplicate=duplicate):
                fonts = writer.pages[0]['/Resources']['/Font']
                font = next(iter(fonts.values())).get_object()
                stream = font['/ToUnicode'].get_object()
                raw = zlib.decompress(stream._data)
                rows = list(re.finditer(rb'(?m)^<([0-9a-fA-F]{4})> <([0-9a-fA-F]+)>$', raw))
                self.assertGreaterEqual(len(rows), 2)
                first, second = rows[:2]
                replacement = (b'<' + first.group(1) + b'> <' + second.group(2) + b'>'
                               if duplicate else b'<' + second.group(1) + b'> <d800>')
                raw = raw[:second.start()] + replacement + raw[second.end():]
                stream._data = zlib.compress(raw)
                stream.decoded_self = None
            with self.subTest(duplicate=duplicate):
                self.reject_change(mutate)


    def test_table_attribute_exception_does_not_admit_actions_elsewhere(self):
        def catalog(writer):
            writer.root_object[NameObject('/A')] = DictionaryObject({
                NameObject('/O'): NameObject('/Table')})
        def fake(writer):
            node = DictionaryObject({
                NameObject('/Type'): NameObject('/StructElem'),
                NameObject('/S'): NameObject('/TH'),
                NameObject('/A'): DictionaryObject({NameObject('/O'): NameObject('/Table')})})
            writer.root_object[NameObject('/Foreign')] = writer._add_object(node)
        def nested(writer):
            _, doc = native_document(writer)
            table = next(x.get_object() for x in doc['/K']
                         if x.get_object().get('/S') == '/Table')
            head = next(x.get_object() for x in table['/K']
                        if x.get_object().get('/S') == '/THead')
            cell = head['/K'][0].get_object()['/K'][0].get_object()
            cell['/A'][NameObject('/JS')] = TextStringObject('inert forbidden action')
        for change in [catalog, fake, nested]:
            with self.subTest(change=change.__name__):
                self.reject_change(change)

    def test_raw_page_tree_does_not_silently_drop_invalid_children(self):
        from pypdf.generic import NullObject
        def null_child(writer):
            writer.root_object['/Pages']['/Kids'].append(NullObject())
        def count(writer):
            writer.root_object['/Pages'][NameObject('/Count')] = NumberObject(999)
        for change in [null_child, count]:
            with self.subTest(change=change.__name__):
                self.reject_change(change)

    def test_inherited_resources_keep_the_same_physical_font_binding(self):
        def inherit(writer):
            page = writer.pages[0]
            writer.root_object['/Pages'][NameObject('/Resources')] = page.raw_get('/Resources')
            del page['/Resources']
        data = self.observe(changed(self.fixtures['wrapped'], inherit)).to_dict()
        self.assertEqual(data['counts']['mcids'], 18)
        self.assertIn('Literal A  BC',
                      [r['text'] for p in data['trace']['pages'] for r in p['runs']])

    def test_duplicate_color_setup_inside_a_fragment_refuses(self):
        def duplicate(raw):
            self.assertIn(b'/Span\n<</MCID 0>>\nBDC', raw)
            return raw.replace(b'/Span\n<</MCID 0>>\nBDC',
                b'/Span\n<</MCID 0>>\nBDC\n0 0 0 rg\n0 0 0 rg', 1)
        self.reject_change(lambda w: change_stream(w, duplicate))

    def test_high_precision_positioning_retains_the_parsed_number(self):
        import re
        expected = '120.12345678901234'
        def transform(raw):
            altered, count = re.subn(
                rb'(?<=>)[+-]?[0-9]+(?:\.[0-9]+)?(?=<)',
                expected.encode('ascii'), raw, count=1)
            self.assertEqual(count, 1)
            self.assertNotEqual(altered, raw)
            return altered
        pdf = changed(self.fixtures['wrapped'], lambda w: change_stream(w, transform))
        data = self.observe(pdf).to_dict()
        parts = [part for p in data['trace']['pages'] for r in p['runs'] for part in r['parts']]
        self.assertIn(['adjust', expected], parts)

    def test_fractional_color_above_one_refuses_before_rounding(self):
        import re
        def transform(raw):
            altered, count = re.subn(
                rb'(?m)^[0-9.]+ [0-9.]+ [0-9.]+ rg$',
                b'1.000000001 0 0 rg', raw, count=1)
            self.assertEqual(count, 1)
            self.assertNotEqual(altered, raw)
            return altered
        self.reject_change(lambda w: change_stream(w, transform))

    def test_invalid_nested_entry_poisoning_precedes_input_admission(self):
        import test_reporting_pdf_native_launch_budget as helpers
        helper = helpers.PdfNativeLaunchBudgetTests()
        helper.setUp()
        plan = helper.plan(1)
        for mode in ['digest', 'bytes', 'disabled']:
            crossed = []
            def child(*args, **kwargs):
                crossed.append(True)
                return object()
            changes = ({'expected_pdf_sha256': 'f' * 64} if mode == 'digest'
                       else {'pdf_bytes': b'invalid'} if mode == 'bytes' else {})
            environment = {'OMNIGENIS_TYPED_PDF_OBSERVATION_DISABLED': '1'} if mode == 'disabled' else {}
            with self.subTest(mode=mode), patch.dict(os.environ, environment):
                with helper.scope(plan):
                    with self.assertRaises(self.m.TypedPdfObservationError):
                        self.observe(**changes)
                    with self.assertRaises(ValueError):
                        helpers.launch_one(helpers.launch_specs(plan)[0], helper.pdf, child=child)
                    self.assertEqual(crossed, [])

    def test_protocol_patterns_reject_trailing_newlines_in_physical_fields(self):
        for mode in ['content_digest', 'cmap_digest', 'base_font', 'unused_code']:
            data = self.data()
            if mode == 'content_digest':
                data['trace']['pages'][0]['content_sha256'] += '\n'
            elif mode == 'cmap_digest':
                data['trace']['fonts'][0]['cmap_sha256'] += '\n'
            elif mode == 'base_font':
                data['trace']['fonts'][0]['base_font'] += '\n'
            else:
                font = data['trace']['fonts'][0]
                self.assertNotIn('ffff', dict(font['mapping']))
                font['mapping'].append(['ffff\n', 'X'])
                font['explicit_cid_width_count'] = max(
                    font['explicit_cid_width_count'], len(font['mapping']))
                data['counts']['cmap_records'] += 1
            with self.subTest(mode=mode), self.assertRaises(self.m.TypedPdfObservationError):
                self.decode(self.frame(data))

    def test_protocol_rejects_cross_category_and_inconsistent_shared_references(self):
        for mode in ['cid_root', 'cmap_page', 'content_page', 'shared_cmap', 'shared_cid']:
            data = self.data()
            trace = data['trace']
            self.assertGreaterEqual(len(trace['fonts']), 2)
            if mode == 'cid_root':
                trace['fonts'][0]['cid_reference'] = trace['root_reference']
            elif mode == 'cmap_page':
                trace['fonts'][0]['cmap_reference'] = trace['pages'][0]['reference']
            elif mode == 'content_page':
                trace['pages'][0]['contents_reference'] = trace['pages'][0]['reference']
            elif mode == 'shared_cmap':
                trace['fonts'][1]['cmap_reference'] = trace['fonts'][0]['cmap_reference']
            else:
                trace['fonts'][1]['cid_reference'] = trace['fonts'][0]['cid_reference']
            with self.subTest(mode=mode), self.assertRaises(self.m.TypedPdfObservationError):
                self.decode(self.frame(data))

    def test_protocol_refuses_width_counts_beyond_the_native_font_limit(self):
        data = self.data()
        data['trace']['fonts'][0]['explicit_cid_width_count'] = 4097
        with self.assertRaises(self.m.TypedPdfObservationError):
            self.decode(self.frame(data))

    def test_closed_schema_accepts_real_observation_and_rejects_extras(self):
        schema = json.loads((ROOT / 'schemas/report-typed-pdf-observation.v1.schema.json').read_text())
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        for name in self.fixtures:
            self.assertEqual(list(validator.iter_errors(self.data(name))), [])
        data = self.data()
        data['trace']['pages'][0]['runs'][0]['invented'] = True
        self.assertTrue(list(validator.iter_errors(data)))

    def test_profile_and_actual_worker_limits_are_bound_without_legacy_activation(self):
        data = self.data()
        profile = json.loads((ROOT / 'reporting/pdf-typed-observation.v1.json').read_text())
        self.assertEqual(data['profile_sha256'], sha256(encoded(profile)).hexdigest())
        self.assertEqual(data['limits'], {
            'memory_bytes': 268435456, 'cpu_seconds': 5,
            'file_bytes': 11 * 1024 * 1024, 'open_files': 128})
        self.assertFalse(profile['default_pipeline_activated'])
        self.assertFalse(profile['aggregate_job_resources_qualified'])
        for name, expected in profile['source_sha256'].items():
            self.assertEqual(sha256((ROOT / name).read_bytes()).hexdigest(), expected)

    @contextmanager
    def _profile_case_tree(self):
        """Copy only the current fixed observer closure into a private test tree."""
        profile_name = 'reporting/pdf-typed-observation.v1.json'
        raw = (ROOT / profile_name).read_bytes()
        profile = json.loads(raw)
        self.assertEqual(len(profile['source_sha256']), 109)
        with tempfile.TemporaryDirectory(prefix='typed-observer-profile-') as name:
            root = Path(name)
            for relative, expected in profile['source_sha256'].items():
                content = (ROOT / relative).read_bytes()
                self.assertEqual(sha256(content).hexdigest(), expected)
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
            manifest = root / profile_name
            manifest.write_bytes(raw)
            with patch.object(self.m, 'ROOT', root), \
                    patch.object(self.m, '_PROFILE_FILE', manifest), \
                    patch.dict(os.environ, {'OMNIGENIS_TYPED_PDF_OBSERVATION_DISABLED': '0'}):
                yield root, manifest, profile

    @contextmanager
    def _profile_case_io(self, protected, *, before_open=None, after_open=None,
                         before_read=None, after_read=None):
        """Observe real descriptor I/O; refuse a protected pathname reopen safely."""
        protected = {Path(path) for path in protected}
        original_open, original_read, original_close = os.open, os.read, os.close
        original_path_open = Path.open
        records, active = [], {}

        def pathname_open(path, *args, **kwargs):
            if Path(path) in protected:
                self.fail('profile admission reopened a protected path via Path.open')
            return original_path_open(path, *args, **kwargs)

        def descriptor_open(path, flags, *args, **kwargs):
            named = Path(os.fsdecode(path)) if isinstance(path, (str, bytes, os.PathLike)) else None
            if named in protected:
                required = os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
                self.assertEqual(flags & required, required)
                self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)
                if before_open is not None:
                    before_open(named)
            fd = original_open(path, flags, *args, **kwargs)
            if named in protected:
                record = {'path': named, 'fd': fd, 'requests': [], 'bytes': 0,
                          'closed': False}
                records.append(record)
                active[fd] = record
                if after_open is not None:
                    after_open(named)
            return fd

        def descriptor_read(fd, count):
            record = active.get(fd)
            if record is not None:
                limit = (65536 if record['path'].name == 'pdf-typed-observation.v1.json'
                         else 262144)
                self.assertGreater(count, 0)
                self.assertLessEqual(count, 8192)
                self.assertLessEqual(count, limit - record['bytes'])
                record['requests'].append(count)
                replacement = (before_read(record['path'])
                               if before_read is not None else None)
                content = original_read(fd, count) if replacement is None else replacement
                record['bytes'] += len(content)
                self.assertLessEqual(record['bytes'], limit)
                if after_read is not None:
                    after_read(record['path'])
                return content
            return original_read(fd, count)

        def descriptor_close(fd):
            result = original_close(fd)
            record = active.pop(fd, None)
            if record is not None:
                record['closed'] = True
            return result

        try:
            with patch.object(Path, 'open', pathname_open), \
                    patch.object(os, 'open', descriptor_open), \
                    patch.object(os, 'read', descriptor_read), \
                    patch.object(os, 'close', descriptor_close):
                yield records
        finally:
            leaked = [record['path'] for record in active.values()]
            for fd in tuple(active):
                try:
                    original_close(fd)
                except OSError:
                    pass
            self.assertEqual(leaked, [], 'profile admission leaked a descriptor')

    def test_profile_manifest_uses_one_bounded_descriptor(self):
        for size in ('actual', 'existing-cap'):
            with self.subTest(size=size), self._profile_case_tree() as (_, manifest, profile):
                raw = manifest.read_bytes()
                if size == 'existing-cap':
                    raw += b' ' * (65536 - len(raw))
                    manifest.write_bytes(raw)
                with self._profile_case_io([manifest]) as records:
                    actual = self.m._profile()
                self.assertEqual(actual, sha256(encoded(profile)).hexdigest())
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]['bytes'], len(raw))
                self.assertTrue(records[0]['closed'])

    def test_profile_source_reads_preserve_raw_hash_and_empty_file_semantics(self):
        for case in ('actual', 'empty-source', 'defensive-source-cap'):
            with self.subTest(case=case), self._profile_case_tree() as (root, manifest, profile):
                relative = sorted(profile['source_sha256'])[0]
                target = root / relative
                if case != 'actual':
                    content = b'' if case == 'empty-source' else b'x' * 262144
                    target.write_bytes(content)
                    profile['source_sha256'][relative] = sha256(content).hexdigest()
                    manifest.write_bytes(encoded(profile) + b'\n')
                expected_bytes = target.stat().st_size
                sources = [root / path for path in profile['source_sha256']]
                # Only this known regular, finite fixture manifest may use the
                # old read path, so its defect cannot hide the separate source site.
                with self._profile_case_io(sources) as records:
                    actual = self.m._profile()
                self.assertEqual(actual, sha256(encoded(profile)).hexdigest())
                self.assertEqual({row['path'] for row in records}, set(sources))
                self.assertEqual(len(records), 109)
                chosen = [row for row in records if row['path'] == target]
                self.assertEqual(len(chosen), 1)
                self.assertEqual(chosen[0]['bytes'], expected_bytes)
                if case == 'empty-source':
                    self.assertEqual(chosen[0]['requests'], [])

    def test_profile_files_refuse_nonregular_and_oversized_before_read(self):
        for site in ('manifest', 'source'):
            for case in ('fifo-no-writer', 'directory', 'symlink', 'oversized'):
                with self.subTest(site=site, case=case), \
                        self._profile_case_tree() as (root, manifest, profile):
                    sources = [root / path for path in sorted(profile['source_sha256'])]
                    target = manifest if site == 'manifest' else sources[0]
                    original = target.read_bytes()
                    target.unlink()
                    if case == 'fifo-no-writer':
                        os.mkfifo(target)
                    elif case == 'directory':
                        target.mkdir()
                    elif case == 'symlink':
                        backing = target.with_name(target.name + '.backing')
                        backing.write_bytes(original)
                        target.symlink_to(backing)
                    else:
                        limit = 65536 if site == 'manifest' else 262144
                        target.write_bytes(b'x' * (limit + 1))
                    protected = [manifest] if site == 'manifest' else sources
                    with self._profile_case_io(protected) as records:
                        with self.assertRaises((ValueError, OSError)):
                            self.m._profile()
                    target_records = [row for row in records if row['path'] == target]
                    self.assertTrue(all(row['bytes'] == 0 and not row['requests']
                                        for row in target_records))

    def test_profile_descriptor_drift_and_io_failure_close_without_reopening(self):
        for site in ('manifest', 'source'):
            for case in ('replace-before-open', 'replace-after-open', 'replace-after-read',
                         'same-size-write-after-read', 'grow-after-read', 'truncate-after-read',
                         'read-error', 'short-read'):
                with self.subTest(site=site, case=case), \
                        self._profile_case_tree() as (root, manifest, profile):
                    sources = [root / path for path in sorted(profile['source_sha256'])]
                    target = manifest if site == 'manifest' else sources[0]
                    fired = []

                    def before_open(path):
                        if path == target and case == 'replace-before-open' and not fired:
                            target.rename(target.with_name(target.name + '.original'))
                            os.mkfifo(target)
                            fired.append(case)

                    def before_read(path):
                        if path == target and case in ('read-error', 'short-read') and not fired:
                            fired.append(case)
                            if case == 'read-error':
                                raise OSError('injected bounded profile read failure')
                            return b''
                        return None

                    def after_open(path):
                        if path == target and case == 'replace-after-open' and not fired:
                            target.rename(target.with_name(target.name + '.original'))
                            os.mkfifo(target)
                            fired.append(case)

                    def after_read(path):
                        if path == target and case in ('replace-after-read', 'same-size-write-after-read',
                                                       'grow-after-read', 'truncate-after-read') \
                                and not fired:
                            fired.append(case)
                            if case == 'replace-after-read':
                                target.rename(target.with_name(target.name + '.original'))
                                os.mkfifo(target)
                            elif case == 'same-size-write-after-read':
                                before = target.stat()
                                with open(target, 'r+b') as mutation:
                                    first = mutation.read(1)
                                    self.assertEqual(len(first), 1)
                                    mutation.seek(0)
                                    mutation.write(bytes([first[0] ^ 1]))
                                os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns + 1000000000))
                                self.assertEqual(target.stat().st_size, before.st_size)
                            else:
                                length = target.stat().st_size + 1 if case == 'grow-after-read' else 0
                                os.truncate(target, length)

                    protected = [manifest] if site == 'manifest' else sources
                    with self._profile_case_io(protected, before_open=before_open,
                                               after_open=after_open,
                                               before_read=before_read,
                                               after_read=after_read) as records:
                        with self.assertRaises((ValueError, OSError)):
                            self.m._profile()
                    self.assertEqual(fired, [case])
                    self.assertTrue(all(row['closed'] for row in records))
                    self.assertEqual(len([row for row in records if row['path'] == target]), 1)

    def test_profile_audit_and_qa_admission_keep_inherited_guards(self):
        for case in ('disabled', 'empty-manifest', 'contract', 'duplicate-key', 'source-membership',
                     'source-digest', 'source-root-escape', 'qa-fifo-admission'):
            with self.subTest(case=case), self._profile_case_tree() as (root, manifest, profile):
                source = root / sorted(profile['source_sha256'])[0]
                if case == 'empty-manifest':
                    manifest.write_bytes(b'')
                elif case == 'contract':
                    profile['release_authorized'] = True
                    manifest.write_bytes(encoded(profile) + b'\n')
                elif case == 'duplicate-key':
                    manifest.write_bytes(encoded(profile)[:-1] + b',"schema_version":"1.0.0"}')
                elif case == 'source-membership':
                    del profile['source_sha256'][sorted(profile['source_sha256'])[0]]
                    manifest.write_bytes(encoded(profile) + b'\n')
                elif case == 'source-digest':
                    source.write_bytes(source.read_bytes() + b'\nchanged')
                elif case == 'source-root-escape':
                    outside = root.with_name(root.name + '-outside')
                    self.addCleanup(lambda path=outside: shutil.rmtree(path, ignore_errors=True))
                    (root / 'schemas').rename(outside)
                    (root / 'schemas').symlink_to(outside, target_is_directory=True)
                elif case == 'qa-fifo-admission':
                    qa = importlib.import_module('reporting.typed_original_brand_qa')
                    manifest.unlink()
                    os.mkfifo(manifest)
                environment = ({'OMNIGENIS_TYPED_PDF_OBSERVATION_DISABLED': '1'}
                               if case == 'disabled' else {})
                with patch.dict(os.environ, environment), \
                        self._profile_case_io([manifest]) as records:
                    if case == 'qa-fifo-admission':
                        with patch.object(qa, '_bounded_profile_file') as later_qa_read:
                            with self.assertRaises((ValueError, OSError)):
                                qa.profile_sha256()
                            later_qa_read.assert_not_called()
                    else:
                        with self.assertRaises((ValueError, OSError)):
                            self.m._profile()
                if case == 'disabled':
                    self.assertEqual(records, [])



class TypedPdfProcessObservationTests(unittest.TestCase):
    """Optional records describe actual parent observations, not authenticated events."""

    _small_pdf = None
    _small_attempted = False

    def setUp(self):
        self.m = importlib.import_module('reporting.pdf_typed_observation')

    @classmethod
    def small_pdf(cls):
        if cls._small_pdf is None:
            if cls._small_attempted:
                raise RuntimeError('small process-observation fixture already failed')
            cls._small_attempted = True
            cls._small_pdf = native_fixture(0)
        return cls._small_pdf

    def observed(self, pdf=None):
        pdf = self.small_pdf() if pdf is None else pdf
        return self.m.observe_typed_pdf_with_processes(
            pdf_bytes=pdf, expected_pdf_sha256=sha256(pdf).hexdigest())


    def test_observed_api_binds_one_real_worker_to_same_observation(self):
        observer = importlib.import_module('reporting.pdf_typed_observation')
        observe = getattr(observer, 'observe_typed_pdf_with_processes', None)
        self.assertTrue(callable(observe), 'explicit observed typed PDF API is missing')
        pdf = self.small_pdf()
        pdf_sha = sha256(pdf).hexdigest()
        worker = ROOT / 'reporting/_pdf_typed_observation_worker.py'
        started = []
        original_start = observer._start

        def capture_start(*args, **kwargs):
            process = original_start(*args, **kwargs)
            started.append(process)
            return process

        before = monotonic_ns()
        with patch.object(observer, '_start', side_effect=capture_start) as start, \
                patch.object(observer, '_wait', wraps=observer._wait) as wait, \
                patch.object(observer, '_decode', wraps=observer._decode) as decode:
            observed, record = observe(pdf_bytes=pdf, expected_pdf_sha256=pdf_sha)
        after = monotonic_ns()

        start.assert_called_once()
        self.assertEqual(len(started), 1)
        wait.assert_called_once_with(started[0])
        decode.assert_called_once()
        self.assertIsInstance(observed, observer.TypedPdfObservation)
        self.assertEqual(set(record), {
            'kind', 'worker', 'worker_source_sha256', 'parent_pid', 'pid',
            'spawn_monotonic_ns', 'exit_monotonic_ns', 'exit_code',
            'request_header_sha256', 'pdf_sha256', 'pdf_byte_count', 'page_count',
            'typed_observation_sha256', 'profile_sha256', 'observation_sha256',
        })
        self.assertEqual(record['kind'], 'typed_observation')
        self.assertEqual(record['worker'], worker.name)
        self.assertEqual(record['worker_source_sha256'], sha256(worker.read_bytes()).hexdigest())
        self.assertEqual(record['parent_pid'], os.getpid())
        self.assertEqual(record['pid'], started[0].pid)
        self.assertNotEqual(record['pid'], record['parent_pid'])
        self.assertEqual(record['exit_code'], 0)
        self.assertEqual(started[0].returncode, 0)
        self.assertIs(type(record['spawn_monotonic_ns']), int)
        self.assertIs(type(record['exit_monotonic_ns']), int)
        self.assertLessEqual(before, record['spawn_monotonic_ns'])
        self.assertLessEqual(record['spawn_monotonic_ns'], record['exit_monotonic_ns'])
        self.assertLessEqual(record['exit_monotonic_ns'], after)
        self.assertEqual(record['request_header_sha256'],
                         sha256(b'1 ' + pdf_sha.encode('ascii') + b'\n').hexdigest())
        self.assertEqual(record['pdf_sha256'], pdf_sha)
        self.assertEqual(record['pdf_byte_count'], len(pdf))
        metadata = observed.to_dict()
        self.assertEqual(record['page_count'], metadata['page_count'])
        self.assertEqual(record['profile_sha256'], metadata['profile_sha256'])
        self.assertEqual(record['typed_observation_sha256'], observed.observation_sha256)
        self.assertTrue(all(metadata[name] is False for name in observer.NONCLAIMS))
        unsigned = dict(record)
        checksum = unsigned.pop('observation_sha256')
        self.assertEqual(checksum, sha256(encoded(unsigned)).hexdigest())
        self.assertLessEqual(len(encoded(record)), 2048)

    def test_observed_api_input_disable_and_scope_guards_precede_launch(self):
        pdf = b'%PDF-1.7\nsynthetic input for prelaunch refusals'
        pdf_sha = sha256(pdf).hexdigest()
        cases = [(bytearray(pdf), pdf_sha), (b'not a PDF', pdf_sha),
                 (pdf, 'f' * 64), (pdf, True)]
        with patch.object(self.m, '_start') as start:
            for value, expected in cases:
                with self.subTest(value_type=type(value).__name__, expected_type=type(expected).__name__):
                    with self.assertRaises(self.m.TypedPdfObservationError):
                        self.m.observe_typed_pdf_with_processes(
                            pdf_bytes=value, expected_pdf_sha256=expected)
            with patch.dict(os.environ, {'OMNIGENIS_TYPED_PDF_OBSERVATION_DISABLED': '1'}):
                with self.assertRaises(self.m.TypedPdfObservationError):
                    self.observed(pdf)
            with patch.object(self.m, 'require_no_active_scope',
                              side_effect=ValueError('active scope')) as guard:
                with self.assertRaises(self.m.TypedPdfObservationError):
                    self.m.observe_typed_pdf_with_processes(
                        pdf_bytes=None, expected_pdf_sha256=None)
                guard.assert_called_once()
            with self.assertRaises(TypeError):
                self.m.observe_typed_pdf_with_processes(
                    pdf_bytes=pdf, expected_pdf_sha256=pdf_sha, direct_record={})
        start.assert_not_called()

    def test_observed_api_rejects_fake_process_without_cleanup_or_decode(self):
        pdf = b'%PDF-1.7\nsynthetic input for fake launch refusal'
        with patch.object(self.m, '_start', return_value=object()) as start, \
                patch.object(self.m, '_cleanup_owned') as cleanup, \
                patch.object(self.m, '_wait') as wait, \
                patch.object(self.m, '_decode') as decode:
            with self.assertRaisesRegex(self.m.TypedPdfObservationError,
                                        '^typed_pdf_observation_execution_failed$'):
                self.observed(pdf)
        start.assert_called_once()
        cleanup.assert_not_called()
        wait.assert_not_called()
        decode.assert_not_called()

    def test_lost_or_reaped_child_identity_is_terminal_without_cleanup_retry(self):
        pdf = b'%PDF-1.7\nsynthetic input for lost ownership refusal'
        for returncode in (None, 0):
            with self.subTest(returncode=returncode):
                def foreign(command, _fds):
                    process = object.__new__(self.m._Process)
                    process._child_created = False
                    process.args = command
                    process.pid = os.getppid()
                    process.returncode = returncode
                    return process
                with patch.object(self.m, '_start', side_effect=foreign), \
                        patch.object(self.m, '_cleanup_owned') as cleanup, \
                        patch.object(self.m, '_wait') as wait, \
                        patch.object(self.m, '_decode') as decode, \
                        patch.object(self.m.os, 'killpg') as kill_group, \
                        patch.object(self.m.os, 'kill') as kill_child:
                    with self.assertRaises(self.m.TypedPdfObservationError):
                        self.observed(pdf)
                cleanup.assert_not_called()
                wait.assert_not_called()
                decode.assert_not_called()
                kill_group.assert_not_called()
                kill_child.assert_not_called()

    def test_capture_failure_reaps_owned_child_before_refusal(self):
        pdf = self.small_pdf()
        started = []
        original_start = self.m._start

        def capture_start(*args):
            process = original_start(*args)
            started.append(process)
            return process

        with patch.object(self.m, '_start', side_effect=capture_start) as start, \
                patch.object(self.m, 'monotonic_ns', side_effect=ValueError('capture clock unavailable')), \
                patch.object(self.m, '_cleanup_owned', wraps=self.m._cleanup_owned) as cleanup, \
                patch.object(self.m, '_wait', wraps=self.m._wait) as wait, \
                patch.object(self.m, '_decode', wraps=self.m._decode) as decode:
            with self.assertRaisesRegex(self.m.TypedPdfObservationError,
                                        '^typed_pdf_observation_execution_failed$'):
                self.observed(pdf)
        start.assert_called_once()
        self.assertEqual(len(started), 1)
        cleanup.assert_called_once_with(started[0], wall_seconds=0)
        wait.assert_not_called()
        decode.assert_not_called()
        self.assertIsNotNone(started[0].returncode)
        with self.assertRaises(ChildProcessError):
            os.waitpid(started[0].pid, os.WNOHANG)

    def test_foreign_command_metadata_cleans_the_held_original_child(self):
        pdf = self.small_pdf()
        started = []
        original_start = self.m._start

        def altered_start(command, fds):
            process = original_start(command, fds)
            started.append(process)
            process.args = [*command, 'foreign']
            return process

        with patch.object(self.m, '_start', side_effect=altered_start) as start, \
                patch.object(self.m, '_cleanup_owned', wraps=self.m._cleanup_owned) as cleanup, \
                patch.object(self.m, '_wait', wraps=self.m._wait) as wait, \
                patch.object(self.m, '_decode', wraps=self.m._decode) as decode:
            with self.assertRaises(self.m.TypedPdfObservationError):
                self.observed(pdf)
        start.assert_called_once()
        cleanup.assert_called_once_with(started[0], wall_seconds=0)
        wait.assert_not_called()
        decode.assert_not_called()
        self.assertIsNotNone(started[0].returncode)
        with self.assertRaises(ChildProcessError):
            os.waitpid(started[0].pid, os.WNOHANG)

    def test_invalid_exit_clock_or_status_withholds_decode_after_owned_wait(self):
        pdf = self.small_pdf()
        for failure in ('backwards_clock', 'clock_overflow', 'boolean_exit', 'foreign_exit'):
            with self.subTest(failure=failure):
                started = []
                original_start, original_wait = self.m._start, self.m._wait

                def capture_start(*args):
                    process = original_start(*args)
                    started.append(process)
                    return process

                def observed_wait(process):
                    code = original_wait(process)
                    self.assertEqual(code, 0)
                    if failure == 'boolean_exit':
                        return False
                    if failure == 'foreign_exit':
                        process.returncode = 1
                    return code

                clocks = [200, 199] if failure == 'backwards_clock' else (
                    [200, 9223372036854775808] if failure == 'clock_overflow' else [200, 201])
                with patch.object(self.m, '_start', side_effect=capture_start) as start, \
                        patch.object(self.m, '_wait', side_effect=observed_wait) as wait, \
                        patch.object(self.m, 'monotonic_ns', side_effect=clocks), \
                        patch.object(self.m, '_decode', wraps=self.m._decode) as decode:
                    with self.assertRaises(self.m.TypedPdfObservationError):
                        self.observed(pdf)
                start.assert_called_once()
                wait.assert_called_once_with(started[0])
                decode.assert_not_called()
                with self.assertRaises(ChildProcessError):
                    os.waitpid(started[0].pid, os.WNOHANG)
