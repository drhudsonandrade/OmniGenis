"""One-use child combining preloaded PNG resources with native footer tags.

All compatibility patches are made in this isolated subprocess; no inherited
renderer, V1 source, or default mode is changed on disk or in the parent.
"""
from __future__ import annotations

from pathlib import Path
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reporting import _pdf_render_worker as engine
from reporting import _pdf_pagination_core as pagination
from reporting import pdf_raster_render as raster
from reporting import pdf_original_brand_pagination_composite as composite


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    if (sys.platform != "linux" or not sys.flags.isolated or not sys.dont_write_bytecode
            or type(argv) is not list or len(argv) != 7 or argv[5] != composite.MODE):
        return 64
    try:
        composite._admit()
        # Preserve both original pinned contracts before child-only overrides.
        original_raster_profile_sha = raster.profile_sha256
        original_raster_profile_id = raster.PROFILE_ID
        original_raster_profile_sha()
        pagination.require_source_manifest()
        composite_sha = composite.composite_profile_sha256()
        original_render = engine._render

        def rendered(html: str, digest: str, *, max_pdf_bytes: int = engine.PDF_BYTES,
                     resources: object = None):
            pagination.require_folio_html(html)
            if resources is None:
                raise ValueError("one preloaded original PNG required")
            return original_render(html, digest, max_pdf_bytes=max_pdf_bytes,
                                   resources=resources)

        def pinned_composite_profile() -> str:
            # The original fixed digest includes its original profile ID.
            # Check it without letting a child-only ID override mask drift.
            modified_id = raster.PROFILE_ID
            try:
                raster.PROFILE_ID = original_raster_profile_id
                original_raster_profile_sha()
            finally:
                raster.PROFILE_ID = modified_id
            return composite_sha

        engine._render = rendered
        engine.RASTER_MODE = composite.MODE
        raster.PROFILE_ID = composite.PROFILE_ID
        raster.profile_sha256 = pinned_composite_profile
        pagination.install_in_isolated_render_worker()
    except Exception:
        return 70
    return engine.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
