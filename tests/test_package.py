import subprocess
import sys
import tempfile
import unittest


class InstalledPackage(unittest.TestCase):
    def test_help_is_utf8_even_with_legacy_windows_streams(self):
        for module, expected in [("cli", "실행 전"), ("gui", "TC 표기")]:
            with self.subTest(module=module), tempfile.TemporaryDirectory() as directory:
                code = (
                    "import importlib, sys; "
                    "sys.stdout.reconfigure(encoding='cp1252', errors='strict'); "
                    "sys.stderr.reconfigure(encoding='cp1252', errors='strict'); "
                    "sys.argv = ['decision-tool', '--help']; "
                    f"importlib.import_module('decision_tool.{module}').main()"
                )
                result = subprocess.run(
                    [sys.executable, "-I", "-c", code],
                    cwd=directory,
                    capture_output=True,
                    timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout.decode("utf-8"))

    def test_module_runs_outside_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-I", "-m", "decision_tool", "--help"],
                cwd=directory,
                capture_output=True,
                timeout=10,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"--func", result.stdout)
