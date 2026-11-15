"""Strictly optional child. Reuse the unchanged native render limits and sandbox."""
from __future__ import annotations

from pathlib import Path
import sys

if __name__ == "__main__":
    if not sys.flags.isolated or not sys.dont_write_bytecode:
        raise SystemExit(64)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from reporting import _pdf_render_worker as base
    from reporting import _pdf_pagination_core as pagination

    base.NATIVE_MODE = pagination.MODE
    base.NATIVE_PROFILE_ID = pagination.PROFILE_ID
    base.native_render_profile = pagination.renderer_profile

    original_render = base._render

    def render(html, digest, **options):
        try:
            pagination.require_folio_html(html)
        except Exception:
            return None, "pdf_render_failed"
        return original_render(html, digest, **options)

    base._render = render
    pagination.require_source_manifest()
    pagination.install_in_isolated_render_worker()
    raise SystemExit(base.main(sys.argv))
