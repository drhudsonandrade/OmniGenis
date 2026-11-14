"""Same-PDF typed candidate integration with synthetic native and failure controls."""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import copy
import importlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator
from reporting.typed_pdf_source import prepare_typed_pdf_source
from reporting.pdf_typed_observation import TypedPdfObservation
from test_reporting_typed_pdf_source import digest
from test_reporting_typed_pdf_association import declarations
from test_reporting_typed_pdf_observation import large_response_fixture

ROOT = Path(__file__).resolve().parents[1]
STAGES = ('render_pdf_candidate', 'observe_typed_pdf', 'associate_typed_pdf',
          'inspect_pdf_raster_window')
OBSERVED_STAGES = ('_render_pdf_candidate', 'observe_typed_pdf_with_processes',
                   'associate_typed_pdf', 'inspect_process_observed_pdf_raster_window')


def encoded(value):
    """Independently encode the declared canonical metadata convention."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


class TypedPdfCandidateTests(unittest.TestCase):
    """Private candidates require complete real stages; unsigned evidence is narrow."""

    fixtures = {}
    observed_fixtures = {}
    observed_attempts = set()

    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('reporting.typed_pdf_candidate'),
                             'complete same-PDF typed candidate is missing')
        self.m = importlib.import_module('reporting.typed_pdf_candidate')

    def inputs(self, source=None):
        source = declarations('empty') if source is None else source
        ir_sha = digest(source)
        expectation = prepare_typed_pdf_source(
            presentation_ir=source, expected_ir_sha256=ir_sha)
        profile = json.loads((ROOT / 'reporting/pdf-typed-candidate.v1.json').read_bytes())
        return dict(presentation_ir=source, expected_ir_sha256=ir_sha,
                    expected_source_expectation_sha256=expectation.expectation_sha256,
                    expected_profile_sha256=sha256(encoded(profile)).hexdigest())

    def case(self, name='empty'):
        if name not in self.fixtures:
            inputs = self.inputs(declarations(name))
            calls, outputs = [], {key: [] for key in STAGES}

            def observe_call(key, function):
                def call(*args, **kwargs):
                    calls.append((key, args, kwargs))
                    value = function(*args, **kwargs)
                    outputs[key].append(value)
                    return value
                return call

            with ExitStack() as stack:
                for key in STAGES:
                    stack.enter_context(patch.object(
                        self.m, key, side_effect=observe_call(key, getattr(self.m, key))))
                candidate = self.m.prepare_typed_pdf_candidate(**inputs)
            self.fixtures[name] = dict(inputs=inputs, candidate=candidate,
                                       calls=calls, outputs=outputs)
        return self.fixtures[name]

    def replay(self, *, name='empty', substitutions=None, constructor=False):
        """Substitute already captured synthetic receipts; do not repeat native jobs."""
        case = self.case(name)
        outputs = {key: list(value) for key, value in case['outputs'].items()}
        outputs.update(substitutions or {})
        with ExitStack() as stack:
            mocks = {key: stack.enter_context(patch.object(self.m, key, side_effect=value))
                     for key, value in outputs.items()}
            self.last_mocks = mocks
            operation = (self.m.TypedPdfCandidate if constructor
                         else self.m.prepare_typed_pdf_candidate)
            return operation(**copy.deepcopy(case['inputs']))

    def refused(self, **kwargs):
        with self.assertRaisesRegex(self.m.TypedPdfCandidateError,
                                    '^typed_pdf_candidate_refused$'):
            self.replay(**kwargs)


    def observed_case(self, name='empty'):
        """Capture each bounded native observed fixture once, without failure retries."""
        self.assertIn(name, ('empty', 'row'))
        if name not in self.observed_fixtures:
            self.assertNotIn(name, self.observed_attempts, 'observed fixture attempt already consumed')
            self.observed_attempts.add(name)
            inputs = self.inputs(declarations(name))
            calls, outputs = [], {key: [] for key in OBSERVED_STAGES}

            def observed_call(key, function):
                def call(*args, **kwargs):
                    calls.append((key, args, kwargs))
                    value = function(*args, **kwargs)
                    outputs[key].append(value)
                    return value
                return call

            with ExitStack() as stack:
                for key in OBSERVED_STAGES:
                    stack.enter_context(patch.object(
                        self.m, key, side_effect=observed_call(key, getattr(self.m, key))))
                for key in ('render_pdf_candidate', 'observe_typed_pdf',
                            'inspect_pdf_raster_window'):
                    stack.enter_context(patch.object(
                        self.m, key, side_effect=AssertionError('unobserved route invoked')))
                candidate, observation = self.m.prepare_observed_typed_pdf_candidate(**inputs)
            self.observed_fixtures[name] = dict(
                inputs=inputs, candidate=candidate, observation=observation,
                calls=calls, outputs=outputs)
        return self.observed_fixtures[name]

    def replay_observed(self, *, name='empty', substitutions=None, reuse_clock=True):
        """Use retained synthetic receipts and explicit clock doubles, never new workers."""
        case = self.observed_case(name)
        outputs = {key: list(value) for key, value in case['outputs'].items()}
        outputs.update(substitutions or {})
        with ExitStack() as stack:
            self.last_observed_mocks = {
                key: stack.enter_context(patch.object(self.m, key, side_effect=value))
                for key, value in outputs.items()}
            if reuse_clock:
                stack.enter_context(patch.object(self.m, 'monotonic_ns', side_effect=[
                    case['observation']['started_monotonic_ns'],
                    case['observation']['verified_monotonic_ns']]))
            for key in ('render_pdf_candidate', 'observe_typed_pdf',
                        'inspect_pdf_raster_window'):
                stack.enter_context(patch.object(
                    self.m, key, side_effect=AssertionError('unobserved route invoked')))
            return self.m.prepare_observed_typed_pdf_candidate(**copy.deepcopy(case['inputs']))

    def refused_observed(self, **kwargs):
        """Require a whole-operation refusal rather than a candidate prefix."""
        with self.assertRaisesRegex(self.m.TypedPdfCandidateError,
                                    '^typed_pdf_candidate_refused$'):
            self.replay_observed(**kwargs)

    @staticmethod
    def reseal_process(record):
        """Rehash a deliberate metadata mutation without claiming it was observed."""
        value = copy.deepcopy(record)
        value.pop('observation_sha256', None)
        value['observation_sha256'] = sha256(encoded(value)).hexdigest()
        return value

    def rebind_child_hash(self, entry):
        """Keep only descriptive hashes coherent in malformed nested-record controls."""
        entry['child_observation'] = self.reseal_process(entry['child_observation'])
        entry['direct_process']['nested_observation_sha256'] = sha256(
            encoded(entry['child_observation'])).hexdigest()
        entry['direct_process'] = self.reseal_process(entry['direct_process'])

    def rejected_process_sidecar(self, observation, name='empty'):
        """Exercise closed composition admission against an already accepted candidate."""
        candidate = self.observed_case(name)['candidate']
        with self.assertRaises(ValueError):
            self.m._validate_process_observation(
                observation, candidate.to_bytes(), candidate.pdf_bytes)

    def test_observed_candidate_accounts_for_actual_stage_processes_once(self):
        operation = getattr(self.m, 'prepare_observed_typed_pdf_candidate', None)
        self.assertTrue(callable(operation), 'observed typed composition is missing')
        case = self.observed_case()
        candidate, observation = case['candidate'], case['observation']
        self.assertIs(type(candidate), self.m.TypedPdfCandidate)
        metadata = candidate.to_dict()
        self.assertEqual(metadata['page_count'], 1)
        self.assertEqual(metadata['raster_window_count'], 1)
        self.assertEqual([item[0] for item in case['calls']], list(OBSERVED_STAGES))
        self.assertTrue(all(len(case['outputs'][key]) == 1 for key in OBSERVED_STAGES))
        self.assertIs(case['calls'][0][2]['observe_processes'], True)
        self.assertEqual(observation['candidate_sha256'], candidate.candidate_sha256)
        self.assertEqual(observation['pdf_sha256'], sha256(candidate.pdf_bytes).hexdigest())
        self.assertEqual(observation['observation_sha256'], sha256(encoded({
            key: value for key, value in observation.items()
            if key != 'observation_sha256'})).hexdigest())
        self.assertEqual((observation['observed_direct_spawns'],
                          observation['observed_direct_exits'],
                          observation['observed_nested_spawns'],
                          observation['observed_nested_exits'],
                          observation['observed_total_spawns'],
                          observation['observed_total_exits']), (3, 3, 2, 2, 5, 5))
        self.assertLessEqual(len(encoded(observation)), 160 * 1024)
        self.assertFalse(observation['aggregate_job_resources_qualified'])
        self.assertFalse(observation['authenticated_worker_execution'])
        self.assertFalse(observation['release_authorized'])
        self.assertFalse(metadata['declared_process_schedule']['execution_authenticated'])


    def test_observed_multiwindow_reconciles_every_direct_and_nested_child(self):
        case = self.observed_case('row')
        candidate, observation = case['candidate'], case['observation']
        metadata = candidate.to_dict()
        self.assertEqual((metadata['page_count'], metadata['raster_window_count']), (5, 2))
        self.assertEqual((observation['observed_direct_spawns'],
                          observation['observed_nested_spawns'],
                          observation['observed_total_spawns']), (4, 7, 11))
        self.assertEqual([item[2]['window_index'] for item in case['calls']
                          if item[0] == OBSERVED_STAGES[-1]], [0, 1])
        pids = {observation['parent_pid']}
        previous = observation['started_monotonic_ns']
        records = [observation['render_process'], observation['typed_observation_process']]
        records.extend(entry['direct_process'] for entry in observation['raster_windows'])
        for record in records:
            self.assertEqual(record['parent_pid'], os.getpid())
            self.assertNotIn(record['pid'], pids)
            pids.add(record['pid'])
            self.assertLessEqual(previous, record['spawn_monotonic_ns'])
            self.assertLessEqual(record['spawn_monotonic_ns'], record['exit_monotonic_ns'])
            previous = record['exit_monotonic_ns']
        for entry in observation['raster_windows']:
            direct = entry['direct_process']
            for child in entry['child_observation']['processes']:
                self.assertEqual(child['parent_pid'], direct['pid'])
                self.assertNotIn(child['pid'], pids)
                pids.add(child['pid'])
                self.assertLessEqual(direct['spawn_monotonic_ns'], child['observed_spawn_ns'])
                self.assertLessEqual(child['observed_exit_ns'], direct['exit_monotonic_ns'])
        self.assertEqual(len(pids) - 1, 11)
        self.assertLessEqual(previous, observation['verified_monotonic_ns'])

    def test_observed_and_default_results_remain_identical_and_separate(self):
        observed = self.observed_case()['candidate']
        ordinary = self.case()['candidate']
        self.assertEqual(observed.pdf_bytes, ordinary.pdf_bytes)
        self.assertEqual(observed.to_bytes(), ordinary.to_bytes())
        with ExitStack() as stack:
            mocks = [stack.enter_context(patch.object(self.m, key))
                     for key in ('_render_pdf_candidate', 'observe_typed_pdf_with_processes',
                                 'inspect_process_observed_pdf_raster_window',
                                 '_build_process_observation')]
            self.replay()
        for mock in mocks:
            mock.assert_not_called()

    def test_observed_factory_accepts_no_artifact_executor_or_success_options(self):
        operation = self.m.prepare_observed_typed_pdf_candidate
        self.assertEqual(inspect.signature(operation).parameters,
                         inspect.signature(self.m.prepare_typed_pdf_candidate).parameters)
        for field, value in (('pdf_bytes', b'%PDF-1.7'), ('html', '<p>Injected</p>'),
                             ('executor', lambda: None), ('callback', lambda: None),
                             ('sidecar', {}), ('passed', True), ('budget', 1)):
            with self.subTest(field=field), patch.object(self.m, '_render_pdf_candidate') as render:
                with self.assertRaises(TypeError):
                    operation(**self.inputs(), **{field: value})
                render.assert_not_called()

    def test_observed_admission_disables_and_active_scope_precede_render(self):
        for field in ('expected_ir_sha256', 'expected_source_expectation_sha256',
                      'expected_profile_sha256'):
            inputs = self.inputs()
            inputs[field] = '0' * 64
            with self.subTest(field=field), patch.object(self.m, '_render_pdf_candidate') as render:
                with self.assertRaises(self.m.TypedPdfCandidateError):
                    self.m.prepare_observed_typed_pdf_candidate(**inputs)
                render.assert_not_called()
        for flag in ('OMNIGENIS_TYPED_PDF_CANDIDATE_DISABLED', 'OMNIGENIS_PDF_RENDERER_DISABLED',
                     'OMNIGENIS_TYPED_PDF_OBSERVATION_DISABLED', 'OMNIGENIS_PDF_RASTER_WINDOWS_DISABLED'):
            with self.subTest(flag=flag), patch.dict(os.environ, {flag: '1'}), \
                    patch.object(self.m, '_render_pdf_candidate') as render:
                with self.assertRaises(self.m.TypedPdfCandidateError):
                    self.m.prepare_observed_typed_pdf_candidate(**self.inputs())
                render.assert_not_called()
        with patch.object(self.m, 'require_no_active_scope',
                          side_effect=ValueError('active scope')) as guard, \
                patch.object(self.m, '_render_pdf_candidate') as render:
            with self.assertRaises(self.m.TypedPdfCandidateError):
                self.m.prepare_observed_typed_pdf_candidate(**self.inputs())
            guard.assert_called_once()
            render.assert_not_called()

    def test_observed_stage_and_final_window_failures_do_not_retry(self):
        for index, stage in enumerate(OBSERVED_STAGES):
            with self.subTest(stage=stage):
                self.refused_observed(substitutions={stage: RuntimeError('PRIVATE-CONTENT')})
                self.assertEqual(self.last_observed_mocks[stage].call_count, 1)
                for later in OBSERVED_STAGES[index + 1:]:
                    self.last_observed_mocks[later].assert_not_called()
        first = self.observed_case('row')['outputs'][OBSERVED_STAGES[-1]][0]
        self.refused_observed(name='row', substitutions={
            OBSERVED_STAGES[-1]: [first, RuntimeError('final window failed')]})
        self.assertEqual(self.last_observed_mocks[OBSERVED_STAGES[-1]].call_count, 2)

    def test_observed_direct_headers_sources_and_result_bindings_refuse(self):
        case = self.observed_case()
        controls = (
            (OBSERVED_STAGES[0], 1, ('worker_source_sha256', 'request_header_sha256',
                                    'html_sha256', 'pdf_sha256', 'render_sha256', 'profile_sha256')),
            (OBSERVED_STAGES[1], 1, ('worker_source_sha256', 'request_header_sha256',
                                    'pdf_sha256', 'typed_observation_sha256', 'profile_sha256')),
            (OBSERVED_STAGES[-1], 2, ('worker_source_sha256', 'request_header_sha256',
                                     'inspection_sha256', 'nested_observation_sha256')),
        )
        for stage, position, fields in controls:
            for field in fields:
                values = list(case['outputs'][stage][0])
                record = copy.deepcopy(values[position])
                record[field] = '0' * 64
                values[position] = self.reseal_process(record)
                with self.subTest(stage=stage, field=field):
                    self.refused_observed(substitutions={stage: [tuple(values)]})
        for field in ('pdf_sha256', 'ir_sha256', 'plan_sha256'):
            values = list(case['outputs'][OBSERVED_STAGES[-1]][0])
            record = copy.deepcopy(values[2])
            record['identity'][field] = '0' * 64
            values[2] = self.reseal_process(record)
            with self.subTest(identity=field):
                self.refused_observed(substitutions={OBSERVED_STAGES[-1]: [tuple(values)]})

    def test_observed_direct_shapes_and_integer_domains_are_closed(self):
        case = self.observed_case()
        observed, record = case['outputs'][OBSERVED_STAGES[1]][0]
        controls = []
        for field, value in (('kind', 'render'), ('worker', '_pdf_render_worker.py'),
                             ('parent_pid', os.getpid() + 1), ('parent_pid', True),
                             ('pid', True), ('pid', 1), ('pid', 2147483648),
                             ('spawn_monotonic_ns', True), ('spawn_monotonic_ns', -1),
                             ('exit_monotonic_ns', 1 << 63), ('exit_code', True),
                             ('exit_code', 1), ('pdf_byte_count', True), ('page_count', True)):
            changed = copy.deepcopy(record)
            changed[field] = value
            controls.append((field, changed))
        controls.append(('extra', dict(record, extra='unexpected')))
        missing = copy.deepcopy(record)
        del missing['worker']
        controls.append(('missing', missing))
        for label, changed in controls:
            with self.subTest(control=label):
                self.refused_observed(substitutions={
                    OBSERVED_STAGES[1]: [(observed, self.reseal_process(changed))]})
                self.last_observed_mocks['associate_typed_pdf'].assert_not_called()

    def test_observed_global_pid_collisions_and_direct_order_refuse(self):
        case = self.observed_case()
        original = case['observation']
        for field, value in (('pid', original['render_process']['pid']),
                             ('spawn_monotonic_ns', original['render_process']['exit_monotonic_ns'] - 1)):
            observed, record = case['outputs'][OBSERVED_STAGES[1]][0]
            changed = copy.deepcopy(record)
            changed[field] = value
            with self.subTest(field=field):
                self.refused_observed(substitutions={
                    OBSERVED_STAGES[1]: [(observed, self.reseal_process(changed))]})
        sidecar = copy.deepcopy(original)
        entry = sidecar['raster_windows'][0]
        entry['child_observation']['processes'][0]['pid'] = original['typed_observation_process']['pid']
        self.rebind_child_hash(entry)
        self.rejected_process_sidecar(self.reseal_process(sidecar))

    def test_observed_missing_duplicate_reordered_or_extra_windows_refuse(self):
        original = self.observed_case('row')['observation']
        first, last = original['raster_windows']
        for windows in ([], [first], [first, first], [last, first], [first, last, last]):
            changed = copy.deepcopy(original)
            changed['raster_windows'] = copy.deepcopy(windows)
            with self.subTest(windows=[entry['window_index'] for entry in windows]):
                self.rejected_process_sidecar(self.reseal_process(changed), name='row')
        changed = copy.deepcopy(original)
        changed['raster_windows'][0]['extra'] = True
        self.rejected_process_sidecar(self.reseal_process(changed), name='row')

    def test_observed_nested_schedule_parent_and_lifetime_are_rechecked(self):
        original = self.observed_case()['observation']
        for change in ('missing', 'extra', 'reverse', 'parent', 'page', 'early', 'late'):
            sidecar = copy.deepcopy(original)
            entry = sidecar['raster_windows'][0]
            children = entry['child_observation']['processes']
            direct = entry['direct_process']
            if change == 'missing':
                children.pop()
            elif change == 'extra':
                children.append(copy.deepcopy(children[-1]))
            elif change == 'reverse':
                children.reverse()
            elif change == 'parent':
                children[0]['parent_pid'] = original['typed_observation_process']['pid']
            elif change == 'page':
                children[-1]['page'] = 2
            elif change == 'early':
                children[0]['observed_spawn_ns'] = direct['spawn_monotonic_ns'] - 1
            else:
                children[-1]['observed_exit_ns'] = direct['exit_monotonic_ns'] + 1
            self.rebind_child_hash(entry)
            with self.subTest(change=change):
                self.rejected_process_sidecar(self.reseal_process(sidecar))

    def test_observed_context_counts_clocks_and_nonclaims_cannot_be_rehashed(self):
        original = self.observed_case()['observation']
        controls = {
            **{field: '0' * 64 for field in (
                'candidate_sha256', 'candidate_profile_sha256', 'ir_sha256',
                'source_expectation_sha256', 'typed_html_sha256', 'html_sha256',
                'typed_observation_sha256', 'association_sha256', 'pdf_sha256',
                'raster_window_plan_sha256', 'raster_ledger_sha256')},
            'parent_pid': os.getpid() + 1, 'page_count': 2,
            'observed_direct_spawns': True, 'observed_direct_exits': 4,
            'observed_nested_spawns': 3, 'observed_nested_exits': 3,
            'observed_total_spawns': 6, 'observed_total_exits': 6,
            'declared_retries': 1, 'timer_helper_processes_added': 1,
            'started_monotonic_ns': original['render_process']['spawn_monotonic_ns'] + 1,
            'verified_monotonic_ns': original['raster_windows'][-1]['direct_process']['exit_monotonic_ns'] - 1,
            'aggregate_job_resources_qualified': True, 'authenticated_worker_execution': True,
            'visual_semantics_verified': True, 'accessibility_qualified': True,
            'default_pipeline_activated': True, 'release_authorized': True,
        }
        for field, value in controls.items():
            changed = copy.deepcopy(original)
            changed[field] = value
            with self.subTest(field=field):
                self.rejected_process_sidecar(self.reseal_process(changed))
        self.rejected_process_sidecar(self.reseal_process(dict(original, extra=True)))

    def test_observed_prior_process_receipts_do_not_fit_a_fresh_operation(self):
        self.observed_case()
        self.refused_observed(reuse_clock=False)
        self.assertEqual(self.last_observed_mocks[OBSERVED_STAGES[0]].call_count, 1)
        for stage in OBSERVED_STAGES[1:]:
            self.last_observed_mocks[stage].assert_not_called()

    def test_observed_records_are_detached_before_following_stages(self):
        case = self.observed_case()
        rendered, record = case['outputs'][OBSERVED_STAGES[0]][0]
        record = copy.deepcopy(record)
        expected = encoded(record)

        def mutate_render(*args, **kwargs):
            record['html_sha256'] = '0' * 64
            return case['outputs'][OBSERVED_STAGES[1]][0]

        _, observation = self.replay_observed(substitutions={
            OBSERVED_STAGES[0]: [(rendered, record)], OBSERVED_STAGES[1]: mutate_render})
        self.assertEqual(encoded(observation['render_process']), expected)
        row = self.observed_case('row')
        first, last = row['outputs'][OBSERVED_STAGES[-1]]
        mutable_first = (first[0], copy.deepcopy(first[1]), copy.deepcopy(first[2]))
        expected_nested = encoded(mutable_first[1])

        def mutate_first(*args, **kwargs):
            if kwargs['window_index'] == 0:
                return mutable_first
            mutable_first[1]['processes'][0]['pid'] = 1
            mutable_first[2]['request_header_sha256'] = '0' * 64
            return last

        _, observation = self.replay_observed(
            name='row', substitutions={OBSERVED_STAGES[-1]: mutate_first})
        self.assertEqual(encoded(observation['raster_windows'][0]['child_observation']),
                         expected_nested)

    def test_observed_export_drift_cap_and_interrupt_withhold_the_pair(self):
        self.observed_case()
        original_validate = self.m._validate_process_observation
        original_profile = self.m._observer_profile
        for mode in ('disabled', 'profile'):
            changed = [False]

            def export(*args, **kwargs):
                result = original_validate(*args, **kwargs)
                changed[0] = True
                if mode == 'disabled':
                    os.environ['OMNIGENIS_TYPED_PDF_CANDIDATE_DISABLED'] = '1'
                return result

            def profile():
                return '0' * 64 if changed[0] and mode == 'profile' else original_profile()

            with self.subTest(mode=mode), patch.dict(os.environ), \
                    patch.object(self.m, '_validate_process_observation', side_effect=export), \
                    patch.object(self.m, '_observer_profile', side_effect=profile):
                self.refused_observed()
        with patch.object(self.m, 'MAX_PROCESS_OBSERVATION_BYTES', 1):
            self.refused_observed()
        failure = KeyboardInterrupt('interrupted export')
        with patch.object(self.m, '_validate_process_observation', side_effect=failure):
            with self.assertRaises(KeyboardInterrupt) as raised:
                self.replay_observed()
        self.assertIs(raised.exception, failure)

    def test_observed_sidecar_remains_detached_bounded_and_content_free(self):
        case = self.observed_case('row')
        candidate, observation = case['candidate'], case['observation']
        raw = encoded(observation)
        self.assertLessEqual(len(raw), 160 * 1024)
        for content in (b'SYNTHETIC-CASE', b'Technical report', b'NM_000001', b'R0000', b'%PDF-1.7'):
            self.assertNotIn(content, raw)
        self.assertTrue(all(observation[field] is False for field in (
            'source_authenticity_verified', 'worker_results_authenticated',
            'whole_graph_observed', 'arbitrary_descendants_observed',
            'external_observer_measured', 'aggregate_job_resources_qualified',
            'authenticated_worker_execution', 'default_pipeline_activated', 'release_authorized')))
        original_candidate = candidate.to_bytes()
        detached = copy.deepcopy(observation)
        detached['render_process']['pdf_sha256'] = '0' * 64
        self.assertEqual(encoded(observation), raw)
        self.assertEqual(candidate.to_bytes(), original_candidate)

    def test_native_empty_table_completes_the_same_pdf(self):
        case = self.case()
        result = case['candidate'].to_dict()
        self.assertEqual(result['page_count'], 1)
        self.assertEqual(result['source_counts']['table_rows'], 0)
        self.assertEqual(result['pdf_sha256'], sha256(case['candidate'].pdf_bytes).hexdigest())
        self.assertEqual([c[0] for c in case['calls']], list(STAGES))
        for _, _, kwargs in case['calls'][1:2] + case['calls'][3:]:
            self.assertIs(kwargs['pdf_bytes'], case['candidate'].pdf_bytes)
        self.assertEqual(result['raster_inspected_pages'], 1)

    def test_native_split_row_covers_full_and_partial_windows(self):
        case = self.case('row')
        result = case['candidate'].to_dict()
        self.assertEqual(result['page_count'], 5)
        self.assertEqual([(r['first_page'], r['last_page'])
                          for r in result['raster_windows']], [(1, 4), (5, 5)])
        self.assertEqual(result['source_counts']['table_rows'], 1)
        self.assertEqual(result['raster_inspected_pages'], 5)
        self.assertEqual(result['raster_window_count'], 2)
        self.assertEqual([c[2]['window_index'] for c in case['calls']
                          if c[0] == 'inspect_pdf_raster_window'], [0, 1])
        self.assertEqual(result['declared_process_schedule']['total'], 11)
        self.assertFalse(result['declared_process_schedule']['execution_authenticated'])

    def test_native_96_rows_retains_every_finding(self):
        result = self.case('long')['candidate'].to_dict()
        self.assertEqual(result['source_counts']['table_rows'], 96)
        self.assertEqual(result['source_counts']['represented_findings'], 96)
        self.assertGreater(result['page_count'], 1)
        self.assertEqual(result['raster_inspected_pages'], result['page_count'])

    def test_native_literal_loss_refuses_before_any_raster(self):
        inputs = self.inputs(declarations('controls'))
        with patch.object(self.m, 'render_pdf_candidate',
                          wraps=self.m.render_pdf_candidate) as render, \
                patch.object(self.m, 'observe_typed_pdf',
                             wraps=self.m.observe_typed_pdf) as observe, \
                patch.object(self.m, 'associate_typed_pdf',
                             wraps=self.m.associate_typed_pdf) as associate, \
                patch.object(self.m, 'inspect_pdf_raster_window') as raster:
            with self.assertRaisesRegex(self.m.TypedPdfCandidateError,
                                        '^typed_pdf_candidate_refused$'):
                self.m.prepare_typed_pdf_candidate(**inputs)
        self.assertEqual((render.call_count, observe.call_count, associate.call_count), (1, 1, 1))
        raster.assert_not_called()

    def test_bad_input_digests_refuse_before_render(self):
        for field in ('expected_ir_sha256', 'expected_source_expectation_sha256',
                      'expected_profile_sha256'):
            for value in (None, True, '0' * 64, ' ' + 'a' * 64):
                with self.subTest(field=field, value=value):
                    inputs = self.inputs()
                    inputs[field] = value
                    with patch.object(self.m, 'render_pdf_candidate') as render:
                        with self.assertRaises(self.m.TypedPdfCandidateError):
                            self.m.prepare_typed_pdf_candidate(**inputs)
                    render.assert_not_called()

    def test_unsupported_locale_refuses_before_render(self):
        inputs = self.inputs()
        inputs['presentation_ir']['locale_id'] = 'pt-BR'
        inputs['expected_ir_sha256'] = digest(inputs['presentation_ir'])
        with patch.object(self.m, 'render_pdf_candidate') as render:
            with self.assertRaises(self.m.TypedPdfCandidateError):
                self.m.prepare_typed_pdf_candidate(**inputs)
        render.assert_not_called()

    def test_no_caller_pdf_html_receipts_or_pass_options(self):
        for field, value in (('pdf_bytes', b'%PDF-1.7'), ('html', '<h1>Injected</h1>'),
                             ('evidence', {'status': 'PASS'}), ('passed', True),
                             ('pages_per_window', 32), ('output_path', '/tmp/unused')):
            with self.subTest(field=field), patch.object(self.m, 'render_pdf_candidate') as render:
                with self.assertRaises(TypeError):
                    self.m.prepare_typed_pdf_candidate(**self.inputs(), **{field: value})
                render.assert_not_called()

    def test_all_declared_disable_flags_refuse_before_render(self):
        for flag in ('OMNIGENIS_TYPED_PDF_CANDIDATE_DISABLED',
                     'OMNIGENIS_PDF_RENDERER_DISABLED',
                     'OMNIGENIS_TYPED_PDF_OBSERVATION_DISABLED',
                     'OMNIGENIS_PDF_RASTER_WINDOWS_DISABLED'):
            with self.subTest(flag=flag), patch.dict(os.environ, {flag: '1'}), \
                    patch.object(self.m, 'render_pdf_candidate') as render:
                with self.assertRaises(self.m.TypedPdfCandidateError):
                    self.m.prepare_typed_pdf_candidate(**self.inputs())
                render.assert_not_called()

    def test_active_native_scope_guard_runs_before_render(self):
        with patch.object(self.m, 'require_no_active_scope',
                          side_effect=ValueError('blocked scope')) as guard, \
                patch.object(self.m, 'render_pdf_candidate') as render:
            with self.assertRaises(self.m.TypedPdfCandidateError):
                self.m.prepare_typed_pdf_candidate(**self.inputs())
        guard.assert_called_once()
        render.assert_not_called()

    def test_each_stage_failure_stops_without_retry(self):
        for index, stage in enumerate(STAGES):
            with self.subTest(stage=stage):
                self.refused(substitutions={stage: RuntimeError('PRIVATE-CONTENT')})
                self.assertEqual(self.last_mocks[stage].call_count, 1)
                for later in STAGES[index + 1:]:
                    self.last_mocks[later].assert_not_called()

    def test_final_raster_failure_exposes_no_partial_candidate(self):
        first = self.case('row')['outputs']['inspect_pdf_raster_window'][0]
        self.refused(name='row', substitutions={
            'inspect_pdf_raster_window': [first, RuntimeError('last window failure')]})
        self.assertEqual(self.last_mocks['inspect_pdf_raster_window'].call_count, 2)

    def test_render_html_identity_mismatch_refuses_before_observation(self):
        rendered = self.case()['outputs']['render_pdf_candidate'][0]
        self.refused(substitutions={'render_pdf_candidate': [replace(rendered, html_sha256='0' * 64)]})
        self.last_mocks['observe_typed_pdf'].assert_not_called()

    def test_render_page_count_mismatch_refuses_before_raster(self):
        rendered = self.case()['outputs']['render_pdf_candidate'][0]
        self.refused(substitutions={'render_pdf_candidate': [replace(rendered, page_count=2)]})
        self.last_mocks['inspect_pdf_raster_window'].assert_not_called()

    def test_foreign_observation_refuses_before_association(self):
        foreign = self.case('row')['outputs']['observe_typed_pdf'][0]
        self.refused(substitutions={'observe_typed_pdf': [foreign]})
        self.last_mocks['associate_typed_pdf'].assert_not_called()

    def test_large_observation_is_readmitted_before_association(self):
        source, rendered, observed = large_response_fixture()
        self.assertGreater(len(observed.to_bytes()), 4 * 1024 * 1024)
        self.assertLessEqual(len(observed.to_bytes()), 11 * 1024 * 1024)
        with patch.object(self.m, 'render_pdf_candidate', return_value=rendered) as render, \
                patch.object(self.m, 'observe_typed_pdf', return_value=observed) as observe, \
                patch.object(self.m, 'associate_typed_pdf',
                             side_effect=RuntimeError('intentional boundary stop')) as associate, \
                patch.object(self.m, 'inspect_pdf_raster_window') as raster:
            with self.assertRaisesRegex(self.m.TypedPdfCandidateError,
                                        '^typed_pdf_candidate_refused$'):
                self.m.prepare_typed_pdf_candidate(**self.inputs(source))
        render.assert_called_once()
        observe.assert_called_once_with(
            pdf_bytes=rendered.pdf_bytes,
            expected_pdf_sha256=sha256(rendered.pdf_bytes).hexdigest())
        associate.assert_called_once()
        admitted = associate.call_args.kwargs['observation']
        self.assertIs(type(admitted), TypedPdfObservation)
        self.assertIsNot(admitted, observed)
        self.assertEqual(admitted.to_bytes(), observed.to_bytes())
        self.assertEqual(associate.call_args.kwargs['expected_observation_sha256'],
                         admitted.observation_sha256)
        raster.assert_not_called()

    def test_oversized_forged_observation_refuses_before_association(self):
        self.case()
        forged = object.__new__(TypedPdfObservation)
        object.__setattr__(forged, '_payload', b' ' * (11 * 1024 * 1024 + 1))
        with patch.object(TypedPdfObservation, '__post_init__') as reconstruct:
            self.refused(substitutions={'observe_typed_pdf': [forged]})
            reconstruct.assert_not_called()
        self.last_mocks['associate_typed_pdf'].assert_not_called()

    def test_association_receipt_keeps_its_separate_four_mib_ceiling(self):
        original = self.case()['outputs']['associate_typed_pdf'][0]
        forged = copy.copy(original)
        oversized = b' ' * (4 * 1024 * 1024 + 1)
        object.__setattr__(forged, '_serialized', oversized)
        object.__setattr__(forged, 'association_sha256', sha256(oversized).hexdigest())
        with patch.object(type(forged), 'summary') as summarize:
            self.refused(substitutions={'associate_typed_pdf': [forged]})
            summarize.assert_not_called()
        self.last_mocks['inspect_pdf_raster_window'].assert_not_called()

    def test_wrong_stage_result_types_are_refused(self):
        for stage in STAGES:
            for bad in (True, None, {'status': 'PASS'}):
                with self.subTest(stage=stage, bad=bad):
                    self.refused(substitutions={stage: [bad]})

    def test_foreign_association_refuses_before_raster(self):
        foreign = self.case('row')['outputs']['associate_typed_pdf'][0]
        self.refused(substitutions={'associate_typed_pdf': [foreign]})
        self.last_mocks['inspect_pdf_raster_window'].assert_not_called()

    def test_raster_ir_and_page_count_are_checked_before_ledger_projection(self):
        receipt = self.case()['outputs']['inspect_pdf_raster_window'][0]
        for changes in ({'ir_sha256': '0' * 64}, {'page_count': 2}):
            with self.subTest(changes=changes):
                self.refused(substitutions={
                    'inspect_pdf_raster_window': [replace(receipt, **changes)]})

    def test_raster_pdf_and_plan_identity_must_match(self):
        receipt = self.case()['outputs']['inspect_pdf_raster_window'][0]
        for key in ('pdf_sha256', 'plan_sha256'):
            with self.subTest(key=key):
                self.refused(substitutions={
                    'inspect_pdf_raster_window': [replace(receipt, **{key: '0' * 64})]})

    def test_duplicate_and_reordered_raster_windows_refuse(self):
        first, last = self.case('row')['outputs']['inspect_pdf_raster_window']
        for receipts in ([first, first], [last, first]):
            with self.subTest(order=[x.window_index for x in receipts]):
                self.refused(name='row', substitutions={'inspect_pdf_raster_window': receipts})

    def test_missing_raster_window_refuses(self):
        first = self.case('row')['outputs']['inspect_pdf_raster_window'][0]
        self.refused(name='row', substitutions={'inspect_pdf_raster_window': [first]})

    def test_failed_binding_ledger_refuses_complete_result(self):
        with patch.object(self.m, 'bind_pdf_page_window_evidence',
                          side_effect=ValueError('missing range')):
            self.refused()

    def test_detached_metadata_and_frozen_result(self):
        candidate = self.case()['candidate']
        raw = candidate.to_bytes()
        exported = candidate.to_dict()
        exported['raster_windows'][0]['pdf_sha256'] = '0' * 64
        self.assertEqual(candidate.to_bytes(), raw)
        self.assertEqual(candidate.candidate_sha256, sha256(raw).hexdigest())
        with self.assertRaises((FrozenInstanceError, AttributeError, TypeError)):
            candidate._pdf_bytes = b'changed'

    def test_retained_pdf_and_evidence_tampering_are_refused(self):
        original = self.case()['candidate']
        for slot, value in (('_pdf_bytes', original.pdf_bytes + b'\n'),
                            ('_payload', original.to_bytes() + b' ')):
            candidate = copy.copy(original)
            object.__setattr__(candidate, slot, value)
            with self.subTest(slot=slot), self.assertRaises(self.m.TypedPdfCandidateError):
                candidate.to_dict()

    def test_forged_unbounded_candidate_slots_are_refused(self):
        candidate = object.__new__(self.m.TypedPdfCandidate)
        object.__setattr__(candidate, '_pdf_bytes', b'%PDF-1.7')
        object.__setattr__(candidate, '_payload', b' ' * (65536 + 1))
        with self.assertRaises(self.m.TypedPdfCandidateError):
            candidate.to_bytes()

    def test_result_is_bounded_canonical_and_content_free(self):
        candidate = self.case('row')['candidate']
        raw = candidate.to_bytes()
        self.assertEqual(encoded(json.loads(raw)), raw)
        self.assertLessEqual(len(raw), 65536)
        for text in (b'SYNTHETIC-CASE', b'Technical report', b'NM_000001', b'R0000'):
            self.assertNotIn(text, raw)
            self.assertNotIn(text.decode(), repr(candidate))

    def test_narrow_claims_do_not_authorize_release(self):
        result = self.case()['candidate'].to_dict()
        positive = {'literal_source_associated', 'effective_typed_graph_policy_checked',
                    'all_pages_raster_geometry_inspected'}
        self.assertEqual({k for k, v in result['claims'].items() if v is True}, positive)
        self.assertTrue(all(type(v) is bool for v in result['claims'].values()))
        self.assertEqual(result['release_authorization'], 'NOT_ESTABLISHED')
        self.assertFalse(result['claims']['aggregate_job_resources_qualified'])
        self.assertFalse(result['claims']['visual_semantics_verified'])
        self.assertFalse(result['claims']['release_authorized'])

    def test_closed_schema_accepts_native_and_rejects_expanded_claims(self):
        schema = json.loads((ROOT / 'schemas/report-typed-pdf-candidate.v1.schema.json').read_bytes())
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        valid = self.case()['candidate'].to_dict()
        self.assertEqual(list(validator.iter_errors(valid)), [])
        for field in ('release_authorized', 'visual_semantics_verified', 'accessibility_qualified'):
            expanded = copy.deepcopy(valid)
            expanded['claims'][field] = True
            self.assertTrue(list(validator.iter_errors(expanded)))
        expanded = dict(valid, unexpected=True)
        self.assertTrue(list(validator.iter_errors(expanded)))

    def test_separate_html_identities_and_all_upstream_digests(self):
        case = self.case()
        data = case['candidate'].to_dict()
        self.assertNotEqual(data['typed_html_sha256'], data['html_sha256'])
        self.assertEqual(data['ir_sha256'], case['inputs']['expected_ir_sha256'])
        self.assertEqual(data['source_expectation_sha256'],
                         case['inputs']['expected_source_expectation_sha256'])
        self.assertEqual(data['render_result_sha256'],
                         case['outputs']['render_pdf_candidate'][0].result_sha256)
        self.assertEqual(data['association_sha256'],
                         case['outputs']['associate_typed_pdf'][0].association_sha256)

    def test_direct_construction_runs_the_complete_boundary(self):
        candidate = self.replay(constructor=True)
        self.assertEqual(candidate.to_bytes(), self.case()['candidate'].to_bytes())
        self.assertEqual([self.last_mocks[key].call_count for key in STAGES], [1, 1, 1, 1])

    def forged(self, data, pdf=None):
        """Build bounded malformed retained evidence without claiming native execution."""
        pdf = self.case()['candidate'].pdf_bytes if pdf is None else pdf
        result = object.__new__(self.m.TypedPdfCandidate)
        payload = encoded(data)
        object.__setattr__(result, '_pdf_bytes', pdf)
        object.__setattr__(result, '_payload', payload)
        object.__setattr__(result, '_sealed_sha256', sha256(payload).hexdigest())
        return result

    def test_bounded_minimal_forged_candidate_is_refused(self):
        pdf = b'%PDF-1.7'
        data = {'pdf_sha256': sha256(pdf).hexdigest(), 'pdf_byte_count': len(pdf),
                'profile_sha256': self.inputs()['expected_profile_sha256']}
        candidate = self.forged(data, pdf)
        with self.assertRaises(self.m.TypedPdfCandidateError):
            candidate.pdf_bytes

    def test_rehashed_expanded_claims_and_extra_fields_are_refused(self):
        original = self.case()['candidate'].to_dict()
        for key in ('release_authorized', 'visual_semantics_verified', 'accessibility_qualified'):
            data = copy.deepcopy(original)
            data['claims'][key] = True
            with self.subTest(claim=key), self.assertRaises(self.m.TypedPdfCandidateError):
                self.forged(data).to_dict()
        data = dict(original, unexpected='PRIVATE-CONTENT')
        with self.assertRaises(self.m.TypedPdfCandidateError):
            self.forged(data).to_bytes()

    def test_rehashed_incomplete_or_foreign_raster_evidence_is_refused(self):
        original = self.case()['candidate'].to_dict()
        controls = []
        missing = copy.deepcopy(original)
        missing['raster_windows'] = []
        controls.append(missing)
        for key in ('ir_sha256', 'pdf_sha256', 'plan_sha256'):
            foreign = copy.deepcopy(original)
            foreign['raster_windows'][0][key] = '0' * 64
            controls.append(foreign)
        for data in controls:
            with self.subTest(windows=data['raster_windows']), \
                    self.assertRaises(self.m.TypedPdfCandidateError):
                self.forged(data).to_bytes()

    def test_rehashed_coverage_count_schedule_and_ledger_are_refused(self):
        original = self.case()['candidate'].to_dict()
        for key, value in (('raster_inspected_pages', 2),
                           ('raster_window_count', 2), ('raster_ledger_sha256', '0' * 64),
                           ('raster_window_plan_sha256', '0' * 64),
                           ('observer_profile_sha256', '0' * 64)):
            data = copy.deepcopy(original)
            data[key] = value
            with self.subTest(field=key), self.assertRaises(self.m.TypedPdfCandidateError):
                self.forged(data).to_dict()
        data = copy.deepcopy(original)
        data['declared_process_schedule']['total'] += 1
        with self.assertRaises(self.m.TypedPdfCandidateError):
            self.forged(data).to_dict()

    def test_profile_manifest_drift_refuses_before_render(self):
        from tempfile import TemporaryDirectory
        profile = json.loads((ROOT / 'reporting/pdf-typed-candidate.v1.json').read_bytes())
        profile['source_sha256']['reporting/typed_pdf_source.py'] = '0' * 64
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'changed.json'
            path.write_bytes(encoded(profile))
            with patch.object(self.m, '_PROFILE_FILE', path), \
                    patch.object(self.m, 'render_pdf_candidate') as render:
                with self.assertRaises(self.m.TypedPdfCandidateError):
                    self.m.prepare_typed_pdf_candidate(**self.inputs())
            render.assert_not_called()

    def test_disable_during_execution_withholds_whole_candidate(self):
        original = self.case()['outputs']['render_pdf_candidate'][0]

        def disable(*args, **kwargs):
            os.environ['OMNIGENIS_TYPED_PDF_CANDIDATE_DISABLED'] = '1'
            return original

        with patch.dict(os.environ):
            self.refused(substitutions={'render_pdf_candidate': disable})

    def test_render_evidence_is_captured_before_later_stage_mutation(self):
        case = self.case()
        rendered = copy.copy(case['outputs']['render_pdf_candidate'][0])
        expected = rendered.result_sha256

        def change_prior(*args, **kwargs):
            object.__setattr__(rendered, 'html_sha256', '0' * 64)
            return case['outputs']['observe_typed_pdf'][0]

        result = self.replay(substitutions={
            'render_pdf_candidate': [rendered], 'observe_typed_pdf': change_prior})
        self.assertEqual(result.to_dict()['render_result_sha256'], expected)

    def test_association_digest_is_captured_before_raster_mutation(self):
        case = self.case()
        associated = copy.copy(case['outputs']['associate_typed_pdf'][0])
        expected = associated.association_sha256

        def change_prior(*args, **kwargs):
            object.__setattr__(associated, 'association_sha256', '0' * 64)
            return case['outputs']['inspect_pdf_raster_window'][0]

        result = self.replay(substitutions={
            'associate_typed_pdf': [associated], 'inspect_pdf_raster_window': change_prior})
        self.assertEqual(result.to_dict()['association_sha256'], expected)

    def test_first_raster_receipt_is_detached_before_next_window(self):
        case = self.case('row')
        first, last = [copy.copy(x) for x in case['outputs']['inspect_pdf_raster_window']]

        def receipts(*args, **kwargs):
            if kwargs['window_index'] == 0:
                return first
            object.__setattr__(first, 'ir_sha256', '0' * 64)
            return last

        result = self.replay(name='row', substitutions={'inspect_pdf_raster_window': receipts})
        self.assertEqual(result.to_dict()['raster_windows'][0]['ir_sha256'],
                         case['inputs']['expected_ir_sha256'])

    def test_typed_html_digest_is_captured_before_render_mutation(self):
        from reporting.typed_html import prepare_typed_html
        case = self.case()
        html, proof = prepare_typed_html(
            presentation_ir=case['inputs']['presentation_ir'],
            expected_ir_sha256=case['inputs']['expected_ir_sha256'])
        expected = proof.html_sha256

        def change_prior(*args, **kwargs):
            object.__setattr__(proof, 'html_sha256', '0' * 64)
            return case['outputs']['render_pdf_candidate'][0]

        with patch.object(self.m, 'prepare_typed_html', return_value=(html, proof)):
            result = self.replay(substitutions={'render_pdf_candidate': change_prior})
        self.assertEqual(result.to_dict()['typed_html_sha256'], expected)


if __name__ == '__main__':
    unittest.main()
