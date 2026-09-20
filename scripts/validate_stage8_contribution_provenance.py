#!/usr/bin/env python3
"""Validate Stage 8 technical authorship and contribution provenance."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY_REL = "config/contribution_provenance_policy.json"
LEDGER_REL = "config/contribution_provenance_ledger.json"
LEGACY_MANIFEST_SCHEMA = "omnigenis-contribution-provenance-manifest-v1"
CURRENT_MANIFEST_SCHEMA = "omnigenis-contribution-provenance-manifest-v2"
SUPPORTED_CHANGE_STATUSES = frozenset({"A", "M", "D", "T"})
SHA256_LENGTH = 64


def _json(path: Path) -> Any:
    """Load a UTF-8 JSON artifact."""
    return json.loads(path.read_text(encoding="utf-8"))


def _git_executable() -> str:
    """Resolve Git explicitly so subprocess calls avoid a partial executable path."""
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("git executable unavailable")
    return executable


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a textual Git command without raising."""
    return subprocess.run(
        [_git_executable(), *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )


def _git_bytes(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """Run a byte-preserving Git command without raising."""
    return subprocess.run(
        [_git_executable(), *args],
        cwd=root,
        check=False,
        capture_output=True,
    )


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 of an evidence artifact."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_blob_digest(root: Path, commit: str, path: str) -> str | None:
    """Return the SHA-256 of a Git blob, or None when the path is absent."""
    proc = _git_bytes(root, "show", f"{commit}:{path}")
    if proc.returncode != 0:
        return None
    return hashlib.sha256(proc.stdout).hexdigest()


def _changed_paths(root: Path, base_sha: str, head_sha: str = "HEAD") -> set[str] | None:
    """Return paths changed between two Git identities, or None when Git cannot derive them."""
    proc = _git(root, "diff", "--name-only", "--no-renames", f"{base_sha}..{head_sha}", "--")
    if proc.returncode != 0:
        return None
    return {line for line in proc.stdout.splitlines() if line}


def _changed_file_records(
    root: Path,
    base_sha: str,
    implementation_sha: str,
) -> list[tuple[str, str]] | None:
    """Return status/path records for the exact implementation diff."""
    proc = _git_bytes(
        root,
        "diff",
        "--name-status",
        "--no-renames",
        "-z",
        f"{base_sha}..{implementation_sha}",
        "--",
    )
    if proc.returncode != 0:
        return None
    parts = proc.stdout.split(b"\0")
    if parts and parts[-1] == b"":
        parts.pop()
    if len(parts) % 2:
        return None
    records: list[tuple[str, str]] = []
    for index in range(0, len(parts), 2):
        try:
            status = parts[index].decode("ascii", errors="strict")
            path = parts[index + 1].decode("utf-8", errors="surrogateescape")
        except UnicodeError:
            return None
        if status not in SUPPORTED_CHANGE_STATUSES:
            return None
        records.append((status, path))
    return sorted(records, key=lambda item: (item[1], item[0]))


def _tree_sha(root: Path, commit: str) -> str | None:
    """Return the Git tree identity for a commit when the object is available."""
    proc = _git(root, "rev-parse", f"{commit}^{{tree}}")
    value = proc.stdout.strip()
    return value if proc.returncode == 0 and len(value) == 40 else None


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    """Return whether one Git commit is reachable from another."""
    proc = _git(root, "merge-base", "--is-ancestor", ancestor, descendant)
    return proc.returncode == 0


def _load_previous_ledger(root: Path, base_sha: str) -> dict[str, Any] | None:
    """Load the ledger at a PR base; absence means the base predates Stage 8."""
    proc = _git(root, "show", f"{base_sha}:{LEDGER_REL}")
    if proc.returncode != 0:
        return None
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _runtime_manifest_locks(
    root: Path,
    runtime_lock_rel: str,
) -> tuple[dict[str, str], list[str]]:
    """Map every hash-locked compliance artifact path to its retained digest."""
    errors: list[str] = []
    runtime_path = root / runtime_lock_rel
    if not runtime_path.is_file():
        return {}, [f"Stage 8 runtime lock missing: {runtime_lock_rel}"]
    try:
        runtime = _json(runtime_path)
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"Stage 8 runtime lock invalid: {exc}"]
    records = runtime.get("compliance_locks") if isinstance(runtime, dict) else None
    if not isinstance(records, dict):
        return {}, ["Stage 8 runtime lock compliance_locks missing"]
    locks: dict[str, str] = {}
    for name, record in records.items():
        if not isinstance(record, dict):
            errors.append(f"Stage 8 compliance lock record invalid: {name}")
            continue
        path = record.get("path")
        digest = record.get("sha256")
        if not isinstance(path, str) or not isinstance(digest, str) or len(digest) != SHA256_LENGTH:
            errors.append(f"Stage 8 compliance lock identity invalid: {name}")
            continue
        previous = locks.get(path)
        if previous is not None and previous != digest:
            errors.append(f"Stage 8 conflicting compliance locks for {path}")
            continue
        locks[path] = digest
    return locks, errors


def _valid_sha256(value: object) -> bool:
    """Return whether value is a hexadecimal SHA-256 identity."""
    return isinstance(value, str) and len(value) == SHA256_LENGTH and all(
        char in "0123456789abcdefABCDEF" for char in value
    )


def _validate_legacy_records(
    change_set_id: str,
    files: list[object],
    errors: list[str],
) -> set[str]:
    """Validate retained v1 records without requiring their historical Git objects."""
    paths: set[str] = set()
    for record in files:
        if not isinstance(record, dict):
            errors.append(f"{change_set_id}: manifest file record must be an object")
            continue
        path = record.get("path")
        digest = record.get("sha256")
        if not isinstance(path, str) or not path or path in paths:
            errors.append(f"{change_set_id}: invalid or duplicate manifest path")
            continue
        paths.add(path)
        if not _valid_sha256(digest):
            errors.append(f"{change_set_id}: invalid sha256 for {path}")
    return paths


def _validate_v2_records(
    change_set_id: str,
    files: list[object],
    errors: list[str],
) -> dict[str, dict[str, object]]:
    """Validate durable v2 file transitions, including deletion tombstones."""
    records: dict[str, dict[str, object]] = {}
    for raw_record in files:
        if not isinstance(raw_record, dict):
            errors.append(f"{change_set_id}: manifest file record must be an object")
            continue
        path = raw_record.get("path")
        status = raw_record.get("status")
        base_digest = raw_record.get("base_sha256")
        implementation_digest = raw_record.get("sha256")
        if not isinstance(path, str) or not path or path in records:
            errors.append(f"{change_set_id}: invalid or duplicate manifest path")
            continue
        if status not in SUPPORTED_CHANGE_STATUSES:
            errors.append(f"{change_set_id}: unsupported change status for {path}")
            continue
        if status == "A":
            valid_transition = base_digest is None and _valid_sha256(implementation_digest)
        elif status == "D":
            valid_transition = _valid_sha256(base_digest) and implementation_digest is None
        else:
            valid_transition = _valid_sha256(base_digest) and _valid_sha256(implementation_digest)
        if not valid_transition:
            errors.append(f"{change_set_id}: invalid {status} digest transition for {path}")
            continue
        records[path] = raw_record
    return records


def _current_manifest_git_errors(
    root: Path,
    *,
    manifest: dict[str, Any],
    change_set_id: str,
    base_sha: str,
    implementation_sha: str,
    runtime_lock_rel: str,
    evidence_prefix: str,
    manifest_rel: str,
) -> list[str]:
    """Re-derive the latest PR manifest and reject any post-capture implementation edits."""
    errors: list[str] = []
    if manifest.get("schema") != CURRENT_MANIFEST_SCHEMA:
        return [f"{change_set_id}: latest PR manifest must use {CURRENT_MANIFEST_SCHEMA}"]

    base_tree = _tree_sha(root, base_sha)
    implementation_tree = _tree_sha(root, implementation_sha)
    if base_tree is None or implementation_tree is None:
        return [f"{change_set_id}: current PR Git objects are unavailable"]
    if manifest.get("base_tree") != base_tree:
        errors.append(f"{change_set_id}: base_tree mismatch")
    if manifest.get("implementation_tree") != implementation_tree:
        errors.append(f"{change_set_id}: implementation_tree mismatch")
    if manifest.get("git_objects_verified_at_capture") is not True:
        errors.append(f"{change_set_id}: Git-object capture verification missing")
    if not _is_ancestor(root, implementation_sha, "HEAD"):
        errors.append(f"{change_set_id}: implementation_sha is not reachable from current HEAD")

    raw_files = manifest.get("files")
    if not isinstance(raw_files, list):
        return errors + [f"{change_set_id}: manifest files must be a list"]
    manifest_records = _validate_v2_records(change_set_id, raw_files, errors)

    changed = _changed_file_records(root, base_sha, implementation_sha)
    if changed is None:
        errors.append(f"{change_set_id}: cannot derive implementation diff")
        return errors
    expected = {
        path: status
        for status, path in changed
        if path not in {LEDGER_REL, runtime_lock_rel}
        and not path.startswith(evidence_prefix)
    }
    if set(manifest_records) != set(expected):
        missing = sorted(set(expected) - set(manifest_records))
        extra = sorted(set(manifest_records) - set(expected))
        errors.append(f"{change_set_id}: manifest diff mismatch missing={missing} extra={extra}")
    for path, expected_status in expected.items():
        record = manifest_records.get(path)
        if record is None:
            continue
        if record.get("status") != expected_status:
            errors.append(
                f"{change_set_id}: status mismatch for {path}: "
                f"{record.get('status')!r} != {expected_status!r}"
            )
            continue
        base_digest = _git_blob_digest(root, base_sha, path)
        implementation_digest = _git_blob_digest(root, implementation_sha, path)
        if record.get("base_sha256") != base_digest:
            errors.append(f"{change_set_id}: base sha256 mismatch for {path}")
        if record.get("sha256") != implementation_digest:
            errors.append(f"{change_set_id}: implementation sha256 mismatch for {path}")

    post_capture = _changed_paths(root, implementation_sha, "HEAD")
    if post_capture is None:
        errors.append(f"{change_set_id}: cannot derive post-capture change set")
    else:
        allowed_control = {LEDGER_REL, runtime_lock_rel, manifest_rel}
        unexpected = post_capture - allowed_control
        if unexpected:
            errors.append(
                f"Stage 8 uncovered post-implementation paths: {sorted(unexpected)}"
            )
    return errors



def collect_errors(root: Path = ROOT, *, base_sha: str | None = None) -> list[str]:
    """Validate policy, durable evidence, append-only history and exact current PR coverage."""
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
        return ["invalid Stage 8 policy schema"]
    if policy.get("status") != "ACTIVE":
        errors.append("Stage 8 policy status must be ACTIVE")

    origin_classes = policy.get("origin_classes")
    assistance_classes = policy.get("assistance_classes")
    if not isinstance(origin_classes, list) or not all(
        isinstance(value, str) and value for value in origin_classes
    ):
        errors.append("Stage 8 origin_classes must be non-empty strings")
        origin_classes = []
    if not isinstance(assistance_classes, list) or not all(
        isinstance(value, str) and value for value in assistance_classes
    ):
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
        "retained_manifest_is_durable_evidence",
    ):
        if rules.get(key) is not True:
            errors.append(f"Stage 8 protected rule must remain true: {key}")
    if rules.get("legal_ownership_inference_from_git") is not False:
        errors.append("Stage 8 must prohibit legal ownership inference from Git")
    if rules.get("unknown_origin_machine_authorized") is not False:
        errors.append("Stage 8 UNKNOWN origin must fail closed for new entries")
    if rules.get("historical_git_object_required") is not False:
        errors.append("Stage 8 historical validation must not require ephemeral Git objects")
    hash_algorithm = rules.get("manifest_hash_algorithm")
    if hash_algorithm != "sha256":
        errors.append("Stage 8 manifest_hash_algorithm must be sha256")
    if rules.get("current_manifest_schema") != CURRENT_MANIFEST_SCHEMA:
        errors.append("Stage 8 current_manifest_schema drift")

    control = policy.get("control_paths")
    if not isinstance(control, dict):
        return errors + ["Stage 8 control_paths missing"]
    if control.get("ledger") != LEDGER_REL:
        errors.append("Stage 8 ledger control path drift")
    runtime_lock_rel = control.get("runtime_lock")
    evidence_prefix = control.get("evidence_prefix")
    if runtime_lock_rel != "locks/runtime-lock.json":
        errors.append("Stage 8 runtime-lock control path drift")
    if evidence_prefix != "docs/evidence/contribution_provenance/":
        errors.append("Stage 8 evidence prefix drift")
    if not isinstance(runtime_lock_rel, str) or not isinstance(evidence_prefix, str):
        return errors

    manifest_locks, lock_errors = _runtime_manifest_locks(root, runtime_lock_rel)
    errors.extend(lock_errors)

    if not isinstance(ledger, dict) or ledger.get("schema") != "omnigenis-contribution-provenance-ledger-v1":
        return errors + ["invalid Stage 8 ledger schema"]
    if ledger.get("append_only") is not True:
        errors.append("Stage 8 ledger must be append-only")
    entries = ledger.get("entries")
    if not isinstance(entries, list) or not entries:
        return errors + ["Stage 8 ledger must contain at least one entry"]

    prior_entries: list[Any] = []
    if base_sha:
        previous = _load_previous_ledger(root, base_sha)
        if previous is not None:
            candidate = previous.get("entries")
            if not isinstance(candidate, list):
                errors.append("base Stage 8 ledger entries are invalid")
            else:
                prior_entries = candidate
                if entries[: len(prior_entries)] != prior_entries:
                    errors.append("Stage 8 ledger history is not append-only relative to PR base")
    new_entry_indexes = set(range(len(prior_entries), len(entries))) if base_sha else set()

    seen: set[str] = set()
    manifests_by_index: dict[int, dict[str, Any]] = {}
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
        if entry.get("origin_class") == "UNKNOWN" and index in new_entry_indexes:
            errors.append(f"{change_set_id}: UNKNOWN origin is not machine-authorized for a new ledger entry")
        if entry.get("legal_ownership_inferred") is not False:
            errors.append(f"{change_set_id}: legal_ownership_inferred must be false")
        if entry.get("third_party_code_introduced") is not False:
            errors.append(
                f"{change_set_id}: third-party code introduction requires separate Stage 4-7 clearance"
            )

        base = entry.get("base_sha")
        implementation = entry.get("implementation_sha")
        manifest_rel = entry.get("manifest_path")
        if not isinstance(base, str) or len(base) != 40:
            errors.append(f"{change_set_id}: base_sha must be a full SHA")
        if not isinstance(implementation, str) or len(implementation) != 40:
            errors.append(f"{change_set_id}: implementation_sha must be a full SHA")
        if not isinstance(manifest_rel, str) or not manifest_rel.startswith(evidence_prefix):
            errors.append(f"{change_set_id}: manifest_path must be under the Stage 8 evidence prefix")
            continue
        manifest_path = root / manifest_rel
        if not manifest_path.is_file():
            errors.append(f"{change_set_id}: manifest missing: {manifest_rel}")
            continue
        actual_manifest_digest = _sha256_file(manifest_path)
        if manifest_locks.get(manifest_rel) != actual_manifest_digest:
            errors.append(f"{change_set_id}: manifest is not durably hash-locked")
        try:
            manifest = _json(manifest_path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{change_set_id}: manifest invalid: {exc}")
            continue
        if not isinstance(manifest, dict):
            errors.append(f"{change_set_id}: manifest must be an object")
            continue
        manifests_by_index[index] = manifest
        schema = manifest.get("schema")
        if schema not in {LEGACY_MANIFEST_SCHEMA, CURRENT_MANIFEST_SCHEMA}:
            errors.append(f"{change_set_id}: invalid manifest schema")
            continue
        for key in (
            "change_set_id",
            "base_sha",
            "implementation_sha",
            "origin_class",
            "assistance_class",
        ):
            if manifest.get(key) != entry.get(key):
                errors.append(f"{change_set_id}: manifest/ledger mismatch for {key}")
        if manifest.get("human_direction") is not True:
            errors.append(f"{change_set_id}: manifest human_direction must be true")
        if manifest.get("third_party_code_introduced") is not False:
            errors.append(f"{change_set_id}: manifest cannot declare uncleared third-party code")
        if manifest.get("hash_algorithm") != hash_algorithm:
            errors.append(f"{change_set_id}: manifest hash_algorithm drift")
        files = manifest.get("files")
        if not isinstance(files, list):
            errors.append(f"{change_set_id}: manifest files must be a list")
            continue
        if schema == LEGACY_MANIFEST_SCHEMA:
            _validate_legacy_records(change_set_id, files, errors)
        else:
            _validate_v2_records(change_set_id, files, errors)
    if base_sha:
        latest_index = len(entries) - 1
        latest = entries[latest_index] if isinstance(entries[latest_index], dict) else {}
        if latest.get("base_sha") != base_sha:
            errors.append("latest Stage 8 ledger entry base_sha does not match PR base")
        manifest_rel = latest.get("manifest_path")
        implementation = latest.get("implementation_sha")
        change_set_id = latest.get("change_set_id")
        manifest = manifests_by_index.get(latest_index)
        if (
            isinstance(manifest_rel, str)
            and isinstance(implementation, str)
            and isinstance(change_set_id, str)
            and isinstance(manifest, dict)
        ):
            errors.extend(
                _current_manifest_git_errors(
                    root,
                    manifest=manifest,
                    change_set_id=change_set_id,
                    base_sha=base_sha,
                    implementation_sha=implementation,
                    runtime_lock_rel=runtime_lock_rel,
                    evidence_prefix=evidence_prefix,
                    manifest_rel=manifest_rel,
                )
            )

    return errors


def main() -> int:
    """Run the Stage 8 validator and emit a compact verdict."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-sha")
    args = parser.parse_args()
    errors = collect_errors(ROOT, base_sha=args.base_sha)
    if errors:
        for error in errors:
            print(f"FAIL\t{error}")
        return 1
    ledger = _json(ROOT / LEDGER_REL)
    entries = ledger.get("entries") if isinstance(ledger, dict) else []
    count = len(entries) if isinstance(entries, list) else 0
    print(f"PASS\tstage8_contribution_provenance\tentries={count}\tdurable=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
