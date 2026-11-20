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
    original_failure = base._failure
    validated_profile = None

    def bounded_failure(fd, error, limits, *, native_mode=False, raster_mode=False):
        # Before admission, reuse the existing generic refusal without a profile claim.
        if validated_profile is None:
            return original_failure(fd, error, limits)
        # A typed refusal carries only the digest already validated in this child.
        if error not in base._NATIVE_ERRORS:
            error = "pdf_render_failed"
        base._write_result(fd, {"protocol_version": 3, "profile_id": emission.PROFILE_ID,
            "profile_sha256": validated_profile, "status": "FAIL", "error": error,
            "limits": limits})
        return 0

    def admitted_profile_sha256():
        # Re-audit before reusing the profile identity that admitted this request.
        emission.require(emission.profile_sha256() == validated_profile)
        return validated_profile

    def protected_reader(request_fd, resource_fd):
        # The base calls this only after descriptor, limit, lifetime and OS guards.
        global validated_profile
        validated_profile = require_policy()
        inherited.PROFILE_ID = emission.PROFILE_ID
        inherited.MODE = emission.MODE
        inherited.profile_sha256 = admitted_profile_sha256
        emission.install_in_isolated_render_worker()
        return original_reader(request_fd, resource_fd)

    base.RASTER_MODE = emission.MODE
    base._failure = bounded_failure
    inherited.read_raster_inputs = protected_reader
    raise SystemExit(base.main(sys.argv))
