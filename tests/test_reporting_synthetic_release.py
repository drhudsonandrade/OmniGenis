"""Synthetic transaction tests, not native PDF QA or real-user authorization.

The small PDF-shaped bytes and schema-valid QA records below are explicit unit
fixtures. No renderer, native inspector or current-final-candidate integration
runs here; actual same-artifact qualification is a separate dependency.
"""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
import multiprocessing
import os
from pathlib import Path
import stat
from threading import Barrier, Thread

import pytest

from reporting import synthetic_release as release
from reporting.bindings import (
    ReportArtifactBinding, ReportSourceBinding, ReportSourceIdentity, bind_report_sources,
)
from reporting.completeness import ReportCompletenessPlan
from reporting.requests import ReportRequestSnapshot, bind_report_request
from reporting.workflow import ConsentStatus, ReviewState

ROOT = Path(__file__).resolve().parents[1]
REQUESTER = "SYNTHETIC-REQUESTER"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return sha256(value).hexdigest()


def case(*, analysis_revision="1", namespace="SYNTHETIC-NAMESPACE",
         content_sha256=None, pdf_bytes=None, artifact_id="SYNTHETIC-REPORT"):
    """Build sealed synthetic inputs; the QA receipt is a unit fixture only."""
    content = b'{"synthetic":true,"value":0.12345678901234567890}'
    evidence = b"SYNTHETIC-EVIDENCE-SNAPSHOT"
    identity = ReportSourceIdentity(namespace, "SYNTHETIC-CASE", "SYNTHETIC-SUBJECT",
        "SYNTHETIC-SAMPLE", "SYNTHETIC-ANALYSIS", analysis_revision, None)
    source = ReportSourceBinding(identity,
        ReportArtifactBinding("SYNTHETIC-INPUT", "a" * 64),
        ReportArtifactBinding("SYNTHETIC-RESULT", digest(content)),
        ReportArtifactBinding("SYNTHETIC-EVIDENCE", digest(evidence)), "b" * 64)
    sources = bind_report_sources(expected_binding=source, resolved_binding=source,
        canonical_result_bytes=content, evidence_snapshot_bytes=evidence)
    plan = ReportCompletenessPlan(source.binding_sha256, ("SYNTHETIC-F1",),
                                  ("identity", "findings"), ("identity", "findings"))
    locale = (ROOT / "reporting/locales/en-US.v1.json").read_bytes()
    pack = b"SYNTHETIC-SEALED-REPORT-PACK"
    request = ReportRequestSnapshot(
        request_id="SYNTHETIC-REQUEST", requester_id=REQUESTER, source_binding=source,
        completeness_plan_sha256=plan.plan_sha256, report_contract_bundle_sha256=digest(pack),
        locale_pack=ReportArtifactBinding("SYNTHETIC-LOCALE", digest(locale)),
        policy_record=ReportArtifactBinding("SYNTHETIC-POLICY", "e" * 64),
        consent_status=ConsentStatus.VERIFIED,
        consent_record=ReportArtifactBinding("SYNTHETIC-CONSENT", "f" * 64),
        review_state=ReviewState.APPROVED,
        review_record=ReportArtifactBinding("SYNTHETIC-REVIEW", "0" * 64))
    bound = bind_report_request(request=request, resolved_request=request, sources=sources, plan=plan)
    pdf = pdf_bytes or b"%PDF-1.7\nSYNTHETIC TRANSACTION UNIT FIXTURE; NOT NATIVE-INSPECTED\n%%EOF\n"
    schema = json.loads((ROOT / "schemas/report-typed-original-brand-qa.v1.schema.json").read_bytes())
    qa = {}
    for key in schema["required"]:
        spec = schema["properties"][key]
        if "const" in spec:
            qa[key] = spec["const"]
        elif "$ref" in spec:
            qa[key] = digest(("SYNTHETIC-" + key).encode())
        elif spec.get("type") == "integer":
            qa[key] = max(spec.get("minimum", 1), 1)
        elif key == "stages":
            qa[key] = {k: v["const"] for k, v in spec["properties"].items()}
    qa.update(
        request_sha256=request.request_sha256, source_binding_sha256=source.binding_sha256,
        plan_sha256=plan.plan_sha256, profile_sha256=release.qa.profile_sha256(),
        pdf_sha256=digest(pdf), pdf_byte_count=len(pdf), page_count=2,
        raster_inspected_pages=2, raster_window_count=1, represented_findings=1, source_units=5,
        navigation_entries=1, pagination_pages=2,
        content_sha256=content_sha256 or digest(b"SYNTHETIC-PROTECTED-CONTENT"),
        coverage={"native_nodes": 32, "projected_nodes": 25, "accounted_brand_wrapper_nodes": 6,
                  "accounted_navigation_wrapper_nodes": 1, "native_mcids": 11,
                  "text_mcids": 10, "figure_count": 1, "pagination_blocks": 2, "operations": 631})
    raw = canonical(qa)
    # Validate the real receipt contract; this never runs the native worker.
    release.qa.TypedOriginalBrandQA(raw)
    expectations = {
        "release_profile_sha256": release.profile_sha256(), "pdf_sha256": digest(pdf),
        "qa_sha256": digest(raw), "qa_profile_sha256": qa["profile_sha256"],
        "renderer_profile_sha256": digest((ROOT / "reporting/pdf-typed-brand-render.v1.json").read_bytes()),
        "ir_sha256": qa["ir_sha256"], "source_expectation_sha256": qa["source_expectation_sha256"],
        "content_sha256": qa["content_sha256"]}
    return dict(bound_request=bound, pdf_bytes=pdf, qa_bytes=raw, locale_pack_bytes=locale,
                report_pack_bytes=pack, expectations=expectations, artifact_id=artifact_id,
                content_revision="SYNTHETIC-CONTENT-1", evidence_cutoff="2026-01-01T00:00:00Z")


@pytest.fixture
def packet(monkeypatch):
    monkeypatch.setenv(release.MODE_KEY, "qualification-only")
    monkeypatch.delenv(release.DISABLE_KEY, raising=False)
    return case()


def authority(bound, **changes):
    data = bound.to_dict()
    request = data["request_snapshot"]
    record = {
        "schema_version": "1.0.0", "record_type": "SYNTHETIC_TEST_AUTHORITY",
        "record_id": "SYNTHETIC-AUTHORITY-RECORD", "record_revision": 1,
        "scope": release.SCOPE, "state": "AUTHORIZED_FOR_SYNTHETIC_QUALIFICATION",
        "namespace_id": request["source_binding"]["identity"]["namespace_id"],
        "request_id": request["request_id"], "requester_id": request["requester_id"],
        "request_sha256": data["request_sha256"], "artifact_id": data["artifact"]["artifact_id"],
        "binding_sha256": bound.binding_sha256,
        "consent_record": request["consent_record"], "review_record": request["review_record"],
        "actions": ["publish", "read"],
    }
    record.update(changes)
    raw = canonical(record)
    return release.SyntheticAuthorityContext(
        authorization_record=raw, expected_record_sha256=digest(raw))


def publish(bound, context, directory):
    return release.publish_synthetic_release(
        release=bound, authority=context, directory=directory, requester_id=REQUESTER)


def read(result, context, directory, **changes):
    args = dict(directory=directory, artifact_id=result["artifact_id"],
                expected_manifest_sha256=result["manifest_sha256"],
                authority=context, requester_id=REQUESTER)
    args.update(changes)
    return release.read_synthetic_release(**args)


def inventory(directory):
    result = {}
    for path in directory.rglob("*"):
        if path.is_symlink():
            continue
        info = path.stat()
        value = path.read_bytes() if path.is_file() else None
        result[str(path.relative_to(directory))] = (
            value, info.st_mode, info.st_ino, info.st_size, info.st_mtime_ns)
    return result


def test_default_disabled_refuses_publication_and_access(packet, tmp_path, monkeypatch):
    bound = release.bind_synthetic_release(**packet)
    context = authority(bound)
    existing, fresh = tmp_path / "existing", tmp_path / "fresh"
    existing.mkdir(mode=0o700)
    fresh.mkdir(mode=0o700)
    result = publish(bound, context, existing)
    before = inventory(existing)
    monkeypatch.delenv(release.MODE_KEY, raising=False)
    monkeypatch.delenv(release.DISABLE_KEY, raising=False)
    profile = release.synthetic_release_profile()
    assert profile["mode"] == "DISABLED"
    assert profile["product_profile_qualified"] is False
    with pytest.raises(release.SyntheticReleaseError):
        read(result, context, existing)
    with pytest.raises(release.SyntheticReleaseError):
        publish(bound, context, fresh)
    assert inventory(existing) == before
    assert not list(fresh.iterdir())


@pytest.mark.parametrize("mode", ["production", "on", "qualification-only "])
def test_unknown_mode_refuses_without_storage(mode, monkeypatch):
    monkeypatch.setenv(release.MODE_KEY, mode)
    with pytest.raises(release.SyntheticReleaseError):
        release.synthetic_release_profile()


def test_one_profile_is_explicitly_unqualified_and_other_locales_staged(packet):
    profile = release.synthetic_release_profile()
    assert profile["supported_profile"]["locale_id"] == "en-US"
    assert profile["qualification_state"] == "PREPARATION_ONLY"
    assert profile["staged_locale_ids"] == ["pt-BR", "es-ES", "it-IT", "fr-FR"]
    assert profile["other_profiles"] == "DISABLED"
    assert all(profile[name] is False for name in release.NONCLAIMS)


def test_exact_publication_readback_and_retry_are_immutable(packet, tmp_path):
    bound = release.bind_synthetic_release(**packet)
    context = authority(bound)
    result = publish(bound, context, tmp_path)
    before = inventory(tmp_path)
    pdf, manifest = read(result, context, tmp_path)
    assert pdf == packet["pdf_bytes"]
    assert manifest == result["manifest"]
    assert manifest["publication_state"] == "FINAL_SYNTHETIC_TECHNICAL"
    assert all(manifest[name] is False for name in release.NONCLAIMS)
    assert manifest["binding"]["qa_record_json"].encode() == packet["qa_bytes"]
    assert publish(bound, context, tmp_path) == result
    assert inventory(tmp_path) == before
    assert bound._request.sources.canonical_result_bytes == b'{"synthetic":true,"value":0.12345678901234567890}'


@pytest.mark.parametrize("key", sorted(release.EXPECTATIONS))
def test_independent_binding_pins_cannot_be_substituted(packet, key):
    packet["expectations"] = dict(packet["expectations"], **{key: "1" * 64})
    with pytest.raises(release.SyntheticReleaseError):
        release.bind_synthetic_release(**packet)


@pytest.mark.parametrize("key", ["request_sha256", "source_binding_sha256", "plan_sha256"])
def test_foreign_qa_refuses_even_when_its_own_digest_is_updated(packet, key):
    data = json.loads(packet["qa_bytes"])
    data[key] = "1" * 64
    packet["qa_bytes"] = canonical(data)
    packet["expectations"]["qa_sha256"] = digest(packet["qa_bytes"])
    with pytest.raises(release.SyntheticReleaseError):
        release.bind_synthetic_release(**packet)


@pytest.mark.parametrize("key", ["locale_pack_bytes", "report_pack_bytes"])
def test_missing_or_changed_control_artifact_is_not_qualified(packet, key):
    packet[key] = b""
    with pytest.raises(release.SyntheticReleaseError):
        release.bind_synthetic_release(**packet)


def test_nonenglish_request_is_rejected_by_existing_snapshot(packet):
    with pytest.raises(ValueError):
        replace(packet["bound_request"].request, locale_id="pt-BR")


def test_missing_locale_keys_cannot_be_replaced_by_self_matching_hashes(packet):
    altered = b'{"schema_version":"1.0.0","locale_id":"en-US","labels":{}}'
    request = replace(packet["bound_request"].request,
                      locale_pack=ReportArtifactBinding("SYNTHETIC-LOCALE", digest(altered)))
    packet["bound_request"] = bind_report_request(
        request=request, resolved_request=request, sources=packet["bound_request"].sources,
        plan=packet["bound_request"].plan)
    data = json.loads(packet["qa_bytes"])
    data["request_sha256"] = request.request_sha256
    packet["qa_bytes"] = canonical(data)
    packet["expectations"]["qa_sha256"] = digest(packet["qa_bytes"])
    packet["locale_pack_bytes"] = altered
    with pytest.raises(release.SyntheticReleaseError):
        release.bind_synthetic_release(**packet)


@pytest.mark.parametrize("fake", [True, {"status": "PASS"}, {"release_ready": True}])
def test_bare_pass_or_boolean_is_never_authority(packet, tmp_path, fake):
    bound = release.bind_synthetic_release(**packet)
    with pytest.raises(release.SyntheticReleaseError):
        publish(bound, fake, tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("field,value", [
    ("requester_id", "SYNTHETIC-OTHER"),
    ("namespace_id", "SYNTHETIC-OTHER"),
    ("request_sha256", "1" * 64),
    ("binding_sha256", "1" * 64),
    ("artifact_id", "SYNTHETIC-OTHER"),
    ("actions", ["read"]),
])
def test_wrong_synthetic_grant_refuses_before_any_write(packet, tmp_path, field, value):
    bound = release.bind_synthetic_release(**packet)
    context = authority(bound, **{field: value})
    with pytest.raises(release.SyntheticReleaseError):
        publish(bound, context, tmp_path)
    assert not list(tmp_path.iterdir())


def test_authority_record_must_be_closed_canonical_and_pinned(packet):
    bound = release.bind_synthetic_release(**packet)
    context = authority(bound)
    for raw, pin in [(context._record, "1" * 64), (b'{"PASS":true}', digest(b'{"PASS":true}')),
                     (context._record + b" ", digest(context._record + b" "))]:
        with pytest.raises(release.SyntheticReleaseError):
            release.SyntheticAuthorityContext(authorization_record=raw, expected_record_sha256=pin)


def test_authority_record_identifier_rejects_trailing_newline(packet, tmp_path):
    bound = release.bind_synthetic_release(**packet)
    context = authority(bound, record_id="SYNTHETIC-RECORD")
    result = publish(bound, context, tmp_path)
    assert read(result, context, tmp_path)[1]["authorization_record"]["record_id"] == "SYNTHETIC-RECORD"
    with pytest.raises(release.SyntheticReleaseError):
        authority(bound, record_id="SYNTHETIC-RECORD\n")


def test_direct_constructor_invalid_binding_returns_content_free_refusal(packet):
    bound = release.bind_synthetic_release(**packet)
    with pytest.raises(release.SyntheticReleaseError, match="^synthetic_release_binding_refused$"):
        replace(bound, _binding_bytes=b"{}")


@pytest.mark.parametrize("operation", ["publish", "read"])
def test_requester_subclass_refused_before_any_callback(packet, tmp_path, operation):
    bound = release.bind_synthetic_release(**packet)
    context = authority(bound)
    calls = []

    class RevokingRequester(str):
        def __eq__(self, other):
            calls.append(True)
            context.revoke()
            return str.__eq__(self, other)

    requester = RevokingRequester(REQUESTER)
    result = publish(bound, context, tmp_path) if operation == "read" else None
    before = inventory(tmp_path)
    with pytest.raises(release.SyntheticReleaseError):
        if operation == "read":
            read(result, context, tmp_path, requester_id=requester)
        else:
            release.publish_synthetic_release(release=bound, authority=context,
                directory=tmp_path, requester_id=requester)
    assert calls == []
    assert context._revoked is False
    assert inventory(tmp_path) == before


def test_revocation_blocks_publication_and_later_access(packet, tmp_path):
    bound = release.bind_synthetic_release(**packet)
    revoked = authority(bound)
    revoked.revoke()
    with pytest.raises(release.SyntheticReleaseError):
        publish(bound, revoked, tmp_path)
    assert not list(tmp_path.iterdir())
    context = authority(bound)
    result = publish(bound, context, tmp_path)
    before = inventory(tmp_path)
    context.revoke()
    with pytest.raises(release.SyntheticReleaseError):
        read(result, context, tmp_path)
    assert inventory(tmp_path) == before


def test_revocation_after_staging_prevents_commit_and_preserves_attempt(packet, tmp_path, monkeypatch):
    bound = release.bind_synthetic_release(**packet)
    context = authority(bound)
    original = release.storage._write_data

    def writing(fd, name, data, limit):
        original(fd, name, data, limit)
        if name == "manifest.json":
            context.revoke()

    with monkeypatch.context() as patch:
        patch.setattr(release.storage, "_write_data", writing)
        with pytest.raises(release.SyntheticReleaseError):
            publish(bound, context, tmp_path)
    attempted = tmp_path / packet["artifact_id"]
    assert not (attempted / "publication.commit").exists()
    before = inventory(tmp_path)
    with pytest.raises(release.SyntheticReleaseError):
        publish(bound, authority(bound), tmp_path)
    assert inventory(tmp_path) == before


def test_read_authorization_happens_before_artifact_io(packet, tmp_path, monkeypatch):
    bound = release.bind_synthetic_release(**packet)
    context = authority(bound)
    result = publish(bound, context, tmp_path)
    calls = []
    original = release._artifact_directory

    def observed(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(release, "_artifact_directory", observed)
    with pytest.raises(release.SyntheticReleaseError):
        read(result, context, tmp_path, requester_id="SYNTHETIC-OTHER")
    assert calls == []


@pytest.mark.parametrize("name", ["candidate.pdf", "manifest.json"])
def test_stored_tampering_refuses_without_repair(packet, tmp_path, name):
    bound = release.bind_synthetic_release(**packet)
    context = authority(bound)
    result = publish(bound, context, tmp_path)
    path = tmp_path / result["artifact_id"] / name
    path.write_bytes(path.read_bytes() + b" ")
    before = inventory(tmp_path)
    with pytest.raises(release.SyntheticReleaseError):
        read(result, context, tmp_path)
    assert inventory(tmp_path) == before


@pytest.mark.parametrize("kind", ["nonempty", "public", "file", "symlink"])
def test_invalid_commit_marker_refuses_without_repair(packet, tmp_path, kind):
    bound = release.bind_synthetic_release(**packet)
    context = authority(bound)
    result = publish(bound, context, tmp_path)
    marker = tmp_path / result["artifact_id"] / "publication.commit"
    if kind == "nonempty":
        (marker / "unexpected").write_bytes(b"SYNTHETIC-TAMPER")
    elif kind == "public":
        marker.chmod(0o755)
    else:
        marker.rmdir()
        if kind == "file":
            marker.write_bytes(b"SYNTHETIC-TAMPER")
        else:
            outside = tmp_path / "outside"
            outside.mkdir(mode=0o700)
            marker.symlink_to(outside, target_is_directory=True)
    before = inventory(tmp_path)
    marker_identity = marker.lstat()
    with pytest.raises(release.SyntheticReleaseError):
        read(result, context, tmp_path)
    with pytest.raises(release.SyntheticReleaseError):
        publish(bound, context, tmp_path)
    assert inventory(tmp_path) == before
    after = marker.lstat()
    assert (after.st_ino, after.st_mode, after.st_mtime_ns) == (
        marker_identity.st_ino, marker_identity.st_mode, marker_identity.st_mtime_ns)


def test_symlink_and_untrusted_directory_modes_refuse(packet, tmp_path):
    bound = release.bind_synthetic_release(**packet)
    context = authority(bound)
    external = tmp_path / "outside"
    external.mkdir(mode=0o700)
    (tmp_path / packet["artifact_id"]).symlink_to(external, target_is_directory=True)
    with pytest.raises(release.SyntheticReleaseError):
        publish(bound, context, tmp_path)
    assert not list(external.iterdir())
    other = tmp_path / "public"
    other.mkdir(mode=0o700)
    other.chmod(0o755)
    assert other.stat().st_mode & 0o777 == 0o755
    with pytest.raises(release.SyntheticReleaseError):
        publish(bound, context, other)


def test_disable_blocks_existing_artifact_without_deleting_it(packet, tmp_path, monkeypatch):
    bound = release.bind_synthetic_release(**packet)
    context = authority(bound)
    result = publish(bound, context, tmp_path)
    before = inventory(tmp_path)
    monkeypatch.setenv(release.DISABLE_KEY, "1")
    assert release.synthetic_release_profile()["mode"] == "DISABLED"
    with pytest.raises(release.SyntheticReleaseError):
        read(result, context, tmp_path)
    with pytest.raises(release.SyntheticReleaseError):
        publish(bound, context, tmp_path)
    assert inventory(tmp_path) == before


@pytest.mark.parametrize("phase", ["before_install", "after_install", "post_install_readback"])
def test_publication_failure_reports_visibility_boundary(packet, tmp_path, monkeypatch, phase):
    bound = release.bind_synthetic_release(**packet)
    context = authority(bound)
    original_fsync = release.os.fsync
    original_read = release._read_complete
    faults = []

    def sync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            names = set(os.listdir(fd))
            installed = "publication.commit" in names
            if ({"candidate.pdf", "manifest.json"} <= names
                    and ((phase == "before_install" and not installed)
                         or (phase == "after_install" and installed))):
                faults.append(phase)
                raise OSError("synthetic directory synchronization fault")
        return original_fsync(fd)

    def read_complete(*args, **kwargs):
        if phase == "post_install_readback" and not faults:
            faults.append(phase)
            raise FileExistsError("synthetic post-installation readback fault")
        return original_read(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(release.os, "fsync", sync)
        patch.setattr(release, "_read_complete", read_complete)
        with pytest.raises(release.SyntheticReleaseError) as error:
            publish(bound, context, tmp_path)
    expected_code = ("synthetic_publication_refused" if phase == "before_install"
                     else "synthetic_publication_outcome_unknown_retry_same_artifact")
    assert error.value.args == (expected_code,)
    assert faults == [phase]
    attempted = tmp_path / packet["artifact_id"]
    result = {"artifact_id": packet["artifact_id"],
              "manifest_sha256": digest((attempted / "manifest.json").read_bytes())}
    before = inventory(tmp_path)
    if phase == "before_install":
        assert not (attempted / "publication.commit").exists()
        with pytest.raises(release.SyntheticReleaseError):
            read(result, context, tmp_path)
        with pytest.raises(release.SyntheticReleaseError):
            publish(bound, context, tmp_path)
    else:
        assert read(result, context, tmp_path)[0] == packet["pdf_bytes"]
        retry = publish(bound, context, tmp_path)
        assert retry["artifact_id"] == result["artifact_id"]
        assert retry["manifest_sha256"] == result["manifest_sha256"]
        assert read(retry, context, tmp_path)[0] == packet["pdf_bytes"]
    assert inventory(tmp_path) == before


@pytest.mark.parametrize("phase", ["before_install", "after_install"])
def test_actual_process_interruption_has_exact_recovery_boundary(packet, tmp_path, phase):
    bound = release.bind_synthetic_release(**packet)
    context = authority(bound)
    original = release.os.mkdir

    def child():
        def interrupted(path, mode=0o777, *, dir_fd=None):
            if path == "publication.commit":
                if phase == "after_install":
                    original(path, mode, dir_fd=dir_fd)
                os._exit(23)
            return original(path, mode, dir_fd=dir_fd)
        release.os.mkdir = interrupted
        publish(bound, context, tmp_path)
        os._exit(99)

    process = multiprocessing.get_context("fork").Process(target=child)
    process.start()
    try:
        process.join(10)
        assert not process.is_alive(), "synthetic publisher exceeded the test deadline"
        assert process.exitcode == 23
    finally:
        if process.is_alive():
            process.kill()
            process.join(5)
    before = inventory(tmp_path)
    if phase == "before_install":
        attempted = tmp_path / packet["artifact_id"]
        result = {"artifact_id": packet["artifact_id"],
                  "manifest_sha256": digest((attempted / "manifest.json").read_bytes())}
        with pytest.raises(release.SyntheticReleaseError):
            read(result, context, tmp_path)
        with pytest.raises(release.SyntheticReleaseError):
            publish(bound, context, tmp_path)
    else:
        result = publish(bound, context, tmp_path)
        assert read(result, context, tmp_path)[0] == packet["pdf_bytes"]
    assert inventory(tmp_path) == before


def test_concurrent_conflicting_publications_never_overwrite(packet, tmp_path):
    first = release.bind_synthetic_release(**packet)
    other = case(pdf_bytes=packet["pdf_bytes"] + b"OTHER\n")
    second = release.bind_synthetic_release(**other)
    contexts = [authority(first), authority(second)]
    barrier = Barrier(2)
    results, failures = [], []

    def worker(bound, context):
        barrier.wait(timeout=5)
        try:
            results.append((publish(bound, context, tmp_path), context, bound._pdf_bytes))
        except release.SyntheticReleaseError:
            failures.append(True)

    threads = [Thread(target=worker, args=(value, context), daemon=True)
               for value, context in zip([first, second], contexts)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
        assert not thread.is_alive()
    assert len(results) == 1 and len(failures) == 1
    result, context, expected = results[0]
    assert read(result, context, tmp_path)[0] == expected


@pytest.mark.parametrize("delta", ["DESIGN_ONLY", "EDITORIAL_ONLY"])
def test_successor_lineage_preserves_prior_bytes_and_analysis(packet, tmp_path, delta):
    bound = release.bind_synthetic_release(**packet)
    first = publish(bound, authority(bound), tmp_path)
    before = inventory(tmp_path)
    later = case(pdf_bytes=packet["pdf_bytes"] + b"SYNTHETIC EDITORIAL VERSION\n",
                 artifact_id="SYNTHETIC-REPORT-2")
    later.update(delta=delta, content_revision="SYNTHETIC-CONTENT-2",
                 parent_manifest_bytes=canonical(first["manifest"]),
                 expected_parent_manifest_sha256=first["manifest_sha256"])
    successor = release.bind_synthetic_release(**later)
    final = publish(successor, authority(successor), tmp_path)
    assert final["manifest"]["binding"]["lineage"]["parent_artifact_id"] == "SYNTHETIC-REPORT"
    assert final["manifest"]["binding"]["lineage"]["analysis_revision"] == "1"
    assert all(inventory(tmp_path)[key] == value for key, value in before.items())


@pytest.mark.parametrize("change", ["analysis", "content", "parent_pin", "unsupported_delta"])
def test_lineage_cannot_hide_analysis_changes_or_wrong_parent(packet, tmp_path, change):
    first_bound = release.bind_synthetic_release(**packet)
    first = publish(first_bound, authority(first_bound), tmp_path)
    args = case(analysis_revision="2" if change == "analysis" else "1",
                content_sha256="2" * 64 if change == "content" else None,
                artifact_id="SYNTHETIC-REPORT-2")
    args.update(delta="RECLASSIFIED" if change == "unsupported_delta" else "DESIGN_ONLY",
                parent_manifest_bytes=canonical(first["manifest"]),
                expected_parent_manifest_sha256="2" * 64 if change == "parent_pin" else first["manifest_sha256"])
    with pytest.raises(release.SyntheticReleaseError):
        release.bind_synthetic_release(**args)


def test_oversized_pdf_refuses_without_truncation(packet):
    oversized = dict(packet, pdf_bytes=b"%PDF-1.7" + b" " * 16777216)
    oversized["expectations"] = dict(packet["expectations"], pdf_sha256=digest(oversized["pdf_bytes"]))
    with pytest.raises(release.SyntheticReleaseError):
        release.bind_synthetic_release(**oversized)


def test_qa_json_byte_limit_accepts_exact_bound_and_rejects_next_byte():
    limit = 65536
    overhead = len(canonical({"padding": ""}))
    value = {"padding": "x" * (limit - overhead)}
    exact = canonical(value)
    oversized = canonical({"padding": value["padding"] + "x"})
    assert len(exact) == limit and len(oversized) == limit + 1
    assert release._loads(exact, limit) == value
    with pytest.raises(release.SyntheticReleaseError):
        release._loads(oversized, limit)


def test_qa_cannot_claim_human_acceptance_or_omit_closed_fields(packet):
    data = json.loads(packet["qa_bytes"])
    data["human_assistive_accessibility_verified"] = True
    packet["qa_bytes"] = canonical(data)
    packet["expectations"]["qa_sha256"] = digest(packet["qa_bytes"])
    with pytest.raises(release.SyntheticReleaseError):
        release.bind_synthetic_release(**packet)


@pytest.mark.parametrize("fault", ["missing_parent", "wrong_pin", "analysis", "content", "wrong_parent_id"])
def test_direct_constructor_cannot_bypass_parent_lineage(packet, tmp_path, fault):
    first_bound = release.bind_synthetic_release(**packet)
    first = publish(first_bound, authority(first_bound), tmp_path)
    args = case(
        analysis_revision="2" if fault == "analysis" else "1",
        content_sha256="2" * 64 if fault == "content" else None,
        artifact_id="SYNTHETIC-DIRECT-CHILD")
    root_bound = release.bind_synthetic_release(**args)
    forged = root_bound.to_dict()
    parent_id = "SYNTHETIC-WRONG-PARENT" if fault == "wrong_parent_id" else first["artifact_id"]
    forged["lineage"].update(
        parent_artifact_id=parent_id,
        parent_manifest_sha256="2" * 64 if fault == "wrong_pin" else first["manifest_sha256"],
        delta="DESIGN_ONLY")
    forged["artifact"]["parent_artifact_ids"].append(parent_id)
    positional = (canonical(forged), args["pdf_bytes"], args["bound_request"],
                  args["locale_pack_bytes"], args["report_pack_bytes"])
    with pytest.raises(release.SyntheticReleaseError):
        if fault == "missing_parent":
            release.BoundSyntheticRelease(*positional)
        else:
            release.BoundSyntheticRelease(
                *positional, _parent_manifest_bytes=canonical(first["manifest"]))
    assert not (tmp_path / "SYNTHETIC-DIRECT-CHILD").exists()


def test_publication_revalidates_retained_parent_after_construction(packet, tmp_path):
    first_bound = release.bind_synthetic_release(**packet)
    first = publish(first_bound, authority(first_bound), tmp_path)
    args = case(artifact_id="SYNTHETIC-RECHECK-CHILD")
    args.update(delta="DESIGN_ONLY", parent_manifest_bytes=canonical(first["manifest"]),
                expected_parent_manifest_sha256=first["manifest_sha256"])
    child = release.bind_synthetic_release(**args)
    context = authority(child)
    # Exercise repeated validation even if trusted in-process code breaks freezing.
    object.__setattr__(child, "_parent_manifest_bytes", None)
    with pytest.raises(release.SyntheticReleaseError):
        publish(child, context, tmp_path)
    assert not (tmp_path / "SYNTHETIC-RECHECK-CHILD").exists()


def test_source_growth_is_refused_with_descriptor_reads_bounded_by_initial_size(tmp_path, monkeypatch):
    probe = tmp_path / "source.py"
    initial = b"bounded synthetic source\n"
    probe.write_bytes(initial)
    identity = probe.stat().st_ino
    original_read = os.read
    requests, returned = [], []
    grew = False

    def growing_read(fd, count):
        nonlocal grew
        chunk = original_read(fd, count)
        if os.fstat(fd).st_ino == identity:
            requests.append(count)
            returned.append(len(chunk))
            if not grew:
                grew = True
                with probe.open("ab") as output:
                    output.write(b"x" * 4096)
                    output.flush()
                    os.fsync(output.fileno())
        return chunk

    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setattr(release.qa.os, "read", growing_read)
    with pytest.raises(ValueError):
        release._source(probe, 64)
    assert grew and requests
    assert max(requests) <= len(initial)
    assert sum(returned) <= len(initial)
    assert probe.stat().st_size > 64
