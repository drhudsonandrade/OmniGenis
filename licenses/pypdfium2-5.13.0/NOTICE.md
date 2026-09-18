# Upstream Attribution Notice — pypdfium2 5.13.0 / PDFium

This notice records attribution for the exact Linux x86_64 `pypdfium2==5.13.0`
wheel accepted by OmniGenis Stage 2/3. It does not transfer ownership of any
third-party material to OmniGenis and does not replace the controlling upstream
license texts.

The verbatim upstream license/notice payloads from the audited wheel are preserved
as three direct license files plus deterministic `upstream/BUILD_LICENSES.tar.gz`.
Their logical SHA-256 values are recorded in `UPSTREAM_LICENSES.sha256`; the
archive itself is pinned by `UPSTREAM_BUILD_LICENSES.sha256`. If this summary and a verbatim upstream notice ever
differ, the applicable upstream license/notice is the controlling notice.

## Direct runtime components

### pypdfium2 5.13.0

- Upstream package author metadata: `pypdfium2-team`.
- The inspected Python wrapper files carry
  `SPDX-FileCopyrightText: 2026 geisserml <geisserml@gmail.com>`.
- Wrapper-code license: `Apache-2.0 OR BSD-3-Clause`.
- Upstream documentation/examples: `CC-BY-4.0` where identified by the
  upstream distribution.
- Source/home page identified by the wheel metadata:
  `https://github.com/pypdfium2-team/pypdfium2`.
- Controlling license texts:
  `upstream/LICENSES/Apache-2.0.txt`,
  `upstream/LICENSES/BSD-3-Clause.txt`, and
  `upstream/LICENSES/CC-BY-4.0.txt`.

### PDFium

- Copyright 2014 The PDFium Authors.
- BSD-style redistribution terms are preserved verbatim in
  `upstream/data/linux_x64/BUILD_LICENSES/pdfium.txt`.
- The license prohibits using Google Inc. or contributor names to endorse or
  promote derived products without specific prior written permission.

### PDFium binary distribution

- Copyright 2014-2025 Benoit Blanchon.
- The bundled permission notice is preserved verbatim in
  `upstream/data/linux_x64/BUILD_LICENSES/pdfium-binaries.txt`.
- That notice requires preservation of its copyright and permission notice in
  copies or substantial portions of the software.

## Bundled dependency notices in the audited wheel

The audited binary wheel also ships third-party dependency notices. OmniGenis
preserves the complete upstream files verbatim rather than rewriting their
legal terms. Named copyright holders found in those files include:

- Anti-Grain Geometry 2.3 — Copyright (C) 2002-2005 Maxim Shemanarev.
- fast_float — Copyright (c) 2021 The fast_float authors.
- FreeType Project — Copyright 1996-2002, 2006 by David Turner, Robert Wilhelm,
  and Werner Lemberg.
- ICU/Unicode data and software — Copyright © 2016-2025 Unicode, Inc.; the
  bundled notice also preserves earlier IBM Corporation and other notices.
- Little Color Management System — Copyright (c) 1998-2026 Marti Maria Saguer.
- IJG/libjpeg material — Copyright (C) 1991-2020 Thomas G. Lane and Guido
  Vollbeding.
- libjpeg-turbo material — Copyright (C) 2009-2024 D. R. Commander; Copyright
  (C) 2015 Viktor Szathmáry, in addition to the IJG and other terms identified
  by the upstream notice.
- OpenJPEG material — copyright notices for Herve Drolon / FreeImage Team,
  Centre National d'Etudes Spatiales (CNES), and CS Systemes d'Information.
- PNG Reference Library — notices for The PNG Reference Library Authors,
  Cosmin Truta, Glenn Randers-Pehrson, Andreas Dilger, and Guy Eric Schalnat /
  Group 42, Inc.
- libtiff — Copyright (c) 1988-1997 Sam Leffler and Copyright (c) 1991-1997
  Silicon Graphics, Inc.
- simdutf — Copyright 2021 The simdutf authors.
- zlib — Copyright (C) 1995-2026 Jean-loup Gailly and Mark Adler.

Some bundled license files, including generic Apache-2.0 texts used for
components such as Abseil/LLVM-related material, do not themselves enumerate a
single copyright-holder line. OmniGenis therefore does not invent one; the
verbatim upstream file is preserved as the authoritative notice for that
artifact.

## Redistribution rule

When an OmniGenis distribution includes the audited pypdfium2/PDFium binary
artifact, the applicable upstream license and attribution notices must remain
available with that distribution in the form required by their respective
licenses. No OmniGenis copyright or proprietary notice may replace, obscure, or
relicense these third-party notices.

This Stage 2/3 attribution snapshot is deliberately narrower than the complete
repository/container SBOM and third-party registry planned for Stage 4. It is
technical compliance evidence, not a legal opinion or a claim that every
transitive dependency in OmniGenis has already been reconciled.
