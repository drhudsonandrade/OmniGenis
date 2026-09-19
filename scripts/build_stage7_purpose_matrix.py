#!/usr/bin/env python3
"""Build/check the deterministic Stage 7 resource × purpose decision matrix."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OUTPUT = Path('config/data_use_purpose_matrix.json')

from scripts.data_use_purpose_gate import PURPOSES, _load_json, evaluate_use


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_matrix(root: Path = ROOT) -> dict[str, Any]:
    registry_path = root / 'config/data_source_registry.yaml'
    policy_path = root / 'config/data_use_purpose_policy.json'
    registry = _load_json(registry_path)
    rows = registry.get('resources')
    if not isinstance(rows, list):
        raise ValueError('registry resources must be a list')
    validated_rows: list[dict[str, Any]] = []
    for resource in rows:
        if (
            not isinstance(resource, dict)
            or not isinstance(resource.get('id'), str)
            or not resource['id'].strip()
        ):
            raise ValueError(
                'registry resource must be an object with a non-empty string id'
            )
        validated_rows.append(resource)
    matrix: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for resource in sorted(validated_rows, key=lambda row: row['id']):
        resource_id = resource['id']
        decisions: dict[str, str] = {}
        for purpose in sorted(PURPOSES):
            result = evaluate_use(resource_id, [purpose], root=root)
            decisions[purpose] = result['decision']
            counts[result['decision']] += 1
        matrix.append({'resource_id':resource_id,'status':resource.get('status'),'decisions':decisions})
    return {
        'schema':'omnigenis-data-use-purpose-matrix-v1',
        'stage':7,
        'source_registry_sha256':_sha256(registry_path),
        'purpose_policy_sha256':_sha256(policy_path),
        'resource_count':len(matrix),
        'purpose_count':len(PURPOSES),
        'decision_count':len(matrix)*len(PURPOSES),
        'decision_counts':dict(sorted(counts.items())),
        'matrix':matrix,
    }


def _serialized(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n'


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--output', default=str(OUTPUT))
    args=parser.parse_args()
    output=ROOT / args.output
    expected=_serialized(build_matrix(ROOT))
    if args.check:
        if not output.is_file() or output.read_text(encoding='utf-8') != expected:
            print('FAIL\tstage7_purpose_matrix_drift')
            return 1
        payload=json.loads(expected)
        print(f"PASS\tstage7_purpose_matrix\tresources={payload['resource_count']} purposes={payload['purpose_count']} decisions={payload['decision_count']}")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(expected, encoding='utf-8')
    payload=json.loads(expected)
    print(json.dumps({'output':str(output.relative_to(ROOT)),'decision_counts':payload['decision_counts']},sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
