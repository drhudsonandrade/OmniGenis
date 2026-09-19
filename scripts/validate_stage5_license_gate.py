#!/usr/bin/env python3
"""Fail-closed Stage 5 software-license gate for repository and PR enforcement."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_stage5_license_gate import (  # noqa: E402
    DEBT_BASELINE_PATH,
    GATE_REGISTRY_PATH,
    POLICY_PATH,
    render_gate_registry,
)

SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_VOCABULARY = [
    "APPROVED",
    "APPROVED_WITH_NOTICE",
    "REVIEW_REQUIRED",
    "RESTRICTED",
    "BLOCKED",
    "UNKNOWN",
]
EXPECTED_ALLOWED = {"APPROVED", "APPROVED_WITH_NOTICE"}
EXPECTED_DEBT = {"REVIEW_REQUIRED", "RESTRICTED", "BLOCKED", "UNKNOWN"}
REQUIRED = (
    POLICY_PATH,
    GATE_REGISTRY_PATH,
    DEBT_BASELINE_PATH,
    "config/third_party_software_registry.json",
)


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_executable() -> str:
    executable = shutil.which("git")
    if not executable or not Path(executable).is_absolute():
        raise RuntimeError("trusted git executable is unavailable")
    return executable


def _git_commit_exists(root: Path, sha: str) -> bool:
    completed = subprocess.run(
        [_git_executable(), "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def _git_tree(root: Path, sha: str) -> str | None:
    completed = subprocess.run(
        [_git_executable(), "rev-parse", f"{sha}^{{tree}}"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _git_blob(root: Path, sha: str, relative: str) -> bytes | None:
    completed = subprocess.run(
        [_git_executable(), "show", f"{sha}:{relative}"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout


def _validate_policy(policy: dict[str, Any], errors: list[str]) -> None:
    if policy.get("schema") != "omnigenis-software-license-policy-v1":
        errors.append("Stage 5 software license policy schema mismatch")
    if policy.get("status_vocabulary") != EXPECTED_VOCABULARY:
        errors.append("Stage 5 license status vocabulary mismatch")
    if set(policy.get("new_dependency_allowed_statuses", [])) != EXPECTED_ALLOWED:
        errors.append("Stage 5 auto-allowed statuses must be APPROVED/APPROVED_WITH_NOTICE only")
    if set(policy.get("debt_statuses", [])) != EXPECTED_DEBT:
        errors.append("Stage 5 debt status vocabulary mismatch")
    semantics = policy.get("semantics")
    if not isinstance(semantics, dict):
        errors.append("Stage 5 policy semantics missing")
    else:
        if semantics.get("unknown_is_approved") is not False:
            errors.append("Stage 5 UNKNOWN must never be approved")
        if semantics.get("baseline_entries_are_approvals") is not False:
            errors.append("Stage 5 debt baseline must remain non-authorizing")
        if semantics.get("policy_states_are_legal_opinions") is not False:
            errors.append("Stage 5 engineering statuses must not claim legal-opinion authority")
        if semantics.get("new_non_approved_dependency_must_fail") is not True:
            errors.append("Stage 5 must fail new non-approved dependencies")


def _validate_baseline(
    root: Path,
    policy: dict[str, Any],
    registry: dict[str, Any],
    baseline: dict[str, Any],
    errors: list[str],
) -> None:
    if baseline.get("schema") != "omnigenis-stage5-license-debt-baseline-v1":
        errors.append("Stage 5 debt baseline schema mismatch")
    frozen_hash = policy.get("frozen_debt_baseline_sha256")
    if not isinstance(frozen_hash, str) or not SHA256.fullmatch(frozen_hash):
        errors.append("Stage 5 frozen debt baseline digest is invalid")
    elif _sha256(root / DEBT_BASELINE_PATH) != frozen_hash:
        errors.append("Stage 5 frozen debt baseline digest mismatch")

    bootstrap = policy.get("bootstrap")
    if not isinstance(bootstrap, dict):
        errors.append("Stage 5 bootstrap identity missing")
    else:
        if baseline.get("base_main_sha") != bootstrap.get("base_main_sha"):
            errors.append("Stage 5 baseline is not bound to the approved bootstrap main SHA")
        if baseline.get("base_main_tree") != bootstrap.get("base_main_tree"):
            errors.append("Stage 5 baseline is not bound to the approved bootstrap main tree")

    entries = baseline.get("entries")
    if not isinstance(entries, list):
        errors.append("Stage 5 debt baseline entries missing")
        return
    if baseline.get("entry_count") != len(entries):
        errors.append("Stage 5 debt baseline entry count mismatch")

    by_id: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            errors.append("Stage 5 debt baseline entry invalid")
            continue
        component_id = entry["id"]
        if component_id in by_id:
            errors.append(f"Stage 5 duplicate debt baseline entry: {component_id}")
            continue
        by_id[component_id] = entry
        if entry.get("license_gate_status") not in EXPECTED_DEBT:
            errors.append(f"Stage 5 baseline entry is not debt: {component_id}")
        fingerprint = entry.get("component_fingerprint")
        if not isinstance(fingerprint, str) or not SHA256.fullmatch(fingerprint):
            errors.append(f"Stage 5 baseline fingerprint invalid: {component_id}")

    components = registry.get("components")
    if not isinstance(components, list):
        errors.append("Stage 5 license gate registry components missing")
        return
    current_ids: set[str] = set()
    for component in components:
        if not isinstance(component, dict) or not isinstance(component.get("id"), str):
            errors.append("Stage 5 gate registry component invalid")
            continue
        component_id = component["id"]
        if component_id in current_ids:
            errors.append(f"Stage 5 duplicate gate registry component: {component_id}")
            continue
        current_ids.add(component_id)
        status = component.get("license_gate_status")
        allowed = component.get("allowed_for_new_dependency")
        if status not in EXPECTED_VOCABULARY:
            errors.append(f"Stage 5 unknown component status: {component_id}")
            continue
        if allowed is not (status in EXPECTED_ALLOWED):
            errors.append(f"Stage 5 component allow verdict mismatch: {component_id}")
        if status in EXPECTED_ALLOWED:
            continue
        baseline_entry = by_id.get(component_id)
        if baseline_entry is None:
            errors.append(f"Stage 5 new non-approved dependency is blocked: {component_id}")
            continue
        if baseline_entry.get("component_fingerprint") != component.get("component_fingerprint"):
            errors.append(f"Stage 5 inherited debt fingerprint changed: {component_id}")
        if baseline_entry.get("license_gate_status") != status:
            errors.append(f"Stage 5 inherited debt status changed: {component_id}")


def _validate_base_immutability(
    root: Path,
    policy: dict[str, Any],
    base_sha: str,
    errors: list[str],
) -> None:
    if not GIT_SHA.fullmatch(base_sha):
        errors.append("Stage 5 PR base SHA must be a full lowercase Git SHA")
        return
    try:
        if not _git_commit_exists(root, base_sha):
            errors.append("Stage 5 PR base commit is unavailable locally")
            return
        base_policy = _git_blob(root, base_sha, POLICY_PATH)
        base_baseline = _git_blob(root, base_sha, DEBT_BASELINE_PATH)
    except (OSError, RuntimeError) as exc:
        errors.append(f"Stage 5 base comparison unavailable: {type(exc).__name__}: {exc}")
        return

    if base_policy is None and base_baseline is None:
        bootstrap = policy.get("bootstrap")
        if not isinstance(bootstrap, dict):
            errors.append("Stage 5 bootstrap policy missing during initial activation")
            return
        if base_sha != bootstrap.get("base_main_sha"):
            errors.append("Stage 5 baseline bootstrap is allowed only from the recorded Stage 4 main SHA")
        tree = _git_tree(root, base_sha)
        if tree != bootstrap.get("base_main_tree"):
            errors.append("Stage 5 baseline bootstrap main tree mismatch")
        return

    if base_policy is None or base_baseline is None:
        errors.append("Stage 5 protected policy/baseline pair is incomplete in PR base")
        return

    if base_policy != (root / POLICY_PATH).read_bytes():
        errors.append("Stage 5 software license policy is immutable after activation")
    if base_baseline != (root / DEBT_BASELINE_PATH).read_bytes():
        errors.append("Stage 5 non-authorizing debt baseline is immutable after activation")


def collect_errors(root: Path = ROOT, *, base_sha: str | None = None) -> list[str]:
    """Return all Stage 5 gate failures without mutating repository state."""
    errors: list[str] = []
    for relative in REQUIRED:
        if not (root / relative).is_file():
            errors.append(f"Stage 5 required artifact missing: {relative}")
    if errors:
        return errors

    try:
        policy = _load(root / POLICY_PATH)
        registry = _load(root / GATE_REGISTRY_PATH)
        baseline = _load(root / DEBT_BASELINE_PATH)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return [f"Stage 5 artifact loading failed: {type(exc).__name__}: {exc}"]

    _validate_policy(policy, errors)

    try:
        expected = render_gate_registry(root)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"Stage 5 gate registry rebuild failed: {type(exc).__name__}: {exc}")
    else:
        actual = (root / GATE_REGISTRY_PATH).read_text(encoding="utf-8")
        if actual != expected:
            errors.append("Stage 5 software license gate registry drift")

    if registry.get("schema") != "omnigenis-software-license-gate-registry-v1":
        errors.append("Stage 5 gate registry schema mismatch")
    summary = registry.get("summary")
    if not isinstance(summary, dict):
        errors.append("Stage 5 gate registry summary missing")
    else:
        if summary.get("license_clean_claim_allowed") is not False:
            errors.append("Stage 5 must not make a LICENSE-CLEAN claim")
        if set(summary.get("new_dependency_allowed_statuses", [])) != EXPECTED_ALLOWED:
            errors.append("Stage 5 gate registry allowed-status summary mismatch")

    _validate_baseline(root, policy, registry, baseline, errors)
    if base_sha is not None:
        _validate_base_immutability(root, policy, base_sha, errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-sha")
    args = parser.parse_args()
    errors = collect_errors(ROOT, base_sha=args.base_sha)
    if errors:
        for error in errors:
            print(f"FAIL\t{error}")
        return 1
    registry = _load(ROOT / GATE_REGISTRY_PATH)
    summary = registry["summary"]
    counts = summary["by_license_gate_status"]
    print(
        "PASS\tstage5_software_license_gate\t"
        f"components={summary['component_records']} "
        f"approved={counts.get('APPROVED', 0)} "
        f"approved_with_notice={counts.get('APPROVED_WITH_NOTICE', 0)} "
        f"debt={summary['current_non_approved_records']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
