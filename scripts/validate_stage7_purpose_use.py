#!/usr/bin/env python3
"""Validate Stage 7 purpose-of-use policy, coverage, matrix and fail-closed invariants."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
EXPECTED_PURPOSES = {
    'RESEARCH', 'COMMERCIAL', 'CLINICAL', 'REPORT_GENERATION',
    'MODEL_TRAINING', 'REDISTRIBUTION', 'DERIVED_DATA',
}
EXPECTED_DECISIONS = {
    'ALLOW', 'ALLOW_WITH_OBLIGATIONS', 'REVIEW_REQUIRED',
    'RECORD_LEVEL_REVIEW_REQUIRED', 'DENY',
}

EXPECTED_DECISION_ORDER = {
    'ALLOW': 0,
    'ALLOW_WITH_OBLIGATIONS': 1,
    'REVIEW_REQUIRED': 2,
    'RECORD_LEVEL_REVIEW_REQUIRED': 3,
    'DENY': 4,
}
EXPECTED_STATUS_FLOORS = {
    'DOCUMENTED_OPEN': 'ALLOW',
    'DOCUMENTED_WITH_OBLIGATIONS': 'ALLOW_WITH_OBLIGATIONS',
    'REVIEW_REQUIRED': 'REVIEW_REQUIRED',
    'RESTRICTED': 'REVIEW_REQUIRED',
    'RECORD_LEVEL_TERMS_REQUIRED': 'RECORD_LEVEL_REVIEW_REQUIRED',
}
EXPECTED_PURPOSE_FIELDS = {
    'RESEARCH': (['research_use'], 'ALLOW'),
    'COMMERCIAL': (['commercial_use'], 'ALLOW'),
    'CLINICAL': (['clinical_use'], 'ALLOW'),
    'REPORT_GENERATION': (['derived_data', 'redistribution'], 'ALLOW_WITH_OBLIGATIONS'),
    'MODEL_TRAINING': (['modification', 'derived_data'], 'REVIEW_REQUIRED'),
    'REDISTRIBUTION': (['redistribution'], 'ALLOW'),
    'DERIVED_DATA': (['derived_data'], 'ALLOW'),
}
EXPECTED_ATTRIBUTION_VALUES = [
    'YES', 'REQUESTED', 'YES_SCIENTIFIC_PROVENANCE', 'REVIEW_REQUIRED',
]
EXPECTED_PROTECTED_TOKEN_DECISIONS = {
    'PROHIBITED_OUTSIDE_HPO_CONTRIBUTION_PROCESS': 'DENY',
    'PROHIBITED_WITHOUT_SEPARATE_AGREEMENT': 'DENY',
    'PROHIBITED_WITHOUT_SEPARATE_AGREEMENT_FOR_DIAGNOSTIC_OR_MEDICAL_DECISION_USE': 'DENY',
    'RESTRICTED; third-party content can impose additional conditions': 'DENY',
    'RESTRICTED_BY_TERMS_AND_EMBEDDED_THIRD_PARTY_RIGHTS': 'DENY',
    'RECORD_LEVEL_TERMS_REQUIRED': 'RECORD_LEVEL_REVIEW_REQUIRED',
    'PROFESSIONAL_REVIEW_REQUIRED': 'REVIEW_REQUIRED',
}

from scripts.build_stage7_purpose_matrix import build_matrix
from scripts.data_use_purpose_gate import PurposeUseError, _load_json, evaluate_use


def collect_errors(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    paths={
        'registry':root/'config/data_source_registry.yaml',
        'policy':root/'config/data_use_purpose_policy.json',
        'matrix':root/'config/data_use_purpose_matrix.json',
    }
    for label,path in paths.items():
        if not path.is_file():
            errors.append(f'Stage 7 {label} artifact missing: {path.relative_to(root)}')
    if errors:
        return errors
    try:
        registry=_load_json(paths['registry'])
        policy=_load_json(paths['policy'])
        matrix=_load_json(paths['matrix'])
    except PurposeUseError as exc:
        return [f'Stage 7 artifact unreadable: {exc}']

    if policy.get('schema') != 'omnigenis-data-use-purpose-policy-v1' or policy.get('stage') != 7:
        errors.append('Stage 7 purpose policy identity mismatch')
    raw_purposes = policy.get('purposes')
    if not isinstance(raw_purposes, dict) or set(raw_purposes) != EXPECTED_PURPOSES:
        errors.append('Stage 7 purpose vocabulary mismatch')
    order=policy.get('decision_order')
    if order != EXPECTED_DECISION_ORDER:
        errors.append('Stage 7 decision order invalid or weakened')
    claims=policy.get('claims')
    expected_false={
        'purpose_gate_is_legal_opinion','review_required_means_authorized',
        'record_level_review_required_means_authorized','stage7_replaces_clinical_regulatory_review',
        'stage7_replaces_privacy_consent_review',
    }
    if not isinstance(claims, dict) or any(claims.get(key) is not False for key in expected_false):
        errors.append('Stage 7 policy must reject over-clearance claims')

    resources=registry.get('resources')
    if not isinstance(resources, list):
        errors.append('Stage 7 source registry resources invalid')
        return errors
    raw_token_decisions=policy.get('token_decisions')
    raw_purpose_specs=policy.get('purposes')
    raw_status_floors=policy.get('status_floors')
    if not isinstance(raw_token_decisions, dict):
        errors.append('Stage 7 rights-token mapping structure invalid')
        return errors
    if not isinstance(raw_purpose_specs, dict):
        errors.append('Stage 7 purpose mapping structure invalid')
        return errors
    if not isinstance(raw_status_floors, dict):
        errors.append('Stage 7 status-floor mapping structure invalid')
        return errors
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in raw_token_decisions.items()):
        errors.append('Stage 7 rights-token mapping structure invalid')
        return errors
    if not all(isinstance(key, str) and isinstance(value, dict) for key, value in raw_purpose_specs.items()):
        errors.append('Stage 7 purpose mapping structure invalid')
        return errors
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in raw_status_floors.items()):
        errors.append('Stage 7 status-floor mapping structure invalid')
        return errors
    token_decisions: dict[str, str] = {
        key: value for key, value in raw_token_decisions.items()
        if isinstance(key, str) and isinstance(value, str)
    }
    purpose_specs: dict[str, dict[str, Any]] = {
        key: dict(value) for key, value in raw_purpose_specs.items()
        if isinstance(key, str) and isinstance(value, dict)
    }
    status_floors: dict[str, str] = {
        key: value for key, value in raw_status_floors.items()
        if isinstance(key, str) and isinstance(value, str)
    }
    if status_floors != EXPECTED_STATUS_FLOORS:
        errors.append('Stage 7 status floors invalid or weakened')
    if policy.get('attribution_values_requiring_obligations') != EXPECTED_ATTRIBUTION_VALUES:
        errors.append('Stage 7 attribution-obligation values invalid or weakened')
    for token, expected in EXPECTED_PROTECTED_TOKEN_DECISIONS.items():
        if token_decisions.get(token) != expected:
            errors.append(f'Stage 7 protected token decision weakened: {token}')
    for purpose, (expected_fields, expected_minimum) in EXPECTED_PURPOSE_FIELDS.items():
        spec = purpose_specs.get(purpose)
        if not isinstance(spec, dict) or spec.get('source_fields') != expected_fields or spec.get('minimum_decision') != expected_minimum:
            errors.append(f'Stage 7 protected purpose mapping drift: {purpose}')

    referenced_fields: set[str] = set()
    for purpose,spec in purpose_specs.items():
        fields=spec.get('source_fields') if isinstance(spec,dict) else None
        if not isinstance(fields,list) or not fields:
            errors.append(f'Stage 7 purpose source fields invalid: {purpose}')
            continue
        referenced_fields.update(str(field) for field in fields)
        minimum=spec.get('minimum_decision')
        if minimum not in EXPECTED_DECISIONS:
            errors.append(f'Stage 7 purpose minimum decision invalid: {purpose}')
    for resource in resources:
        if not isinstance(resource,dict):
            errors.append('Stage 7 source registry contains non-object resource')
            continue
        resource_id=str(resource.get('id'))
        status_value = resource.get('status')
        status = status_value if isinstance(status_value, str) else ''
        if not status or status not in status_floors:
            errors.append(f'Stage 7 unmapped source status: {resource_id}={status_value}')
        for field in referenced_fields:
            token=str(resource.get(field) or '').strip()
            if not token:
                errors.append(f'Stage 7 missing source rights token: {resource_id}.{field}')
            elif token_decisions.get(token) is None:
                errors.append(f'Stage 7 unmapped source rights token: {resource_id}.{field}={token}')

    try:
        rebuilt=build_matrix(root)
    except (ValueError, OSError) as exc:
        errors.append(f'Stage 7 matrix rebuild failed: {type(exc).__name__}: {exc}')
    else:
        if matrix != rebuilt:
            errors.append('Stage 7 purpose matrix drift')
        if rebuilt.get('resource_count') != 19 or rebuilt.get('purpose_count') != 7 or rebuilt.get('decision_count') != 133:
            errors.append('Stage 7 purpose matrix coverage mismatch')

    for resource in resources:
        if not isinstance(resource,dict) or not isinstance(resource.get('id'),str):
            continue
        resource_id=resource['id']
        status_value = resource.get('status')
        status = status_value if isinstance(status_value, str) else ''
        for purpose in EXPECTED_PURPOSES:
            try:
                result=evaluate_use(resource_id,[purpose],root=root)
            except PurposeUseError as exc:
                errors.append(f'Stage 7 evaluation failed: {resource_id}/{purpose}: {exc}')
                continue
            decision=result.get('decision')
            if decision not in EXPECTED_DECISIONS:
                errors.append(f'Stage 7 invalid evaluation decision: {resource_id}/{purpose}')
            if status == 'REVIEW_REQUIRED' and decision in {'ALLOW','ALLOW_WITH_OBLIGATIONS'}:
                errors.append(f'Stage 7 REVIEW_REQUIRED source auto-authorized: {resource_id}/{purpose}')
            if purpose == 'MODEL_TRAINING' and decision in {'ALLOW','ALLOW_WITH_OBLIGATIONS'}:
                errors.append(f'Stage 7 model training auto-authorized: {resource_id}')
            if status == 'RECORD_LEVEL_TERMS_REQUIRED' and decision != 'RECORD_LEVEL_REVIEW_REQUIRED':
                errors.append(f'Stage 7 record-level source flattened: {resource_id}/{purpose}')

    try:
        panel=evaluate_use('panelapp-genomics-england',['COMMERCIAL'],root=root)
        panel_clinical=evaluate_use('panelapp-genomics-england',['CLINICAL'],root=root)
    except PurposeUseError as exc:
        errors.append(f'Stage 7 protected PanelApp evaluation failed: {exc}')
    else:
        if panel['decision'] != 'DENY':
            errors.append('Stage 7 PanelApp commercial use must DENY')
        if panel_clinical['decision'] != 'DENY':
            errors.append('Stage 7 PanelApp clinical use must DENY')
    return errors


def main() -> int:
    errors=collect_errors(ROOT)
    if errors:
        for error in errors:
            print(f'FAIL\t{error}')
        return 1
    matrix=_load_json(ROOT/'config/data_use_purpose_matrix.json')
    counts=matrix['decision_counts']
    print('PASS\tstage7_purpose_use\tresources=19 purposes=7 decisions=133 '+ ' '.join(f'{k.lower()}={v}' for k,v in sorted(counts.items())))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
