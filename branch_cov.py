"""실행 전 분기 커버리지 추정기 (C, decision coverage).

사용: python branch_cov.py <C 파일> [TC 표기 파일] [--func 함수이름] [--csv 경계값TC.csv]
                          [--stub stub코드.c] [--stub-free] [--time 초] [--inc 헤더폴더] [--path N]

TC 표기 파일: 빈 줄로 TC를 나눈다. 한 줄은 `D1 [T] if(a==1)` 형식
(if / else if / elseif / else / case / default / while / for).
if / else if 사슬은 D 하나: 들어간 갈래를 [T] if(…)·[T] elseif(…)로, else(모두 F)는 [F] if(첫 조건)으로.
블록 첫 줄이 이 형식이 아니면 TC 이름으로 본다.

화면에는 개수·줄 번호·TC 순번만 찍는다. 조건문 원문은 %TEMP%\\branch_cov_report.txt 에만 쓴다.
결과 표의 TC1* 처럼 * 가 붙은 것은 직접 적지 않았지만 구조상 따라 나온 결과다.
"""
import argparse
import bisect
import glob
import os
import re
import sys
import tempfile
import time
import traceback

REPORT = 'branch_cov_report.txt'
VERSION = '2026-11-05.24'   # 화면 첫 줄에 찍어 어느 코드로 돌렸는지 가린다
TERMINAL = {'break', 'continue', 'return', 'goto'}
KEYKIND = {'if': 'if', 'elseif': 'if', 'while': 'while', 'dowhile': 'while', 'for': 'for'}
TC_LINE = re.compile(r'^\s*(?:D\s*(\d+)\s*)?\[\s*([TFtf](?:\s*/\s*[TFtf])?)\s*\]\s*(.+?)\s*$')  # [T] [F] [T/F]


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
        self.guards = ()          # 앞쪽 조기 반환을 피한 결과들 [(Dec, 결과)]
        self.func = None

    def reqs(self):
        """이 결정문이 평가되려면 필요한 결과들."""
        return ([self.parent] if self.parent else []) + list(self.guards)

    def outcomes(self):
        return [lab for lab, _ in self.labels] if self.kind == 'switch' else ['T', 'F']

    def line_of(self, o):
        return dict(self.labels).get(o, self.line)


def parse(clean, func=None):
    """결정문 목록과 TC 매칭용 색인 {(종류, 조건): [(위치, Dec, 결과)]}을 만든다."""
    toks = [(m.group(), m.start()) for m in re.finditer(r'\w+|\S', clean)]
    nls = [m.start() for m in re.finditer('\n', clean)]
    decs, keys, funcs = [], {}, []
    guards = []   # 지금 위치에서 유효한 조기 반환 회피 조건 (블록을 나가면 줄어든다)
    cur = {'func': None}
    sw = []       # 지금 들어 있는 switch 본문들: (switch 결정문, 여기까지 흘러올 수 있는 case들)
    # set: 이름 -> [(토큰 위치, 줄, 받은 stub 이름 또는 None, 연산, 오른쪽 식)]
    # stmts: 단순 문장 [(시작, 끝 토큰, (부모, 가드, 반복문 시작 위치 또는 False, switch 묶음))]
    # loops: 반복문 시작 위치 -> 끝 위치, locals: 함수 -> 선언한 지역 변수 이름들
    info = {'params': {}, 'body': {}, 'set': {}, 'stmts': [], 'loops': {}, 'locals': {}}

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
        d.guards, d.func = tuple(guards), cur['func']
        decs.append(d)
        if kind in KEYKIND:
            index((KEYKIND[kind], norm(cond)), at, d, None)
        return d

    def params(a, b):
        """함수 머리 ( … ) 안의 인자 이름들."""
        names, last, depth = set(), None, 0
        for k in range(a + 1, b + 1):
            t = T(k)
            if t in ('[', '('):
                depth += 1
            elif t in (']', ')') and depth:
                depth -= 1
            elif depth == 0 and t in (',', ')'):
                if last and last != 'void':
                    names.add(last)
                last = None
            elif depth == 0 and _is_ident(t):
                last = t
        return names

    def skip_to_semi(k):
        while T(k) not in (';', '}'):
            if T(k) == '':
                raise SyntaxError('eof')
            k = close(k) + 1 if T(k) in ('(', '[', '{') else k + 1
        return k + (T(k) == ';')

    def body(k, parent, loop):
        n = len(guards)
        r = stmt(k, parent, loop)
        del guards[n:]
        return r

    def stmt(k, parent, loop):
        t = T(k)
        if t == '{':
            k, last, n = k + 1, None, len(guards)
            while T(k) != '}':
                if T(k) == '':
                    raise SyntaxError('eof')
                k, last = stmt(k, parent, loop)
            del guards[n:]
            return k + 1, last
        if t == 'if':
            cond, k2 = paren(k + 1)
            d = add('if', cond, k, parent, loop)
            k, last = body(k2, (d, 'T'), loop)
            branches = [((d, 'T'), last in TERMINAL)]   # (갈래, return 등으로 끝나나)
            while T(k) == 'else':
                if T(k + 1) == 'if':
                    cond, k2 = paren(k + 2)
                    e = add('elseif', cond, k + 1, (d, 'F'), loop)
                    d.next, d = e, e
                    k, last = body(k2, (d, 'T'), loop)
                    branches.append(((d, 'T'), last in TERMINAL))
                else:
                    index(('else', ''), k, d, 'F')
                    k, last = body(k + 1, (d, 'F'), loop)
                    branches.append(((d, 'F'), last in TERMINAL))
                    break
            else:
                branches.append(((d, 'F'), False))
            go_on = [b for b, ends in branches if not ends]
            if len(go_on) == 1 and len(branches) > 1:   # 빠져나가는 갈래가 하나뿐이면 뒤 문장은 그 결과를 요구
                guards.append(go_on[0])
            return k, (None if go_on else 'return')
        if t == 'switch':
            at = k
            cond, k = paren(k + 1)
            d = add('switch', cond, at, parent, loop)
            if T(k) != '{':
                return stmt(k, parent, loop)
            k, last, group, cpar, fresh, n, live = k + 1, 'start', [], parent, True, len(guards), []
            while T(k) != '}':
                if T(k) in ('case', 'default'):
                    del guards[n:]
                    if not group and (last in TERMINAL or last == 'start'):
                        live = []
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
                    live.append(lab)
                    index(key, k, d, lab)
                    if not group:  # 앞 case에서 흘러들어오면(fall-through) 어느 case인지 모른다
                        fresh = last in TERMINAL or last == 'start'
                    group.append(lab)
                    k = j + 1
                    continue
                if group:
                    cpar = (d, group[0]) if len(group) == 1 and fresh else parent
                    group = []
                sw.append((d, tuple(live)))
                k, last = stmt(k, cpar, loop)
                sw.pop()
            del guards[n:]
            return k + 1, None
        if t == 'while':
            cond, k2 = paren(k + 1)
            d = add('while', cond, k, parent, loop)
            info['loops'][k] = e = body(k2, (d, 'T'), k)[0]
            return e, None
        if t == 'for':
            j = close(k + 1)
            parts = clean[toks[k + 1][1] + 1:toks[j][1]].split(';')
            mid = parts[1] if len(parts) == 3 else ''
            s, dep = k + 2, 0   # 초기식 끝 ';' (조건보다 먼저 실행되는 대입)
            while s < j and not (T(s) == ';' and dep == 0):
                dep += (T(s) in '([') - (T(s) in ')]')
                s += 1
            info['stmts'].append((k + 2, s, (parent, tuple(guards), loop, tuple(sw))))
            d = add('for', mid, s, parent, loop) if mid.strip() else None
            info['loops'][k] = e = body(j + 1, (d, 'T') if d else parent, k)[0]
            return e, None
        if t == 'do':
            k0, k = k, body(k + 1, parent, k)[0]
            info['loops'][k0] = k + 1   # do-while 조건까지 반복문 안
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
        e = skip_to_semi(k)
        info['stmts'].append((k, e, (parent, tuple(guards), loop, tuple(sw))))
        # 선언: 형 이름 [*]이름 …, 또는 매크로 선언 VAR(uint8, AUTOMATIC) 이름 (괄호 안 쉼표로 캐스트와 구별)
        macro = bool(re.fullmatch(r'[A-Z][A-Z0-9_]*', t)) and T(k + 1) == '(' and \
            ',' in [T(i) for i in range(k + 2, close(k + 1))] and (_is_ident(T(close(k + 1) + 1)) or
                                                                   T(close(k + 1) + 1) == '*')
        if _is_ident(t) and (_is_ident(T(k + 1)) or T(k + 1) == '*' or macro):
            dep = 0
            for i in range(k + 1, e):
                dep += (T(i) in '([{') - (T(i) in ')]}')
                if dep == 0 and _is_ident(T(i)) and T(i + 1) in ('=', ';', ',', '[') and \
                        (_is_ident(T(i - 1)) or T(i - 1) in ('*', ',') or (macro and T(i - 1) == ')')):
                    info['locals'].setdefault(cur['func'], set()).add(T(i))
        return e, None

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
                cur['func'], guards[:] = name, []
                info['body'][name] = (k, j)
                info['params'][name] = params(m, k - 1)
                stmt(k, None, False)
        k = j + 1
    if func is not None and not funcs:
        raise LookupError('func')
    info['set'] = assignments(toks, T, line, clean)
    return decs, keys, funcs, info


def _is_ident(t):
    return bool(re.fullmatch(r'[A-Za-z_]\w*', t))


def assignments(toks, T, line, clean=''):
    """값이 바뀌는 곳: 'a' / 'p->b' / 's.x' -> [(토큰 위치, 줄, v = f(); 면 f, 연산, 오른쪽 식)].
    연산은 '=' 또는 += 의 '+' 같은 것. a[i] 는 a 로 센다."""
    def op(k, s):  # k부터 붙어 있는 문자 토큰이 s인가
        return all(T(k + i) == c for i, c in enumerate(s)) and \
            all(toks[k + i][1] == toks[k][1] + i for i in range(len(s)) if k + i < len(toks))

    out = {}
    for k in range(len(toks)):
        if not _is_ident(T(k)) or T(k + 1) == '(':
            continue
        b = k  # 멤버 접근이면 맨 앞 이름까지 거슬러 올라간다
        while True:
            if T(b - 1) == '.' and _is_ident(T(b - 2)):
                b -= 2
            elif op(b - 2, '->') and _is_ident(T(b - 3)):
                b -= 3
            else:
                break
        e = k + 1
        while T(e) == '[':  # a[i] = ...
            d = 0
            for e in range(e, len(toks)):
                d += (T(e) == '[') - (T(e) == ']')
                if d == 0:
                    break
            e += 1
        if T(e) == '.' or op(e, '->'):
            continue  # 더 긴 멤버 접근의 앞부분
        # f(&x) 로 넘긴 값은 stub이 바꾸지 않는 것으로 본다 (대입으로 세지 않음)
        changed = ((T(e) == '=' and T(e + 1) != '=')
                   or (T(e) != '' and T(e) in '+-*/%&|^' and op(e + 1, '='))
                   or op(e, '<<=') or op(e, '>>=') or op(e, '++') or op(e, '--')
                   or op(b - 2, '++') or op(b - 2, '--'))
        call = None   # v = f(...); 처럼 stub 반환값을 그대로 받는가
        if T(e) == '=' and _is_ident(T(e + 1)) and T(e + 2) == '(':
            d, j = 0, e + 2
            for j in range(e + 2, len(toks)):
                d += (T(j) == '(') - (T(j) == ')')
                if d == 0:
                    break
            call = T(e + 1) if T(j + 1) == ';' else None
        if not changed:
            continue
        if T(e) == '=':
            o, s = '=', e + 1
        elif op(e, '<<=') or op(e, '>>='):
            o, s = T(e) * 2, e + 3
        elif T(e) in '+-*/%&|^' and op(e + 1, '='):
            o, s = T(e), e + 2
        else:   # ++ / --
            o, s = ('+' if '+' in (T(e), T(b - 1)) else '-'), None
        rhs = '1'
        if s is not None:
            d, j = 0, s
            while T(j) != '' and not (d == 0 and T(j) in (';', ',', ')', ']', '}')):
                d += (T(j) in ('(', '[', '{')) - (T(j) in (')', ']', '}'))
                j += 1
            rhs = clean[toks[s][1]:toks[j][1]] if clean and j < len(toks) else ''
        out.setdefault(''.join(T(i) for i in range(b, k + 1)), []).append((k, line(k), call, o, rhs))
    return out


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
        lines = [x for x in block.splitlines() if x.strip() and not x.lstrip().startswith(('#', '//'))]  # 주석 줄
        tc = {'name': '', 'items': [], 'bad': []}
        for i, x in enumerate(lines):
            m = TC_LINE.match(x)
            if m:
                tc['items'].append((m.group(1) or '?', re.sub(r'[\s/]', '', m.group(2).upper()), m.group(3)))
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
    decs, keys, funcs, info = parsed or parse(strip_code(c_src), func)
    tcs = read_tcs(tc_text)
    cov, warns, matched = {}, [], 0   # warns: (TC 순번, D번호, 화면용 문구, 상세용 원문)
    for n, tc in enumerate(tcs, 1):
        got, prev = {}, -1

        def imply(dd):
            for pd, po in dd.reqs():
                if po not in got.get(pd, {}):
                    got.setdefault(pd, {})[po] = False
                    imply(pd)

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
            # tf: 'T' / 'F' / 'TF'(반복문처럼 둘 다 탐). case·else 는 T일 때만 그 결과, [F]는 평가만 됨
            for outcome in (dict.fromkeys(tf) if o is None else [o] if 'T' in tf else []):
                got.setdefault(d, {})[outcome] = True
            imply(d)
            nd = d
            while o is None and 'F' in tf and nd.next:   # [F] if(…) 는 사슬 전체 F (else): 뒤 else if 도 F
                nd = nd.next
                got.setdefault(nd, {})['F'] = True

        for d, outs in got.items():
            for o, explicit in outs.items():
                cov.setdefault((d, o), []).append(f'TC{n}' + ('' if explicit else '*'))
            if d.in_loop or d.kind in ('while', 'for', 'dowhile'):  # 반복하면 T·F 둘 다 나올 수 있다
                continue
            if len(outs) > 1:
                warns.append((n, '-', f'L{d.line} 결정문에 서로 다른 결과가 함께 적힘', ''))

    missing = sorted(((d.line_of(o), d, o) for d in decs for o in d.outcomes() if (d, o) not in cov),
                     key=lambda m: m[0])
    return {'decs': decs, 'cov': cov, 'warns': warns, 'missing': missing, 'tcs': tcs, 'info': info,
            'funcs': funcs, 'matched': matched, 'lines': sum(len(t['items']) for t in tcs)}


def _kind(d, o):
    return o.split()[0] if d.kind == 'switch' else d.kind


C_WORDS = {'sizeof', 'int', 'char', 'short', 'long', 'unsigned', 'signed', 'float', 'double', 'void',
           'const', 'volatile', 'struct', 'union', 'enum', 'true', 'false'}


def need(d, o):
    """(d, o)에 가려면 꼭 거쳐야 하는 (결정문, 결과)들. 코드 순서대로."""
    seen, stack = {d: o}, [d]
    while stack:
        for pd, po in stack.pop().reqs():
            if pd not in seen:
                seen[pd] = po
                stack.append(pd)
    return sorted(seen.items(), key=lambda t: t[0].pos)


def values(path, info, stubs=None):
    """경로의 조건에 쓰인 이름마다 값이 어디서 오는지. 다른 함수는 stub으로 본다."""
    out = {}
    for d, _ in path:
        a, _b = info['body'].get(d.func, (0, 0))
        ps = info['params'].get(d.func, set())
        c = re.sub(r'\s+', '', d.cond)
        for m in re.finditer(r'[A-Za-z_]\w*(?:(?:\.|->)[A-Za-z_]\w*)*', c):
            t, base = m.group(), re.match(r'\w+', m.group()).group()
            if (t in out or base in C_WORDS or base.isupper() or base.endswith('_t')
                    or c[max(m.start() - 1, 0):m.start()] == '.' or c[max(m.start() - 2, 0):m.start()] == '->'):
                continue
            if c[m.end():m.end() + 1] == '(':
                out[t] = stubs.desc(t) if stubs else 'stub 반환값'
                continue
            why, col = source_of(t, d, info)
            if stubs and col and col.endswith('()'):
                why = f'{col} 받음 — {stubs.desc(col[:-2])}'
            out[t] = why
    return out


def source_of(t, d, info):
    """결정문 d의 조건에 쓰인 이름 t(멤버 경로 포함)의 값 출처.
    (설명, 경계값 TC의 열 이름 — 테스트에서 직접 넣을 수 없으면 None)"""
    a, _b = info['body'].get(d.func, (0, 0))
    base = re.match(r'\w+', t).group()
    hits = sorted(h for p, hs in info['set'].items()
                  if t == p or t.startswith(p + '.') or t.startswith(p + '->')
                  for h in hs if a < h[0] < d.pos)
    lines = ', '.join(f'L{h[1]}' for h in hits[-3:])
    if len(hits) == 1 and hits[0][2]:
        return f'{hits[0][2]} stub 반환값 ({lines})', hits[0][2] + '()'
    if hits:   # 경계값 TC는 대입을 경로대로 따라가 계산한다 (bva.Model.value)
        calls = list(dict.fromkeys(h[2] for h in hits if h[2]))
        return (f"대입을 따라 계산 ({lines}{', stub ' + ', '.join(calls) if calls else ''})"), None
    if base in info['params'].get(d.func, set()):
        return ('입력 인자' if t == base else f'입력 인자 {base}가 가리키는 값'), t
    return '전역/외부 값', t


def tc_line(i, d, o):
    return f"D{i} [{'T' if d.kind == 'switch' else o}] {template(d, o)}"


def chain(path):
    """if / else if 사슬은 TC 표기에서 D 하나: 들어간 갈래를 [T] if(…)·[T] elseif(…)로,
    조건이 모두 F(else, 또는 아무 갈래도 안 탐)면 [F] if(첫 조건)으로. path: 코드 순서의 (결정문, 결과)."""
    ds = {d for d, _ in path}
    out = []
    for d, o in path:
        if o == 'F' and d.next in ds:
            continue   # 사슬의 뒤 조건 줄이 이 줄을 대신한다
        while o == 'F' and d.kind == 'elseif':
            d = d.parent[0]
        out.append((d, o))
    return out


def howto(d, o, info, stubs=None):
    """(d, o)를 채우는 TC를 쓰는 데 필요한 것: 거쳐야 할 분기와 조건에 쓰인 값의 출처."""
    path = need(d, o)
    out = [f"L{d.line_of(o)} {_kind(d, o)} {'' if d.kind == 'switch' else o} 채우기",
           '  꼭 거쳐야 하는 분기 (여기 없는 분기는 어떤 결과든 됨, D 번호는 실제 순서로 고칠 것)']
    out += ['    ' + tc_line(i, pd, po) for i, (pd, po) in enumerate(chain(path), 1)]
    vals = values(path, info, stubs)
    if vals:
        w = max(map(len, vals))
        out.append('  조건에 쓰인 값')
        out += [f'    {n:<{w}}  {v}' for n, v in vals.items()]
    if any(pd.in_loop or pd.kind in ('while', 'for', 'dowhile') for pd, _ in path):
        out.append('  반복문이 끼어 있어 반복 횟수에 따라 결과가 달라질 수 있음')
    return out


def render_screen(r, bva_lines=()):
    """bva_lines: 경계값 TC 요약 줄들 (있으면 목록을 줄여 한 화면에 맞춘다)."""
    total = sum(len(d.outcomes()) for d in r['decs'])
    done = total - len(r['missing'])
    pct = 100.0 * done / total if total else 0.0
    out = [f'실행 전 분기 커버리지 (decision coverage 추정)  도구 {VERSION}', '-' * 60,
           f"함수 {len(r['funcs'])}개  결정문 {len(r['decs'])}개  분기 결과 {total}개  채움 {done}개 ({pct:.1f}%)",
           f"TC {len(r['tcs'])}개  표기 줄 {r['lines']}개  코드와 맞춘 줄 {r['matched']}개", '']
    if not r['tcs']:   # TC가 없으면 못 채운 목록은 전부라 의미 없다 → 종류별 개수만
        kinds = {}
        for d in r['decs']:
            kinds[d.kind] = kinds.get(d.kind, 0) + 1
        out.append('결정문 종류  ' + '  '.join(f'{k} {n}개' for k, n in kinds.items()))
    else:
        miss, cap = r['missing'], (10 if bva_lines else 18)
        out.append(f'못 채운 분기 {len(miss)}개' + ('  (L=줄 번호)' if miss else ''))
        for ln, d, o in miss[:cap]:
            out.append(f'  L{ln:<6} {_kind(d, o):<8} {"" if d.kind == "switch" else o}')
        if len(miss) > cap:
            out.append(f'  … 외 {len(miss) - cap}개는 상세 파일')
        cap = 5 if bva_lines else 8
        out += ['', f"경고 {len(r['warns'])}개"]
        for n, dno, msg, _ in r['warns'][:cap]:
            out.append(f'  TC{n:<3} D{dno:<3} {msg}')
        if len(r['warns']) > cap:
            out.append(f"  … 외 {len(r['warns']) - cap}개는 상세 파일")
    if bva_lines:
        out += [''] + list(bva_lines)
    out += ['', f'상세(조건문 포함, 밖으로 보내지 말 것): %TEMP%\\{REPORT}']
    return '\n'.join(out)


def render_report(r):
    out = ['# 분기별 채운 TC (* = 직접 적지 않았지만 구조상 따라 나온 결과)', '']
    for d in r['decs']:
        out.append(f"L{d.line} {d.kind} ({' '.join(d.cond.split())})" + ('  [반복문 안]' if d.in_loop else ''))
        for o in d.outcomes():
            hit = r['cov'].get((d, o))
            out.append(f"    {o:<12} {', '.join(hit) if hit else '-- 못 채움'}")
    out += ['', '# 못 채운 분기 채우는 법']
    for _, d, o in r['missing']:
        out += howto(d, o, r['info']) + ['']
    out += ['# 경고']
    for n, dno, msg, detail in r['warns']:
        out.append(f'TC{n} D{dno} {msg}' + (f'  |  {detail}' if detail else ''))
    out += ['', '# TC 순번과 이름']
    for n, tc in enumerate(r['tcs'], 1):
        out.append(f"TC{n} = {tc['name'] or '(이름 없음)'}")
    return '\n'.join(out) + '\n'


def _read(path):
    raw = open(path, 'rb').read()
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('cp949', errors='replace')
    return text.replace('\r\n', '\n').replace('\r', '\n')   # CRLF면 줄 끝 \r 때문에 #define 등을 못 읽는다


def main(argv=None):
    ap = argparse.ArgumentParser(description='실행 전 분기 커버리지 추정 (C)')
    ap.add_argument('c_file')
    ap.add_argument('tc_file', nargs='?', help='없으면 TC 없이 분기만 센다')
    ap.add_argument('--func', help='이 함수만 본다')
    ap.add_argument('--csv', help='경계값 TC 입력값을 이 CSV로 저장 (TC 표기는 상세 파일에)')
    ap.add_argument('--stub', help='직접 쓴 stub 코드 파일 (반환식을 계산에 씀)')
    ap.add_argument('--stub-free', action='store_true', help='정의 안 한 stub 반환값을 0 대신 자유 입력으로')
    ap.add_argument('--time', type=float, default=60, help='경계값 TC 계산 시간 한도(초), 기본 60')
    ap.add_argument('--path', type=int, help='경계값 TC 중 N번의 예상 경로를 줄 번호로 찍는다 (실제 결과와 비교용)')
    ap.add_argument('--inc', action='append', default=[],
                    help='enum·#define 값을 찾을 헤더 폴더(하위 폴더까지) 또는 파일. 여러 번 줄 수 있음')
    a = ap.parse_args(argv)
    os.system('cls' if os.name == 'nt' else 'clear')  # 고정 문자열이라 주입 위험 없음
    rpt = os.path.join(tempfile.gettempdir(), REPORT)
    try:
        src = _read(a.c_file)
    except OSError:
        print('오류: C 파일을 열 수 없음 — 경로를 확인하세요')
        return 1
    try:
        tc_text = _read(a.tc_file) if a.tc_file else ''
    except OSError:
        print('오류: TC 표기 파일을 열 수 없음 — 경로를 확인하세요')
        return 1
    try:
        stub_src = _read(a.stub) if a.stub else ''
    except OSError:
        print('오류: stub 파일을 열 수 없음 — 경로를 확인하세요')
        return 1
    try:
        parsed = parse(strip_code(src), a.func)
        r = analyze('', tc_text, parsed=parsed)
        # TC 표기 파일이 없으면 경계값 TC로 얼마나 채울 수 있는지 보여 준다 (CSV는 --csv 줄 때만)
        heads = headers_near(a.c_file) + [h for d in a.inc for h in headers_in(d)]
        if any(not os.path.exists(d) for d in a.inc):
            print('오류: --inc 로 준 폴더나 파일이 없음 — 경로를 확인하세요')
            return 1
        extra = (make_bva(parsed, [src] + heads, a.csv, stub_src, not a.stub_free, a.time, a.path)
                 if a.csv or not a.tc_file else None)
    except LookupError:
        print('오류: --func 로 준 함수를 C 파일에서 못 찾음 — 함수 이름을 확인하세요')
        return 1
    except OSError:
        print('오류: CSV 파일을 쓸 수 없음 — 엑셀에서 열려 있지 않은지, 폴더가 있는지 확인하세요')
        return 1
    except Exception:
        with open(rpt, 'w', encoding='utf-8') as f:
            f.write(traceback.format_exc())
        print('오류: 코드 구조 해석 실패 — 괄호 짝, #ifdef 안의 중괄호를 확인하세요')
        print(f'상세: %TEMP%\\{REPORT}')
        return 1
    with open(rpt, 'w', encoding='utf-8') as f:
        f.write(render_report(r) + (extra[1] if extra else ''))
    print(render_screen(r, extra[0] if extra else ()))
    return 0


def headers_near(c_path):
    """C 파일과 같은 폴더의 .h (상수 값과 형을 찾는 데만 쓴다)."""
    return headers_in(os.path.dirname(os.path.abspath(c_path)), False)


def headers_in(folder, deep=True):
    """폴더(deep이면 하위 폴더까지)의 .h·.hpp 내용들, 또는 파일 하나(확장자 상관없이).
    enum·#define 값과 변수 선언을 찾는 데 쓴다."""
    if os.path.isfile(folder):
        files = [folder]
    else:
        files = [h for ext in ('*.h', '*.hpp')
                 for h in glob.glob(os.path.join(folder, '**' if deep else '', ext), recursive=deep)]
    out = []
    for h in files:
        try:
            out.append(_read(h))
        except OSError:
            pass
    return out


def make_bva(parsed, srcs, csv_path, stub_src='', stub_zero=True, budget=60, path_n=None):
    """경계값 TC를 만들고 csv_path가 있으면 CSV로 쓴다 → (화면 요약 줄들, 상세 보고서에 붙일 글).
    화면에는 개수·줄 번호·이유 종류만 찍는다 (이유 원문에는 변수·상수 이름이 들어 있다)."""
    import bva
    t0 = time.monotonic()
    try:
        m = bva.Model(parsed, srcs, stub_src, stub_zero, budget)
        tcs, manual = m.generate()
    except Exception:
        return ['경계값 TC: 계산 실패 — 상세 파일의 오류를 확인하세요'], \
            '\n# 경계값 TC 계산 실패\n' + traceback.format_exc()
    if csv_path:
        m.write_csv(csv_path, tcs)
    total = sum(len(d.outcomes()) for d in parsed[0])
    got = set().union(set(), *(g for _, _, g in tcs))
    plain = sorted({d.line_of(o) for d, o in got & m.plain})
    lines = [f"경계값 TC {len(tcs)}개로 {len(got)}/{total} 채움 ({time.monotonic() - t0:.0f}초)" +
             (' → CSV 저장' if csv_path else '  (CSV·TC 표기가 필요하면 --csv 파일이름.csv)')]
    if plain:   # 갈 수는 있지만 경계 바로 옆 값이 없는 분기 (예: 바깥 조건이 더 좁음)
        lines.append(f'  그중 경계가 아닌 값으로 채운 분기 {len(got & m.plain)}개  ' +
                     ' '.join(f'L{x}' for x in plain[:8]) + (' …' if len(plain) > 8 else ''))
    cs = m.cstats   # 헤더를 읽었는지, 상수를 몇 개 구했는지 (이름 없이 개수만)
    lines.append(f"헤더 파일 {len(srcs) - 1}개 · 읽은 상수 {cs['consts']}개 · 값을 못 구한 #define {len(cs['failed'])}개"
                 f" (#define {cs['defines']}개 중)")
    if stub_src.strip():   # 넣은 stub 코드가 실제로 쓰였는지 (이름 없이 개수만)
        n, used, free, mth = m.stubs.counts()
        lines.append(f'stub 코드 함수 {n}개: 반환식으로 계산 {used}개 · 수학 함수 이름이라 실제 값 {mth}개'
                     f' · 자유 입력 {free}개 (반환문이 여럿 등)')
    lines.append(f'자동으로 못 만든 분기 {len(manual)}개' + ('  (이유 종류별, L=줄 번호)' if manual else ''))
    kinds = {}
    for (d, o), why in manual.items():
        kinds.setdefault(bva.reason_kind(why), []).append(d.line_of(o))
    for k, lns in sorted(kinds.items(), key=lambda kv: -len(kv[1])):
        ls = sorted(set(lns))
        lines.append(f'  {k:<20} {len(lns):>3}개  ' + ' '.join(f'L{x}' for x in ls[:8]) +
                     (' …' if len(ls) > 8 else ''))
    unknown = sorted({n for w in manual.values() for n in re.findall(r'상수 값 모름 \((\w+)\)', w)})
    if unknown:   # 값을 모르는 상수는 사용자에게 받아야 한다 (이름은 상세 파일에만)
        lines.append(f'  값을 모르는 상수 {len(unknown)}개 — 정의된 헤더 폴더를 --inc 폴더 로 주세요 (이름은 상세 파일)')
        # 종류별 개수만 (원인 찾기용): 읽은 코드의 enum 안 / #define / 어디에도 선언 없음
        bodies = [b for s_ in srcs for b in re.findall(r'\benum\b[^{;]*\{([^}]*)\}', strip_code(s_))]
        in_enum = {n for n in unknown if any(re.search(rf'\b{n}\b', b) for b in bodies)}
        in_def = {n for n in unknown if n not in in_enum and
                  any(re.search(rf'^[ \t]*#[ \t]*define[ \t]+{n}\b', s_, re.M) for s_ in srcs)}
        lines.append(f'    종류: enum 항목인데 값 못 구함 {len(in_enum)}개 · #define인데 값 못 구함 {len(in_def)}개'
                     f' · 선언을 못 찾음 {len(unknown) - len(in_enum) - len(in_def)}개'
                     ' (다른 모듈 전역 변수나 읽지 않은 헤더의 enum)')
    if m.guessed:   # 선언을 못 찾은 이름은 입력 변수로 본다: enum이면 틀리므로 개수를 알린다
        lines.append(f'선언 없이 입력 변수로 본 이름 {len(m.guessed)}개 — 다른 모듈 전역 변수면 그대로,'
                     ' enum·상수면 정의된 헤더를 --inc로 (이름은 상세 파일)')
    if 0 < len(manual) <= 8:   # 남은 게 적으면 분기마다 숫자로 진단 (원인 찾기용)
        lines.append('  분기별 진단  탐색 = 경계(1~2개만·조각씩·빠른·전체) 일반(같은 넷) 순, '
                     'o 찾음 x 없음 상 상한 시 시간')
        lines.append('    식 = 조건의 연산자 · 호출 = 함수 호출 수 · 풀이 = 풀 수 있는 비교 수 · 차 = 값 0일 때 왼쪽-오른쪽')
        lines += [f'    L{d.line_of(o)} {o:<4} {m.diagnose((d, o))}' for d, o in sorted(manual, key=lambda t: t[0].pos)]
    if path_n:   # 실제 도구 결과와 맞춰 보기: TC 하나의 예상 경로를 줄 번호·결과로만
        lines += path_lines(m, tcs, path_n)
    text = ['', '# 경계값 TC (CSV와 같은 순서)', m.tc_text(tcs), '# 자동으로 못 만든 분기']
    text += [f'L{d.line_of(o)} {d.kind} {o}: {why}' for (d, o), why in manual.items()]
    text += ['', '# 값을 모르는 상수 (정의된 헤더 폴더를 --inc 로)'] + unknown
    text += ['', '# 선언 없이 입력 변수로 본 이름 (enum·상수면 정의된 헤더를 --inc 로)'] + sorted(m.guessed)
    text += ['', '# 값을 못 구한 #define (식 안의 이름을 모르거나 숫자 식이 아님)'] + cs['failed']
    return lines, '\n'.join(text) + '\n'


def path_lines(m, tcs, n):
    """TC n이 지나갈 것으로 계산한 분기들. switch는 case 이름 대신 그 case 줄 번호만 (이름은 내부 정보)."""
    if not 1 <= n <= len(tcs):
        return [f'--path {n}: TC는 1~{len(tcs)} 중에서 고르세요']
    res = tcs[n - 1][1]
    items = []
    for d in m.decs:
        if d not in res:
            continue
        o = res[d]
        if o is None:
            items.append(f'L{d.line} ?')
        elif d.kind == 'switch':
            items.append(f"L{d.line_of(o)} {'default' if o == 'default' else 'case'}")
        else:
            items.append(f"L{d.line} {'T/F' if o == 'T' and d.kind in ('while', 'for', 'dowhile') else o}")
    out = [f'TC{n} 예상 경로 {len(items)}개 (코드 순서, ? = 계산 못 함) — 실제 도구에서 처음 다른 줄을 찾아 주세요']
    out += ['  ' + '  '.join(f'{x:<11}' for x in items[i:i + 9]).rstrip() for i in range(0, len(items), 9)]
    return out


def utf8_output():
    """출력을 파일·파이프로 돌리면 영문 Windows는 cp1252라 한글에서 죽는다. UTF-8로 바꾼다."""
    for stream in (sys.stdout, sys.stderr):
        if callable(getattr(stream, 'reconfigure', None)):   # pythonw는 stream이 None
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')


if __name__ == '__main__':
    utf8_output()
    sys.exit(main())
