# OmniGenis Phase 2D Legacy Elimination and Seal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the last repository-controlled temporary legacy compatibility, replace broad historical bypasses with an exact immutable allowlist, and record a repository-complete Phase 2D checkpoint without falsely claiming the externally blocked runner-name seal.

**Architecture:** Keep the canonical identity contract authoritative. Retire the deprecated CodeRabbit bin-directory fallback, move the legacy ledger to Phase 2D, and make historical exemptions exact by path plus SHA-256. Record external runner continuity as verified while preserving the runner-name re-registration blocker until its Runtime/Resource Gate is actually executable.

**Tech Stack:** Python 3.12, Bash, unittest, Git, GitHub CLI.

**Spec:** `docs/superpowers/specs/2026-09-10-omnigenis-phase2-internal-identity-migration-design.md`

## Global Constraints

- No scientific or normative genomic behavior changes.
- English-first remains mandatory for new technical content.
- Historical/provenance bytes are not cosmetically rewritten.
- No broad historical-directory exemption may survive the final legacy scanner.
- No runner may be unregistered until recreation and rollback are verifiably available.
- No automatic merge.

---
### Task 1: Retire the deprecated CodeRabbit bin-directory fallback

**Files:**
- Modify: `tests/test_coderabbit_guardrails.py`
- Modify: `scripts/codex/setup-coderabbit.sh`
- Modify: `docs/PROJECT_IDENTITY_CONTRACT.md`

**Interfaces:**
- Consumes: canonical `OMNIGENIS_CODERABBIT_BIN_DIR` and default `$HOME/.local/bin` behavior.
- Produces: setup behavior that never reads the deprecated alias.

- [ ] **Step 1: Write failing behavior tests**

Change the fake-runner helper so legacy-only mode expects installation under `$HOME/.local/bin`, then replace legacy-acceptance/conflict tests with assertions that the legacy environment value cannot control or conflict with canonical selection. Add a structural assertion that `LEGACY_BIN_ENV` is absent from the setup script.

- [ ] **Step 2: Run RED**

Run: `PYTHONPATH=tests:. python3 -m unittest tests.test_coderabbit_guardrails -v`
Expected: FAIL because the current setup script still reads the legacy variable and emits its deprecation/conflict behavior.

- [ ] **Step 3: Implement the minimum removal**

Set `INSTALL_BIN_DIR="${OMNIGENIS_CODERABBIT_BIN_DIR:-$HOME/.local/bin}"` and delete the legacy-variable lookup, warning, dual-value branch, and conflict branch. Update the identity contract to state the fallback is removed in Phase 2D.

- [ ] **Step 4: Run GREEN**

Run the CodeRabbit guardrail suite, `bash -n scripts/codex/setup-coderabbit.sh`, identity guard, and repository validator.

- [ ] **Step 5: Commit**

Commit only Task 1 files with message `refactor: retire legacy CodeRabbit bin alias`.

---
### Task 2: Replace broad historical bypasses with an exact immutable allowlist

**Files:**
- Modify: `config/legacy_identity_ledger.json`
- Modify: `scripts/project_identity_guard.py`
- Modify: `tests/test_project_identity_guard.py`
- Create: `tests/test_phase2d_legacy_elimination.py`

**Interfaces:**
- Consumes: tracked-file inventory and existing ledger matchers.
- Produces: Phase 2D ledger schema with exact `historical_files[path] = {sha256, reason}` entries and zero migrate-location budgets.

- [ ] **Step 1: Write failing guard tests**

Require: a historical-prefix file containing a legacy token fails when not explicitly allowlisted; the same exact file passes only when its path and SHA-256 are allowlisted; byte drift after allowlisting fails closed; the real ledger is Phase 2D; every `disposition=migrate` entry has `locations == {}`; preserve-historical entries retain exact reviewed locations.

- [ ] **Step 2: Run RED**

Run: `PYTHONPATH=tests:. python3 -m unittest tests.test_project_identity_guard tests.test_phase2d_legacy_elimination -v`
Expected: FAIL because the current guard skips broad historical prefixes and the ledger is still Phase 2A.

- [ ] **Step 3: Implement exact historical verification**

Remove broad-prefix skipping from the scan. Validate an exact historical-file map, verify each allowlisted file exists in the tracked index and matches its pinned SHA-256, skip only a verified exact file, and report `historical_drift` on mismatch. Reject the old broad `historical_prefixes` contract in the Phase 2D ledger.

- [ ] **Step 4: Regenerate the reviewed historical allowlist**

Pin only the 21 currently tracked historical files that contain legacy terminology, with their current SHA-256 and a non-empty provenance reason. Set the ledger phase to `2D`, remove the deprecated alias location budget, and preserve exact historical runtime-archive / PR-reference locations.

- [ ] **Step 5: Run GREEN and mutation checks**

Run both test modules, `project_identity_guard.py --check`, `--inventory`, and mutations for an unallowlisted historical file plus hash drift.

- [ ] **Step 6: Commit**

Commit Task 2 files with message `refactor: seal exact legacy history allowlist`.

---
### Task 3: Record the Phase 2D repository checkpoint and external blocker

**Files:**
- Create: `docs/superpowers/evidence/2026-09-13-omnigenis-phase2d-legacy-elimination.json`
- Create: `tests/test_phase2d_evidence_contract.py`

**Interfaces:**
- Consumes: merged PR #71 SHA, protected-main canary jobs, current runner IDs/labels/status, Phase 2D ledger inventory, and Runtime/Resource Gate result.
- Produces: reproducible evidence that repository legacy elimination is complete while global Phase 2 completion remains blocked on runner-name re-registration.

- [ ] **Step 1: Write the failing evidence contract**

Require exact main merge SHA, repository status `VERIFIED`, active migrate-location count zero, exact historical allowlist count, two successful canonical-pool canary jobs, canonical runner labels with no retired labels, `runner_name_reregistration.status == "BLOCKED_RUNTIME_RESOURCE_GATE"`, and `phase2_global_seal == "BLOCKED"`.

- [ ] **Step 2: Run RED**

Run: `PYTHONPATH=tests:. python3 -m unittest tests.test_phase2d_evidence_contract -v`
Expected: FAIL because the evidence file does not yet exist.

- [ ] **Step 3: Capture fresh non-secret external facts**

Query GitHub by repository ID. Record only run/job IDs, merge SHA, runner IDs, canonical labels/status, ruleset semantic evidence, and the explicit inability to prove container recreation/rollback from the authorized executor. Never record credentials, registration tokens, cookies, patient/genomic data, or raw secret-bearing environment values.

- [ ] **Step 4: Write evidence and run GREEN**

Create the evidence JSON from executed facts only, then run the evidence contract, identity guard, zero-identity guard, repository validator, supply-chain verifier, language gates, shell syntax, and `git diff --check`.

- [ ] **Step 5: Run complete regression**

Run the complete root unittest suite in the pinned environment. Record exact count/result and output digest.

- [ ] **Step 6: Commit evidence child**

Keep the evidence commit separate if the repository's evidence-binding tests require an implementation/evidence topology; otherwise commit the evidence with its contract after exact validation.

---

### Task 4: Draft PR and governed handoff

- [ ] Open a draft PR from the Phase 2D branch.
- [ ] Keep the PR in draft until the exact remote HEAD equals the locally validated HEAD.
- [ ] Run/await DeepSource, CodeRabbit, Semgrep, GitGuardian, Snyk, applicable Actions, and all required checks.
- [ ] Resolve only verified current findings; any implementation change requires fresh local validation and refreshed evidence.
- [ ] Mark Ready only with zero unresolved threads and a clean merge state.
- [ ] Stop for explicit human merge. Do not auto-merge.

## Completion semantics

The Phase 2D **repository checkpoint** may be complete when all repository-controlled legacy compatibility is eliminated and the exact historical allowlist is sealed. The overall **Phase 2 global seal must remain BLOCKED** until runner names are safely re-registered or a future approved design changes that requirement.
