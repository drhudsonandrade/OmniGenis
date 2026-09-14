#!/usr/bin/env python3
"""Validate the canonical OmniGenis identity contract and migration ledger."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
IDENTITY_PATH = Path("config/project_identity.json")
LEDGER_PATH = Path("config/legacy_identity_ledger.json")
LEGACY_PATTERN = re.compile("code" + "work", re.IGNORECASE)
CONTROL_METADATA_PATHS = {LEDGER_PATH}
LEDGER_SCHEMA = "omnigenis-legacy-identity-ledger-v2"
LEDGER_PHASE = "2D"
PHASE2D_BASELINE_COMMIT = "a7cb7f5559a83adc3c75f61284fecb09d1fb5553"
PHASE2D_SCAN_SUFFIXES = (
    "", ".example", ".json", ".md", ".nf", ".py", ".service",
    ".sh", ".toml", ".ts", ".txt", ".yaml", ".yml",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _flatten_strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _flatten_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _flatten_strings(child)


def _repository_paths(root: Path) -> tuple[Path, ...]:
    proc = subprocess.run(
        ["git", "ls-files", "-z", "--cached"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return tuple(
        Path(item)
        for item in proc.stdout.decode("utf-8").split("\0")
        if item
    )


def _read_index_regular_blob(root: Path, relative: Path) -> bytes:
    """Read the exact stage-0 regular-file blob for a tracked path."""
    relative_text = relative.as_posix()
    proc = subprocess.run(
        ["git", "ls-files", "-s", "-z", "--", relative_text],
        cwd=root,
        check=True,
        capture_output=True,
    )
    entries = [part for part in proc.stdout.split(b"\0") if part]
    if len(entries) != 1 or b"\t" not in entries[0]:
        raise ValueError(f"tracked index entry is ambiguous: {relative_text}")
    metadata, indexed_path = entries[0].split(b"\t", 1)
    fields = metadata.split()
    expected_path = relative_text.encode("utf-8")
    if len(fields) != 3 or indexed_path != expected_path or fields[2] != b"0":
        raise ValueError(f"tracked index entry is invalid: {relative_text}")
    mode, object_id, _stage = fields
    if not mode.startswith(b"100"):
        raise ValueError(f"tracked index entry is not a regular file: {relative_text}")
    return subprocess.check_output(
        ["git", "cat-file", "blob", object_id.decode("ascii")], cwd=root
    )


def _compile_matcher(entry: dict[str, Any]) -> re.Pattern[str]:
    matcher = entry["matcher"]
    kind = matcher["kind"]
    value = matcher["value"]
    if kind == "literal":
        return re.compile(re.escape(value))
    if kind == "regex":
        return re.compile(value)
    raise ValueError(f"unsupported matcher kind: {kind}")


def _validate_scope_policy(ledger: dict[str, Any]) -> None:
    if ledger.get("baseline_commit") != PHASE2D_BASELINE_COMMIT:
        raise ValueError("Phase 2D baseline commit mismatch")
    if ledger.get("scan_suffixes") != list(PHASE2D_SCAN_SUFFIXES):
        raise ValueError("legacy identity scan suffix policy mismatch")
    if ledger.get("control_metadata_paths") != [LEDGER_PATH.as_posix()]:
        raise ValueError("legacy identity control metadata policy mismatch")
    if "historical_prefixes" in ledger:
        raise ValueError("broad historical prefix exemptions are forbidden in Phase 2D")
    historical = ledger.get("historical_files")
    if not isinstance(historical, dict):
        raise ValueError("exact historical file allowlist is required in Phase 2D")
    for relative, record in historical.items():
        candidate = Path(relative) if isinstance(relative, str) else Path("/")
        if (
            not isinstance(relative, str)
            or not relative
            or candidate.is_absolute()
            or candidate.as_posix() != relative
            or ".." in candidate.parts
            or candidate == LEDGER_PATH
        ):
            raise ValueError("invalid historical allowlist path")
        if not isinstance(record, dict) or set(record) != {"sha256", "reason"}:
            raise ValueError(f"invalid historical allowlist record: {relative}")
        digest = record.get("sha256")
        reason = record.get("reason")
        if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
            raise ValueError(f"invalid historical allowlist SHA-256: {relative}")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"historical allowlist reason is required: {relative}")


def scan_legacy_identities(root: Path, ledger: dict[str, Any]) -> dict[str, Any]:
    _validate_scope_policy(ledger)
    suffixes = tuple(ledger["scan_suffixes"])
    historical: dict[str, dict[str, str]] = ledger["historical_files"]
    entries = ledger["entries"]
    report: dict[str, Any] = {
        "counts": {},
        "historical_drift": [],
        "historical_verified": [],
        "unclassified": [],
        "over_budget": [],
    }
    repository_paths = _repository_paths(root)
    tracked = {relative.as_posix() for relative in repository_paths}
    baseline_commit = ledger["baseline_commit"]
    for historical_relative, record in historical.items():
        if historical_relative not in tracked:
            report["historical_drift"].append(
                {"path": historical_relative, "reason": "allowlisted path is not tracked"}
            )
            continue
        try:
            baseline_payload = subprocess.check_output(
                ["git", "show", f"{baseline_commit}:{historical_relative}"],
                cwd=root,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            report["historical_drift"].append(
                {
                    "path": historical_relative,
                    "reason": "historical path absent from Phase 2D baseline",
                }
            )
            continue
        baseline_digest = hashlib.sha256(baseline_payload).hexdigest()
        if baseline_digest != record["sha256"]:
            report["historical_drift"].append(
                {"path": historical_relative, "reason": "historical baseline digest mismatch"}
            )

    for tracked_relative in repository_paths:
        posix = tracked_relative.as_posix()
        if tracked_relative in CONTROL_METADATA_PATHS:
            continue
        path = root / tracked_relative
        if posix in historical:
            record = historical[posix]
            try:
                staged_blob = _read_index_regular_blob(root, tracked_relative)
            except ValueError:
                report["historical_drift"].append(
                    {"path": posix, "reason": "allowlisted path is not a regular staged file"}
                )
                continue
            digest = hashlib.sha256(staged_blob).hexdigest()
            if digest != record["sha256"]:
                report["historical_drift"].append(
                    {"path": posix, "reason": "SHA-256 mismatch"}
                )
                continue
            report["historical_verified"].append(posix)
            continue
        if tracked_relative.suffix not in suffixes:
            continue
        text = _read_index_regular_blob(root, tracked_relative).decode("utf-8")
        covered: list[tuple[int, int]] = []
        for entry in entries:
            allowed = entry["locations"].get(posix)
            pattern = _compile_matcher(entry)
            matches = list(pattern.finditer(text))
            count = len(matches)
            if count:
                report["counts"].setdefault(entry["id"], {})[posix] = count
            if count and allowed is None:
                continue
            if allowed is not None and count > allowed:
                report["over_budget"].append(
                    {"id": entry["id"], "path": posix, "count": count, "max": allowed}
                )
            if allowed is not None:
                covered.extend((match.start(), match.end()) for match in matches)
        for match in LEGACY_PATTERN.finditer(text):
            if not any(start <= match.start() and match.end() <= end for start, end in covered):
                line = text.count("\n", 0, match.start()) + 1
                report["unclassified"].append({"path": posix, "line": line})
    return report


def validate_project_identity(root: Path) -> list[str]:
    errors: list[str] = []
    try:
        identity = _load_json(root / IDENTITY_PATH)
        ledger = _load_json(root / LEDGER_PATH)
    except (OSError, ValueError) as exc:
        return [f"project identity contract unreadable: {exc}"]

    if identity.get("schema") != "omnigenis-project-identity-v1":
        errors.append("project identity schema mismatch")
    if ledger.get("schema") != LEDGER_SCHEMA:
        errors.append("legacy identity ledger schema mismatch")
    if ledger.get("phase") != LEDGER_PHASE:
        errors.append("legacy identity ledger must be in Phase 2D")

    canonical = set(_flatten_strings(identity))
    for entry in ledger.get("entries", []):
        replacement = entry.get("replacement")
        disposition = entry.get("disposition", "migrate")
        locations = entry.get("locations")
        if not isinstance(locations, dict):
            errors.append(f"legacy identity locations must be a mapping: {entry.get('id')}")
            continue
        if disposition == "migrate":
            if replacement not in canonical:
                errors.append(f"legacy identity replacement is not canonical: {entry.get('id')}")
            if locations:
                errors.append(
                    f"Phase 2D migrate entry retains compatibility budget: {entry.get('id')}"
                )
        elif disposition != "preserve_historical":
            errors.append(f"unsupported legacy identity disposition: {entry.get('id')}")
        if not entry.get("reason") or not entry.get("retire_by"):
            errors.append(f"legacy identity entry lacks reason/retire_by: {entry.get('id')}")

    try:
        report = scan_legacy_identities(root, ledger)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        return errors + [f"legacy identity scan failed closed: {exc}"]
    for item in report["historical_drift"]:
        errors.append(
            f"historical allowlist drift: {item['path']}: {item['reason']}"
        )
    for item in report["unclassified"]:
        errors.append(
            f"unclassified legacy identity: {item['path']}:{item['line']}"
        )
    for item in report["over_budget"]:
        errors.append(
            "legacy occurrence count increased: "
            f"{item['id']} {item['path']} {item['count']}>{item['max']}"
        )
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--inventory", action="store_true")
    args = parser.parse_args()

    if args.inventory:
        try:
            ledger = _load_json(ROOT / LEDGER_PATH)
            report = scan_legacy_identities(ROOT, ledger)
        except (OSError, subprocess.CalledProcessError, ValueError) as exc:
            raise SystemExit(f"legacy identity inventory failed closed: {exc}") from exc
        print(json.dumps(report, indent=2, sort_keys=True))
        return

    errors = validate_project_identity(ROOT)
    if errors:
        for error in errors:
            print(f"FAIL\t{error}")
        raise SystemExit(1)
    print("PASS\tproject_identity_contract")


if __name__ == "__main__":
    main()
