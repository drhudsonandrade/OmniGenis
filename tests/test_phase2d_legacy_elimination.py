from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

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
            "baseline_commit": "fixture",
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
                        "value": "(?<!/)(?<![A-Za-z0-9_-])Codework(?![A-Za-z0-9_-])",
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
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        return root, historical

    def test_unallowlisted_historical_file_is_scanned(self) -> None:
        root, _historical = self._make_repo(allow_exact_history=False)
        errors = validate_project_identity(root)
        self.assertTrue(any("unclassified legacy identity" in e for e in errors), errors)

    def test_exact_historical_file_hash_is_allowed(self) -> None:
        root, _historical = self._make_repo(allow_exact_history=True)
        self.assertEqual(validate_project_identity(root), [])

    def test_historical_file_hash_drift_fails_closed(self) -> None:
        root, historical = self._make_repo(allow_exact_history=True)
        historical.write_text(
            historical.read_text(encoding="utf-8") + "drift\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", str(historical.relative_to(root))], cwd=root, check=True)
        errors = validate_project_identity(root)
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
