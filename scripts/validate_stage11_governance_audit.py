#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY_REL = "config/stage11_governance_audit_policy.json"
EVIDENCE_REL = "docs/evidence/STAGE11_FINAL_GOVERNANCE_AUDIT_2026-09-21.json"
SHA256 = re.compile(r"^[0-9a-f]{64}$")

EXPECTED_STAGE_CHAIN = (
    (1, 74, "3df0c18b39dee583f785ff99fe23411af2ca45e2"),
    (2, 75, "6ea2a8879cb0cadc953cef25084acb21f5d60900"),
    (3, 76, "181bd15f56c1d00ff00fa21c1e78aca6e6daf0ae"),
    (4, 77, "9a379f58ac9fe0bb101ebfd737ac648e510bbca2"),
    (5, 78, "5bb0b03d25f9bc3be26850a72a85950f34a61dca"),
    (6, 79, "016f92ae18051ca5a65238819d4414ba7cf37a08"),
    (7, 80, "deb6db0a59b00dce86985d2dfc1cfda6037d1362"),
    (8, 81, "6ecaf0132b1ad163fd3fe7792bf69f62021a1262"),
    (9, 82, "c25ba19a0fb4fbf2c8adc272165d7affa2ffc869"),
    (10, 83, "8ab7dcf50c51d4fe16de7a70b35b8e0ae14e349f"),
)
FALSE_CLAIMS = (
    "legal_compliance_determined_by_software",
    "license_clean_certification",
    "clinical_validity_determined_by_software",
    "regulatory_approval_determined_by_software",
    "research_ethics_determined_by_software",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_policy_contract(policy: object) -> list[str]:
    if not isinstance(policy, dict):
        return ["Stage 11 policy must be an object"]
    errors: list[str] = []
    if policy.get("schema") != "omnigenis-final-governance-audit-policy-v1":
        errors.append("Stage 11 policy schema mismatch")
    if policy.get("status") != "ACTIVE" or policy.get("version") != "1.0":
        errors.append("Stage 11 policy identity mismatch")
    if policy.get("audit_scope") != "COPYRIGHT_AND_GOVERNANCE_CHAIN_ONLY":
        errors.append("Stage 11 audit scope drift")
    stages = policy.get("stages")
    if not isinstance(stages, list) or len(stages) != 10:
        errors.append("Stage 11 policy stage coverage mismatch")
    else:
        actual = [
            (item.get("stage"), item.get("pr"), item.get("merge_commit"))
            for item in stages
            if isinstance(item, dict)
        ]
        if actual != list(EXPECTED_STAGE_CHAIN):
            errors.append("Stage 11 policy chain identity drift")
    boundary = policy.get("claim_boundary")
    if not isinstance(boundary, dict) or any(boundary.get(key) is not False for key in FALSE_CLAIMS):
        errors.append("Stage 11 policy claim boundary must remain false")
    return errors


def _git_show_bytes(root: Path, commit: str, relative: str) -> bytes:
    completed = subprocess.run(
        ["git", "show", f"{commit}:{relative}"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return completed.stdout


def validate_evidence_payload(payload: object, root: Path = ROOT) -> list[str]:
    if not isinstance(payload, dict):
        return ["Stage 11 evidence must be an object"]
    errors: list[str] = []
    if payload.get("schema") != "omnigenis-final-governance-audit-evidence-v1":
        errors.append("Stage 11 evidence schema mismatch")
    if payload.get("audit_scope") != "COPYRIGHT_AND_GOVERNANCE_CHAIN_ONLY":
        errors.append("Stage 11 evidence audit scope mismatch")
    if payload.get("operational_status") != "EXECUTADO" or payload.get("result") != "PASS":
        errors.append("Stage 11 evidence is not an executed PASS")
    stages = payload.get("stages")
    if not isinstance(stages, list) or len(stages) != 10:
        errors.append("Stage 11 evidence stage coverage mismatch")
    else:
        numbers = [item.get("stage") for item in stages if isinstance(item, dict)]
        if numbers != list(range(1, 11)):
            errors.append("Stage 11 evidence stage coverage mismatch")
        for item in stages:
            if not isinstance(item, dict):
                errors.append("Stage 11 stage record invalid")
                continue
            if (
                item.get("operational_status") != "EXECUTADO"
                or item.get("result") != "PASS"
                or item.get("merge_ancestry_verified") is not True
                or item.get("errors") != []
            ):
                errors.append(f"Stage 11 stage {item.get('stage')} is not a clean PASS")
    controls = payload.get("global_controls")
    if not isinstance(controls, list) or len(controls) != 3:
        errors.append("Stage 11 global control coverage mismatch")
    elif any(
        not isinstance(item, dict)
        or item.get("operational_status") != "EXECUTADO"
        or item.get("result") != "PASS"
        for item in controls
    ):
        errors.append("Stage 11 global control is not a clean PASS")
    boundary = payload.get("claim_boundary")
    if not isinstance(boundary, dict) or any(boundary.get(key) is not False for key in FALSE_CLAIMS):
        errors.append("Stage 11 evidence claim boundary must remain false")

    implementation_sha = payload.get("implementation_sha")
    tree_sha = payload.get("tree_sha")
    if not isinstance(implementation_sha, str) or SHA256.fullmatch(implementation_sha) is None:
        errors.append("Stage 11 implementation SHA invalid")
        return errors
    if not isinstance(tree_sha, str) or SHA256.fullmatch(tree_sha) is None:
        errors.append("Stage 11 tree SHA invalid")
    try:
        actual_tree = subprocess.run(
            ["git", "rev-parse", f"{implementation_sha}^{{tree}}"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", implementation_sha, "HEAD"],
            cwd=root,
        ).returncode == 0
        policy_bytes = _git_show_bytes(root, implementation_sha, POLICY_REL)
        runner_bytes = _git_show_bytes(
            root,
            implementation_sha,
            "scripts/run_stage11_governance_audit.py",
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        errors.append(f"Stage 11 implementation binding unavailable: {exc}")
        return errors
    if actual_tree != tree_sha:
        errors.append("Stage 11 implementation tree mismatch")
    if not ancestor:
        errors.append("Stage 11 implementation SHA is not an ancestor of current HEAD")
    if hashlib.sha256(policy_bytes).hexdigest() != payload.get("policy_sha256"):
        errors.append("Stage 11 policy hash does not bind audited implementation")
    if hashlib.sha256(runner_bytes).hexdigest() != payload.get("runner_sha256"):
        errors.append("Stage 11 runner hash does not bind audited implementation")
    return errors


def collect_errors(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    try:
        policy = json.loads((root / POLICY_REL).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"Stage 11 policy unavailable: {exc}"]
    errors.extend(validate_policy_contract(policy))
    evidence_path = root / EVIDENCE_REL
    if not evidence_path.is_file():
        errors.append(f"Stage 11 evidence missing: {EVIDENCE_REL}")
        return errors
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"Stage 11 evidence unavailable: {exc}")
        return errors
    errors.extend(validate_evidence_payload(evidence, root))
    return errors


def main() -> int:
    errors = collect_errors(ROOT)
    if errors:
        for error in errors:
            print(f"FAIL\t{error}")
        return 1
    print(
        "PASS\tstage11_final_governance_audit\t"
        "stages=10\tlegal_compliance_determined_by_software=false\t"
        "clinical_final_audit_replaced=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
