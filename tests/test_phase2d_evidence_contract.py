from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

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
    """Run a read-only Git command in the repository under test."""
    return subprocess.check_output([GIT, *args], cwd=ROOT, text=True).strip()


def _resolve_evidence_commit(implementation: str) -> str:
    """Resolve the evidence-only child across branch, PR merge-ref, and main history."""
    expected = EVIDENCE.read_bytes()
    candidates: list[str] = []
    for line in _git("rev-list", "--parents", "HEAD").splitlines():
        fields = line.split()
        commit, parents = fields[0], fields[1:]
        if parents != [implementation]:
            continue
        changed = _git(
            "diff-tree", "--no-commit-id", "--name-only", "-r", commit
        ).splitlines()
        if changed != [EVIDENCE_RELATIVE]:
            continue
        committed = subprocess.check_output(
            [GIT, "show", f"{commit}:{EVIDENCE_RELATIVE}"], cwd=ROOT
        )
        if committed == expected:
            candidates.append(commit)
    if len(candidates) != 1:
        raise AssertionError(
            "expected exactly one reachable Phase 2D evidence-only child"
        )
    evidence_commit = candidates[0]
    head = _git("rev-parse", "HEAD")
    if head == evidence_commit:
        return evidence_commit
    head_fields = _git("rev-list", "--parents", "-n", "1", "HEAD").split()
    if len(head_fields) == 3 and head_fields[2] == evidence_commit:
        return evidence_commit
    first_parent_merges = []
    for line in _git(
        "rev-list", "--first-parent", "--merges", "--parents", "HEAD"
    ).splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[2] == evidence_commit:
            first_parent_merges.append(fields[0])
    if len(first_parent_merges) == 1:
        return evidence_commit
    raise AssertionError(
        "Phase 2D evidence must be HEAD, HEAD's second parent, or the second "
        "parent of one merge on HEAD's first-parent history"
    )


class Phase2DEvidenceContractTest(unittest.TestCase):
    """Bind the repository-complete checkpoint without overstating Phase 2."""

    def load(self) -> dict:
        """Load the required Phase 2D evidence artifact."""
        self.assertTrue(EVIDENCE.is_file(), f"missing evidence: {EVIDENCE}")
        return json.loads(EVIDENCE.read_text(encoding="utf-8"))
    def test_repository_checkpoint_is_verified_but_global_seal_is_blocked(self) -> None:
        """Keep the repository checkpoint verified while the global runner seal is blocked."""
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
        """Bind evidence to the implementation and reviewed PR #71 baseline."""
        evidence = self.load()
        implementation = evidence["implementation_head_sha"]
        self.assertEqual(
            _git("rev-parse", f"{implementation}^{{tree}}"),
            evidence["implementation_tree_sha"],
        )
        ancestry = subprocess.run(
            [GIT, "merge-base", "--is-ancestor", MERGE_SHA, implementation],
            cwd=ROOT,
            check=False,
        )
        self.assertEqual(ancestry.returncode, 0)
        evidence_commit = _resolve_evidence_commit(implementation)
        self.assertEqual(_git("rev-parse", f"{evidence_commit}^"), implementation)
        changed = _git(
            "diff-tree", "--no-commit-id", "--name-only", "-r", evidence_commit
        ).splitlines()
        self.assertEqual(changed, [EVIDENCE_RELATIVE])
    def test_evidence_binding_accepts_pr_merge_ref(self) -> None:
        """Accept a PR merge-ref whose second parent is the evidence-only child."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)

            def git(*args: str) -> str:
                return subprocess.check_output([GIT, *args], cwd=repo, text=True).strip()

            def run(*args: str) -> None:
                subprocess.run([GIT, *args], cwd=repo, check=True, capture_output=True)

            run("init", "-b", "main")
            run("config", "user.email", "test@example.invalid")
            run("config", "user.name", "Phase2D Evidence Test")
            (repo / "base.txt").write_text("base\n", encoding="utf-8")
            run("add", "base.txt")
            run("commit", "-m", "base")
            run("checkout", "-b", "feature")
            (repo / "implementation.txt").write_text("implementation\n", encoding="utf-8")
            run("add", "implementation.txt")
            run("commit", "-m", "implementation")
            implementation = git("rev-parse", "HEAD")
            evidence_relative = "docs/evidence.json"
            evidence_path = repo / evidence_relative
            evidence_path.parent.mkdir(parents=True)
            evidence_path.write_text('{"status":"verified"}\n', encoding="utf-8")
            run("add", evidence_relative)
            run("commit", "-m", "evidence")
            evidence_commit = git("rev-parse", "HEAD")
            run("checkout", "main")
            run("merge", "--no-ff", "feature", "-m", "synthetic PR merge ref")

            module = __name__
            with (
                mock.patch(f"{module}.ROOT", repo),
                mock.patch(f"{module}.EVIDENCE", evidence_path),
                mock.patch(f"{module}.EVIDENCE_RELATIVE", evidence_relative),
            ):
                self.assertEqual(_resolve_evidence_commit(implementation), evidence_commit)

    def test_evidence_binding_rejects_extra_feature_commit_before_merge(self) -> None:
        """Reject a branch that adds unvalidated work after its evidence child."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)

            def git(*args: str) -> str:
                return subprocess.check_output([GIT, *args], cwd=repo, text=True).strip()

            def run(*args: str) -> None:
                subprocess.run([GIT, *args], cwd=repo, check=True, capture_output=True)

            run("init", "-b", "main")
            run("config", "user.email", "test@example.invalid")
            run("config", "user.name", "Phase2D Evidence Test")
            (repo / "base.txt").write_text("base\n", encoding="utf-8")
            run("add", "base.txt")
            run("commit", "-m", "base")
            run("checkout", "-b", "feature")
            (repo / "implementation.txt").write_text("implementation\n", encoding="utf-8")
            run("add", "implementation.txt")
            run("commit", "-m", "implementation")
            implementation = git("rev-parse", "HEAD")
            evidence_relative = "docs/evidence.json"
            evidence_path = repo / evidence_relative
            evidence_path.parent.mkdir(parents=True)
            evidence_path.write_text('{"status":"verified"}\n', encoding="utf-8")
            run("add", evidence_relative)
            run("commit", "-m", "evidence")
            (repo / "later.txt").write_text("later\n", encoding="utf-8")
            run("add", "later.txt")
            run("commit", "-m", "later")
            run("checkout", "main")
            run("merge", "--no-ff", "feature", "-m", "merge feature")

            module = __name__
            with (
                mock.patch(f"{module}.ROOT", repo),
                mock.patch(f"{module}.EVIDENCE", evidence_path),
                mock.patch(f"{module}.EVIDENCE_RELATIVE", evidence_relative),
                self.assertRaises(AssertionError),
            ):
                _resolve_evidence_commit(implementation)

    def test_legacy_elimination_inventory_is_exact(self) -> None:
        """Require zero active migration budgets and an exact immutable-history count."""
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
        """Bind both post-merge canaries to stable runner IDs and canonical routing."""
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
        """Record live ruleset identity without claiming a semantic recomputation."""
        evidence = self.load()
        rulesets = evidence["rulesets"]
        by_id = {int(item["id"]): item for item in rulesets}
        self.assertEqual(set(by_id), set(EXPECTED_RULESETS))
        for ruleset_id, (name, enforcement) in EXPECTED_RULESETS.items():
            self.assertEqual(by_id[ruleset_id]["name"], name)
            self.assertEqual(by_id[ruleset_id]["enforcement"], enforcement)
            self.assertNotIn("semantic_sha256", by_id[ruleset_id])
        self.assertEqual(evidence["ruleset_semantic_recomputation"], "NOT_CLAIMED")

    def test_implementation_suite_provenance_is_replayable(self) -> None:
        """Require an executable suite command rather than a prose placeholder."""
        evidence = self.load()
        record = evidence["validation_provenance"]["implementation_suite_without_phase2d_evidence"]
        command = record["command"]
        self.assertNotIn("<", command)
        self.assertNotIn(">", command)
        self.assertIn("find tests", command)
        self.assertIn("test_phase2d_evidence_contract.py", command)
        self.assertEqual(record["exit_code"], 0)
        self.assertIn("Ran ", record["sanitized_output"])
        self.assertIn("OK", record["sanitized_output"])

    def test_zero_identity_seal_and_class_counts_are_evidence_bound(self) -> None:
        """Bind the plan's zero-seal claim to executed evidence and explicit P1-P4 counts."""
        evidence = self.load()
        counts = evidence["zero_identity_class_counts"]
        self.assertEqual(set(counts), {"P1", "P2", "P3", "P4"})
        for class_id, record in counts.items():
            self.assertEqual(record, {"path": 0, "blob": 0}, class_id)
        seal = evidence["validation_provenance"]["zero_identity_seal"]
        self.assertEqual(seal["exit_code"], 0)
        self.assertIn("Ran ", seal["sanitized_output"])
        self.assertIn("OK", seal["sanitized_output"])

    def test_runner_snapshot_has_authenticated_readback_provenance(self) -> None:
        """Bind volatile runner state to repository identity, time, and sanitized API bytes."""
        evidence = self.load()
        self.assertEqual(evidence["repository_id"], 1212760346)
        captured_at = evidence["runner_snapshot_captured_at"]
        self.assertRegex(captured_at, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        provenance = evidence["runner_readback_provenance"]
        self.assertEqual(provenance["source"], "GitHub REST API")
        self.assertIn("repositories/1212760346", provenance["command"])
        self.assertIn("actions/runners", provenance["command"])
        sanitized = provenance["sanitized_output"]
        self.assertEqual(
            hashlib.sha256(sanitized.encode("utf-8")).hexdigest(),
            provenance["sanitized_output_sha256"],
        )
        self.assertEqual(json.loads(sanitized), evidence["runner_snapshot"])

    def test_live_runner_snapshot_is_canonical_and_name_neutral(self) -> None:
        """Require live canonical labels while keeping retired runner names out of evidence."""
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
