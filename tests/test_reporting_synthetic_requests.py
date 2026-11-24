"""Finite request-slot tests using the real synthetic store and existing fixtures.

PDF-shaped bytes and completed-QA records remain explicit unit fixtures. These
tests neither render a PDF nor qualify a current final artifact or accessibility.
"""
from dataclasses import replace
import importlib.util
import json
import multiprocessing
from pathlib import Path
from threading import Event, Thread

import pytest

from reporting import synthetic_release as release
from reporting import synthetic_requests as requests
from reporting.requests import bind_report_request

# Reuse the exact sibling fixtures without depending on pytest's import mode or
# introducing a tests package/path override. This imports definitions, not tests.
_spec = importlib.util.spec_from_file_location(
    "synthetic_request_release_fixtures",
    Path(__file__).with_name("test_reporting_synthetic_release.py"))
helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helpers)


def case(**changes):
    packet = helpers.case(**changes)
    del packet["artifact_id"]
    return packet


@pytest.fixture
def packet(monkeypatch):
    monkeypatch.setenv(release.MODE_KEY, "qualification-only")
    monkeypatch.delenv(release.DISABLE_KEY, raising=False)
    return case()


def publish(bound, context, directory):
    return requests.publish_synthetic_request(
        request=bound, directory=directory, authority=context,
        requester_id=bound.to_dict()["request_snapshot"]["requester_id"])


def read(bound, result, context, directory):
    return requests.read_synthetic_request(
        request=bound, directory=directory,
        expected_manifest_sha256=result["manifest_sha256"], authority=context,
        requester_id=bound.to_dict()["request_snapshot"]["requester_id"])


def update_qa(packet, **changes):
    data = json.loads(packet["qa_bytes"])
    data.update(changes)
    packet["qa_bytes"] = helpers.canonical(data)
    packet["expectations"]["qa_sha256"] = helpers.digest(packet["qa_bytes"])
    for key, value in changes.items():
        if key in packet["expectations"]:
            packet["expectations"][key] = value


def update_request(packet, *, plan=None, **changes):
    old = packet["bound_request"]
    plan = old.plan if plan is None else plan
    captured = replace(old.request, **changes)
    packet["bound_request"] = bind_report_request(
        request=captured, resolved_request=captured, sources=old.sources, plan=plan)
    update_qa(packet, request_sha256=captured.request_sha256,
              source_binding_sha256=captured.source_binding.binding_sha256,
              plan_sha256=plan.plan_sha256)


@pytest.mark.parametrize("disabled", ["unset", "explicit"])
def test_default_and_explicit_disablement_preserve_existing_store(packet, tmp_path,
                                                                monkeypatch, disabled):
    bound = requests.bind_synthetic_request(**packet)
    context = helpers.authority(bound)
    result = publish(bound, context, tmp_path)
    before = helpers.inventory(tmp_path)
    if disabled == "unset":
        monkeypatch.delenv(release.MODE_KEY, raising=False)
    else:
        monkeypatch.setenv(release.DISABLE_KEY, "1")
    with pytest.raises(release.SyntheticReleaseError):
        requests.bind_synthetic_request(**packet)
    with pytest.raises(release.SyntheticReleaseError):
        publish(bound, context, tmp_path)
    with pytest.raises(release.SyntheticReleaseError):
        read(bound, result, context, tmp_path)
    assert helpers.inventory(tmp_path) == before


def test_repeated_binding_and_publication_reuse_one_immutable_slot(packet, tmp_path):
    first = requests.bind_synthetic_request(**packet)
    second = requests.bind_synthetic_request(**case())
    assert first.to_dict() == second.to_dict()
    assert first.binding_sha256 == second.binding_sha256
    context = helpers.authority(first)
    result = publish(first, context, tmp_path)
    before = helpers.inventory(tmp_path)
    assert publish(second, helpers.authority(second), tmp_path) == result
    assert read(second, result, context, tmp_path) == (packet["pdf_bytes"], result["manifest"])
    assert all(result["manifest"][key] is False for key in release.NONCLAIMS)
    assert list(tmp_path.iterdir()) == [tmp_path / result["artifact_id"]]
    assert helpers.inventory(tmp_path) == before


@pytest.mark.parametrize("output", ["pdf", "qa"])
def test_changed_outputs_conflict_in_the_same_semantic_slot(packet, tmp_path, output):
    first = requests.bind_synthetic_request(**packet)
    result = publish(first, helpers.authority(first), tmp_path)
    before = helpers.inventory(tmp_path)
    other = case(pdf_bytes=packet["pdf_bytes"] + b"SYNTHETIC-OUTPUT-CHANGE\n") if output == "pdf" else case()
    if output == "qa":
        update_qa(other, native_observation_sha256=helpers.digest(b"SYNTHETIC-DIFFERENT-QA"))
    second = requests.bind_synthetic_request(**other)
    assert second.to_dict()["artifact"]["artifact_id"] == result["artifact_id"]
    assert second.binding_sha256 != first.binding_sha256
    with pytest.raises(release.SyntheticReleaseError, match="^synthetic_publication_refused$"):
        publish(second, helpers.authority(second), tmp_path)
    assert helpers.inventory(tmp_path) == before
    assert read(first, result, helpers.authority(first), tmp_path)[0] == packet["pdf_bytes"]


@pytest.mark.parametrize("dimension", [
    "request_id", "requester_id", "namespace", "analysis_revision", "locale_reference",
    "policy_record", "consent_record", "review_record", "report_pack", "completeness_plan",
    "content_sha256", "ir_sha256", "source_expectation_sha256", "content_revision", "evidence_cutoff",
])
def test_captured_input_dimensions_remain_distinct(packet, dimension):
    original = requests.bind_synthetic_request(**packet)
    other = case()
    if dimension == "namespace":
        other = case(namespace="SYNTHETIC-OTHER-NAMESPACE")
    elif dimension == "analysis_revision":
        other = case(analysis_revision="2")
    elif dimension == "locale_reference":
        other = case(upstream_ids={"locale": "SYNTHETIC-LOCALE-2"})
    elif dimension in ("request_id", "requester_id"):
        update_request(other, **{dimension: "SYNTHETIC-DIFFERENT-" + dimension.upper()})
    elif dimension in ("policy_record", "consent_record", "review_record"):
        captured = other["bound_request"].request
        record = replace(getattr(captured, dimension), sha256=helpers.digest(dimension.encode()))
        update_request(other, **{dimension: record})
    elif dimension == "report_pack":
        other["report_pack_bytes"] = b"SYNTHETIC-SEALED-OTHER-REPORT-PACK"
        update_request(other, report_contract_bundle_sha256=helpers.digest(other["report_pack_bytes"]))
    elif dimension == "completeness_plan":
        old = other["bound_request"].plan
        plan = type(old)(old.source_binding_sha256, ("SYNTHETIC-F2",),
                         ("identity", "findings"), ("identity", "findings"))
        update_request(other, plan=plan, completeness_plan_sha256=plan.plan_sha256)
    elif dimension in ("content_sha256", "ir_sha256", "source_expectation_sha256"):
        update_qa(other, **{dimension: helpers.digest(dimension.encode())})
    elif dimension == "content_revision":
        other[dimension] = "SYNTHETIC-CONTENT-2"
    else:
        other[dimension] = "2026-01-02T00:00:00Z"
    changed = requests.bind_synthetic_request(**other)
    assert changed.to_dict()["artifact"]["artifact_id"] != original.to_dict()["artifact"]["artifact_id"]


@pytest.mark.parametrize("delta", ["DESIGN_ONLY", "EDITORIAL_ONLY"])
def test_parent_lineage_has_its_own_slot_and_retains_prior_bytes(packet, tmp_path, delta):
    parent = requests.bind_synthetic_request(**packet)
    original = publish(parent, helpers.authority(parent), tmp_path)
    before = helpers.inventory(tmp_path)
    child_packet = case(
        pdf_bytes=packet["pdf_bytes"] + b"SYNTHETIC-SUCCESSOR\n",
        content_sha256=helpers.digest(b"SYNTHETIC-EDITORIAL-CONTENT") if delta == "EDITORIAL_ONLY" else None)
    child_packet.update(
        content_revision="SYNTHETIC-CONTENT-2", delta=delta,
        parent_manifest_bytes=helpers.canonical(original["manifest"]),
        expected_parent_manifest_sha256=original["manifest_sha256"])
    child = requests.bind_synthetic_request(**child_packet)
    result = publish(child, helpers.authority(child), tmp_path)
    assert result["artifact_id"] != original["artifact_id"]
    assert result["manifest"]["binding"]["lineage"]["parent_artifact_id"] == original["artifact_id"]
    assert result["manifest"]["binding"]["lineage"]["parent_manifest_sha256"] == original["manifest_sha256"]
    assert result["manifest"]["binding"]["lineage"]["analysis_revision"] == "1"
    assert publish(requests.bind_synthetic_request(**child_packet), helpers.authority(child), tmp_path) == result
    after = helpers.inventory(tmp_path)
    assert all(after[key] == value for key, value in before.items())


def test_new_process_reconstructs_same_slot_without_an_in_memory_registry(packet, tmp_path):
    bound = requests.bind_synthetic_request(**packet)
    result = publish(bound, helpers.authority(bound), tmp_path)
    before = helpers.inventory(tmp_path)

    def child():
        reconstructed = requests.bind_synthetic_request(**case())
        # This is a fresh, explicit synthetic harness grant, not durable consent.
        context = helpers.authority(reconstructed)
        assert publish(reconstructed, context, tmp_path) == result
        assert read(reconstructed, result, context, tmp_path)[0] == packet["pdf_bytes"]

    process = multiprocessing.get_context("fork").Process(target=child)
    process.start()
    try:
        process.join(10)
        assert not process.is_alive(), "synthetic retry exceeded the test deadline"
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.kill()
            process.join(5)
    assert helpers.inventory(tmp_path) == before


@pytest.mark.parametrize("conflicting", [False, True])
def test_in_flight_duplicate_has_one_writer_then_exact_retry_or_conflict(packet, tmp_path,
                                                                      monkeypatch, conflicting):
    first = requests.bind_synthetic_request(**packet)
    other = case(pdf_bytes=packet["pdf_bytes"] + b"SYNTHETIC-CONFLICT\n") if conflicting else case()
    second = requests.bind_synthetic_request(**other)
    assert first.to_dict()["artifact"]["artifact_id"] == second.to_dict()["artifact"]["artifact_id"]
    entered, continue_write = Event(), Event()
    original_write = release.storage._write_data
    results, errors = [], []

    def paused_write(fd, name, data, limit):
        if name == "candidate.pdf" and not entered.is_set():
            entered.set()
            assert continue_write.wait(5), "synthetic duplicate exceeded the test deadline"
        return original_write(fd, name, data, limit)

    def first_writer():
        try:
            results.append(publish(first, helpers.authority(first), tmp_path))
        except BaseException as error:
            errors.append(error)

    with monkeypatch.context() as patch:
        patch.setattr(release.storage, "_write_data", paused_write)
        thread = Thread(target=first_writer, daemon=True)
        thread.start()
        try:
            assert entered.wait(5)
            before_duplicate = helpers.inventory(tmp_path)
            with pytest.raises(release.SyntheticReleaseError) as failure:
                publish(second, helpers.authority(second), tmp_path)
            assert failure.value.args[0] in (
                "synthetic_publication_refused", "synthetic_publication_outcome_unknown_retry_same_artifact")
            assert helpers.inventory(tmp_path) == before_duplicate
        finally:
            continue_write.set()
            thread.join(10)
        assert not thread.is_alive()
    assert not errors and len(results) == 1
    result = results[0]
    before_retry = helpers.inventory(tmp_path)
    if conflicting:
        with pytest.raises(release.SyntheticReleaseError, match="^synthetic_publication_refused$"):
            publish(second, helpers.authority(second), tmp_path)
    else:
        assert publish(second, helpers.authority(second), tmp_path) == result
    assert list(tmp_path.iterdir()) == [tmp_path / result["artifact_id"]]
    assert helpers.inventory(tmp_path) == before_retry
    assert read(first, result, helpers.authority(first), tmp_path)[0] == packet["pdf_bytes"]


def test_precommit_partial_is_retained_and_refused_without_automatic_repair(packet, tmp_path, monkeypatch):
    bound = requests.bind_synthetic_request(**packet)
    context = helpers.authority(bound)

    def fail_before_commit(_fd):
        raise OSError("synthetic precommit interruption")

    with monkeypatch.context() as patch:
        patch.setattr(release, "_commit_record", fail_before_commit)
        with pytest.raises(release.SyntheticReleaseError, match="^synthetic_publication_refused$"):
            publish(bound, context, tmp_path)
    slot = tmp_path / bound.to_dict()["artifact"]["artifact_id"]
    assert {path.name for path in slot.iterdir()} == {"candidate.pdf", "manifest.json"}
    before = helpers.inventory(tmp_path)
    reconstructed = requests.bind_synthetic_request(**case())
    with pytest.raises(release.SyntheticReleaseError, match="^synthetic_publication_refused$"):
        publish(reconstructed, helpers.authority(reconstructed), tmp_path)
    partial = {"manifest_sha256": helpers.digest((slot / "manifest.json").read_bytes())}
    with pytest.raises(release.SyntheticReleaseError):
        read(reconstructed, partial, helpers.authority(reconstructed), tmp_path)
    assert helpers.inventory(tmp_path) == before


def test_postcommit_unknown_outcome_propagates_then_exact_retry_recovers(packet, tmp_path, monkeypatch):
    bound = requests.bind_synthetic_request(**packet)
    context = helpers.authority(bound)

    def unreadable_after_commit(*_args, **_kwargs):
        raise OSError("synthetic postcommit readback interruption")

    with monkeypatch.context() as patch:
        patch.setattr(release, "_read_complete", unreadable_after_commit)
        with pytest.raises(release.SyntheticReleaseError) as failure:
            publish(bound, context, tmp_path)
    assert failure.value.args == ("synthetic_publication_outcome_unknown_retry_same_artifact",)
    slot = tmp_path / bound.to_dict()["artifact"]["artifact_id"]
    assert (slot / "publication.commit").is_dir()
    before = helpers.inventory(tmp_path)
    reconstructed = requests.bind_synthetic_request(**case())
    result = publish(reconstructed, helpers.authority(reconstructed), tmp_path)
    assert read(reconstructed, result, helpers.authority(reconstructed), tmp_path)[0] == packet["pdf_bytes"]
    assert helpers.inventory(tmp_path) == before


@pytest.mark.parametrize("operation", ["publish", "read"])
def test_revocation_is_rechecked_for_existing_request_slots(packet, tmp_path, operation):
    bound = requests.bind_synthetic_request(**packet)
    context = helpers.authority(bound)
    result = publish(bound, context, tmp_path)
    before = helpers.inventory(tmp_path)
    context.revoke()
    with pytest.raises(release.SyntheticReleaseError):
        if operation == "publish":
            publish(bound, context, tmp_path)
        else:
            read(bound, result, context, tmp_path)
    assert helpers.inventory(tmp_path) == before


def test_wrong_case_and_requester_cannot_reuse_another_live_grant(packet, tmp_path):
    first = requests.bind_synthetic_request(**packet)
    context = helpers.authority(first)
    result = publish(first, context, tmp_path)
    before = helpers.inventory(tmp_path)
    other = requests.bind_synthetic_request(**case(namespace="SYNTHETIC-OTHER-NAMESPACE"))
    with pytest.raises(release.SyntheticReleaseError):
        publish(other, context, tmp_path)
    with pytest.raises(release.SyntheticReleaseError):
        read(other, result, context, tmp_path)
    with pytest.raises(release.SyntheticReleaseError):
        requests.read_synthetic_request(
            request=first, directory=tmp_path, expected_manifest_sha256=result["manifest_sha256"],
            authority=context, requester_id="SYNTHETIC-OTHER-REQUESTER")
    assert helpers.inventory(tmp_path) == before


def test_artifact_scoped_binding_cannot_bypass_the_derived_identifier(packet, tmp_path):
    old = release.bind_synthetic_release(**dict(packet, artifact_id="SYNTHETIC-CALLER-CHOSEN"))
    context = helpers.authority(old)
    with pytest.raises(release.SyntheticReleaseError, match="^synthetic_request_refused$"):
        publish(old, context, tmp_path)
    assert not list(tmp_path.iterdir())


def test_temporary_binding_name_does_not_collide_with_an_upstream_role(packet):
    other = case(upstream_ids={"result": "SYNTHETIC-REQUEST-BIND-0"})
    bound = requests.bind_synthetic_request(**other)
    data = bound.to_dict()
    assert data["artifact"]["artifact_id"].startswith(requests.ARTIFACT_PREFIX)
    assert data["artifact"]["artifact_id"] not in data["artifact"]["parent_artifact_ids"]
    assert data["request_snapshot"] == other["bound_request"].request.to_dict()
