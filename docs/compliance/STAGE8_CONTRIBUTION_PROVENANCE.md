# Stage 8 — Authorship and Contribution Provenance

Stage 8 makes technical contribution provenance explicit and machine-verifiable without turning Git metadata into a legal ownership conclusion.

## What is recorded

Each governed change set records:

- a full base commit SHA;
- a full implementation commit SHA;
- an origin class;
- an assistance class;
- whether human direction was declared;
- whether third-party code was introduced;
- a deterministic manifest of changed paths and SHA-256 hashes;
- an append-only ledger entry that points to that manifest.

The manifest is generated from Git objects at the implementation commit. Later edits therefore do not rewrite historical evidence.

## Assistance disclosure

The policy distinguishes human-only, AI-assisted, automation-assisted, multi-tool-assisted, and unresolved/historical states. The declaration describes technical process only. It does not identify a vendor and does not imply that a tool is an author, copyright holder, contributor of record, or legal rights holder.

## Legal boundary

Git history, pull requests, reviews, commit verification and Stage 8 manifests are technical provenance evidence. They do not by themselves prove exclusive ownership, assignment, work-for-hire status, employment rights, patent rights, or the absence of third-party claims.

Any third-party code introduction remains subject to the Stage 4–7 inventory, licensing and purpose-of-use controls. Stage 8 cannot waive or override those gates.

## Fail-closed behavior

New ledger entries cannot use UNKNOWN origin as a machine-authorized state. Repository-native entries require explicit human direction. Any third-party-code introduction must be reviewed through the earlier licensing controls instead of being silently accepted.

During pull-request validation, the ledger is checked for append-only continuity against the PR base and the latest manifest must cover the implementation diff. Control-plane evidence files are allowed only as the small evidence-only child of the implementation commit.
