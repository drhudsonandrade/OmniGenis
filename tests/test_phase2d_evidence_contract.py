from __future__ import annotations

import copy
import gzip
import hashlib
import json
import os
import re
from datetime import datetime
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
MAIN_RULESET_MANIFEST = ROOT / ".github/governance/main-ruleset.json"
APPROVAL_RULESET_MANIFEST = ROOT / ".github/governance/main-approval-ruleset.json"
RUNNER_NAME_BASELINE = ROOT / "docs/superpowers/evidence/2026-09-11-omnigenis-phase2c-runner-cutover.json"
MERGE_SHA = "a7cb7f5559a83adc3c75f61284fecb09d1fb5553"
EXPECTED_HISTORICAL_FILES = 21
EXPECTED_RUNNERS = {21: "omnigenis-01", 22: "omnigenis-02"}
EXPECTED_CANARIES = {
    21: (34778271301, 103780369158),
    22: (34778271133, 103780390014),
}
EXPECTED_CANARY_RUNS = {
    34778271301: (334755305, "Genome runtime scaffold", ".github/workflows/scaffold-validation.yml"),
    34778271133: (335346657, "GENOMA deterministic policy engine", ".github/workflows/genoma-policy-engine.yml"),
}
EXPECTED_RULESETS = {
    21303100: ("GENOMA protected main", "active"),
    22347095: ("GENOMA approval gate", "active"),
}
LIVE_REPLAY_ENV = "OMNIGENIS_PHASE2D_LIVE_REPLAY"
REPOSITORY_ID = 1212760346
VALIDATION_INTERPRETER = "/tmp/omnigenis-phase2d-validation-venv/bin/python"
VALIDATION_SETUP_COMMAND = (
    "python3.12 -m venv --clear /tmp/omnigenis-phase2d-validation-venv && "
    "/tmp/omnigenis-phase2d-validation-venv/bin/python -m pip install "
    "--disable-pip-version-check --require-hashes -r reporting/requirements.txt && "
    "/tmp/omnigenis-phase2d-validation-venv/bin/python -m pip check"
)
VALIDATION_PIP_CHECK_COMMAND = (
    "/tmp/omnigenis-phase2d-validation-venv/bin/python -m pip check"
)
EXPECTED_REPOSITORY_IDENTITY = {
    "id": REPOSITORY_ID,
    "name": "OmniGenis",
    "visibility": "public",
    "default_branch": "main",
}
GIT = shutil.which("git")
GH = shutil.which("gh")
if GIT is None:
    raise RuntimeError("git is required by the Phase 2D evidence contract")


def _git(*args: str) -> str:
    """Run a read-only Git command in the repository under test."""
    return subprocess.check_output([GIT, *args], cwd=ROOT, text=True).strip()


def _git_bytes(*args: str) -> bytes:
    """Read byte-exact historical Git content without normalizing line endings."""
    return subprocess.check_output([GIT, *args], cwd=ROOT)


def _gh_api_json(endpoint: str, *options: str) -> object:
    """Read one allowlisted GitHub REST endpoint without invoking a shell."""
    if GH is None:
        raise AssertionError("gh is required for authenticated Phase 2D live replay")
    proc = subprocess.run(
        [GH, "api", *options, endpoint],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(f"GitHub REST readback failed: {proc.stderr}")
    if proc.stderr:
        raise AssertionError(f"GitHub REST readback wrote stderr: {proc.stderr}")
    return json.loads(proc.stdout)


def _canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _canonical_json_text(value: object) -> str:
    return _canonical_json_bytes(value).decode("utf-8")


def _validated_unittest_summary(raw_output: str) -> str:
    if re.search(r"(?m)^(?:FAILED|ERROR|NOT OK)(?:\b|:)", raw_output):
        raise AssertionError("unittest transcript contains a failure marker")
    ran = re.findall(
        r"^Ran \d+ tests(?: in [0-9.]+s)?$",
        raw_output,
        flags=re.MULTILINE,
    )
    ok = re.findall(
        r"^OK(?: \(skipped=\d+\))?$",
        raw_output,
        flags=re.MULTILINE,
    )
    if len(ran) != 1 or len(ok) != 1:
        raise AssertionError("unittest transcript must contain one complete success summary")
    return ran[0] + "\n" + ok[0] + "\n"


def _live_repository_record() -> dict:
    raw = _gh_api_json(f"repositories/{REPOSITORY_ID}")
    assert isinstance(raw, dict)
    return raw


def _live_repository_identity_output() -> str:
    raw = _live_repository_record()
    return _canonical_json_text(
        {
            "id": raw["id"],
            "name": raw["name"],
            "visibility": raw["visibility"],
            "default_branch": raw["default_branch"],
        }
    )


def _live_repository_full_name() -> str:
    raw = _live_repository_record()
    return str(raw["full_name"])


def _live_canary_jobs_output() -> str:
    repo = _live_repository_full_name()
    rows = []
    for runner_id, (run_id, job_id) in EXPECTED_CANARIES.items():
        raw = _gh_api_json(f"repos/{repo}/actions/jobs/{job_id}")
        assert isinstance(raw, dict)
        rows.append(
            {
                "runner_id": raw["runner_id"],
                "run_id": raw["run_id"],
                "job_id": raw["id"],
                "commit_sha": raw["head_sha"],
                "conclusion": raw["conclusion"],
                "selector_labels": raw["labels"],
            }
        )
        if int(raw["runner_id"]) != runner_id or int(raw["run_id"]) != run_id:
            raise AssertionError("canary job identity mismatch")
    return _canonical_json_text(sorted(rows, key=lambda item: item["runner_id"]))


def _live_runner_output() -> str:
    repo = _live_repository_full_name()
    pages = _gh_api_json(
        f"repos/{repo}/actions/runners?per_page=100", "--paginate", "--slurp"
    )
    assert isinstance(pages, list) and pages
    total_counts = {int(page["total_count"]) for page in pages}
    if len(total_counts) != 1:
        raise AssertionError("runner page total_count values disagree")
    runners = [item for page in pages for item in page["runners"]]
    if len(runners) != total_counts.pop():
        raise AssertionError("paginated runner readback is incomplete")
    if len({int(item["id"]) for item in runners}) != len(runners):
        raise AssertionError("paginated runner readback contains duplicate IDs")
    rows = [
        {
            "id": item["id"],
            "status": item["status"],
            "busy": item["busy"],
            "labels": [label["name"] for label in item["labels"]],
            "runner_name_sha256": hashlib.sha256(item["name"].encode()).hexdigest(),
        }
        for item in runners
    ]
    return _canonical_json_text(sorted(rows, key=lambda item: item["id"]))


def _live_ruleset_summary_output() -> str:
    repo = _live_repository_full_name()
    pages = _gh_api_json(
        f"repos/{repo}/rulesets?per_page=100", "--paginate", "--slurp"
    )
    assert isinstance(pages, list)
    rows = [item for page in pages for item in page]
    active = [
        {"id": item["id"], "name": item["name"], "enforcement": item["enforcement"]}
        for item in rows
        if item["enforcement"] == "active"
    ]
    return _canonical_json_text(sorted(active, key=lambda item: item["id"]))


def _live_ruleset_detail_output(ruleset_id: int) -> str:
    repo = _live_repository_full_name()
    raw = _gh_api_json(f"repos/{repo}/rulesets/{ruleset_id}")
    assert isinstance(raw, dict)
    return _canonical_json_text(
        {
            key: raw[key]
            for key in (
                "id",
                "name",
                "target",
                "enforcement",
                "conditions",
                "bypass_actors",
                "rules",
            )
        }
    )


def _live_canary_run_output(run_id: int) -> str:
    repo = _live_repository_full_name()
    raw = _gh_api_json(f"repos/{repo}/actions/runs/{run_id}")
    assert isinstance(raw, dict)
    keys = (
        "id", "name", "event", "head_branch", "head_sha", "workflow_id",
        "path", "conclusion", "status", "run_attempt",
    )
    return _canonical_json_text({key: raw[key] for key in keys})


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
    """Resolve commit-bound evidence or payload-equivalent flattened delivery.

    The fallback proves byte-identical payload/evidence content only. It does not
    claim commit ancestry from an implementation object that may have been pruned.
    """
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
        return "payload_equivalent_squashed_delivery", candidate



def _expected_ruleset_semantics() -> dict:
    """Derive required live semantics from the current governance manifests."""
    manifests = {
        21303100: MAIN_RULESET_MANIFEST,
        22347095: APPROVAL_RULESET_MANIFEST,
    }
    expected: dict[str, dict] = {}
    for ruleset_id, path in manifests.items():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("target") != "branch":
            raise AssertionError(f"{path} must target branches")
        manifest["id"] = ruleset_id
        required_status_contexts: list[object] = []
        for rule in manifest["rules"]:
            if rule["type"] != "required_status_checks":
                continue
            for item in rule["parameters"]["required_status_checks"]:
                required_status_contexts.append(
                    item["context"]
                    if "context" in item
                    else {"context_fingerprint": copy.deepcopy(item["context_fingerprint"])}
                )
        manifest["required_status_contexts"] = required_status_contexts
        expected[str(ruleset_id)] = manifest
    return expected


def _same_integration_binding(live: dict, expected: dict) -> bool:
    """Require integration binding presence and identity to remain exact."""
    if ("integration_id" in live) != ("integration_id" in expected):
        return False
    return "integration_id" not in expected or live["integration_id"] == expected["integration_id"]


def _neutralize_ruleset(raw: dict, expected: dict) -> dict:
    """Project authenticated provider JSON into the versioned neutral semantic shape."""
    if raw.get("target") != expected.get("target"):
        raise AssertionError("ruleset target mismatch")
    normalized = {
        "id": raw["id"],
        "name": raw["name"],
        "target": raw["target"],
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
            and _same_integration_binding(live_check, expected_check)
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

    def load_execution_transcripts(self, evidence: dict) -> dict:
        """Load the commit-bound transcript archive outside the validation bundle."""
        record = evidence["validation_provenance"][
            "implementation_suite_without_phase2d_evidence"
        ]
        self.assertEqual(record["transcript_path"], TRANSCRIPT_RELATIVE)
        compressed = TRANSCRIPT.read_bytes()
        self.assertEqual(
            hashlib.sha256(compressed).hexdigest(),
            record["transcript_gzip_sha256"],
        )
        archive = json.loads(gzip.decompress(compressed).decode("utf-8"))
        self.assertEqual(archive["schema"], "omnigenis-phase2d-transcripts-v1")
        return archive
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

    def test_repository_identity_is_canonical_and_authenticated(self) -> None:
        """Bind VERIFIED to the full canonical GitHub repository identity."""
        evidence = self.load()
        bundle = self.load_validation_bundle(evidence)
        self.assertEqual(evidence["repository_id"], REPOSITORY_ID)
        self.assertEqual(evidence["repository_identity"], EXPECTED_REPOSITORY_IDENTITY)
        provenance = evidence["repository_identity_provenance"]
        captured = bundle["repository_identity_readback"]
        replay = bundle["repository_identity_replay"]
        self.assertEqual(provenance["source"], "GitHub REST API")
        self.assertIn(f"repositories/{REPOSITORY_ID}", provenance["command"])
        self.assertEqual(captured["command"], provenance["command"])
        self.assertEqual(replay["command"], provenance["command"])
        self.assertEqual(captured["exit_code"], 0)
        self.assertEqual(replay["exit_code"], 0)
        self.assertEqual(captured["raw_output"], replay["raw_output"])
        self.assertEqual(json.loads(captured["raw_output"]), EXPECTED_REPOSITORY_IDENTITY)
        self.assertEqual(
            hashlib.sha256(captured["raw_output"].encode("utf-8")).hexdigest(),
            captured["raw_output_sha256"],
        )
        self.assertEqual(captured["raw_output_sha256"], replay["raw_output_sha256"])
        self.assertEqual(
            provenance["validation_bundle_record_sha256"],
            hashlib.sha256(_canonical_json_bytes(captured)).hexdigest(),
        )
        self.assertEqual(
            provenance["replay_bundle_record_sha256"],
            hashlib.sha256(_canonical_json_bytes(replay)).hexdigest(),
        )

    def test_implementation_tree_and_evidence_only_child_are_bound(self) -> None:
        """Bind evidence to the independently captured validated payload tree."""
        evidence = self.load()
        bundle = self.load_validation_bundle(evidence)
        implementation = evidence["implementation_head_sha"]
        payload_tree = bundle["validated_payload_tree_sha"]
        self.assertEqual(
            evidence["flattened_delivery_fallback"],
            {
                "proof_scope": "PAYLOAD_TREE_EQUIVALENCE_ONLY",
                "claims_implementation_ancestry": False,
                "requires_base_main_ancestry": True,
                "requires_evidence_artifact_equality": True,
            },
        )
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
            self.assertEqual(mode, "payload_equivalent_squashed_delivery")
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
                        "flattened_delivery_fallback": {
                            "proof_scope": "PAYLOAD_TREE_EQUIVALENCE_ONLY",
                            "claims_implementation_ancestry": False,
                            "requires_base_main_ancestry": True,
                            "requires_evidence_artifact_equality": True,
                        },
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
            self.assertEqual(mode, "payload_equivalent_squashed_delivery")
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
        """Bind pre-Phase2D protected-main canaries as baseline context only."""
        evidence = self.load()
        bundle = self.load_validation_bundle(evidence)
        canaries = evidence["protected_main_canaries"]
        scope = evidence["protected_main_canary_scope"]
        self.assertEqual(
            scope,
            {
                "role": "PRE_PHASE2D_BASELINE_CONTEXT_ONLY",
                "commit_sha": MERGE_SHA,
                "validates_implementation_payload": False,
                "implementation_head_sha": evidence["implementation_head_sha"],
            },
        )
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
        replay = bundle["canary_replay"]
        self.assertEqual(captured["command"], command)
        self.assertEqual(replay["command"], command)
        self.assertEqual(replay["exit_code"], 0)
        self.assertEqual(replay["raw_output"], sanitized)
        self.assertEqual(replay["raw_output_sha256"], captured["raw_output_sha256"])
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
        run_hashes = provenance["run_readback_bundle_record_sha256"]
        self.assertEqual(set(run_hashes), {str(run_id) for run_id in EXPECTED_CANARY_RUNS})
        for run_id, (workflow_id, workflow_name, workflow_path) in EXPECTED_CANARY_RUNS.items():
            run_record = bundle["canary_run_readbacks"][str(run_id)]
            self.assertEqual(run_record["exit_code"], 0)
            self.assertIn(f"actions/runs/{run_id}", run_record["command"])
            replay_record = bundle["canary_run_replays"][str(run_id)]
            self.assertEqual(replay_record["command"], run_record["command"])
            self.assertEqual(replay_record["exit_code"], 0)
            self.assertEqual(replay_record["raw_output"], run_record["raw_output"])
            self.assertEqual(replay_record["raw_output_sha256"], run_record["raw_output_sha256"])
            self.assertEqual(
                hashlib.sha256(run_record["raw_output"].encode("utf-8")).hexdigest(),
                run_record["raw_output_sha256"],
            )
            self.assertEqual(
                run_hashes[str(run_id)],
                hashlib.sha256(_canonical_json_bytes(run_record)).hexdigest(),
            )
            run = json.loads(run_record["raw_output"])
            self.assertEqual(run["id"], run_id)
            self.assertEqual(run["workflow_id"], workflow_id)
            self.assertEqual(run["name"], workflow_name)
            self.assertEqual(run["path"], workflow_path)
            self.assertEqual(run["event"], "push")
            self.assertEqual(run["head_branch"], "main")
            self.assertEqual(run["head_sha"], MERGE_SHA)
            self.assertEqual(run["conclusion"], "success")
            self.assertEqual(run["status"], "completed")
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

    def _assert_external_query_executions(self, evidence: dict, bundle: dict) -> None:
        """Bind each read-only query to its own archived process capture."""
        pairs = [
            ("repository_identity_readback", "repository_identity_replay"),
            ("runner_readback", "runner_replay"),
            ("ruleset_summary", "ruleset_summary_replay"),
            ("canary_readback", "canary_replay"),
        ]
        for first, second, identities in (
            ("ruleset_readbacks", "ruleset_replays", EXPECTED_RULESETS),
            ("canary_run_readbacks", "canary_run_replays", EXPECTED_CANARY_RUNS),
        ):
            pairs.extend((f"{first}:{key}", f"{second}:{key}") for key in identities)
        records = {}
        for name in (name for pair in pairs for name in pair):
            group, separator, key = name.partition(":")
            records[name] = bundle[group][key] if separator else bundle[group]
        captures = self.load_execution_transcripts(evidence).get("external_readbacks")
        self.assertIsInstance(captures, dict, "independent query transcripts are missing")
        self.assertEqual(set(captures), set(records))
        seen_ids: set[str] = set()
        times = {}
        expected = _expected_ruleset_semantics()
        for name, record in records.items():
            context = record.get("execution_provenance")
            self.assertIsInstance(context, dict, "query execution provenance is missing")
            capture = captures[name]
            self.assertEqual(capture["schema"], "omnigenis-github-query-execution-v1")
            self.assertEqual(
                context["transcript_locator"],
                f"{TRANSCRIPT_RELATIVE}#/external_readbacks/{name}",
            )
            self.assertEqual(
                context["transcript_sha256"],
                hashlib.sha256(_canonical_json_bytes(capture)).hexdigest(),
            )
            execution_id = capture["execution_id"]
            self.assertRegex(execution_id, r"^[0-9a-f]{32}$")
            self.assertNotIn(execution_id, seen_ids, "query executions must not be copied")
            seen_ids.add(execution_id)
            self.assertEqual(context["execution_id"], execution_id)
            self.assertEqual(capture["command"], record["command"])
            self.assertEqual(
                capture["argv"], ["bash", "-o", "pipefail", "-lc", record["command"]]
            )
            self.assertEqual(capture["head_sha"], evidence["implementation_head_sha"])
            self.assertEqual(capture["tree_sha"], evidence["implementation_tree_sha"])
            self.assertIs(type(capture["exit_code"]), int)
            self.assertEqual(capture["exit_code"], 0)
            self.assertEqual(capture["exit_code"], record["exit_code"])
            self.assertEqual(capture["stderr"], "")
            output = capture["stdout"]
            self.assertEqual(
                hashlib.sha256(output.encode("utf-8")).hexdigest(),
                record["raw_output_sha256"],
            )
            if "raw_output" in record:
                self.assertEqual(output, record["raw_output"])
            else:
                key = name.partition(":")[2]
                normalized = _neutralize_ruleset(json.loads(output), expected[key])
                self.assertEqual(normalized, json.loads(record["semantic_output"]))
            for field in ("started_at", "completed_at"):
                self.assertRegex(
                    capture[field], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$"
                )
            start, end = (
                datetime.fromisoformat(capture[field].replace("Z", "+00:00"))
                for field in ("started_at", "completed_at")
            )
            self.assertLessEqual(start, end)
            self.assertEqual(record["captured_at"], end.strftime("%Y-%m-%dT%H:%M:%SZ"))
            times[name] = (start, end)
        for first, second in pairs:
            self.assertEqual(captures[first]["command"], captures[second]["command"])
            self.assertLessEqual(times[first][1], times[second][0])

    def test_governance_rulesets_match_authorized_hardened_semantics(self) -> None:
        """Recompute live ruleset semantics from captured per-ID REST responses."""
        evidence = self.load()
        bundle = self.load_validation_bundle(evidence)
        self._assert_external_query_executions(evidence, bundle)
        rulesets = evidence["rulesets"]
        provenance = evidence["ruleset_readback_provenance"]
        sanitized = provenance["sanitized_output"]
        self.assertEqual(provenance["source"], "GitHub REST API")
        self.assertRegex(provenance["captured_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        for ruleset_id in EXPECTED_RULESETS:
            self.assertNotIn(str(ruleset_id), provenance["command"])
        self.assertIn("--paginate", provenance["command"])
        self.assertIn("--slurp", provenance["command"])
        self.assertIn("per_page=100", provenance["command"])
        self.assertIn("pages=json.load", provenance["command"])
        self.assertIn("for page in pages for x in page", provenance["command"])
        summary_replay = bundle["ruleset_summary_replay"]
        self.assertEqual(summary_replay["command"], provenance["command"])
        self.assertEqual(summary_replay["exit_code"], 0)
        self.assertEqual(summary_replay["raw_output"], sanitized)
        self.assertEqual(summary_replay["raw_output_sha256"], provenance["sanitized_output_sha256"])
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

        expected = _expected_ruleset_semantics()
        expected_sha = hashlib.sha256(_canonical_json_bytes(expected)).hexdigest()
        comparison = evidence["ruleset_semantic_comparison"]
        manifest_sources = {
            "21303100": ".github/governance/main-ruleset.json",
            "22347095": ".github/governance/main-approval-ruleset.json",
        }
        self.assertEqual(comparison["governance_manifest_sources"], manifest_sources)
        self.assertEqual(
            comparison["governance_manifest_sha256"],
            {
                key: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                for key, path in manifest_sources.items()
            },
        )
        self.assertEqual(comparison["expected_semantics_sha256"], expected_sha)

        readback = comparison["readback_provenance"]
        self.assertEqual(readback["source"], "GitHub REST API")
        self.assertEqual(readback["bundle_path"], VALIDATION_BUNDLE_RELATIVE)
        neutralized: dict[str, dict] = {}
        for ruleset_id in EXPECTED_RULESETS:
            key = str(ruleset_id)
            bundle_record = bundle["ruleset_readbacks"][key]
            self.assertEqual(bundle_record["exit_code"], 0)
            self.assertIn(f"rulesets/{ruleset_id}", bundle_record["command"])
            self.assertRegex(bundle_record["captured_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
            self.assertNotIn("raw_output", bundle_record)
            replay_record = bundle["ruleset_replays"][key]
            self.assertNotIn("raw_output", replay_record)
            self.assertEqual(replay_record["command"], bundle_record["command"])
            self.assertEqual(replay_record["exit_code"], 0)
            self.assertEqual(replay_record["raw_output_sha256"], bundle_record["raw_output_sha256"])
            self.assertEqual(replay_record["semantic_output"], bundle_record["semantic_output"])
            self.assertEqual(replay_record["semantic_output_sha256"], bundle_record["semantic_output_sha256"])
            normalized = json.loads(bundle_record["semantic_output"])
            self.assertEqual(normalized, expected[key])
            semantic = _canonical_json_bytes(normalized).decode("utf-8")
            self.assertEqual(bundle_record["semantic_output"], semantic)
            self.assertEqual(
                hashlib.sha256(semantic.encode("utf-8")).hexdigest(),
                bundle_record["semantic_output_sha256"],
            )
            evidence_record = readback["records"][key]
            self.assertEqual(evidence_record["command"], bundle_record["command"])
            self.assertEqual(evidence_record["raw_output_sha256"], bundle_record["raw_output_sha256"])
            self.assertEqual(
                evidence_record["bundle_record_sha256"],
                hashlib.sha256(_canonical_json_bytes(bundle_record)).hexdigest(),
            )
            self.assertEqual(
                evidence_record["replay_bundle_record_sha256"],
                hashlib.sha256(_canonical_json_bytes(replay_record)).hexdigest(),
            )
            neutralized[key] = normalized

        self.assertEqual(neutralized, expected)
        live_sha = hashlib.sha256(_canonical_json_bytes(neutralized)).hexdigest()
        self.assertEqual(comparison["neutralized_live_semantics"], neutralized)
        self.assertEqual(comparison["live_neutralized_sha256"], live_sha)
        self.assertEqual(live_sha, expected_sha)
        self.assertTrue(comparison["match"])

    def test_ruleset_contract_rejects_copied_execution_provenance(self) -> None:
        """A consistently rehashed copy must not stand in for a second query."""
        baseline = self.load()
        baseline_bundle = self.load_validation_bundle(baseline)
        baseline_archive = self.load_execution_transcripts(baseline)
        target = "test_governance_rulesets_match_authorized_hardened_semantics"
        baseline_result = unittest.TestResult()
        type(self)(target).run(baseline_result)
        self.assertTrue(baseline_result.wasSuccessful(), baseline_result.errors)
        for key in baseline_bundle["ruleset_readbacks"]:
            with self.subTest(ruleset=key), tempfile.TemporaryDirectory() as tmp:
                evidence = copy.deepcopy(baseline)
                bundle = copy.deepcopy(baseline_bundle)
                archive = copy.deepcopy(baseline_archive)
                replay = copy.deepcopy(bundle["ruleset_readbacks"][key])
                if "execution_provenance" in replay:
                    source_name = f"ruleset_readbacks:{key}"
                    replay_name = f"ruleset_replays:{key}"
                    copied = copy.deepcopy(archive["external_readbacks"][source_name])
                    archive["external_readbacks"][replay_name] = copied
                    replay["execution_provenance"]["transcript_locator"] = (
                        f"{TRANSCRIPT_RELATIVE}#/external_readbacks/{replay_name}"
                    )
                    replay["execution_provenance"]["transcript_sha256"] = hashlib.sha256(
                        _canonical_json_bytes(copied)
                    ).hexdigest()
                bundle["ruleset_replays"][key] = replay
                evidence["ruleset_semantic_comparison"]["readback_provenance"]["records"][key][
                    "replay_bundle_record_sha256"
                ] = hashlib.sha256(_canonical_json_bytes(replay)).hexdigest()
                bundle_bytes = _canonical_json_bytes(bundle)
                evidence["validation_bundle"]["sha256"] = hashlib.sha256(bundle_bytes).hexdigest()
                compressed = gzip.compress(_canonical_json_bytes(archive), mtime=0)
                evidence["validation_provenance"]["implementation_suite_without_phase2d_evidence"][
                    "transcript_gzip_sha256"
                ] = hashlib.sha256(compressed).hexdigest()
                root = Path(tmp)
                evidence_path, bundle_path, archive_path = (
                    root / "evidence.json", root / "bundle.json", root / "transcript.gz"
                )
                evidence_path.write_bytes(_canonical_json_bytes(evidence))
                bundle_path.write_bytes(bundle_bytes)
                archive_path.write_bytes(compressed)
                result = unittest.TestResult()
                with (
                    mock.patch(f"{__name__}.EVIDENCE", evidence_path),
                    mock.patch(f"{__name__}.VALIDATION_BUNDLE", bundle_path),
                    mock.patch(f"{__name__}.TRANSCRIPT", archive_path),
                ):
                    type(self)(target).run(result)
                self.assertFalse(result.errors, result.errors)
                self.assertTrue(result.failures, "copied query execution was accepted")

    def test_ruleset_normalization_rejects_new_integration_binding(self) -> None:
        """Do not erase a newly introduced provider integration binding."""
        expected = {
            "id": 1,
            "name": "fixture",
            "target": "branch",
            "enforcement": "active",
            "conditions": {},
            "bypass_actors": [],
            "rules": [
                {
                    "type": "required_status_checks",
                    "parameters": {"required_status_checks": [{"context": "CodeRabbit"}]},
                }
            ],
            "required_status_contexts": ["CodeRabbit"],
        }
        live = copy.deepcopy(expected)
        check = live["rules"][0]["parameters"]["required_status_checks"][0]
        check["integration_id"] = 999999
        with self.assertRaises(AssertionError):
            _neutralize_ruleset(live, expected)

    def test_ruleset_normalization_rejects_non_branch_target(self) -> None:
        """Do not certify a ruleset that applies to a target other than branches."""
        expected = {
            "id": 1,
            "name": "fixture",
            "target": "branch",
            "enforcement": "active",
            "conditions": {},
            "bypass_actors": [],
            "rules": [],
            "required_status_contexts": [],
        }
        live = copy.deepcopy(expected)
        live["target"] = "tag"
        with self.assertRaisesRegex(AssertionError, "ruleset target mismatch"):
            _neutralize_ruleset(live, expected)

    @unittest.skipUnless(
        os.environ.get(LIVE_REPLAY_ENV) == "1",
        "authenticated Phase 2D live replay is an opt-in evidence ceremony",
    )
    def test_authenticated_external_readbacks_replay_exactly(self) -> None:
        """Replay only fixed read-only GitHub queries outside untrusted PR CI."""
        evidence = self.load()
        bundle = self.load_validation_bundle(evidence)
        self.assertEqual(
            _live_repository_identity_output(),
            bundle["repository_identity_readback"]["raw_output"],
        )
        self.assertEqual(
            _live_canary_jobs_output(), bundle["canary_readback"]["raw_output"]
        )
        self.assertEqual(
            _live_runner_output(), bundle["runner_readback"]["raw_output"]
        )
        self.assertEqual(
            _live_ruleset_summary_output(), bundle["ruleset_summary"]["raw_output"]
        )
        for key, record in bundle["canary_run_readbacks"].items():
            self.assertEqual(_live_canary_run_output(int(key)), record["raw_output"])
        expected_rulesets = _expected_ruleset_semantics()
        for key, record in bundle["ruleset_readbacks"].items():
            live_raw = _live_ruleset_detail_output(int(key))
            self.assertEqual(
                hashlib.sha256(live_raw.encode("utf-8")).hexdigest(),
                record["raw_output_sha256"],
            )
            live_semantics = _neutralize_ruleset(
                json.loads(live_raw), expected_rulesets[key]
            )
            self.assertEqual(
                _canonical_json_bytes(live_semantics).decode("utf-8"),
                record["semantic_output"],
            )
        self.assertEqual(
            evidence["repository_identity"],
            json.loads(_live_repository_identity_output()),
        )

    def test_validation_environment_is_deterministically_reconstructible(self) -> None:
        """Require a recorded venv setup rooted in the versioned requirements lock."""
        evidence = self.load()
        environment = evidence["validation_environment"]
        self.assertEqual(environment["requirements_file"], "reporting/requirements.txt")
        _, delivery = _resolve_evidence_delivery(
            evidence["implementation_head_sha"],
            evidence["implementation_payload_tree_sha"],
            evidence["base_main_sha"],
        )
        historical_requirements = _git_bytes(
            "show",
            f"{delivery}:{environment['requirements_file']}",
        )
        self.assertEqual(
            hashlib.sha256(historical_requirements).hexdigest(),
            environment["requirements_sha256"],
        )
        setup = environment["setup_command"]
        self.assertEqual(setup, VALIDATION_SETUP_COMMAND)
        self.assertIn("--require-hashes", setup)
        self.assertEqual(
            hashlib.sha256(setup.encode("utf-8")).hexdigest(),
            environment["setup_command_sha256"],
        )
        self.assertEqual(environment["setup_status"], "EXECUTED")
        bundle = self.load_validation_bundle(evidence)
        receipt = bundle["validation_environment_setup"]
        self.assertEqual(receipt["command"], setup)
        self.assertEqual(receipt["exit_code"], 0)
        self.assertEqual(receipt["requirements_sha256"], environment["requirements_sha256"])
        self.assertEqual(receipt["interpreter"], VALIDATION_INTERPRETER)
        self.assertEqual(receipt["python_version"], environment["python_version"])
        pip_check = receipt["pip_check"]
        self.assertEqual(pip_check["command"], VALIDATION_PIP_CHECK_COMMAND)
        self.assertEqual(pip_check["exit_code"], 0)
        self.assertEqual(pip_check["output"], "No broken requirements found.\n")
        self.assertEqual(
            hashlib.sha256(pip_check["output"].encode("utf-8")).hexdigest(),
            pip_check["output_sha256"],
        )
        self.assertTrue(receipt["raw_output"].endswith(pip_check["output"]))
        self.assertEqual(
            hashlib.sha256(receipt["raw_output"].encode("utf-8")).hexdigest(),
            receipt["raw_output_sha256"],
        )
        historical_requirements_text = historical_requirements.decode("utf-8")
        locked = {
            match.group(1).lower().replace("_", "-"): match.group(2).strip()
            for match in re.finditer(
                r"(?m)^([A-Za-z0-9_.-]+)==([^\s\\]+)", historical_requirements_text
            )
        }
        self.assertTrue(locked)
        self.assertEqual(receipt["locked_distributions"], locked)
        self.assertEqual(
            hashlib.sha256(_canonical_json_bytes(receipt)).hexdigest(),
            environment["setup_receipt_sha256"],
        )
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
        archive = self.load_execution_transcripts(evidence)
        self.assertEqual(
            set(archive),
            {"external_readbacks", "gates", "implementation_suite", "schema"},
        )
        transcript = archive["implementation_suite"].encode("utf-8")
        marker = re.search(rb"\n__OMNIGENIS_EXIT_CODE__=(\d+)\n$", transcript)
        self.assertIsNotNone(marker)
        assert marker is not None
        raw_output = transcript[: marker.start()]
        exit_code = int(marker.group(1))
        self.assertEqual(exit_code, record["exit_code"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(hashlib.sha256(raw_output).hexdigest(), record["raw_output_sha256"])
        decoded = raw_output.decode("utf-8")
        summary = _validated_unittest_summary(decoded)
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
        transcripts = self.load_execution_transcripts(evidence)
        self.assertEqual(set(transcripts["gates"]), set(required))
        for name in required:
            with self.subTest(gate=name):
                record = evidence["validation_provenance"][name]
                captured = bundle["gates"][name]
                self.assertEqual(captured["exit_code"], 0)
                self.assertEqual(captured["validated_head_sha"], implementation)
                self.assertEqual(captured["validated_tree_sha"], tree)
                execution_context = captured["execution_context"]
                self.assertEqual(execution_context["mode"], "detached_git_worktree")
                self.assertEqual(execution_context["head_sha"], implementation)
                self.assertEqual(execution_context["tree_sha"], tree)
                self.assertEqual(execution_context["status_porcelain"], "")
                self.assertEqual(
                    execution_context["transcript_locator"],
                    f"{TRANSCRIPT_RELATIVE}#/gates/{name}",
                )
                gate_transcript = transcripts["gates"][name].encode("utf-8")
                self.assertEqual(
                    execution_context["transcript_sha256"],
                    hashlib.sha256(gate_transcript).hexdigest(),
                )
                marker = re.search(
                    rb"\n__OMNIGENIS_EXIT_CODE__=(\d+)\n$",
                    gate_transcript,
                )
                self.assertIsNotNone(marker)
                assert marker is not None
                transcript_exit_code = int(marker.group(1))
                self.assertEqual(transcript_exit_code, captured["exit_code"])
                transcript_raw = gate_transcript[: marker.start()].decode("utf-8")
                self.assertEqual(transcript_raw, captured["raw_output"])
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
                if name in ("docs_language", "phase2d_hardening", "zero_identity_seal"):
                    self.assertEqual(sanitized, _validated_unittest_summary(transcript_raw))
                self.assertEqual(
                    hashlib.sha256(sanitized.encode("utf-8")).hexdigest(),
                    record["output_sha256"],
                )
        self.assertEqual(
            evidence["validation_provenance"]["diff_check"]["command"],
            f"git diff --check {evidence['base_main_sha']} {implementation}",
        )

    def test_unittest_gate_summary_rejects_rehashed_inconsistent_transcripts(self) -> None:
        """Reject forged success summaries even when every capture hash agrees."""
        baseline = self.load()
        baseline_bundle = self.load_validation_bundle(baseline)
        baseline_transcripts = self.load_execution_transcripts(baseline)
        for gate in ("docs_language", "phase2d_hardening", "zero_identity_seal"):
            for raw in (
                "Ran 999 tests in 0.1s\nFAILED (failures=1)\n",
                "Ran 999 tests in 0.1s\nOK\n",
            ):
                with self.subTest(gate=gate, raw=raw), tempfile.TemporaryDirectory() as tmp:
                    evidence = copy.deepcopy(baseline)
                    bundle = copy.deepcopy(baseline_bundle)
                    transcripts = copy.deepcopy(baseline_transcripts)
                    captured = bundle["gates"][gate]
                    captured["raw_output"] = raw
                    captured["raw_output_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
                    transcript = raw + "\n__OMNIGENIS_EXIT_CODE__=0\n"
                    transcripts["gates"][gate] = transcript
                    captured["execution_context"]["transcript_sha256"] = hashlib.sha256(
                        transcript.encode()
                    ).hexdigest()
                    record = evidence["validation_provenance"][gate]
                    record["raw_output_sha256"] = captured["raw_output_sha256"]
                    record["validation_bundle_record_sha256"] = hashlib.sha256(
                        _canonical_json_bytes(captured)
                    ).hexdigest()
                    bundle_bytes = _canonical_json_bytes(bundle)
                    evidence["validation_bundle"]["sha256"] = hashlib.sha256(bundle_bytes).hexdigest()
                    compressed = gzip.compress(_canonical_json_bytes(transcripts), mtime=0)
                    evidence["validation_provenance"]["implementation_suite_without_phase2d_evidence"][
                        "transcript_gzip_sha256"
                    ] = hashlib.sha256(compressed).hexdigest()
                    root = Path(tmp)
                    evidence_path = root / "evidence.json"
                    bundle_path = root / "bundle.json"
                    transcript_path = root / "transcript.gz"
                    evidence_path.write_bytes(_canonical_json_bytes(evidence))
                    bundle_path.write_bytes(bundle_bytes)
                    transcript_path.write_bytes(compressed)
                    probe = type(self)(
                        "test_required_validation_records_are_backed_by_captured_bundle_outputs"
                    )
                    result = unittest.TestResult()
                    with (
                        mock.patch(f"{__name__}.EVIDENCE", evidence_path),
                        mock.patch(f"{__name__}.VALIDATION_BUNDLE", bundle_path),
                        mock.patch(f"{__name__}.TRANSCRIPT", transcript_path),
                    ):
                        probe.run(result)
                    self.assertFalse(result.errors, result.errors)
                    self.assertTrue(result.failures, "inconsistent gate transcript was accepted")

    def test_zero_identity_seal_and_class_counts_are_evidence_bound(self) -> None:
        """Bind the plan's zero-seal claim to executed evidence and explicit P1-P4 counts."""
        evidence = self.load()
        bundle = self.load_validation_bundle(evidence)
        counts = evidence["zero_identity_class_counts"]
        inventory_record = bundle["gates"]["zero_identity_plane_inventory"]
        inventory_counts = json.loads(inventory_record["raw_output"])
        self.assertEqual(set(counts), {"P1", "P2", "P3", "P4"})
        self.assertEqual(inventory_counts, counts)
        for class_id, record in counts.items():
            self.assertEqual(record, {"path": 0, "blob": 0}, class_id)
        seal = evidence["validation_provenance"]["zero_identity_seal"]
        captured = bundle["gates"]["zero_identity_seal"]
        self.assertEqual(seal["exit_code"], 0)
        summary = _validated_unittest_summary(captured["raw_output"])
        self.assertEqual(seal["sanitized_output"], summary)

    def test_unittest_summary_validation_rejects_ambiguous_results(self) -> None:
        """Reject success substrings embedded beside unittest failure markers."""
        for raw_output in (
            "Ran 2 tests in 0.1s\nFAILED (failures=1)\nOK\n",
            "Ran 2 tests in 0.1s\nERROR: setup failed\nOK\n",
            "Ran 2 tests in 0.1s\nNOT OK\n",
            "prefix OK suffix\n",
        ):
            with self.subTest(raw_output=raw_output), self.assertRaises(AssertionError):
                _validated_unittest_summary(raw_output)

    def test_live_runner_readback_requires_complete_pagination(self) -> None:
        """Flatten every runner page and reject incomplete provider responses."""
        pages = [
            {
                "total_count": 2,
                "runners": [
                    {
                        "id": 21,
                        "name": "runner-a",
                        "status": "online",
                        "busy": False,
                        "labels": [{"name": "self-hosted"}],
                    }
                ],
            },
            {
                "total_count": 2,
                "runners": [
                    {
                        "id": 22,
                        "name": "runner-b",
                        "status": "online",
                        "busy": False,
                        "labels": [{"name": "self-hosted"}],
                    }
                ],
            },
        ]
        with (
            mock.patch(f"{__name__}._live_repository_full_name", return_value="owner/repo"),
            mock.patch(f"{__name__}._gh_api_json", return_value=pages) as readback,
        ):
            output = json.loads(_live_runner_output())
        self.assertEqual([item["id"] for item in output], [21, 22])
        readback.assert_called_once_with(
            "repos/owner/repo/actions/runners?per_page=100", "--paginate", "--slurp"
        )

        incomplete = [{"total_count": 2, "runners": pages[0]["runners"]}]
        with (
            mock.patch(f"{__name__}._live_repository_full_name", return_value="owner/repo"),
            mock.patch(f"{__name__}._gh_api_json", return_value=incomplete),
            self.assertRaisesRegex(AssertionError, "incomplete"),
        ):
            _live_runner_output()

    def test_runner_snapshot_has_authenticated_readback_provenance(self) -> None:
        """Bind volatile runner state to an executed raw-output bundle record."""
        evidence = self.load()
        bundle = self.load_validation_bundle(evidence)
        self.assertEqual(evidence["repository_id"], 1212760346)
        captured_at = evidence["runner_snapshot_captured_at"]
        self.assertRegex(captured_at, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        provenance = evidence["runner_readback_provenance"]
        captured = bundle["runner_readback"]
        replay = bundle["runner_replay"]
        self.assertEqual(provenance["source"], "GitHub REST API")
        self.assertIn("repositories/1212760346", provenance["command"])
        self.assertIn("actions/runners", provenance["command"])
        self.assertIn("per_page=100", provenance["command"])
        self.assertIn("--paginate", provenance["command"])
        self.assertIn("--slurp", provenance["command"])
        self.assertIn("pages=json.load", provenance["command"])
        self.assertIn("for page in pages for x in page", provenance["command"])
        self.assertIn("total_count", provenance["command"])
        self.assertEqual(captured["command"], provenance["command"])
        self.assertEqual(replay["command"], provenance["command"])
        self.assertEqual(captured["exit_code"], 0)
        self.assertEqual(replay["exit_code"], 0)
        self.assertRegex(captured["captured_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        sanitized = provenance["sanitized_output"]
        self.assertEqual(captured["raw_output"], sanitized)
        self.assertEqual(replay["raw_output"], sanitized)
        self.assertEqual(replay["raw_output_sha256"], captured["raw_output_sha256"])
        self.assertEqual(
            hashlib.sha256(captured["raw_output"].encode("utf-8")).hexdigest(),
            captured["raw_output_sha256"],
        )
        self.assertEqual(
            provenance["validation_bundle_record_sha256"],
            hashlib.sha256(_canonical_json_bytes(captured)).hexdigest(),
        )
        self.assertEqual(
            provenance["replay_bundle_record_sha256"],
            hashlib.sha256(_canonical_json_bytes(replay)).hexdigest(),
        )
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
