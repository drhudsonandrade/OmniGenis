from __future__ import annotations

import copy
import gzip
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.governance_context_identity import match_expected_check

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_RELATIVE = (
    "docs/superpowers/evidence/2026-09-13-omnigenis-phase2d-legacy-elimination.json"
)
EVIDENCE = ROOT / EVIDENCE_RELATIVE
TRANSCRIPT_RELATIVE = (
    "docs/superpowers/evidence/2026-09-13-omnigenis-phase2d-implementation-suite.log.gz"
)
TRANSCRIPT = ROOT / TRANSCRIPT_RELATIVE
VALIDATION_BUNDLE_RELATIVE = (
    "docs/superpowers/evidence/2026-09-14-omnigenis-phase2d-validation-bundle.json"
)
VALIDATION_BUNDLE = ROOT / VALIDATION_BUNDLE_RELATIVE
LEDGER = ROOT / "config/legacy_identity_ledger.json"
RULESET_BASELINE = ROOT / "docs/superpowers/evidence/2026-09-10-omnigenis-repository-identity-migration.json"
RUNNER_NAME_BASELINE = ROOT / "docs/superpowers/evidence/2026-09-11-omnigenis-phase2c-runner-cutover.json"
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


def _canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _evidence_artifacts() -> tuple[tuple[str, Path], ...]:
    return (
        (EVIDENCE_RELATIVE, EVIDENCE),
        (TRANSCRIPT_RELATIVE, TRANSCRIPT),
        (VALIDATION_BUNDLE_RELATIVE, VALIDATION_BUNDLE),
    )


def _resolve_evidence_commit(implementation: str) -> str:
    """Resolve the evidence-only child across branch, PR merge-ref, and main history."""
    expected = {relative: path.read_bytes() for relative, path in _evidence_artifacts()}
    expected_paths = sorted(expected)
    candidates: list[str] = []
    for line in _git("rev-list", "--parents", "HEAD").splitlines():
        fields = line.split()
        commit, parents = fields[0], fields[1:]
        if parents != [implementation]:
            continue
        changed = _git(
            "diff-tree", "--no-commit-id", "--name-only", "-r", commit
        ).splitlines()
        if sorted(changed) != expected_paths:
            continue
        committed = {
            relative: subprocess.check_output([GIT, "show", f"{commit}:{relative}"], cwd=ROOT)
            for relative in expected_paths
        }
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


def _tree_without_evidence(commit: str) -> str:
    """Return a tree identity with Phase 2D evidence artifacts removed."""
    with tempfile.TemporaryDirectory() as td:
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(Path(td) / "index")
        subprocess.run(
            [GIT, "read-tree", f"{commit}^{{tree}}"],
            cwd=ROOT, env=env, check=True, capture_output=True,
        )
        for relative, _path in _evidence_artifacts():
            subprocess.run(
                [GIT, "update-index", "--force-remove", "--", relative],
                cwd=ROOT, env=env, check=True, capture_output=True,
            )
        return subprocess.check_output([GIT, "write-tree"], cwd=ROOT, env=env, text=True).strip()


def _resolve_evidence_delivery(implementation: str, payload_tree: str, base_main: str) -> tuple[str, str]:
    """Resolve commit-bound evidence or a flattened squash carrying the same payload."""
    try:
        return "evidence_commit", _resolve_evidence_commit(implementation)
    except AssertionError as original:
        fields = _git("rev-list", "--parents", "-n", "1", "HEAD").split()
        if len(fields) == 2:
            candidate = fields[0]
            candidate_parent = fields[1]
        elif len(fields) == 3:
            candidate = fields[2]
            candidate_fields = _git(
                "rev-list", "--parents", "-n", "1", candidate
            ).split()
            if len(candidate_fields) != 2:
                raise original
            candidate_parent = candidate_fields[1]
            first_parent_ancestry = subprocess.run(
                [GIT, "merge-base", "--is-ancestor", base_main, fields[1]],
                cwd=ROOT, check=False, capture_output=True,
            )
            if first_parent_ancestry.returncode != 0:
                raise original
        else:
            raise original
        ancestry = subprocess.run(
            [GIT, "merge-base", "--is-ancestor", base_main, candidate_parent],
            cwd=ROOT, check=False, capture_output=True,
        )
        if ancestry.returncode != 0 or _tree_without_evidence(candidate) != payload_tree:
            raise original
        try:
            committed = {
                relative: subprocess.check_output([GIT, "show", f"{candidate}:{relative}"], cwd=ROOT)
                for relative, _path in _evidence_artifacts()
            }
        except subprocess.CalledProcessError:
            raise original
        expected = {relative: path.read_bytes() for relative, path in _evidence_artifacts()}
        if committed != expected:
            raise original
        return "squashed_delivery", candidate



def _expected_ruleset_semantics(predecessor: dict) -> dict:
    """Apply the one authorized approval hardening delta to predecessor semantics."""
    expected = copy.deepcopy(predecessor["ruleset_semantics_post"])
    approval = expected["22347095"]
    pull_request_rules = [rule for rule in approval["rules"] if rule["type"] == "pull_request"]
    if len(pull_request_rules) != 1:
        raise AssertionError("approval baseline must contain exactly one pull_request rule")
    parameters = pull_request_rules[0]["parameters"]
    parameters["required_approving_review_count"] = 1
    parameters["require_last_push_approval"] = True
    return expected


def _neutralize_ruleset(raw: dict, expected: dict) -> dict:
    """Project authenticated provider JSON into the versioned neutral semantic shape."""
    normalized = {
        "id": raw["id"],
        "name": raw["name"],
        "enforcement": raw["enforcement"],
        "conditions": copy.deepcopy(raw["conditions"]),
        "bypass_actors": copy.deepcopy(raw["bypass_actors"]),
        "rules": copy.deepcopy(raw["rules"]),
        "required_status_contexts": [],
    }
    expected_status = [rule for rule in expected["rules"] if rule["type"] == "required_status_checks"]
    live_status = [rule for rule in normalized["rules"] if rule["type"] == "required_status_checks"]
    if not expected_status:
        if live_status:
            raise AssertionError("unexpected live required_status_checks rule")
        return normalized
    if len(expected_status) != 1 or len(live_status) != 1:
        raise AssertionError("required_status_checks rule cardinality mismatch")
    expected_checks = expected_status[0]["parameters"]["required_status_checks"]
    live_checks = live_status[0]["parameters"]["required_status_checks"]
    if len(expected_checks) != len(live_checks):
        raise AssertionError("required status check count mismatch")
    neutral_checks: list[dict] = []
    for expected_check in expected_checks:
        matches = [
            live_check
            for live_check in live_checks
            if match_expected_check(live_check, expected_check)
        ]
        if len(matches) != 1:
            raise AssertionError("required status check did not resolve uniquely")
        neutral_checks.append(copy.deepcopy(expected_check))
    live_status[0]["parameters"]["required_status_checks"] = neutral_checks
    normalized["required_status_contexts"] = [
        item["context"]
        if "context" in item
        else {"context_fingerprint": copy.deepcopy(item["context_fingerprint"])}
        for item in neutral_checks
    ]
    return normalized


class Phase2DEvidenceContractTest(unittest.TestCase):
    """Bind the repository-complete checkpoint without overstating Phase 2."""

    def load(self) -> dict:
        """Load the required Phase 2D evidence artifact."""
        self.assertTrue(EVIDENCE.is_file(), f"missing evidence: {EVIDENCE}")
        return json.loads(EVIDENCE.read_text(encoding="utf-8"))

    def load_validation_bundle(self, evidence: dict) -> dict:
        """Load the independently captured gate/ruleset validation bundle."""
        binding = evidence["validation_bundle"]
        self.assertEqual(binding["path"], VALIDATION_BUNDLE_RELATIVE)
        self.assertTrue(VALIDATION_BUNDLE.is_file(), f"missing validation bundle: {VALIDATION_BUNDLE}")
        raw = VALIDATION_BUNDLE.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), binding["sha256"])
        bundle = json.loads(raw.decode("utf-8"))
        self.assertEqual(bundle["schema"], "omnigenis-phase2d-validation-bundle-v1")
        return bundle
    def test_validation_bundle_payload_probe_is_independent_and_replayable(self) -> None:
        """Bind head/tree/payload claims to captured command output in the bundle."""
        evidence = self.load()
        bundle = self.load_validation_bundle(evidence)
        probe = bundle["payload_probe"]
        self.assertEqual(probe["exit_code"], 0)
        self.assertRegex(probe["captured_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertIn("git rev-parse HEAD", probe["command"])
        raw = probe["raw_output"]
        self.assertEqual(hashlib.sha256(raw.encode("utf-8")).hexdigest(), probe["raw_output_sha256"])
        lines = raw.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], bundle["validated_head_sha"])
        self.assertEqual(lines[1], bundle["validated_tree_sha"])
        self.assertEqual(lines[2], bundle["validated_payload_tree_sha"])
        self.assertEqual(lines[0], evidence["implementation_head_sha"])
        self.assertEqual(lines[1], evidence["implementation_tree_sha"])
        self.assertEqual(lines[2], evidence["implementation_payload_tree_sha"])

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
        """Bind evidence to the independently captured validated payload tree."""
        evidence = self.load()
        bundle = self.load_validation_bundle(evidence)
        implementation = evidence["implementation_head_sha"]
        payload_tree = bundle["validated_payload_tree_sha"]
        self.assertEqual(bundle["validated_head_sha"], implementation)
        self.assertEqual(bundle["validated_tree_sha"], evidence["implementation_tree_sha"])
        self.assertEqual(evidence["implementation_payload_tree_sha"], payload_tree)
        implementation_available = subprocess.run(
            [GIT, "cat-file", "-e", f"{implementation}^{{commit}}"],
            cwd=ROOT,
            check=False,
            capture_output=True,
        ).returncode == 0
        if implementation_available:
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
            self.assertEqual(_tree_without_evidence(implementation), payload_tree)
        mode, delivery = _resolve_evidence_delivery(implementation, payload_tree, MERGE_SHA)
        if mode == "evidence_commit":
            self.assertEqual(_git("rev-parse", f"{delivery}^"), implementation)
            changed = _git(
                "diff-tree", "--no-commit-id", "--name-only", "-r", delivery
            ).splitlines()
            self.assertEqual(
                sorted(changed), sorted(relative for relative, _path in _evidence_artifacts())
            )
        else:
            self.assertEqual(mode, "squashed_delivery")
            self.assertEqual(_tree_without_evidence(delivery), payload_tree)

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
            transcript_relative = "docs/evidence.log.gz"
            transcript_path = repo / transcript_relative
            transcript_path.write_bytes(gzip.compress(b"suite\n__OMNIGENIS_EXIT_CODE__=0\n", mtime=0))
            bundle_relative = "docs/validation-bundle.json"
            bundle_path = repo / bundle_relative
            bundle_path.write_text('{"schema":"fixture"}\n', encoding="utf-8")
            run("add", evidence_relative, transcript_relative, bundle_relative)
            run("commit", "-m", "evidence")
            evidence_commit = git("rev-parse", "HEAD")
            run("checkout", "main")
            run("merge", "--no-ff", "feature", "-m", "synthetic PR merge ref")

            module = __name__
            with (
                mock.patch(f"{module}.ROOT", repo),
                mock.patch(f"{module}.EVIDENCE", evidence_path),
                mock.patch(f"{module}.EVIDENCE_RELATIVE", evidence_relative),
                mock.patch(f"{module}.TRANSCRIPT", transcript_path),
                mock.patch(f"{module}.TRANSCRIPT_RELATIVE", transcript_relative),
                mock.patch(f"{module}.VALIDATION_BUNDLE", bundle_path),
                mock.patch(f"{module}.VALIDATION_BUNDLE_RELATIVE", bundle_relative),
            ):
                self.assertEqual(_resolve_evidence_commit(implementation), evidence_commit)

    def test_evidence_binding_accepts_flattened_squash_payload(self) -> None:
        """Accept a squash checkout after unreachable implementation objects are pruned."""
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
            base = git("rev-parse", "HEAD")
            run("checkout", "-b", "feature")
            (repo / "implementation.txt").write_text("implementation\n", encoding="utf-8")
            run("add", "implementation.txt")
            run("commit", "-m", "implementation")
            implementation = git("rev-parse", "HEAD")
            implementation_tree = git("rev-parse", f"{implementation}^{{tree}}")

            evidence_relative = "docs/evidence.json"
            transcript_relative = "docs/evidence.log.gz"
            bundle_relative = "docs/validation-bundle.json"
            evidence_path = repo / evidence_relative
            transcript_path = repo / transcript_relative
            bundle_path = repo / bundle_relative
            evidence_path.parent.mkdir(parents=True)
            module = __name__
            with (
                mock.patch(f"{module}.ROOT", repo),
                mock.patch(f"{module}.EVIDENCE_RELATIVE", evidence_relative),
                mock.patch(f"{module}.TRANSCRIPT_RELATIVE", transcript_relative),
                mock.patch(f"{module}.VALIDATION_BUNDLE_RELATIVE", bundle_relative),
            ):
                payload_tree = _tree_without_evidence(implementation)
            bundle_payload = {
                "schema": "omnigenis-phase2d-validation-bundle-v1",
                "validated_head_sha": implementation,
                "validated_tree_sha": implementation_tree,
                "validated_payload_tree_sha": payload_tree,
                "gates": {},
                "ruleset_readbacks": {},
            }
            bundle_path.write_bytes(_canonical_json_bytes(bundle_payload))
            evidence_path.write_text(
                json.dumps(
                    {
                        "base_main_sha": base,
                        "implementation_head_sha": implementation,
                        "implementation_tree_sha": implementation_tree,
                        "implementation_payload_tree_sha": payload_tree,
                        "validation_bundle": {
                            "path": bundle_relative,
                            "sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
                        },
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            transcript_path.write_bytes(b"gzip-fixture")
            run("add", evidence_relative, transcript_relative, bundle_relative)
            run("commit", "-m", "evidence")

            run("checkout", "main")
            run("merge", "--squash", "feature")
            run("commit", "-m", "squashed delivery")
            run("branch", "-D", "feature")
            run("reflog", "expire", "--expire=now", "--all")
            run("gc", "--prune=now")
            missing = subprocess.run(
                [GIT, "cat-file", "-e", f"{implementation}^{{commit}}"],
                cwd=repo,
                check=False,
                capture_output=True,
            )
            self.assertNotEqual(missing.returncode, 0)

            case = Phase2DEvidenceContractTest(
                "test_implementation_tree_and_evidence_only_child_are_bound"
            )
            with (
                mock.patch(f"{module}.ROOT", repo),
                mock.patch(f"{module}.EVIDENCE", evidence_path),
                mock.patch(f"{module}.EVIDENCE_RELATIVE", evidence_relative),
                mock.patch(f"{module}.TRANSCRIPT", transcript_path),
                mock.patch(f"{module}.TRANSCRIPT_RELATIVE", transcript_relative),
                mock.patch(f"{module}.VALIDATION_BUNDLE", bundle_path),
                mock.patch(f"{module}.VALIDATION_BUNDLE_RELATIVE", bundle_relative),
                mock.patch(f"{module}.MERGE_SHA", base),
            ):
                case.test_implementation_tree_and_evidence_only_child_are_bound()

    def test_evidence_binding_accepts_merge_ref_wrapping_squash(self) -> None:
        """Accept a merge-ref whose second parent is a validated flattened squash."""
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
            base = git("rev-parse", "HEAD")
            run("checkout", "-b", "feature")
            (repo / "implementation.txt").write_text("implementation\n", encoding="utf-8")
            run("add", "implementation.txt")
            run("commit", "-m", "implementation")
            implementation = git("rev-parse", "HEAD")
            implementation_tree = git("rev-parse", f"{implementation}^{{tree}}")

            evidence_relative = "docs/evidence.json"
            transcript_relative = "docs/evidence.log.gz"
            bundle_relative = "docs/validation-bundle.json"
            evidence_path = repo / evidence_relative
            transcript_path = repo / transcript_relative
            bundle_path = repo / bundle_relative
            evidence_path.parent.mkdir(parents=True)
            module = __name__
            with (
                mock.patch(f"{module}.ROOT", repo),
                mock.patch(f"{module}.EVIDENCE_RELATIVE", evidence_relative),
                mock.patch(f"{module}.TRANSCRIPT_RELATIVE", transcript_relative),
                mock.patch(f"{module}.VALIDATION_BUNDLE_RELATIVE", bundle_relative),
            ):
                payload_tree = _tree_without_evidence(implementation)
            bundle_path.write_bytes(
                _canonical_json_bytes(
                    {
                        "schema": "omnigenis-phase2d-validation-bundle-v1",
                        "validated_head_sha": implementation,
                        "validated_tree_sha": implementation_tree,
                        "validated_payload_tree_sha": payload_tree,
                        "gates": {},
                        "ruleset_readbacks": {},
                    }
                )
            )
            evidence_path.write_text('{"status":"verified"}\n', encoding="utf-8")
            transcript_path.write_bytes(b"gzip-fixture")
            run("add", evidence_relative, transcript_relative, bundle_relative)
            run("commit", "-m", "evidence")

            run("checkout", "main")
            run("checkout", "-b", "delivery")
            run("merge", "--squash", "feature")
            run("commit", "-m", "squashed delivery")
            squash = git("rev-parse", "HEAD")
            run("checkout", "main")
            run("merge", "--no-ff", "delivery", "-m", "synthetic merge ref")

            with (
                mock.patch(f"{module}.ROOT", repo),
                mock.patch(f"{module}.EVIDENCE", evidence_path),
                mock.patch(f"{module}.EVIDENCE_RELATIVE", evidence_relative),
                mock.patch(f"{module}.TRANSCRIPT", transcript_path),
                mock.patch(f"{module}.TRANSCRIPT_RELATIVE", transcript_relative),
                mock.patch(f"{module}.VALIDATION_BUNDLE", bundle_path),
                mock.patch(f"{module}.VALIDATION_BUNDLE_RELATIVE", bundle_relative),
            ):
                mode, delivery = _resolve_evidence_delivery(implementation, payload_tree, base)
            self.assertEqual(mode, "squashed_delivery")
            self.assertEqual(delivery, squash)

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
            transcript_relative = "docs/evidence.log.gz"
            transcript_path = repo / transcript_relative
            transcript_path.write_bytes(gzip.compress(b"suite\n__OMNIGENIS_EXIT_CODE__=0\n", mtime=0))
            bundle_relative = "docs/validation-bundle.json"
            bundle_path = repo / bundle_relative
            bundle_path.write_text('{"schema":"fixture"}\n', encoding="utf-8")
            run("add", evidence_relative, transcript_relative, bundle_relative)
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
                mock.patch(f"{module}.TRANSCRIPT", transcript_path),
                mock.patch(f"{module}.TRANSCRIPT_RELATIVE", transcript_relative),
                mock.patch(f"{module}.VALIDATION_BUNDLE", bundle_path),
                mock.patch(f"{module}.VALIDATION_BUNDLE_RELATIVE", bundle_relative),
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
        bundle = self.load_validation_bundle(evidence)
        canaries = evidence["protected_main_canaries"]
        provenance = evidence["canary_readback_provenance"]
        captured = bundle["canary_readback"]
        sanitized = provenance["sanitized_output"]
        self.assertEqual(provenance["source"], "GitHub REST API")
        self.assertRegex(provenance["captured_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        command = provenance["command"]
        for _runner_id, (_run_id, job_id) in EXPECTED_CANARIES.items():
            self.assertIn(f"actions/jobs/{job_id}", command)
        self.assertNotIn("authenticated protected-main canary jobs", command)
        subprocess.run(["bash", "-n", "-c", command], check=True, capture_output=True)
        self.assertEqual(captured["command"], command)
        self.assertEqual(captured["exit_code"], 0)
        self.assertRegex(captured["captured_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(captured["raw_output"], sanitized)
        self.assertEqual(
            hashlib.sha256(captured["raw_output"].encode("utf-8")).hexdigest(),
            captured["raw_output_sha256"],
        )
        self.assertEqual(
            provenance["validation_bundle_record_sha256"],
            hashlib.sha256(_canonical_json_bytes(captured)).hexdigest(),
        )
        self.assertEqual(
            hashlib.sha256(sanitized.encode("utf-8")).hexdigest(),
            provenance["sanitized_output_sha256"],
        )
        self.assertEqual(json.loads(sanitized), canaries)
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

    def test_governance_rulesets_match_authorized_hardened_semantics(self) -> None:
        """Recompute live ruleset semantics from captured per-ID REST responses."""
        evidence = self.load()
        bundle = self.load_validation_bundle(evidence)
        rulesets = evidence["rulesets"]
        provenance = evidence["ruleset_readback_provenance"]
        sanitized = provenance["sanitized_output"]
        self.assertEqual(provenance["source"], "GitHub REST API")
        self.assertRegex(provenance["captured_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(
            hashlib.sha256(sanitized.encode("utf-8")).hexdigest(),
            provenance["sanitized_output_sha256"],
        )
        self.assertEqual(json.loads(sanitized), rulesets)
        by_id = {int(item["id"]): item for item in rulesets}
        self.assertEqual(set(by_id), set(EXPECTED_RULESETS))
        for ruleset_id, (name, enforcement) in EXPECTED_RULESETS.items():
            self.assertEqual(by_id[ruleset_id]["name"], name)
            self.assertEqual(by_id[ruleset_id]["enforcement"], enforcement)

        predecessor = json.loads(RULESET_BASELINE.read_text(encoding="utf-8"))
        baseline = predecessor["ruleset_semantics_post"]
        expected = _expected_ruleset_semantics(predecessor)
        baseline_sha = hashlib.sha256(_canonical_json_bytes(baseline)).hexdigest()
        expected_sha = hashlib.sha256(_canonical_json_bytes(expected)).hexdigest()
        comparison = evidence["ruleset_semantic_comparison"]
        self.assertEqual(
            comparison["baseline_source"],
            "docs/superpowers/evidence/2026-09-10-omnigenis-repository-identity-migration.json#ruleset_semantics_post",
        )
        self.assertEqual(comparison["baseline_semantics_sha256"], baseline_sha)
        self.assertEqual(
            comparison["authorized_delta"],
            {
                "ruleset_id": 22347095,
                "source": ".github/governance/main-approval-ruleset.json",
                "required_approving_review_count": {"from": 0, "to": 1},
                "require_last_push_approval": {"from": False, "to": True},
            },
        )
        self.assertEqual(comparison["expected_semantics_sha256"], expected_sha)

        readback = comparison["readback_provenance"]
        self.assertEqual(readback["source"], "GitHub REST API")
        self.assertEqual(readback["bundle_path"], VALIDATION_BUNDLE_RELATIVE)
        captured: dict[str, dict] = {}
        for ruleset_id in EXPECTED_RULESETS:
            key = str(ruleset_id)
            bundle_record = bundle["ruleset_readbacks"][key]
            self.assertEqual(bundle_record["exit_code"], 0)
            self.assertIn(f"rulesets/{ruleset_id}", bundle_record["command"])
            self.assertRegex(bundle_record["captured_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
            raw = bundle_record["raw_output"]
            self.assertEqual(
                hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                bundle_record["raw_output_sha256"],
            )
            evidence_record = readback["records"][key]
            self.assertEqual(evidence_record["command"], bundle_record["command"])
            self.assertEqual(evidence_record["raw_output_sha256"], bundle_record["raw_output_sha256"])
            self.assertEqual(
                evidence_record["bundle_record_sha256"],
                hashlib.sha256(_canonical_json_bytes(bundle_record)).hexdigest(),
            )
            captured[key] = json.loads(raw)

        neutralized = {
            key: _neutralize_ruleset(captured[key], expected[key])
            for key in sorted(expected)
        }
        self.assertEqual(neutralized, expected)
        live_sha = hashlib.sha256(_canonical_json_bytes(neutralized)).hexdigest()
        self.assertEqual(comparison["neutralized_live_semantics"], neutralized)
        self.assertEqual(comparison["live_neutralized_sha256"], live_sha)
        self.assertEqual(live_sha, expected_sha)
        self.assertTrue(comparison["match"])

    def test_validation_environment_is_deterministically_reconstructible(self) -> None:
        """Require a recorded venv setup rooted in the versioned requirements lock."""
        evidence = self.load()
        environment = evidence["validation_environment"]
        requirements = ROOT / environment["requirements_file"]
        self.assertEqual(environment["requirements_file"], "reporting/requirements.txt")
        self.assertEqual(
            hashlib.sha256(requirements.read_bytes()).hexdigest(),
            environment["requirements_sha256"],
        )
        setup = environment["setup_command"]
        self.assertIn("python3.12 -m venv", setup)
        self.assertIn("-r reporting/requirements.txt", setup)
        self.assertEqual(
            hashlib.sha256(setup.encode("utf-8")).hexdigest(),
            environment["setup_command_sha256"],
        )
        self.assertEqual(environment["setup_status"], "REPRODUCIBLE_SPEC")
        self.assertNotIn("setup_log_sha256", environment)
        interpreter = environment["interpreter"]
        for name in (
            "docs_language",
            "implementation_suite_without_phase2d_evidence",
            "phase2d_hardening",
            "zero_identity_seal",
        ):
            self.assertIn(interpreter, evidence["validation_provenance"][name]["command"], name)

    def test_implementation_suite_provenance_is_replayable(self) -> None:
        """Bind the suite claim to its command, committed transcript, and exit status."""
        evidence = self.load()
        record = evidence["validation_provenance"]["implementation_suite_without_phase2d_evidence"]
        command = record["command"]
        self.assertNotIn("<", command)
        self.assertNotIn(">", command)
        self.assertIn("find tests", command)
        self.assertIn("test_phase2d_evidence_contract.py", command)
        self.assertEqual(record["transcript_path"], TRANSCRIPT_RELATIVE)
        compressed = TRANSCRIPT.read_bytes()
        self.assertEqual(hashlib.sha256(compressed).hexdigest(), record["transcript_gzip_sha256"])
        transcript = gzip.decompress(compressed)
        marker = re.search(rb"\n__OMNIGENIS_EXIT_CODE__=(\d+)\n$", transcript)
        self.assertIsNotNone(marker)
        assert marker is not None
        raw_output = transcript[: marker.start()]
        exit_code = int(marker.group(1))
        self.assertEqual(exit_code, record["exit_code"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(hashlib.sha256(raw_output).hexdigest(), record["raw_output_sha256"])
        decoded = raw_output.decode("utf-8")
        ran = re.findall(r"^Ran \d+ tests(?: in [0-9.]+s)?$", decoded, flags=re.MULTILINE)
        ok = re.findall(r"^OK(?: \(skipped=\d+\))?$", decoded, flags=re.MULTILINE)
        self.assertTrue(ran)
        self.assertTrue(ok)
        self.assertNotRegex(decoded, r"(?m)^FAILED \(")
        self.assertNotRegex(decoded, r"(?m)^ERROR: ")
        summary = ran[-1] + "\n" + ok[-1] + "\n"
        self.assertEqual(summary, record["sanitized_output"])
        self.assertEqual(
            hashlib.sha256(summary.encode("utf-8")).hexdigest(),
            record["output_sha256"],
        )
        self.assertEqual(record["validated_head_sha"], evidence["implementation_head_sha"])
        self.assertEqual(record["validated_tree_sha"], evidence["implementation_tree_sha"])
        receipt_payload = {
            "command": command,
            "exit_code": exit_code,
            "output_sha256": record["output_sha256"],
            "raw_output_sha256": record["raw_output_sha256"],
            "transcript_gzip_sha256": record["transcript_gzip_sha256"],
            "validated_head_sha": record["validated_head_sha"],
            "validated_tree_sha": record["validated_tree_sha"],
            "environment_setup_sha256": evidence["validation_environment"]["setup_command_sha256"],
            "requirements_sha256": evidence["validation_environment"]["requirements_sha256"],
        }
        canonical = json.dumps(receipt_payload, sort_keys=True, separators=(",", ":"))
        self.assertEqual(
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            record["execution_receipt_sha256"],
        )

    def test_required_validation_records_are_backed_by_captured_bundle_outputs(self) -> None:
        """Back every required gate claim with committed raw execution output."""
        evidence = self.load()
        bundle = self.load_validation_bundle(evidence)
        implementation = evidence["implementation_head_sha"]
        tree = evidence["implementation_tree_sha"]
        required = (
            "identity",
            "zero_identity",
            "validate_repo",
            "supply_chain",
            "code_language",
            "residual_language",
            "docs_language",
            "zero_identity_seal",
            "phase2d_hardening",
            "shell_syntax",
            "diff_check",
            "legacy_inventory",
            "zero_identity_plane_inventory",
        )
        self.assertEqual(set(bundle["gates"]), set(required))
        for name in required:
            with self.subTest(gate=name):
                record = evidence["validation_provenance"][name]
                captured = bundle["gates"][name]
                self.assertEqual(captured["exit_code"], 0)
                self.assertEqual(captured["validated_head_sha"], implementation)
                self.assertEqual(captured["validated_tree_sha"], tree)
                self.assertEqual(record["command"], captured["command"])
                self.assertEqual(record["exit_code"], captured["exit_code"])
                self.assertEqual(record["validated_head_sha"], implementation)
                self.assertEqual(record["validated_tree_sha"], tree)
                raw = captured["raw_output"]
                self.assertEqual(
                    hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                    captured["raw_output_sha256"],
                )
                self.assertEqual(
                    record["validation_bundle_record_sha256"],
                    hashlib.sha256(_canonical_json_bytes(captured)).hexdigest(),
                )
                self.assertNotIn("execution_receipt_sha256", record)
                sanitized = record["sanitized_output"]
                self.assertEqual(
                    hashlib.sha256(sanitized.encode("utf-8")).hexdigest(),
                    record["output_sha256"],
                )
        self.assertEqual(
            evidence["validation_provenance"]["diff_check"]["command"],
            f"git diff --check {evidence['base_main_sha']} {implementation}",
        )

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
            predecessor = json.loads(RUNNER_NAME_BASELINE.read_text(encoding="utf-8"))
            expected_names = {
                int(record["id"]): record["retired_name_sha256"]
                for record in predecessor["runner_snapshot_after"]
            }
            self.assertEqual(item["runner_name_sha256"], expected_names[runner_id])
            self.assertRegex(item["runner_name_sha256"], r"^[0-9a-f]{64}$")
            self.assertNotIn("runner_name", item)


if __name__ == "__main__":
    unittest.main()
