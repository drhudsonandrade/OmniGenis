#!/usr/bin/env python3
"""Build a deterministic Stage 8 contribution-provenance change-set manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "contribution_provenance_policy.json"


class ProvenanceBuildError(RuntimeError):
    """Raised when a deterministic change-set manifest cannot be built."""


def _run_git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise ProvenanceBuildError(proc.stderr.strip() or "git command failed")
    return proc.stdout


def _load_policy() -> dict[str, object]:
    payload = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ProvenanceBuildError("Stage 8 policy must be an object")
    return payload


def _changed_paths(base_sha: str, implementation_sha: str) -> list[str]:
    output = _run_git("diff", "--name-only", "--no-renames", f"{base_sha}..{implementation_sha}", "--")
    return sorted(path for path in output.splitlines() if path)


def _blob_bytes(commit_sha: str, path: str) -> bytes:
    proc = subprocess.run(
        ["git", "show", f"{commit_sha}:{path}"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise ProvenanceBuildError(f"cannot read {path!r} from {commit_sha}")
    return proc.stdout


def build_manifest(
    *,
    base_sha: str,
    implementation_sha: str,
    change_set_id: str,
    origin_class: str,
    assistance_class: str,
) -> dict[str, object]:
    policy = _load_policy()
    origin_classes = policy.get("origin_classes")
    assistance_classes = policy.get("assistance_classes")
    if not isinstance(origin_classes, list) or origin_class not in origin_classes:
        raise ProvenanceBuildError(f"unsupported origin class: {origin_class}")
    if not isinstance(assistance_classes, list) or assistance_class not in assistance_classes:
        raise ProvenanceBuildError(f"unsupported assistance class: {assistance_class}")

    control = policy.get("control_paths")
    if not isinstance(control, dict):
        raise ProvenanceBuildError("Stage 8 policy control_paths missing")
    ledger = str(control.get("ledger") or "")
    runtime_lock = str(control.get("runtime_lock") or "")
    evidence_prefix = str(control.get("evidence_prefix") or "")
    excluded = {ledger, runtime_lock}

    records: list[dict[str, str]] = []
    for path in _changed_paths(base_sha, implementation_sha):
        if path in excluded or (evidence_prefix and path.startswith(evidence_prefix)):
            continue
        data = _blob_bytes(implementation_sha, path)
        records.append({"path": path, "sha256": hashlib.sha256(data).hexdigest()})

    return {
        "schema": "omnigenis-contribution-provenance-manifest-v1",
        "change_set_id": change_set_id,
        "base_sha": base_sha,
        "implementation_sha": implementation_sha,
        "origin_class": origin_class,
        "assistance_class": assistance_class,
        "human_direction": True,
        "third_party_code_introduced": False,
        "hash_algorithm": "sha256",
        "files": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--implementation-sha", required=True)
    parser.add_argument("--change-set-id", required=True)
    parser.add_argument("--origin-class", default="REPOSITORY_NATIVE")
    parser.add_argument("--assistance-class", default="AI_ASSISTED_DECLARED")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    payload = build_manifest(
        base_sha=args.base_sha,
        implementation_sha=args.implementation_sha,
        change_set_id=args.change_set_id,
        origin_class=args.origin_class,
        assistance_class=args.assistance_class,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"PASS\tstage8_manifest\tfiles={len(payload['files'])}\timplementation={args.implementation_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
