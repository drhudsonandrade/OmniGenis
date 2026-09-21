# Stage 11 — Final Governance, Integrity and Evidence Audit

## Purpose

Stage 11 closes the copyright and governance remediation sequence by executing a repository-level audit across Stages 1 through 10. It reuses the validators and evidence contracts already established by those stages instead of creating a second legal, clinical or regulatory decision system.

The canonical Stage 11 policy is `config/stage11_governance_audit_policy.json`. The execution entrypoint is `scripts/run_stage11_governance_audit.py`.

## Scope

The audit covers:

1. ownership and licensing baseline;
2. PyMuPDF replacement and the reviewed PDFium boundary;
3. active-runtime strong-copyleft cleanup;
4. third-party inventory and SBOM governance;
5. automated software-license enforcement;
6. scientific-data licensing and provenance;
7. purpose-bound use enforcement;
8. authorship/contribution and AI-assistance provenance;
9. genetic-data privacy governance;
10. research, clinical and regulatory use boundaries.

For each stage, the audit verifies that the recorded merge commit is an ancestor of the audited implementation SHA and executes the stage's current repository validator. Stage 1 has a dedicated baseline inspection because it predates the standalone stage-validator pattern.

The audit also executes the supply-chain lock verifier, residual-language audit and code-language guard as cross-stage integrity controls.

## Evidence semantics

The Stage 11 evidence keeps execution status separate from result:

- `EXECUTADO` means the check actually ran;
- `NÃO DISPONÍVEL` means the check could not be executed;
- `PASS` means an executed check accepted the current repository state;
- `FAIL` means an executed check rejected it;
- `ERROR` means the check could not produce a valid conclusion.

Unknown, unavailable, timed-out or failed controls never become PASS.

The evidence binds the exact implementation commit and Git tree together with the SHA-256 identities of the Stage 11 policy and execution script.

## Non-overlap with the clinical FINAL_AUDIT_GATE

Stage 11 does **not** replace `FINAL_AUDIT_GATE` in the Policy Control/Audit Plane. That gate remains responsible for case-level criteria required before a `FINAL_AUDITED_REPORT` can be released.

Stage 11 is a repository governance-chain audit only. It does not:

- determine legal compliance;
- certify the repository or image as license-clean;
- grant clinical validity;
- classify or approve a medical device;
- issue regulatory clearance;
- issue research-ethics approval.

Those non-claims are machine-enforced by the Stage 11 policy and evidence validator.

## Change control

The final Stage 11 evidence is generated only after the implementation has been committed. The evidence must name that immutable implementation SHA and tree. Subsequent evidence/provenance commits may only preserve or strengthen the contract; they may not rewrite the audited implementation identity.

Stage 11 does not authorize auto-merge. Final merge remains human-only.
