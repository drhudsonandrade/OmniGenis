"""Fixed four-descriptor entrypoint for the opt-in typed original-brand worker."""
from pathlib import Path
import sys


if __name__ == "__main__":
    if not sys.flags.isolated or not sys.dont_write_bytecode or len(sys.argv) != 7:
        raise SystemExit(64)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from reporting import _pdf_render_worker as base
    from reporting import _pdf_typed_brand_emission as emission
    from reporting import pdf_raster_render as inherited
    from reporting.pdf_typed_brand_render import require_policy

    if sys.argv[5] != emission.MODE:
        raise SystemExit(64)
    original_reader = inherited.read_raster_inputs

    def protected_reader(request_fd, resource_fd):
        # The base calls this only after descriptor, limit, lifetime and OS guards.
        try:
            require_policy()
        finally:
            # Failure responses must also identify the route selected by this child.
            inherited.PROFILE_ID = emission.PROFILE_ID
            inherited.MODE = emission.MODE
            inherited.profile_sha256 = emission.profile_sha256
        emission.install_in_isolated_render_worker()
        return original_reader(request_fd, resource_fd)

    base.RASTER_MODE = emission.MODE
    inherited.read_raster_inputs = protected_reader
    raise SystemExit(base.main(sys.argv))
