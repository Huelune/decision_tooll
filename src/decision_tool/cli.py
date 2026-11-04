"""Command-line entry point."""

import argparse
import os
import tempfile
import traceback

from .analysis import analyze, parse, strip_code
from .reports import render_report, render_screen
from .storage import REPORT, headers_near, read_text


def main(argv=None):
    ap = argparse.ArgumentParser(description="실행 전 분기 커버리지 추정 (C)")
    ap.add_argument("c_file")
    ap.add_argument("tc_file", nargs="?", help="없으면 TC 없이 분기만 센다")
    ap.add_argument("--func", help="이 함수만 본다")
    ap.add_argument("--csv", help="경계값 TC 입력값을 이 CSV로 저장 (TC 표기는 상세 파일에)")
    a = ap.parse_args(argv)
    rpt = os.path.join(tempfile.gettempdir(), REPORT)
    try:
        src = read_text(a.c_file)
    except OSError:
        print("오류: C 파일을 열 수 없음 — 경로를 확인하세요")
        return 1
    try:
        tc_text = read_text(a.tc_file) if a.tc_file else ""
    except OSError:
        print("오류: TC 표기 파일을 열 수 없음 — 경로를 확인하세요")
        return 1
    try:
        parsed = parse(strip_code(src), a.func)
        r = analyze("", tc_text, parsed=parsed)
        extra = make_bva(parsed, [src] + headers_near(a.c_file), a.csv) if a.csv else None
    except LookupError:
        print("오류: --func 로 준 함수를 C 파일에서 못 찾음 — 함수 이름을 확인하세요")
        return 1
    except OSError:
        print("오류: CSV 파일을 쓸 수 없음 — 엑셀에서 열려 있지 않은지, 폴더가 있는지 확인하세요")
        return 1
    except Exception:
        with open(rpt, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        print("오류: 코드 구조 해석 실패 — 괄호 짝, #ifdef 안의 중괄호를 확인하세요")
        print(f"상세: %TEMP%\\{REPORT}")
        return 1
    with open(rpt, "w", encoding="utf-8") as f:
        f.write(render_report(r) + (extra[2] if extra else ""))
    print(render_screen(r))
    if extra:
        print(
            f"경계값 TC {extra[0]}개 → CSV 저장 / 자동으로 못 만든 분기 {extra[1]}개 (이유와 TC 표기는 상세 파일)"
        )
    return 0


def make_bva(parsed, srcs, csv_path):
    """경계값 TC를 만들어 CSV로 쓴다 → (TC 수, 수동 분기 수, 상세 보고서에 붙일 글)."""
    from . import boundary as bva

    m = bva.Model(parsed, srcs)
    tcs, manual = m.generate()
    m.write_csv(csv_path, tcs)
    text = ["", "# 경계값 TC (CSV와 같은 순서)", m.tc_text(tcs), "# 자동으로 못 만든 분기"]
    text += [f"L{d.line_of(o)} {d.kind} {o}: {why}" for (d, o), why in manual.items()]
    return len(tcs), len(manual), "\n".join(text) + "\n"
