import contextlib
import io
import tempfile
import unittest
from unittest.mock import patch

import legacy_suite


class ExistingBehavior(unittest.TestCase):
    def test_existing_analysis_cli_and_boundary_suite(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(tempfile, "tempdir", directory),
        ):
            with contextlib.redirect_stdout(io.StringIO()):
                legacy_suite.test(include_gui=False)
