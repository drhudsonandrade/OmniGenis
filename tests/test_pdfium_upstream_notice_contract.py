from __future__ import annotations

import gzip
import hashlib
import json
import tarfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
LICENSE_ROOT = ROOT / "licenses/pypdfium2-5.13.0"
NOTICE = LICENSE_ROOT / "NOTICE.md"
HASHES = LICENSE_ROOT / "UPSTREAM_LICENSES.sha256"


class PdfiumUpstreamNoticeContractTest(unittest.TestCase):
    def test_direct_upstream_attributions_are_explicit(self) -> None:
        text = NOTICE.read_text(encoding="utf-8")
        required = (
            "pypdfium2-team",
            "geisserml",
            "Copyright 2014 The PDFium Authors",
            "Copyright 2014-2025 Benoit Blanchon",
            "Apache-2.0 OR BSD-3-Clause",
            "CC-BY-4.0",
            "upstream/",
            "controlling notice",
        )
        for token in required:
            self.assertIn(token, text)
    def test_bundled_upstream_license_snapshot_is_hash_verified(self) -> None:
        lines = [
            line.strip()
            for line in HASHES.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(lines), 19)

        archive_manifest = (
            LICENSE_ROOT / "UPSTREAM_BUILD_LICENSES.sha256"
        ).read_text(encoding="utf-8").strip()
        archive_expected, archive_relative = archive_manifest.split("  ", 1)
        archive = ROOT / archive_relative
        self.assertTrue(archive.is_file(), archive_relative)
        self.assertEqual(
            hashlib.sha256(archive.read_bytes()).hexdigest(),
            archive_expected,
        )
        with tarfile.open(archive, mode="r:gz") as bundle:
            archived_members = [
                member for member in bundle.getmembers() if member.isfile()
            ]
            self.assertEqual(len(archived_members), 16)
            archived_names = [member.name for member in archived_members]
            self.assertEqual(len(set(archived_names)), len(archived_names))
            archived = {
                member.name: bundle.extractfile(member).read()
                for member in archived_members
            }

        prefix = "licenses/pypdfium2-5.13.0/upstream/"
        for line in lines:
            expected, relative = line.split("  ", 1)
            path = ROOT / relative
            if path.is_file():
                payload = path.read_bytes()
            else:
                self.assertTrue(relative.startswith(prefix), relative)
                member_name = relative[len(prefix):]
                self.assertIn(member_name, archived, member_name)
                payload = archived[member_name]
            self.assertEqual(hashlib.sha256(payload).hexdigest(), expected, relative)

    def test_every_upstream_license_manifest_entry_is_in_tracked_bundle(self) -> None:
        import subprocess

        tracked = set(
            subprocess.run(
                ["git", "ls-files"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
        )
        archive = "licenses/pypdfium2-5.13.0/upstream/BUILD_LICENSES.tar.gz"
        self.assertIn(archive, tracked)
        self.assertIn("licenses/pypdfium2-5.13.0/UPSTREAM_BUILD_LICENSES.sha256", tracked)
        for line in HASHES.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            _expected, relative = line.split("  ", 1)
            if "/upstream/data/" not in relative:
                self.assertIn(relative, tracked, relative)
        self.assertFalse(
            any(
                path.startswith("licenses/pypdfium2-5.13.0/upstream/data/")
                for path in tracked
            )
        )

    def test_upstream_package_metadata_is_hash_verified(self) -> None:
        manifest = {}
        for line in (LICENSE_ROOT / "UPSTREAM_METADATA.sha256").read_text(
            encoding="utf-8"
        ).splitlines():
            key, value = line.split("  ", 1)
            manifest[key] = value

        archive = ROOT / manifest["artifact"]
        self.assertTrue(archive.is_file(), manifest["artifact"])
        self.assertEqual(
            hashlib.sha256(archive.read_bytes()).hexdigest(),
            manifest["archive_sha256"],
        )
        raw = gzip.decompress(archive.read_bytes())
        self.assertEqual(
            hashlib.sha256(raw).hexdigest(),
            manifest["original_sha256"],
        )
        metadata = raw.decode("utf-8", errors="replace")
        self.assertIn("Author: pypdfium2-team", metadata)
        self.assertIn(
            "License: BSD-3-Clause, Apache-2.0, dependency licenses",
            metadata,
        )
        self.assertIn("SPDX-FileCopyrightText: 2026 geisserml", metadata)

    def test_stage3_evidence_binds_third_party_notice_snapshot(self) -> None:
        evidence = json.loads(
            (
                ROOT
                / "docs/evidence/STRONG_COPYLEFT_RUNTIME_CLEANUP_2026-09-17.json"
            ).read_text(encoding="utf-8")
        )
        notice = evidence["third_party_notice_evidence"]
        self.assertEqual(notice["status"], "VERIFIED")
        for path_key, hash_key in (
            ("notice_path", "notice_sha256"),
            ("upstream_license_manifest_path", "upstream_license_manifest_sha256"),
            ("upstream_metadata_manifest_path", "upstream_metadata_manifest_sha256"),
            ("third_party_notices_path", "third_party_notices_sha256"),
        ):
            path = ROOT / notice[path_key]
            self.assertTrue(path.is_file(), notice[path_key])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), notice[hash_key])
        self.assertEqual(notice["verbatim_upstream_license_files"], 19)

    def test_third_party_notice_points_to_verbatim_upstream_bundle(self) -> None:
        text = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.assertIn("licenses/pypdfium2-5.13.0/NOTICE.md", text)
        self.assertIn("UPSTREAM_LICENSES.sha256", text)
        self.assertIn("UPSTREAM_BUILD_LICENSES.sha256", text)
        self.assertIn("UPSTREAM_METADATA.sha256", text)
        self.assertIn("verbatim", text)


if __name__ == "__main__":
    unittest.main()
