# Third-Party Notices

This file began as the Stage 1 third-party licensing baseline and is updated as remediation stages are completed. It is intentionally not yet a complete SBOM or complete transitive-license inventory.

Third-party software, datasets, standards, reference resources, and documentation remain governed by their own terms. OmniGenis does not relicense those materials under the repository's proprietary license.

## Stage 2 PDF runtime remediation

- PyMuPDF/MuPDF is removed from the active editorial coordinate-compiler runtime, its direct reporting dependency lock, active compiler tests, and active visual-QA workflow. Historical evidence may retain references to the dependency that was actually used when that evidence was created.
- `pypdfium2==5.13.0` is the replacement wrapper. Its wrapper code is offered under Apache-2.0 OR BSD-3-Clause; its documentation/examples also carry CC-BY-4.0 terms. The exact Linux x86_64 wheel inspected for this stage is pinned in `licenses/pypdfium2-5.13.0/README.md`.
- PDFium uses a BSD-style license. Binary PDFium distributions also carry licenses for bundled dependencies, each of which remains governed by its own terms; the inspected pypdfium2 wheel preserves those notices in its installed license directory.
- Stage 2 technically accepts only this exact verified artifact boundary for the active coordinate-compiler runtime. This is not a repository-wide license-clean certification or a legal opinion. A different package version, platform wheel, or custom PDFium build requires renewed license review.

## Known remediation items

- Poppler remains a known licensing remediation item and is not treated as license-cleared by this stage.
- Scientific datasets and scoring resources may carry per-resource or per-score restrictions that must be evaluated independently.

## Required handling

Before distribution or production use, each included third-party artifact must have its exact version, source, license, required notices, commercial-use status, redistribution status, and integrity evidence recorded.

A later compliance stage will generate the complete third-party registry and SBOM. Until that inventory is reconciled, no document or build may claim that OmniGenis is `LICENSE-CLEAN`.
