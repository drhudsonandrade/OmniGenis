#!/usr/bin/env python3
"""Deterministic path classification for targeted GitHub Actions jobs."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from pathlib import Path

POLICY_EXACT = {
    "manifests/RULESET_V3.4.sha256",
    "scripts/validate_repo.py",
    "scripts/verify_supply_chain_lock.py",
    "scripts/materialize_ruleset.py",
    "scripts/sealed_ruleset.py",
    "scripts/ci_change_classifier.py",
    "scripts/run_live_post_deployment_smoke.py",
    "scripts/build_stage5_license_gate.py",
    "scripts/validate_stage5_license_gate.py",
    "config/software_license_policy.json",
    "config/software_license_gate_registry.json",
    "config/data_source_registry.yaml",
    "scripts/validate_stage6_data_sources.py",
    "config/data_use_purpose_policy.json",
    "config/data_use_purpose_matrix.json",
    "scripts/data_use_purpose_gate.py",
    "scripts/build_stage7_purpose_matrix.py",
    "scripts/validate_stage7_purpose_use.py",
    ".github/workflows/genoma-policy-engine.yml",
}
POLICY_PREFIXES = (
    "normative/",
    "policy_engine/",
    "locks/",
    "template_store/",
    "adapters/",
    ".github/governance/",
)

REGO_FORCE_EXACT = {
    ".github/workflows/genoma-policy-engine.yml",
    "scripts/ci_change_classifier.py",
}
REGO_PREFIXES = (
    "policy_engine/policy/rego/",
)

POLICY_CONTAINER_EXACT = {
    ".github/workflows/genoma-policy-engine.yml",
    "manifests/RULESET_V3.4.sha256",
    "scripts/ci_change_classifier.py",
    "scripts/materialize_ruleset.py",
    "scripts/sealed_ruleset.py",
}
POLICY_CONTAINER_PREFIXES = (
    "normative/",
    "policy_engine/",
)

CONTAINER_FORCE_EXACT = {
    ".github/workflows/scaffold-validation.yml",
    "scripts/ci_change_classifier.py",
}
CONTAINER_IGNORED_EXACT = {
    ".github/workflows/fallow.yml",
}
CONTAINER_IGNORED_PREFIXES = (
    "docs/",
    "tests/",
)

GOVERNED_MARKDOWN_FORCE_EXACT = {
    "AUTHORS.md",
    "COPYRIGHT.md",
    "CONTRIBUTING.md",
    "docs/compliance/STAGE8_CONTRIBUTION_PROVENANCE.md",
}


def _normalize(path: str) -> str:
    normalized = path.replace("\\", "/")
    return normalized[2:] if normalized.startswith("./") else normalized


def policy_relevant(paths: Iterable[str]) -> bool:
    for raw_path in paths:
        path = _normalize(raw_path)
        if path in POLICY_EXACT or path.startswith(POLICY_PREFIXES):
            return True
    return False


def rego_required(paths: Iterable[str]) -> bool:
    """Return whether OPA/Rego parity can be affected by these paths."""
    for raw_path in paths:
        path = _normalize(raw_path)
        if path in REGO_FORCE_EXACT or path.startswith(REGO_PREFIXES):
            return True
    return False


def policy_container_required(paths: Iterable[str]) -> bool:
    """Return whether the policy container build/runtime can be affected."""
    for raw_path in paths:
        path = _normalize(raw_path)
        if path in POLICY_CONTAINER_EXACT or path.startswith(POLICY_CONTAINER_PREFIXES):
            return True
    return False


def validation_required(changed_paths: Iterable[str], deleted_paths: Iterable[str]) -> bool:
    if any(True for _ in deleted_paths):
        return True
    normalized = [_normalize(path) for path in changed_paths]
    if any(path in GOVERNED_MARKDOWN_FORCE_EXACT for path in normalized):
        return True
    return any(not path.endswith(".md") for path in normalized)


def container_required(changed_paths: Iterable[str]) -> bool:
    for raw_path in changed_paths:
        path = _normalize(raw_path)
        if path in CONTAINER_FORCE_EXACT:
            return True
        if (
            path in CONTAINER_IGNORED_EXACT
            or path.endswith(".md")
            or path.startswith(CONTAINER_IGNORED_PREFIXES)
        ):
            continue
        return True
    return False


def _read_nul_paths(path: Path) -> list[str]:
    payload = path.read_bytes()
    if not payload:
        return []
    if not payload.endswith(b"\0"):
        raise ValueError("NUL-delimited path list is truncated")
    return [item.decode("utf-8") for item in payload[:-1].split(b"\0") if item]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("policy", "rego", "policy-container", "markdown", "container"))
    parser.add_argument("--changed", type=Path, required=True)
    parser.add_argument("--deleted", type=Path)
    args = parser.parse_args()

    changed = _read_nul_paths(args.changed)
    if args.mode == "policy":
        result = policy_relevant(changed)
    elif args.mode == "rego":
        result = rego_required(changed)
    elif args.mode == "policy-container":
        result = policy_container_required(changed)
    elif args.mode == "container":
        result = container_required(changed)
    else:
        if args.deleted is None:
            parser.error("--deleted is required for markdown mode")
        result = validation_required(changed, _read_nul_paths(args.deleted))
    print("true" if result else "false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
