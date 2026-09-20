# GENOMA branch governance

This document defines the repository-side governance required before the control plane is considered closed. It does not claim that GitHub settings are active; the live settings must be verified through the GitHub repository administration surface after they are applied.

## `main`

Target branch: `main`.

Required policy:

- require a pull request before changes reach `main`;
- require the final merge decision to remain manual; the current owner-authored workflow uses zero formal approving reviews because GitHub does not count an author self-approval;
- dismiss stale approvals after new code-modifying commits;
- do not require a separate last-push approval while the repository operates with one owner performing the final human merge decision;
- require status checks to pass before merge;
- require the branch to be up to date before merge;
- block force pushes;
- block branch deletion;
- do not use an unrestricted administrator bypass to turn a failing gate into a merge;
- keep auto-merge disabled unless every required gate and the human merge decision are still enforced.

The required checks must be selected from checks that have actually reported on the repository recently. Do not invent check names. For the current architecture, the selected set must cover the repository/scaffold contract, policy contract, MCP contract, supply-chain/security checks and CodeRabbit review policy used by the active PR process. Any check configured as required must have an unconditional pull-request provider; a path-filtered workflow must not be made globally required because unrelated PRs would wait forever for a check that never starts.

The desired state for `main` is split into two layered rulesets:

- `.github/governance/main-ruleset.json` - deletion, non-fast-forward, and all required CI/security status checks, with **no bypass actors**;
- `.github/governance/main-approval-ruleset.json` - pull-request/review policy only, with the approved User bypass actor represented by provider-stable identity rather than a persisted account name.

The approval-layer bypass does not apply to the Security & CI ruleset, so required checks cannot be bypassed through this architecture. These files are desired-state artifacts, not evidence that GitHub has applied the rulesets.

For the current user-owned repository workflow, the formal GitHub approving-review count is zero and `require_last_push_approval` is disabled. This does not authorize automatic merge: the repository owner must still make the final merge action manually after every required status check is green and every review conversation is resolved. The same ruleset also restricts updates to `main`; only the repository-owner bypass actor may bypass that update restriction, and its `pull_request` mode keeps direct pushes blocked. A collaborator with ordinary `write`/`push` permission therefore cannot complete or directly push an update to `main`. If an independent human reviewer becomes an enforced part of the operating model, the formal approval count can be raised again in a separately reviewed governance change.

## Reviewer retirement — 2026-09-15

The owner retired Greptile for this repository only. `Greptile Review` and
integration ID `867647` must not be required or reintroduced by bootstrap,
restoration, or checkpoint validation. The protected-main manifest now retains
exactly 14 required checks; all other check identities and integration bindings,
strict status-check enforcement, deletion/force-push protection, and the separate
human approval layer remain unchanged.

This corrects a stale desired-state manifest: the authenticated protected-main
ruleset already omitted the retired check. Do not add it to the live ruleset to
make an outdated manifest pass. Continue comparing live state against the current
versioned manifests and retain fail-closed behavior for any other divergence.

GitHub App repository access is a separate administrative surface. Removing a
required status check does not prove that application access was revoked. Verify
repository-specific removal in the installation settings without uninstalling or
changing the application for other repositories. Historical plans and evidence
remain historical records, not authority to reactivate the retired reviewer.

### Retained blocking review responsibilities

The authorized retirement consolidates general AI code review into the existing
mandatory `CodeRabbit` check; it does not remove the AI review responsibility.
The equivalent responsibility is enforced by the retained reviewer rather than by
adding another provider or bypassing a failed check. This is not a claim that two
models have identical findings, recall, or implementation.

| Responsibility formerly shared by AI reviewers | Retained blocking control | Auditable contract |
| --- | --- | --- |
| Correctness, regressions, test integrity, and architectural review | `CodeRabbit`, with request-changes workflow enabled | `.coderabbit.yaml` review profile, path instructions, and error-mode pre-merge checks; `tests/test_coderabbit_guardrails.py` |
| Repository, runtime, and policy regressions | `static`, `container-canary`, `Canonical policy + 263-rule contract`, and the existing conditional parity/runtime jobs | `.github/governance/main-ruleset.json` and the corresponding workflow contracts |
| Static analysis, secrets, dependencies, and security findings | All existing DeepSource, GitGuardian, dependency-security, and Semgrep requirements | Unchanged remaining check identities and integration bindings in the protected-main manifest |
| Manual owner merge decision and mandatory conversation resolution | The separate pull-request governance ruleset | `.github/governance/main-approval-ruleset.json` |

The acceptance evidence for this consolidation is the exact remaining-check
manifest, authenticated live ruleset comparison, reviewer configuration tests,
and the retained CodeRabbit review and pre-merge results on the candidate SHA.
A green `CodeRabbit` commit status alone is insufficient: its error-mode
pre-merge checks and approval state must also be inspected. Pending or failing
pre-merge checks must not be overridden to complete this retirement.

## `audit-evidence`

Target branch: `audit-evidence`.

This branch is an append-only publication surface for production witnesses, not a normal development branch. The production witness workflow already refuses to overwrite a witness for an existing exact Git SHA unless the content is byte-identical.

Required policy:

- block force pushes for every actor, including the publisher;
- block branch deletion for every actor, including the publisher;
- restrict ordinary updates to the trusted deploy-key publisher;
- preserve direct append publication by the reviewed `GENOMA Production Witness` publisher;
- do not grant the publisher a bypass over history-mutation protections;
- do not require a normal pull-request merge path that would prevent the reviewed witness publisher from appending evidence;
- periodically verify that every `latest.json` target also exists under `witnesses/<git_sha>/witness.json` with matching `SHA256SUMS`.

The desired state is deliberately split into two layered GitHub rulesets:

- `.github/governance/audit-evidence-integrity-ruleset.json` — `deletion` + `non_fast_forward`, with **no bypass actors**;
- `.github/governance/audit-evidence-publisher-ruleset.json` — `update` only, with the GitHub `DeployKey` actor class as the bypass actor.

For GitHub rulesets, a `DeployKey` bypass is represented with `actor_id: null`; this field therefore does not identify one numeric deploy-key ID. The desired-state JSON limits that bypass to the update-only layer, so it does not share a ruleset with deletion or non-fast-forward protections. Before governance can be marked verified, the live repository settings must also be read back to confirm that only the intended publisher deploy key is write-enabled for this publication path. If additional write-enabled deploy keys exist or the live rulesets differ from this design, keep governance status `PENDING`.

These JSON files are desired-state artifacts, not evidence that GitHub has applied the rulesets or installed the corresponding deploy key.

If the repository plan/settings cannot express the documented layered restriction, record the limitation explicitly and keep governance status `PENDING`; do not represent the desired-state JSON as enforced.

## Verification record

After applying the GitHub settings, record all of the following in the closure report:

- repository ID: `1212760346`; repository name: `OmniGenis`;
- branch names: `main`, `audit-evidence`;
- observed protected/ruleset state for each branch;
- required status checks actually configured on `main`;
- force-push and deletion policy for both branches;
- update-restriction bypass actor class on `audit-evidence`;
- write-enabled deploy keys observed for the repository and which one is the intended publisher;
- verification timestamp and GitHub settings/ruleset locator.

The repository must remain **GOVERNANCE PENDING** until the live GitHub settings are read back and match this contract.

## GitHub documentation

- Rulesets: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets
- Available rules, including required status checks and force-push controls: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets
- Protected branches: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches

Provider account-derived required checks are stored as SHA-256 fingerprints plus provider-family metadata. The tracked `main-ruleset.json` is therefore a neutral desired-state specification, not a provider API payload. Before applying or restoring it, fetch the authenticated live ruleset, materialize the fingerprinted entry, inspect the generated provider payload, and only then submit that generated payload to GitHub.

```bash
runner_temp="${RUNNER_TEMP:-}"
if [[ -z "$runner_temp" ]]; then
  runner_temp="$(mktemp -d)"
fi
test -d "$runner_temp"
repo="$(gh api repositories/1212760346 --jq .full_name)"
test -n "$repo"
gh api "repos/$repo/rulesets/21303100" > "$runner_temp/live-main-ruleset.json"
python3 scripts/governance_context_identity.py \
  --spec .github/governance/main-ruleset.json \
  --live "$runner_temp/live-main-ruleset.json" \
  --output "$runner_temp/materialized-main-ruleset.json"
```

Never send `.github/governance/main-ruleset.json` directly to the provider API while it contains `context_fingerprint`. The materializer must resolve every fingerprint uniquely from authenticated live state or fail closed.
