# Stage 4 — Third-Party Software Inventory and SBOM

## Purpose

Stage 4 establishes the auditable software/container licensing inventory for OmniGenis. It does not declare the repository, image, operating system, or runtime license-clean.

The stage covers third-party software that is shipped, installed, or executed as part of the build/runtime boundary:

- digest-pinned container base image and its installed operating-system packages;
- the complete Linux x86_64 Conda resolution, including transitive packages;
- pinned Python reporting dependencies;
- npm direct and transitive dependencies;
- pinned third-party GitHub Actions used by the build;
- the SBOM generation tool itself.

Scientific datasets, scores, models, reference databases, and resource-specific usage terms remain a separate licensing domain and are intentionally deferred to the later scientific-data licensing stage.

## Canonical artifacts

- `config/third_party_software_registry.json` — normalized software registry and policy triage.
- `locks/conda-linux-64-resolution.json` — exact Conda transitive resolution with source URL, build, SHA-256, and declared license.
- `locks/conda-linux-64-explicit.txt` — executable Linux x86_64 Conda lock installed by the Docker build.
- `locks/base-image-software.json` — package/license snapshot of the digest-pinned base image.
- `locks/python-license-metadata.json` — exact PyPI package/license metadata for the reporting lock.
- `locks/action-license-metadata.json` — license metadata for the SHA-pinned GitHub Actions.
- `locks/sbom-tool-lock.json` — exact Syft release, commit, archive URL, and archive SHA-256.
- `docs/evidence/STAGE4_THIRD_PARTY_INVENTORY_2026-09-18.json` — execution/evidence summary.

All compliance locks are integrity-bound from `locks/runtime-lock.json`.

## Standards and generator boundary

CycloneDX 1.7 is the current stable CycloneDX specification used by this stage.

SPDX 3.0 is the current upstream SPDX specification. The pinned Syft 1.52.0 generator currently emits SPDX 2.3 JSON, so OmniGenis records that capability boundary explicitly rather than mislabeling SPDX 2.3 as the current SPDX specification.

Official references:

- https://cyclonedx.org/specification/overview/
- https://spdx.dev/use/specifications/
- https://oss.anchore.com/docs/reference/syft/cli/
- https://github.com/anchore/syft/releases/tag/v1.52.0

## Runtime locking change

Before Stage 4, `environment.yml` pinned critical top-level packages but allowed the Conda solver to choose transitive package builds at image-build time.

Stage 4 preserves `environment.yml` as the human-reviewed dependency specification but changes the Docker build to install `locks/conda-linux-64-explicit.txt`. The explicit lock was derived from a successful micromamba 2.9.0 Linux x86_64 solve and contains exact package URLs and SHA-256 identities for the complete 161-package resolution.

This means the software inventory and the software installed by the image build are bound to the same package identities.

## npm runtime boundary

The Docker image still installs npm dependencies required for the TypeScript build. After `npm run build`, it runs `npm prune --omit=dev --ignore-scripts` so development-only packages are removed from the installed runtime tree.

The source `package-lock.json` remains part of the repository and registry because it is the provenance record for both production and build dependencies.

## Policy triage

The registry applies the repository dependency policy conservatively:

- `PERMISSIVE` — license metadata matches the repository's permissive baseline; notices still remain required.
- `REVIEWED_ACCEPTED_EXACT_ARTIFACT` — an exact artifact has already received the repository's technical review and integrity evidence.
- `REVIEW_REQUIRED` — linkage, exception, redistribution, attribution, source, or custom-license obligations need artifact-specific review.
- `BLOCKED_BY_DEFAULT` — the repository policy does not permit a new distributed-runtime use without an explicit documented exception or remediation.

These states are engineering/compliance controls, not legal opinions.

## Current findings

The Stage 4 registry contains 417 component records:

- 101 base-image/Ubuntu package records;
- 161 Conda package records;
- 8 Python package records;
- 137 npm package records;
- 10 GitHub Action records.

The current registry intentionally reports default-blocked and review-required components. This includes copyleft software present in the base operating-system layer and Conda environment.

Therefore:

- OmniGenis MUST NOT claim to be `LICENSE-CLEAN`, GPL-free, or copyleft-free.
- Stage 4 inventory completion does not equal redistribution clearance.
- Exact notice, source-availability, linkage, and redistribution obligations for affected artifacts remain open work.
- Stage 5 may automate fail-closed policy enforcement only after the Stage 4 inventory and exceptions/remediation decisions are stable.

## Final-image SBOM contract

The pull-request container job builds the real OmniGenis image and then executes `scripts/generate_stage4_sbom.sh`.

The script:

1. downloads the exact Syft archive from the locked upstream release;
2. verifies its SHA-256 before execution;
3. generates Syft JSON, SPDX 2.3 JSON, and CycloneDX 1.7 JSON from the built image;
4. extracts the installed Conda environment from the same built image with `micromamba list --json`;
5. reconciles every non-virtual installed Conda package against the audited 161-package lock;
6. validates scanner-visible package coverage and required Python/npm runtime components;
7. rejects reintroduction of the retired Poppler runtime in both scanner and Conda inventories;
8. writes SHA-256 hashes for all generated evidence files;
9. uploads the SBOM/evidence bundle as CI evidence.

The published main image also enables BuildKit provenance and SBOM attestations.

## Validation

Repository-local validation:

```bash
python3 scripts/build_third_party_registry.py --check
python3 scripts/validate_stage4_compliance.py
python3 scripts/verify_supply_chain_lock.py
python3 scripts/validate_repo.py
```

Final-image validation is executed by CI because the current remote-development executor does not have permission to access its Docker daemon.

## Status semantics

Stage 4 can be considered complete when:

- all committed inventory/lock validations pass;
- the final-image SBOM job passes on the exact pull-request HEAD;
- generated SPDX/CycloneDX/Syft SBOMs are uploaded as CI artifacts;
- the PR does not claim that open license obligations are resolved.

A successful Stage 4 means **inventory and evidence coverage are established**. It does not mean all third-party distribution obligations have been discharged.
