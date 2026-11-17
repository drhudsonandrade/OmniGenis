"""Runtime opt-out must enforce the published bounded adapter registry.

Owned synthetic inputs; no standards expansion, source fetch or clinical claim.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import json
import unittest

from interoperability.fhir import project_genomic_report
from interoperability.phenopackets import project_phenopacket
from interoperability.vrs import project_vrs_allele

ROOT=Path(__file__).resolve().parents[1]
REGISTRY=ROOT/'interoperability/profiles.v1.json'
PROFILE_FUNCS={
    'fhir-genomics-reporting-r4': (project_genomic_report, dict(
        report_id='SYNTHETIC-REPORT-01',subject_reference=None)),
    'ga4gh-phenopackets-v2': (project_phenopacket, dict(
        phenopacket_id='SYNTHETIC-PACKET-01',subject_id='SYNTHETIC-SUBJECT-01',
        genomic_file_uri='urn:omnigenis:artifact:sha256:abc123',
        genomic_file_format='vcf',genome_assembly='GRCh38',
        canonical_sample_id='SYNTHETIC-SAMPLE-01',created='2026-10-02T12:00:00Z')),
    'ga4gh-vrs-v2': (project_vrs_allele, dict(
        contig_accession='NC_000001.11',position_1_based=101,ref='A',alt='G',
        refget_accession='SQ.example',bound_contig_accession='NC_000001.11')),
}

@contextmanager
def mock_registry_bytes(raw: bytes | None):
    original=Path.read_bytes
    def read(path):
        if path==REGISTRY:
            if raw is None:
                raise FileNotFoundError('isolated synthetic registry-unavailable test')
            return raw
        return original(path)
    with patch.object(Path,'read_bytes',read):
        yield

class Rpt11RuntimeProfileDisableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry=json.loads(REGISTRY.read_text())
        cls.raw=REGISTRY.read_bytes()
        cls.assert_registry=REGISTRY.resolve()

    def test_exact_reviewed_profiles_are_usable_under_active_registry(self):
        with mock_registry_bytes(self.raw):
            for profile_id,(project,kwargs) in PROFILE_FUNCS.items():
                with self.subTest(profile=profile_id):
                    result=project(**kwargs)
                    self.assertTrue(result.passed,result.errors)
                    self.assertIsNotNone(result.payload)

    def test_disabled_registry_profile_prevents_runtime_projection_without_partial_payload(self):
        for profile_id,(project,kwargs) in PROFILE_FUNCS.items():
            manifest=deepcopy(self.registry)
            for p in manifest['profiles']:
                if p['profile_id']==profile_id:
                    p['adapter_enabled']=False
                    p['activation_state']='DISABLED'
            raw=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
            with self.subTest(profile=profile_id),mock_registry_bytes(raw):
                result=project(**kwargs)
                self.assertFalse(result.passed)
                self.assertEqual(result.errors,('adapter_profile_not_active',))
                self.assertIsNone(result.payload)
                self.assertIsNone(result.payload_sha256)

    def test_disabling_one_profile_does_not_disable_other_admitted_profiles(self):
        manifest=deepcopy(self.registry)
        for profile in manifest['profiles']:
            if profile['profile_id']=='ga4gh-phenopackets-v2':
                profile['adapter_enabled']=False
                profile['activation_state']='DISABLED'
        raw=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
        with mock_registry_bytes(raw):
            self.assertTrue(PROFILE_FUNCS['fhir-genomics-reporting-r4'][0](
                **PROFILE_FUNCS['fhir-genomics-reporting-r4'][1]).passed)
            self.assertTrue(PROFILE_FUNCS['ga4gh-vrs-v2'][0](
                **PROFILE_FUNCS['ga4gh-vrs-v2'][1]).passed)
            blocked=PROFILE_FUNCS['ga4gh-phenopackets-v2'][0](
                **PROFILE_FUNCS['ga4gh-phenopackets-v2'][1])
            self.assertFalse(blocked.passed)
            self.assertIsNone(blocked.payload)

    def test_inconsistent_activation_state_fails_closed(self):
        manifest=deepcopy(self.registry)
        manifest['profiles'][0]['adapter_enabled']=False
        # An enabled activation state with adapter_enabled=false is contradictory.
        raw=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
        with mock_registry_bytes(raw):
            result=project_genomic_report(report_id='SYNTHETIC-REPORT-01',subject_reference=None)
        self.assertFalse(result.passed)
        self.assertEqual(result.errors,('adapter_profile_not_active',))

    def test_unreviewed_standard_or_scientific_profile_version_stays_disabled(self):
        manifest=deepcopy(self.registry)
        for field,value in (("standard_version","unreviewed-999"),
                            ("scientific_semantics","EXPANDED_UNREVIEWED_CAPABILITY"),
                            ("license","UNKNOWN_RIGHTS")):
            changed=deepcopy(manifest)
            changed['profiles'][0][field]=value
            raw=json.dumps(changed,sort_keys=True,separators=(',',':')).encode()
            with self.subTest(field=field),mock_registry_bytes(raw):
                result=project_genomic_report(report_id='SYNTHETIC-REPORT-01',
                                              subject_reference=None)
                self.assertFalse(result.passed)
                self.assertEqual(result.errors,('adapter_profile_not_active',))

    def test_missing_malformed_or_duplicate_registry_fails_closed(self):
        case=PROFILE_FUNCS['ga4gh-vrs-v2']
        duplicated=self.raw.replace(b'"registry_id":',b'"registry_id":"dup","registry_id":',1)
        for raw in (None,b'',b'not-json',b'\xff',duplicated,self.raw+b'{}'):
            with self.subTest(data=repr(raw)[:40]),mock_registry_bytes(raw):
                result=case[0](**case[1])
                self.assertFalse(result.passed)
                self.assertEqual(result.errors,('adapter_profile_not_active',))
                self.assertIsNone(result.payload)

    def test_unknown_profile_does_not_activate_by_injecting_unapproved_entry(self):
        manifest=deepcopy(self.registry)
        extra=deepcopy(manifest['profiles'][0])
        extra['profile_id']='unapproved-scientific-profile'
        extra['adapter_module']='interoperability.fhir'
        manifest['profiles'].append(extra)
        raw=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
        with mock_registry_bytes(raw):
            result=project_genomic_report(report_id='SYNTHETIC-REPORT-01',subject_reference=None)
        self.assertFalse(result.passed)
        self.assertEqual(result.errors,('adapter_profile_not_active',))

if __name__=='__main__':unittest.main()
