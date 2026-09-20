#!/usr/bin/env python3
"""Build a deterministic Stage 8 contribution-provenance change-set manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "contribution_provenance_policy.json"
CURRENT_MANIFEST_SCHEMA = "omnigenis-contribution-provenance-manifest-v2"
SUPPORTED_CHANGE_STATUSES = frozenset({"A", "M", "D", "T"})


class ProvenanceBuildError(RuntimeError):
    """Raised when a deterministic change-set manifest cannot be built."""


def _git_executable() -> str:
    """Resolve Git explicitly before launching subprocesses."""
    executable = shutil.which("git")
    if executable is None:
        raise ProvenanceBuildError("git executable unavailable")
    return executable


def _run_git(*args: str) -> str:
    """Run a textual Git command in the repository and return stdout."""
    proc = subprocess.run(
        [_git_executable(), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise ProvenanceBuildError(proc.stderr.strip() or "git command failed")
    return proc.stdout


def _run_git_bytes(*args: str) -> bytes:
    """Run a byte-preserving Git command in the repository and return stdout."""
    proc = subprocess.run(
        [_git_executable(), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if proc.returncode != 0:
        message = proc.stderr.decode("utf-8", errors="replace").strip()
        raise ProvenanceBuildError(message or "git command failed")
    return proc.stdout


def _load_policy() -> dict[str, object]:
    """Load the active Stage 8 provenance policy."""
    payload = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ProvenanceBuildError("Stage 8 policy must be an object")
    return payload


def _changed_file_records(base_sha: str, implementation_sha: str) -> list[tuple[str, str]]:
    """Return deterministic status/path records with rename detection disabled."""
    raw = _run_git_bytes(
        "diff",
        "--name-status",
        "--no-renames",
        "-z",
        f"{base_sha}..{implementation_sha}",
        "--",
    )
    parts = raw.split(b"\0")
    if parts and parts[-1] == b"":
        parts.pop()
    if len(parts) % 2:
        raise ProvenanceBuildError("Git name-status output is truncated")
    records: list[tuple[str, str]] = []
    for index in range(0, len(parts), 2):
        status = parts[index].decode("ascii", errors="strict")
        path = parts[index + 1].decode("utf-8", errors="surrogateescape")
        if status not in SUPPORTED_CHANGE_STATUSES:
            raise ProvenanceBuildError(f"unsupported Git change status: {status!r}")
        records.append((status, path))
    return sorted(records, key=lambda item: (item[1], item[0]))


def _blob_digest(commit_sha: str, path: str) -> str | None:
    """Return a blob SHA-256, or None when the path is absent at that commit."""
    proc = subprocess.run(
        [_git_executable(), "show", f"{commit_sha}:{path}"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    return hashlib.sha256(proc.stdout).hexdigest()


def _tree_sha(commit_sha: str) -> str:
    """Return the immutable Git tree identity for a commit."""
    value = _run_git("rev-parse", f"{commit_sha}^{{tree}}").strip()
    if len(value) != 40:
        raise ProvenanceBuildError(f"invalid tree identity for {commit_sha}")
    return value


def _record_for_change(
    *,
    status: str,
    path: str,
    base_sha: str,
    implementation_sha: str,
) -> dict[str, str | None]:
    """Build one durable file-state transition record, including tombstones."""
    base_digest = _blob_digest(base_sha, path)
    implementation_digest = _blob_digest(implementation_sha, path)
    if status == "A":
        if base_digest is not None or implementation_digest is None:
            raise ProvenanceBuildError(f"{path}: invalid added-file transition")
    elif status == "D":
        if base_digest is None or implementation_digest is not None:
            raise ProvenanceBuildError(f"{path}: invalid deleted-file transition")
    elif base_digest is None or implementation_digest is None:
        raise ProvenanceBuildError(f"{path}: modified/type-changed path lacks one side")
    return {
        "path": path,
        "status": status,
        "base_sha256": base_digest,
        "sha256": implementation_digest,
    }


def build_manifest(
    *,
    base_sha: str,
    implementation_sha: str,
    change_set_id: str,
    origin_class: str,
    assistance_class: str,
    third_party_component_ids: list[str] | None = None,
) -> dict[str, object]:
    """Capture a self-contained, hash-addressed change-set manifest from Git objects."""
    policy = _load_policy()
    origin_classes = policy.get("origin_classes")
    assistance_classes = policy.get("assistance_classes")
    if not isinstance(origin_classes, list) or origin_class not in origin_classes:
        raise ProvenanceBuildError(f"unsupported origin class: {origin_class}")
    if not isinstance(assistance_classes, list) or assistance_class not in assistance_classes:
        raise ProvenanceBuildError(f"unsupported assistance class: {assistance_class}")

    rules = policy.get("rules")
    if not isinstance(rules, dict):
        raise ProvenanceBuildError("Stage 8 policy rules missing")
    if rules.get("manifest_hash_algorithm") != "sha256":
        raise ProvenanceBuildError("Stage 8 manifest hash algorithm must be sha256")
    if rules.get("current_manifest_schema") != CURRENT_MANIFEST_SCHEMA:
        raise ProvenanceBuildError("Stage 8 current manifest schema drift")

    raw_components = third_party_component_ids or []
    if any(not isinstance(value, str) or not value.strip() for value in raw_components):
        raise ProvenanceBuildError("third-party component IDs must be non-empty strings")
    components = sorted(set(raw_components))

    control = policy.get("control_paths")
    if not isinstance(control, dict):
        raise ProvenanceBuildError("Stage 8 policy control_paths missing")
    ledger = str(control.get("ledger") or "")
    runtime_lock = str(control.get("runtime_lock") or "")
    evidence_prefix = str(control.get("evidence_prefix") or "")
    excluded = {ledger, runtime_lock}

    records: list[dict[str, str | None]] = []
    for status, path in _changed_file_records(base_sha, implementation_sha):
        if path in excluded or (evidence_prefix and path.startswith(evidence_prefix)):
            continue
        records.append(
            _record_for_change(
                status=status,
                path=path,
                base_sha=base_sha,
                implementation_sha=implementation_sha,
            )
        )

    return {
        "schema": CURRENT_MANIFEST_SCHEMA,
        "change_set_id": change_set_id,
        "base_sha": base_sha,
        "implementation_sha": implementation_sha,
        "base_tree": _tree_sha(base_sha),
        "implementation_tree": _tree_sha(implementation_sha),
        "origin_class": origin_class,
        "assistance_class": assistance_class,
        "human_direction": True,
        "third_party_code_introduced": bool(components),
        "third_party_component_ids": components,
        "hash_algorithm": "sha256",
        "git_objects_verified_at_capture": True,
        "files": records,
    }


def main() -> int:
    """Build and persist one deterministic Stage 8 manifest."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--implementation-sha", required=True)
    parser.add_argument("--change-set-id", required=True)
    parser.add_argument("--origin-class", default="REPOSITORY_NATIVE")
    parser.add_argument("--assistance-class", default="AI_ASSISTED_DECLARED")
    parser.add_argument("--third-party-component-id", action="append", default=[])
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    payload = build_manifest(
        base_sha=args.base_sha,
        implementation_sha=args.implementation_sha,
        change_set_id=args.change_set_id,
        origin_class=args.origin_class,
        assistance_class=args.assistance_class,
        third_party_component_ids=args.third_party_component_id,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    file_records = payload.get("files")
    if not isinstance(file_records, list):
        raise ProvenanceBuildError("generated manifest files must be a list")
    print(
        "PASS\tstage8_manifest\t"
        f"schema={CURRENT_MANIFEST_SCHEMA}\tfiles={len(file_records)}\t"
        f"implementation={args.implementation_sha}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
