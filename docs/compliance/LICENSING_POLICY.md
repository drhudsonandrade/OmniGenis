# OmniGenis Licensing Policy

## Scope

This policy separates repository-native OmniGenis material from third-party software, datasets, scientific resources, standards, specifications, and reference artifacts.

## Repository-native material

Repository-native material is proprietary unless an individual file or component explicitly states a different license. Public visibility does not create an open-source grant.

Independent implementation must be based on requirements, primary scientific literature, public standards, public APIs, or independently written specifications. Do not copy competitor source code, distinctive internal schemas, prompts, tests, error messages, class naming, or internal organization.

## Third-party material

Third-party material keeps its original license and attribution requirements. No OmniGenis document may imply that external material is owned by or relicensed by OmniGenis.

Every dependency or data source must ultimately record exact version, source, license, commercial-use rights, redistribution rights, required attribution, retrieval evidence, and integrity evidence.

## Compliance states

Use `APPROVED`, `APPROVED_WITH_NOTICE`, `RESTRICTED`, `BLOCKED`, or `UNKNOWN` for compliance classification. `UNKNOWN` is never equivalent to approval.

Strong-copyleft, source-available, non-commercial, academic-only, research-only, or unknown terms require explicit review before introduction into a distributed or production runtime.

Existing unresolved dependencies are remediation debt, not grandfathered approvals.

## Claims and change control

Do not claim `LICENSE-CLEAN` until the complete dependency, transitive, container, and data-source inventories have been reconciled and the final compliance gate has passed.

Licensing changes require a dedicated pull request, evidence of the exact affected artifacts, and human merge approval. This Stage 1 policy does not modify the canonical ruleset, scientific behavior, evidence schemas, or runtime contracts.
