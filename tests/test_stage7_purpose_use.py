from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Stage7PurposeUseTest(unittest.TestCase):
    @staticmethod
    def _copy_contract_root(destination: Path) -> None:
        for relative in (
            'config/data_source_registry.yaml',
            'config/data_use_purpose_policy.json',
            'config/data_use_purpose_matrix.json',
            'docs/evidence/PGS_CATALOG_REGISTRY.json.gz',
        ):
            source = ROOT / relative
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )

    def test_canonical_policy_and_gate_exist(self) -> None:
        self.assertTrue((ROOT / 'config/data_use_purpose_policy.json').is_file())
        self.assertTrue((ROOT / 'config/data_use_purpose_matrix.json').is_file())
        self.assertTrue((ROOT / 'scripts/data_use_purpose_gate.py').is_file())
        self.assertTrue((ROOT / 'scripts/build_stage7_purpose_matrix.py').is_file())
        self.assertTrue((ROOT / 'scripts/validate_stage7_purpose_use.py').is_file())

    def test_gate_module_contract(self) -> None:
        from scripts.data_use_purpose_gate import PURPOSES, evaluate_use
        self.assertEqual(PURPOSES, {
            'RESEARCH', 'COMMERCIAL', 'CLINICAL', 'REPORT_GENERATION',
            'MODEL_TRAINING', 'REDISTRIBUTION', 'DERIVED_DATA',
        })
        result = evaluate_use('clingen-gene-disease-validity', ['RESEARCH'], root=ROOT)
        self.assertIn(result['decision'], {'ALLOW', 'ALLOW_WITH_OBLIGATIONS'})

    def test_multiple_purposes_take_strictest_decision(self) -> None:
        from scripts.data_use_purpose_gate import evaluate_use
        result = evaluate_use('panelapp-genomics-england', ['RESEARCH', 'COMMERCIAL'], root=ROOT)
        self.assertEqual(result['decision'], 'DENY')

    def test_review_required_source_never_auto_allows(self) -> None:
        from scripts.data_use_purpose_gate import evaluate_use
        result = evaluate_use('gnomad', ['RESEARCH'], root=ROOT)
        self.assertEqual(result['decision'], 'REVIEW_REQUIRED')

    def test_model_training_is_fail_closed_by_default(self) -> None:
        from scripts.data_use_purpose_gate import evaluate_use
        result = evaluate_use('clingen-gene-disease-validity', ['MODEL_TRAINING'], root=ROOT)
        self.assertEqual(result['decision'], 'REVIEW_REQUIRED')

    def test_pgs_never_flattens_record_level_terms(self) -> None:
        from scripts.data_use_purpose_gate import evaluate_use
        result = evaluate_use('ebi-pgs-catalog', ['RESEARCH'], root=ROOT, record_id='PGS000001')
        self.assertEqual(result['decision'], 'RECORD_LEVEL_REVIEW_REQUIRED')
        self.assertEqual(result['record_id'], 'PGS000001')
        self.assertTrue(result['record_terms']['license'])

    def test_pgs_missing_record_id_fails_closed(self) -> None:
        from scripts.data_use_purpose_gate import evaluate_use
        result = evaluate_use('ebi-pgs-catalog', ['RESEARCH'], root=ROOT)
        self.assertEqual(result['decision'], 'RECORD_LEVEL_REVIEW_REQUIRED')
        self.assertIn('record_id', ' '.join(result['blockers']))

    def test_unknown_purpose_is_rejected(self) -> None:
        from scripts.data_use_purpose_gate import PurposeUseError, evaluate_use
        with self.assertRaises(PurposeUseError):
            evaluate_use('clingen-gene-disease-validity', ['INVENTED'], root=ROOT)

    def test_unknown_resource_is_rejected(self) -> None:
        from scripts.data_use_purpose_gate import PurposeUseError, evaluate_use
        with self.assertRaises(PurposeUseError):
            evaluate_use('invented-resource', ['RESEARCH'], root=ROOT)

    def test_cli_blocks_review_required_decision(self) -> None:
        proc = subprocess.run(
            [sys.executable, 'scripts/data_use_purpose_gate.py', '--resource', 'gnomad', '--purpose', 'RESEARCH'],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(proc.returncode, 2)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload['decision'], 'REVIEW_REQUIRED')

    def test_cli_denied_decision_uses_distinct_exit_code(self) -> None:
        proc = subprocess.run(
            [sys.executable, 'scripts/data_use_purpose_gate.py', '--resource', 'panelapp-genomics-england', '--purpose', 'COMMERCIAL'],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(json.loads(proc.stdout)['decision'], 'DENY')

    def test_clinical_use_never_auto_clears_professional_review(self) -> None:
        from scripts.data_use_purpose_gate import evaluate_use
        result = evaluate_use('clingen-gene-disease-validity', ['CLINICAL'], root=ROOT)
        self.assertEqual(result['decision'], 'REVIEW_REQUIRED')

    def test_report_generation_combines_derived_and_redistribution_rights(self) -> None:
        from scripts.data_use_purpose_gate import evaluate_use
        result = evaluate_use('cpic', ['REPORT_GENERATION'], root=ROOT)
        self.assertEqual(result['decision'], 'REVIEW_REQUIRED')
        tokens = result['per_purpose'][0]['source_tokens']
        self.assertEqual(set(tokens), {'derived_data', 'redistribution'})

    def test_purpose_composition_is_order_independent(self) -> None:
        from scripts.data_use_purpose_gate import evaluate_use
        a = evaluate_use('panelapp-genomics-england', ['RESEARCH', 'COMMERCIAL'], root=ROOT)
        b = evaluate_use('panelapp-genomics-england', ['COMMERCIAL', 'RESEARCH'], root=ROOT)
        self.assertEqual(a['decision'], b['decision'])

    def test_unknown_pgs_record_is_rejected(self) -> None:
        from scripts.data_use_purpose_gate import PurposeUseError, evaluate_use
        with self.assertRaisesRegex(PurposeUseError, 'PGS record not found'):
            evaluate_use('ebi-pgs-catalog', ['RESEARCH'], root=ROOT, record_id='PGS999999')

    def test_materialized_matrix_has_full_cartesian_coverage(self) -> None:
        matrix = json.loads((ROOT / 'config/data_use_purpose_matrix.json').read_text(encoding='utf-8'))
        self.assertEqual(matrix['resource_count'], 19)
        self.assertEqual(matrix['purpose_count'], 7)
        self.assertEqual(matrix['decision_count'], 133)
        self.assertEqual(sum(matrix['decision_counts'].values()), 133)
        self.assertNotIn('ALLOW', matrix['decision_counts'])

    def test_builder_check_passes_for_committed_matrix(self) -> None:
        proc = subprocess.run(
            [sys.executable, 'scripts/build_stage7_purpose_matrix.py', '--check'],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('PASS\tstage7_purpose_matrix', proc.stdout)

    def test_malformed_registry_resource_becomes_validation_error(self) -> None:
        from scripts.validate_stage7_purpose_use import collect_errors
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_contract_root(root)
            registry_path = root / 'config/data_source_registry.yaml'
            registry = json.loads(registry_path.read_text(encoding='utf-8'))
            registry['resources'][0] = None
            self._write_json(registry_path, registry)
            errors = collect_errors(root)
        self.assertTrue(
            any('matrix rebuild failed: ValueError' in error for error in errors),
            errors,
        )

    def test_null_purpose_map_is_reported_not_raised(self) -> None:
        from scripts.validate_stage7_purpose_use import collect_errors
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_contract_root(root)
            policy_path = root / 'config/data_use_purpose_policy.json'
            policy = json.loads(policy_path.read_text(encoding='utf-8'))
            policy['purposes'] = None
            self._write_json(policy_path, policy)
            errors = collect_errors(root)
        self.assertIn('Stage 7 purpose vocabulary mismatch', errors)

    def test_attribution_obligation_list_is_protected_and_nonempty(self) -> None:
        from scripts.validate_stage7_purpose_use import collect_errors
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_contract_root(root)
            policy_path = root / 'config/data_use_purpose_policy.json'
            policy = json.loads(policy_path.read_text(encoding='utf-8'))
            policy['attribution_values_requiring_obligations'] = []
            self._write_json(policy_path, policy)
            errors = collect_errors(root)
        self.assertTrue(
            any('attribution-obligation values invalid or weakened' in error for error in errors),
            errors,
        )

    def test_protected_token_decision_cannot_be_weakened_even_with_regenerated_matrix(self) -> None:
        from scripts.build_stage7_purpose_matrix import build_matrix
        from scripts.validate_stage7_purpose_use import collect_errors
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_contract_root(root)
            policy_path = root / 'config/data_use_purpose_policy.json'
            policy = json.loads(policy_path.read_text(encoding='utf-8'))
            policy['token_decisions']['PROFESSIONAL_REVIEW_REQUIRED'] = 'ALLOW_WITH_OBLIGATIONS'
            self._write_json(policy_path, policy)
            self._write_json(root / 'config/data_use_purpose_matrix.json', build_matrix(root))
            errors = collect_errors(root)
        self.assertIn(
            'Stage 7 protected token decision weakened: PROFESSIONAL_REVIEW_REQUIRED',
            errors,
        )


if __name__ == '__main__':
    unittest.main()
