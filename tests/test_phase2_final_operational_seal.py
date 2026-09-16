"""Verify the post-merge Phase 2 operational seal without rewriting history."""

from pathlib import Path
import copy
import hashlib
import json
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_RELATIVE = (
    "docs/superpowers/evidence/2026-09-16-omnigenis-phase2-final-operational-seal.json"
)
BUNDLE_RELATIVE = (
    "docs/superpowers/evidence/2026-09-16-omnigenis-phase2-final-operational-bundle.json"
)
HISTORICAL_RELATIVE = (
    "docs/superpowers/evidence/2026-09-13-omnigenis-phase2d-legacy-elimination.json"
)
EVIDENCE = ROOT / EVIDENCE_RELATIVE
BUNDLE = ROOT / BUNDLE_RELATIVE
HISTORICAL = ROOT / HISTORICAL_RELATIVE
HISTORICAL_SHA256 = "d0e2192b3a4c069f46cada3d480c92d4ad5e34c9c03014cc053ad2284537be84"
MAIN_SHA = "1cb09951fac586b9774e550815d3718993fc23ec"
REPOSITORY_ID = 1212760346
PROTECTED_MAIN_RULESET_ID = 21303100
GIT = shutil.which("git")
if GIT is None:
    raise RuntimeError("git executable is required by the Phase 2 final seal contract")


def _git(*args: str) -> str:
    return subprocess.check_output([GIT, *args], cwd=ROOT, text=True).strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Phase2FinalOperationalSealTest(unittest.TestCase):
    """Bind the final operational closure to immutable historical evidence."""

    def load_evidence(self) -> dict:
        self.assertTrue(EVIDENCE.is_file(), f"missing evidence: {EVIDENCE}")
        return json.loads(EVIDENCE.read_text(encoding="utf-8"))

    def load_bound_json(self, binding: dict) -> dict:
        path = ROOT / binding["path"]
        self.assertTrue(path.is_file(), f"missing bound artifact: {path}")
        self.assertEqual(binding["sha256"], _sha256(path))
        return json.loads(path.read_text(encoding="utf-8"))

    def load_bundle(self, evidence: dict) -> dict:
        self.assertTrue(BUNDLE.is_file(), f"missing bundle: {BUNDLE}")
        binding = evidence["operational_bundle"]
        self.assertEqual(binding["path"], BUNDLE_RELATIVE)
        self.assertEqual(binding["sha256"], _sha256(BUNDLE))
        bundle = json.loads(BUNDLE.read_text(encoding="utf-8"))
        self.assertEqual(bundle["schema"], "omnigenis-phase2-final-operational-bundle-v1")
        self.assertEqual(bundle["repository_id"], REPOSITORY_ID)
        self.assertEqual(bundle["main_sha"], MAIN_SHA)
        self.assertFalse(bundle["secret_material_recorded"])
        self.assertEqual(
            bundle["secret_material_recorded"], evidence["secret_material_recorded"]
        )
        return bundle

    def test_historical_blocker_is_preserved_and_superseded(self) -> None:
        evidence = self.load_evidence()
        self.assertEqual(_sha256(HISTORICAL), HISTORICAL_SHA256)
        historical = json.loads(HISTORICAL.read_text(encoding="utf-8"))
        self.assertEqual(historical["phase2_global_seal"], "BLOCKED")
        self.assertEqual(
            historical["runner_name_reregistration"]["status"],
            "BLOCKED_RUNTIME_RESOURCE_GATE",
        )
        supersedes = evidence["supersedes"]
        self.assertEqual(supersedes["path"], HISTORICAL_RELATIVE)
        self.assertEqual(supersedes["sha256"], HISTORICAL_SHA256)
        self.assertEqual(supersedes["mutation"], "NONE_HISTORICAL_RECORD_PRESERVED")

    def test_seal_identity_and_main_anchor_are_exact(self) -> None:
        evidence = self.load_evidence()
        self.assertEqual(evidence["schema"], "omnigenis-phase2-final-operational-seal-v1")
        self.assertEqual(evidence["status"], "VERIFIED")
        self.assertEqual(evidence["phase2_global_seal"], "VERIFIED")
        self.assertEqual(evidence["repository_id"], REPOSITORY_ID)
        self.assertEqual(evidence["main_sha"], MAIN_SHA)
        subprocess.run(
            [GIT, "merge-base", "--is-ancestor", MAIN_SHA, "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        self.assertFalse(evidence["secret_material_recorded"])

    def test_runtime_resource_gate_is_executed_and_verified(self) -> None:
        evidence = self.load_evidence()
        gate = evidence["runtime_resource_gate"]
        self.assertEqual(gate["status"], "EXECUTED_AND_VERIFIED")
        self.assertEqual(
            gate["proofs"],
            {
                "container_creation_restart_mechanism": "VERIFIED",
                "container_image_digest": "VERIFIED",
                "container_mounts": "VERIFIED",
                "registration_workflow": "VERIFIED",
                "rollback_procedure": "VERIFIED",
                "throwaway_registration_rehearsal": "PASS",
            },
        )
        self.assertFalse(gate["secrets_captured"])
        sources = gate["source_artifacts"]
        self.assertEqual(
            set(sources),
            {
                "runtime_resource_gate_v6",
                "runtime_resource_gate_v7",
                "registration_rehearsal",
                "runner01_contract",
                "operational_checkpoint",
            },
        )
        for binding in sources.values():
            source = self.load_bound_json(binding)
            self.assertEqual(source["external_raw_sha256"], binding["external_raw_sha256"])
            self.assertFalse(source["secrets_captured"])

    def test_final_runner_readback_and_replay_are_exact(self) -> None:
        evidence = self.load_evidence()
        bundle = self.load_bundle(evidence)
        expected = [
            {
                "busy": False,
                "id": 24,
                "labels": ["self-hosted", "Linux", "X64", "omnigenis-isolated", "omnigenis-01"],
                "name": "omnigenis-runner-01",
                "status": "online",
            },
            {
                "busy": False,
                "id": 25,
                "labels": ["self-hosted", "Linux", "X64", "omnigenis-isolated", "omnigenis-02"],
                "name": "omnigenis-runner-02",
                "status": "online",
            },
        ]
        self.assertEqual(bundle["runner_readback"]["result"], expected)
        self.assertEqual(bundle["runner_replay"]["result"], expected)
        self.assertNotEqual(
            bundle["runner_readback"]["execution_id"],
            bundle["runner_replay"]["execution_id"],
        )
        self.assertEqual(evidence["final_runners"], expected)

    def test_both_controlled_canaries_are_bound_to_canonical_runners(self) -> None:
        evidence = self.load_evidence()
        bundle = self.load_bundle(evidence)
        expected = {
            "runner01": (35123838618, 104887984148, 24, "omnigenis-runner-01"),
            "runner02": (35125654567, 104894004131, 25, "omnigenis-runner-02"),
        }
        for key, (run_id, job_id, runner_id, runner_name) in expected.items():
            record = bundle["controlled_canaries"][key]
            self.assertEqual(record["workflow_run_id"], run_id)
            self.assertEqual(record["workflow_head_sha"], MAIN_SHA)
            self.assertEqual(record["workflow_conclusion"], "success")
            self.assertEqual(record["static_job_id"], job_id)
            self.assertEqual(record["static_job_conclusion"], "success")
            self.assertEqual(record["static_runner_id"], runner_id)
            self.assertEqual(record["static_runner_name"], runner_name)
            self.assertEqual(record["four_plane_audit_conclusion"], "success")

    def _assert_completion_consistency(self, evidence: dict, bundle: dict) -> None:
        gate = evidence["final_completion_gate"]
        self.assertEqual(gate["tracked_zero_identity_findings"], 0)
        self.assertEqual(gate["project_identity_guard"], "PASS")
        self.assertEqual(gate["repository_validator"], "PASS")
        self.assertEqual(gate["supply_chain_lock"], "PASS")
        self.assertEqual(gate["code_language_guard"], "PASS")
        self.assertEqual(gate["residual_language_audit"], "PASS_CLEAN")
        self.assertEqual(gate["ruleset_v3_4_sha_pinned"], "PASS")

        main = gate["main_operational_canary"]
        candidate = gate["evidence_pr_candidate_validation"]
        bundle_main = bundle["final_test_summary"]["main_operational_canary"]
        bundle_candidate = bundle["final_test_summary"]["evidence_pr_candidate_validation"]
        self.assertEqual(main, bundle_main)
        self.assertEqual(candidate, bundle_candidate)
        self.assertEqual(main["root_suite"], {"tests": 1219, "failures": 0, "skipped": 2})
        self.assertEqual(
            main["mcp_suite"],
            {"tests": 46, "passed": 45, "failures": 0, "skipped": 1},
        )
        self.assertEqual(candidate["root_suite"], {"tests": 1228, "failures": 0, "skipped": 2})
        self.assertEqual(
            candidate["mcp_suite"],
            {"tests": 46, "passed": 45, "failures": 0, "skipped": 1},
        )
        self.assertNotEqual(candidate["validated_commit_sha"], MAIN_SHA)
        subprocess.run(
            [GIT, "merge-base", "--is-ancestor", candidate["validated_commit_sha"], "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        main_source = self.load_bound_json(main["source_artifact"])
        candidate_source = self.load_bound_json(candidate["source_artifact"])
        self.assertEqual(main_source["root_suite"], main["root_suite"])
        self.assertEqual(main_source["mcp_suite"], main["mcp_suite"])
        self.assertEqual(candidate_source["root_suite"], candidate["root_suite"])
        self.assertEqual(candidate_source["mcp_suite"], candidate["mcp_suite"])
        self.assertEqual(
            candidate_source["candidate_commit_at_execution"],
            candidate["validated_commit_sha"],
        )

        gate_map = {
            "zero_identity": "tracked_zero_identity_findings",
            "project_identity": "project_identity_guard",
            "repository_validator": "repository_validator",
            "supply_chain": "supply_chain_lock",
            "code_language": "code_language_guard",
            "residual_language": "residual_language_audit",
        }
        for bundle_key, seal_key in gate_map.items():
            self.assertEqual(bundle["final_main_gates"][bundle_key]["exit_code"], 0)
            if seal_key != "tracked_zero_identity_findings":
                self.assertTrue(str(gate[seal_key]).startswith("PASS"))

    def test_final_completion_gate_matches_executed_results(self) -> None:
        evidence = self.load_evidence()
        bundle = self.load_bundle(evidence)
        self._assert_completion_consistency(evidence, bundle)

        tampered = copy.deepcopy(bundle)
        tampered["final_test_summary"]["evidence_pr_candidate_validation"][
            "root_suite"
        ]["failures"] = 1
        with self.assertRaises(AssertionError):
            self._assert_completion_consistency(evidence, tampered)

    def test_governance_and_manual_merge_remain_intact(self) -> None:
        evidence = self.load_evidence()
        bundle = self.load_bundle(evidence)
        governance = bundle["governance_readback"]
        self.assertEqual(governance["protected_main_ruleset_id"], PROTECTED_MAIN_RULESET_ID)
        self.assertEqual(governance["enforcement"], "active")
        self.assertEqual(governance["required_status_check_count"], 14)
        self.assertEqual(governance["bypass_actor_count"], 0)
        merge = bundle["pr72_merge_record"]
        self.assertEqual(merge["pull_request"], 72)
        self.assertEqual(merge["state"], "MERGED")
        self.assertEqual(merge["merge_commit_sha"], MAIN_SHA)
        self.assertFalse(merge["auto_merge_enabled"])
        self.assertEqual(merge["review_decision"], "APPROVED")

    def test_new_evidence_contains_no_retired_personal_identity(self) -> None:
        forbidden = bytes.fromhex("6472687564736f6e").lower()
        self.assertNotIn(forbidden, EVIDENCE.read_bytes().lower())
        self.assertNotIn(forbidden, BUNDLE.read_bytes().lower())

    def test_operational_source_hashes_are_exact(self) -> None:
        evidence = self.load_evidence()
        expected_raw = {
            "runtime_resource_gate_v6": "ea93581c8e3feac98220da1ec4675b80257206dbf1582f516e96b63db9800be8",
            "runtime_resource_gate_v7": "bae1c5bf22a8bfaf8be1f25fbf8f40c2539d4cb991d43527913e00798fca84c4",
            "registration_rehearsal": "50f8980f870041e97b51016c52d0b4e6ab496f8171055545d7ed1c2eb0552d86",
            "runner01_contract": "2e59c06b681f569b1a5c8ba9e8b5e59cb5f0ab71658deed2a3bd9e2095f8e8c3",
            "operational_checkpoint": "d229f441fb4ba9fc850d834c5aacc7deb981bfa36d435cec12e5b87873394998",
        }
        bindings = evidence["runtime_resource_gate"]["source_artifacts"]
        self.assertEqual(set(bindings), set(expected_raw))
        for key, expected_sha in expected_raw.items():
            source = self.load_bound_json(bindings[key])
            self.assertEqual(bindings[key]["external_raw_sha256"], expected_sha)
            self.assertEqual(source["external_raw_sha256"], expected_sha)

        for section in (
            evidence["final_completion_gate"]["main_operational_canary"],
            evidence["final_completion_gate"]["evidence_pr_candidate_validation"],
        ):
            self.load_bound_json(section["source_artifact"])


if __name__ == "__main__":
    unittest.main()
