"""Compatibility command: uv run python branch_cov.py ..."""

from decision_tool.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
