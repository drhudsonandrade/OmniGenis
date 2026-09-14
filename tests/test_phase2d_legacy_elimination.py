from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import scripts.project_identity_guard as identity_guard
from scripts.project_identity_guard import validate_project_identity

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "config" / "legacy_identity_ledger.json"
IDENTITY = ROOT / "config" / "project_identity.json"
LEGACY_WORD = "code" + "work"
EXPECTED_HISTORICAL_FILE_COUNT = 21


class Phase2DLegacyEliminationTest(unittest.TestCase):
    """Seal zero active legacy identity with exact immutable history."""

    def _make_repo(self, *, allow_exact_history: bool) -> tuple[Path, Path]:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        (root / "config").mkdir()
        (root / "docs" / "history").mkdir(parents=True)
        (root / "config/project_identity.json").write_bytes(IDENTITY.read_bytes())
        historical = root / "docs/history/old.md"
        historical.write_text(f"retired product: {LEGACY_WORD}\n", encoding="utf-8")
        history: dict[str, dict[str, str]] = {}
        if allow_exact_history:
            history["docs/history/old.md"] = {
                "sha256": hashlib.sha256(historical.read_bytes()).hexdigest(),
                "reason": "Immutable migration-history fixture.",
            }
        ledger = {
            "schema": "omnigenis-legacy-identity-ledger-v2",
            "baseline_commit": "pending",
            "phase": "2D",
            "control_metadata_paths": ["config/legacy_identity_ledger.json"],
            "scan_suffixes": [
                "", ".example", ".json", ".md", ".nf", ".py", ".service",
                ".sh", ".toml", ".ts", ".txt", ".yaml", ".yml",
            ],
            "historical_files": history,
            "entries": [
                {
                    "id": "product-word-active",
                    "matcher": {
                        "kind": "regex",
                        "value": rf"(?<!/)(?<![A-Za-z0-9_-]){LEGACY_WORD}(?![A-Za-z0-9_-])",
                    },
                    "replacement": "OmniGenis",
                    "retire_by": "2B",
                    "reason": "Legacy product word must be absent from active files.",
                    "locations": {},
                }
            ],
        }
        (root / "config/legacy_identity_ledger.json").write_text(
            json.dumps(ledger, indent=2) + "\n", encoding="utf-8"
        )
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "Phase2D Test"], cwd=root, check=True)
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=root, check=True)
        baseline = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        ledger_path = root / "config/legacy_identity_ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger["baseline_commit"] = baseline
        ledger_path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
        subprocess.run(["git", "add", "config/legacy_identity_ledger.json"], cwd=root, check=True)
        return root, historical

    @staticmethod
    def _validate_fixture(root: Path) -> list[str]:
        """Validate a temporary repository against its own committed baseline."""
        ledger = json.loads((root / "config/legacy_identity_ledger.json").read_text(encoding="utf-8"))
        with mock.patch.object(
            identity_guard, "PHASE2D_BASELINE_COMMIT", ledger["baseline_commit"]
        ):
            return validate_project_identity(root)

    def test_unallowlisted_historical_file_is_scanned(self) -> None:
        root, _historical = self._make_repo(allow_exact_history=False)
        errors = self._validate_fixture(root)
        self.assertTrue(any("unclassified legacy identity" in e for e in errors), errors)

    def test_exact_historical_file_hash_is_allowed(self) -> None:
        root, _historical = self._make_repo(allow_exact_history=True)
        self.assertEqual(self._validate_fixture(root), [])

    def test_historical_file_hash_drift_fails_closed(self) -> None:
        root, historical = self._make_repo(allow_exact_history=True)
        historical.write_text(
            historical.read_text(encoding="utf-8") + "drift\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", str(historical.relative_to(root))], cwd=root, check=True)
        errors = self._validate_fixture(root)
        self.assertTrue(any("historical allowlist drift" in e for e in errors), errors)

    def test_historical_hash_uses_staged_blob_not_restored_worktree(self) -> None:
        """Reject staged historical drift even when worktree bytes are restored."""
        root, historical = self._make_repo(allow_exact_history=True)
        original = historical.read_bytes()
        historical.write_bytes(original + b"staged-drift\n")
        subprocess.run(
            ["git", "add", str(historical.relative_to(root))], cwd=root, check=True
        )
        historical.write_bytes(original)
        errors = self._validate_fixture(root)
        self.assertTrue(any("historical allowlist drift" in e for e in errors), errors)
    def test_real_ledger_is_phase2d_and_has_zero_migrate_budgets(self) -> None:
        ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        self.assertEqual(ledger["schema"], "omnigenis-legacy-identity-ledger-v2")
        self.assertEqual(ledger["phase"], "2D")
        self.assertNotIn("historical_prefixes", ledger)
        for entry in ledger["entries"]:
            if entry.get("disposition", "migrate") == "migrate":
                self.assertEqual(entry["locations"], {}, entry["id"])

    def test_real_historical_allowlist_is_exact_and_current(self) -> None:
        ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        historical = ledger["historical_files"]
        self.assertEqual(len(historical), EXPECTED_HISTORICAL_FILE_COUNT)
        tracked = set(
            subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
        )
        for relative, record in historical.items():
            with self.subTest(path=relative):
                self.assertIn(relative, tracked)
                self.assertTrue(record["reason"])
                payload = (ROOT / relative).read_bytes()
                self.assertEqual(hashlib.sha256(payload).hexdigest(), record["sha256"])

    def test_scanner_uses_distinct_loop_targets_for_string_and_path_domains(self) -> None:
        """Keep historical string keys separate from tracked Path loop variables."""
        source = (ROOT / "scripts/project_identity_guard.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        scanners = [
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == "scan_legacy_identities"
        ]
        self.assertEqual(len(scanners), 1)
        scanner = scanners[0]
        targets: dict[str, str] = {}
        for loop in (node for node in ast.walk(scanner) if isinstance(node, ast.For)):
            source_name: str | None = None
            if isinstance(loop.iter, ast.Name):
                source_name = loop.iter.id
            elif (
                isinstance(loop.iter, ast.Call)
                and isinstance(loop.iter.func, ast.Attribute)
                and loop.iter.func.attr == "items"
                and isinstance(loop.iter.func.value, ast.Name)
            ):
                source_name = loop.iter.func.value.id
            if source_name not in {"historical", "repository_paths"}:
                continue
            if source_name == "historical":
                self.assertIsInstance(loop.target, ast.Tuple)
                first_target = loop.target.elts[0]
                self.assertIsInstance(first_target, ast.Name)
                targets[source_name] = first_target.id
            else:
                self.assertIsInstance(loop.target, ast.Name)
                targets[source_name] = loop.target.id
        self.assertEqual(
            targets,
            {
                "historical": "historical_relative",
                "repository_paths": "tracked_relative",
            },
        )


    def test_new_historical_allowlist_entry_cannot_self_authorize_after_baseline(self) -> None:
        """Reject history added after the independently reviewed Phase 2D baseline."""
        root, _historical = self._make_repo(allow_exact_history=True)
        baseline = json.loads((root / "config/legacy_identity_ledger.json").read_text(encoding="utf-8"))["baseline_commit"]
        added = root / "docs/history/added-after-baseline.md"
        added.write_text(f"retired product: {LEGACY_WORD}\n", encoding="utf-8")
        ledger_path = root / "config/legacy_identity_ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger["baseline_commit"] = baseline
        ledger["historical_files"]["docs/history/added-after-baseline.md"] = {
            "sha256": hashlib.sha256(added.read_bytes()).hexdigest(),
            "reason": "Attempted same-change historical authorization.",
        }
        ledger_path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        errors = self._validate_fixture(root)
        self.assertTrue(any("historical path absent from Phase 2D baseline" in e for e in errors), errors)

    def test_historical_hash_cannot_be_rebased_to_post_baseline_bytes(self) -> None:
        """Reject ledger hash updates that bless bytes changed after the reviewed baseline."""
        root, historical = self._make_repo(allow_exact_history=True)
        baseline = json.loads((root / "config/legacy_identity_ledger.json").read_text(encoding="utf-8"))["baseline_commit"]
        historical.write_text(f"retired product: {LEGACY_WORD} changed\n", encoding="utf-8")
        ledger_path = root / "config/legacy_identity_ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger["baseline_commit"] = baseline
        ledger["historical_files"]["docs/history/old.md"]["sha256"] = hashlib.sha256(
            historical.read_bytes()
        ).hexdigest()
        ledger_path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        errors = self._validate_fixture(root)
        self.assertTrue(any("historical baseline digest mismatch" in e for e in errors), errors)

    def test_preserve_historical_locations_remain_explicit(self) -> None:
        ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        preserved = {
            entry["id"]: entry["locations"]
            for entry in ledger["entries"]
            if entry.get("disposition") == "preserve_historical"
        }
        self.assertEqual(
            preserved,
            {
                "historical-runtime-zip": {"docs/GITHUB_MOBILE_IMPORT.md": 2},
                "historical-repository-pr-urls": {
                    "docs/POLICY_CODE_LANGUAGE_INVENTORY.md": 1,
                    "docs/REPORTING_CODE_LANGUAGE_INVENTORY.md": 1,
                },
            },
        )


if __name__ == "__main__":
    unittest.main()
