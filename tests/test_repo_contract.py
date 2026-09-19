import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_repo.py"


def load_validator():
    spec = importlib.util.spec_from_file_location("validate_repo", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load repository validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RepoContractTest(unittest.TestCase):
    def test_contract_detects_missing_paths(self):
        validator = load_validator()
        with tempfile.TemporaryDirectory() as directory:
            errors = validator.validate(Path(directory))
        self.assertTrue(any("missing required path" in error for error in errors))

    def test_contract_accepts_repository_scaffold(self):
        validator = load_validator()
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(validator.validate(root), [])


    def test_validate_repo_invokes_project_identity_guard(self):
        validator = load_validator()
        root = Path(__file__).resolve().parents[1]
        with patch.object(
            validator,
            "validate_project_identity",
            return_value=["project identity sentinel"],
        ) as guard:
            errors = validator.validate(root)
        guard.assert_called_once_with(root)
        self.assertIn("project identity sentinel", errors)

    def test_validate_repo_invokes_stage7_purpose_use_gate(self):
        validator = load_validator()
        root = Path(__file__).resolve().parents[1]
        with patch.object(
            validator,
            "validate_stage7_purpose_use",
            return_value=["stage7 purpose sentinel"],
        ) as gate:
            errors = validator.validate(root)
        gate.assert_called_once_with(root)
        self.assertIn("stage7 purpose sentinel", errors)

    def test_validate_repo_invokes_stage6_data_source_gate(self):
        validator = load_validator()
        root = Path(__file__).resolve().parents[1]
        with patch.object(
            validator,
            "validate_stage6_data_sources",
            return_value=["stage6 data sentinel"],
        ) as gate:
            errors = validator.validate(root)
        gate.assert_called_once_with(root)
        self.assertIn("stage6 data sentinel", errors)

    def test_validate_repo_invokes_stage5_license_gate(self):
        validator = load_validator()
        root = Path(__file__).resolve().parents[1]
        with patch.object(
            validator,
            "validate_stage5_license_gate",
            return_value=["stage5 license sentinel"],
        ) as gate:
            errors = validator.validate(root)
        gate.assert_called_once_with(root)
        self.assertIn("stage5 license sentinel", errors)

    def test_validate_repo_invokes_zero_identity_guard(self):
        validator = load_validator()
        root = Path(__file__).resolve().parents[1]
        with patch.object(
            validator,
            "validate_zero_identity",
            return_value=["zero identity sentinel"],
        ) as guard:
            errors = validator.validate(root)
        guard.assert_called_once_with(root)
        self.assertIn("zero identity sentinel", errors)

    def test_zero_identity_paths_are_required(self):
        validator = load_validator()
        for relative in (
            "config/zero_identity_policy.json",
            "scripts/zero_identity_guard.py",
        ):
            self.assertIn(relative, validator.REQUIRED_PATHS)

    def test_compliance_baseline_paths_are_required(self):
        validator = load_validator()
        compliance_paths = (
            "LICENSE",
            "COPYRIGHT.md",
            "AUTHORS.md",
            "THIRD_PARTY_NOTICES.md",
            "docs/compliance/LICENSING_POLICY.md",
            "docs/compliance/DEPENDENCY_POLICY.md",
            "licenses/README.md",
            "policy_engine/LICENSE",
            "config/identity_provenance_authorizations.json",
        )
        with tempfile.TemporaryDirectory() as directory:
            errors = validator.validate(Path(directory))
        for relative in compliance_paths:
            self.assertIn(relative, validator.REQUIRED_PATHS)
            self.assertIn(f"missing required path: {relative}", errors)

    def test_stage7_purpose_use_paths_are_required(self):
        validator = load_validator()
        for relative in (
            "config/data_use_purpose_policy.json",
            "config/data_use_purpose_matrix.json",
            "scripts/data_use_purpose_gate.py",
            "scripts/build_stage7_purpose_matrix.py",
            "scripts/validate_stage7_purpose_use.py",
            "docs/compliance/STAGE7_PURPOSE_USE_ENFORCEMENT.md",
            "docs/evidence/STAGE7_PURPOSE_USE_ENFORCEMENT_2026-09-19.json",
            "docs/evidence/STAGE7_PURPOSE_USE_VALIDATION_2026-09-19.txt",
        ):
            self.assertIn(relative, validator.REQUIRED_PATHS)

    def test_stage6_data_source_paths_are_required(self):
        validator = load_validator()
        for relative in (
            "config/data_source_registry.yaml",
            "scripts/validate_stage6_data_sources.py",
            "docs/compliance/STAGE6_SCIENTIFIC_DATA_LICENSING.md",
            "docs/evidence/STAGE6_SCIENTIFIC_DATA_LICENSING_2026-09-19.json",
        ):
            self.assertIn(relative, validator.REQUIRED_PATHS)

    def test_stage5_license_gate_paths_are_required(self):
        validator = load_validator()
        for relative in (
            "config/software_license_policy.json",
            "config/software_license_gate_registry.json",
            "locks/stage5-license-debt-baseline.json",
            "scripts/build_stage5_license_gate.py",
            "scripts/validate_stage5_license_gate.py",
            "docs/compliance/STAGE5_AUTOMATED_LICENSE_GATE.md",
            "docs/evidence/STAGE5_LICENSE_GATE_2026-09-18.json",
        ):
            self.assertIn(relative, validator.REQUIRED_PATHS)

    def test_identity_provenance_authorization_registry_is_required(self):
        validator = load_validator()
        relative = "config/identity_provenance_authorizations.json"
        with tempfile.TemporaryDirectory() as directory:
            errors = validator.validate(Path(directory))
        self.assertIn(relative, validator.REQUIRED_PATHS)
        self.assertIn(f"missing required path: {relative}", errors)

    def test_policy_engine_distribution_has_local_authorized_license(self):
        validator = load_validator()
        root = Path(__file__).resolve().parents[1]
        self.assertIn("policy_engine/LICENSE", validator.REQUIRED_PATHS)
        self.assertEqual(
            (root / "policy_engine" / "LICENSE").read_bytes(),
            (root / "LICENSE").read_bytes(),
        )
        pyproject = (root / "policy_engine" / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('license = {file = "LICENSE"}', pyproject)

    def test_official_validator_rejects_each_prohibited_fingerprint_class(self):
        validator = load_validator()
        policy_source = Path(__file__).resolve().parents[1] / "config" / "zero_identity_policy.json"
        mutations = {
            "P1": bytes.fromhex("6472687564736f6e"),
            "P2": bytes.fromhex("687564736f6e"),
            "P3": bytes.fromhex("63686174677074"),
            "P4": bytes.fromhex("636c61756465"),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git_executable = shutil.which("git")
            if git_executable is None:
                self.fail("git executable unavailable")
            subprocess.run([git_executable, "init", "-q"], cwd=root, check=True)
            (root / "config").mkdir()
            (root / "config" / "zero_identity_policy.json").write_bytes(
                policy_source.read_bytes()
            )
            (root / "config" / "identity_provenance_authorizations.json").write_text(
                json.dumps(
                    {
                        "schema": "omnigenis-identity-provenance-authorization-v1",
                        "authorizations": [],
                    }
                ),
                encoding="utf-8",
            )
            subprocess.run(
                [
                    git_executable,
                    "add",
                    "config/zero_identity_policy.json",
                    "config/identity_provenance_authorizations.json",
                ],
                cwd=root,
                check=True,
            )
            for class_id, payload in mutations.items():
                with self.subTest(class_id=class_id):
                    target = root / "identity-mutation.bin"
                    target.write_bytes(b"safe-" + payload + b"-fixture")
                    subprocess.run(
                        [git_executable, "add", "identity-mutation.bin"],
                        cwd=root,
                        check=True,
                    )
                    errors = validator.validate(root)
                    self.assertTrue(
                        any(
                            class_id in error and "identity-mutation.bin" in error
                            for error in errors
                        ),
                        errors,
                    )
                    subprocess.run(
                        [git_executable, "rm", "--cached", "-f", "identity-mutation.bin"],
                        cwd=root,
                        check=True,
                        stdout=subprocess.DEVNULL,
                    )
                    target.unlink()
    def test_identity_contract_paths_are_required(self):
        validator = load_validator()
        for relative in (
            "config/project_identity.json",
            "config/legacy_identity_ledger.json",
            "scripts/project_identity_guard.py",
            "docs/PROJECT_IDENTITY_CONTRACT.md",
        ):
            self.assertIn(relative, validator.REQUIRED_PATHS)

    def test_json_scan_reads_utf8_explicitly(self):
        validator = load_validator()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "utf8.json"
            payload.write_text('{"label": "≥"}', encoding="utf-8")
            original_read_text = Path.read_text

            def guarded_read_text(candidate, encoding=None, errors=None) -> str:
                if candidate == payload and encoding != "utf-8":
                    raise UnicodeDecodeError("charmap", b"\x8d", 0, 1, "test non-UTF-8 encoding")
                return original_read_text(candidate, encoding=encoding, errors=errors)

            with patch.object(Path, "read_text", guarded_read_text):
                with self.assertRaises(UnicodeDecodeError):
                    payload.read_text(encoding="latin-1")
                errors = validator.validate(root)
        self.assertFalse(any("invalid JSON: utf8.json" in error for error in errors), errors)

    def test_contract_rejects_micromamba_entrypoint_bypass(self):
        validator = load_validator()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runbook = root / "docs" / "MAGALU_PRIVATE_MCP_SETUP.md"
            runbook.parent.mkdir(parents=True)
            runbook.write_text(
                "PRE-DEPLOYMENT VALIDATION PASS / POST-DEPLOYMENT PENDENTE\n"
                "docker run --entrypoint /bin/bash image command\n",
                encoding="utf-8",
            )
            errors = validator.validate(root)
        self.assertIn("runbook must not bypass the micromamba container entrypoint", errors)

    def test_non_ascii_ruleset_manifest_is_reported_not_raised(self):
        """A non-ASCII SHA manifest must fail the check, not abort the whole run.

        read_text(encoding="ascii") raises on the first non-ASCII byte, and that
        UnicodeDecodeError used to propagate out of validate(), skipping every check
        after it instead of reporting the manifest as invalid.
        """
        validator = load_validator()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifests" / "RULESET_V3.4.sha256"
            manifest.parent.mkdir(parents=True)
            # A non-breaking space: valid UTF-8, not decodable as ASCII.
            manifest.write_text(
                "ab7a5f0ba9709e2f92a11ae4630f82ebae70385eab877ad3464fac6bd44a3580  "
                "REGRAS_PROJETO_GENOMA_VIGENTE_v3.4_2026-08-17.txt\u00a0\n",
                encoding="utf-8",
            )
            # This deliberately wrong dependency is checked after the ruleset manifest,
            # so its diagnostic proves validate() continued beyond the failed ASCII read.
            package = root / "mcp" / "package.json"
            package.parent.mkdir(parents=True)
            package.write_text(
                json.dumps({"devDependencies": {"fallow": "0.0.0"}}),
                encoding="utf-8",
            )
            with self.assertRaises(UnicodeDecodeError):
                manifest.read_text(encoding="ascii")
            errors = validator.validate(root)
        self.assertIn("ruleset external manifest does not match the verified v3.4 artifact", errors)
        self.assertIn("mcp/package.json must pin fallow 3.16.0 exactly", errors)


if __name__ == "__main__":
    unittest.main()
