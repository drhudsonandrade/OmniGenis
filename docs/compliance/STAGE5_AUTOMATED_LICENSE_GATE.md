# Stage 5 — Automated Software License Gate

## Purpose

Stage 5 turns the Stage 4 third-party software inventory into deterministic, fail-closed pull-request enforcement. It is an engineering compliance control, not a legal opinion, and it does not declare OmniGenis `LICENSE-CLEAN`.

Scientific datasets, scores, models, reference databases, and resource-specific usage terms are intentionally outside this stage and remain reserved for Stage 6.

## Canonical artifacts

- `config/software_license_policy.json` — immutable Stage 5 software-policy vocabulary and bootstrap identity.
- `config/software_license_gate_registry.json` — deterministic projection of every Stage 4 component into the Stage 5 status vocabulary.
- `locks/stage5-license-debt-baseline.json` — frozen, non-authorizing ceiling for unresolved software-license debt inherited from the Stage 4 merge.
- `scripts/build_stage5_license_gate.py` — deterministic builder and fingerprint logic.
- `scripts/validate_stage5_license_gate.py` — repository and PR enforcement gate.
- `docs/evidence/STAGE5_LICENSE_GATE_2026-09-18.json` — Stage 5 execution/evidence summary.

The three policy/registry/baseline artifacts are integrity-bound from `locks/runtime-lock.json`.

## Closed status vocabulary

- `APPROVED` — the Stage 4 exact component record is within the verified permissive baseline and can be introduced by an ordinary future PR. Routine attribution/notice obligations still apply.
- `APPROVED_WITH_NOTICE` — an exact reviewed artifact is accepted only with its recorded notice/evidence obligations. This is artifact-specific and does not authorize other versions.
- `REVIEW_REQUIRED` — linkage, exception, attribution, custom-license, or other artifact-specific obligations still require explicit disposition.
- `RESTRICTED` — the declared terms contain a known field-of-use or redistribution restriction such as non-commercial, academic-only, research-only, no-derivatives, or source-available language.
- `BLOCKED` — the repository policy blocks the software family by default for a new distributed-runtime introduction, including unreviewed strong-copyleft/network-copyleft cases.
- `UNKNOWN` — license identity or terms are not classified sufficiently for approval. `UNKNOWN != APPROVED`.

Only `APPROVED` and `APPROVED_WITH_NOTICE` are auto-eligible for a new dependency.

## Existing debt is not approval

At the Stage 4 merge, 146 component records were not approved. Stage 5 fingerprints those exact records into an immutable debt baseline. The baseline exists solely so Stage 5 can activate without falsely converting historical unresolved items into approvals.

The gate requires every current non-approved component to match an exact baseline ID, status, and SHA-256 component fingerprint. A new non-approved component or a changed fingerprint fails the gate. Removing a historical debt component is allowed without rewriting the baseline because the baseline is a ceiling, not a desired-state list.

The baseline and policy themselves are immutable after activation under the ordinary PR path. For pull requests after Stage 5 is merged, the gate reads the exact base commit with Git and requires byte-for-byte equality of those two protected artifacts. The initial Stage 5 bootstrap is allowed only from the recorded PR77 merge commit and tree.

## Exact reviewed artifact

`pypi:pypdfium2@5.13.0` retains the prior exact-artifact acceptance from Stage 2 and maps to `APPROVED_WITH_NOTICE` only while the Stage 4 record still contains the required `licenses/pypdfium2-5.13.0/NOTICE.md` evidence. Removing that evidence makes the result fail closed.

## Pull-request enforcement

The required scaffold `static` job executes the Stage 5 gate before the broader repository test suite. For pull requests it supplies `github.event.pull_request.base.sha`, allowing the gate to distinguish the one-time Stage 5 bootstrap from all later changes and to freeze the policy/debt baseline after activation.

`scripts/validate_repo.py` calls the same Stage 5 validator. Policy-change classification also treats the Stage 5 builder, validator, policy, registry, and lock surfaces as policy-relevant so changes cannot silently avoid governance CI.

## External identifier reference

The Stage 5 policy records SPDX License List 3.29.0 (published 2026-09-16) as the identifier-normalization reference. SPDX identifiers do not make an OmniGenis allow/deny decision by themselves; the repository policy remains a separate engineering control.

Official reference: https://spdx.org/licenses/

## Status boundary

A passing Stage 5 gate proves that the current software dependency set has not introduced new non-approved license debt relative to the frozen Stage 4 baseline and that allowed new components meet the automated policy. It does not prove that all inherited notice, source-availability, redistribution, linkage, or other legal obligations have been discharged.
