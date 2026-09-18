#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_third_party_registry import render_payload  # noqa: E402

SHA256 = re.compile(r"^[0-9a-f]{64}$")
REQUIRED = (
    "locks/conda-linux-64-resolution.json",
    "locks/conda-linux-64-explicit.txt",
    "locks/base-image-software.json",
    "locks/python-license-metadata.json",
    "locks/action-license-metadata.json",
    "locks/sbom-tool-lock.json",
    "config/third_party_software_registry.json",
)

def _load(root: Path, relative: str) -> dict[str, Any]:
    return json.loads((root / relative).read_text(encoding="utf-8"))

def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def collect_errors(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    for required_path in REQUIRED:
        if not (root / required_path).is_file():
            errors.append(f"Stage 4 required artifact missing: {required_path}")
    if errors:
        return errors

    runtime = _load(root, "locks/runtime-lock.json")
    conda = _load(root, "locks/conda-linux-64-resolution.json")
    base = _load(root, "locks/base-image-software.json")
    py_meta = _load(root, "locks/python-license-metadata.json")
    action_meta = _load(root, "locks/action-license-metadata.json")
    sbom = _load(root, "locks/sbom-tool-lock.json")
    registry = _load(root, "config/third_party_software_registry.json")

    if conda.get("schema") != "omnigenis-conda-resolution-v1":
        errors.append("Stage 4 Conda resolution schema mismatch")
    packages = conda.get("packages")
    if not isinstance(packages, list) or conda.get("package_count") != len(packages) or len(packages) < 150:
        errors.append("Stage 4 Conda transitive inventory is incomplete")
        packages = packages if isinstance(packages, list) else []
    identities: set[tuple[str, str, str]] = set()
    expected_explicit: list[str] = ["@EXPLICIT"]
    for item in packages:
        if not isinstance(item, dict):
            errors.append("Stage 4 Conda package record invalid")
            continue
        identity = (str(item.get("name")), str(item.get("version")), str(item.get("build")))
        if identity in identities:
            errors.append(f"Stage 4 duplicate Conda package identity: {identity}")
        identities.add(identity)
        digest = str(item.get("sha256") or "")
        url = str(item.get("url") or "")
        license_id = str(item.get("license") or "")
        if not SHA256.fullmatch(digest):
            errors.append(f"Stage 4 Conda SHA-256 invalid: {identity}")
        if not url.startswith("https://"):
            errors.append(f"Stage 4 Conda source URL is not HTTPS: {identity}")
        if not license_id or license_id == "UNKNOWN":
            errors.append(f"Stage 4 Conda license missing: {identity}")
        expected_explicit.append(f"{url}#sha256:{digest}")

    actual_explicit = (root / "locks/conda-linux-64-explicit.txt").read_text(
        encoding="utf-8"
    ).splitlines()
    if actual_explicit != expected_explicit:
        errors.append("Stage 4 explicit Conda lock differs from audited resolution")

    if base.get("schema") != "omnigenis-base-image-software-v1":
        errors.append("Stage 4 base-image inventory schema mismatch")
    if base.get("requested_image_reference") != runtime.get("base_image", {}).get("reference"):
        errors.append("Stage 4 base-image inventory does not match runtime-lock digest")
    base_components = base.get("components")
    if not isinstance(base_components, list) or base.get("component_count") != len(base_components) or len(base_components) < 90:
        errors.append("Stage 4 base-image package inventory is incomplete")
    elif any(not isinstance(item, dict) or not item.get("licenses") for item in base_components):
        errors.append("Stage 4 base-image package lacks license evidence")

    req_names: set[str] = set()
    req_versions: dict[str, str] = {}
    for line in (root / "reporting/requirements.txt").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^ \\]+)", line)
        if match:
            name = match.group(1).lower()
            req_names.add(name)
            req_versions[name] = match.group(2)
    py_packages = py_meta.get("packages")
    if not isinstance(py_packages, list):
        errors.append("Stage 4 Python license metadata is invalid")
    else:
        by_name = {
            str(item.get("name", "")).lower(): item
            for item in py_packages
            if isinstance(item, dict)
        }
        if set(by_name) != req_names:
            errors.append("Stage 4 Python license metadata coverage mismatch")
        for name, version in req_versions.items():
            item = by_name.get(name, {})
            if item.get("version") != version or not item.get("license"):
                errors.append(f"Stage 4 Python metadata mismatch: {name}")

    package_lock = _load(root, "mcp/package-lock.json")
    npm_packages = package_lock.get("packages")
    if not isinstance(npm_packages, dict):
        errors.append("Stage 4 npm package lock is invalid")
    else:
        missing = [
            path
            for path, meta in npm_packages.items()
            if path and (not isinstance(meta, dict) or not meta.get("license"))
        ]
        if missing:
            errors.append(f"Stage 4 npm licenses missing for {len(missing)} package entries")

    actions_lock = _load(root, "locks/actions-lock.json").get("actions")
    actions_meta = action_meta.get("actions")
    if not isinstance(actions_lock, dict) or not isinstance(actions_meta, list):
        errors.append("Stage 4 action metadata is invalid")
    else:
        by_action = {
            item.get("name"): item
            for item in actions_meta
            if isinstance(item, dict)
        }
        if set(by_action) != set(actions_lock):
            errors.append("Stage 4 GitHub Action license coverage mismatch")
        for name, locked in actions_lock.items():
            record = by_action.get(name, {})
            locked_sha = locked.get("sha")
            license_url = record.get("license_url")
            parsed_license_url = (
                urlsplit(license_url) if isinstance(license_url, str) else None
            )
            expected_prefix = f"/{name}/blob/{locked_sha}/"
            license_path = parsed_license_url.path if parsed_license_url else ""
            license_url_matches = (
                parsed_license_url is not None
                and parsed_license_url.scheme == "https"
                and parsed_license_url.netloc == "github.com"
                and license_path.startswith(expected_prefix)
                and len(license_path) > len(expected_prefix)
            )
            if (
                record.get("sha") != locked_sha
                or not record.get("license")
                or not license_url_matches
            ):
                errors.append(f"Stage 4 action license metadata mismatch: {name}")

    if (
        sbom.get("schema") != "omnigenis-sbom-tool-lock-v1"
        or sbom.get("tool") != "syft"
        or sbom.get("version") != "1.52.0"
        or sbom.get("archive_sha256") != "caeedb81fb0491615f1ebd1761e4145d41ee86dd2cc7bf80669f9f5ad9d6133d"
        or sbom.get("outputs", {}).get("cyclonedx_json") != "1.7"
        or sbom.get("outputs", {}).get("spdx_json") != "2.3"
    ):
        errors.append("Stage 4 SBOM tool lock mismatch")

    expected_registry = render_payload(root)
    actual_registry = (root / "config/third_party_software_registry.json").read_text(
        encoding="utf-8"
    )
    if expected_registry != actual_registry:
        errors.append("Stage 4 third-party software registry drift")
    summary = registry.get("summary")
    if not isinstance(summary, dict) or summary.get("license_clean_claim_allowed") is not False:
        errors.append("Stage 4 registry must explicitly forbid LICENSE-CLEAN claims")
    elif int(summary.get("component_records", 0)) < 400:
        errors.append("Stage 4 registry component coverage is unexpectedly small")
    elif int(summary.get("by_policy_status", {}).get("BLOCKED_BY_DEFAULT", 0)) <= 0:
        errors.append("Stage 4 registry lost known default-blocked license findings")

    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    if "ARG OMNIGENIS_CONDA_SPEC=locks/conda-linux-64-explicit.txt" not in dockerfile:
        errors.append("Stage 4 Dockerfile default is not the audited explicit Conda lock")
    if "ARG OMNIGENIS_CONDA_SPEC_FILE=conda-linux-64-explicit.txt" not in dockerfile:
        errors.append("Stage 4 Dockerfile default Conda spec filename is not the audited explicit lock")
    if "${OMNIGENIS_CONDA_SPEC}" not in dockerfile or "${OMNIGENIS_CONDA_SPEC_FILE}" not in dockerfile:
        errors.append("Stage 4 Dockerfile does not consume the selected Conda specification and filename")
    runtime_workflow = (root / ".github/workflows/genoma-ngs-runtime-gate.yml").read_text(
        encoding="utf-8"
    )
    if runtime_workflow.count("--build-arg OMNIGENIS_CONDA_SPEC=environment.yml") < 2:
        errors.append("Stage 4 runtime gate does not build the generated latest Conda candidate")
    if runtime_workflow.count("--build-arg OMNIGENIS_CONDA_SPEC_FILE=environment.yml") < 2:
        errors.append("Stage 4 runtime gate does not preserve YAML parsing for the latest candidate")
    if "npm prune --omit=dev --ignore-scripts" not in dockerfile:
        errors.append("Stage 4 Dockerfile does not prune npm development dependencies")

    compliance = runtime.get("compliance_locks")
    if not isinstance(compliance, dict):
        errors.append("Stage 4 runtime-lock compliance_locks missing")
    else:
        for key, record in compliance.items():
            if not isinstance(record, dict):
                errors.append(f"Stage 4 compliance lock invalid: {key}")
                continue
            artifact_relative = record.get("path")
            expected = record.get("sha256")
            if not isinstance(artifact_relative, str) or not isinstance(expected, str):
                errors.append(f"Stage 4 compliance lock path/hash invalid: {key}")
                continue
            path = root / artifact_relative
            if not path.is_file() or not SHA256.fullmatch(expected):
                errors.append(f"Stage 4 compliance lock path/hash invalid: {key}")
                continue
            if _sha256(path) != expected:
                errors.append(f"Stage 4 compliance lock hash mismatch: {key}")

    return errors

def main() -> int:
    errors = collect_errors(ROOT)
    if errors:
        for error in errors:
            print(f"FAIL\t{error}")
        return 1
    registry = _load(ROOT, "config/third_party_software_registry.json")
    summary = registry.get("summary")
    if not isinstance(summary, dict):
        print("FAIL\tStage 4 registry summary missing")
        return 1
    policy_status = summary.get("by_policy_status")
    if not isinstance(policy_status, dict):
        print("FAIL\tStage 4 registry policy summary missing")
        return 1
    print(
        "PASS\tstage4_third_party_inventory\t"
        f"components={summary.get('component_records')} "
        f"blocked_default={policy_status.get('BLOCKED_BY_DEFAULT')} "
        f"review_required={policy_status.get('REVIEW_REQUIRED')}"
    )
    return 0

if __name__ == "__main__":
    sys.exit(main())
