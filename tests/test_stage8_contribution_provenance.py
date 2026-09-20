from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import build_stage8_change_manifest as stage8_builder
from scripts import validate_stage8_contribution_provenance as stage8


class Stage8ContributionProvenanceTests(unittest.TestCase):
    @staticmethod
    def _policy() -> dict[str, object]:
        return json.loads((stage8.ROOT / stage8.POLICY_REL).read_text(encoding="utf-8"))

    @staticmethod
    def _ledger() -> dict[str, object]:
        return {
            "schema": "omnigenis-contribution-provenance-ledger-v1",
            "append_only": True,
            "entries": [
                {
                    "change_set_id": "TEST-1",
                    "stage": 8,
                    "base_sha": "a" * 40,
                    "implementation_sha": "b" * 40,
                    "origin_class": "REPOSITORY_NATIVE",
                    "assistance_class": "AI_ASSISTED_DECLARED",
                    "human_direction": True,
                    "third_party_code_introduced": False,
                    "legal_ownership_inferred": False,
                    "declared_by_role": "repository_owner",
                    "declared_at": "2026-09-20",
                    "manifest_path": "docs/evidence/contribution_provenance/TEST-1.json",
                }
            ],
        }

    @staticmethod
    def _manifest() -> dict[str, object]:
        return {
            "schema": "omnigenis-contribution-provenance-manifest-v1",
            "change_set_id": "TEST-1",
            "base_sha": "a" * 40,
            "implementation_sha": "b" * 40,
            "origin_class": "REPOSITORY_NATIVE",
            "assistance_class": "AI_ASSISTED_DECLARED",
            "human_direction": True,
            "third_party_code_introduced": False,
            "hash_algorithm": "sha256",
            "files": [],
        }

    def _root(self, ledger: dict[str, object], manifest: dict[str, object]) -> Path:
        tmp = Path(tempfile.mkdtemp())
        (tmp / "config").mkdir()
        (tmp / "locks").mkdir()
        (tmp / "docs/evidence/contribution_provenance").mkdir(parents=True)
        (tmp / "config/contribution_provenance_policy.json").write_text(
            json.dumps(self._policy()), encoding="utf-8"
        )
        (tmp / "config/contribution_provenance_ledger.json").write_text(
            json.dumps(ledger), encoding="utf-8"
        )
        manifest_path = tmp / "docs/evidence/contribution_provenance/TEST-1.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        runtime = {
            "compliance_locks": {
                "test_manifest": {
                    "path": "docs/evidence/contribution_provenance/TEST-1.json",
                    "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                }
            }
        }
        (tmp / "locks/runtime-lock.json").write_text(json.dumps(runtime), encoding="utf-8")
        return tmp

    def test_valid_repository_native_entry_passes(self) -> None:
        errors = stage8.collect_errors(self._root(self._ledger(), self._manifest()))
        self.assertEqual(errors, [])

    def test_historical_unknown_origin_is_retained(self) -> None:
        ledger = self._ledger()
        manifest = self._manifest()
        ledger["entries"][0]["origin_class"] = "UNKNOWN"  # type: ignore[index]
        manifest["origin_class"] = "UNKNOWN"
        errors = stage8.collect_errors(self._root(ledger, manifest))
        self.assertFalse(any("UNKNOWN origin" in error for error in errors))

    def test_new_unknown_origin_fails_closed(self) -> None:
        ledger = self._ledger()
        manifest = self._manifest()
        ledger["entries"][0]["origin_class"] = "UNKNOWN"  # type: ignore[index]
        manifest["origin_class"] = "UNKNOWN"
        root = self._root(ledger, manifest)
        with (
            mock.patch.object(stage8, "_load_previous_ledger", return_value={"entries": []}),
            mock.patch.object(stage8, "_current_manifest_git_errors", return_value=[]),
        ):
            errors = stage8.collect_errors(root, base_sha="a" * 40)
        self.assertTrue(any("UNKNOWN origin" in error for error in errors))

    def test_repository_native_requires_human_direction(self) -> None:
        ledger = self._ledger()
        manifest = self._manifest()
        ledger["entries"][0]["human_direction"] = False  # type: ignore[index]
        manifest["human_direction"] = False
        errors = stage8.collect_errors(self._root(ledger, manifest))
        self.assertTrue(any("human_direction" in error for error in errors))

    def test_third_party_code_cannot_bypass_prior_gates(self) -> None:
        ledger = self._ledger()
        manifest = self._manifest()
        ledger["entries"][0]["third_party_code_introduced"] = True  # type: ignore[index]
        manifest["third_party_code_introduced"] = True
        errors = stage8.collect_errors(self._root(ledger, manifest))
        self.assertTrue(any("third-party code" in error for error in errors))

    def test_append_only_history_detects_rewrite(self) -> None:
        ledger = self._ledger()
        manifest = self._manifest()
        root = self._root(ledger, manifest)
        previous = copy.deepcopy(ledger)
        previous["entries"][0]["change_set_id"] = "ORIGINAL"  # type: ignore[index]
        with (
            mock.patch.object(stage8, "_load_previous_ledger", return_value=previous),
            mock.patch.object(stage8, "_changed_paths", return_value=set()),
        ):
            errors = stage8.collect_errors(root, base_sha="a" * 40)
        self.assertTrue(any("not append-only" in error for error in errors))


    def test_manifest_hash_algorithm_drift_is_rejected(self) -> None:
        manifest = self._manifest()
        manifest["hash_algorithm"] = "md5"
        errors = stage8.collect_errors(self._root(self._ledger(), manifest))
        self.assertTrue(any("hash_algorithm drift" in error for error in errors))

    def test_historical_manifest_does_not_require_git_objects(self) -> None:
        root = self._root(self._ledger(), self._manifest())
        with (
            mock.patch.object(stage8, "_git", side_effect=AssertionError("historical Git lookup")),
            mock.patch.object(stage8, "_git_bytes", side_effect=AssertionError("historical Git lookup")),
        ):
            self.assertEqual(stage8.collect_errors(root), [])

    def test_post_capture_edit_is_rejected_even_for_an_already_manifested_path(self) -> None:
        manifest = {
            "schema": stage8.CURRENT_MANIFEST_SCHEMA,
            "base_tree": "1" * 40,
            "implementation_tree": "2" * 40,
            "git_objects_verified_at_capture": True,
            "files": [],
        }
        with (
            mock.patch.object(stage8, "_tree_sha", side_effect=["1" * 40, "2" * 40]),
            mock.patch.object(stage8, "_is_ancestor", return_value=True),
            mock.patch.object(stage8, "_changed_file_records", return_value=[]),
            mock.patch.object(stage8, "_changed_paths", return_value={"scripts/example.py"}),
        ):
            errors = stage8._current_manifest_git_errors(
                Path("."),
                manifest=manifest,
                change_set_id="TEST",
                base_sha="a" * 40,
                implementation_sha="b" * 40,
                runtime_lock_rel="locks/runtime-lock.json",
                evidence_prefix="docs/evidence/contribution_provenance/",
                manifest_rel="docs/evidence/contribution_provenance/TEST.json",
            )
        self.assertTrue(any("uncovered post-implementation" in error for error in errors))

    def test_builder_represents_deleted_file_with_tombstone(self) -> None:
        with mock.patch.object(
            stage8_builder,
            "_blob_digest",
            side_effect=["a" * 64, None],
        ):
            record = stage8_builder._record_for_change(
                status="D",
                path="deleted.txt",
                base_sha="a" * 40,
                implementation_sha="b" * 40,
            )
        self.assertEqual(record["status"], "D")
        self.assertEqual(record["base_sha256"], "a" * 64)
        self.assertIsNone(record["sha256"])


if __name__ == "__main__":
    unittest.main()
