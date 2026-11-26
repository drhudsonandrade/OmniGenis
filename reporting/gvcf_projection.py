"""Project a completed, source-bound gVCF run into a technical preview.

The boundary reconstructs in-memory bindings and validates the fixed canonical
export schema. It does not rerun the native executor, attest execution origin,
resolve ownership or consent, or authorize a clinical release. Callers retain
the actual runner result and obtain report identity through upstream resolution.
Raw gVCF, header text and private execution paths never enter this mapping.
"""
from __future__ import annotations

from copy import deepcopy

from omnigenis.gvcf_preview_run import (
    GvcfPreviewBindingError,
    GvcfPreviewRunResult,
    rebind_gvcf_preview_run,
    validate_gvcf_preview_export,
)
from reporting.bindings import ReportSourceIdentity


class GvcfReportProjectionError(ValueError):
    """A fixed diagnostic without source payloads, identities or paths."""


def project_gvcf_interpretation(
    *, run: GvcfPreviewRunResult, identity: ReportSourceIdentity,
) -> dict[str, object]:
    """Return the five declared semantic inputs after strict source binding."""
    try:
        if type(run) is not GvcfPreviewRunResult or not run.passed:
            raise GvcfReportProjectionError("completed_gvcf_preview_run_required")
        if type(identity) is not ReportSourceIdentity:
            raise GvcfReportProjectionError("report_source_identity_required")
        # Revalidate the nested identity contract without inferring its origin.
        checked_identity = ReportSourceIdentity(**identity.to_dict())
        rebind_gvcf_preview_run(run)
        exported = run.to_dict()
        validate_gvcf_preview_export(exported)
        if checked_identity.sample_id != exported["canonical_model"]["sample_id"]:
            raise GvcfReportProjectionError("report_sample_identity_mismatch")
        limitations = list(exported["limitations"])
        limitations.extend((
            "No clinical or variant-interpretation analysis classes were evaluated.",
            "Technical preview only; clinical assessment and release authorization "
            "are not established by this projection.",
        ))
        return {
            "identity": checked_identity.to_dict(),
            "items": deepcopy(exported["interpretation"]["items"]),
            "conflicts": {
                "assessment_status": "NOT_ASSESSED",
                "availability": "NOT_MEASURED",
                "reason": "Clinical and cross-source conflict analysis was not performed.",
            },
            "limitations": list(dict.fromkeys(limitations)),
            "provenance": [deepcopy(exported)],
        }
    except GvcfReportProjectionError:
        raise
    except (GvcfPreviewBindingError, AttributeError, KeyError, TypeError, ValueError):
        raise GvcfReportProjectionError("gvcf_run_binding_invalid") from None
