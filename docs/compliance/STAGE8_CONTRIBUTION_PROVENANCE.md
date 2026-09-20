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

## Third-party component truth

A change set may truthfully declare `third_party_code_introduced: true`. It must also name one or more `third_party_component_ids`, and every ID must exist in the Stage 4 third-party software inventory and be marked `allowed_for_new_dependency: true` by the Stage 5 software-license gate. Stage 8 does not convert an unreviewed component into repository-native code and does not waive any Stage 6/7 restriction that independently applies to scientific data or purpose of use.

The ledger and manifest must agree on `human_direction`, `third_party_code_introduced`, and the component IDs. Repository-native change sets still require `human_direction: true`; other origin classes are not silently rewritten to that value.
## Durable manifest v2

New change sets use `omnigenis-contribution-provenance-manifest-v2`. Each governed path records its Git transition (`A`, `M`, `D` or `T`) together with the SHA-256 state before and after the implementation commit. Deletions are represented by an explicit tombstone: the base digest is retained and the implementation digest is null. Rename detection is disabled deliberately, so a rename is preserved as one deletion plus one addition.

The builder also records the base and implementation tree identities and declares that the Git objects were verified at capture time. Before a pull request can pass, the Stage 8 validator re-derives the latest manifest from the live Git objects, verifies the tree identities and file digests, and requires the implementation commit to be reachable from the current HEAD.

After capture, only the ledger, runtime lock and that exact manifest may change before the evidence commit. The validator compares `implementation_sha..HEAD` directly, so editing an already-manifested implementation file again cannot disappear through path-set subtraction.

## Durable historical verification

Historical manifests are retained as hash-locked compliance artifacts in `locks/runtime-lock.json`. Once a change set has passed exact-Git validation, later repository validation verifies the retained artifact and its declared digests without requiring the intermediate implementation commit to remain reachable forever. This keeps provenance verifiable after an allowed squash/rebase or branch deletion instead of turning ephemeral Git objects into a permanent runtime dependency. Legacy v1 manifests remain readable as retained historical evidence; the latest entry of a new pull request must use v2.

An `UNKNOWN` origin may remain in inherited historical ledger entries as explicitly unresolved provenance. A newly appended entry cannot use `UNKNOWN` as machine-authorized origin.

## Governed documentation

The Markdown-only CI optimization does not bypass Stage 8 for `AUTHORS.md`, `COPYRIGHT.md`, `CONTRIBUTING.md` or this document. Changes to these governance surfaces force repository validation and therefore require a corresponding provenance update.
## Third-party code declarations

`third_party_code_introduced` records what actually happened; it is not required to be false. A change set that introduces third-party software must list its canonical `third_party_component_ids`. Stage 8 accepts those IDs only when the same component exists in the Stage 4 inventory and the Stage 5 software-license gate marks it `allowed_for_new_dependency=true`. Missing, duplicate, unknown or non-approved component IDs fail closed. Scientific-data rights remain governed independently by Stages 6 and 7; a software-component approval cannot authorize a dataset or a purpose of use.

The ledger and manifest must agree on `third_party_code_introduced`, `third_party_component_ids` and `human_direction`. Repository-native changes require `human_direction=true`; third-party or mixed records may use another value only when both evidence surfaces record the same value. This prevents a manifest from silently contradicting its ledger.
