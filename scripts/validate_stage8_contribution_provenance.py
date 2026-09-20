#!/usr/bin/env python3
"""Validate Stage 8 technical authorship and contribution provenance."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY_REL = "config/contribution_provenance_policy.json"
LEDGER_REL = "config/contribution_provenance_ledger.json"


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )


def _git_blob(root: Path, commit: str, path: str) -> bytes | None:
    proc = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    return proc.stdout if proc.returncode == 0 else None


def _changed_paths(root: Path, base_sha: str, head_sha: str = "HEAD") -> set[str] | None:
    proc = _git(root, "diff", "--name-only", "--no-renames", f"{base_sha}..{head_sha}", "--")
    if proc.returncode != 0:
        return None
    return {line for line in proc.stdout.splitlines() if line}


def _load_previous_ledger(root: Path, base_sha: str) -> dict[str, Any] | None:
    proc = _git(root, "show", f"{base_sha}:{LEDGER_REL}")
    if proc.returncode != 0:
        return None
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def collect_errors(root: Path = ROOT, *, base_sha: str | None = None) -> list[str]:
    errors: list[str] = []
    policy_path = root / POLICY_REL
    ledger_path = root / LEDGER_REL
    if not policy_path.is_file():
        return [f"missing {POLICY_REL}"]
    if not ledger_path.is_file():
        return [f"missing {LEDGER_REL}"]

    try:
        policy = _json(policy_path)
        ledger = _json(ledger_path)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"Stage 8 JSON load failed: {exc}"]
    if not isinstance(policy, dict) or policy.get("schema") != "omnigenis-contribution-provenance-policy-v1":
        errors.append("invalid Stage 8 policy schema")
        return errors
    if policy.get("status") != "ACTIVE":
        errors.append("Stage 8 policy status must be ACTIVE")
    origin_classes = policy.get("origin_classes")
    assistance_classes = policy.get("assistance_classes")
    if not isinstance(origin_classes, list) or not all(isinstance(v, str) and v for v in origin_classes):
        errors.append("Stage 8 origin_classes must be non-empty strings")
        origin_classes = []
    if not isinstance(assistance_classes, list) or not all(isinstance(v, str) and v for v in assistance_classes):
        errors.append("Stage 8 assistance_classes must be non-empty strings")
        assistance_classes = []

    rules = policy.get("rules")
    if not isinstance(rules, dict):
        errors.append("Stage 8 policy rules missing")
        rules = {}
    for key in (
        "append_only_ledger",
        "unknown_assistance_is_historical_or_unresolved_only",
        "third_party_rights_must_use_stage4_to_stage7_controls",
        "human_direction_required_for_repository_native_changesets",
    ):
        if rules.get(key) is not True:
            errors.append(f"Stage 8 protected rule must remain true: {key}")
    if rules.get("legal_ownership_inference_from_git") is not False:
        errors.append("Stage 8 must prohibit legal ownership inference from Git")
    if rules.get("unknown_origin_machine_authorized") is not False:
        errors.append("Stage 8 UNKNOWN origin must fail closed")

    control = policy.get("control_paths")
    if not isinstance(control, dict):
        errors.append("Stage 8 control_paths missing")
        return errors
    if control.get("ledger") != LEDGER_REL:
        errors.append("Stage 8 ledger control path drift")
    runtime_lock = control.get("runtime_lock")
    evidence_prefix = control.get("evidence_prefix")
    if runtime_lock != "locks/runtime-lock.json":
        errors.append("Stage 8 runtime-lock control path drift")
    if evidence_prefix != "docs/evidence/contribution_provenance/":
        errors.append("Stage 8 evidence prefix drift")

    if not isinstance(ledger, dict) or ledger.get("schema") != "omnigenis-contribution-provenance-ledger-v1":
        errors.append("invalid Stage 8 ledger schema")
        return errors
    if ledger.get("append_only") is not True:
        errors.append("Stage 8 ledger must be append-only")
    entries = ledger.get("entries")
    if not isinstance(entries, list) or not entries:
        errors.append("Stage 8 ledger must contain at least one entry")
        return errors

    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"Stage 8 ledger entry {index} must be an object")
            continue
        change_set_id = entry.get("change_set_id")
        if not isinstance(change_set_id, str) or not change_set_id:
            errors.append(f"Stage 8 ledger entry {index} change_set_id missing")
            continue
        if change_set_id in seen:
            errors.append(f"Stage 8 duplicate change_set_id: {change_set_id}")
        seen.add(change_set_id)
        if entry.get("origin_class") not in origin_classes:
            errors.append(f"{change_set_id}: unsupported origin_class")
        if entry.get("assistance_class") not in assistance_classes:
            errors.append(f"{change_set_id}: unsupported assistance_class")
        if entry.get("origin_class") == "REPOSITORY_NATIVE" and entry.get("human_direction") is not True:
            errors.append(f"{change_set_id}: repository-native changes require human_direction=true")
        if entry.get("origin_class") == "UNKNOWN":
            errors.append(f"{change_set_id}: UNKNOWN origin is not machine-authorized for a new ledger entry")
        if entry.get("legal_ownership_inferred") is not False:
            errors.append(f"{change_set_id}: legal_ownership_inferred must be false")
        if entry.get("third_party_code_introduced") is not False:
            errors.append(f"{change_set_id}: third-party code introduction requires separate Stage 4-7 clearance")

        base = entry.get("base_sha")
        implementation = entry.get("implementation_sha")
        manifest_rel = entry.get("manifest_path")
        if not isinstance(base, str) or len(base) != 40:
            errors.append(f"{change_set_id}: base_sha must be a full SHA")
        if not isinstance(implementation, str) or len(implementation) != 40:
            errors.append(f"{change_set_id}: implementation_sha must be a full SHA")
        if not isinstance(manifest_rel, str) or not manifest_rel.startswith(str(evidence_prefix)):
            errors.append(f"{change_set_id}: manifest_path must be under the Stage 8 evidence prefix")
            continue
        manifest_path = root / manifest_rel
        if not manifest_path.is_file():
            errors.append(f"{change_set_id}: manifest missing: {manifest_rel}")
            continue
        try:
            manifest = _json(manifest_path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{change_set_id}: manifest invalid: {exc}")
            continue
        if not isinstance(manifest, dict) or manifest.get("schema") != "omnigenis-contribution-provenance-manifest-v1":
            errors.append(f"{change_set_id}: invalid manifest schema")
            continue
        for key in ("change_set_id", "base_sha", "implementation_sha", "origin_class", "assistance_class"):
            if manifest.get(key) != entry.get(key):
                errors.append(f"{change_set_id}: manifest/ledger mismatch for {key}")
        if manifest.get("human_direction") is not True:
            errors.append(f"{change_set_id}: manifest human_direction must be true")
        if manifest.get("third_party_code_introduced") is not False:
            errors.append(f"{change_set_id}: manifest cannot declare uncleared third-party code")
        files = manifest.get("files")
        if not isinstance(files, list):
            errors.append(f"{change_set_id}: manifest files must be a list")
            continue

        manifest_paths: set[str] = set()
        for record in files:
            if not isinstance(record, dict):
                errors.append(f"{change_set_id}: manifest file record must be an object")
                continue
            path = record.get("path")
            digest = record.get("sha256")
            if not isinstance(path, str) or not path or path in manifest_paths:
                errors.append(f"{change_set_id}: invalid or duplicate manifest path")
                continue
            manifest_paths.add(path)
            if not isinstance(digest, str) or len(digest) != 64:
                errors.append(f"{change_set_id}: invalid sha256 for {path}")
                continue
            if not isinstance(implementation, str) or len(implementation) != 40:
                continue
            blob = _git_blob(root, implementation, path)
            if blob is None:
                errors.append(f"{change_set_id}: {path} missing from implementation commit")
                continue
            if hashlib.sha256(blob).hexdigest() != digest:
                errors.append(f"{change_set_id}: sha256 mismatch for {path}")

        if isinstance(base, str) and len(base) == 40 and isinstance(implementation, str) and len(implementation) == 40:
            changed = _changed_paths(root, base, implementation)
            if changed is None:
                errors.append(f"{change_set_id}: cannot derive implementation diff")
            else:
                expected = {
                    path
                    for path in changed
                    if path not in {LEDGER_REL, str(runtime_lock)}
                    and not path.startswith(str(evidence_prefix))
                }
                if manifest_paths != expected:
                    missing = sorted(expected - manifest_paths)
                    extra = sorted(manifest_paths - expected)
                    errors.append(f"{change_set_id}: manifest diff mismatch missing={missing} extra={extra}")

    if base_sha:
        previous = _load_previous_ledger(root, base_sha)
        if previous is not None:
            prior_entries = previous.get("entries")
            if not isinstance(prior_entries, list):
                errors.append("base Stage 8 ledger entries are invalid")
            elif entries[: len(prior_entries)] != prior_entries:
                errors.append("Stage 8 ledger history is not append-only relative to PR base")
        latest = entries[-1] if isinstance(entries[-1], dict) else {}
        if latest.get("base_sha") != base_sha:
            errors.append("latest Stage 8 ledger entry base_sha does not match PR base")
        manifest_rel = latest.get("manifest_path")
        implementation = latest.get("implementation_sha")
        if isinstance(manifest_rel, str) and isinstance(implementation, str):
            pr_changed = _changed_paths(root, base_sha, "HEAD")
            implementation_changed = _changed_paths(root, base_sha, implementation)
            if pr_changed is None or implementation_changed is None:
                errors.append("cannot derive PR change-set coverage")
            else:
                allowed_control = {LEDGER_REL, str(runtime_lock), manifest_rel}
                unexpected = pr_changed - implementation_changed - allowed_control
                if unexpected:
                    errors.append(f"Stage 8 uncovered post-implementation paths: {sorted(unexpected)}")

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
    ledger = _json(ROOT / LEDGER_REL)
    print(f"PASS\tstage8_contribution_provenance\tentries={len(ledger['entries'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
