# Stage 6 — Scientific Data Licensing and Provenance Registry

## Purpose

Stage 6 inventories the scientific datasets, databases, ontologies, reference resources, and curated knowledge sources used or referenced by OmniGenis. It is a provenance and rights-disclosure stage, not a blanket use authorization and not a legal opinion.

The canonical registry is `config/data_source_registry.yaml`. It is serialized as JSON-compatible YAML 1.2 so the validation path remains deterministic and uses only the Python standard library.

## Status semantics

- `DOCUMENTED_OPEN` — authoritative terms identify an open/public-domain grant for the relevant data.
- `DOCUMENTED_WITH_OBLIGATIONS` — use is documented but attribution, share-alike, professional-review, or third-party-rights obligations remain.
- `RECORD_LEVEL_TERMS_REQUIRED` — rights vary by record and must never be flattened to the resource level. PGS Catalog scores use this state.
- `RESTRICTED` — authoritative terms prohibit or condition material uses. Genomics England PanelApp is restricted without a separate agreement.
- `REVIEW_REQUIRED` — provenance is recorded, but the exact authoritative permission needed for broader use is not sufficiently verified.

A Stage 6 validator `PASS` means the rights/provenance state is explicitly and consistently recorded. It does **not** mean `LICENSE-CLEAN`, commercial clearance, clinical clearance, or that a `REVIEW_REQUIRED`/`RESTRICTED` resource may be used for a new purpose. Purpose-bound authorization is Stage 7.

## PGS Catalog boundary

PGS licensing is score-specific. The retained metadata registry contains 6,972 scores and preserves a non-empty `license` field for each score. Forty current score records are explicitly flagged restrictive in the retained artifact. No PGS scoring weights are committed to the repository.

The Stage 6 validator fails if a score loses its license field, if the restrictive count drifts from the retained metadata, if the resource is downgraded from `RECORD_LEVEL_TERMS_REQUIRED`, or if a PGS scoring-weight file is committed.

## Restricted and unresolved sources

Genomics England PanelApp Terms of Use prohibit commercial purposes, including commercial research, diagnostic use, medical decision-making, and healthcare-service use without a separate agreement. The registry therefore records it as `RESTRICTED`; no inherited use may be interpreted as permission.

PanelApp Australia, gnomAD, and AADR remain `REVIEW_REQUIRED` where Stage 6 did not independently verify authoritative terms sufficiently to grant a new purpose. The AADR entry preserves the existing repository record that identifies CC0 1.0, while explicitly refusing to promote that record to `DOCUMENTED_OPEN` without independent authoritative verification.

## Ontologies

HPO and Mondo identifiers appear through scientific source records, but OmniGenis currently commits no complete HPO or Mondo ontology artifact. Their registry entries are reference-only and deliberately do not pretend that the latest upstream ontology release generated every inherited identifier.

HPO's published conditions require acknowledgement, public display of file date/version, and prohibit alteration of HPO content/logical relationships outside the project contribution process. Mondo is published under CC BY 4.0.

## Runtime reference resources

`manifests/GRCh38.sources.tsv` is fully covered by registry entries for GENCODE, the Broad GATK resource bundle, and the Boyle-Lab/ENCODE blacklist. Locally generated tabix indices are explicitly marked derived exemptions. Runtime resources that are not committed use an explicit `RUNTIME_RESOURCE_NOT_COMMITTED` digest sentinel instead of a fabricated hash.

## Clinical/research boundary

Terms that permit data access do not establish clinical validity or authorize medical decision-making. ClinVar, ClinGen, GenCC, CPIC/ClinPGx, PanelApp, GWAS, PGS and population resources retain their scientific limitations independently of copyright/licensing status. Stage 10 handles the clinical/research/regulatory boundary.

## Official terms consulted in Stage 6

The registry records the authoritative URLs consulted on 2026-09-19, including NCBI data policies, EMBL-EBI Terms of Use, ClinGen Terms of Use, GenCC Terms, ClinPGx API terms, PGS Catalog scoring-file license semantics, HPO license conditions, Mondo licensing, Genomics England PanelApp Terms of Use, IGSR reuse guidance, GENCODE access statements, and Broad GATK resource-bundle documentation.

## Invariants

1. Every expected scientific source has a unique registry identity.
2. Every entry has provider, version/provenance, retrieval state, source URL, terms URL, license/terms basis, use/redistribution fields, attribution, access method, digest state, limitations, and evidence.
3. `UNKNOWN`, `NOASSERTION`, or an empty license is never accepted.
4. `NOT_VERIFIED...` terms may exist only as `REVIEW_REQUIRED`.
5. Local artifacts must match their recorded SHA-256.
6. All evidence adapters must map to a registry source.
7. Every non-derived GRCh38 manifest artifact must be covered.
8. Restricted resources must state the restriction explicitly.
9. PGS rights remain score-specific.
10. No Stage 6 artifact may claim global legal, commercial, or clinical clearance.
