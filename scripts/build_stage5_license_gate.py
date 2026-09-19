#!/usr/bin/env python3
"""Build the deterministic Stage 5 software-license enforcement view."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = "config/software_license_policy.json"
SOURCE_REGISTRY_PATH = "config/third_party_software_registry.json"
GATE_REGISTRY_PATH = "config/software_license_gate_registry.json"
DEBT_BASELINE_PATH = "locks/stage5-license-debt-baseline.json"

FINGERPRINT_FIELDS = (
    "id",
    "ecosystem",
    "name",
    "version",
    "scope",
    "relationship",
    "source",
    "purl",
    "sha256",
    "sha",
    "integrity",
    "licenses",
)


class LicenseGateBuildError(ValueError):
    """Raised when Stage 5 policy inputs are internally inconsistent."""


def _load(root: Path, relative: str) -> dict[str, Any]:
    return json.loads((root / relative).read_text(encoding="utf-8"))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_bytes(payload: object) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def component_fingerprint(component: dict[str, Any]) -> str:
    """Hash the exact dependency identity and license-bearing fields used by the gate."""
    identity = {
        field: component[field]
        for field in FINGERPRINT_FIELDS
        if field in component
    }
    return _sha256_bytes(_canonical_bytes(identity))


def _reviewed_exact_artifacts(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = policy.get("reviewed_exact_artifacts")
    if not isinstance(records, list):
        raise LicenseGateBuildError("reviewed_exact_artifacts must be a list")
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("component_id"), str):
            raise LicenseGateBuildError("reviewed exact artifact record is invalid")
        component_id = record["component_id"]
        if component_id in result:
            raise LicenseGateBuildError(f"duplicate reviewed exact artifact: {component_id}")
        result[component_id] = record
    return result


def _is_unknown_license(value: str, policy: dict[str, Any]) -> bool:
    text = value.strip()
    upper = text.upper()
    markers = {str(item).upper() for item in policy.get("unknown_license_markers", [])}
    prefixes = tuple(str(item).lower() for item in policy.get("unknown_license_prefixes", []))
    return not text or upper in markers or text.lower().startswith(prefixes)


def gate_status(component: dict[str, Any], policy: dict[str, Any]) -> str:
    """Map one Stage 4 component into the closed Stage 5 policy vocabulary."""
    stage4_status = component.get("policy_status")
    licenses = component.get("licenses")
    if (
        not isinstance(stage4_status, str)
        or not isinstance(licenses, list)
        or not licenses
        or not all(isinstance(item, str) for item in licenses)
    ):
        return "UNKNOWN"

    dispositions = _reviewed_exact_artifacts(policy)
    component_id = str(component.get("id", ""))
    if stage4_status == "REVIEWED_ACCEPTED_EXACT_ARTIFACT":
        disposition = dispositions.get(component_id)
        if disposition is None:
            return "UNKNOWN"
        if disposition.get("required_stage4_status") != stage4_status:
            return "UNKNOWN"
        required_evidence = disposition.get("required_evidence")
        evidence = component.get("evidence")
        if (
            not isinstance(required_evidence, str)
            or not isinstance(evidence, list)
            or required_evidence not in evidence
        ):
            return "UNKNOWN"
        return "APPROVED_WITH_NOTICE"

    joined = " ".join(licenses).upper()
    restricted_markers = tuple(str(item).upper() for item in policy.get("restricted_markers", []))
    if any(marker and marker in joined for marker in restricted_markers):
        return "RESTRICTED"

    if stage4_status == "BLOCKED_BY_DEFAULT":
        return "BLOCKED"

    if any(_is_unknown_license(item, policy) for item in licenses):
        return "UNKNOWN"

    mapping = policy.get("stage4_status_mapping")
    if not isinstance(mapping, dict):
        raise LicenseGateBuildError("stage4_status_mapping must be an object")
    mapped = mapping.get(stage4_status)
    vocabulary = policy.get("status_vocabulary")
    if not isinstance(mapped, str) or not isinstance(vocabulary, list) or mapped not in vocabulary:
        return "UNKNOWN"
    return mapped


def build_gate_registry(root: Path = ROOT) -> dict[str, Any]:
    """Return the deterministic Stage 5 status projection over the Stage 4 registry."""
    policy = _load(root, POLICY_PATH)
    source = _load(root, SOURCE_REGISTRY_PATH)
    components = source.get("components")
    if not isinstance(components, list):
        raise LicenseGateBuildError("Stage 4 component registry is invalid")

    allowed = policy.get("new_dependency_allowed_statuses")
    vocabulary = policy.get("status_vocabulary")
    if not isinstance(allowed, list) or not isinstance(vocabulary, list):
        raise LicenseGateBuildError("Stage 5 status policy is invalid")
    allowed_set = set(allowed)
    vocabulary_set = set(vocabulary)

    output: list[dict[str, Any]] = []
    for component in components:
        if not isinstance(component, dict) or not isinstance(component.get("id"), str):
            raise LicenseGateBuildError("Stage 4 component entry is invalid")
        status = gate_status(component, policy)
        if status not in vocabulary_set:
            raise LicenseGateBuildError(f"unknown Stage 5 status: {status}")
        output.append(
            {
                "id": component["id"],
                "ecosystem": component.get("ecosystem"),
                "name": component.get("name"),
                "version": component.get("version"),
                "scope": component.get("scope"),
                "licenses": component.get("licenses"),
                "stage4_policy_status": component.get("policy_status"),
                "component_fingerprint": component_fingerprint(component),
                "license_gate_status": status,
                "allowed_for_new_dependency": status in allowed_set,
            }
        )

    output.sort(key=lambda item: item["id"])
    counts = Counter(item["license_gate_status"] for item in output)
    return {
        "schema": "omnigenis-software-license-gate-registry-v1",
        "policy_sha256": _sha256_path(root / POLICY_PATH),
        "source_registry_sha256": _sha256_path(root / SOURCE_REGISTRY_PATH),
        "components": output,
        "summary": {
            "component_records": len(output),
            "by_license_gate_status": dict(sorted(counts.items())),
            "new_dependency_allowed_statuses": sorted(allowed_set),
            "current_non_approved_records": sum(
                count for status, count in counts.items() if status not in allowed_set
            ),
            "license_clean_claim_allowed": False,
        },
    }


def build_debt_baseline(
    root: Path,
    *,
    base_main_sha: str,
    base_main_tree: str,
) -> dict[str, Any]:
    """Freeze current non-approved components as non-authorizing historical debt."""
    registry = build_gate_registry(root)
    policy = _load(root, POLICY_PATH)
    allowed = set(policy["new_dependency_allowed_statuses"])
    entries = [
        {
            "id": item["id"],
            "component_fingerprint": item["component_fingerprint"],
            "license_gate_status": item["license_gate_status"],
            "ecosystem": item["ecosystem"],
            "scope": item["scope"],
        }
        for item in registry["components"]
        if item["license_gate_status"] not in allowed
    ]
    entries.sort(key=lambda item: item["id"])
    return {
        "schema": "omnigenis-stage5-license-debt-baseline-v1",
        "purpose": "non-authorizing historical debt ceiling; entries are not approvals",
        "base_main_sha": base_main_sha,
        "base_main_tree": base_main_tree,
        "source_registry_sha256": registry["source_registry_sha256"],
        "entry_count": len(entries),
        "entries": entries,
    }


def render_gate_registry(root: Path = ROOT) -> str:
    return _canonical_bytes(build_gate_registry(root)).decode("utf-8")


def render_debt_baseline(root: Path, *, base_main_sha: str, base_main_tree: str) -> str:
    return _canonical_bytes(
        build_debt_baseline(root, base_main_sha=base_main_sha, base_main_tree=base_main_tree)
    ).decode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write-registry", action="store_true")
    parser.add_argument("--bootstrap-baseline", action="store_true")
    parser.add_argument("--base-sha")
    parser.add_argument("--base-tree")
    args = parser.parse_args()

    rendered = render_gate_registry(ROOT)
    registry_path = ROOT / GATE_REGISTRY_PATH
    if args.write_registry:
        registry_path.write_text(rendered, encoding="utf-8")
    if args.check and (
        not registry_path.is_file()
        or registry_path.read_text(encoding="utf-8") != rendered
    ):
        raise SystemExit("Stage 5 license-gate registry drift")

    if args.bootstrap_baseline:
        if not args.base_sha or not args.base_tree:
            raise SystemExit("--bootstrap-baseline requires --base-sha and --base-tree")
        baseline_path = ROOT / DEBT_BASELINE_PATH
        if baseline_path.exists():
            raise SystemExit("Stage 5 debt baseline already exists; bootstrap is one-time only")
        baseline_path.write_text(
            render_debt_baseline(ROOT, base_main_sha=args.base_sha, base_main_tree=args.base_tree),
            encoding="utf-8",
        )

    payload = json.loads(rendered)
    summary = payload["summary"]
    print(
        "PASS\tstage5_license_gate_registry\t"
        f"components={summary['component_records']} "
        f"non_approved={summary['current_non_approved_records']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
