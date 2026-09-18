from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]


class StrongCopyleftRuntimeCleanupTest(unittest.TestCase):
    def test_active_runtime_has_no_poppler_dependency_or_commands(self) -> None:
        active = (
            "environment.yml",
            "locks/runtime-lock.json",
            "scripts/runtime_stack.py",
            "scripts/check_versions.sh",
            "scripts/run_canary.sh",
            "reporting/template_v3.py",
        )
        prohibited = ("poppler", "pdftoppm", "pdftocairo")
        for relative in active:
            text = (ROOT / relative).read_text(encoding="utf-8").lower()
            with self.subTest(path=relative):
                for token in prohibited:
                    self.assertNotIn(token, text)

    def test_docx_background_renderer_uses_pdfium_at_print_resolution(self) -> None:
        from reporting.template_v3 import (
            DOCX_BACKGROUND_DPI,
            _render_template_pages_pdfium,
        )

        self.assertEqual(DOCX_BACKGROUND_DPI, 288)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pdf"
            writer = canvas.Canvas(str(source), pagesize=A4)
            writer.drawString(72, 760, "OmniGenis PDFium DOCX background test")
            writer.save()

            outputs = _render_template_pages_pdfium(source, root / "work", 1)
            self.assertEqual(len(outputs), 1)
            self.assertTrue(outputs[0].is_file())
            with Image.open(outputs[0]) as image:
                self.assertEqual(image.mode, "RGB")
                self.assertGreaterEqual(image.width, 2300)
                self.assertGreaterEqual(image.height, 3300)

    def test_docx_runtime_no_longer_uses_svg_patch(self) -> None:
        source = (ROOT / "reporting/template_v3.py").read_text(encoding="utf-8")
        self.assertNotIn("_patch_docx_svg", source)
        self.assertNotIn("template-v3-svg-docx", source)
        self.assertIn("template-v3-pdfium-raster-docx", source)

    def test_editorial_canary_and_version_gate_use_pdfium(self) -> None:
        canary = (ROOT / "scripts/run_canary.sh").read_text(encoding="utf-8").lower()
        versions = (ROOT / "scripts/check_versions.sh").read_text(encoding="utf-8").lower()
        self.assertIn("pypdfium2", canary)
        self.assertIn("pypdfium2", versions)
        self.assertNotIn("pdftoppm", canary)
        self.assertNotIn("pdftocairo", canary)
        self.assertNotIn("pdftoppm", versions)
        self.assertNotIn("pdftocairo", versions)


class Stage3ValidatorContractTest(unittest.TestCase):
    @staticmethod
    def _write_valid_surfaces(root: Path) -> None:
        payloads = {
            "environment.yml": "dependencies:\n  - python=3.11\n",
            "locks/runtime-lock.json": "{}\n",
            "scripts/runtime_stack.py": "MANAGED_RUNTIME_PACKAGES = ('python',)\n",
            "scripts/check_versions.sh": "check_python_package pypdfium2 '5.13.0'\n",
            "scripts/run_canary.sh": (
                "import pypdfium2 as pdfium\n"
                'payload = {"renderer": "PDFium"}\n'
            ),
            "reporting/template_v3.py": (
                "DOCX_BACKGROUND_DPI = 288\n"
                "def _render_template_pages_pdfium(): pass\n"
                "MODE = 'template-v3-pdfium-raster-docx'\n"
            ),
        }
        for relative, content in payloads.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        import hashlib
        import json

        tree = "1" * 40
        gates = {}
        for name in (
            "directed_tests",
            "repository_validator",
            "supply_chain_gate",
            "residual_language_gate",
            "full_test_suite",
        ):
            output_path = root / "docs/evidence/stage3/test" / f"{name}.log"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(f"PASS {name}\n", encoding="utf-8")
            gates[name] = {
                "status": "PASS",
                "command": f"test-command-{name}",
                "exit_code": 0,
                "output_path": output_path.relative_to(root).as_posix(),
                "output_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
                "tested_tree_sha": tree,
            }
        evidence = {
            "schema": "omnigenis-stage3-strong-copyleft-runtime-cleanup-v2",
            "status": "VERIFIED",
            "removed_runtime_component": {"name": "Poppler", "version": "26.07.0"},
            "replacement": {
                "wrapper": "pypdfium2",
                "version": "5.13.0",
                "backend": "PDFium",
                "docx_static_background_dpi": 288,
                "audited_linux_x86_64_wheel_sha256":
                    "81df25c1ab4c13ff773102d3cbea1967511d079123b067fc077bd0c4d57d91d8",
            },
            "verification": {
                "pre_attestation_tested_tree_sha": tree,
                "gates": gates,
            },
        }
        evidence_path = root / "docs/evidence/STRONG_COPYLEFT_RUNTIME_CLEANUP_2026-09-17.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    def test_stage3_validator_accepts_remediated_runtime(self) -> None:
        from scripts.validate_repo import validate_stage3_copyleft_contract

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_valid_surfaces(root)
            errors: list[str] = []
            validate_stage3_copyleft_contract(root, errors)
            self.assertEqual(errors, [])

    def test_stage3_validator_rejects_poppler_reintroduction(self) -> None:
        from scripts.validate_repo import validate_stage3_copyleft_contract

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_valid_surfaces(root)
            (root / "environment.yml").write_text(
                "dependencies:\n  - poppler=26.07.0\n", encoding="utf-8"
            )
            errors: list[str] = []
            validate_stage3_copyleft_contract(root, errors)
            self.assertTrue(any("reintroduced" in error for error in errors), errors)

    def test_stage3_validator_rejects_python_constructed_retired_command(self) -> None:
        from scripts.validate_repo import validate_stage3_copyleft_contract

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_valid_surfaces(root)
            (root / "reporting/template_v3.py").write_text(
                "DOCX_BACKGROUND_DPI = 288\n"
                "def _render_template_pages_pdfium(): pass\n"
                "MODE = 'template-v3-pdfium-raster-docx'\n"
                "import subprocess\n"
                "prefix = 'pdf'\n"
                "tool = prefix + 'toppm'\n"
                "subprocess.run([tool], check=True)\n",
                encoding="utf-8",
            )
            errors: list[str] = []
            validate_stage3_copyleft_contract(root, errors)
            self.assertTrue(any("constructed" in error for error in errors), errors)

    def test_stage3_validator_rejects_module_alias_unresolved_command(self) -> None:
        from scripts.validate_repo import validate_stage3_copyleft_contract

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_valid_surfaces(root)
            (root / "reporting/template_v3.py").write_text(
                "DOCX_BACKGROUND_DPI = 288\n"
                "def _render_template_pages_pdfium(): pass\n"
                "MODE = 'template-v3-pdfium-raster-docx'\n"
                "import os\n"
                "import subprocess as sp\n"
                "sp.run([os.environ['PDF_TOOL']], check=True)\n",
                encoding="utf-8",
            )
            errors: list[str] = []
            validate_stage3_copyleft_contract(root, errors)
            self.assertTrue(any("unresolved executable" in error for error in errors), errors)

    def test_stage3_validator_rejects_from_import_unresolved_command(self) -> None:
        from scripts.validate_repo import validate_stage3_copyleft_contract

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_valid_surfaces(root)
            (root / "reporting/template_v3.py").write_text(
                "DOCX_BACKGROUND_DPI = 288\n"
                "def _render_template_pages_pdfium(): pass\n"
                "MODE = 'template-v3-pdfium-raster-docx'\n"
                "import os\n"
                "from subprocess import run\n"
                "run([os.environ['PDF_TOOL']], check=True)\n",
                encoding="utf-8",
            )
            errors: list[str] = []
            validate_stage3_copyleft_contract(root, errors)
            self.assertTrue(any("unresolved executable" in error for error in errors), errors)

    def test_stage3_validator_rejects_shell_constructed_retired_command(self) -> None:
        from scripts.validate_repo import validate_stage3_copyleft_contract

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_valid_surfaces(root)
            (root / "scripts/run_canary.sh").write_text(
                "prefix=pdf\n"
                "suffix=toppm\n"
                'tool="$prefix$suffix"\n'
                '"$tool" -v\n'
                "import pypdfium2 as pdfium\n"
                'payload={"renderer":"PDFium"}\n',
                encoding="utf-8",
            )
            errors: list[str] = []
            validate_stage3_copyleft_contract(root, errors)
            self.assertTrue(any("constructed" in error for error in errors), errors)

    def test_stage3_validator_rejects_unresolved_python_command(self) -> None:
        from scripts.validate_repo import validate_stage3_copyleft_contract

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_valid_surfaces(root)
            (root / "reporting/template_v3.py").write_text(
                "DOCX_BACKGROUND_DPI = 288\n"
                "def _render_template_pages_pdfium(): pass\n"
                "MODE = 'template-v3-pdfium-raster-docx'\n"
                "import os, subprocess\n"
                "subprocess.run([os.environ['PDF_TOOL']], check=True)\n",
                encoding="utf-8",
            )
            errors: list[str] = []
            validate_stage3_copyleft_contract(root, errors)
            self.assertTrue(any("unresolved executable" in error for error in errors), errors)

    def test_stage3_validator_rejects_missing_gate_output_artifact(self) -> None:
        import json

        from scripts.validate_repo import validate_stage3_copyleft_contract

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_valid_surfaces(root)
            evidence = root / "docs/evidence/STRONG_COPYLEFT_RUNTIME_CLEANUP_2026-09-17.json"
            payload = json.loads(evidence.read_text(encoding="utf-8"))
            record = payload["verification"]["gates"]["full_test_suite"]
            (root / record["output_path"]).unlink()
            evidence.write_text(json.dumps(payload), encoding="utf-8")
            errors: list[str] = []
            validate_stage3_copyleft_contract(root, errors)
            self.assertTrue(any("output artifact" in error for error in errors), errors)

    def test_stage3_validator_rejects_gate_output_hash_mismatch(self) -> None:
        import json

        from scripts.validate_repo import validate_stage3_copyleft_contract

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_valid_surfaces(root)
            evidence = root / "docs/evidence/STRONG_COPYLEFT_RUNTIME_CLEANUP_2026-09-17.json"
            payload = json.loads(evidence.read_text(encoding="utf-8"))
            record = payload["verification"]["gates"]["full_test_suite"]
            (root / record["output_path"]).write_text("tampered\n", encoding="utf-8")
            errors: list[str] = []
            validate_stage3_copyleft_contract(root, errors)
            self.assertTrue(any("output hash mismatch" in error for error in errors), errors)

    def test_stage3_validator_rejects_incomplete_gate_provenance(self) -> None:
        import json

        from scripts.validate_repo import validate_stage3_copyleft_contract

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_valid_surfaces(root)
            evidence = root / "docs/evidence/STRONG_COPYLEFT_RUNTIME_CLEANUP_2026-09-17.json"
            payload = json.loads(evidence.read_text(encoding="utf-8"))
            del payload["verification"]["gates"]["full_test_suite"]["output_sha256"]
            evidence.write_text(json.dumps(payload), encoding="utf-8")
            errors: list[str] = []
            validate_stage3_copyleft_contract(root, errors)
            self.assertTrue(any("provenance" in error for error in errors), errors)

    def test_stage3_validator_rejects_yaml_escaped_dependency(self) -> None:
        from scripts.validate_repo import validate_stage3_copyleft_contract

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_valid_surfaces(root)
            (root / "environment.yml").write_text(
                'dependencies:\n  - "pop\\u0070ler=26.07.0"\n',
                encoding="utf-8",
            )
            errors: list[str] = []
            validate_stage3_copyleft_contract(root, errors)
            self.assertTrue(any("structured surface" in error for error in errors), errors)

    def test_stage3_validator_rejects_obfuscated_structured_dependency(self) -> None:
        from scripts.validate_repo import validate_stage3_copyleft_contract

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_valid_surfaces(root)
            (root / "environment.yml").write_text(
                "dependencies:\n  - pop-pler=26.07.0\n", encoding="utf-8"
            )
            errors: list[str] = []
            validate_stage3_copyleft_contract(root, errors)
            self.assertTrue(any("structured surface" in error for error in errors), errors)

    def test_stage3_validator_rejects_missing_pdfium_contract(self) -> None:
        from scripts.validate_repo import validate_stage3_copyleft_contract

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_valid_surfaces(root)
            (root / "reporting/template_v3.py").write_text(
                "DOCX_BACKGROUND_DPI = 288\n", encoding="utf-8"
            )
            errors: list[str] = []
            validate_stage3_copyleft_contract(root, errors)
            self.assertTrue(any("PDFium DOCX contract missing" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
