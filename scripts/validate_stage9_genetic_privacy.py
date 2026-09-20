#!/usr/bin/env python3
"""Validate Stage 9 LGPD/genetic-data privacy controls."""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.genetic_data_privacy_gate import validate_policy_contract
POLICY_REL = "config/genetic_data_privacy_policy.json"


def _json(path: Path) -> Any:
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
        node for node in ast.walk(function)
        if isinstance(node, ast.Call) and _call_name(node) == name
    ]


def _keywords(call: ast.Call) -> set[str]:
    return {kw.arg for kw in call.keywords if kw.arg is not None}


def _strip_nextflow_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", text)


def collect_errors(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    path = root / POLICY_REL
    if not path.is_file():
        return [f"missing {POLICY_REL}"]
    try:
        policy = _json(path)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"Stage 9 policy load failed: {exc}"]
    errors.extend(f"Stage 9 policy contract: {e}" for e in validate_policy_contract(policy))

    gate_path = root / "scripts/genetic_data_privacy_gate.py"
    try:
        gate = gate_path.read_text(encoding="utf-8")
    except OSError as exc:
        return errors + [f"privacy gate unavailable: {exc}"]
    if '"lgpd_compliance_claimed": False' not in gate:
        errors.append("privacy gate must not claim LGPD legal compliance")
    if '"legal_basis_inferred": False' not in gate:
        errors.append("privacy gate must state legal basis is not inferred")
    if '"synthetic_non_personal_fixture": True' not in gate:
        errors.append("privacy gate must preserve the non-personal synthetic fixture path")
    if '"legal_basis_reference": None' not in gate:
        errors.append("synthetic fixture path must not invent a legal basis")

    wgs_path = root / "scripts/wgs_consent_gate.py"
    wgs_fn = _function(wgs_path, "evaluate_consent")
    if wgs_fn is None:
        errors.append("WGS evaluate_consent function is missing or unparsable")
    else:
        privacy_calls = _calls(wgs_fn, "evaluate_privacy")
        if not privacy_calls:
            errors.append("WGS evaluate_consent does not call evaluate_privacy")
        elif not {"requested_purpose", "case_id", "input_sha256"}.issubset(
            _keywords(privacy_calls[0])
        ):
            errors.append("WGS privacy call must bind purpose, case_id and input_sha256")
        fn_text = ast.unparse(wgs_fn)
        if "ready_for_genetic_processing" not in fn_text:
            errors.append("WGS privacy decision is not fail-closed before first DNA read")

    array_path = root / "scripts/run_snp_array.py"
    array_fn = _function(array_path, "main")
    if array_fn is None:
        errors.append("SNP-array main function is missing or unparsable")
    else:
        load_calls = _calls(array_fn, "load_and_evaluate")
        inspect_calls = _calls(array_fn, "inspect_array")
        write_calls = _calls(array_fn, "write_outputs")
        receipt_calls = _calls(array_fn, "build_privacy_authorization_reference")
        if not load_calls:
            errors.append("SNP-array runner does not call load_and_evaluate")
        elif not {"requested_purpose", "case_id", "input_sha256"}.issubset(
            _keywords(load_calls[0])
        ):
            errors.append("SNP-array privacy call must bind purpose, case_id and input_sha256")
        if not inspect_calls or not load_calls or load_calls[0].lineno >= inspect_calls[0].lineno:
            errors.append("SNP-array privacy clearance must execute before inspect_array")
        if not receipt_calls or not write_calls or receipt_calls[0].lineno >= write_calls[0].lineno:
            errors.append("SNP-array Stage 9 authorization receipt must precede write_outputs")
        guard_lines = [
            node.lineno for node in ast.walk(array_fn)
            if isinstance(node, ast.If)
            and "privacy_result" in ast.unparse(node.test)
            and "ready_for_genetic_processing" in ast.unparse(node.test)
        ]
        inspect_line = inspect_calls[0].lineno if inspect_calls else 10**9
        load_line = load_calls[0].lineno if load_calls else -1
        if not any(load_line < line < inspect_line for line in guard_lines):
            errors.append("SNP-array runner lacks a fail-closed privacy guard before inspect_array")
        array_text = ast.unparse(array_fn)
        if "result['privacy_authorization'] = privacy_authorization" not in array_text:
            errors.append("SNP-array QC output is not bound to the Stage 9 authorization receipt")

    try:
        main_workflow = _strip_nextflow_comments((root / "main.nf").read_text(encoding="utf-8"))
        array_flow = _strip_nextflow_comments((root / "workflows/array.nf").read_text(encoding="utf-8"))
    except OSError as exc:
        errors.append(f"Stage 9 workflow source unavailable: {exc}")
    else:
        main_required = (
            r"privacy_record_ch\s*=\s*Channel\.fromPath\(params\.privacy_record",
            r"ARRAY_PRODUCTION\s*\([^)]*privacy_record_ch",
        )
        for pattern in main_required:
            if re.search(pattern, main_workflow, flags=re.S) is None:
                errors.append("Nextflow entrypoint does not structurally bind the array privacy record")
                break
        array_required = (
            r"process\s+ARRAY_QC\s*\{.*?input:.*?path\s+privacy_record.*?script:.*?--privacy-record\s+'\$\{privacy_record\}'",
            r"workflow\s+ARRAY_PRODUCTION\s*\{.*?take:.*?privacy_record.*?ARRAY_QC\s*\(array_input,\s*privacy_record,",
        )
        for pattern in array_required:
            if re.search(pattern, array_flow, flags=re.S) is None:
                errors.append("array workflow does not structurally pass privacy_record to ARRAY_QC")
                break

    array_ci_path = root / ".github/workflows/genoma-snp-array.yml"
    try:
        array_ci = array_ci_path.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"SNP-array CI privacy contract unavailable: {exc}")
    else:
        for token in (
            "SYNTHETIC_NON_PERSONAL_GENETIC_FIXTURE",
            "NO_NATURAL_PERSON",
            "CI_CANARY",
            "privacy-record.json",
            "--privacy-record",
            "--privacy_record",
        ):
            if token not in array_ci:
                errors.append(f"SNP-array synthetic CI privacy contract missing: {token}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    errors = collect_errors(ROOT)
    if errors:
        for error in errors:
            print(f"FAIL\t{error}")
        return 1
    print("PASS\tstage9_genetic_privacy\tfail_closed=true\tlegal_basis_inference=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
