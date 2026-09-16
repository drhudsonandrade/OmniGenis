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

PyMuPDF/MuPDF and Poppler are unresolved licensing items. Their current presence must not be interpreted as approval or as a permanent exception.

## Enforcement boundary

Automated license gating and SBOM reconciliation are separate later stages. Until those controls are merged, reviewers must apply this policy manually and fail closed on `UNKNOWN` or undocumented restricted terms.
