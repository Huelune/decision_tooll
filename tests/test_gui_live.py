import os
import tempfile
import unittest
from unittest.mock import patch

import legacy_suite

from decision_tool import gui


@unittest.skipUnless(os.name == "nt", "Windows desktop smoke test")
class WindowsGui(unittest.TestCase):
    def test_existing_gui_workflow(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(tempfile, "tempdir", directory),
        ):
            with patch.object(
                gui.messagebox, "showerror", side_effect=AssertionError("GUI error dialog")
            ):
                legacy_suite.test_gui()
