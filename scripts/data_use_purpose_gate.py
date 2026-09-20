#!/usr/bin/env python3
"""Fail-closed Stage 7 purpose-of-use authorization gate for scientific resources."""
from __future__ import annotations

import argparse
import gzip
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = Path('config/data_use_purpose_policy.json')
REGISTRY_PATH = Path('config/data_source_registry.yaml')
PGS_ID = re.compile(r'^PGS\d{6}$')

PURPOSES = {
    'RESEARCH', 'COMMERCIAL', 'CLINICAL', 'REPORT_GENERATION',
    'MODEL_TRAINING', 'REDISTRIBUTION', 'DERIVED_DATA',
}


class PurposeUseError(ValueError):
    """Raised for malformed or unknown Stage 7 authorization requests."""


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PurposeUseError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_reject_duplicate_pairs)
    except (OSError, json.JSONDecodeError) as exc:
        raise PurposeUseError(f'unreadable JSON: {path}: {type(exc).__name__}: {exc}') from exc
    if not isinstance(payload, dict):
        raise PurposeUseError(f'JSON object required: {path}')
    return payload


def _resolve_repo_path(root: Path, relative: str) -> Path:
    base = root.resolve()
    candidate = (base / relative).resolve()
    if not candidate.is_relative_to(base):
        raise PurposeUseError(f'path escapes repository root: {relative}')
    return candidate


def _strictest(decisions: list[str], order: dict[str, int]) -> str:
    if not decisions:
        raise PurposeUseError('no decisions to aggregate')
    try:
        return max(decisions, key=lambda item: order[item])
    except KeyError as exc:
        raise PurposeUseError(f'unknown decision: {exc.args[0]}') from exc


def _require_str_int_map(value: Any, *, name: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise PurposeUseError(f'{name} is invalid')
    result: dict[str, int] = {}
    for key, item in value.items():
        if (
            not isinstance(key, str)
            or not isinstance(item, int)
            or isinstance(item, bool)
        ):
            raise PurposeUseError(f'{name} is invalid')
        result[key] = item
    return result


def _require_str_str_map(value: Any, *, name: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise PurposeUseError(f'{name} is invalid')
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise PurposeUseError(f'{name} is invalid')
        result[key] = item
    return result


def _require_purpose_specs(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        raise PurposeUseError('Stage 7 purpose map is invalid')
    result: dict[str, dict[str, Any]] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, dict):
            raise PurposeUseError('Stage 7 purpose map is invalid')
        result[key] = dict(item)
    return result


def _registry_by_id(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = registry.get('resources')
    if not isinstance(rows, list):
        raise PurposeUseError('scientific source registry resources must be a list')
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str):
            raise PurposeUseError('invalid scientific source registry resource')
        resource_id = row['id']
        if resource_id in result:
            raise PurposeUseError(f'duplicate scientific resource id: {resource_id}')
        result[resource_id] = row
    return result


def _pgs_record_terms(root: Path, resource: dict[str, Any], record_id: str | None) -> tuple[dict[str, Any] | None, list[str]]:
    blockers: list[str] = []
    if not record_id:
        return None, ['record_id is required for record-level PGS terms']
    if not PGS_ID.fullmatch(record_id):
        raise PurposeUseError(f'invalid PGS record id: {record_id}')
    record_level = resource.get('record_level_terms')
    if not isinstance(record_level, dict) or not isinstance(record_level.get('artifact'), str):
        raise PurposeUseError('PGS record-level artifact is not configured')
    path = _resolve_repo_path(root, record_level['artifact'])
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as handle:
            payload = json.load(handle, object_pairs_hook=_reject_duplicate_pairs)
    except (OSError, json.JSONDecodeError) as exc:
        raise PurposeUseError(f'unreadable PGS record-level artifact: {type(exc).__name__}: {exc}') from exc
    scores = payload.get('scores') if isinstance(payload, dict) else None
    if not isinstance(scores, dict) or record_id not in scores or not isinstance(scores[record_id], dict):
        raise PurposeUseError(f'PGS record not found: {record_id}')
    record = scores[record_id]
    license_text = str(record.get('license') or '').strip()
    if not license_text:
        raise PurposeUseError(f'PGS record has no license terms: {record_id}')
    blockers.append('record-level PGS terms require explicit review before authorization')
    return {
        'license': license_text,
        'license_is_restrictive': record.get('license_is_restrictive') is True,
        'release_date': record.get('release_date'),
        'publication': record.get('publication'),
    }, blockers


def evaluate_use(
    resource_id: str,
    purposes: list[str] | tuple[str, ...],
    *,
    root: Path = ROOT,
    record_id: str | None = None,
) -> dict[str, Any]:
    """Evaluate one resource against one or more simultaneous downstream purposes."""
    if not isinstance(resource_id, str) or not resource_id.strip():
        raise PurposeUseError('resource id is required')
    if not purposes:
        raise PurposeUseError('at least one purpose is required')
    normalized: list[str] = []
    for raw in purposes:
        purpose = str(raw).strip().upper()
        if purpose not in PURPOSES:
            raise PurposeUseError(f'unknown purpose: {raw}')
        if purpose not in normalized:
            normalized.append(purpose)

    policy = _load_json(_resolve_repo_path(root, str(POLICY_PATH)))
    registry = _load_json(_resolve_repo_path(root, str(REGISTRY_PATH)))
    resources = _registry_by_id(registry)
    if resource_id not in resources:
        raise PurposeUseError(f'unknown resource: {resource_id}')
    resource = resources[resource_id]

    order: dict[str, int] = _require_str_int_map(
        policy.get('decision_order'), name='Stage 7 decision order'
    )
    purpose_specs: dict[str, dict[str, Any]] = _require_purpose_specs(policy.get('purposes'))
    token_decisions: dict[str, str] = _require_str_str_map(
        policy.get('token_decisions'), name='Stage 7 rights-token map'
    )
    status_floors: dict[str, str] = _require_str_str_map(
        policy.get('status_floors'), name='Stage 7 status-floor map'
    )

    raw_attribution_values = policy.get('attribution_values_requiring_obligations')
    if (
        not isinstance(raw_attribution_values, list)
        or not raw_attribution_values
        or not all(isinstance(value, str) and value for value in raw_attribution_values)
    ):
        raise PurposeUseError('Stage 7 attribution-obligation values are invalid')
    attribution_values: list[str] = [value for value in raw_attribution_values if isinstance(value, str)]

    status = str(resource.get('status') or '')
    status_floor = status_floors.get(status)
    if not isinstance(status_floor, str):
        raise PurposeUseError(f'unmapped resource status: {status}')

    per_purpose: list[dict[str, Any]] = []
    purpose_decisions: list[str] = []
    blockers: list[str] = []
    obligations: list[str] = []
    record_terms: dict[str, Any] | None = None

    if status == 'RECORD_LEVEL_TERMS_REQUIRED':
        record_terms, record_blockers = _pgs_record_terms(root, resource, record_id)
        blockers.extend(record_blockers)

    for purpose in normalized:
        spec = purpose_specs.get(purpose)
        if not isinstance(spec, dict):
            raise PurposeUseError(f'purpose policy missing: {purpose}')
        fields = spec.get('source_fields')
        if not isinstance(fields, list) or not fields:
            raise PurposeUseError(f'purpose source fields missing: {purpose}')
        source_tokens: dict[str, str] = {}
        field_decisions: list[str] = []
        for field in fields:
            if not isinstance(field, str):
                raise PurposeUseError(f'invalid source field in purpose: {purpose}')
            token = str(resource.get(field) or '').strip()
            if not token:
                raise PurposeUseError(f'missing rights token: {resource_id}.{field}')
            decision = token_decisions.get(token)
            if not isinstance(decision, str):
                raise PurposeUseError(f'unmapped rights token: {resource_id}.{field}={token}')
            source_tokens[field] = token
            field_decisions.append(decision)

        minimum = spec.get('minimum_decision')
        if not isinstance(minimum, str):
            raise PurposeUseError(f'purpose minimum decision missing: {purpose}')
        decision = _strictest(field_decisions + [minimum, status_floor], order)

        attribution_required = resource.get('attribution_required')
        if (
            decision == 'ALLOW'
            and isinstance(attribution_required, str)
            and attribution_required in attribution_values
        ):
            decision = 'ALLOW_WITH_OBLIGATIONS'

        if decision in {'REVIEW_REQUIRED', 'RECORD_LEVEL_REVIEW_REQUIRED', 'DENY'}:
            blockers.append(f'{resource_id}:{purpose}:{decision}')
        purpose_decisions.append(decision)
        per_purpose.append({
            'purpose': purpose,
            'decision': decision,
            'source_tokens': source_tokens,
            'definition': spec.get('definition'),
        })

    overall = _strictest(purpose_decisions, order)
    citation = str(resource.get('required_citation') or '').strip()
    if citation:
        obligations.append(citation)
    attribution = str(resource.get('attribution_required') or '').strip()
    if attribution:
        obligations.append(f'attribution_required={attribution}')
    if overall in {'ALLOW_WITH_OBLIGATIONS', 'REVIEW_REQUIRED', 'RECORD_LEVEL_REVIEW_REQUIRED'}:
        obligations.append(f'license={resource.get("license")}')
        obligations.append(f'terms={resource.get("terms_url")}')
    obligations = list(dict.fromkeys(obligations))
    blockers = list(dict.fromkeys(blockers))

    return {
        'schema':'omnigenis-data-use-purpose-decision-v1',
        'resource_id': resource_id,
        'provider': resource.get('provider'),
        'resource_status': status,
        'purposes': normalized,
        'decision': overall,
        'authorized': overall in {'ALLOW', 'ALLOW_WITH_OBLIGATIONS'},
        'per_purpose': per_purpose,
        'obligations': obligations,
        'blockers': blockers,
        'record_id': record_id,
        'record_terms': record_terms,
        'limitations': resource.get('limitations'),
        'claims': {
            'legal_opinion': False,
            'clinical_regulatory_clearance': False,
            'privacy_consent_clearance': False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resource', required=True)
    parser.add_argument('--purpose', action='append', required=True, choices=sorted(PURPOSES))
    parser.add_argument('--record-id')
    args = parser.parse_args()
    try:
        result = evaluate_use(args.resource, args.purpose, root=ROOT, record_id=args.record_id)
    except PurposeUseError as exc:
        print(json.dumps({'schema':'omnigenis-data-use-purpose-error-v1','error':str(exc)}, ensure_ascii=False, indent=2))
        return 4
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return {
        'ALLOW':0,
        'ALLOW_WITH_OBLIGATIONS':0,
        'REVIEW_REQUIRED':2,
        'RECORD_LEVEL_REVIEW_REQUIRED':2,
        'DENY':3,
    }[result['decision']]


if __name__ == '__main__':
    raise SystemExit(main())
