"""Validate new commit subjects without installing a Git hook."""

import re
import subprocess
import sys

SUBJECT = re.compile(r"^(feat|fix|refactor|test|docs|build|ci|chore)(\([a-z0-9_-]+\))?!?: \S.*$")


def main():
    if len(sys.argv) != 2 or sys.argv[1].startswith("-"):
        print("Usage: check_commits.py <base>..HEAD", file=sys.stderr)
        return 2
    result = subprocess.run(
        ["git", "log", "--no-merges", "--format=%s", sys.argv[1], "--"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    invalid = [line for line in result.stdout.splitlines() if not SUBJECT.fullmatch(line)]
    for subject in invalid:
        print(f"Invalid commit subject: {subject}", file=sys.stderr)
    return int(bool(invalid))


if __name__ == "__main__":
    raise SystemExit(main())
