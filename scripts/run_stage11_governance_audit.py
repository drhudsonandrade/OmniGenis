#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
POLICY_REL = "config/stage11_governance_audit_policy.json"

from scripts.validate_repo import validate_stage2_pdf_contract, validate_stage3_copyleft_contract
from scripts.validate_stage4_compliance import collect_errors as validate_stage4
from scripts.validate_stage5_license_gate import collect_errors as validate_stage5
from scripts.validate_stage6_data_sources import collect_errors as validate_stage6
from scripts.validate_stage7_purpose_use import collect_errors as validate_stage7
from scripts.validate_stage8_contribution_provenance import collect_errors as validate_stage8
from scripts.validate_stage9_genetic_privacy import collect_errors as validate_stage9
from scripts.validate_stage10_use_boundary import collect_errors as validate_stage10

EXECUTED = "EXECUTADO"
UNAVAILABLE = "NÃO DISPONÍVEL"
PASS = "PASS"
FAIL = "FAIL"
ERROR = "ERROR"

EXPECTED_STAGE_CHAIN = (
    {"stage": 1, "pr": 74, "merge_commit": "3df0c18b39dee583f785ff99fe23411af2ca45e2"},
    {"stage": 2, "pr": 75, "merge_commit": "6ea2a8879cb0cadc953cef25084acb21f5d60900"},
    {"stage": 3, "pr": 76, "merge_commit": "181bd15f56c1d00ff00fa21c1e78aca6e6daf0ae"},
    {"stage": 4, "pr": 77, "merge_commit": "9a379f58ac9fe0bb101ebfd737ac648e510bbca2"},
    {"stage": 5, "pr": 78, "merge_commit": "5bb0b03d25f9bc3be26850a72a85950f34a61dca"},
    {"stage": 6, "pr": 79, "merge_commit": "016f92ae18051ca5a65238819d4414ba7cf37a08"},
    {"stage": 7, "pr": 80, "merge_commit": "deb6db0a59b00dce86985d2dfc1cfda6037d1362"},
    {"stage": 8, "pr": 81, "merge_commit": "6ecaf0132b1ad163fd3fe7792bf69f62021a1262"},
    {"stage": 9, "pr": 82, "merge_commit": "c25ba19a0fb4fbf2c8adc272165d7affa2ffc869"},
    {"stage": 10, "pr": 83, "merge_commit": "8ab7dcf50c51d4fe16de7a70b35b8e0ae14e349f"},
)

STAGE1_REQUIRED_PATHS = (
    "LICENSE",
    "policy_engine/LICENSE",
    "COPYRIGHT.md",
    "AUTHORS.md",
    "THIRD_PARTY_NOTICES.md",
    "docs/compliance/LICENSING_POLICY.md",
    "docs/compliance/DEPENDENCY_POLICY.md",
    "licenses/README.md",
    "config/identity_provenance_authorizations.json",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def load_policy(root: Path = ROOT) -> dict[str, Any]:
    payload = json.loads((root / POLICY_REL).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Stage 11 policy must be a JSON object")
    return payload


def _stage1_errors(root: Path) -> list[str]:
    errors: list[str] = []
    for relative in STAGE1_REQUIRED_PATHS:
        if not (root / relative).is_file():
            errors.append(f"Stage 1 baseline file missing: {relative}")
    if errors:
        return errors
    if (root / "LICENSE").read_bytes() != (root / "policy_engine/LICENSE").read_bytes():
        errors.append("Stage 1 root and policy-engine license copies differ")
    try:
        auth = json.loads(
            (root / "config/identity_provenance_authorizations.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError) as exc:
        return [f"Stage 1 identity provenance authorization unreadable: {exc}"]
    if auth.get("schema") != "omnigenis-identity-provenance-authorization-v1":
        errors.append("Stage 1 identity provenance authorization schema mismatch")
    items = auth.get("authorizations")
    if not isinstance(items, list):
        errors.append("Stage 1 identity provenance authorizations missing")
        return errors
    by_path = {
        item.get("path"): item
        for item in items
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    for relative in ("LICENSE", "policy_engine/LICENSE"):
        record = by_path.get(relative)
        if not isinstance(record, dict):
            errors.append(f"Stage 1 authorization missing: {relative}")
            continue
        if record.get("sha256") != _sha256(root / relative):
            errors.append(f"Stage 1 authorization hash mismatch: {relative}")
    return errors


def _stage2_errors(root: Path) -> list[str]:
    errors: list[str] = []
    validate_stage2_pdf_contract(root, errors)
    return errors


def _stage3_errors(root: Path) -> list[str]:
    errors: list[str] = []
    validate_stage3_copyleft_contract(root, errors)
    return errors


VALIDATORS: dict[int, tuple[str, Callable[[Path], list[str]]]] = {
    1: ("stage1_baseline", _stage1_errors),
    2: ("validate_stage2_pdf_contract", _stage2_errors),
    3: ("validate_stage3_copyleft_contract", _stage3_errors),
    4: ("validate_stage4_compliance", validate_stage4),
    5: ("validate_stage5_license_gate", validate_stage5),
    6: ("validate_stage6_data_sources", validate_stage6),
    7: ("validate_stage7_purpose_use", validate_stage7),
    8: ("validate_stage8_contribution_provenance", validate_stage8),
    9: ("validate_stage9_genetic_privacy", validate_stage9),
    10: ("validate_stage10_use_boundary", validate_stage10),
}


def _is_ancestor(root: Path, commit: str, head: str) -> bool:
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, head],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


def _governance_manifest_errors(root: Path) -> list[str]:
    errors: list[str] = []
    approval_path = root / ".github/governance/main-approval-ruleset.json"
    protected_path = root / ".github/governance/main-ruleset.json"
    try:
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
        protected = json.loads(protected_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"governance manifest unavailable: {exc}"]

    if approval.get("name") != "GENOMA approval gate":
        errors.append("approval ruleset name mismatch")
    if approval.get("target") != "branch" or approval.get("enforcement") != "active":
        errors.append("approval ruleset activation mismatch")
    if approval.get("conditions") != {
        "ref_name": {"include": ["refs/heads/main"], "exclude": []}
    }:
        errors.append("approval ruleset branch condition mismatch")
    if approval.get("bypass_actors") != [
        {
            "actor_id": 116986656,
            "actor_type": "User",
            "bypass_mode": "pull_request",
        }
    ]:
        errors.append("approval ruleset bypass actor mismatch")
    approval_rules = approval.get("rules")
    if not isinstance(approval_rules, list):
        errors.append("approval ruleset rules missing")
    else:
        types = [rule.get("type") for rule in approval_rules if isinstance(rule, dict)]
        if types != ["pull_request"]:
            errors.append("approval ruleset must contain only the pull_request rule")
        elif approval_rules[0].get("parameters", {}).get(
            "required_review_thread_resolution"
        ) is not True:
            errors.append("approval ruleset must require review-thread resolution")

    if protected.get("name") != "GENOMA protected main":
        errors.append("protected-main ruleset name mismatch")
    if protected.get("bypass_actors") != []:
        errors.append("protected-main ruleset must not have bypass actors")
    protected_rules = protected.get("rules")
    if not isinstance(protected_rules, list):
        errors.append("protected-main rules missing")
    else:
        types = [
            rule.get("type") for rule in protected_rules if isinstance(rule, dict)
        ]
        if types != ["deletion", "non_fast_forward", "required_status_checks"]:
            errors.append("protected-main rule set drift")
        status_rules = [
            rule
            for rule in protected_rules
            if isinstance(rule, dict) and rule.get("type") == "required_status_checks"
        ]
        if len(status_rules) != 1:
            errors.append("protected-main required-status rule count mismatch")
        else:
            parameters = status_rules[0].get("parameters", {})
            if parameters.get("strict_required_status_checks_policy") is not True:
                errors.append("protected-main strict status checks must remain enabled")
            checks = parameters.get("required_status_checks")
            if not isinstance(checks, list) or len(checks) != 14:
                errors.append("protected-main required-check coverage mismatch")
            elif any(
                "greptile" in str(item.get("context", "")).casefold()
                for item in checks
                if isinstance(item, dict)
            ):
                errors.append("retired Greptile check reintroduced")
    return errors


def _governance_manifest_control(root: Path) -> dict[str, Any]:
    try:
        errors = _governance_manifest_errors(root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "id": "GITHUB_GOVERNANCE_MANIFESTS",
            "operational_status": UNAVAILABLE,
            "result": ERROR,
            "returncode": None,
            "evidence": f"{type(exc).__name__}: {exc}",
        }
    return {
        "id": "GITHUB_GOVERNANCE_MANIFESTS",
        "operational_status": EXECUTED,
        "result": PASS if not errors else FAIL,
        "returncode": 0 if not errors else 1,
        "evidence": json.dumps({"errors": errors}, sort_keys=True),
    }


def _command_control(root: Path, control_id: str, command: list[str]) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=240,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "id": control_id,
            "operational_status": UNAVAILABLE,
            "result": ERROR,
            "returncode": None,
            "evidence": f"{type(exc).__name__}: {exc}",
        }
    return {
        "id": control_id,
        "operational_status": EXECUTED,
        "result": PASS if completed.returncode == 0 else FAIL,
        "returncode": completed.returncode,
        "evidence": (completed.stdout + "\n" + completed.stderr)[-6000:],
    }


def run_audit(root: Path = ROOT, output: Path | None = None) -> dict[str, Any]:
    policy = load_policy(root)
    head = _git(root, "rev-parse", "HEAD")
    tree = _git(root, "rev-parse", "HEAD^{tree}")
    stage_records: list[dict[str, Any]] = []
    for stage in policy["stages"]:
        number = int(stage["stage"])
        validator_name, validator = VALIDATORS[number]
        try:
            errors = validator(root)
            operational_status = EXECUTED
            result = PASS if not errors else FAIL
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors = [f"{type(exc).__name__}: {exc}"]
            operational_status = UNAVAILABLE
            result = ERROR
        ancestry = _is_ancestor(root, stage["merge_commit"], head)
        if not ancestry and result == PASS:
            result = FAIL
            errors = [*errors, "stage merge commit is not an ancestor of audited HEAD"]
        stage_records.append(
            {
                "stage": number,
                "pr": stage["pr"],
                "name": stage["name"],
                "merge_commit": stage["merge_commit"],
                "merge_ancestry_verified": ancestry,
                "validator": validator_name,
                "operational_status": operational_status,
                "result": result,
                "errors": errors,
            }
        )

    controls = [
        _governance_manifest_control(root),
        _command_control(
            root,
            "SUPPLY_CHAIN_LOCK",
            [sys.executable, "scripts/verify_supply_chain_lock.py"],
        ),
        _command_control(
            root,
            "RESIDUAL_LANGUAGE_AUDIT",
            [sys.executable, "scripts/residual_language_audit.py", "--check"],
        ),
        _command_control(
            root,
            "CODE_LANGUAGE_GUARD",
            [sys.executable, "scripts/code_language_guard.py", "--check"],
        ),
    ]
    overall = (
        PASS
        if all(item["result"] == PASS for item in stage_records)
        and all(item["result"] == PASS for item in controls)
        else FAIL
    )
    payload = {
        "schema": "omnigenis-final-governance-audit-evidence-v1",
        "audit_scope": policy["audit_scope"],
        "operational_status": EXECUTED,
        "result": overall,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "implementation_sha": head,
        "tree_sha": tree,
        "policy_sha256": _sha256(root / POLICY_REL),
        "runner_sha256": _sha256(root / "scripts/run_stage11_governance_audit.py"),
        "stages": stage_records,
        "global_controls": controls,
        "claim_boundary": dict(policy["claim_boundary"]),
        "note": (
            "This audit verifies repository governance contracts and evidence integrity. "
            "It does not replace the clinical FINAL_AUDIT_GATE and does not issue legal, "
            "licensing, regulatory, clinical-validity, or research-ethics determinations."
        ),
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="docs/evidence/STAGE11_FINAL_GOVERNANCE_AUDIT_2026-09-21.json",
    )
    args = parser.parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    payload = run_audit(ROOT, output)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload["result"] == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
