from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import scripts.project_identity_guard as identity_guard
from scripts.project_identity_guard import PHASE2D_BASELINE_COMMIT, validate_project_identity


ROOT = Path(__file__).resolve().parents[1]
LEGACY_WORD = "code" + "work"
LEGACY_RUNTIME_ROOT = "/opt/" + LEGACY_WORD
LEGACY_SURPRISE = LEGACY_WORD + "-surprise"
LEGACY_RUNNER_POOL = LEGACY_WORD + "-isolated"
LEGACY_CODERABBIT_BIN_ENV = LEGACY_WORD.upper() + "_CODERABBIT_BIN_DIR"

EXPECTED_SCAN_SUFFIXES = [
    "", ".example", ".json", ".md", ".nf", ".py", ".service",
    ".sh", ".toml", ".ts", ".txt", ".yaml", ".yml",
]

IDENTITY = {
    "schema": "omnigenis-project-identity-v1",
    "version": "2026-09-11.1",
    "repository": {"repository_id": 1212760346, "repository_name": "OmniGenis"},
    "runtime": {
        "root": "/opt/omnigenis",
        "config_root": "/etc/omnigenis",
        "state_root": "/var/lib/omnigenis-tunnel",
        "container_package": "omnigenis-genome",
        "cache_namespace": "omnigenis-genome-scaffold-v2",
        "tunnel_profile": "omnigenis-genome",
    },
    "mcp": {"identity": "omnigenis-genome-mcp"},
    "ngs": {
        "conda_environment": "omnigenis-ngs",
        "nextflow_manifest": "omnigenis/genome-runtime",
        "synthetic_fixture": "omnigenis-synthetic-germline-v2",
    },
    "runners": {
        "pool_label": "omnigenis-isolated",
        "per_runner_labels": ["omnigenis-01", "omnigenis-02"],
        "runner_names": ["omnigenis-runner-01", "omnigenis-runner-02"],
    },
    "codex": {
        "marketplace": "omnigenis-codex",
        "plugin": "coderabbit@omnigenis-codex",
        "bin_dir_env": "OMNIGENIS_CODERABBIT_BIN_DIR",
        "lock_schema": "omnigenis-coderabbit-cli-release-lock-v2",
    },
    "testing": {
        "temp_prefix_namespace": "omnigenis-",
        "runner_test_symbol": "private_omnigenis_runners",
    },
}


class ProjectIdentityGuardTest(unittest.TestCase):
    @staticmethod
    def validate_fixture(root: Path) -> list[str]:
        ledger_path = root / "config/legacy_identity_ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        historical = ledger.get("historical_files", {})
        membership = identity_guard._historical_path_set_sha256(historical)
        with mock.patch.object(
            identity_guard, "PHASE2D_HISTORICAL_PATHS_SHA256", membership
        ):
            return validate_project_identity(root)

    def make_repo(
        self,
        text: str,
        *,
        scan_suffixes: list[str] | None = None,
        locations: dict[str, int] | None = None,
        disposition: str = "migrate",
    ) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "config").mkdir()
        (root / "config/project_identity.json").write_text(
            json.dumps(IDENTITY), encoding="utf-8"
        )
        entry = {
            "id": "runtime-root",
            "matcher": {"kind": "literal", "value": LEGACY_RUNTIME_ROOT},
            "replacement": "/opt/omnigenis",
            "retire_by": "2B",
            "reason": "Legacy runtime-root test fixture.",
            "locations": {} if locations is None else locations,
        }
        if disposition != "migrate":
            entry["disposition"] = disposition
        ledger = {
            "schema": "omnigenis-legacy-identity-ledger-v2",
            "baseline_commit": PHASE2D_BASELINE_COMMIT,
            "phase": "2D",
            "control_metadata_paths": ["config/legacy_identity_ledger.json"],
            "scan_suffixes": EXPECTED_SCAN_SUFFIXES if scan_suffixes is None else scan_suffixes,
            "historical_files": {},
            "entries": [entry],
        }
        (root / "config/legacy_identity_ledger.json").write_text(
            json.dumps(ledger), encoding="utf-8"
        )
        (root / "active.txt").write_text(text, encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        return root



    def make_real_repo_subset(self, relative_paths: list[str]) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        required = [
            "config/project_identity.json",
            "config/legacy_identity_ledger.json",
            *relative_paths,
        ]
        for relative in required:
            source = ROOT / relative
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        ledger_path = root / "config/legacy_identity_ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger["historical_files"] = {}
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        return root

    def test_phase2d_rejects_active_compatibility_budget(self) -> None:
        errors = self.validate_fixture(
            self.make_repo(LEGACY_RUNTIME_ROOT, locations={"active.txt": 1})
        )
        self.assertTrue(
            any("Phase 2D migrate entry retains compatibility budget" in error for error in errors),
            errors,
        )

    def test_removing_a_preserved_historical_occurrence_is_allowed(self) -> None:
        self.assertEqual(
            self.validate_fixture(
                self.make_repo(
                    "clean",
                    locations={"active.txt": 1},
                    disposition="preserve_historical",
                )
            ),
            [],
        )

    def test_new_unclassified_legacy_identity_fails(self) -> None:
        errors = self.validate_fixture(self.make_repo(LEGACY_SURPRISE))
        self.assertTrue(any("unclassified legacy identity" in error for error in errors), errors)

    def test_preserve_historical_count_may_not_increase(self) -> None:
        errors = self.validate_fixture(
            self.make_repo(
                f"{LEGACY_RUNTIME_ROOT} {LEGACY_RUNTIME_ROOT}",
                locations={"active.txt": 1},
                disposition="preserve_historical",
            )
        )
        self.assertTrue(any("legacy occurrence count increased" in error for error in errors), errors)

    def test_preserve_historical_identity_may_not_move_to_unlisted_path(self) -> None:
        root = self.make_repo(
            LEGACY_RUNTIME_ROOT,
            locations={"active.txt": 1},
            disposition="preserve_historical",
        )
        (root / "moved.txt").write_text(LEGACY_RUNTIME_ROOT, encoding="utf-8")
        subprocess.run(["git", "add", "moved.txt"], cwd=root, check=True)
        errors = self.validate_fixture(root)
        self.assertTrue(any("unclassified legacy identity" in error for error in errors), errors)



    def test_control_metadata_ledger_is_not_scanned(self) -> None:
        root = self.make_repo("clean")
        errors = self.validate_fixture(root)
        self.assertEqual(errors, [])

    def test_untracked_file_does_not_affect_official_guard(self) -> None:
        root = self.make_repo("clean")
        (root / "local.txt").write_text(LEGACY_SURPRISE, encoding="utf-8")
        errors = self.validate_fixture(root)
        self.assertEqual(errors, [])

    def test_malformed_locations_fail_closed_without_raising(self) -> None:
        root = self.make_repo("clean")
        ledger_path = root / "config/legacy_identity_ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger["entries"][0]["locations"] = []
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
        subprocess.run(["git", "add", "config/legacy_identity_ledger.json"], cwd=root, check=True)
        errors = self.validate_fixture(root)
        self.assertTrue(
            any("legacy identity locations must be a mapping" in error for error in errors),
            errors,
        )

    def test_staged_control_contracts_override_restored_worktree(self) -> None:
        cases = (
            ("config/legacy_identity_ledger.json", "phase", "BROKEN", "legacy identity ledger must be in Phase 2D"),
            ("config/project_identity.json", "schema", "broken-schema", "project identity schema mismatch"),
        )
        for relative, field, invalid_value, expected in cases:
            with self.subTest(path=relative):
                root = self.make_repo("clean")
                path = root / relative
                original = path.read_bytes()
                payload = json.loads(original.decode("utf-8"))
                payload[field] = invalid_value
                path.write_text(json.dumps(payload), encoding="utf-8")
                subprocess.run(["git", "add", relative], cwd=root, check=True)
                path.write_bytes(original)
                errors = self.validate_fixture(root)
                self.assertTrue(any(expected in error for error in errors), errors)

    def test_ledger_entries_schema_fails_closed(self) -> None:
        cases = (
            (None, "legacy identity entries must be a list"),
            ([None], "legacy identity entry must be an object"),
        )
        for replacement, expected in cases:
            with self.subTest(entries=replacement):
                root = self.make_repo("clean")
                ledger_path = root / "config/legacy_identity_ledger.json"
                ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
                if replacement is None:
                    ledger.pop("entries", None)
                else:
                    ledger["entries"] = replacement
                ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
                subprocess.run(
                    ["git", "add", "config/legacy_identity_ledger.json"],
                    cwd=root, check=True,
                )
                errors = self.validate_fixture(root)
                self.assertTrue(any(expected in error for error in errors), errors)

    def test_zero_count_historical_pr_url_budget_cannot_reintroduce_identity(self) -> None:
        relative = "docs/POLICY_CODE_LANGUAGE_INVENTORY.md"
        root = self.make_real_repo_subset([relative])
        target = root / relative
        target.write_text(
            target.read_text(encoding="utf-8")
            + f"\nhttps://github.com/example/{LEGACY_WORD.capitalize()}/pull/60\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", relative], cwd=root, check=True)
        errors = self.validate_fixture(root)
        self.assertTrue(any("unclassified legacy identity" in error for error in errors), errors)

    def test_empty_scan_suffix_policy_fails_closed(self) -> None:
        root = self.make_repo("clean", scan_suffixes=[])
        errors = self.validate_fixture(root)
        self.assertTrue(any("scan suffix policy mismatch" in error for error in errors))

    def test_broad_historical_prefix_bypass_fails_closed(self) -> None:
        root = self.make_repo("clean")
        ledger_path = root / "config/legacy_identity_ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger["historical_prefixes"] = [""]
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
        subprocess.run(["git", "add", "config/legacy_identity_ledger.json"], cwd=root, check=True)
        errors = self.validate_fixture(root)
        self.assertTrue(
            any("broad historical prefix exemptions are forbidden" in error for error in errors),
            errors,
        )

    def test_invalid_utf8_tracked_file_fails_closed(self) -> None:
        root = self.make_repo("clean")
        (root / "invalid.txt").write_bytes(b"\xff\xfelegacy")
        subprocess.run(["git", "add", "invalid.txt"], cwd=root, check=True)
        errors = self.validate_fixture(root)
        self.assertTrue(any("legacy identity scan failed closed" in error for error in errors))


    def test_real_repository_has_no_unclassified_legacy_identity(self) -> None:
        self.assertEqual(validate_project_identity(ROOT), [])

    def test_real_ledger_rejects_new_unclassified_identity(self) -> None:
        root = self.make_real_repo_subset([])
        (root / "active.txt").write_text(LEGACY_WORD + "-new-identity", encoding="utf-8")
        subprocess.run(["git", "add", "active.txt"], cwd=root, check=True)
        errors = self.validate_fixture(root)
        self.assertTrue(any("unclassified legacy identity" in error for error in errors))

    def test_real_ledger_rejects_retired_compatibility_reintroduction(self) -> None:
        root = self.make_real_repo_subset(["scripts/codex/setup-coderabbit.sh"])
        script = root / "scripts/codex/setup-coderabbit.sh"
        script.write_text(
            script.read_text(encoding="utf-8") + f"\n# {LEGACY_CODERABBIT_BIN_ENV}\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "scripts/codex/setup-coderabbit.sh"], cwd=root, check=True)
        errors = self.validate_fixture(root)
        self.assertTrue(any("unclassified legacy identity" in error for error in errors), errors)

    def test_real_ledger_rejects_reviewed_identity_on_unlisted_path(self) -> None:
        root = self.make_real_repo_subset([])
        (root / "moved.txt").write_text(LEGACY_RUNTIME_ROOT, encoding="utf-8")
        subprocess.run(["git", "add", "moved.txt"], cwd=root, check=True)
        errors = self.validate_fixture(root)
        self.assertTrue(any("unclassified legacy identity" in error for error in errors))

    def test_ledger_replacement_must_be_canonical(self) -> None:
        root = self.make_repo(LEGACY_RUNTIME_ROOT)
        ledger_path = root / "config/legacy_identity_ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger["entries"][0]["replacement"] = "/opt/not-omnigenis"
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
        subprocess.run(["git", "add", "config/legacy_identity_ledger.json"], cwd=root, check=True)
        errors = self.validate_fixture(root)
        self.assertTrue(any("replacement is not canonical" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
