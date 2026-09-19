# OmniGenis Dependency Licensing Policy

## Coverage

The dependency boundary includes direct and transitive Python, Node.js, Conda, container, operating-system, CLI, scientific-tool, model, dataset, and reference-resource dependencies.

## Default classification

Permissive licenses such as MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, ISC, CC0, and public-domain dedications may be approved after the exact artifact and notice obligations are verified.

Licenses such as LGPL, MPL, EPL, CC BY, or custom terms require artifact-specific review because obligations depend on linkage, distribution, modification, attribution, and deployment model.

AGPL, GPL, SSPL, source-available restrictions, non-commercial terms, academic-only terms, research-only terms, and unknown terms are blocked by default from new distributed-runtime use unless an explicit documented exception is approved.

## Required evidence

Every approved dependency must record its exact version or digest, authoritative source, license identifier or text, required notices, redistribution status, commercial-use status, and integrity evidence.

A package name is not sufficient evidence: classification applies to the exact version and artifact actually used.

## Existing remediation debt

PyMuPDF/MuPDF was removed from the active editorial coordinate-compiler runtime in Stage 2 rather than granted a standing exception. The replacement `pypdfium2==5.13.0` / PDFium boundary is technically accepted for Stage 2 only for the exact artifact and bundled notices recorded in `licenses/pypdfium2-5.13.0/README.md`; this does not replace the later transitive inventory, SBOM, or legal review.

Poppler was removed from the active OmniGenis application runtime in Stage 3 rather than granted a standing exception. The DOCX renderer and editorial canary now use the reviewed PDFium boundary. Stage 3 is an application-layer cleanup only: it does not classify the whole container or operating-system layer as copyleft-free.

Stage 4 establishes the software/container inventory in `config/third_party_software_registry.json`, freezes the complete Linux x86_64 Conda resolution, records base-image/Python/npm/Action licensing metadata, and requires final-image SPDX/CycloneDX/Syft SBOMs in CI. The Stage 4 registry deliberately retains `BLOCKED_BY_DEFAULT` and `REVIEW_REQUIRED` findings for copyleft/custom obligations that have not yet received artifact-specific disposition.

## Enforcement boundary

Stage 5 converts the Stage 4 inventory into the closed enforcement vocabulary `APPROVED`, `APPROVED_WITH_NOTICE`, `REVIEW_REQUIRED`, `RESTRICTED`, `BLOCKED`, and `UNKNOWN`. Only `APPROVED` and `APPROVED_WITH_NOTICE` are eligible for automatic introduction as new software dependencies. `UNKNOWN` is never approval.

The 146 non-approved records present at the Stage 4 merge are frozen in `locks/stage5-license-debt-baseline.json` as a non-authorizing historical debt ceiling. Their presence is not a legal clearance or grandfathered approval. Future pull requests may remove that debt, but may not add new non-approved records, mutate a debt fingerprint, or rewrite the frozen policy/baseline through the ordinary PR gate.

The required `static` workflow executes `scripts/validate_stage5_license_gate.py` against the exact pull-request base SHA. The same gate is called by `scripts/validate_repo.py`, so local repository validation and CI use the same fail-closed policy. Scientific datasets, scores, models, reference resources, and their usage terms remain outside this software gate and are handled in the later scientific-data licensing stage.
