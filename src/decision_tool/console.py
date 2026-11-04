"""Stable Unicode output for Windows consoles and redirected command output."""

import sys


def configure_output():
    # Windows pipes may default to cp1252 even when the terminal uses Unicode.
    # StringIO/test captures and pythonw's missing streams need no reconfiguration.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")
