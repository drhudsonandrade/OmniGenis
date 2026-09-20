#!/usr/bin/env python3
"""Validate static repository safety and the shared GENOMA v3.4 sealed contract."""
from __future__ import annotations

import ast
import base64
import csv
import hashlib
import json
import re
import shlex
import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.code_language_guard import (  # noqa: E402
    LanguagePolicyError,
    validate_code_language,
)
from scripts.project_identity_guard import validate_project_identity  # noqa: E402
from scripts.residual_language_audit import (  # noqa: E402
    ResidualLanguageError,
    audit_repository,
)
from scripts.sealed_ruleset import (  # noqa: E402
    EXPECTED_NAME,
    EXPECTED_SHA,
    SealedRulesetError,
    verify_transport,
)
from scripts.zero_identity_guard import (  # noqa: E402
    PolicyError,
    RepositoryScanError,
    validate_zero_identity,
)
from scripts.validate_stage4_compliance import collect_errors as validate_stage4_compliance  # noqa: E402
from scripts.validate_stage5_license_gate import collect_errors as validate_stage5_license_gate  # noqa: E402
from scripts.validate_stage6_data_sources import collect_errors as validate_stage6_data_sources  # noqa: E402
from scripts.validate_stage7_purpose_use import collect_errors as validate_stage7_purpose_use  # noqa: E402
from scripts.validate_stage8_contribution_provenance import collect_errors as validate_stage8_contribution_provenance  # noqa: E402
from scripts.validate_stage9_genetic_privacy import collect_errors as validate_stage9_genetic_privacy  # noqa: E402

CANONICAL_RULESET = EXPECTED_NAME
CANONICAL_RULESET_SHA256 = EXPECTED_SHA
FALLOW_ACTION_SHA = "45fd28766199acb1f939f6862274a37aad12770b"
PRODUCTION_WITNESS_CAPABILITY_GUARD = "${{ vars.GENOMA_PRODUCTION_WITNESS_ENABLED == 'true' }}"
REQUIRED_PATHS = (
    "LICENSE", "COPYRIGHT.md", "AUTHORS.md", "THIRD_PARTY_NOTICES.md",
    "docs/compliance/LICENSING_POLICY.md", "docs/compliance/DEPENDENCY_POLICY.md", "licenses/README.md",
    "licenses/pypdfium2-5.13.0/README.md", "docs/evidence/PDFIUM_COORDINATE_MIGRATION_2026-09-17.json",
    "docs/evidence/PDFIUM_STATIC_PIXEL_QA_200DPI_2026-09-17.json",
    "docs/evidence/STRONG_COPYLEFT_RUNTIME_CLEANUP_2026-09-17.json",
    "docs/evidence/stage3/THIRD_PARTY_NOTICES_STAGE3.md",
    "docs/evidence/STAGE4_THIRD_PARTY_INVENTORY_2026-09-18.json",
    "docs/compliance/STAGE4_THIRD_PARTY_INVENTORY.md",
    "policy_engine/LICENSE",
    ".fallowrc.json", ".github/workflows/fallow.yml", ".github/workflows/scaffold-validation.yml",
    ".github/workflows/genoma-policy-engine.yml", ".github/workflows/genoma-production-ceremony.yml",
    ".github/workflows/genoma-production-witness.yml", ".github/workflows/genoma-ngs-runtime-gate.yml",
    ".github/workflows/genoma-snp-array.yml", ".gitignore", "Dockerfile", "environment.yml", "main.nf",
    "nextflow.config", "workflows/wgs.nf", "workflows/array.nf", "array_pipeline/qc.py",
    "array_pipeline/annotation.py", "array_pipeline/targets.py", "config/partial_genome_annotation_targets.json",
    "config/code_language_policy.json", "config/code_language_legacy_baseline.json",
    "config/residual_language_classification.json",
    "config/project_identity.json", "config/legacy_identity_ledger.json",
    "config/zero_identity_policy.json", "config/identity_provenance_authorizations.json",
    "manifests/GRCh38.sources.tsv", "manifests/GRCh38.lock.sha256.example", "manifests/RULESET_V3.4.sha256",
    "normative/sealed/MANIFEST.json", "normative/sealed/README.md",
    "scripts/__init__.py", "scripts/sealed_ruleset.py", "scripts/code_language_guard.py",
    "scripts/residual_language_audit.py", "scripts/project_identity_guard.py",
    "scripts/zero_identity_guard.py", "scripts/pdfium_backend.py", "scripts/build_report_coordinate_pack.py",
    "scripts/run_pdfium_static_pixel_qa.py",
    "scripts/check_versions.sh", "scripts/fetch_grch38.sh",
    "scripts/build_bwa_mem2_index.sh", "scripts/validate_grch38.sh", "scripts/validate_bwa_mem2_functional.sh",
    "scripts/generate_canary.py", "scripts/score_variants.py", "scripts/run_canary.sh", "scripts/verify_ruleset.sh",
    "scripts/materialize_ruleset.py", "scripts/bootstrap_attestation.py", "scripts/run_live_post_deployment_smoke.py",
    "scripts/runtime_resource_gate.py", "scripts/runtime_stack.py", "scripts/prepare_latest_candidate.py",
    "scripts/promote_latest_candidate.py", "scripts/freshness_gate.py", "scripts/latest_runtime_resource_gate.py",
    "scripts/verify_runtime_gate_manifest.py", "scripts/wgs_consent_gate.py", "scripts/wgs_input_gate.py",
    "scripts/wgs_align_or_stage.sh", "scripts/build_wgs_curated_manifest.py", "scripts/query_evidence.py",
    "scripts/build_adapter_capabilities.py", "scripts/run_snp_array.py", "scripts/annotate_partial_genome.py",
    "scripts/build_array_case_manifest.py", "scripts/verify_prebuilt_bwa_mem2_bundle.py",
    "scripts/verify_supply_chain_lock.py", "scripts/generate_report.py", "scripts/generate_all_reports.py",
    "reporting/__init__.py", "reporting/catalog.json", "reporting/engine.py", "reporting/editorial_v3.py",
    "reporting/editorial_v3_hifi.py", "reporting/locale_pt_br.py",
    "reporting/requirements.in", "reporting/requirements.txt", "reporting/reference_v3_manifest.json",
    "reporting/legacy_field_aliases.json",
    "template_store/v3.0/MANIFEST.json", "locks/actions-lock.json", "locks/runtime-lock.json",
    "locks/conda-linux-64-resolution.json", "locks/conda-linux-64-explicit.txt",
    "locks/base-image-software.json", "locks/python-license-metadata.json",
    "locks/action-license-metadata.json", "locks/sbom-tool-lock.json",
    "config/third_party_software_registry.json", "scripts/build_third_party_registry.py",
    "scripts/validate_stage4_compliance.py", "scripts/generate_stage4_sbom.sh",
    "scripts/validate_stage4_sbom.py",
    "config/software_license_policy.json", "config/software_license_gate_registry.json",
    "locks/stage5-license-debt-baseline.json", "scripts/build_stage5_license_gate.py",
    "scripts/validate_stage5_license_gate.py", "docs/compliance/STAGE5_AUTOMATED_LICENSE_GATE.md",
    "docs/evidence/STAGE5_LICENSE_GATE_2026-09-18.json",
    "config/data_source_registry.yaml", "scripts/validate_stage6_data_sources.py",
    "docs/compliance/STAGE6_SCIENTIFIC_DATA_LICENSING.md",
    "docs/evidence/STAGE6_SCIENTIFIC_DATA_LICENSING_2026-09-19.json",
    "config/data_use_purpose_policy.json", "config/data_use_purpose_matrix.json",
    "scripts/data_use_purpose_gate.py", "scripts/build_stage7_purpose_matrix.py",
    "scripts/validate_stage7_purpose_use.py", "docs/compliance/STAGE7_PURPOSE_USE_ENFORCEMENT.md",
    "docs/evidence/STAGE7_PURPOSE_USE_ENFORCEMENT_2026-09-19.json",
    "docs/evidence/STAGE7_PURPOSE_USE_VALIDATION_2026-09-19.txt",
    "config/contribution_provenance_policy.json", "config/contribution_provenance_ledger.json",
    "scripts/build_stage8_change_manifest.py", "scripts/validate_stage8_contribution_provenance.py",
    "docs/compliance/STAGE8_CONTRIBUTION_PROVENANCE.md", "CONTRIBUTING.md",
    "config/genetic_data_privacy_policy.json", "scripts/genetic_data_privacy_gate.py",
    "scripts/validate_stage9_genetic_privacy.py", "docs/compliance/STAGE9_LGPD_GENETIC_DATA.md",
    "evidence_adapters/__init__.py", "policy_engine/pyproject.toml", "policy_engine/genoma_policy/engine.py",
    "policy_engine/genoma_policy/attestation.py", "policy_engine/genoma_policy/ledger.py",
    "policy_engine/genoma_policy/version.py", "policy_engine/policy/schema/execution-manifest.schema.json",
    "policy_engine/Dockerfile", "policy_engine/docker-compose.yml", "mcp/package.json", "mcp/package-lock.json",
    "mcp/tsconfig.json", "mcp/src/server.ts", "deploy/docker-compose.yml",
    "deploy/attestations/bootstrap-project-v3.4.json", "adapters/README.md", "adapters/config.example.json",
    "docs/CODE_LANGUAGE_POLICY.md", "docs/FALLOW_SECURITY_REVIEW.md", "docs/GITHUB_MOBILE_IMPORT.md",
    "docs/MAGALU_PRIVATE_MCP_SETUP.md", "docs/PROJECT_IDENTITY_CONTRACT.md",
    "docs/PRE_DEPLOYMENT_VALIDATION_2026-08-15.md", "docs/RECOVERY_AND_ACTIVATION_RUNBOOK.md", "docs/PR_BODY.md",
    "docs/DETERMINISTIC_ENGINE.md", "docs/PRODUCTION_CEREMONY.md", "docs/PORTABILITY_MATRIX.md",
    "docs/GRCH38_COMPUTE_STRATEGY.md", "docs/audits/GENOMA_V0.8_PREIMPLEMENTATION_AUDIT_2026-08-16.md",
)
EXPECTED_ARTIFACTS = {
    "Homo_sapiens_assembly38.fasta", "Homo_sapiens_assembly38.fasta.fai", "Homo_sapiens_assembly38.dict",
    "gencode.v50.primary_assembly.annotation.gtf.gz", "Homo_sapiens_assembly38.dbsnp138.vcf.gz",
    "Homo_sapiens_assembly38.dbsnp138.vcf.gz.tbi", "Mills_and_1000G_gold_standard.indels.hg38.vcf.gz",
    "Mills_and_1000G_gold_standard.indels.hg38.vcf.gz.tbi", "hg38-blacklist.v2.bed.gz",
}
EXPECTED_EVIDENCE_ADAPTERS = {"clinvar", "clingen", "cpic", "clinpgx", "gnomad", "pgs_catalog"}
FORBIDDEN_SUFFIXES = (".fastq", ".fq", ".bam", ".bai", ".cram", ".crai", ".vcf", ".tbi")
SKIP_PARTS = {".git", "node_modules", "dist", "__pycache__", ".pytest_cache"}
TEXT_IDENTITY_SUFFIXES = {
    ".json", ".md", ".nf", ".py", ".rego", ".sh", ".toml", ".ts", ".txt", ".yaml", ".yml",
}
SUPERSEDED_IDENTITY_TEST_FIXTURES = frozenset(
    {
        "policy_engine/tests/test_policy_engine.py",
        "tests/test_v34_activation_contract.py",
        "tests/test_validate_repo_static_fstrings.py",
        "tests/test_superseded_identity_scanner.py",
    }
)
MISSING_PATH_HINTS = {
    "manifests/GRCh38.sources.tsv": "restore the tracked GRCh38 source manifest before running the Runtime/Resource Gate",
    "manifests/RULESET_V3.4.sha256": "restore the tracked canonical SHA manifest; do not add an active plaintext ruleset to the repository",
    "normative/sealed/MANIFEST.json": "restore the sealed transport manifest; materialization is runtime-only after the transport is valid",
}
ACTIVE_IDENTITY_SURFACES = (
    "scripts/run_live_post_deployment_smoke.py", "scripts/verify_ruleset.sh", "scripts/genoma_audit.py",
    "scripts/run_snp_array.py", "scripts/annotate_partial_genome.py", "scripts/build_wgs_curated_manifest.py",
    "scripts/build_array_case_manifest.py", "scripts/generate_report.py", "scripts/generate_all_reports.py",
    "array_pipeline/qc.py", "array_pipeline/annotation.py", "workflows/wgs.nf", "workflows/array.nf",
    "main.nf", "nextflow.config", "Dockerfile", "deploy/docker-compose.yml", "mcp/src/server.ts",
    "reporting/engine.py", "reporting/editorial_v3_hifi.py", "reporting/template_v3.py",
    "policy_engine/Dockerfile", "policy_engine/docker-compose.yml",
    "policy_engine/pyproject.toml", "policy_engine/README_GENOMA_POLICY.md", "policy_engine/tests/test_server.py",
    "policy_engine/genoma_policy/__init__.py", "policy_engine/genoma_policy/cli.py",
    "policy_engine/genoma_policy/engine.py", "policy_engine/genoma_policy/gates_core.py",
    "policy_engine/genoma_policy/gates_audit.py", "policy_engine/genoma_policy/models.py",
    "policy_engine/genoma_policy/paths.py", "policy_engine/genoma_policy/ruleset.py",
    "policy_engine/genoma_policy/smoke.py", "policy_engine/policy/rego/genoma.rego",
    "policy_engine/policy/rego/genoma_test.rego", "policy_engine/policy/schema/execution-manifest.schema.json",
    "locks/runtime-lock.json", ".github/workflows/genoma-policy-engine.yml",
    ".github/workflows/genoma-production-ceremony.yml", ".github/workflows/genoma-production-witness.yml",
    ".github/workflows/genoma-ngs-runtime-gate.yml", ".github/workflows/genoma-snp-array.yml",
    "docs/DETERMINISTIC_ENGINE.md", "docs/PRODUCTION_CEREMONY.md", "docs/MAGALU_PRIVATE_MCP_SETUP.md",
    "docs/RECOVERY_AND_ACTIVATION_RUNBOOK.md", "docs/SNP_ARRAY_PARTIAL_GENOME.md", "docs/PR_BODY.md",
)


# A superseded identity is only dangerous when something can activate it. These keys
# name an obsolete ruleset uniquely, so any occurrence outside declared history is an
# error wherever it appears.
SUPERSEDED_STRONG_KEYS = ("canonical_filename", "manifest_filename", "rule_id_prefix", "raw_sha256")
# These keys are bare version numbers and dates. They occur legitimately in prose,
# changelogs, dated filenames, unrelated timestamps and negative regression fixtures,
# so they are errors only inside an active normative declaration.
SUPERSEDED_WEAK_KEYS = ("version", "effective_date", "iso_date")


def _load_superseded_contract() -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Read the superseded-identity registry from every archived ruleset version.

    Refuses when no registry is present rather than scanning for nothing: a check with an
    empty pattern list passes every repository, including one that has reintroduced an
    identity a previous version retired.
    """
    fixtures = sorted((ROOT / "docs" / "history").glob("*/superseded-identities.json"))
    if not fixtures:
        raise RuntimeError("superseded identity registry is missing from docs/history")
    strong: list[str] = []
    weak: list[str] = []
    forbidden_paths: list[str] = []
    for fixture in fixtures:
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        if payload.get("schema") != "genoma-superseded-identity-v1" or payload.get("status") != "HISTORICAL":
            raise RuntimeError(f"invalid superseded identity registry: {fixture}")
        required = (*SUPERSEDED_STRONG_KEYS, *SUPERSEDED_WEAK_KEYS, "forbidden_active_paths")
        missing = [key for key in required if not payload.get(key)]
        if missing:
            raise RuntimeError(f"incomplete superseded identity registry {fixture}: {missing}")
        strong.extend(str(payload[key]) for key in SUPERSEDED_STRONG_KEYS)
        weak.extend(str(payload[key]) for key in SUPERSEDED_WEAK_KEYS)
        forbidden = payload["forbidden_active_paths"]
        if not isinstance(forbidden, list) or not all(isinstance(item, str) and item for item in forbidden):
            raise RuntimeError(f"invalid forbidden_active_paths in {fixture}")
        forbidden_paths.extend(forbidden)
    return tuple(dict.fromkeys(strong)), tuple(dict.fromkeys(weak)), tuple(dict.fromkeys(forbidden_paths))


SUPERSEDED_STRONG_TOKENS, SUPERSEDED_WEAK_TOKENS, FORBIDDEN_ACTIVE_PATHS = _load_superseded_contract()
# Declared active surfaces stay strict: any superseded token at all is an error there.
OLD_ACTIVE_TOKENS = SUPERSEDED_STRONG_TOKENS + SUPERSEDED_WEAK_TOKENS

# Names that make a Python string constant an identity definition rather than a mention.
IDENTITY_BINDING_PATTERN = re.compile(
    r"RULESET|CANONICAL|NORMATIV|VIGENTE|IDENTITY|IDENTIDADE|RULE_ID|EFFECTIVE|CURRENT|VERS(AO|ÃO|ION)",
    re.IGNORECASE,
)
# Markers that turn a line of non-Python text into an active normative declaration.
ACTIVE_DECLARATION_MARKERS = (
    "STATUS NORMATIVO", "VERSÃO NORMATIVA", "VERSAO NORMATIVA", "ARQUIVO CANÔNICO", "ARQUIVO CANONICO",
    "VIGENTE", "ruleset_version", "RULESET_VERSION", "CURRENT_RULESET", "canonical_filename",
    "effective_date", "DATA FORMAL DE EMISSÃO", "DATA FORMAL DE EMISSAO",
)


def validate_sealed_ruleset(root: Path, errors: list[str]) -> None:
    """Record an error unless the sealed transport decodes to the canonical ruleset."""
    try:
        verify_transport(root / "normative" / "sealed")
    except (OSError, ValueError, SealedRulesetError) as exc:
        errors.append(f"sealed normative transport invalid: {type(exc).__name__}: {exc}")


def validate_active_identity_text(text: str, relative: str, errors: list[str]) -> None:
    """Reject each superseded active-identity token independently."""
    for token in OLD_ACTIVE_TOKENS:
        if token in text:
            errors.append(f"active ruleset surface still references superseded identity: {relative}: {token}")


def _constant_value(node: ast.AST) -> str | int | float | bool | None:
    """The literal behind an AST node, or None when it is computed at runtime.

    Only literals can be checked statically; a value assembled at runtime is deliberately
    not guessed at, because guessing wrong would either pass a violation or fail a legal file.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float, bool)):
        return node.value
    return None


def _formatted_constant(value: ast.FormattedValue) -> str | None:
    """The literal text an f-string placeholder expands to, when it is a constant.

    Lets an identity spelled through an f-string be checked like a plain string, so the
    superseded-identity scan cannot be evaded by interpolating a constant.
    """
    constant = _constant_value(value.value)
    if constant is None:
        return None
    if value.conversion == -1:
        converted: object = constant
    elif value.conversion == 115:  # !s
        converted = str(constant)
    elif value.conversion == 114:  # !r
        converted = repr(constant)
    elif value.conversion == 97:  # !a
        converted = ascii(constant)
    else:
        return None
    if value.format_spec is None:
        return str(converted)
    format_spec = _constant_string(value.format_spec)
    if format_spec is None:
        return None
    try:
        return format(converted, format_spec)
    except (TypeError, ValueError):
        return None


def _constant_string(node: ast.AST) -> str | None:
    """Fold static string expressions without executing repository code."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _constant_string(node.left)
        right = _constant_string(node.right)
        if left is not None and right is not None:
            return left + right
        return None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
                continue
            if isinstance(value, ast.FormattedValue):
                rendered = _formatted_constant(value)
                if rendered is None:
                    return None
                parts.append(rendered)
                continue
            return None
        return "".join(parts)
    return None


def _binding_names(node: ast.AST) -> list[str]:
    """Names a value is bound to: assignment targets, dict keys and keyword arguments."""
    names: list[str] = []
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.append(target.id)
            elif isinstance(target, ast.Attribute):
                names.append(target.attr)
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, (ast.Name, ast.Attribute)):
        names.append(node.target.id if isinstance(node.target, ast.Name) else node.target.attr)
    elif isinstance(node, ast.keyword) and node.arg:
        names.append(node.arg)
    return names


def _python_constant_strings(text: str) -> tuple[str, ...]:
    """Every statically foldable string in a module, including split literals.

    Constant folding is what stops a superseded identity from being smuggled past a
    raw-text scan as ``"GENOMA-V3." + "3"`` or ``f"GENOMA-V3.{3}"``.
    """
    tree = ast.parse(text)
    values = (_constant_string(node) for node in ast.walk(tree))
    return tuple(dict.fromkeys(value for value in values if value is not None))


def _identity_declaration_strings(text: str) -> tuple[str, ...]:
    """Python string constants that actually define a normative identity.

    Only values bound to an identity-shaped name are returned — an assignment target,
    a dict key or a keyword argument. Comments, docstrings, prose and unrelated
    literals such as test timestamps are excluded by construction, so a historical
    mention can never be mistaken for an active normative source.
    """
    tree = ast.parse(text)
    declarations: list[str] = []
    for node in ast.walk(tree):
        values: list[ast.AST] = []
        if any(IDENTITY_BINDING_PATTERN.search(name) for name in _binding_names(node)):
            # A bare annotation (``NAME: str``) binds no value.
            if getattr(node, "value", None) is not None:
                values.append(node.value)
        elif isinstance(node, ast.Dict):
            for key, dict_value in zip(node.keys, node.values):
                key_text = _constant_value(key) if key is not None else None
                if isinstance(key_text, str) and IDENTITY_BINDING_PATTERN.search(key_text):
                    values.append(dict_value)
        for value in values:
            declarations.extend(
                constant
                for constant in (_constant_string(child) for child in ast.walk(value))
                if constant is not None
            )
    return tuple(dict.fromkeys(declarations))


def _active_declaration_context(path: Path, text: str) -> tuple[str, ...]:
    """Fragments of a file that assert an active normative identity."""
    if path.suffix.lower() == ".py":
        return _identity_declaration_strings(text)
    return tuple(line for line in text.splitlines() if any(marker in line for marker in ACTIVE_DECLARATION_MARKERS))


def _historical_roots(root: Path) -> tuple[Path, ...]:
    """Directories holding archived ruleset versions, which are exempt from identity checks.

    A superseded identity is *expected* inside its own archive: that is the record of what it
    was. The scan refuses it everywhere else.
    """
    history = root / "docs" / "history"
    return tuple(
        fixture.parent.relative_to(root)
        for fixture in sorted(history.glob("*/superseded-identities.json"))
    )


def _is_historical_path(relative: Path, history_roots: tuple[Path, ...]) -> bool:
    """Whether a path sits inside an archived ruleset version."""
    return any(relative == historical or historical in relative.parents for historical in history_roots)


def validate_superseded_identity_locations(root: Path, errors: list[str]) -> None:
    """Reject superseded normative identities outside declared history.

    Two rules, deliberately separate, so that historical evidence is never confused
    with an active normative source:

    * Strong tokens (canonical filename, manifest filename, rule-id prefix, raw
      SHA-256) uniquely name an obsolete ruleset. They are rejected anywhere outside
      declared history — nothing can activate v3.3 without one of them.
    * Weak tokens (a bare version or date) are rejected only where they actually
      declare an identity: an identity-bound Python constant, or a line carrying an
      active-declaration marker. Prose, dated filenames, unrelated timestamps and
      negative regression fixtures are therefore allowed to mention them.

    Declared active surfaces are validated separately and remain strict about both.
    """
    history_roots = _historical_roots(root)
    for path in root.rglob("*"):
        if not path.is_file() or any(part in SKIP_PARTS for part in path.parts):
            continue
        relative = path.relative_to(root)
        if _is_historical_path(relative, history_roots):
            continue

        relative_text = relative.as_posix()
        if relative_text in SUPERSEDED_IDENTITY_TEST_FIXTURES:
            continue
        errors.extend(
            f"superseded identity path outside explicit history: {relative}: {token}"
            for token in SUPERSEDED_STRONG_TOKENS
            if token in relative_text
        )

        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(
                f"unreadable identity surface cannot be scanned: {relative}: {type(exc).__name__}: {exc}"
            )
            continue
        except UnicodeDecodeError:
            continue
        # Strong tokens are scanned in the raw text and, for Python, in every folded
        # constant as well, so a split or interpolated literal cannot hide one. This scan
        # runs on every readable file: a checksum manifest, a lock or a data file carries a
        # superseded identity just as effectively as a .py or .md, so the suffix guard
        # below applies only to the weak, declaration-scoped tokens.
        strong_surfaces = [text]
        python_declarations: tuple[str, ...] | None = None
        if path.suffix.lower() == ".py":
            try:
                strong_surfaces.extend(_python_constant_strings(text))
                python_declarations = _identity_declaration_strings(text)
            except SyntaxError as exc:
                errors.append(
                    f"unparseable Python surface cannot be identity-scanned: {relative}: "
                    f"{exc.msg} (line {exc.lineno})"
                )
                continue
        errors.extend(
            f"superseded identity outside explicit history: {relative}: {token}"
            for token in SUPERSEDED_STRONG_TOKENS
            if any(token in surface for surface in strong_surfaces)
        )

        if path.suffix.lower() not in TEXT_IDENTITY_SUFFIXES and path.name not in {"Dockerfile", "AGENTS.md"}:
            continue
        declarations = python_declarations if python_declarations is not None else _active_declaration_context(path, text)
        if not declarations:
            continue
        errors.extend(
            f"superseded identity declared as active: {relative}: {token}"
            for token in SUPERSEDED_WEAK_TOKENS
            if any(token in declaration for declaration in declarations)
        )


def _missing_path_error(relative: str) -> str:
    """The error for a required path that is absent, with the hint for fixing it if there is one."""
    hint = MISSING_PATH_HINTS.get(relative)
    if hint:
        return f"missing required path: {relative} — {hint}"
    return f"missing required path: {relative}"


#: The surfaces the "core has no external runtime dependency" claim is about. `reporting` and
#: `scripts` deliberately carry pinned renderer dependencies (python-docx, reportlab, pypdf,
#: pypdfium2/PDFium); the scientific core does not, and that is the property checked below.
CORE_PACKAGES = ("array_pipeline", "normative")

#: Packages that live in this repository but are optional by contract — `.coderabbit.yaml`
#: declares `evidence_adapters/**` an optional Evidence Plane component. The core must import
#: without them, so they are excluded from the "local, therefore fine" allowance below.
OPTIONAL_LOCAL_PACKAGES = frozenset({"evidence_adapters", "adapters", "mcp"})


#: The only errors an optional-adapter guard may catch. `ModuleNotFoundError` is a subclass of
#: `ImportError`, so naming either is enough; naming anything else is not a guard.
_IMPORT_ERRORS = frozenset({"ImportError", "ModuleNotFoundError"})


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    """Does this `except` clause catch import errors and *nothing else*?

    A bare `except:` and `except Exception:` do catch it, and are deliberately not accepted:
    the contract is that an absent adapter degrades to a *stated* refusal, and a handler that
    also swallows a corrupt install or a failing module-level side effect cannot tell the
    caller which of those happened. Naming the error is what makes the guard a declaration
    that the dependency is optional rather than a blanket suppression.

    The same argument applies to a tuple, which an earlier version of this check missed by
    accepting any handler *containing* ImportError: under
    ``except (ImportError, AttributeError)`` an `AttributeError` raised while the dependency
    runs its own import-time code binds the fallback and reports a missing optional adapter
    where there is a broken installed one. Every name in the clause has to be an import error.
    """
    if handler.type is None:
        return False
    candidates = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    named = set()
    for candidate in candidates:
        if isinstance(candidate, ast.Name):
            named.add(candidate.id)
        elif isinstance(candidate, ast.Attribute):
            named.add(candidate.attr)
        else:
            # A computed exception class — a name this walk cannot resolve is not a
            # declaration it can act on, so it does not qualify as a guard.
            return False
    return bool(named) and named <= _IMPORT_ERRORS


def _import_time_statements(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Yield the statements that run when the module is imported, minus the guarded ones.

    The first version of the caller walked `tree.body` alone, reasoning that "an import
    nested in a `try` is guarded by construction". `tree.body` holds the `ast.Try` node and
    not the `ast.Import` inside it, so *every* `try` hid its imports from the walk —
    ``try: import numpy`` / ``except ValueError:`` reported clean and still raised
    `ModuleNotFoundError` at import time. What makes an import optional is the handler, so
    the handler is read: the body of a `try` that catches ImportError is skipped, and
    everything else that executes at import time is yielded, however deeply nested.

    Function and class bodies are not import-time and are not descended into: a lazy import
    inside a function is the pattern this contract exists to permit — `array_pipeline/
    annotation.py` loads `evidence_adapters` that way so the core imports without it.

    `ast.TryStar` (`try`/`except*`, Python 3.11) is a distinct node from `ast.Try`, and
    `ast.Match` is another, so imports inside either were invisible to this walk in exactly
    the way every import was before the handler started being read. Both are traversed, and
    `except*` follows the same guard rule: an `except* ImportError` group does catch the
    plain `ModuleNotFoundError` the import raises.
    """
    #: The `try` forms this walk understands. Both carry body/handlers/orelse/finalbody, so
    #: the same reasoning applies; only the node class differs.
    try_nodes: tuple[type[ast.stmt], ...] = (ast.Try,)
    if hasattr(ast, "TryStar"):  # pragma: no branch - present from Python 3.11
        try_nodes += (ast.TryStar,)

    for node in body:
        yield node
        if isinstance(node, try_nodes):
            if not any(_catches_import_error(handler) for handler in node.handlers):
                yield from _import_time_statements(node.body)
            for handler in node.handlers:
                yield from _import_time_statements(handler.body)
            # `else` runs only when the body did not raise and `finally` runs regardless;
            # neither is covered by the handler that protects the body.
            yield from _import_time_statements(node.orelse)
            yield from _import_time_statements(node.finalbody)
        elif isinstance(node, (ast.If, ast.For, ast.While)):
            yield from _import_time_statements(node.body)
            yield from _import_time_statements(node.orelse)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            yield from _import_time_statements(node.body)
        elif isinstance(node, ast.Match):
            for case in node.cases:
                yield from _import_time_statements(case.body)


def validate_core_runtime_dependencies(root: Path, errors: list[str]) -> None:
    """Check the claim `main()` used to simply print.

    `PASS optional_adapters core has no external runtime dependency` was a literal print with
    nothing behind it, and `array_pipeline/ancestry.py` imported NumPy at module scope —
    unpinned, absent from environment.yml, reporting/requirements.txt and the runtime lock —
    for as long as that line claimed otherwise. A validator that asserts a property it never
    evaluates is worse than one that stays quiet: every run of it published the assurance.

    An import inside `try`/`except ImportError` is an optional adapter and passes; one at
    module scope makes the package unimportable without the library and does not.
    """
    local_modules = {p.stem for p in root.glob("*.py")}
    local_modules |= {p.name for p in root.iterdir() if p.is_dir() and (p / "__init__.py").is_file()}
    local_modules |= {"array_pipeline", "reporting", "scripts", "normative", "tests"}
    allowed = set(sys.stdlib_module_names) | local_modules
    # Present in this repository, yet declared optional by contract, so "is it local?" is
    # the wrong question for these. `array_pipeline/annotation.py` imported
    # `evidence_adapters` at module scope and `completeness.py` imported *annotation* for a
    # single constant, which made an optional Evidence Plane adapter a hard requirement for
    # importing the core — the same defect as the NumPy one, hidden because the package sits
    # inside the repository and so counted as local.
    allowed -= OPTIONAL_LOCAL_PACKAGES

    def module_paths(module: str) -> list[Path]:
        """Files Python executes while importing one absolute local module."""
        parts = module.split(".")
        found: list[Path] = []
        for index in range(1, len(parts) + 1):
            package_init = root.joinpath(*parts[:index], "__init__.py")
            if package_init.is_file():
                found.append(package_init)
        module_file = root.joinpath(*parts).with_suffix(".py")
        if module_file.is_file():
            found.append(module_file)
        return found

    pending = [
        path
        for package in CORE_PACKAGES
        for path in sorted((root / package).rglob("*.py"))
    ]
    visited: set[Path] = set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            errors.append(f"core module could not be parsed: {path.relative_to(root)}: {exc}")
            continue
        for node in _import_time_statements(tree.body):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    modules = [node.module]
                    modules.extend(f"{node.module}.{alias.name}" for alias in node.names)
                elif node.level > 0:
                    relative = path.relative_to(root).with_suffix("").parts[:-1]
                    keep = max(len(relative) - node.level + 1, 0)
                    base_parts = (*relative[:keep], *((node.module,) if node.module else ()))
                    base = ".".join(base_parts)
                    modules = [base] if base else []
                    modules.extend(f"{base}.{alias.name}" for alias in node.names)
            for module in modules:
                name = module.split(".")[0]
                if name in OPTIONAL_LOCAL_PACKAGES:
                    errors.append(
                        f"core module {path.relative_to(root)} imports {name!r} at module "
                        "scope; optional local packages must be lazy and fail closed"
                    )
                elif name in local_modules:
                    pending.extend(module_paths(module))
                elif name not in allowed:
                    errors.append(
                        f"core module {path.relative_to(root)} imports {name!r} at module "
                        "scope; the scientific core must have no external runtime "
                        "dependency. Guard it with try/except ImportError and refuse "
                        "NÃO DISPONÍVEL, or declare and pin it and change this contract."
                    )


STAGE2_PDFIUM_VERSION = "5.13.0"
STAGE2_PDFIUM_COMPILER_ID = "pypdfium2-5.13.0-pdfium-genoma-v3"
STAGE2_PDFIUM_WHEEL = "pypdfium2-5.13.0-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
STAGE2_PDFIUM_WHEEL_SHA256 = "81df25c1ab4c13ff773102d3cbea1967511d079123b067fc077bd0c4d57d91d8"
STAGE2_PIXEL_QA_PATH = "docs/evidence/PDFIUM_STATIC_PIXEL_QA_200DPI_2026-09-17.json"
STAGE2_MIGRATION_PATH = "docs/evidence/PDFIUM_COORDINATE_MIGRATION_2026-09-17.json"
STAGE2_ALIAS_PATH = "reporting/legacy_field_aliases.json"
STAGE2_PRODUCER_PATH = "scripts/run_pdfium_static_pixel_qa.py"
STAGE2_ALIAS_CURRENT_SHA256 = "7d315ae6393bc07659a6831e86624534dcb5c1c0c8accebd5e8da9c0e09aac58"
STAGE2_ALIAS_LEGACY_SHA256 = "2ac96d9bdfa4080c2828ae8a4e0335a627c668c72de54504fb71f554ab534925"


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 digest for one repository artifact."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_sha256(value: object) -> str:
    """Hash a JSON-compatible value using the Stage 2 canonical encoding."""
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_json_object(path: Path, label: str, errors: list[str]) -> dict[str, object] | None:
    """Load a required Stage 2 JSON object and report parse/type failures."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"{label} is unavailable or invalid: {type(exc).__name__}: {exc}")
        return None
    if not isinstance(payload, dict):
        errors.append(f"{label} must be a JSON object")
        return None
    return payload


def _stage2_pdfium_lock_hashes(text: str) -> list[str]:
    """Return hashes attached to the single pypdfium2 5.13.0 lock entry."""
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if line.startswith("pypdfium2==")]
    if len(starts) != 1:
        return []
    header = lines[starts[0]].rstrip()
    expected_header = "pypdfium2==5.13.0 " + chr(92)
    if header != expected_header:
        return []
    hashes: list[str] = []
    for line in lines[starts[0] + 1 :]:
        stripped = line.strip()
        if not stripped.startswith("--hash=sha256:"):
            break
        value = stripped.removeprefix("--hash=sha256:").rstrip()
        if value.endswith(chr(92)):
            value = value[:-1].rstrip()
        hashes.append(value)
    return hashes


def validate_stage2_pdf_contract(root: Path, errors: list[str]) -> None:
    """Enforce the Stage 2 PDFium runtime, evidence, and alias contracts fail-closed."""
    migration_path = root / STAGE2_MIGRATION_PATH
    qa_path = root / STAGE2_PIXEL_QA_PATH
    alias_path = root / STAGE2_ALIAS_PATH
    reference_path = root / "reporting/reference_v3_manifest.json"
    requirements_path = root / "reporting/requirements.txt"
    producer_path = root / STAGE2_PRODUCER_PATH
    required = (migration_path, qa_path, alias_path, reference_path, requirements_path, producer_path)
    if any(not path.is_file() for path in required):
        return

    migration = _load_json_object(migration_path, "Stage 2 migration evidence", errors)
    qa = _load_json_object(qa_path, "Stage 2 pixel-QA evidence", errors)
    aliases = _load_json_object(alias_path, "Stage 2 legacy alias registry", errors)
    reference = _load_json_object(reference_path, "Stage 2 reference manifest", errors)
    if migration is None or qa is None or aliases is None or reference is None:
        return

    lock_hashes = _stage2_pdfium_lock_hashes(requirements_path.read_text(encoding="utf-8"))
    if lock_hashes != [STAGE2_PDFIUM_WHEEL_SHA256]:
        errors.append("Stage 2 pypdfium2 lock must contain only the audited Linux x86_64 wheel hash")

    candidate_backend = migration.get("candidate_backend")
    if not isinstance(candidate_backend, dict) or any(
        candidate_backend.get(key) != value
        for key, value in (
            ("backend", "PDFium"),
            ("compiler_id", STAGE2_PDFIUM_COMPILER_ID),
            ("package", "pypdfium2"),
            ("version", STAGE2_PDFIUM_VERSION),
            ("linux_x86_64_wheel", STAGE2_PDFIUM_WHEEL),
            ("linux_x86_64_wheel_sha256", STAGE2_PDFIUM_WHEEL_SHA256),
        )
    ):
        errors.append("Stage 2 migration candidate backend identity mismatch")
    if migration.get("schema") != "omnigenis-pdf-backend-migration-evidence-v1" or migration.get("status") != "VERIFIED":
        errors.append("Stage 2 migration evidence schema/status mismatch")

    reports = reference.get("reports")
    generated_manifest = reference.get("generated_coordinate_manifest")
    generated_detail = reference.get("generated_coordinate_detail")
    if not isinstance(reports, dict) or set(reports) != {f"{index:02d}" for index in range(1, 12)}:
        errors.append("Stage 2 reference manifest must contain report IDs 01..11")
        reports = {}
    candidate_artifacts = migration.get("candidate_coordinate_artifacts")
    if (
        not isinstance(candidate_artifacts, dict)
        or not isinstance(generated_manifest, dict)
        or not isinstance(generated_detail, dict)
        or candidate_artifacts.get("manifest_sha256") != generated_manifest.get("sha256")
        or candidate_artifacts.get("compressed_detail_sha256") != generated_detail.get("sha256")
    ):
        errors.append("Stage 2 coordinate artifact links do not match the reference manifest")

    producer = qa.get("producer")
    aggregate = qa.get("aggregate")
    qa_reports = qa.get("reports")
    if qa.get("schema") != "omnigenis-pdfium-static-pixel-qa-v2" or qa.get("status") != "VERIFICADO" or qa.get("dpi") != 200:
        errors.append("Stage 2 pixel-QA schema/status/DPI mismatch")
    if not isinstance(producer, dict):
        errors.append("Stage 2 pixel-QA producer metadata missing")
        producer = {}
    if not isinstance(aggregate, dict) or any(
        aggregate.get(key) != value
        for key, value in (("reports", 11), ("reference_pages", 100), ("outside_changed_pixels", 0), ("result", "PASS"))
    ):
        errors.append("Stage 2 pixel-QA aggregate contract mismatch")
        aggregate = {}
    if not isinstance(qa_reports, dict) or set(qa_reports) != {f"{index:02d}" for index in range(1, 12)}:
        errors.append("Stage 2 pixel-QA must contain report IDs 01..11")
        qa_reports = {}

    required_producer_keys = {"path", "producer_sha256", "command", "log_sha256", "mask_manifest_sha256", "candidate_set_sha256"}
    if producer.get("path") != STAGE2_PRODUCER_PATH or not required_producer_keys <= producer.keys():
        errors.append("Stage 2 pixel-QA producer provenance is incomplete")
    elif producer.get("producer_sha256") != _sha256_file(producer_path):
        errors.append("Stage 2 pixel-QA producer hash mismatch")
    for key in ("producer_sha256", "log_sha256", "mask_manifest_sha256", "candidate_set_sha256"):
        value = producer.get(key)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            errors.append(f"Stage 2 pixel-QA producer digest invalid: {key}")

    bundle = {"provenance": producer, "aggregate": aggregate, "reports": qa_reports}
    bundle_sha = _canonical_json_sha256(bundle)
    if qa.get("evidence_sha256") != bundle_sha:
        errors.append("Stage 2 pixel-QA provenance bundle hash mismatch")

    pixel_link = migration.get("pixel_qa")
    if (
        not isinstance(pixel_link, dict)
        or pixel_link.get("evidence_path") != STAGE2_PIXEL_QA_PATH
        or pixel_link.get("evidence_sha256") != _sha256_file(qa_path)
        or pixel_link.get("producer_path") != STAGE2_PRODUCER_PATH
        or pixel_link.get("producer_sha256") != producer.get("producer_sha256")
        or pixel_link.get("provenance_bundle_sha256") != bundle_sha
        or pixel_link.get("pdfium_candidate_status") != "PASS"
    ):
        errors.append("Stage 2 migration pixel-QA cross-artifact link mismatch")

    for report_id, meta in reports.items():
        qa_report = qa_reports.get(report_id) if isinstance(qa_reports, dict) else None
        if not isinstance(meta, dict) or not isinstance(qa_report, dict):
            errors.append(f"Stage 2 pixel-QA report record invalid: {report_id}")
            continue
        source_sha = meta.get("sha256")
        page_count = meta.get("page_count")
        candidate_sha = qa_report.get("candidate_sha256")
        if (
            not isinstance(source_sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", source_sha) is None
            or not isinstance(page_count, int)
            or isinstance(page_count, bool)
            or page_count <= 0
            or qa_report.get("sha256") != source_sha
            or qa_report.get("pages") != page_count
            or qa_report.get("outside_changed_pixels") != 0
            or not isinstance(candidate_sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", candidate_sha) is None
        ):
            errors.append(f"Stage 2 pixel-QA report contract mismatch: {report_id}")

    alias_records = aliases.get("aliases")
    if aliases.get("schema") != "omnigenis-legacy-template-field-aliases-v1" or not isinstance(alias_records, list) or len(alias_records) != 1:
        errors.append("Stage 2 legacy alias registry schema/count mismatch")
    else:
        record = alias_records[0]
        if not isinstance(record, dict):
            errors.append("Stage 2 legacy alias registry entry must be an object")
        else:
            current = record.get("current_field_id_sha256")
            legacy = record.get("legacy_field_id_sha256")
            encoded = record.get("legacy_field_id_utf8_b64")
            if current != STAGE2_ALIAS_CURRENT_SHA256 or legacy != STAGE2_ALIAS_LEGACY_SHA256 or not isinstance(encoded, str):
                errors.append("Stage 2 mandatory legacy alias identity mismatch")
            else:
                try:
                    decoded = base64.b64decode(encoded, validate=True)
                except ValueError:
                    decoded = b""
                if hashlib.sha256(decoded).hexdigest() != STAGE2_ALIAS_LEGACY_SHA256:
                    errors.append("Stage 2 legacy alias payload hash mismatch")

    alias_link = migration.get("legacy_field_alias_registry")
    if (
        not isinstance(alias_link, dict)
        or alias_link.get("path") != STAGE2_ALIAS_PATH
        or alias_link.get("aliases") != 1
        or alias_link.get("sha256") != _sha256_file(alias_path)
    ):
        errors.append("Stage 2 legacy alias registry cross-artifact link mismatch")

    runtime_lock = migration.get("runtime_lock_verification")
    if (
        not isinstance(runtime_lock, dict)
        or runtime_lock.get("status") != "EXECUTED_PASS"
        or runtime_lock.get("platform") != "linux"
        or runtime_lock.get("machine") != "x86_64"
        or runtime_lock.get("pypdfium2_version") != STAGE2_PDFIUM_VERSION
        or runtime_lock.get("audited_pypdfium2_wheel_sha256") != STAGE2_PDFIUM_WHEEL_SHA256
        or runtime_lock.get("allowed_pypdfium2_archive_hashes") != 1
        or runtime_lock.get("requirements_file") != "reporting/requirements.txt"
        or runtime_lock.get("pymupdf_distribution") != "ABSENT"
        or runtime_lock.get("fitz_module") != "ABSENT"
    ):
        errors.append("Stage 2 runtime lock evidence mismatch")




STAGE3_COPYLEFT_ACTIVE_SURFACES = (
    "environment.yml",
    "locks/runtime-lock.json",
    "scripts/runtime_stack.py",
    "scripts/check_versions.sh",
    "scripts/run_canary.sh",
    "reporting/template_v3.py",
)
STAGE3_PROHIBITED_IDENTIFIERS = ("poppler", "pdftoppm", "pdftocairo")
STAGE3_GATE_NAMES = (
    "directed_tests",
    "repository_validator",
    "supply_chain_gate",
    "residual_language_gate",
    "full_test_suite",
)

def _stage3_prohibited(value: str) -> tuple[str, ...]:
    lowered = value.lower()
    compact = re.sub(r"[^a-z0-9]+", "", lowered)
    return tuple(
        token for token in STAGE3_PROHIBITED_IDENTIFIERS
        if token in lowered or token in compact
    )

def _stage3_py_string(node: ast.AST, env: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return env.get(node.id)

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _stage3_py_string(node.left, env)
        right = _stage3_py_string(node.right, env)
        return left + right if left is not None and right is not None else None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                parts.append(part.value)
            elif isinstance(part, ast.FormattedValue):
                rendered = _stage3_py_string(part.value, env)
                if rendered is None:
                    literal = _constant_value(part.value)
                    if literal is None:
                        return None
                    rendered = str(literal)
                parts.append(rendered)
            else:
                return None
        return "".join(parts)
    return None

def _stage3_python_violations(text: str, relative: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [f"Stage 3 active Python surface is not parseable: {relative}: {exc}"]
    env: dict[str, str] = {}
    for statement in tree.body:
        target: str | None = None
        value: ast.expr | None = None

        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
        ):
            target = statement.targets[0].id
            value = statement.value
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            target = statement.target.id
            value = statement.value
        if target is not None and value is not None:
            folded = _stage3_py_string(value, env)
            if folded is not None:
                env[target] = folded
            else:
                env.pop(target, None)
    errors: list[str] = []
    for name, folded_value in env.items():
        for token in _stage3_prohibited(folded_value):
            errors.append(
                f"Stage 3 retired PDF identifier constructed in Python: {relative}:{name}:{token}"
            )
    command_calls = {
        "subprocess.run": 0,
        "subprocess.Popen": 0,
        "subprocess.call": 0,
        "subprocess.check_call": 0,
        "subprocess.check_output": 0,
        "subprocess.getoutput": 0,
        "subprocess.getstatusoutput": 0,
        "os.system": 0,
        "os.popen": 0,
        "os.execl": 0,
        "os.execle": 0,
        "os.execlp": 0,
        "os.execlpe": 0,
        "os.execv": 0,
        "os.execve": 0,
        "os.execvp": 0,
        "os.execvpe": 0,
        "os.posix_spawn": 0,
        "os.posix_spawnp": 0,
        "os.spawnl": 1,
        "os.spawnle": 1,
        "os.spawnlp": 1,
        "os.spawnlpe": 1,
        "os.spawnv": 1,
        "os.spawnve": 1,
        "os.spawnvp": 1,
        "os.spawnvpe": 1,
        "asyncio.create_subprocess_exec": 0,
        "asyncio.create_subprocess_shell": 0,
        "pty.spawn": 0,
        "importlib.import_module": 0,
        "__import__": 0,
    }
    executable_keyword_calls = {
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "asyncio.create_subprocess_exec",
        "asyncio.create_subprocess_shell",
    }
    import_aliases: dict[str, str] = {}
    for imported in ast.walk(tree):
        if isinstance(imported, ast.Import):
            for alias in imported.names:
                local_name = alias.asname or alias.name.split(".")[0]
                import_aliases[local_name] = alias.name
        elif isinstance(imported, ast.ImportFrom) and imported.module:
            for alias in imported.names:
                local_name = alias.asname or alias.name
                import_aliases[local_name] = f"{imported.module}.{alias.name}"

    def dotted(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return import_aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = dotted(node.value)
            return f"{base}.{node.attr}" if base else None
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = dotted(node.func)
        if call_name not in command_calls:
            continue
        argument_index = command_calls[call_name]
        expression = node.args[argument_index] if len(node.args) > argument_index else None
        if expression is None:
            errors.append(
                f"Stage 3 unresolved executable/module on active Python surface: {relative}:{call_name}"
            )
            continue
        if isinstance(expression, (ast.List, ast.Tuple)):
            expression = expression.elts[0] if expression.elts else None
        resolved = _stage3_py_string(expression, env) if expression is not None else None
        if resolved is None:
            errors.append(
                f"Stage 3 unresolved executable/module on active Python surface: {relative}:{call_name}"
            )
            continue
        for token in _stage3_prohibited(resolved):
            errors.append(
                f"Stage 3 retired PDF identifier constructed in Python command: "
                f"{relative}:{call_name}:{token}"
            )
        if call_name not in executable_keyword_calls:
            continue
        if any(keyword.arg is None for keyword in node.keywords):
            errors.append(
                f"Stage 3 unresolved executable keyword arguments on active Python surface: "
                f"{relative}:{call_name}"
            )
        executable_keywords = [
            keyword for keyword in node.keywords if keyword.arg == "executable"
        ]
        for keyword in executable_keywords:
            if isinstance(keyword.value, ast.Constant) and keyword.value.value is None:
                continue
            executable = _stage3_py_string(keyword.value, env)
            if executable is None:
                errors.append(
                    f"Stage 3 unresolved executable override on active Python surface: "
                    f"{relative}:{call_name}"
                )
                continue
            for token in _stage3_prohibited(executable):
                errors.append(
                    f"Stage 3 retired PDF identifier constructed in Python executable override: "
                    f"{relative}:{call_name}:{token}"
                )
    return errors

_STAGE3_SHELL_ASSIGNMENT = re.compile(
    r"^\s*(?:(?:readonly|export|local)\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$"
)
_STAGE3_SHELL_VARIABLE = re.compile(
    r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))"
)

def _stage3_shell_violations(text: str, relative: str) -> list[str]:
    env: dict[str, str | None] = {}
    errors: list[str] = []
    pending_command = ""
    pending_command_line = 0

    def expand(value: str) -> tuple[str, bool]:
        unresolved = False
        def replace(match: re.Match[str]) -> str:
            nonlocal unresolved
            name = match.group(1) or match.group(2)
            resolved = env.get(name)
            if resolved is None:
                unresolved = True
                return match.group(0)
            return resolved
        return _STAGE3_SHELL_VARIABLE.sub(replace, value), unresolved

    def unresolved_env_executable(raw_line: str) -> str | None:
        try:
            parts = shlex.split(raw_line, comments=True, posix=True)
        except ValueError:
            return "<env-parse>" if raw_line.lstrip().startswith("env ") else None
        if not parts or parts[0] != "env":
            return None
        index = 1
        no_value_options = {
            "-i", "--ignore-environment", "-0", "--null", "-v", "--debug",
        }
        value_options = {"-u", "--unset", "-C", "--chdir", "--argv0"}
        assignment_pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
        while index < len(parts):
            token = parts[index]
            if token == "--":
                index += 1
                break
            if token in no_value_options:
                index += 1
                continue
            if token in value_options:
                if index + 1 >= len(parts):
                    return "<env-option>"
                index += 2
                continue
            if token.startswith(("--unset=", "--chdir=", "--argv0=")):
                index += 1
                continue
            if token in {"-S", "--split-string"}:
                if index + 1 >= len(parts):
                    return "<env-split-string>"
                split_command = parts[index + 1]
                for match in _STAGE3_SHELL_VARIABLE.finditer(split_command):
                    name = match.group(1) or match.group(2)
                    if env.get(name) is None:
                        return name
                return None
            if token.startswith("--split-string="):
                split_command = token.split("=", 1)[1]
                for match in _STAGE3_SHELL_VARIABLE.finditer(split_command):
                    name = match.group(1) or match.group(2)
                    if env.get(name) is None:
                        return name
                return None
            if assignment_pattern.match(token):
                index += 1
                continue
            if token.startswith("-"):
                return "<env-option>"
            break
        if index >= len(parts):
            return None
        command = parts[index]
        for match in _STAGE3_SHELL_VARIABLE.finditer(command):
            name = match.group(1) or match.group(2)
            if env.get(name) is None:
                return name
        return None

    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line_continues = raw_line.rstrip().endswith("\\")
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assignment = _STAGE3_SHELL_ASSIGNMENT.match(raw_line)
        if assignment:
            name, expression = assignment.groups()
            if "$(" in expression or "`" in expression or "${!" in expression:
                env[name] = None
            else:
                expanded, unresolved = expand(expression)
                try:
                    parts = shlex.split(expanded, comments=True, posix=True)
                except ValueError:
                    parts = []
                env[name] = parts[0] if not unresolved and len(parts) == 1 else None
                if env[name] is not None:
                    for token in _stage3_prohibited(env[name] or ""):
                        errors.append(
                            f"Stage 3 retired PDF identifier constructed in shell: "
                            f"{relative}:{line_number}:{name}:{token}"
                        )

        if not pending_command:
            pending_command_line = line_number
        command_piece = raw_line.rstrip()
        logical_command = (
            pending_command + command_piece.lstrip()
            if pending_command
            else raw_line
        )
        if line_continues:
            pending_command = logical_command.rstrip()[:-1] + " "
            continue
        pending_command = ""

        expanded, _ = expand(logical_command)
        for token in _stage3_prohibited(expanded):
            errors.append(
                f"Stage 3 retired PDF identifier constructed in shell command: "
                f"{relative}:{pending_command_line}:{token}"
            )
        command_var = re.match(
            r"^\s*[\"']?\$(?:\{)?([A-Za-z_][A-Za-z0-9_]*)",
            logical_command,
        )
        wrapper_var = re.match(
            r"^\s*(?:command|exec|env)\s+(?:--\s+)?[\"']?"
            r"\$(?:\{)?([A-Za-z_][A-Za-z0-9_]*)",
            logical_command,
        )
        shell_c_var = re.match(
            r"^\s*(?:bash|sh)\s+-c\s+[\"']?"
            r"\$(?:\{)?([A-Za-z_][A-Za-z0-9_]*)",
            logical_command,
        )
        unresolved_command_var = unresolved_env_executable(logical_command)
        if unresolved_command_var is None:
            for match in (command_var, wrapper_var, shell_c_var):
                if match and env.get(match.group(1)) is None:
                    unresolved_command_var = match.group(1)
                    break
        if unresolved_command_var is not None:
            errors.append(
                f"Stage 3 unresolved shell executable on active surface: "
                f"{relative}:{pending_command_line}:{unresolved_command_var}"
            )
    if pending_command:
        errors.append(
            f"Stage 3 unterminated shell continuation on active surface: "
            f"{relative}:{pending_command_line}"
        )
    return errors

def _stage3_structured_violations(text: str, relative: str) -> list[str]:
    """Inspect package identities structurally on the active YAML/JSON dependency surfaces."""
    values: list[str] = []
    if relative == "environment.yml":
        in_dependencies = False
        for line_number, raw_line in enumerate(text.splitlines(), 1):
            stripped = raw_line.strip()
            if stripped == "dependencies:":
                in_dependencies = True
                continue
            if not in_dependencies or not stripped or stripped.startswith("#"):
                continue
            if not raw_line.startswith((" ", "\t")):
                in_dependencies = False
                continue
            if not stripped.startswith("- "):
                return [
                    f"Stage 3 unresolved environment dependency syntax: "
                    f"{relative}:{line_number}"
                ]
            scalar = stripped[2:].strip()
            if scalar.startswith('"'):
                try:
                    decoded = json.loads(scalar)
                except json.JSONDecodeError:
                    return [
                        f"Stage 3 unresolved environment dependency syntax: "
                        f"{relative}:{line_number}"
                    ]
                if not isinstance(decoded, str):
                    return [
                        f"Stage 3 unresolved environment dependency syntax: "
                        f"{relative}:{line_number}"
                    ]
                values.append(decoded)
            elif scalar.startswith("'"):
                if len(scalar) < 2 or not scalar.endswith("'"):
                    return [
                        f"Stage 3 unresolved environment dependency syntax: "
                        f"{relative}:{line_number}"
                    ]
                values.append(scalar[1:-1].replace("''", "'"))
            else:
                comment = re.search(r"\s+#", scalar)
                if comment:
                    scalar = scalar[:comment.start()].rstrip()
                if not scalar or any(marker in scalar for marker in ("[", "]", "{", "}")):
                    return [
                        f"Stage 3 unresolved environment dependency syntax: "
                        f"{relative}:{line_number}"
                    ]
                values.append(scalar)
    elif relative.endswith(".json"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            return [f"Stage 3 active JSON surface invalid: {relative}: {exc}"]

        def walk(value: object) -> None:
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, dict):
                for key, item in value.items():
                    values.append(str(key))
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(payload)

    errors: list[str] = []
    for value in values:
        for token in _stage3_prohibited(value):
            errors.append(
                f"Stage 3 retired PDF dependency present in structured surface: "
                f"{relative}:{token}"
            )
    return errors


def _stage3_gate_provenance_errors(
    root: Path, verification: dict[str, object]
) -> list[str]:
    tree_sha = str(verification.get("pre_attestation_tested_tree_sha", ""))
    gates = verification.get("gates")
    if not re.fullmatch(r"[0-9a-f]{40}", tree_sha):
        return ["Stage 3 verification provenance has invalid pre-attestation tree SHA"]
    if not isinstance(gates, dict) or set(gates) != set(STAGE3_GATE_NAMES):
        return ["Stage 3 verification provenance gate set mismatch"]

    errors: list[str] = []
    for name in STAGE3_GATE_NAMES:
        record = gates.get(name)
        if not isinstance(record, dict):
            errors.append(f"Stage 3 verification provenance record invalid: {name}")
            continue
        command = record.get("command")
        output_sha = str(record.get("output_sha256", ""))
        output_path_value = record.get("output_path")
        if (
            record.get("status") != "PASS"
            or not isinstance(command, str)
            or not command.strip()
            or record.get("exit_code") != 0
            or not re.fullmatch(r"[0-9a-f]{64}", output_sha)
            or record.get("tested_tree_sha") != tree_sha
            or not isinstance(output_path_value, str)
            or not output_path_value
        ):
            errors.append(f"Stage 3 verification provenance incomplete: {name}")
            continue

        output_path = Path(output_path_value)
        if (
            output_path.is_absolute()
            or ".." in output_path.parts
            or not output_path.as_posix().startswith("docs/evidence/stage3/")
        ):
            errors.append(f"Stage 3 verification output artifact path invalid: {name}")
            continue

        artifact = root / output_path
        if not artifact.is_file():
            errors.append(f"Stage 3 verification output artifact missing: {name}")
            continue
        actual_sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
        if actual_sha != output_sha:
            errors.append(f"Stage 3 verification output hash mismatch: {name}")
    return errors


def validate_stage3_copyleft_contract(root: Path, errors: list[str]) -> None:
    """Keep the remediated application runtime free of retired PDF executables."""
    for relative in STAGE3_COPYLEFT_ACTIVE_SURFACES:
        path = root / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for token in STAGE3_PROHIBITED_IDENTIFIERS:
            if token in text.lower():
                errors.append(
                    f"Stage 3 retired PDF runtime dependency reintroduced: {relative}: {token}"
                )
        if path.suffix == ".py":
            errors.extend(_stage3_python_violations(text, relative))
        elif path.suffix == ".sh":
            errors.extend(_stage3_shell_violations(text, relative))
        elif relative in {"environment.yml", "locks/runtime-lock.json"}:
            errors.extend(_stage3_structured_violations(text, relative))

    evidence_path = root / "docs/evidence/STRONG_COPYLEFT_RUNTIME_CLEANUP_2026-09-17.json"
    if evidence_path.is_file():
        try:
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"Stage 3 cleanup evidence invalid: {type(exc).__name__}: {exc}")
        else:
            removed = evidence.get("removed_runtime_component")
            replacement = evidence.get("replacement")
            verification = evidence.get("verification")
            if (
                evidence.get("schema") != "omnigenis-stage3-strong-copyleft-runtime-cleanup-v2"
                or evidence.get("status") != "VERIFIED"
                or not isinstance(removed, dict)
                or removed.get("name") != "Poppler"
                or removed.get("version") != "26.07.0"
                or not isinstance(replacement, dict)
                or replacement.get("wrapper") != "pypdfium2"
                or replacement.get("version") != "5.13.0"
                or replacement.get("backend") != "PDFium"
                or replacement.get("docx_static_background_dpi") != 288
                or replacement.get("audited_linux_x86_64_wheel_sha256")
                != STAGE2_PDFIUM_WHEEL_SHA256
                or not isinstance(verification, dict)
            ):
                errors.append("Stage 3 cleanup evidence contract mismatch")
            elif isinstance(verification, dict):
                errors.extend(_stage3_gate_provenance_errors(root, verification))

    template = root / "reporting/template_v3.py"
    if template.is_file():
        text = template.read_text(encoding="utf-8")
        for token in (
            "DOCX_BACKGROUND_DPI = 288",
            "template-v3-pdfium-raster-docx",
            "_render_template_pages_pdfium",
        ):
            if token not in text:
                errors.append(f"Stage 3 PDFium DOCX contract missing: {token}")

    canary = root / "scripts/run_canary.sh"
    if canary.is_file():
        text = canary.read_text(encoding="utf-8")
        for token in ('import pypdfium2 as pdfium', '"renderer": "PDFium"'):
            if token not in text:
                errors.append(f"Stage 3 editorial canary contract missing: {token}")


def validate_language_policy(root: Path, errors: list[str]) -> None:
    try:
        validate_code_language(root, errors)
    except LanguagePolicyError as exc:
        errors.append(f"code language policy unavailable: {exc}")


def validate_residual_language(root: Path, errors: list[str]) -> None:
    """Enforce the reviewed Stage 8 residual-language classification."""
    try:
        report = audit_repository(root)
    except ResidualLanguageError as exc:
        errors.append(f"residual language audit unavailable: {exc}")
        return
    for path in report["unclassified"]:
        errors.append(f"unclassified residual Portuguese: {path}")
    for path in report["missing"]:
        errors.append(f"stale residual language classification: {path}")
    for item in report["drift"]:
        identity = item.get("path", item.get("root", "unknown"))
        errors.append(f"residual language classification drift: {identity}")


def validate_production_witness_contract(root: Path, errors: list[str]) -> None:
    witness = root / ".github/workflows/genoma-production-witness.yml"
    if not witness.is_file():
        return
    text = witness.read_text(encoding="utf-8")
    if "--output-dir evidence/live-section-260" in text:
        errors.append("Production Witness still uses obsolete live smoke --output-dir contract")
    for token in ("--deployment-id", "--output evidence/live-section-260/summary.json"):
        if token not in text:
            errors.append(f"Production Witness missing current live smoke contract: {token}")
    marker = "\n  witness:\n"
    publisher_marker = "\n  publish-witness:\n"
    if marker not in text or publisher_marker not in text:
        errors.append("Production Witness capability guard is not attached to the witness job boundary")
        return
    job = text.split(marker, 1)[1].split(publisher_marker, 1)[0]
    conditions = [line.removeprefix("    if: ").strip() for line in job.splitlines() if line.startswith("    if: ")]
    if conditions != [PRODUCTION_WITNESS_CAPABILITY_GUARD]:
        errors.append("Production Witness capability guard must use the exact job-level repository-variable predicate")


def _presentation_labels(locale_tree: ast.Module, errors: list[str]) -> dict[str, str] | None:
    """Read unique literal strings without importing the presentation module."""
    labels: dict[str, str] = {}
    for index, statement in enumerate(locale_tree.body):
        if (
            index == 0
            and isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        ):
            continue
        if (
            not isinstance(statement, ast.Assign)
            or len(statement.targets) != 1
            or not isinstance(statement.targets[0], ast.Name)
            or not isinstance(statement.value, ast.Constant)
            or not isinstance(statement.value.value, str)
            or statement.targets[0].id in labels
        ):
            errors.append("reporting presentation labels must be unique literal string assignments")
            return None
        labels[statement.targets[0].id] = statement.value.value
    return labels


def _presentation_reads(tree: ast.AST) -> set[str]:
    """Collect loaded names from the statically bound locale, including nested code."""
    return {
        item.attr
        for item in ast.walk(tree)
        if isinstance(item, ast.Attribute)
        and isinstance(item.value, ast.Name)
        and item.value.id == "pt_br"
        and isinstance(item.ctx, ast.Load)
    }


def _presentation_imports(tree: ast.AST) -> list[tuple[str | None, str, str | None, int]]:
    """Describe every import that claims the pt_br binding without executing it."""
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if (alias.asname or alias.name) == "pt_br":
                    imports.append(
                        (
                            getattr(node, "module", None),
                            alias.name,
                            alias.asname,
                            getattr(node, "level", 0),
                        )
                    )
    return imports


def _presentation_shadowed(node: ast.AST) -> bool:
    """Recognize the same assignment, deletion and argument shadows as the original gate."""
    return (
        isinstance(node, ast.Name)
        and node.id == "pt_br"
        and isinstance(node.ctx, (ast.Store, ast.Del))
    ) or (isinstance(node, ast.arg) and node.arg == "pt_br")


def _validate_presentation_binding(
    relative: str, tree: ast.Module, labels: dict[str, str], errors: list[str]
) -> None:
    """Check missing constants before the import binding, preserving diagnostic order."""
    for name in sorted(_presentation_reads(tree) - labels.keys()):
        errors.append(f"reporting presentation attribute missing: {relative}: {name}")
    if _presentation_imports(tree) != [("reporting", "locale_pt_br", "pt_br", 0)] or any(
        _presentation_shadowed(node) for node in ast.walk(tree)
    ):
        errors.append(
            f"reporting renderer {relative} must bind pt_br only to reporting.locale_pt_br"
        )


def _validate_result_caption(renderer_tree: ast.Module, errors: list[str]) -> None:
    """Keep the PDF and DOCX result-caption checks independent of literal ownership."""
    for name in ("_pdf", "_docx"):
        functions = [
            node
            for node in renderer_tree.body
            if isinstance(node, ast.FunctionDef) and node.name == name
        ]
        if len(functions) != 1 or "GENOMIC_RESULT" not in _presentation_reads(functions[0]):
            errors.append(f"editorial v3 renderer {name} must use pt_br.GENOMIC_RESULT")


def validate_report_presentation(root: Path, errors: list[str]) -> None:
    """Validate source, literal ownership, both bindings and both required result captions."""
    try:
        locale_tree = ast.parse((root / "reporting/locale_pt_br.py").read_text(encoding="utf-8"))
        engine_tree = ast.parse((root / "reporting/engine.py").read_text(encoding="utf-8"))
        renderer_tree = ast.parse(
            (root / "reporting/editorial_v3_hifi.py").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, SyntaxError) as exc:
        errors.append(f"reporting presentation source invalid: {type(exc).__name__}: {exc}")
        return
    labels = _presentation_labels(locale_tree, errors)
    if labels is None:
        return
    for name, expected in (("GENOMIC_RESULT", "RESULTADO GENÔMICO"), ("LANGUAGE_TAG", "pt-BR")):
        if labels.get(name) != expected:
            errors.append(f"reporting presentation contract mismatch: {name}")
    for relative, tree in (
        ("reporting/engine.py", engine_tree),
        ("reporting/editorial_v3_hifi.py", renderer_tree),
    ):
        _validate_presentation_binding(relative, tree, labels, errors)
    _validate_result_caption(renderer_tree, errors)


def validate(root: Path) -> list[str]:
    """Run every static repository check and return the accumulated errors.

    Errors accumulate rather than raising, so one run reports everything wrong instead of
    stopping at the first problem. `main` prints its PASS banner only when this returns
    empty — those printed lines are a summary of this function's verdict, not twelve
    independent checks, and should not be quoted as if they were.
    """
    errors: list[str] = []
    errors.extend(
        _missing_path_error(relative)
        for relative in REQUIRED_PATHS
        if not (root / relative).is_file()
    )
    errors.extend(validate_project_identity(root))
    try:
        errors.extend(validate_zero_identity(root))
    except (PolicyError, RepositoryScanError) as exc:
        errors.append(f"zero identity guard failed: {type(exc).__name__}: {exc}")
    errors.extend(
        f"superseded active ruleset path must be archived outside executable surfaces: {relative}"
        for relative in FORBIDDEN_ACTIVE_PATHS
        if (root / relative).exists()
    )
    validate_superseded_identity_locations(root, errors)
    validate_core_runtime_dependencies(root, errors)
    validate_stage2_pdf_contract(root, errors)
    validate_stage3_copyleft_contract(root, errors)
    errors.extend(validate_stage4_compliance(root))
    errors.extend(validate_stage5_license_gate(root))
    errors.extend(validate_stage6_data_sources(root))
    errors.extend(validate_stage7_purpose_use(root))
    errors.extend(validate_stage8_contribution_provenance(root))
    errors.extend(validate_stage9_genetic_privacy(root))

    active = []
    for candidate in root.rglob("REGRAS_PROJETO_GENOMA*.txt"):
        if any(part in SKIP_PARTS for part in candidate.parts):
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if re.search(r"^STATUS NORMATIVO:\s*VIGENTE\s*$", text, re.MULTILINE):
            active.append(candidate.relative_to(root))
    if active:
        errors.append(f"active normative ruleset must be materialized at runtime, not duplicated in repo; found {active}")

    validate_sealed_ruleset(root, errors)

    ruleset_manifest = root / "manifests/RULESET_V3.4.sha256"
    if ruleset_manifest.is_file():
        try:
            manifest_text = ruleset_manifest.read_text(encoding="ascii")
        except (OSError, UnicodeDecodeError):
            # A manifest that is not plain ASCII cannot pin the canonical artifact; report
            # it as a mismatch instead of aborting the whole validation run.
            manifest_text = ""
        if manifest_text.strip().split() != [CANONICAL_RULESET_SHA256, CANONICAL_RULESET]:
            errors.append("ruleset external manifest does not match the verified v3.4 artifact")

    for relative in ACTIVE_IDENTITY_SURFACES:
        path = root / relative
        if path.is_file():
            validate_active_identity_text(path.read_text(encoding="utf-8", errors="replace"), relative, errors)

    manifest = root / "manifests/GRCh38.sources.tsv"
    if manifest.is_file():
        with manifest.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        targets = {row.get("target", "") for row in rows}
        if len(rows) != 9 or targets != EXPECTED_ARTIFACTS:
            errors.append(f"GRCh38 manifest must contain exactly the required 9 artifacts; found {len(rows)}")
        if any(not row.get("url", "").startswith(("https://", "generated-from:")) for row in rows):
            errors.append("GRCh38 manifest contains a non-HTTPS/non-generated source")

    package = root / "mcp/package.json"
    if package.is_file() and json.loads(package.read_text()).get("devDependencies", {}).get("fallow") != "3.16.0":
        errors.append("mcp/package.json must pin fallow 3.16.0 exactly")

    fw = root / ".github/workflows/fallow.yml"
    if fw.is_file():
        text = fw.read_text(encoding="utf-8")
        if f"fallow-rs/fallow@{FALLOW_ACTION_SHA}" not in text or "version: 3.16.0" not in text:
            errors.append("Fallow workflow must pin wrapper SHA and CLI 3.16.0")

    runbook = root / "docs/MAGALU_PRIVATE_MCP_SETUP.md"
    if runbook.is_file() and "--entrypoint /bin/bash" in runbook.read_text(encoding="utf-8"):
        errors.append("runbook must not bypass the micromamba container entrypoint")

    main_nf = root / "main.nf"
    if main_nf.is_file():
        text = main_nf.read_text(encoding="utf-8")
        for token in ("params.mode", "WGS_PRODUCTION", "ARRAY_PRODUCTION", "CANARY", "array_input", "array_build_evidence", "array_strand_evidence"):
            if token not in text:
                errors.append(f"main.nf missing dispatcher contract token: {token}")

    wgs_nf = root / "workflows/wgs.nf"
    if wgs_nf.is_file():
        text = wgs_nf.read_text(encoding="utf-8")
        for token in ("VERIFY_RUNTIME_GATE", "REFRESH_FRESHNESS_GATE", "VERIFY_CONSENT_PROVENANCE", "ready_for_first_dna_read", "INGEST_AND_QC", "ALIGN_OR_STAGE", "RERUN_SAMPLE_RUNTIME_GATE", "CALL_SHORT_VARIANTS", "NORMALIZE_VARIANTS", "ANNOTATE_EVIDENCE", "BUILD_CURATED_MANIFEST", "POLICY_EVALUATE", "GENERATE_REPORTS", "unsupported_variant_classes", "NÃO DISPONÍVEL", "CYP2D6", "CNV", "SV"):
            if token not in text:
                errors.append(f"WGS workflow missing fail-closed contract token: {token}")

    array_nf = root / "workflows/array.nf"
    if array_nf.is_file():
        text = array_nf.read_text(encoding="utf-8")
        for token in ("ARRAY_QC", "ARRAY_ANNOTATE", "ARRAY_BUILD_MANIFEST", "ARRAY_POLICY_EVALUATE", "ARRAY_GENERATE_REPORTS", "LIMITED_INTERPRETATION_GATE", "plan-only", "live"):
            if token not in text:
                errors.append(f"SNP-array workflow missing fail-closed contract token: {token}")

    validate_production_witness_contract(root, errors)

    ngs_gate = root / ".github/workflows/genoma-ngs-runtime-gate.yml"
    if ngs_gate.is_file():
        text = ngs_gate.read_text(encoding="utf-8")
        if "bash -lc './scripts/run_canary.sh" in text:
            errors.append("NGS gate must not bypass micromamba environment with a login-shell canary")
        for token in (
            "freshness_gate.py",
            "GRCh38.lock.sha256.approved",
            "[self-hosted, linux, x64, genoma-production, highmem]",
            "nextflow run /opt/omnigenis/main.nf --mode canary",
            "validate_bwa_mem2_functional.sh",
            "verify_supply_chain_lock.py",
        ):
            if token not in text:
                errors.append(f"NGS gate missing current-session readiness contract: {token}")

    nextflow_cfg = root / "nextflow.config"
    if nextflow_cfg.is_file() and "nextflowVersion = '!>=26.04.6'" not in nextflow_cfg.read_text(encoding="utf-8"):
        errors.append("Nextflow manifest must permit tested forward versions while enforcing minimum 26.04.6")

    adapters = root / "evidence_adapters/__init__.py"
    if adapters.is_file():
        text = adapters.read_text(encoding="utf-8")
        for key in EXPECTED_EVIDENCE_ADAPTERS:
            if f'"{key}"' not in text:
                errors.append(f"missing evidence adapter: {key}")
        if "api.pharmgkb.org" in text:
            errors.append("retired PharmGKB API hostname must not be used; use ClinPGx")
        for token in ("result_digest", "checked_at", "locator", "NÃO DISPONÍVEL", "VERIFICADO"):
            if token not in text:
                errors.append(f"evidence adapter contract missing: {token}")

    renderer = root / "reporting/editorial_v3_hifi.py"
    if renderer.is_file():
        text = renderer.read_text(encoding="utf-8")
        for token in (
            "0B1F33", "0F766E", "A16207", "F2F4F7", "write_editorial_bundle", "DejaVu Sans"
        ):
            if token not in text:
                errors.append(f"editorial v3 high-fidelity renderer contract missing: {token}")

    validate_report_presentation(root, errors)

    catalog = root / "reporting/catalog.json"
    if catalog.is_file():
        models = json.loads(catalog.read_text(encoding="utf-8"))
        expected_accents = {"01": "0F766E", "02": "2563EB", "03": "7C3AED", "04": "166534", "05": "475467", "06": "B42318", "07": "A16207", "08": "0F766E", "09": "475467", "10": "0B1F33", "11": "0B1F33"}
        for report_id, accent in expected_accents.items():
            if models.get(report_id, {}).get("accent") != accent:
                errors.append(f"report {report_id} v3 accent mismatch")

    requirements = root / "reporting/requirements.txt"
    if requirements.is_file():
        req = requirements.read_text(encoding="utf-8")
        if "python-docx==" not in req or "reportlab==" not in req:
            errors.append("editorial renderer dependencies must be exact-pinned")

    for path in root.rglob("*"):
        if not path.is_file() or any(part in SKIP_PARTS for part in path.parts):
            continue
        relative_path = path.relative_to(root)
        name = path.name.lower()
        if name.endswith(FORBIDDEN_SUFFIXES) or name.endswith((".fastq.gz", ".fq.gz", ".vcf.gz")):
            errors.append(f"genomic/reference payload must not be committed: {relative_path}")
        if name == "grch38.lock.sha256.approved":
            errors.append("externally approved GRCh38 lock must not be committed")
        if path.suffix == ".json":
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                errors.append(f"invalid JSON: {relative_path}: {exc}")
    validate_language_policy(root, errors)
    validate_residual_language(root, errors)
    return errors


def main() -> None:
    """Run every repository check and print the verdict.

    The twelve `PASS` lines are a fixed banner printed once `validate()` returns no errors —
    not twelve independent verdicts. What exit 0 supports is "`validate()` found no errors";
    see `validate()` for what that does and does not cover.
    """
    errors = validate(ROOT)
    if errors:
        for error in errors:
            print(f"FAIL\t{error}")
        raise SystemExit(1)
    print("PASS\trepository_contract")
    print("PASS\truleset_manifest_contract\tv3.4 raw SHA-256 pinned")
    print("PASS\trepository_active_rulesets\t0")
    print("PASS\tsealed_normative_transport\tshared decoder")
    print("PASS\tgrch38_manifest\t9/9")
    print("PASS\tpre_dna_readiness_contract\tlatest-tested candidate + canaries + freshness + runtime/resource gate")
    print("PASS\twgs_scientific_data_plane_contract\treal SNV/indel path + explicit unsupported classes")
    print("PASS\tarray_scientific_data_plane_contract\tQC + target-first evidence + policy/report handoff")
    print("PASS\tevidence_adapter_contract\tClinVar/ClinGen/CPIC/ClinPGx/gnomAD/PGS Catalog")
    print("PASS\tsupply_chain_contract\tworkflow/action/container lock paths present")
    print("PASS\treporting_contract\t11-model deterministic renderer and reference identities")
    print("PASS\toptional_adapters\tcore has no external runtime dependency")


if __name__ == "__main__":
    main()
