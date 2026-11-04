"""Presentation of coverage results without filesystem side effects."""

from .analysis import _kind, howto
from .storage import REPORT


def render_screen(r):
    total = sum(len(d.outcomes()) for d in r["decs"])
    done = total - len(r["missing"])
    pct = 100.0 * done / total if total else 0.0
    out = [
        "실행 전 분기 커버리지 (decision coverage 추정)",
        "-" * 60,
        f"함수 {len(r['funcs'])}개  결정문 {len(r['decs'])}개  분기 결과 {total}개  채움 {done}개 ({pct:.1f}%)",
        f"TC {len(r['tcs'])}개  표기 줄 {r['lines']}개  코드와 맞춘 줄 {r['matched']}개",
        "",
    ]
    miss = r["missing"]
    out.append(f"못 채운 분기 {len(miss)}개" + ("  (L=줄 번호)" if miss else ""))
    for ln, d, o in miss[:18]:
        out.append(f"  L{ln:<6} {_kind(d, o):<8} {'' if d.kind == 'switch' else o}")
    if len(miss) > 18:
        out.append(f"  … 외 {len(miss) - 18}개는 상세 파일")
    out += ["", f"경고 {len(r['warns'])}개"]
    for n, dno, msg, _ in r["warns"][:8]:
        out.append(f"  TC{n:<3} D{dno:<3} {msg}")
    if len(r["warns"]) > 8:
        out.append(f"  … 외 {len(r['warns']) - 8}개는 상세 파일")
    out += ["", f"상세(조건문 포함, 밖으로 보내지 말 것): %TEMP%\\{REPORT}"]
    return "\n".join(out)


def render_report(r):
    out = ["# 분기별 채운 TC (* = 직접 적지 않았지만 구조상 따라 나온 결과)", ""]
    for d in r["decs"]:
        out.append(
            f"L{d.line} {d.kind} ({' '.join(d.cond.split())})"
            + ("  [반복문 안]" if d.in_loop else "")
        )
        for o in d.outcomes():
            hit = r["cov"].get((d, o))
            out.append(f"    {o:<12} {', '.join(hit) if hit else '-- 못 채움'}")
    out += ["", "# 못 채운 분기 채우는 법"]
    for _, d, o in r["missing"]:
        out += howto(d, o, r["info"]) + [""]
    out += ["# 경고"]
    for n, dno, msg, detail in r["warns"]:
        out.append(f"TC{n} D{dno} {msg}" + (f"  |  {detail}" if detail else ""))
    out += ["", "# TC 순번과 이름"]
    for n, tc in enumerate(r["tcs"], 1):
        out.append(f"TC{n} = {tc['name'] or '(이름 없음)'}")
    return "\n".join(out) + "\n"
