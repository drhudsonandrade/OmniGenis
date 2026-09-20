# Stage 9 — LGPD and Genetic-Data Privacy Boundary

Stage 9 adds a fail-closed privacy control before OmniGenis processes non-synthetic genetic data. It does not declare that an operation is legally compliant. It proves only that the project-specific privacy prerequisites recorded by the operator were present and internally coherent when the gate ran.

## Classification

For the Brazilian jurisdiction profile, genetic data linked to a natural person are treated as sensitive personal data by default. OmniGenis does not infer anonymization merely because a name was removed. A distinct verified anonymization determination is required before the alternative anonymized class can be used.

## Consent is not a legal-basis inference

The existing project consent controls remain in force. Stage 9 deliberately does not translate the presence of consent into an LGPD legal basis. The privacy record must carry a separately verified legal-basis reference and evidence reference. The gate records `legal_basis_inferred=false`.

This separation prevents an engineering boolean from making a legal conclusion.

## Required controls

A processing record must explicitly verify:

- purpose limitation;
- data minimization;
- access control;
- retention policy;
- incident-response capability;
- a data-subject-rights channel;
- sharing or international-transfer review;
- risk assessment;
- controller reference;
- legal-basis reference and evidence.

`VERIFIED_ANONYMIZED_GENETIC_DATA` additionally requires an `anonymization_determination` block with `status=VERIFICADO` and a non-empty `evidence_ref`; removing identifiers alone never upgrades the class.

The requested processing purpose and case/input identity must match the privacy record. For WGS, the canonical sample manifest supplies `case_id` and `provenance.input_sha256`; either field missing or mismatched blocks the pre-DNA gate.

## Runtime placement

- WGS: the privacy record is evaluated inside the existing consent/provenance gate before the first DNA read.
- SNP-array: `run_snp_array.py` evaluates the privacy record before calling the array QC/parser. The Nextflow array entrypoint requires the record and forwards it to that gate. A sanitized authorization reference is written into `array-qc.json` and propagated into the curation manifest; it contains the privacy-record SHA-256, processing-context ID, data class, purpose, case ID, input SHA-256 and gate decision, but not the subject reference or other unnecessary personal data. The Audit Plane remains `PENDING` until final audit, while retaining this authorization reference.

Synthetic CI canaries remain outside the personal-data legal-basis path because they contain no real person or patient data. They must still present a Stage 9 synthetic privacy record bound to the case ID and input SHA-256, with `data_class=SYNTHETIC_NON_PERSONAL_GENETIC_FIXTURE`, `subject_reference=NO_NATURAL_PERSON`, `generated_for=CI_CANARY`, and an explicit `contains_personal_data=false` attestation. The synthetic path rejects any `legal_basis` field rather than inventing an LGPD basis for non-personal test data.

## What a PASS means

`VERIFICADO` means the Stage 9 engineering prerequisites were present. It does **not** mean:

- that OmniGenis or an operator has received a legal opinion;
- that a specific LGPD legal basis is legally correct for every use;
- that anonymization is irreversible;
- that every organizational security measure is effective;
- that a regulator has approved the processing.

## Official sources checked on 2026-09-20

- ANPD, FAQ on personal and sensitive personal data: genetic data linked to a natural person are included in the sensitive-data category.
- Lei nº 13.709/2018 (LGPD), official consolidated statutory text on Planalto.
- ANPD Resolution CD/ANPD nº 15/2024 and the authority's incident-communication material.

The source URLs and retrieval date are recorded in `config/genetic_data_privacy_policy.json`. Because law and regulatory guidance can change, those references are evidence for the policy version, not a permanent substitute for current legal review.
