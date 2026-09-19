#!/usr/bin/env python3
"""Validate Stage 6 scientific-data licensing/provenance registry."""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config/data_source_registry.yaml"

REQUIRED_FIELDS = (
    "id", "provider", "version", "retrieval_date", "source_url", "terms_url",
    "license", "commercial_use", "clinical_use", "research_use", "redistribution",
    "modification", "derived_data", "local_copy_allowed", "attribution_required",
    "required_citation", "access_method", "sha256", "status", "limitations", "evidence",
)
STATUS_VOCABULARY = {
    "DOCUMENTED_OPEN",
    "DOCUMENTED_WITH_OBLIGATIONS",
    "RECORD_LEVEL_TERMS_REQUIRED",
    "RESTRICTED",
    "REVIEW_REQUIRED",
}
EXPECTED_RESOURCE_IDS = {
    "ncbi-clinvar", "ncbi-dbsnp", "ebi-gwas-catalog", "ebi-pgs-catalog",
    "clingen-gene-disease-validity", "clingen-dosage-sensitivity", "gencc",
    "panelapp-genomics-england", "panelapp-australia", "gnomad", "cpic", "clinpgx",
    "gencode-human", "broad-gatk-grch38-resource-bundle", "encode-blacklist-boyle-lab",
    "1000-genomes-affy6", "aadr", "hpo", "mondo",
}
EXPECTED_ADAPTERS = {"clinvar", "clingen", "cpic", "clinpgx", "gnomad", "pgs_catalog"}
NO_LOCAL_HASH_SENTINELS = {
    "LIVE_API_NO_LOCAL_SNAPSHOT",
    "RUNTIME_RESOURCE_NOT_COMMITTED",
    "REFERENCE_ONLY_NO_LOCAL_COPY",
}
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PGS_WEIGHTS = re.compile(r"^PGS\d{6}.*\.(?:txt|tsv|csv)(?:\.gz)?$", re.IGNORECASE)
PANELAPP_ENGLAND_PROTECTED_FIELDS = {
    "status": "RESTRICTED",
    "license": "Genomics England PanelApp custom Terms of Use (December 2019)",
    "commercial_use": "PROHIBITED_WITHOUT_SEPARATE_AGREEMENT",
    "clinical_use": "PROHIBITED_WITHOUT_SEPARATE_AGREEMENT_FOR_DIAGNOSTIC_OR_MEDICAL_DECISION_USE",
    "research_use": "NON_COMMERCIAL_USE_ONLY_UNLESS_SEPARATE_AGREEMENT",
    "redistribution": "RESTRICTED_BY_TERMS_AND_EMBEDDED_THIRD_PARTY_RIGHTS",
    "modification": "RESTRICTED; third-party content can impose additional conditions",
    "derived_data": "COMMERCIALISATION_RESTRICTED_WITHOUT_CONSENT",
    "local_copy_allowed": "API_SNAPSHOT_ALLOWED_SUBJECT_TO_TERMS",
    "attribution_required": "YES",
}


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_pairs,
    )
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve_repo_path(root: Path, value: str) -> Path:
    root_resolved = root.resolve()
    candidate = (root_resolved / value).resolve()
    if not candidate.is_relative_to(root_resolved):
        raise ValueError("path escapes repository root")
    return candidate


def _resource_map(payload: dict[str, Any], errors: list[str]) -> dict[str, dict[str, Any]]:
    resources = payload.get("resources")
    if not isinstance(resources, list):
        errors.append("Stage 6 resources must be a list")
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in resources:
        if not isinstance(item, dict):
            errors.append("Stage 6 resource entry must be an object")
            continue
        resource_id = item.get("id")
        if not isinstance(resource_id, str) or not resource_id:
            errors.append("Stage 6 resource id missing")
            continue
        if resource_id in result:
            errors.append(f"Stage 6 duplicate resource id: {resource_id}")
            continue
        result[resource_id] = item
    return result


def _validate_required_fields(root: Path, resource: dict[str, Any], errors: list[str]) -> None:
    resource_id = str(resource.get("id", "<missing>"))
    for field in REQUIRED_FIELDS:
        value = resource.get(field)
        if value is None or value == "" or value == []:
            errors.append(f"Stage 6 resource field missing: {resource_id}.{field}")
    status = resource.get("status")
    if status not in STATUS_VOCABULARY:
        errors.append(f"Stage 6 invalid status: {resource_id}={status}")
    license_value = str(resource.get("license", "")).strip().upper()
    if license_value in {"UNKNOWN", "NOASSERTION", "NONE", ""}:
        errors.append(f"Stage 6 silent/unknown license is forbidden: {resource_id}")
    if license_value.startswith("NOT_VERIFIED") and status != "REVIEW_REQUIRED":
        errors.append(f"Stage 6 unverified terms cannot pass as documented: {resource_id}")
    evidence = resource.get("evidence")
    if not isinstance(evidence, list) or not evidence or not all(isinstance(v, str) and v for v in evidence):
        errors.append(f"Stage 6 evidence list invalid: {resource_id}")

    local_artifact = resource.get("local_artifact")
    digest = resource.get("sha256")
    if local_artifact is not None:
        if not isinstance(local_artifact, str) or not local_artifact:
            errors.append(f"Stage 6 local artifact path invalid: {resource_id}")
        else:
            try:
                path = _resolve_repo_path(root, local_artifact)
            except (OSError, ValueError):
                errors.append(f"Stage 6 local artifact escapes repository root: {resource_id}")
            else:
                if not path.is_file():
                    errors.append(f"Stage 6 local artifact missing: {resource_id} -> {local_artifact}")
                elif not isinstance(digest, str) or not SHA256.fullmatch(digest):
                    errors.append(f"Stage 6 local artifact digest invalid: {resource_id}")
                elif _sha256(path) != digest:
                    errors.append(f"Stage 6 local artifact digest mismatch: {resource_id}")
    elif digest not in NO_LOCAL_HASH_SENTINELS:
        errors.append(f"Stage 6 non-local resource must use an explicit no-local-copy digest sentinel: {resource_id}")

    if status == "RESTRICTED":
        joined = " ".join(str(resource.get(k, "")) for k in (
            "commercial_use", "clinical_use", "redistribution", "modification", "derived_data", "limitations"
        )).upper()
        if not any(marker in joined for marker in ("PROHIBITED", "RESTRICTED")):
            errors.append(f"Stage 6 restricted resource lacks explicit restriction: {resource_id}")


def _validate_adapters(resources: dict[str, dict[str, Any]], errors: list[str]) -> None:
    mapped: set[str] = set()
    for resource in resources.values():
        adapters = resource.get("adapter_ids", [])
        if not isinstance(adapters, list) or not all(isinstance(item, str) for item in adapters):
            errors.append(f"Stage 6 adapter_ids invalid: {resource['id']}")
            continue
        mapped.update(adapters)
    if mapped != EXPECTED_ADAPTERS:
        errors.append(f"Stage 6 evidence-adapter coverage mismatch: {sorted(mapped)}")


def _validate_grch38_manifest(root: Path, resources: dict[str, dict[str, Any]], payload: dict[str, Any], errors: list[str]) -> None:
    manifest = root / "manifests/GRCh38.sources.tsv"
    with manifest.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    actual = {row["artifact_id"] for row in rows}
    exempt = payload.get("derived_manifest_exemptions")
    if not isinstance(exempt, list) or not all(isinstance(item, str) for item in exempt):
        errors.append("Stage 6 derived manifest exemptions invalid")
        return
    covered: set[str] = set(exempt)
    for resource in resources.values():
        ids = resource.get("covers_manifest_artifact_ids", [])
        if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
            errors.append(f"Stage 6 manifest coverage list invalid: {resource['id']}")
            continue
        covered.update(ids)
    if covered != actual:
        errors.append(
            "Stage 6 GRCh38 manifest licensing coverage mismatch: "
            f"missing={sorted(actual-covered)} extra={sorted(covered-actual)}"
        )


def _validate_pgs(root: Path, resource: dict[str, Any], errors: list[str]) -> None:
    terms = resource.get("record_level_terms")
    if not isinstance(terms, dict):
        errors.append("Stage 6 PGS record-level terms metadata missing")
        return
    artifact = terms.get("artifact")
    if not isinstance(artifact, str):
        errors.append("Stage 6 PGS record-level artifact invalid")
        return
    try:
        path = _resolve_repo_path(root, artifact)
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle, object_pairs_hook=_reject_duplicate_pairs)
    except (OSError, ValueError) as exc:
        errors.append(f"Stage 6 PGS artifact unreadable: {type(exc).__name__}: {exc}")
        return
    scores = payload.get("scores")
    if not isinstance(scores, dict):
        errors.append("Stage 6 PGS score registry invalid")
        return
    if terms.get("score_count") != len(scores):
        errors.append("Stage 6 PGS score count mismatch")
    empty_licenses = [score_id for score_id, record in scores.items() if not isinstance(record, dict) or not str(record.get("license", "")).strip()]
    if empty_licenses:
        errors.append(f"Stage 6 PGS scores without license terms: {len(empty_licenses)}")
    restrictive = sum(
        1 for record in scores.values()
        if isinstance(record, dict) and record.get("license_is_restrictive") is True
    )
    if terms.get("restricted_score_count") != restrictive:
        errors.append("Stage 6 PGS restrictive-license count mismatch")
    totals = payload.get("totals")
    if not isinstance(totals, dict) or totals.get("scores_with_restrictive_license") != restrictive:
        errors.append("Stage 6 PGS retained summary does not match score-level restrictions")
    if resource.get("status") != "RECORD_LEVEL_TERMS_REQUIRED":
        errors.append("Stage 6 PGS Catalog must remain record-level licensed")
    if resource.get("local_copy_allowed") != "METADATA_ONLY_IN_REPOSITORY; WEIGHTS_NOT_COPIED":
        errors.append("Stage 6 PGS local-copy policy must not imply score weights are vendored")


def _validate_no_pgs_weights_committed(root: Path, errors: list[str]) -> None:
    skip = {".git", "node_modules", "__pycache__", ".mypy_cache"}
    offenders: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or any(part in skip for part in path.parts):
            continue
        if PGS_WEIGHTS.fullmatch(path.name):
            offenders.append(str(path.relative_to(root)))
    if offenders:
        errors.append(f"Stage 6 PGS score-weight files must not be committed: {offenders[:10]}")


def collect_errors(root: Path = ROOT) -> list[str]:
    registry_path = root / "config/data_source_registry.yaml"
    errors: list[str] = []
    if not registry_path.is_file():
        return ["Stage 6 data source registry missing"]
    try:
        payload = _load_json(registry_path)
    except (OSError, ValueError) as exc:
        return [f"Stage 6 data source registry unreadable: {type(exc).__name__}: {exc}"]
    if payload.get("schema") != "omnigenis-scientific-data-source-registry-v1":
        errors.append("Stage 6 data source registry schema mismatch")
    if payload.get("stage") != 6:
        errors.append("Stage 6 registry stage mismatch")
    claims = payload.get("claims")
    if not isinstance(claims, dict) or any(claims.get(key) is not False for key in (
        "license_clean", "all_resources_commercially_cleared", "all_resources_clinically_cleared", "stage6_pass_means_legal_clearance"
    )):
        errors.append("Stage 6 registry must explicitly reject legal-clearance claims")
    if set(payload.get("status_vocabulary", [])) != STATUS_VOCABULARY:
        errors.append("Stage 6 status vocabulary mismatch")
    resources = _resource_map(payload, errors)
    if set(resources) != EXPECTED_RESOURCE_IDS:
        errors.append(
            "Stage 6 resource coverage mismatch: "
            f"missing={sorted(EXPECTED_RESOURCE_IDS-set(resources))} extra={sorted(set(resources)-EXPECTED_RESOURCE_IDS)}"
        )
    for resource in resources.values():
        _validate_required_fields(root, resource, errors)
    _validate_adapters(resources, errors)
    _validate_grch38_manifest(root, resources, payload, errors)
    pgs = resources.get("ebi-pgs-catalog")
    if pgs:
        _validate_pgs(root, pgs, errors)
    _validate_no_pgs_weights_committed(root, errors)
    panelapp = resources.get("panelapp-genomics-england")
    if panelapp:
        for field, expected in PANELAPP_ENGLAND_PROTECTED_FIELDS.items():
            if panelapp.get(field) != expected:
                errors.append(
                    "Stage 6 Genomics England PanelApp protected field drift: "
                    f"{field}"
                )
    for resource_id in ("panelapp-australia", "gnomad", "aadr"):
        unresolved_resource = resources.get(resource_id)
        if unresolved_resource and unresolved_resource.get("status") != "REVIEW_REQUIRED":
            errors.append(f"Stage 6 unresolved official terms must fail closed: {resource_id}")
    return errors

def main() -> int:
    errors = collect_errors(ROOT)
    if errors:
        for error in errors:
            print(f"FAIL\t{error}")
        return 1
    payload = _load_json(REGISTRY)
    counts = Counter(item["status"] for item in payload["resources"])
    print(
        "PASS\tstage6_scientific_data_registry\t"
        f"resources={len(payload['resources'])} "
        f"restricted={counts['RESTRICTED']} "
        f"record_level={counts['RECORD_LEVEL_TERMS_REQUIRED']} "
        f"review_required={counts['REVIEW_REQUIRED']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
