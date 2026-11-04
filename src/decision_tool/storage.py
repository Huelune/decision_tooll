"""Local input-file decoding and adjacent-header discovery."""

import glob
import os

REPORT = "branch_cov_report.txt"


def read_text(path):
    with open(path, "rb") as f:
        raw = f.read()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp949", errors="replace")


def headers_near(c_path):
    """C 파일과 같은 폴더의 .h (상수 값과 형을 찾는 데만 쓴다)."""
    out = []
    for h in glob.glob(os.path.join(os.path.dirname(os.path.abspath(c_path)), "*.h")):
        try:
            out.append(read_text(h))
        except OSError:
            pass
    return out
