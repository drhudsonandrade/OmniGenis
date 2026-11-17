"""Fail-closed runtime activation for the three admitted RPT-11 adapters.

The checked-in, bounded, offline registry is the only profile control source.
No remote standard, patient data, executor, scientific profile or service is
activated by this guard. Missing or contradictory registry state blocks output.
"""
from __future__ import annotations

import json
from pathlib import Path

_EXPECTED = {
    # module, pinned standard version, base version, admitted third-party license
    "fhir-genomics-reporting-r4": (
        "interoperability.fhir", "3.0.0", "FHIR 4.0.1", "CC-BY-4.0"),
    "ga4gh-phenopackets-v2": (
        "interoperability.phenopackets", "2.0", None, "BSD-3-Clause"),
    "ga4gh-vrs-v2": (
        "interoperability.vrs", "2.0", None, "Apache-2.0"),
}
_REGISTRY = Path(__file__).with_name("profiles.v1.json")
_MAX_REGISTRY_BYTES = 8192


def _unique_pairs(pairs):
    """Reject even nested duplicate JSON keys rather than last-wins aliases."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate profile registry key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("invalid profile registry constant")


def profile_is_active(profile_id: str) -> bool:
    """Admit only an enabled known profile with a coherent offline registry.

    This is runtime *projection availability*, never consent, clinical release,
    implementation-guide validation, or scientific activation authorization.
    """
    if type(profile_id) is not str or profile_id not in _EXPECTED:
        return False
    try:
        raw = _REGISTRY.read_bytes()
        if not 0 < len(raw) <= _MAX_REGISTRY_BYTES:
            return False
        registry = json.loads(raw, object_pairs_hook=_unique_pairs,
                              parse_constant=_reject_constant)
        if (type(registry) is not dict or
                set(registry) != {"schema_version", "registry_id", "profiles"} or
                registry["schema_version"] != "1.0.0" or
                registry["registry_id"] != "omnigenis-rpt11-interoperability-profiles-v1"):
            return False
        profiles = registry["profiles"]
        if type(profiles) is not list or len(profiles) != len(_EXPECTED):
            return False
        seen = set()
        selected = False
        for profile in profiles:
            if type(profile) is not dict:
                return False
            name = profile.get("profile_id")
            if type(name) is not str or name not in _EXPECTED or name in seen:
                return False
            seen.add(name)
            expected_module, expected_version, expected_base, expected_license = _EXPECTED[name]
            if (profile.get("schema_version") != "1.0.0" or
                    profile.get("adapter_module") != expected_module or
                    profile.get("standard_version") != expected_version or
                    profile.get("base_version") != expected_base or
                    profile.get("license") != expected_license or
                    profile.get("scientific_semantics") != "PROJECT_EXISTING_CANONICAL_E1A_ONLY" or
                    profile.get("conformance_scope") != "BOUNDED_PROFILE_ONLY" or
                    profile.get("network_access") != "FORBIDDEN_DURING_PROJECTION" or
                    profile.get("clinical_authorization") != "NOT_ESTABLISHED" or
                    profile.get("unsupported_behavior") != "FAIL_CLOSED"):
                return False
            state = profile.get("activation_state")
            enabled = profile.get("adapter_enabled")
            if not ((state == "BOUNDED_ADAPTER_ENABLED" and enabled is True) or
                    (state in {"DISABLED", "PROFILE_DECLARED_ADAPTER_PENDING"} and
                     enabled is False)):
                return False
            if name == profile_id:
                selected = enabled is True
        return seen == set(_EXPECTED) and selected
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        return False
