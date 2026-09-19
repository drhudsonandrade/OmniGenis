from __future__ import annotations

import unittest

from scripts.validate_stage4_sbom import (
    RETIRED,
    SCANNER_EXPECTED,
    _is_pypi_record,
    _normalize_python_name,
    _python_lock_versions,
    _validate_image_identity,
    _validate_required_components,
)


class Stage4SbomValidatorTest(unittest.TestCase):
    def test_micromamba_pypi_records_are_classified_separately(self) -> None:
        self.assertTrue(
            _is_pypi_record(
                {
                    "name": "pypdfium2",
                    "version": "5.13.0",
                    "channel": "pypi",
                    "base_url": "https://pypi.org/",
                    "build_string": "pypi_0",
                }
            )
        )
        self.assertFalse(
            _is_pypi_record(
                {
                    "name": "samtools",
                    "version": "1.24",
                    "channel": "bioconda",
                    "base_url": "https://conda.anaconda.org/bioconda",
                }
            )
        )

    def test_python_package_names_use_pep503_normalization(self) -> None:
        self.assertEqual(_normalize_python_name("typing_extensions"), "typing-extensions")
        self.assertEqual(_normalize_python_name("Typing.Extensions"), "typing-extensions")

    def test_python_lock_versions_preserve_exact_runtime_versions(self) -> None:
        payload = {
            "packages": [
                {"name": "pypdfium2", "version": "5.13.0"},
                {"name": "reportlab", "version": "4.4.9"},
            ]
        }
        self.assertEqual(
            _python_lock_versions(payload),
            {"pypdfium2": "5.13.0", "reportlab": "4.4.9"},
        )

    def test_poppler_utils_is_explicitly_retired(self) -> None:
        self.assertIn("poppler-utils", RETIRED)

    def test_required_components_fail_closed_per_format(self) -> None:
        versions = {
            name: {version}
            for name, version in SCANNER_EXPECTED.items()
        }
        errors: list[str] = []
        _validate_required_components("SPDX", versions, errors)
        self.assertEqual(errors, [])

        missing = dict(versions)
        missing.pop("pypdfium2")
        errors = []
        _validate_required_components("SPDX", missing, errors)
        self.assertTrue(
            any("required SPDX runtime component missing" in error for error in errors),
            errors,
        )

    def test_cross_format_image_identity_accepts_one_image(self) -> None:
        digest = "sha256:" + "a" * 64
        source = {
            "name": "omnigenis-genome",
            "version": "deadbeef",
            "metadata": {"manifestDigest": digest},
        }
        spdx_packages = [
            {
                "SPDXID": "SPDXRef-DocumentRoot-Image-omnigenis-genome",
                "name": "omnigenis-genome",
                "versionInfo": "deadbeef",
                "externalRefs": [
                    {
                        "referenceType": "purl",
                        "referenceLocator": (
                            "pkg:oci/omnigenis-genome@sha256%3A"
                            + "a" * 64
                            + "?tag=deadbeef"
                        ),
                    }
                ],
            }
        ]
        cdx = {
            "metadata": {
                "component": {
                    "type": "container",
                    "name": "omnigenis-genome",
                    "version": "deadbeef",
                }
            }
        }
        errors: list[str] = []
        _validate_image_identity(source, spdx_packages, cdx, errors)
        self.assertEqual(errors, [])

    def test_cross_format_image_identity_rejects_incomplete_sha256(self) -> None:
        source = {
            "name": "omnigenis-genome",
            "version": "candidate-a",
            "metadata": {"manifestDigest": "sha256:"},
        }
        errors: list[str] = []
        _validate_image_identity(source, [], {}, errors)
        self.assertIn("Syft image identity is incomplete", errors)

    def test_cross_format_image_identity_rejects_mismatch(self) -> None:
        digest = "sha256:" + "b" * 64
        source = {
            "name": "omnigenis-genome",
            "version": "candidate-a",
            "metadata": {"manifestDigest": digest},
        }
        spdx_packages = [
            {
                "SPDXID": "SPDXRef-DocumentRoot-Image-omnigenis-genome",
                "name": "omnigenis-genome",
                "versionInfo": "candidate-a",
                "externalRefs": [],
            }
        ]
        cdx = {
            "metadata": {
                "component": {
                    "type": "container",
                    "name": "omnigenis-genome",
                    "version": "candidate-b",
                }
            }
        }
        errors: list[str] = []
        _validate_image_identity(source, spdx_packages, cdx, errors)
        self.assertTrue(
            any("manifest digest" in error for error in errors),
            errors,
        )
        self.assertTrue(
            any("CycloneDX image identity differs" in error for error in errors),
            errors,
        )


if __name__ == "__main__":
    unittest.main()
