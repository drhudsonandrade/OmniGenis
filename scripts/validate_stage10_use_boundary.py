#!/usr/bin/env python3
"""Validate Stage 10 research, clinical and regulatory separation."""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.use_boundary_gate import validate_policy_contract  # noqa: E402

POLICY_REL = "config/use_boundary_policy.json"
OBSOLETE_POLICY_REL = "config/research_clinical_regulatory_policy.json"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _function(path: Path, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None

def _calls(function: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and _call_name(node) == name
    ]


def _keywords(call: ast.Call) -> set[str]:
    return {keyword.arg for keyword in call.keywords if keyword.arg is not None}


def _has_returned_gate_shape(function: ast.AST) -> bool:
    for node in ast.walk(function):
        if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Dict):
            continue
        keys = {
            key.value
            for key in node.value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        if {"ready_for_requested_release", "errors", "gate"} <= keys:
            return True
    return False


def collect_errors(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    path = root / POLICY_REL
    if not path.is_file():
        return [f"missing {POLICY_REL}"]
    if (root / OBSOLETE_POLICY_REL).exists():
        errors.append("Stage 10 must have only one operational policy source")
    try:
        policy = _load(path)
    except (OSError, json.JSONDecodeError) as exc:
        return errors + [f"Stage 10 policy load failed: {exc}"]
    errors.extend(f"Stage 10 policy contract: {item}" for item in validate_policy_contract(policy))

    required = (
        "scripts/use_boundary_gate.py",
        "scripts/prepare_report_release.py",
        "docs/compliance/STAGE10_RESEARCH_CLINICAL_REGULATORY_BOUNDARY.md",
        "tests/test_stage10_use_boundary.py",
    )
    for rel in required:
        if not (root / rel).is_file():
            errors.append(f"Stage 10 integration file missing: {rel}")

    gate_path = root / "scripts/use_boundary_gate.py"
    gate_fn = _function(gate_path, "evaluate_use_boundary")
    if gate_fn is None:
        errors.append("Stage 10 evaluate_use_boundary is missing or unparsable")
    else:
        args = {argument.arg for argument in gate_fn.args.args + gate_fn.args.kwonlyargs}
        if not {"expected_case_id", "expected_input_sha256"} <= args:
            errors.append("Stage 10 gate must bind case_id and input_sha256")
        if not _calls(gate_fn, "validate_policy_contract"):
            errors.append("Stage 10 gate must validate the policy contract at runtime")
        if not _has_returned_gate_shape(gate_fn):
            errors.append("Stage 10 gate must return an explicit auditable decision")
        gate_text = ast.unparse(gate_fn)
        for token in (
            "regulatory_classification_determined_by_software",
            "clinical_validity_determined_by_software",
            "research_ethics_determined_by_software",
            "research_ethics_assessment",
            "professional_review",
            "clinical_validation",
            "regulatory_assessment",
        ):
            if token not in gate_text:
                errors.append(f"Stage 10 gate missing protected boundary behavior: {token}")

    release_path = root / "scripts/prepare_report_release.py"
    release_fn = _function(release_path, "assemble_release")
    if release_fn is None:
        errors.append("prepare_report_release assemble_release is missing or unparsable")
    else:
        calls = _calls(release_fn, "evaluate_use_boundary")
        if not calls:
            errors.append("report release does not execute the Stage 10 boundary gate")
        elif not {"expected_case_id", "expected_input_sha256"} <= _keywords(calls[0]):
            errors.append("report release does not bind Stage 10 to case_id and input_sha256")
        release_text = ast.unparse(release_fn)
        if "UseBoundaryError" not in release_text:
            errors.append("report release must fail closed on Stage 10 policy/gate errors")
        if "ready_for_requested_release" not in release_text:
            errors.append("report release does not enforce the Stage 10 decision")
        boundary_lines = [call.lineno for call in calls]
        publication_lines = [
            node.lineno
            for node in ast.walk(release_fn)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "publication"
                for target in node.targets
            )
        ]
        if boundary_lines and publication_lines and min(boundary_lines) >= min(publication_lines):
            errors.append("Stage 10 gate must execute before final publication evaluation")

    for workflow_rel in (
        ".github/workflows/genoma-ngs-runtime-gate.yml",
        ".github/workflows/scaffold-validation.yml",
    ):
        workflow = root / workflow_rel
        try:
            text = workflow.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"Stage 10 CI integration unavailable: {workflow_rel}: {exc}")
            continue
        if "validate_stage10_use_boundary.py" not in text:
            errors.append(f"Stage 10 validator is not executed by {workflow_rel}")

    return errors

def main() -> int:
    errors = collect_errors()
    if errors:
        for error in errors:
            print(f"FAIL\t{error}")
        return 1
    print(
        "PASS\tstage10_use_boundary\t"
        "software_regulatory_classification=false\t"
        "software_research_ethics_decision=false\t"
        "case_input_binding=true"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
