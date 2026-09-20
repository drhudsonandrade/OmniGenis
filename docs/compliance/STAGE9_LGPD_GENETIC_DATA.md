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

The requested processing purpose and, where available, case/input identity must match the privacy record.

## Runtime placement

- WGS: the privacy record is evaluated inside the existing consent/provenance gate before the first DNA read.
- SNP-array: `run_snp_array.py` evaluates the privacy record before calling the array QC/parser. The Nextflow array entrypoint requires the record and forwards it to that gate.

Synthetic CI canaries remain outside this personal-data path because they contain no real person or patient data.

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
