# pypdfium2 5.13.0 Runtime Evidence

Stage 2 replaces the active PyMuPDF/MuPDF coordinate-compiler dependency with `pypdfium2==5.13.0` backed by PDFium.

Verified Linux x86_64 wheel:

- artifact: `pypdfium2-5.13.0-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl`
- SHA-256: `81df25c1ab4c13ff773102d3cbea1967511d079123b067fc077bd0c4d57d91d8`
- pypdfium2 wrapper-code licensing: Apache-2.0 OR BSD-3-Clause
- pypdfium2 documentation/example licensing: CC-BY-4.0 where identified by the upstream distribution
- PDFium licensing: BSD-style
- installed wheel contains the pypdfium2 Apache-2.0 and BSD-3-Clause texts and 16 bundled PDFium build-license files for the inspected Linux artifact
- the three primary license texts are stored directly under `upstream/LICENSES/`; the 16 PDFium build-license files are preserved byte-for-byte inside deterministic `upstream/BUILD_LICENSES.tar.gz`; all 19 logical notices are integrity-pinned by `UPSTREAM_LICENSES.sha256`, and the archive itself is pinned by `UPSTREAM_BUILD_LICENSES.sha256`
- the wheel `METADATA` that records upstream author/license metadata is preserved as `upstream/METADATA.gz` and integrity-pinned by `UPSTREAM_METADATA.sha256`
- human-readable attribution for the audited PDF boundary is recorded in `NOTICE.md`; verbatim upstream files remain controlling

The binary distribution's bundled license files are part of the redistribution evidence and must remain present in installed/distributed runtime artifacts. Each bundled component remains governed by its own terms. This record is a Stage 2 technical provenance record; it is not a legal opinion and does not replace the complete transitive inventory and SBOM planned for a later compliance stage.

The exact wheel, platform, and bundled notices must be reverified if the package version, platform tag, or build source changes.

Stage 2 installation is fail-closed: `reporting/requirements.txt` permits only the SHA-256 above. Other pypdfium2 5.13.0 wheels and the source distribution require a new artifact/license review before their hashes may enter the lock.
