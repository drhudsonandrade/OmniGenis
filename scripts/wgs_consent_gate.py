#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.genetic_data_privacy_gate import evaluate_privacy


def evaluate_consent(manifest: dict[str, Any], *, requested_purpose: str) -> dict[str, Any]:
    errors: list[str] = []
    sample_id = str(manifest.get("sample_id") or "").strip()
    case_id = str(manifest.get("case_id") or "").strip()
    consent_value = manifest.get("consent")
    consent: dict[str, Any] = consent_value if isinstance(consent_value, dict) else {}
    provenance_value = manifest.get("provenance")
    provenance: dict[str, Any] = (
        provenance_value if isinstance(provenance_value, dict) else {}
    )
    manifest_input_sha = str(provenance.get("input_sha256") or "").strip().lower()
    privacy_value = manifest.get("privacy")
    privacy: dict[str, Any] = privacy_value if isinstance(privacy_value, dict) else {}
    privacy_result = evaluate_privacy(
        privacy,
        requested_purpose=requested_purpose,
        case_id=case_id or "__MISSING_CASE_ID__",
        input_sha256=manifest_input_sha or "__MISSING_INPUT_SHA256__",
    )

    if not sample_id:
        errors.append("sample_id missing")
    if not case_id:
        errors.append("case_id missing")
    if len(manifest_input_sha) != 64 or any(
        c not in "0123456789abcdef" for c in manifest_input_sha
    ):
        errors.append("provenance.input_sha256 must be a lowercase SHA-256 digest")
    if consent.get("status") != "VERIFICADO":
        errors.append("consent.status must be VERIFICADO")
    if not str(consent.get("consent_id") or "").strip():
        errors.append("consent.consent_id missing")
    if not str(consent.get("version") or "").strip():
        errors.append("consent.version missing")
    purpose_value = consent.get("purposes")
    purposes: list[Any] = purpose_value if isinstance(purpose_value, list) else []
    if requested_purpose not in purposes:
        errors.append(f"requested purpose not authorized: {requested_purpose}")
    if provenance.get("status") != "VERIFICADO":
        errors.append("provenance.status must be VERIFICADO")
    if not str(provenance.get("source") or "").strip():
        errors.append("provenance.source missing")
    if not str(provenance.get("chain_of_custody_ref") or "").strip():
        errors.append("provenance.chain_of_custody_ref missing")
    if privacy_result.get("ready_for_genetic_processing") is not True:
        errors.extend(f"privacy: {error}" for error in privacy_result.get("errors", []))

    return {
        "schema": "genoma-consent-provenance-gate-v1",
        "gate": "CONSENT_PROVENANCE_GATE",
        "status": "VERIFICADO" if not errors else "NÃO DISPONÍVEL",
        "sample_id": sample_id or None,
        "case_id": case_id or None,
        "input_sha256": manifest_input_sha or None,
        "requested_purpose": requested_purpose,
        "ready_for_first_dna_read": not errors,
        "secondary_findings": consent.get("secondary_findings", "NÃO DISPONÍVEL"),
        "consent_id": consent.get("consent_id"),
        "consent_version": consent.get("version"),
        "provenance_ref": provenance.get("chain_of_custody_ref"),
        "privacy_clearance": privacy_result,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--purpose", default="genomic_analysis")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    result = evaluate_consent(manifest, requested_purpose=args.purpose)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ready_for_first_dna_read"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
