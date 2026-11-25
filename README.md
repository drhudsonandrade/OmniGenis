# OmniGenis

[Canonical repository](https://github.com/drhudsonandrade/OmniGenis)

OmniGenis provides versioned Python components and contracts for genomic input
validation, evidence provenance, and report construction.

## Components

- [Capability manifests](capabilities/) declare the supported input domain,
  required resources, output contracts, and limitations of each component.
- [Scientific components](omnigenis/capabilities/) cover single-sample germline
  VCF intake, QC observations, reference identity, small-variant normalization,
  canonical genomic models, gene identity, evidence snapshots, and interpretation.
- [Report components](reporting/) compile report packs, build view models and
  presentation structures, and validate candidate report artifacts.
- [Schemas](schemas/) define versioned data contracts. [Tests](tests/) exercise
  component behavior and synthetic fixtures.

## Execution boundaries

Supported behavior is defined by each capability manifest and its pinned
resource profile. The presence of individual components does not establish a
qualified default pipeline or a validated clinical workflow.

Synthetic fixtures and private qualification profiles do not establish
production release readiness. Report artifacts retain the intended use,
limitations, provenance, and validation state of their source results.

## License

See [LICENSE](LICENSE) and [third-party notices](THIRD-PARTY-NOTICES.txt).
