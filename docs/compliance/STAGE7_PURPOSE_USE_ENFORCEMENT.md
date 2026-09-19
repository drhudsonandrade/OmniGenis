# Stage 7 — Purpose-of-Use Enforcement

## Scope

Stage 7 converts the rights/provenance states recorded by Stage 6 into an executable, fail-closed decision for a concrete downstream use. It does not reinterpret source licenses, give legal advice, or replace privacy, consent, clinical, regulatory, or professional review.

The canonical policy is `config/data_use_purpose_policy.json`. The materialized 19-resource × 7-purpose projection is `config/data_use_purpose_matrix.json`. Runtime decisions are produced by `scripts/data_use_purpose_gate.py`.

## Purpose vocabulary

- `RESEARCH` — non-commercial research. Any commercial context must also request `COMMERCIAL`.
- `COMMERCIAL` — any commercial use or context, including commercial research or a commercial product/service.
- `CLINICAL` — diagnosis, medical decisions, patient care, or other clinical use. Stage 10 remains the clinical/regulatory boundary.
- `REPORT_GENERATION` — externally delivered reports containing or derived from a resource. Add `CLINICAL` and/or `COMMERCIAL` when applicable.
- `MODEL_TRAINING` — training, fine-tuning, adaptation, distillation, or any update of model parameters. This is `REVIEW_REQUIRED` by default because Stage 6 did not establish model-training rights.
- `REDISTRIBUTION` — distribution of original or substantially reproduced resource content outside the controlled processing boundary.
- `DERIVED_DATA` — creation or distribution of data derived from a resource. Add every other applicable context.

Purposes compose. A request is not permitted to hide a commercial, clinical, reporting, redistribution, or training context inside a less restrictive label. The strictest decision across all supplied purposes wins.

## Decision vocabulary

From least to most restrictive:

1. `ALLOW`
2. `ALLOW_WITH_OBLIGATIONS`
3. `REVIEW_REQUIRED`
4. `RECORD_LEVEL_REVIEW_REQUIRED`
5. `DENY`

Only the first two are machine-authorized. `REVIEW_REQUIRED` and `RECORD_LEVEL_REVIEW_REQUIRED` are blocking states, not soft warnings. `DENY` is an explicit refusal under the currently registered rights state.

## Fail-closed rules

- A Stage 6 `REVIEW_REQUIRED` resource can never be machine-promoted to `ALLOW` or `ALLOW_WITH_OBLIGATIONS`.
- A Stage 6 `RECORD_LEVEL_TERMS_REQUIRED` resource remains record-level; PGS Catalog is never flattened to one catalog-wide permission.
- `MODEL_TRAINING` can never be machine-authorized by the current Stage 7 policy.
- Genomics England PanelApp commercial and clinical use are `DENY` under the current registered terms.
- Unmapped resource IDs, purposes, statuses, or rights tokens are errors.
- Missing PGS score IDs, unknown PGS score IDs, or missing score license terms fail closed.
- Attribution/citation obligations travel with an allowed decision.
- `REPORT_GENERATION` evaluates both derived-data and redistribution rights.
- The protected purpose→source-field mapping and severity order are validator-enforced and cannot be silently weakened.

## Matrix

The Stage 7 matrix is deterministic and generated only from the merged Stage 6 registry plus the Stage 7 policy. It contains exactly 133 individual decisions: 19 resources × 7 purposes.

The matrix is a precomputed audit projection. Runtime requests with multiple purposes are evaluated dynamically; a multi-purpose request always resolves to its strictest component decision.

## PGS Catalog

PGS remains special. `--record-id PGSxxxxxx` resolves the retained score metadata and returns its exact license text and restrictive flag, but the result remains `RECORD_LEVEL_REVIEW_REQUIRED`. Stage 7 deliberately does not parse free-form score licenses into automatic legal permission.

## CLI examples

```bash
python3 scripts/data_use_purpose_gate.py \
  --resource clingen-gene-disease-validity \
  --purpose RESEARCH

python3 scripts/data_use_purpose_gate.py \
  --resource panelapp-genomics-england \
  --purpose RESEARCH \
  --purpose COMMERCIAL

python3 scripts/data_use_purpose_gate.py \
  --resource ebi-pgs-catalog \
  --purpose RESEARCH \
  --record-id PGS000001
```

Exit status is 0 only for `ALLOW`/`ALLOW_WITH_OBLIGATIONS`, 2 for review-required states, 3 for `DENY`, and 4 for malformed/unknown requests.

## Boundaries preserved for later stages

Stage 7 does not decide privacy/LGPD/consent (Stage 9), and it does not declare a resource clinically valid or regulatorily cleared (Stage 10). A permissive data-use decision can still be blocked by those later gates.
