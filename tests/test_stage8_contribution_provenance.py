from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import validate_stage8_contribution_provenance as stage8


class Stage8ContributionProvenanceTests(unittest.TestCase):
    def _policy(self) -> dict[str, object]:
        return json.loads((stage8.ROOT / stage8.POLICY_REL).read_text(encoding="utf-8"))

    def _ledger(self) -> dict[str, object]:
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

    def _manifest(self) -> dict[str, object]:
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
        (tmp / "docs/evidence/contribution_provenance").mkdir(parents=True)
        (tmp / "config/contribution_provenance_policy.json").write_text(
            json.dumps(self._policy()), encoding="utf-8"
        )
        (tmp / "config/contribution_provenance_ledger.json").write_text(
            json.dumps(ledger), encoding="utf-8"
        )
        (tmp / "docs/evidence/contribution_provenance/TEST-1.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        return tmp

    @mock.patch.object(stage8, "_changed_paths", return_value=set())
    @mock.patch.object(stage8, "_git_blob", return_value=b"")
    def test_valid_repository_native_entry_passes(self, _blob: mock.Mock, _changed: mock.Mock) -> None:
        errors = stage8.collect_errors(self._root(self._ledger(), self._manifest()))
        self.assertEqual(errors, [])

    @mock.patch.object(stage8, "_changed_paths", return_value=set())
    def test_unknown_origin_fails_closed(self, _changed: mock.Mock) -> None:
        ledger = self._ledger()
        manifest = self._manifest()
        ledger["entries"][0]["origin_class"] = "UNKNOWN"  # type: ignore[index]
        manifest["origin_class"] = "UNKNOWN"
        errors = stage8.collect_errors(self._root(ledger, manifest))
        self.assertTrue(any("UNKNOWN origin" in error for error in errors))

    @mock.patch.object(stage8, "_changed_paths", return_value=set())
    def test_repository_native_requires_human_direction(self, _changed: mock.Mock) -> None:
        ledger = self._ledger()
        manifest = self._manifest()
        ledger["entries"][0]["human_direction"] = False  # type: ignore[index]
        manifest["human_direction"] = False
        errors = stage8.collect_errors(self._root(ledger, manifest))
        self.assertTrue(any("human_direction" in error for error in errors))

    @mock.patch.object(stage8, "_changed_paths", return_value=set())
    def test_third_party_code_cannot_bypass_prior_gates(self, _changed: mock.Mock) -> None:
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


if __name__ == "__main__":
    unittest.main()
