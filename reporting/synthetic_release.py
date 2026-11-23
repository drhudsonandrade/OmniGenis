"""Private synthetic publication; disabled by default and not production authority.

Inputs come from an independently controlled synthetic harness. This module
checks their sealed contracts and exact joins; self-supplied matching hashes do
not prove origin or native execution. SyntheticAuthorityContext is a revocable,
in-process qualification fixture, not real-user authentication, legal consent
or a distributed revocation service. Existing candidate nonclaims are retained.

Only these APIs define publication visibility. Partial attempts remain refused;
exact complete retries are read-only. Process-interruption tests do not establish
power-loss durability or protection from a malicious same-owner process.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from threading import RLock

from jsonschema import Draft202012Validator
from omnigenis.capabilities.artifact_gate import verify_artifact_bytes
from reporting import _pdf_job_durable_receipt as directories
from reporting import pdf_candidate_store as storage
from reporting import typed_original_brand_qa as qa
from reporting.requests import BoundReportRequest

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "reporting/synthetic-release-profile.v1.json"
SCHEMA = ROOT / "schemas/report-synthetic-release.v1.schema.json"
PROFILE_ID = "synthetic-technical-final-release-v1"
SCOPE = "SYNTHETIC_PRIVATE_QUALIFICATION_ONLY"
MODE_KEY = "OMNIGENIS_SYNTHETIC_RELEASE_MODE"
DISABLE_KEY = "OMNIGENIS_SYNTHETIC_RELEASE_DISABLED"
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z")
NAMES = frozenset({"candidate.pdf", "manifest.json", "publication.json"})
SOURCE_PATHS = frozenset({
    "reporting/synthetic_release.py", "schemas/report-synthetic-release.v1.schema.json",
    "reporting/requests.py", "reporting/bindings.py", "reporting/completeness.py",
    "reporting/workflow.py", "reporting/pdf_candidate_store.py",
    "reporting/_pdf_job_durable_receipt.py", "omnigenis/capabilities/artifact_gate.py",
    "reporting/families.v1.json", "reporting/locales/en-US.v1.json",
    "reporting/pdf-typed-brand-render.v1.json",
    "reporting/pdf-typed-original-brand-qa.v1.json",
    "schemas/report-request-snapshot.v1.schema.json",
})
EXPECTATIONS = frozenset({
    "release_profile_sha256", "pdf_sha256", "qa_sha256", "qa_profile_sha256",
    "renderer_profile_sha256", "ir_sha256", "source_expectation_sha256", "content_sha256",
})
NONCLAIMS = {name: False for name in (
    "production_release_authorized", "clinical_release_authorized",
    "default_pipeline_activated", "product_profile_qualified",
    "upstream_authority_authenticated", "qa_execution_authenticated",
    "distributed_revocation_supported", "power_loss_durability_qualified",
    "human_assistive_accessibility_verified",
)}


class SyntheticReleaseError(ValueError):
    """Content-free refusal without private artifact contents."""


def _require(condition):
    if not condition:
        raise SyntheticReleaseError("synthetic_release_refused")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _digest(value):
    _require(type(value) is str and DIGEST.fullmatch(value) is not None)


def _identifier(value):
    _require(type(value) is str and IDENTIFIER.fullmatch(value) is not None)


def _loads(raw, limit):
    _require(type(raw) is bytes and 0 < len(raw) <= limit)

    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result)
            result[key] = value
        return result

    def nonfinite(_value):
        raise SyntheticReleaseError("synthetic_release_refused")

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)
    _require(type(value) is dict and _canonical(value) == raw)
    return value


def _source(path, limit):
    _require(not path.is_symlink() and path.is_file()
             and path.resolve().is_relative_to(ROOT) and path.stat().st_size <= limit)
    raw, _ = qa._bounded_profile_file(path, limit, retain=True)
    _require(type(raw) is bytes and 0 < len(raw) <= limit)
    return raw


def _profile():
    raw = _source(PROFILE, 16384)
    value = json.loads(raw)
    _require(type(value) is dict and set(value) == {
        "schema_version", "profile_id", "scope", "qualification_state",
        "default_pipeline_activated", "product_profile_qualified",
        "supported_profile", "staged_locale_ids", "other_profiles",
        "max_pdf_bytes", "max_qa_bytes", "max_manifest_bytes", "max_authority_bytes", "sources",
    })
    _require(value["schema_version"] == "1.0.0" and value["profile_id"] == PROFILE_ID
             and value["scope"] == SCOPE and value["qualification_state"] == "PREPARATION_ONLY"
             and value["default_pipeline_activated"] is False
             and value["product_profile_qualified"] is False
             and value["staged_locale_ids"] == ["pt-BR", "es-ES", "it-IT", "fr-FR"]
             and value["other_profiles"] == "DISABLED"
             and value["supported_profile"] == {
                 "family_id": "e1a-small-variant", "report_id": "e1a-small-variant-report",
                 "locale_id": "en-US", "intended_use_id": "e1a-technical-validation"})
    for key, expected in {"max_pdf_bytes": 16777216, "max_qa_bytes": 65536,
                          "max_manifest_bytes": 131072, "max_authority_bytes": 8192}.items():
        _require(type(value[key]) is int and value[key] == expected)
    _require(type(value["sources"]) is dict and set(value["sources"]) == SOURCE_PATHS)
    for name, expected in value["sources"].items():
        _digest(expected)
        _require(sha256(_source(ROOT / name, 524288)).hexdigest() == expected)
    _require(qa.profile_sha256()
             == value["sources"]["reporting/pdf-typed-original-brand-qa.v1.json"])
    return value, sha256(raw).hexdigest()


def profile_sha256():
    """Return a source-checked pin without enabling publication."""
    try:
        return _profile()[1]
    except Exception:
        raise SyntheticReleaseError("synthetic_release_profile_refused") from None


def synthetic_release_profile():
    """Describe a preparation profile; no combination is product-qualified."""
    try:
        profile, pin = _profile()
        mode, disabled = os.environ.get(MODE_KEY), os.environ.get(DISABLE_KEY)
        _require(mode in (None, "qualification-only") and disabled in (None, "1"))
        return {"profile_id": PROFILE_ID, "profile_sha256": pin, "scope": SCOPE,
                "mode": ("QUALIFICATION_ONLY" if mode == "qualification-only"
                         and disabled is None else "DISABLED"),
                "supported_profile": dict(profile["supported_profile"]),
                "qualification_state": "PREPARATION_ONLY",
                "staged_locale_ids": list(profile["staged_locale_ids"]),
                "other_profiles": "DISABLED", **NONCLAIMS}
    except Exception:
        raise SyntheticReleaseError("synthetic_release_profile_refused") from None


def _policy(expected=None):
    _require(os.environ.get(MODE_KEY) == "qualification-only"
             and os.environ.get(DISABLE_KEY) is None)
    profile, pin = _profile()
    if expected is not None:
        _digest(expected)
        _require(pin == expected)
    return profile, pin


def _schema(value, definition=None):
    schema = json.loads(_source(SCHEMA, 131072))
    if definition:
        schema = {"$schema": schema["$schema"], "$defs": schema["$defs"],
                  "$ref": "#/$defs/" + definition}
    Draft202012Validator(schema).validate(value)


def _validate_binding(binding):
    _schema(binding, "binding")
    request = binding["request_snapshot"]
    Draft202012Validator(json.loads(_source(
        ROOT / "schemas/report-request-snapshot.v1.schema.json", 131072))).validate(request)
    _require(sha256(_canonical(request)).hexdigest() == binding["request_sha256"]
             and sha256(_canonical(request["source_binding"])).hexdigest()
             == binding["source_binding_sha256"]
             and request["completeness_plan_sha256"] == binding["plan_sha256"]
             and request["locale_id"] == "en-US"
             and request["intended_use_id"] == "e1a-technical-validation"
             and request["source_binding"]["report_id"] == "e1a-small-variant-report"
             and request["consent_status"] == "VERIFIED" and request["review_state"] == "APPROVED"
             and request["consent_record"] is not None and request["review_record"] is not None)
    source = request["source_binding"]
    identity = source["identity"]
    _require(identity["namespace_id"].upper().startswith("SYNTHETIC-"))
    raw = binding["qa_record_json"].encode("utf-8")
    result = _loads(raw, 65536)
    _require(sha256(raw).hexdigest() == binding["qa_sha256"]
             and qa.TypedOriginalBrandQA(raw).to_dict() == result)
    for key in ("request_sha256", "source_binding_sha256", "plan_sha256",
                "source_expectation_sha256", "ir_sha256", "content_sha256"):
        _require(result[key] == binding[key])
    _require(result["pdf_sha256"] == binding["artifact"]["sha256"]
             and result["pdf_byte_count"] == binding["artifact"]["size_bytes"]
             and result["profile_sha256"] == binding["qa_profile_sha256"])
    lineage = binding["lineage"]
    expected = {"analysis_id": identity["analysis_id"], "analysis_revision": identity["analysis_revision"],
                "canonical_result": source["canonical_result"], "evidence_snapshot": source["evidence_snapshot"],
                "reference_bundle_sha256": source["reference_bundle_sha256"],
                "report_pack_sha256": request["report_contract_bundle_sha256"],
                "locale_pack": request["locale_pack"], "locale_id": request["locale_id"]}
    _require(all(lineage[k] == v for k, v in expected.items()))
    datetime.strptime(lineage["evidence_cutoff"], "%Y-%m-%dT%H:%M:%SZ")
    parents = [source[k]["artifact_id"] for k in ("input_artifact", "canonical_result", "evidence_snapshot")]
    parents.append(request["locale_pack"]["artifact_id"])
    if lineage["parent_artifact_id"] is not None:
        parents.append(lineage["parent_artifact_id"])
    _require(binding["artifact"]["parent_artifact_ids"] == parents
             and binding["artifact"]["artifact_id"] not in parents
             and binding["artifact"]["producer_execution_profile_id"] == lineage["renderer_profile_id"]
             and (lineage["parent_artifact_id"] is None) == (lineage["parent_manifest_sha256"] is None)
             and (lineage["delta"] == "NEW") == (lineage["parent_artifact_id"] is None))


def _match_grant(grant, binding, action, requester):
    _schema(grant, "authority")
    request = binding["request_snapshot"]
    _require(action in grant["actions"]
             and grant["binding_sha256"] == sha256(_canonical(binding)).hexdigest()
             and grant["artifact_id"] == binding["artifact"]["artifact_id"]
             and grant["namespace_id"] == request["source_binding"]["identity"]["namespace_id"]
             and grant["request_id"] == request["request_id"]
             and grant["request_sha256"] == binding["request_sha256"]
             and grant["requester_id"] == requester == request["requester_id"]
             and grant["consent_record"] == request["consent_record"]
             and grant["review_record"] == request["review_record"])


def _validate_manifest(manifest):
    _schema(manifest)
    _validate_binding(manifest["binding"])
    _require(sha256(_canonical(manifest["binding"])).hexdigest() == manifest["binding_sha256"]
             and sha256(_canonical(manifest["authorization_record"])).hexdigest()
             == manifest["authorization_record_sha256"])
    _match_grant(manifest["authorization_record"], manifest["binding"], "publish",
                 manifest["binding"]["request_snapshot"]["requester_id"])


def _validate_parent(binding, parent_manifest_bytes):
    """Validate the immediate immutable parent on every construction path."""
    lineage = binding["lineage"]
    if parent_manifest_bytes is None:
        _require(lineage["parent_artifact_id"] is None
                 and lineage["parent_manifest_sha256"] is None
                 and lineage["delta"] == "NEW")
        return
    _require(type(parent_manifest_bytes) is bytes and 0 < len(parent_manifest_bytes) <= 131072)
    _digest(lineage["parent_manifest_sha256"])
    _require(sha256(parent_manifest_bytes).hexdigest() == lineage["parent_manifest_sha256"])
    parent = _loads(parent_manifest_bytes, 131072)
    _validate_manifest(parent)
    old = parent["binding"]
    _require(lineage["delta"] in ("DESIGN_ONLY", "EDITORIAL_ONLY")
             and lineage["parent_artifact_id"] == old["artifact"]["artifact_id"]
             and lineage["parent_artifact_id"] != binding["artifact"]["artifact_id"]
             and old["request_snapshot"]["source_binding"]
             == binding["request_snapshot"]["source_binding"])
    for key in ("analysis_id", "analysis_revision", "canonical_result", "evidence_snapshot",
                "evidence_cutoff", "reference_bundle_sha256"):
        _require(old["lineage"][key] == lineage[key])
    if lineage["delta"] == "DESIGN_ONLY":
        _require(old["content_sha256"] == binding["content_sha256"])


class SyntheticAuthorityContext:
    """Sealed synthetic fixture with monotonic local revocation, not real identity.

    The harness supplies controlled record bytes and an independent pin. A new
    context is an explicit harness decision, not restoration of durable consent.
    """
    __slots__ = ("_record", "_pin", "_revoked", "_lock")

    def __init__(self, *, authorization_record, expected_record_sha256):
        try:
            _digest(expected_record_sha256)
            record = _loads(authorization_record, 8192)
            _schema(record, "authority")
            _require(sha256(authorization_record).hexdigest() == expected_record_sha256)
            self._record, self._pin = authorization_record, expected_record_sha256
            self._revoked, self._lock = False, RLock()
        except Exception:
            raise SyntheticReleaseError("synthetic_authority_refused") from None

    def revoke(self):
        """Serialize revocation with synthetic publication and access."""
        with self._lock:
            self._revoked = True

    def _check(self, binding, action, requester):
        _require(not self._revoked and type(self._record) is bytes
                 and sha256(self._record).hexdigest() == self._pin)
        record = _loads(self._record, 8192)
        _match_grant(record, binding, action, requester)
        return record


@dataclass(frozen=True, slots=True, repr=False)
class BoundSyntheticRelease:
    """Immutable inputs; matching digests do not authenticate their origin."""
    _binding_bytes: bytes = field(repr=False)
    _pdf_bytes: bytes = field(repr=False)
    _request: BoundReportRequest = field(repr=False)
    _locale_pack_bytes: bytes = field(repr=False)
    _report_pack_bytes: bytes = field(repr=False)
    _parent_manifest_bytes: bytes | None = field(default=None, repr=False)

    def __post_init__(self):
        self._validate()

    def _validate(self):
        binding = _loads(self._binding_bytes, 131072)
        profile, _ = _policy(binding["profile_sha256"])
        _validate_binding(binding)
        _validate_parent(binding, self._parent_manifest_bytes)
        _require(type(self._request) is BoundReportRequest)
        BoundReportRequest.__post_init__(self._request)
        _require(self._request.request.to_dict() == binding["request_snapshot"]
                 and type(self._pdf_bytes) is bytes
                 and 0 < len(self._pdf_bytes) <= profile["max_pdf_bytes"]
                 and self._pdf_bytes.startswith(b"%PDF-1.7")
                 and verify_artifact_bytes(binding["artifact"], self._pdf_bytes).passed)
        _require(type(self._locale_pack_bytes) is bytes
                 and self._locale_pack_bytes == _source(ROOT / "reporting/locales/en-US.v1.json", 65536)
                 and sha256(self._locale_pack_bytes).hexdigest() == self._request.request.locale_pack.sha256
                 and type(self._report_pack_bytes) is bytes and 0 < len(self._report_pack_bytes) <= 131072
                 and sha256(self._report_pack_bytes).hexdigest() == self._request.request.report_contract_bundle_sha256
                 and binding["qa_profile_sha256"]
                 == profile["sources"]["reporting/pdf-typed-original-brand-qa.v1.json"]
                 and binding["lineage"]["renderer_profile_sha256"]
                 == profile["sources"]["reporting/pdf-typed-brand-render.v1.json"])
        render = json.loads(_source(ROOT / "reporting/pdf-typed-brand-render.v1.json", 16384))
        _require(binding["lineage"]["renderer_profile_id"] == render["profile_id"])
        return binding

    @property
    def binding_sha256(self):
        self._validate()
        return sha256(self._binding_bytes).hexdigest()

    def to_dict(self):
        return self._validate()

    def __repr__(self):
        return "BoundSyntheticRelease()"


def bind_synthetic_release(*, bound_request, pdf_bytes, qa_bytes, locale_pack_bytes,
                           report_pack_bytes, expectations, artifact_id, content_revision,
                           evidence_cutoff, delta="NEW", parent_manifest_bytes=None,
                           expected_parent_manifest_sha256=None):
    """Bind exact inputs without rendering, granting authority or publishing."""
    try:
        _require(type(expectations) is dict and set(expectations) == EXPECTATIONS)
        for value in expectations.values():
            _digest(value)
        profile, pin = _policy(expectations["release_profile_sha256"])
        _identifier(artifact_id)
        _identifier(content_revision)
        _require(type(bound_request) is BoundReportRequest)
        BoundReportRequest.__post_init__(bound_request)
        request = bound_request.request.to_dict()
        result = _loads(qa_bytes, profile["max_qa_bytes"])
        _require(sha256(qa_bytes).hexdigest() == expectations["qa_sha256"]
                 and type(pdf_bytes) is bytes and 0 < len(pdf_bytes) <= profile["max_pdf_bytes"]
                 and sha256(pdf_bytes).hexdigest() == expectations["pdf_sha256"])
        parent = (None if parent_manifest_bytes is None
                  else _loads(parent_manifest_bytes, profile["max_manifest_bytes"]))
        source = request["source_binding"]
        parent_id = parent["binding"]["artifact"]["artifact_id"] if parent else None
        parents = [source[k]["artifact_id"] for k in ("input_artifact", "canonical_result", "evidence_snapshot")]
        parents.append(request["locale_pack"]["artifact_id"])
        if parent_id is not None:
            parents.append(parent_id)
        render = json.loads(_source(ROOT / "reporting/pdf-typed-brand-render.v1.json", 16384))
        lineage = {"parent_artifact_id": parent_id,
                   "parent_manifest_sha256": expected_parent_manifest_sha256, "delta": delta,
                   "analysis_id": source["identity"]["analysis_id"],
                   "analysis_revision": source["identity"]["analysis_revision"],
                   "canonical_result": source["canonical_result"], "evidence_snapshot": source["evidence_snapshot"],
                   "evidence_cutoff": evidence_cutoff, "reference_bundle_sha256": source["reference_bundle_sha256"],
                   "report_pack_sha256": request["report_contract_bundle_sha256"], "content_revision": content_revision,
                   "locale_id": request["locale_id"], "locale_pack": request["locale_pack"],
                   "renderer_profile_id": render["profile_id"],
                   "renderer_profile_sha256": expectations["renderer_profile_sha256"]}
        binding = {"schema_version": "1.0.0", "profile_sha256": pin,
                   "artifact": {"schema_version": "1.0.0", "artifact_id": artifact_id,
                       "artifact_kind": "synthetic-technical-report", "media_type": "application/pdf",
                       "sha256": expectations["pdf_sha256"], "size_bytes": len(pdf_bytes),
                       "producer_execution_profile_id": render["profile_id"], "parent_artifact_ids": parents},
                   "request_snapshot": request, "request_sha256": bound_request.request.request_sha256,
                   "source_binding_sha256": bound_request.request.source_binding.binding_sha256,
                   "plan_sha256": bound_request.plan.plan_sha256,
                   "qa_record_json": qa_bytes.decode("utf-8"), "qa_sha256": expectations["qa_sha256"],
                   "qa_profile_sha256": expectations["qa_profile_sha256"],
                   "source_expectation_sha256": expectations["source_expectation_sha256"],
                   "ir_sha256": expectations["ir_sha256"], "content_sha256": expectations["content_sha256"],
                   "lineage": lineage}
        _require(result["profile_sha256"] == expectations["qa_profile_sha256"])
        return BoundSyntheticRelease(_canonical(binding), pdf_bytes, bound_request,
                                     locale_pack_bytes, report_pack_bytes, parent_manifest_bytes)
    except Exception:
        raise SyntheticReleaseError("synthetic_release_binding_refused") from None


def _dir_identity(fd):
    info = os.fstat(fd)
    _require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700
             and info.st_uid == os.geteuid())
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


@contextmanager
def _artifact_directory(directory, artifact_id, *, create):
    _require(isinstance(directory, Path))
    _identifier(artifact_id)
    root_fd = directories._open_private_directory(directory)
    child_fd = None
    try:
        root_identity = _dir_identity(root_fd)
        if create:
            os.mkdir(artifact_id, 0o700, dir_fd=root_fd)
        child_fd = os.open(artifact_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                           dir_fd=root_fd)
        child_identity = _dir_identity(child_fd)

        def check():
            named = os.stat(artifact_id, dir_fd=root_fd, follow_symlinks=False)
            _require(_dir_identity(root_fd) == root_identity and _dir_identity(child_fd) == child_identity
                     and (named.st_dev, named.st_ino, named.st_mode, named.st_uid, named.st_gid) == child_identity)
            again = directories._open_private_directory(directory)
            try:
                _require(_dir_identity(again) == root_identity)
            finally:
                os.close(again)
        check()
        yield child_fd, check
        check()
    finally:
        if child_fd is not None:
            os.close(child_fd)
        os.close(root_fd)


def _commit_record(fd, raw):
    """Publish a complete synced marker by a create-only link."""
    _require(type(raw) is bytes and 0 < len(raw) <= 1024)
    out = os.open("publication.pending", os.O_WRONLY | os.O_CREAT | os.O_EXCL
                  | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd)
    try:
        os.fchmod(out, 0o600)
        position = 0
        while position < len(raw):
            count = os.write(out, raw[position:])
            _require(type(count) is int and 0 < count <= len(raw) - position)
            position += count
        os.fsync(out)
        storage._file_identity(os.fstat(out), 1024)
    finally:
        os.close(out)
    os.link("publication.pending", "publication.json", src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
    os.unlink("publication.pending", dir_fd=fd)
    os.fsync(fd)


def _read_complete(fd, expected_manifest_sha256, expected_binding=None):
    _digest(expected_manifest_sha256)
    _require(set(os.listdir(fd)) == NAMES)
    limits = {"candidate.pdf": 16777216, "manifest.json": 131072, "publication.json": 1024}
    before = {n: storage._file_identity(os.stat(n, dir_fd=fd, follow_symlinks=False), limit)
              for n, limit in limits.items()}
    raw = storage._read_data(fd, "manifest.json", 131072)
    pdf = storage._read_data(fd, "candidate.pdf", 16777216)
    marker_raw = storage._read_data(fd, "publication.json", 1024)
    marker = _loads(marker_raw, 1024)
    _require(set(marker) == {"schema_version", "state", "manifest_sha256"}
             and marker["schema_version"] == "1.0.0" and marker["state"] == "FINAL_SYNTHETIC_TECHNICAL"
             and marker["manifest_sha256"] == expected_manifest_sha256
             and sha256(raw).hexdigest() == expected_manifest_sha256)
    manifest = _loads(raw, 131072)
    _validate_manifest(manifest)
    _require(verify_artifact_bytes(manifest["binding"]["artifact"], pdf).passed)
    if expected_binding is not None:
        _require(manifest["binding"] == expected_binding)
    for name, limit in limits.items():
        _require(storage._file_identity(os.stat(name, dir_fd=fd, follow_symlinks=False), limit) == before[name])
    _require(storage._read_data(fd, "manifest.json", 131072) == raw
             and storage._read_data(fd, "candidate.pdf", 16777216) == pdf
             and storage._read_data(fd, "publication.json", 1024) == marker_raw and set(os.listdir(fd)) == NAMES)
    return pdf, manifest


def publish_synthetic_release(*, release, directory, authority, requester_id):
    """Commit one synthetic artifact under a live, exact, synthetic grant."""
    try:
        _policy()
        _require(type(release) is BoundSyntheticRelease and type(authority) is SyntheticAuthorityContext)
        with authority._lock:
            binding = release._validate()
            grant = authority._check(binding, "publish", requester_id)
            manifest = {"schema_version": "1.0.0", "publication_state": "FINAL_SYNTHETIC_TECHNICAL",
                        "scope": SCOPE, "profile_id": PROFILE_ID, "binding": binding,
                        "binding_sha256": sha256(_canonical(binding)).hexdigest(),
                        "authorization_record": grant,
                        "authorization_record_sha256": sha256(_canonical(grant)).hexdigest(), **NONCLAIMS}
            _validate_manifest(manifest)
            raw = _canonical(manifest)
            _require(len(raw) <= 131072)
            pin = sha256(raw).hexdigest()
            artifact_id = binding["artifact"]["artifact_id"]
            try:
                with _artifact_directory(directory, artifact_id, create=True) as (fd, check):
                    storage._write_data(fd, "candidate.pdf", release._pdf_bytes, 16777216)
                    storage._write_data(fd, "manifest.json", raw, 131072)
                    os.fsync(fd)
                    check()
                    _policy(binding["profile_sha256"])
                    release._validate()
                    authority._check(binding, "publish", requester_id)
                    _commit_record(fd, _canonical({"schema_version": "1.0.0",
                        "state": "FINAL_SYNTHETIC_TECHNICAL", "manifest_sha256": pin}))
                    check()
                    _read_complete(fd, pin, binding)
            except FileExistsError:
                with _artifact_directory(directory, artifact_id, create=False) as (fd, check):
                    _read_complete(fd, pin, binding)
                    check()
            _policy(binding["profile_sha256"])
            authority._check(binding, "publish", requester_id)
            return {"artifact_id": artifact_id, "manifest_sha256": pin, "manifest": manifest}
    except Exception:
        raise SyntheticReleaseError("synthetic_publication_refused") from None


def read_synthetic_release(*, directory, artifact_id, expected_manifest_sha256, authority, requester_id):
    """Read exact committed bytes under a currently live synthetic access grant."""
    try:
        _policy()
        _require(type(authority) is SyntheticAuthorityContext)
        with authority._lock:
            _require(not authority._revoked and sha256(authority._record).hexdigest() == authority._pin)
            grant = _loads(authority._record, 8192)
            _schema(grant, "authority")
            _require(grant["requester_id"] == requester_id and grant["artifact_id"] == artifact_id
                     and "read" in grant["actions"])
            with _artifact_directory(directory, artifact_id, create=False) as (fd, check):
                pdf, manifest = _read_complete(fd, expected_manifest_sha256)
                binding = manifest["binding"]
                _require(binding["artifact"]["artifact_id"] == artifact_id)
                authority._check(binding, "read", requester_id)
                _policy(binding["profile_sha256"])
                check()
                authority._check(binding, "read", requester_id)
                return pdf, manifest
    except Exception:
        raise SyntheticReleaseError("synthetic_readback_refused") from None
