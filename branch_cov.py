"""실행 전 분기 커버리지 추정기 (C, decision coverage).

사용: python branch_cov.py <C 파일> <TC 표기 파일> [--func 함수이름]

TC 표기 파일: 빈 줄로 TC를 나눈다. 한 줄은 `D1 [T] if(a==1)` 형식
(if / else if / elseif / else / case / default / while / for).
블록 첫 줄이 이 형식이 아니면 TC 이름으로 본다.

화면에는 개수·줄 번호·TC 순번만 찍는다. 조건문 원문은 %TEMP%\\branch_cov_report.txt 에만 쓴다.
결과 표의 TC1* 처럼 * 가 붙은 것은 직접 적지 않았지만 구조상 따라 나온 결과다.
"""
import argparse
import bisect
import os
import re
import sys
import tempfile
import traceback

REPORT = 'branch_cov_report.txt'
TERMINAL = {'break', 'continue', 'return', 'goto'}
KEYKIND = {'if': 'if', 'elseif': 'if', 'while': 'while', 'dowhile': 'while', 'for': 'for'}
TC_LINE = re.compile(r'^\s*(?:D\s*(\d+)\s*)?\[\s*([TFtf])\s*\]\s*(.+?)\s*$')


def norm(s):
    """공백을 없애고 전체를 감싼 괄호를 벗긴다."""
    s = re.sub(r'\s+', '', s)
    while s.startswith('(') and _match_end(s) == len(s) - 1:
        s = s[1:-1]
    return s


def _match_end(s):
    d = 0
    for i, c in enumerate(s):
        d += c == '('
        d -= c == ')'
        if d == 0:
            return i
    return -1


def strip_code(src):
    """주석·문자열·문자·전처리 줄을 공백으로 바꾼다(줄 번호 유지)."""
    pat = r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|^[ \t]*#(?:\\\n|[^\n])*'
    return re.sub(pat, lambda m: re.sub(r'[^\n]', ' ', m.group()), src, flags=re.S | re.M)


class Dec:
    def __init__(self, kind, cond, line, parent, in_loop, pos):
        self.kind, self.cond, self.line = kind, cond, line
        self.parent = parent      # (Dec, 결과): 이 결정문이 평가되려면 필요한 바깥 결과
        self.in_loop, self.pos = in_loop, pos
        self.next = None          # else if 사슬의 다음 결정문
        self.labels = []          # switch: [(결과 이름, 줄)]

    def outcomes(self):
        return [lab for lab, _ in self.labels] if self.kind == 'switch' else ['T', 'F']

    def line_of(self, o):
        return dict(self.labels).get(o, self.line)


def parse(clean, func=None):
    """결정문 목록과 TC 매칭용 색인 {(종류, 조건): [(위치, Dec, 결과)]}을 만든다."""
    toks = [(m.group(), m.start()) for m in re.finditer(r'\w+|\S', clean)]
    nls = [m.start() for m in re.finditer('\n', clean)]
    decs, keys, funcs = [], {}, []

    def T(k):
        return toks[k][0] if 0 <= k < len(toks) else ''

    def line(k):
        return bisect.bisect_right(nls, toks[k][1]) + 1

    def close(k):
        d = 0
        for j in range(k, len(toks)):
            c = toks[j][0]
            if c in ('(', '[', '{'):
                d += 1
            elif c in (')', ']', '}'):
                d -= 1
                if d == 0:
                    return j
        raise SyntaxError('bracket')

    def paren(k):
        if T(k) != '(':
            raise SyntaxError('paren')
        j = close(k)
        return clean[toks[k][1] + 1:toks[j][1]], j + 1

    def index(key, k, d, o):
        keys.setdefault(key, []).append((k, d, o))

    def add(kind, cond, at, parent, loop):
        d = Dec(kind, cond, line(at), parent, loop, at)
        decs.append(d)
        if kind in KEYKIND:
            index((KEYKIND[kind], norm(cond)), at, d, None)
        return d

    def skip_to_semi(k):
        while T(k) not in (';', '}'):
            if T(k) == '':
                raise SyntaxError('eof')
            k = close(k) + 1 if T(k) in ('(', '[', '{') else k + 1
        return k + (T(k) == ';')

    def stmt(k, parent, loop):
        t = T(k)
        if t == '{':
            k, last = k + 1, None
            while T(k) != '}':
                if T(k) == '':
                    raise SyntaxError('eof')
                k, last = stmt(k, parent, loop)
            return k + 1, last
        if t == 'if':
            cond, k2 = paren(k + 1)
            d = add('if', cond, k, parent, loop)
            k = stmt(k2, (d, 'T'), loop)[0]
            while T(k) == 'else':
                if T(k + 1) == 'if':
                    cond, k2 = paren(k + 2)
                    e = add('elseif', cond, k + 1, (d, 'F'), loop)
                    d.next, d = e, e
                    k = stmt(k2, (d, 'T'), loop)[0]
                else:
                    index(('else', ''), k, d, 'F')
                    k = stmt(k + 1, (d, 'F'), loop)[0]
                    break
            return k, None
        if t == 'switch':
            at = k
            cond, k = paren(k + 1)
            d = add('switch', cond, at, parent, loop)
            if T(k) != '{':
                return stmt(k, parent, loop)
            k, last, group, cur, fresh = k + 1, 'start', [], parent, True
            while T(k) != '}':
                if T(k) in ('case', 'default'):
                    j = k + 1
                    while T(j) != ':':
                        if T(j) == '':
                            raise SyntaxError('eof')
                        j += 1
                    if T(k) == 'default':
                        lab, key = 'default', ('default', '')
                    else:
                        v = norm(clean[toks[k + 1][1]:toks[j][1]])
                        lab, key = 'case ' + v, ('case', v)
                    d.labels.append((lab, line(k)))
                    index(key, k, d, lab)
                    if not group:  # 앞 case에서 흘러들어오면(fall-through) 어느 case인지 모른다
                        fresh = last in TERMINAL or last == 'start'
                    group.append(lab)
                    k = j + 1
                    continue
                if group:
                    cur = (d, group[0]) if len(group) == 1 and fresh else parent
                    group = []
                k, last = stmt(k, cur, loop)
            return k + 1, None
        if t == 'while':
            cond, k2 = paren(k + 1)
            d = add('while', cond, k, parent, loop)
            return stmt(k2, (d, 'T'), True)[0], None
        if t == 'for':
            j = close(k + 1)
            parts = clean[toks[k + 1][1] + 1:toks[j][1]].split(';')
            mid = parts[1] if len(parts) == 3 else ''
            d = add('for', mid, k, parent, loop) if mid.strip() else None
            return stmt(j + 1, (d, 'T') if d else parent, True)[0], None
        if t == 'do':
            k = stmt(k + 1, parent, True)[0]
            if T(k) != 'while':
                raise SyntaxError('do')
            cond, k2 = paren(k + 1)
            add('dowhile', cond, k, parent, loop)
            return k2 + (T(k2) == ';'), None
        if t in TERMINAL:
            return skip_to_semi(k), t
        if t == ';':
            return k + 1, None
        if re.fullmatch(r'[A-Za-z_]\w*', t) and T(k + 1) == ':':  # goto 레이블
            return stmt(k + 2, parent, loop)
        return skip_to_semi(k), None

    k = 0
    while k < len(toks):
        if T(k) != '{':
            k += 1
            continue
        j = close(k)
        if T(k - 1) == ')':  # 함수 본문
            d, m = 0, k - 1
            for m in range(k - 1, -1, -1):
                d += (T(m) == ')') - (T(m) == '(')
                if d == 0:
                    break
            name = T(m - 1)
            if func is None or name == func:
                funcs.append(name)
                stmt(k, None, False)
        k = j + 1
    if func is not None and not funcs:
        raise LookupError('func')
    return decs, keys, funcs


def tc_key(text):
    s = text.strip().rstrip('{').strip().rstrip(';').strip()
    m = re.fullmatch(r'(?:else\s*if|elseif|if)\s*(\(.*\))', s)
    if m:
        return 'if', norm(m.group(1))
    m = re.fullmatch(r'while\s*(\(.*\))', s)
    if m:
        return 'while', norm(m.group(1))
    m = re.fullmatch(r'for\s*\((.*)\)', s)
    if m:
        parts = m.group(1).split(';')
        return 'for', norm(parts[1] if len(parts) == 3 else m.group(1))
    m = re.fullmatch(r'case\s+(.+?)\s*:?', s)
    if m:
        return 'case', norm(m.group(1))
    if re.fullmatch(r'default\s*:?', s):
        return 'default', ''
    if s == 'else':
        return 'else', ''
    return None


def read_tcs(text):
    tcs = []
    for block in re.split(r'\n\s*\n', text.strip()):
        lines = [x for x in block.splitlines() if x.strip()]
        tc = {'name': '', 'items': [], 'bad': []}
        for i, x in enumerate(lines):
            m = TC_LINE.match(x)
            if m:
                tc['items'].append((m.group(1) or '?', m.group(2).upper(), m.group(3)))
            elif i == 0:
                tc['name'] = x.strip()
            else:
                tc['bad'].append(x.strip())
        if tc['items'] or tc['bad']:
            tcs.append(tc)
    return tcs


def template(d, o):
    """못 채운 분기 (d, o)를 TC 표기 줄의 분기 부분으로 만든다. 결과 [T]/[F]는 따로 붙인다."""
    if d.kind == 'switch':
        return o + ':'
    c = ' '.join(d.cond.split())
    if d.kind == 'for':
        return f'for(;{c};)'
    return f"{'while' if d.kind == 'dowhile' else d.kind}({c})"


def analyze(c_src, tc_text, func=None, parsed=None):
    decs, keys, funcs = parsed or parse(strip_code(c_src), func)
    tcs = read_tcs(tc_text)
    cov, warns, matched = {}, [], 0   # warns: (TC 순번, D번호, 화면용 문구, 상세용 원문)
    for n, tc in enumerate(tcs, 1):
        got, prev = {}, -1

        def imply(p):
            while p:
                got.setdefault(p[0], {}).setdefault(p[1], False)
                p = p[0].parent

        for bad in tc['bad']:
            warns.append((n, '?', '줄 형식을 모름 (D번호 [T/F] 조건문)', bad))
        for dno, tf, text in tc['items']:
            key = tc_key(text)
            if key is None:
                warns.append((n, dno, '분기 종류를 모름 (if/else/case/default/while/for)', text))
                continue
            cands = keys.get(key, [])
            if not cands:
                warns.append((n, dno, '코드에서 못 찾음 (조건문 글자 확인)', text))
                continue
            after = [c for c in cands if c[0] > prev]
            if not after and len(cands) > 1:
                warns.append((n, dno, '같은 분기가 여러 곳, 순서로 못 가림 (첫 번째로 셈)', text))
            pos, d, o = after[0] if after else cands[0]
            prev, matched = pos, matched + 1
            outcome = tf if o is None else (o if tf == 'T' else None)  # case [F]는 평가만 됨
            if outcome:
                got.setdefault(d, {})[outcome] = True
            imply(d.parent)

        for d, outs in got.items():
            for o, explicit in outs.items():
                cov.setdefault((d, o), []).append(f'TC{n}' + ('' if explicit else '*'))
            if d.in_loop or d.kind in ('while', 'for', 'dowhile'):  # 반복하면 T·F 둘 다 나올 수 있다
                continue
            if len(outs) > 1:
                warns.append((n, '-', f'L{d.line} 결정문에 서로 다른 결과가 함께 적힘', ''))
            if 'F' in outs and d.next and d.next not in got:
                warns.append((n, '-', f'L{d.next.line} else if 결과가 빠짐 (앞이 F면 평가됨)', ''))

    missing = sorted(((d.line_of(o), d, o) for d in decs for o in d.outcomes() if (d, o) not in cov),
                     key=lambda m: m[0])
    return {'decs': decs, 'cov': cov, 'warns': warns, 'missing': missing, 'tcs': tcs,
            'funcs': funcs, 'matched': matched, 'lines': sum(len(t['items']) for t in tcs)}


def _kind(d, o):
    return o.split()[0] if d.kind == 'switch' else d.kind


def render_screen(r):
    total = sum(len(d.outcomes()) for d in r['decs'])
    done = total - len(r['missing'])
    pct = 100.0 * done / total if total else 0.0
    out = ['실행 전 분기 커버리지 (decision coverage 추정)', '-' * 60,
           f"함수 {len(r['funcs'])}개  결정문 {len(r['decs'])}개  분기 결과 {total}개  채움 {done}개 ({pct:.1f}%)",
           f"TC {len(r['tcs'])}개  표기 줄 {r['lines']}개  코드와 맞춘 줄 {r['matched']}개", '']
    miss = r['missing']
    out.append(f'못 채운 분기 {len(miss)}개' + ('  (L=줄 번호)' if miss else ''))
    for ln, d, o in miss[:18]:
        out.append(f'  L{ln:<6} {_kind(d, o):<8} {"" if d.kind == "switch" else o}')
    if len(miss) > 18:
        out.append(f'  … 외 {len(miss) - 18}개는 상세 파일')
    out += ['', f"경고 {len(r['warns'])}개"]
    for n, dno, msg, _ in r['warns'][:8]:
        out.append(f'  TC{n:<3} D{dno:<3} {msg}')
    if len(r['warns']) > 8:
        out.append(f"  … 외 {len(r['warns']) - 8}개는 상세 파일")
    out += ['', f'상세(조건문 포함, 밖으로 보내지 말 것): %TEMP%\\{REPORT}']
    return '\n'.join(out)


def render_report(r):
    out = ['# 분기별 채운 TC (* = 직접 적지 않았지만 구조상 따라 나온 결과)', '']
    for d in r['decs']:
        out.append(f"L{d.line} {d.kind} ({' '.join(d.cond.split())})" + ('  [반복문 안]' if d.in_loop else ''))
        for o in d.outcomes():
            hit = r['cov'].get((d, o))
            out.append(f"    {o:<12} {', '.join(hit) if hit else '-- 못 채움'}")
    out += ['', '# 경고']
    for n, dno, msg, detail in r['warns']:
        out.append(f'TC{n} D{dno} {msg}' + (f'  |  {detail}' if detail else ''))
    out += ['', '# TC 순번과 이름']
    for n, tc in enumerate(r['tcs'], 1):
        out.append(f"TC{n} = {tc['name'] or '(이름 없음)'}")
    return '\n'.join(out) + '\n'


def _read(path):
    raw = open(path, 'rb').read()
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        return raw.decode('cp949', errors='replace')


def main(argv=None):
    ap = argparse.ArgumentParser(description='실행 전 분기 커버리지 추정 (C)')
    ap.add_argument('c_file')
    ap.add_argument('tc_file')
    ap.add_argument('--func', help='이 함수만 본다')
    a = ap.parse_args(argv)
    os.system('cls' if os.name == 'nt' else 'clear')  # 고정 문자열이라 주입 위험 없음
    rpt = os.path.join(tempfile.gettempdir(), REPORT)
    try:
        src = _read(a.c_file)
    except OSError:
        print('오류: C 파일을 열 수 없음 — 경로를 확인하세요')
        return 1
    try:
        tc_text = _read(a.tc_file)
    except OSError:
        print('오류: TC 표기 파일을 열 수 없음 — 경로를 확인하세요')
        return 1
    try:
        r = analyze(src, tc_text, a.func)
    except LookupError:
        print('오류: --func 로 준 함수를 C 파일에서 못 찾음 — 함수 이름을 확인하세요')
        return 1
    except Exception:
        with open(rpt, 'w', encoding='utf-8') as f:
            f.write(traceback.format_exc())
        print('오류: 코드 구조 해석 실패 — 괄호 짝, #ifdef 안의 중괄호를 확인하세요')
        print(f'상세: %TEMP%\\{REPORT}')
        return 1
    with open(rpt, 'w', encoding='utf-8') as f:
        f.write(render_report(r))
    print(render_screen(r))
    return 0


if __name__ == '__main__':
    sys.exit(main())
