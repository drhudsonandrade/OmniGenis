"""Same-sealed-request retries through the existing synthetic release store.

The complete admitted binding determines one slot, except for generated PDF/QA
outputs and the artifact identifier itself. All captured request metadata and
lineage remain significant, including request/requester IDs and control records.
A different request ID is a different request; cross-request deduplication and
event registration are not implemented here.

Binding uses an unpublished in-memory identifier before deriving the final slot.
Only the existing release APIs perform storage or check live synthetic grants.
Their default disablement, profile/renderer restrictions, nonclaims, partial-
attempt refusal and unknown-outcome errors are preserved. No rendering, analysis,
new paper format, automatic cleanup or durable consent restoration occurs here.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256

from reporting import synthetic_release as release
from reporting.requests import BoundReportRequest

IDENTITY_SCHEMA = "synthetic-sealed-request-slot/v1"
ARTIFACT_PREFIX = "SYNTHETIC-REQUEST-"


def _artifact_id(binding):
    """Keep every input field; generated outputs must conflict in the same slot."""
    semantic = deepcopy(binding)
    for key in ("artifact_id", "sha256", "size_bytes"):
        del semantic["artifact"][key]
    for key in ("qa_record_json", "qa_sha256"):
        del semantic[key]
    vector = {"identity_schema": IDENTITY_SCHEMA, "semantic_binding": semantic}
    return ARTIFACT_PREFIX + sha256(release._canonical(vector)).hexdigest()


def _unpublished_id(bound_request, parent_manifest_bytes):
    """Choose a bounded temporary name distinct from the admitted parent roles."""
    release._require(type(bound_request) is BoundReportRequest)
    BoundReportRequest.__post_init__(bound_request)
    snapshot = bound_request.request.to_dict()
    source = snapshot["source_binding"]
    parents = {source[key]["artifact_id"]
               for key in ("input_artifact", "canonical_result", "evidence_snapshot")}
    parents.add(snapshot["locale_pack"]["artifact_id"])
    if parent_manifest_bytes is not None:
        parent = release._loads(parent_manifest_bytes, 131072)
        parents.add(parent["binding"]["artifact"]["artifact_id"])
    for index in range(len(parents) + 1):
        candidate = "SYNTHETIC-REQUEST-BIND-" + str(index)
        if candidate not in parents:
            return candidate
    raise release.SyntheticReleaseError("synthetic_request_binding_refused")


def _validated_request(request):
    try:
        release._require(type(request) is release.BoundSyntheticRelease)
        binding = request.to_dict()
        release._require(binding["artifact"]["artifact_id"] == _artifact_id(binding))
        return binding
    except Exception:
        pass
    raise release.SyntheticReleaseError("synthetic_request_refused") from None


def bind_synthetic_request(*, bound_request, pdf_bytes, qa_bytes, locale_pack_bytes,
                           report_pack_bytes, expectations, content_revision,
                           evidence_cutoff, delta="NEW", parent_manifest_bytes=None,
                           expected_parent_manifest_sha256=None):
    """Admit existing release inputs and bind their deterministic, unpublished slot.

    The returned existing BoundSyntheticRelease is not publication or a grant.
    A harness must independently authorize its exact derived artifact/binding.
    """
    try:
        prepared = release.bind_synthetic_release(
            bound_request=bound_request, pdf_bytes=pdf_bytes, qa_bytes=qa_bytes,
            locale_pack_bytes=locale_pack_bytes, report_pack_bytes=report_pack_bytes,
            expectations=expectations,
            artifact_id=_unpublished_id(bound_request, parent_manifest_bytes),
            content_revision=content_revision, evidence_cutoff=evidence_cutoff,
            delta=delta, parent_manifest_bytes=parent_manifest_bytes,
            expected_parent_manifest_sha256=expected_parent_manifest_sha256)
        binding = prepared.to_dict()
        binding["artifact"]["artifact_id"] = _artifact_id(binding)
        # The existing constructor rechecks the complete binding and parent.
        return replace(prepared, _binding_bytes=release._canonical(binding))
    except Exception:
        pass
    raise release.SyntheticReleaseError("synthetic_request_binding_refused") from None


def publish_synthetic_request(*, request, directory, authority, requester_id):
    """Publish/retry the exact derived slot with existing live-grant semantics.

    In-flight or interrupted attempts keep the existing refusal/unknown outcome.
    No automatic retry, replacement, deletion or repair is attempted.
    """
    _validated_request(request)
    return release.publish_synthetic_release(
        release=request, directory=directory, authority=authority,
        requester_id=requester_id)


def read_synthetic_request(*, request, directory, expected_manifest_sha256,
                           authority, requester_id):
    """Read the exact captured request and committed manifest under a live grant."""
    expected = _validated_request(request)
    pdf, manifest = release.read_synthetic_release(
        directory=directory, artifact_id=expected["artifact"]["artifact_id"],
        expected_manifest_sha256=expected_manifest_sha256,
        authority=authority, requester_id=requester_id)
    if manifest["binding"] != expected:
        raise release.SyntheticReleaseError("synthetic_request_readback_refused")
    return pdf, manifest
