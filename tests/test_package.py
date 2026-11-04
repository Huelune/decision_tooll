import subprocess
import sys
import tempfile
import unittest


class InstalledPackage(unittest.TestCase):
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
