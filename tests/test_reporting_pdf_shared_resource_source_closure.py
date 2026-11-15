"""Guard the exact source closure shared by opt-in PDF profiles."""
from hashlib import sha256
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SHARED_RESOURCE_SOURCE = "reporting/pdf_raster_resources.py"
DEPENDENT_MANIFESTS = {
    "reporting/pdf-bound-raster-candidate.v1.json": SHARED_RESOURCE_SOURCE,
    "reporting/pdf-final-document-request.v1.json": SHARED_RESOURCE_SOURCE,
    "reporting/pdf-typed-observation.v1.json": SHARED_RESOURCE_SOURCE,
    "reporting/pdf-raster-render.v1.json": "pdf_raster_resources.py",
}


class PdfSharedResourceSourceClosureTests(unittest.TestCase):
    def test_pinned_resource_source_matches_actual_repository_bytes(self):
        source = ROOT / SHARED_RESOURCE_SOURCE
        expected = sha256(source.read_bytes()).hexdigest()
        for manifest_name, source_key in DEPENDENT_MANIFESTS.items():
            with self.subTest(profile=manifest_name):
                manifest = json.loads((ROOT / manifest_name).read_bytes())
                self.assertEqual(
                    manifest["source_sha256"][source_key], expected,
                    "dependent PDF profile source closure is stale",
                )


if __name__ == "__main__":
    unittest.main()
