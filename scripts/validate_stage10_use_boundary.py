#!/usr/bin/env python3
"""Validate Stage 10 research, clinical and regulatory separation."""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.use_boundary_gate import validate_policy_contract  # noqa: E402

POLICY_REL = "config/use_boundary_policy.json"
OBSOLETE_POLICY_REL = "config/research_clinical_regulatory_policy.json"
FINAL_REPORT_OPERATION = "FINAL_AUDITED_REPORT"


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


class _ReachableCallVisitor(ast.NodeVisitor):
    """Collect executable calls while excluding nested definitions and dead literals."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[ast.Call] = []

    def visit_Call(self, node: ast.Call) -> None:
        if _call_name(node) == self.name:
            self.calls.append(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        if isinstance(node.test, ast.Constant) and isinstance(node.test.value, bool):
            selected = node.body if node.test.value else node.orelse
            for statement in selected:
                self.visit(statement)
            return
        for statement in node.body:
            self.visit(statement)
        for statement in node.orelse:
            self.visit(statement)


def _reachable_calls(function: ast.AST, name: str) -> list[ast.Call]:
    visitor = _ReachableCallVisitor(name)
    body = getattr(function, "body", [])
    for statement in body:
        visitor.visit(statement)
    return visitor.calls


def _statement_calls(statement: ast.stmt, name: str) -> list[ast.Call]:
    visitor = _ReachableCallVisitor(name)
    visitor.visit(statement)
    return visitor.calls


def _keywords(call: ast.Call) -> set[str]:
    return {keyword.arg for keyword in call.keywords if keyword.arg is not None}


def _keyword_constant(call: ast.Call, name: str) -> object | None:
    for keyword in call.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return keyword.value.value
    return None


def _target_has_name(target: ast.AST, name: str) -> bool:
    if isinstance(target, ast.Name):
        return target.id == name
    if isinstance(target, (ast.Tuple, ast.List)):
        return any(_target_has_name(item, name) for item in target.elts)
    return False


def _direct_assigns(statement: ast.stmt, name: str) -> bool:
    if isinstance(statement, ast.Assign):
        return any(_target_has_name(target, name) for target in statement.targets)
    if isinstance(statement, ast.AnnAssign):
        return _target_has_name(statement.target, name)
    return False


def _block_assigns_on_all_paths(statements: list[ast.stmt], name: str) -> bool:
    for statement in statements:
        if _direct_assigns(statement, name):
            return True
        if _statement_assigns_on_all_paths(statement, name):
            return True
        if isinstance(statement, (ast.Return, ast.Raise)):
            return False
    return False


def _statement_assigns_on_all_paths(statement: ast.stmt, name: str) -> bool:
    if isinstance(statement, ast.If):
        if isinstance(statement.test, ast.Constant) and isinstance(
            statement.test.value, bool
        ):
            selected = statement.body if statement.test.value else statement.orelse
            return _block_assigns_on_all_paths(selected, name)
        if not statement.orelse:
            return False
        return _block_assigns_on_all_paths(
            statement.body, name
        ) and _block_assigns_on_all_paths(statement.orelse, name)
    if isinstance(statement, ast.Try):
        if not _block_assigns_on_all_paths(statement.body, name):
            return False
        if not statement.handlers:
            return True
        return all(
            _block_assigns_on_all_paths(handler.body, name)
            for handler in statement.handlers
        )
    return False


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


def _top_level_assignment_index(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
) -> int | None:
    for index, statement in enumerate(function.body):
        if _direct_assigns(statement, name):
            return index
    return None


def _boundary_guard_index(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> int | None:
    for index, statement in enumerate(function.body):
        if not isinstance(statement, ast.If):
            continue
        test_text = ast.unparse(statement.test)
        body_text = ast.unparse(statement)
        if (
            "boundary_result" in test_text
            and "ready_for_requested_release" in test_text
            and "blockers.append('use_boundary')" in body_text
        ):
            return index
    return None


def _release_flow_errors(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[str]:
    errors: list[str] = []
    decision_indexes: list[int] = []
    decision_calls: list[ast.Call] = []
    for index, statement in enumerate(function.body):
        calls = _statement_calls(statement, "evaluate_use_boundary")
        if calls:
            decision_indexes.append(index)
            decision_calls.extend(calls)

    if len(decision_calls) != 1 or len(set(decision_indexes)) != 1:
        errors.append(
            "report release does not execute a reachable top-level Stage 10 boundary decision"
        )
        return errors

    decision_index = decision_indexes[0]
    decision_statement = function.body[decision_index]
    if not _statement_assigns_on_all_paths(decision_statement, "boundary_result"):
        errors.append(
            "report release Stage 10 boundary_result is not assigned on all release paths"
        )

    call = decision_calls[0]
    required_keywords = {
        "requested_operation",
        "expected_case_id",
        "expected_input_sha256",
        "evidence_ledger",
    }
    if not required_keywords <= _keywords(call):
        errors.append(
            "report release does not bind Stage 10 operation, case, input and evidence ledger"
        )
    if _keyword_constant(call, "requested_operation") != FINAL_REPORT_OPERATION:
        errors.append(
            "report release Stage 10 requested_operation must be FINAL_AUDITED_REPORT"
        )

    guard_index = _boundary_guard_index(function)
    publication_index = _top_level_assignment_index(function, "publication")
    if guard_index is None:
        errors.append("report release does not enforce the Stage 10 decision")
    if publication_index is None:
        errors.append("report release publication evaluation is missing")
    if (
        guard_index is not None
        and publication_index is not None
        and not (decision_index < guard_index < publication_index)
    ):
        errors.append(
            "Stage 10 boundary decision and blocker must dominate final publication evaluation"
        )
    return errors


def _workflow_run_commands(text: str) -> list[str]:
    """Extract only YAML step run bodies, excluding path filters and comments."""
    lines = text.splitlines()
    commands: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if not stripped.startswith("run:"):
            index += 1
            continue
        value = stripped[len("run:") :].strip()
        if value and value not in {"|", ">"}:
            commands.append(value)
            index += 1
            continue
        block: list[str] = []
        index += 1
        while index < len(lines):
            candidate = lines[index]
            candidate_stripped = candidate.lstrip()
            candidate_indent = len(candidate) - len(candidate_stripped)
            if candidate_stripped and candidate_indent <= indent:
                break
            if candidate_stripped and not candidate_stripped.startswith("#"):
                block.append(candidate_stripped)
            index += 1
        commands.append("\n".join(block))
    return commands


def _command_runs_script(command: str, script: str) -> bool:
    pattern = re.compile(
        rf"(?m)^(?:\s*(?:[A-Za-z_][A-Za-z0-9_]*=[^ ]+\s+)*)"
        rf"(?:python3|python)\s+{re.escape(script)}(?:\s|$)"
    )
    return pattern.search(command) is not None


def _workflow_executes_stage10(root: Path, workflow_path: Path) -> bool:
    text = workflow_path.read_text(encoding="utf-8")
    commands = _workflow_run_commands(text)
    if any(
        _command_runs_script(command, "scripts/validate_stage10_use_boundary.py")
        for command in commands
    ):
        return True
    if any(
        _command_runs_script(command, "scripts/validate_repo.py")
        for command in commands
    ):
        repo_validator = (root / "scripts/validate_repo.py").read_text(
            encoding="utf-8"
        )
        return (
            "from scripts.validate_stage10_use_boundary import collect_errors"
            in repo_validator
            and "validate_stage10_use_boundary(root)" in repo_validator
        )
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
    errors.extend(
        f"Stage 10 policy contract: {item}"
        for item in validate_policy_contract(policy)
    )

    required = (
        "scripts/use_boundary_gate.py",
        "scripts/prepare_report_release.py",
        "scripts/generate_all_reports.py",
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
        args = {
            argument.arg
            for argument in gate_fn.args.args + gate_fn.args.kwonlyargs
        }
        if not {
            "requested_operation",
            "expected_case_id",
            "expected_input_sha256",
            "evidence_ledger",
        } <= args:
            errors.append(
                "Stage 10 gate must bind operation, case_id, input_sha256 and evidence_ledger"
            )
        if not _reachable_calls(gate_fn, "validate_policy_contract"):
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
            "_authenticated_evidence_index",
        ):
            if token not in gate_text:
                errors.append(
                    f"Stage 10 gate missing protected boundary behavior: {token}"
                )

    release_path = root / "scripts/prepare_report_release.py"
    release_fn = _function(release_path, "assemble_release")
    if release_fn is None:
        errors.append("prepare_report_release assemble_release is missing or unparsable")
    else:
        errors.extend(_release_flow_errors(release_fn))
        release_text = ast.unparse(release_fn)
        if "UseBoundaryError" not in release_text:
            errors.append(
                "report release must fail closed on Stage 10 policy/gate errors"
            )

    generator_text = (root / "scripts/generate_all_reports.py").read_text(
        encoding="utf-8"
    )
    for token in ("--use-boundary", "--use-boundary-evidence-ledger"):
        if token not in generator_text:
            errors.append(f"generate_all_reports.py missing Stage 10 input: {token}")

    for workflow_rel in (
        ".github/workflows/genoma-ngs-runtime-gate.yml",
        ".github/workflows/scaffold-validation.yml",
    ):
        workflow = root / workflow_rel
        try:
            if not _workflow_executes_stage10(root, workflow):
                errors.append(f"Stage 10 validator is not executed by {workflow_rel}")
        except OSError as exc:
            errors.append(
                f"Stage 10 CI integration unavailable: {workflow_rel}: {exc}"
            )

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
        "case_input_binding=true\t"
        "authenticated_evidence=true"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
