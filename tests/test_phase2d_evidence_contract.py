from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_RELATIVE = (
    "docs/superpowers/evidence/2026-09-13-omnigenis-phase2d-legacy-elimination.json"
)
EVIDENCE = ROOT / EVIDENCE_RELATIVE
LEDGER = ROOT / "config/legacy_identity_ledger.json"
MERGE_SHA = "a7cb7f5559a83adc3c75f61284fecb09d1fb5553"
EXPECTED_HISTORICAL_FILES = 21
EXPECTED_RUNNERS = {21: "omnigenis-01", 22: "omnigenis-02"}
EXPECTED_CANARIES = {
    21: (34778271301, 103780369158),
    22: (34778271133, 103780390014),
}
EXPECTED_RULESETS = {
    21303100: ("GENOMA protected main", "active"),
    22347095: ("GENOMA approval gate", "active"),
}
GIT = shutil.which("git")
if GIT is None:
    raise RuntimeError("git is required by the Phase 2D evidence contract")


def _git(*args: str) -> str:
    return subprocess.check_output([GIT, *args], cwd=ROOT, text=True).strip()


class Phase2DEvidenceContractTest(unittest.TestCase):
    """Bind the repository-complete checkpoint without overstating Phase 2."""

    def load(self) -> dict:
        self.assertTrue(EVIDENCE.is_file(), f"missing evidence: {EVIDENCE}")
        return json.loads(EVIDENCE.read_text(encoding="utf-8"))
    def test_repository_checkpoint_is_verified_but_global_seal_is_blocked(self) -> None:
        evidence = self.load()
        self.assertEqual(evidence["schema"], "omnigenis-phase2d-legacy-elimination-v1")
        self.assertEqual(evidence["base_main_sha"], MERGE_SHA)
        self.assertEqual(evidence["repository_checkpoint_status"], "VERIFIED")
        self.assertEqual(evidence["phase2_global_seal"], "BLOCKED")
        blocker = evidence["runner_name_reregistration"]
        self.assertEqual(blocker["status"], "BLOCKED_RUNTIME_RESOURCE_GATE")
        self.assertEqual(
            blocker["missing_proofs"],
            [
                "container_creation_restart_mechanism",
                "container_image_digest",
                "container_mounts",
                "registration_workflow",
                "rollback_procedure",
            ],
        )
        self.assertEqual(
            blocker["authorized_executor_observations"],
            {
                "rootful_docker_socket": "DENIED",
                "rootless_docker_socket": "DENIED",
                "noninteractive_privilege_elevation": "DENIED_NO_NEW_PRIVILEGES",
                "runner_reregistration_attempted": False,
            },
        )
        self.assertFalse(evidence["secret_material_recorded"])

    def test_implementation_tree_and_evidence_only_child_are_bound(self) -> None:
        evidence = self.load()
        implementation = evidence["implementation_head_sha"]
        self.assertEqual(
            _git("rev-parse", f"{implementation}^{{tree}}"),
            evidence["implementation_tree_sha"],
        )
        self.assertEqual(_git("rev-parse", "HEAD^"), implementation)
        changed = _git(
            "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"
        ).splitlines()
        self.assertEqual(changed, [EVIDENCE_RELATIVE])
    def test_legacy_elimination_inventory_is_exact(self) -> None:
        evidence = self.load()
        inventory = evidence["legacy_inventory"]
        self.assertEqual(inventory["active_migrate_location_count"], 0)
        self.assertEqual(inventory["unclassified"], [])
        self.assertEqual(inventory["over_budget"], [])
        self.assertEqual(inventory["historical_drift"], [])
        self.assertEqual(inventory["historical_verified_count"], EXPECTED_HISTORICAL_FILES)
        ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        self.assertEqual(len(ledger["historical_files"]), EXPECTED_HISTORICAL_FILES)
        self.assertEqual(
            evidence["legacy_ledger_sha256"],
            hashlib.sha256(LEDGER.read_bytes()).hexdigest(),
        )

    def test_protected_main_canaries_cover_both_canonical_runners(self) -> None:
        evidence = self.load()
        canaries = evidence["protected_main_canaries"]
        self.assertEqual(len(canaries), 2)
        by_runner = {int(item["runner_id"]): item for item in canaries}
        self.assertEqual(set(by_runner), set(EXPECTED_RUNNERS))
        for runner_id in EXPECTED_RUNNERS:
            item = by_runner[runner_id]
            self.assertEqual(
                (item["run_id"], item["job_id"]), EXPECTED_CANARIES[runner_id]
            )
            self.assertEqual(item["commit_sha"], MERGE_SHA)
            self.assertEqual(item["conclusion"], "success")
            self.assertEqual(item["selector_labels"], ["omnigenis-isolated"])
            self.assertNotIn("runner_name", item)

    def test_governance_rulesets_are_still_active_without_semantic_overclaim(self) -> None:
        evidence = self.load()
        rulesets = evidence["rulesets"]
        by_id = {int(item["id"]): item for item in rulesets}
        self.assertEqual(set(by_id), set(EXPECTED_RULESETS))
        for ruleset_id, (name, enforcement) in EXPECTED_RULESETS.items():
            self.assertEqual(by_id[ruleset_id]["name"], name)
            self.assertEqual(by_id[ruleset_id]["enforcement"], enforcement)
            self.assertNotIn("semantic_sha256", by_id[ruleset_id])
        self.assertEqual(evidence["ruleset_semantic_recomputation"], "NOT_CLAIMED")

    def test_live_runner_snapshot_is_canonical_and_name_neutral(self) -> None:
        evidence = self.load()
        snapshot = evidence["runner_snapshot"]
        self.assertEqual(len(snapshot), 2)
        by_id = {int(item["id"]): item for item in snapshot}
        self.assertEqual(set(by_id), set(EXPECTED_RUNNERS))
        for runner_id, per_runner_label in EXPECTED_RUNNERS.items():
            item = by_id[runner_id]
            self.assertEqual(item["status"], "online")
            self.assertFalse(item["busy"])
            self.assertEqual(
                set(item["labels"]),
                {"self-hosted", "Linux", "X64", "omnigenis-isolated", per_runner_label},
            )
            self.assertNotIn("runner_name", item)


if __name__ == "__main__":
    unittest.main()
