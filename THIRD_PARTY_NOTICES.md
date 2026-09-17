# Third-Party Notices

This file is the Stage 1 baseline for third-party licensing provenance. It is intentionally not yet a complete SBOM or complete transitive-license inventory.

Third-party software, datasets, standards, reference resources, and documentation remain governed by their own terms. OmniGenis does not relicense those materials under the repository's proprietary license.

## Known remediation items

- PyMuPDF/MuPDF is a known licensing remediation item and is not treated as license-cleared by this baseline.
- Poppler is a known licensing remediation item and is not treated as license-cleared by this baseline.
- Scientific datasets and scoring resources may carry per-resource or per-score restrictions that must be evaluated independently.

## Required handling

Before distribution or production use, each included third-party artifact must have its exact version, source, license, required notices, commercial-use status, redistribution status, and integrity evidence recorded.

A later compliance stage will generate the complete third-party registry and SBOM. Until that inventory is reconciled, no document or build may claim that OmniGenis is `LICENSE-CLEAN`.
