"""경계값 TC 만들기.

분기 결과(T/F, case)마다 조건이 참·거짓으로 바뀌는 경계 바로 양쪽 값으로 채우고,
그런 분기를 되도록 많이 묶어 TC 수를 줄인다. 정수는 ±1, float은 ±0.1이 경계 한 칸이다.

값을 테스트에서 직접 넣을 수 있는 것(입력 인자, stub 반환값, 전역)만 다룬다.
stub에 &로 넘긴 값은 stub이 바꾸지 않는 것으로 본다.
함수 안에서 계산되는 값이 끼거나 상수 값을 모르는 분기는 '수동'으로 남기고 이유를 적는다.
"""
import bisect
import csv
import math
import re
import time

import branch_cov as bc

FLOAT_T = r'(?:float|double|\w*[fF](?:32|64)\b|\w*(?:float|real)\w*)'
UNSIGNED_T = r'(?:unsigned|uint\w*|u(?:8|16|32|64)\b|\w*_u(?:8|16|32|64)\w*|U(?:8|16|32|64)\w*|UINT\w*|bool\w*)'
INT_T = r'(?:char|short|int|long|signed|unsigned|u?int\w*|[us](?:8|16|32|64)\b|[US](?:8|16|32|64)\w*|U?INT\w*|bool\w*)'
NUM = r'0[xX][0-9a-fA-F]+[uUlL]*|(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?[uUlLfF]*'
TOK = re.compile(rf'\s*({NUM}|[A-Za-z_]\w*(?:\s*(?:\.|->)\s*[A-Za-z_]\w*)*|&&|\|\||==|!=|<=|>=|<<|>>|\S)')
# C 우선순위 (클수록 먼저). ?: 는 다루지 않는다
PREC = {'||': 1, '&&': 2, '|': 3, '^': 4, '&': 5, '==': 6, '!=': 6, '<': 7, '>': 7, '<=': 7, '>=': 7,
        '<<': 8, '>>': 8, '+': 9, '-': 9, '*': 10, '/': 10, '%': 10}


def _cdiv(a, b):
    # C 정수 나눗셈은 0 쪽으로 버린다 (-7 / 2 == -3). Python // 는 아래로 버린다
    if isinstance(a, int) and isinstance(b, int):
        q = abs(a) // abs(b)
        return -q if (a < 0) != (b < 0) else q
    return a / b


def _shift(b):
    if not 0 <= b <= 64:
        raise ValueError('shift')   # 큰 시프트로 거대한 정수를 만들지 않게
    return b


BIN = {'+': lambda a, b: a + b, '-': lambda a, b: a - b, '*': lambda a, b: a * b, '/': _cdiv,
       '%': lambda a, b: a - _cdiv(a, b) * b,
       '<': lambda a, b: int(a < b), '>': lambda a, b: int(a > b), '<=': lambda a, b: int(a <= b),
       '>=': lambda a, b: int(a >= b), '==': lambda a, b: int(a == b), '!=': lambda a, b: int(a != b),
       '&': lambda a, b: a & b, '|': lambda a, b: a | b, '^': lambda a, b: a ^ b,
       '<<': lambda a, b: a << _shift(b), '>>': lambda a, b: a >> _shift(b)}
UNARY = {'!': lambda x: int(not x), '~': lambda x: ~x, '-': lambda x: -x, '+': lambda x: x}
LOOPS = ('while', 'for', 'dowhile')
LIMIT = 3000  # ponytail: 값 조합 탐색 상한. 넘으면 그 묶음은 못 푼 것으로 보고 다른 TC로 넘긴다
BUDGET = 60    # ponytail: 경계값 계산 전체 시간 한도(초) 기본값. 넘으면 남은 분기는 '시간 초과' 수동


class Manual(Exception):
    """자동으로 값을 못 정하는 조건. 메시지가 이유."""


class Args(list):
    """함수 호출의 인자 글자들. f() 처럼 비어 있어도 '호출'이라 참."""

    def __bool__(self):
        return True


def _round(x):
    return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)   # C round: 0.5는 0에서 먼 쪽


# 표준 수학 함수: stub 코드가 없으면 실제 값으로 계산한다. (함수, 경계 후보를 만들 역함수 또는 None)
_MATH = {'sqrt': (math.sqrt, lambda n: n * n), 'fabs': (abs, lambda n: -n), 'abs': (abs, lambda n: -n),
         'labs': (abs, lambda n: -n), 'pow': (math.pow, None), 'floor': (math.floor, None),
         'ceil': (math.ceil, None), 'round': (_round, None), 'exp': (math.exp, math.log),
         'log': (math.log, math.exp), 'log10': (math.log10, lambda n: 10 ** n), 'sin': (math.sin, math.asin),
         'cos': (math.cos, math.acos), 'tan': (math.tan, math.atan), 'atan': (math.atan, math.tan),
         'atan2': (math.atan2, None), 'fmin': (min, None), 'fmax': (max, None), 'hypot': (math.hypot, None)}
MATH = {**_MATH, **{k + 'f': v for k, v in _MATH.items() if k not in ('abs', 'labs')}}   # sqrtf 등


# 수동 이유 원문(이름이 들어 있음) → 화면에 찍을 종류. 앞에 있는 것부터 본다
REASONS = [('고정 stub', '고정 stub이 막음'), ('갈 수 없는 경로', '갈 수 없는 경로/후보 밖'),
           ('탐색 상한', '탐색 상한에 걸림'),
           ('실제 경로가 달라짐', '값과 경로가 안 맞음'), ('반복문 안에서 대입', '반복문 안에서 대입'),
           ('식 안에서 대입', '조건식 안에서 대입'), ('구조체 통째로', '구조체 통째로 대입'),
           ('결과에 따라 달라짐', '수동 분기 뒤의 대입'), ('상수 값 모름', '상수 값 모름'),
           ('sizeof', 'sizeof 식'), ('잘못된 숫자', '잘못된 숫자'), ('상수 식', '상수 식 계산 실패'),
           ('못 다루는', '못 다루는 식'), ('괄호 짝', '못 다루는 식'), ('시간 한도', '계산 시간 초과')]


def reason_kind(why):
    return next((k for p, k in REASONS if p in why), '기타')


class Defaults(dict):
    def __missing__(self, key):
        return 0


def num(s):
    if s.lower().startswith('0x'):
        return int(re.sub(r'[uUlL]+$', '', s), 16)
    if re.search(r'[.eE]', s) or re.search(r'[fF]$', s):
        return float(re.sub(r'[fF]$', '', s))
    s = re.sub(r'[uUlL]+$', '', s)
    try:
        return int(s, 8) if len(s) > 1 and s[0] == '0' else int(s)   # 010 은 8진수 8
    except ValueError:
        raise Manual(f'잘못된 숫자 ({s})') from None


def translate(cond, consts, var_of=None):
    """C 식 → (값 함수 f(V, R), 변수 열 이름들, 식에 나온 숫자들).
    eval 없이 직접 해석한다: C 우선순위, &&·|| 단락 평가, 0 쪽으로 버리는 정수 나눗셈, 8진수.
    V는 열 이름 → 값, R(i)는 결정문 i의 결과 (Model.value 참고).
    var_of(이름, 함수호출?) → 그 자리의 (값 함수, 열 이름들, 숫자들) 또는 Manual."""
    c = re.sub(r'\(\s*(?:(?:unsigned|signed|const|volatile|struct|enum)\s+)*[A-Za-z_]\w*\s*\**\s*\)(?=\s*[\w(!~])',
               ' ', cond)   # 형 변환 (uint8)x 제거
    toks = TOK.findall(c)
    vs, nums, at = [], [], [0]

    def peek():
        return toks[at[0]] if at[0] < len(toks) else ''

    def atom():
        t = peek()
        at[0] += 1
        if t == '-' and re.fullmatch(NUM, peek()):   # 음수 상수는 그 값을 경계 후보로
            at[0] += 1
            x = -num(toks[at[0] - 1])
            nums.append(x)
            return lambda V, R: x
        if t in UNARY:
            f, op = atom(), UNARY[t]
            return lambda V, R: op(f(V, R))
        if t == '(':
            f = expr(1)
            if peek() != ')':
                raise Manual('괄호 짝이 안 맞는 식')
            at[0] += 1
            return f
        if re.fullmatch(NUM, t):
            x = num(t)
            nums.append(x)
            return lambda V, R: x
        if not re.match(r'[A-Za-z_]', t):
            raise Manual(f'못 다루는 식 ({t})' if t else '못 다루는 식')
        t = re.sub(r'\s+', '', t)
        call = peek() == '('
        if call:   # 인자 글자들을 모은다 (계산할지는 var_of가 정한다)
            at[0] += 1
            d, cur, call = 1, [], Args()
            while at[0] < len(toks):
                tk = toks[at[0]]
                at[0] += 1
                d += (tk == '(') - (tk == ')')
                if d == 0:
                    break
                if tk == ',' and d == 1:
                    call.append(' '.join(cur))
                    cur = []
                else:
                    cur.append(tk)
            if cur or call:
                call.append(' '.join(cur))
        if not call and t in consts:
            x = consts[t]
            nums.append(x)
            return lambda V, R: x
        if t == 'sizeof':
            raise Manual('sizeof 식')
        if var_of is None or (not call and re.fullmatch(r'[A-Z][A-Z0-9_]*', t)):
            raise Manual(f'상수 값 모름 ({t})')
        f, ks, ns = var_of(t, call)
        nums.extend(ns)
        vs.extend(k for k in ks if k not in vs)
        return f

    def expr(lo):
        f = atom()
        while peek() in PREC and PREC[peek()] >= lo:
            op = peek()
            at[0] += 1
            g = expr(PREC[op] + 1)
            if op == '&&':
                f = (lambda a, b: lambda V, R: int(bool(a(V, R)) and bool(b(V, R))))(f, g)
            elif op == '||':
                f = (lambda a, b: lambda V, R: int(bool(a(V, R)) or bool(b(V, R))))(f, g)
            else:
                f = (lambda a, b, o: lambda V, R: o(a(V, R), b(V, R)))(f, g, BIN[op])
        return f

    f = expr(1)
    if at[0] != len(toks):
        raise Manual(f'못 다루는 식 ({peek()})')
    return f, vs, nums


def const_eval(expr, consts):
    f, _, _ = translate(expr, consts)
    try:
        return f({}, None)
    except Exception:
        raise Manual(f'상수 식 계산 실패 ({expr.strip()})') from None


def constants(srcs, stats=None):
    """#define 상수와 enum 값. #define 식((A + 1), (1 << 3), 캐스트)도 계산하고, 다른 헤더의 상수를 쓰는 것도
    읽는 순서와 상관없이 값이 더 정해지지 않을 때까지 여러 번 돌려 채운다."""
    out = {'NULL': 0, 'TRUE': 1, 'FALSE': 0, 'true': 1, 'false': 0}
    defs, enums, cdefs = [], [], []   # cdefs: const 변수
    for src in srcs:
        # 함수 꼴 매크로 FOO(x) 는 뺀다 (이름 바로 뒤가 '(')
        for m in re.finditer(r'^[ \t]*#[ \t]*define[ \t]+(\w+)(?!\()[ \t]+([^\n]*?)[ \t]*(?://[^\n]*|/\*[^\n]*)?$',
                             src, re.M):
            if m.group(2).strip() and not m.group(2).rstrip().endswith('\\'):
                defs.append((m.group(1), m.group(2)))
        for m in re.finditer(r'\benum\b[^{;]*\{([^}]*)\}', bc.strip_code(src)):
            enums.append([item.partition('=') for item in m.group(1).split(',') if item.split('=')[0].strip()])
        # const 변수도 상수: `static const uint8 c_x = 5u;`, `uint8 const c_x = 5u;`, `CONST(uint8, X) c_x = 5u;`
        # (포인터 `const T *p`·volatile·배열은 빼기: 이름 앞에 * 나 [ 가 있으면 안 맞는다)
        for m in re.finditer(r'(?:^|(?<=[;{}]))\s*(?:(?:static|extern)\s+)*(?:const\s+\w+(?:\s+\w+)?|\w+(?:\s+\w+)?\s+const'
                             r'|CONST\s*\([^()]*\))\s+(\w+)\s*=\s*([^;,{}]+);', bc.strip_code(src), re.M):
            cdefs.append((m.group(1), m.group(2)))
    for _ in range(10):   # 값이 더 안 늘 때까지 (헤더끼리 서로 참조)
        n = len(out)
        for name, expr in defs + cdefs:
            if name not in out:
                try:
                    out[name] = const_eval(expr, out)
                except Manual:
                    pass
        for items in enums:
            v = -1
            for name, _, expr in items:
                name = name.strip()
                if name not in out:
                    try:
                        out[name] = const_eval(expr, out) if expr.strip() else v + 1
                    except Manual:
                        break  # 값을 모르면 뒤 항목도 모른다 (다음 바퀴에 다시)
                v = out[name]
        if len(out) == n:
            break
    if stats is not None:   # 화면용 숫자와 상세 파일용 이름
        stats['defines'] = len({name for name, _ in defs})
        stats['failed'] = sorted({name for name, _ in defs if name not in out})
        stats['consts'] = len(out) - 5
    return out


NOT_TYPE = {'return', 'if', 'else', 'case', 'goto', 'while', 'for', 'switch', 'sizeof', 'do', 'typedef', 'default'}


def decl_names(text):
    """선언문에서 변수 이름을 모두 모은다: `uint8 a, b = 0u, c[3];`, `unsigned char x;`,
    `static VAR(uint8, AUTOMATIC) s_a, s_b;`. 함수 원형 f(int a)·호출 f(a, B)·대입 x = y 는 빼기."""
    names = set()
    for st in re.split(r'[;{}]', text):
        m = re.match(r'\s*(?:(?:static|extern|const|volatile|register|auto|struct|union|enum)\s+)*'
                     r'([A-Za-z_]\w*)(\s*\([^()]*,[^()]*\))?([\s*]+)(.*)$', st, re.S)
        if not m or m.group(1) in NOT_TYPE:
            continue
        dep, cur, parts = 0, '', []
        for ch in m.group(4):   # 맨 바깥 쉼표로 나눈다
            dep += (ch in '([{') - (ch in ')]}')
            if ch == ',' and dep == 0:
                parts.append(cur)
                cur = ''
            else:
                cur += ch
        parts.append(cur)
        for p in parts:
            nm = re.match(r'\s*(?:[A-Za-z_]\w*[\s*]+)*\**\s*([A-Za-z_]\w*)\s*(?:$|=|\[)', p, re.S)
            if nm and nm.group(1) not in NOT_TYPE:
                names.add(nm.group(1))
    return names


def closure(reqs):
    """(결정문, 결과)들과 그것들이 평가되려면 필요한 바깥 결과 전부."""
    seen, stack = set(), list(reqs)
    while stack:
        c = stack.pop()
        if c not in seen:
            seen.add(c)
            stack += c[0].reqs()
    return seen


def _var(k):
    return (lambda V, R: V[k]), [k], []


def _fixed(x):
    return (lambda V, R: x), [], [x]


def _close(s, i):
    """s[i] == '{' 의 짝 '}' 위치."""
    d = 0
    for j in range(i, len(s)):
        d += (s[j] == '{') - (s[j] == '}')
        if d == 0:
            return j
    return len(s)


class Stubs:
    """사용자가 써 넣은 stub 코드. 함수마다 반환식을 읽어 조건식 자리에 넣는다.
    정의 안 한 stub은 zero=True면 0을 돌려주고, False면 반환값을 자유 입력으로 본다."""

    def __init__(self, src='', consts=None, zero=True):
        self.zero, self.fns, self.note, self.rtype = zero, {}, {}, {}
        self.pfns = {}   # 인자를 쓰는 반환식: 이름 -> (인자 이름들, 반환식) — Model이 호출 자리 인자로 계산
        clean = bc.strip_code(src)
        for m in re.finditer(r'\b([A-Za-z_]\w*)\s*\(([^()]*)\)\s*\{', clean):
            name = m.group(1)
            if name in ('if', 'while', 'for', 'switch'):
                continue
            self.rtype[name] = re.split(r'[;}]', clean[:m.start()])[-1].strip()
            body = clean[m.end():_close(clean, m.end() - 1)]
            params = [re.findall(r'\w+', p)[-1] for p in m.group(2).split(',')
                      if re.findall(r'\w+', p) and p.strip() != 'void']
            rets = re.findall(r'\breturn\b\s*([^;]*);', body)
            if len(rets) != 1 or not rets[0].strip():
                self.note[name] = '반환문이 하나가 아님'
                continue
            try:
                self.fns[name] = translate(rets[0], consts or {}, lambda t, call, ps=params: self._glob(t, call, ps))
            except Manual as e:
                if str(e) == '인자':
                    self.pfns[name] = (params, rets[0])
                else:
                    self.note[name] = str(e)

    @staticmethod
    def _glob(t, call, ps):
        if call or re.match(r'\w+', t).group() in ps:
            raise Manual('인자')   # 인자나 다른 함수를 쓰면 호출 자리에서 계산 (pfns)
        return _var(t)

    def counts(self):
        """화면용 숫자: (읽은 함수, 계산에 쓰는 것, 자유 입력, 그중 수학 함수 이름이라 실제 값으로 계산)"""
        math_named = sum(n in MATH for n in self.note)
        return (len(self.fns) + len(self.pfns) + len(self.note), len(self.fns) + len(self.pfns),
                len(self.note) - math_named, math_named)

    def ref(self, name):
        if name in self.fns:
            return self.fns[name]
        if self.zero and name not in self.note and name not in self.pfns:
            return _fixed(0)
        return _var(name + '()')

    def desc(self, name):
        if name in self.fns:
            f, vs, _ = self.fns[name]
            return f"stub 코드: {', '.join(vs)} 값" if vs else f'stub 코드: {f({}, None)} 고정'
        if name in self.pfns:
            return 'stub 코드: 인자로 계산'
        if name in MATH:
            return '표준 수학 함수: 실제 값으로 계산'
        if name in self.note:
            return f'stub 반환값 자유 입력 (stub 코드: {self.note[name]})'
        return 'stub 정의 없음 → 0 고정' if self.zero else 'stub 반환값 자유 입력'


def needed_stubs(parsed):
    """분기 조건에 값이 들어가는 stub 이름들 (직접 호출, 또는 조건에 쓴 변수에 대입한 식 안의 호출)."""
    info, out, seen = parsed[3], [], set()

    def walk(expr, pos, func):
        c = re.sub(r'\s+', '', expr)
        for m in re.finditer(r'[A-Za-z_]\w*(?:(?:\.|->)[A-Za-z_]\w*)*', c):
            t = m.group()
            if c[max(m.start() - 1, 0):m.start()] == '.' or c[max(m.start() - 2, 0):m.start()] == '->':
                continue
            if c[m.end():m.end() + 1] == '(':
                if t != 'sizeof' and not t.isupper() and t not in out and t not in MATH:
                    out.append(t)
                continue
            a = info['body'].get(func, (0, 0))[0]
            for p, hs in info['set'].items():
                if t == p or t.startswith(p + '.') or t.startswith(p + '->'):
                    for h in hs:
                        if a < h[0] < pos and h[0] not in seen:
                            seen.add(h[0])
                            walk(h[4], h[0], func)

    for d in sorted(parsed[0], key=lambda d: d.pos):
        walk(d.cond, d.pos, d.func)
    return out


def skeleton(names, srcs, stub_src=''):
    """아직 stub 코드가 없는 이름마다 `반환형 이름(인자) { return 0; }`. 원형이 있으면 그 형을 따른다."""
    have = Stubs(stub_src)
    text = '\n'.join(bc.strip_code(s) for s in srcs)
    lines = []
    for n in names:
        if n in have.fns or n in have.note:
            continue
        m = re.search(rf'(?:^|[;}}\n])\s*((?:[A-Za-z_]\w*[ \t\*]+)+?)\**{re.escape(n)}\s*\(([^;{{)]*)\)\s*;', text)
        rtype = re.sub(r'\b(?:extern|static|inline)\b', '', m.group(1)).strip() if m else 'int'
        if rtype == 'void':
            continue
        lines.append(f"{rtype} {n}({(m.group(2).strip() if m else '') or 'void'}) {{ return 0; }}")
    return lines


class Model:
    def __init__(self, parsed, srcs, stub_src='', stub_zero=True, budget=BUDGET):
        self.budget = budget
        self.decs = sorted(parsed[0], key=lambda d: d.pos)
        self.info = parsed[3]
        self.cstats = {}
        self.consts = constants(srcs + [stub_src], self.cstats)
        self.stubs = Stubs(stub_src, self.consts, stub_zero)
        self.fixed, self.float_hint = {}, set()   # 결정문 -> 고정값 stub 이름들 / float stub 반환 변수
        text = self.text = '\n'.join(bc.strip_code(s) for s in srcs + [stub_src])
        self.isvar, self.declnames = {}, decl_names(text)
        self.setnames = {re.match(r'\w+', k).group() for k in self.info['set']}   # 파일 어디서든 대입되는 이름
        # 읽은 코드에서 상수로 정의된 이름 (enum 항목·#define): 값을 못 구하면 사용자에게 받는다
        self.constlike = {n for b in re.findall(r'\benum\b[^{;]*\{([^}]*)\}', text)
                          for n in re.findall(r'(?:^|,)\s*([A-Za-z_]\w*)', b)} | {
            n for s_ in srcs + [stub_src] for n in re.findall(r'^[ \t]*#[ \t]*define[ \t]+(\w+)', s_, re.M)}
        self.guessed = set()   # 선언을 못 찾았지만 상수 정의도 없어 입력 변수로 본 이름 (다른 모듈 전역 변수)
        self.fn, self.vars, self.manual, self.labels = {}, {}, {}, {}
        self.cands, self.isf, self.uns, self.cols = {}, {}, {}, []
        self.vmemo, self.known, self.via = {}, {}, False   # 대입을 따라 만든 값 (value 참고)
        self.own, self.leaf, self.deadline = {}, set(), float('inf')   # own: 경계 검사에서 움직일 변수
        # plain: 경계는 못 맞추고 일반 값으로 채우는 분기, alone: 분기 하나만 채우는 값
        self.plain, self.stop, self.alone, self.trace = set(), None, {}, {}
        self.dnums, self.ccache, self.parts = {}, {}, {}   # parts: 결정문 -> ('&&' 또는 '||', 조각 값 함수들)   # 결정문 -> 조건 숫자, (변수, 경로 결정문들) -> 후보
        self.info['stmts'].sort(key=lambda s: s[0])
        self.starts = [s[0] for s in self.info['stmts']]
        for d in self.decs:
            try:
                self.via, self.leaf, self.inv = False, set(), []
                f, vs, nums = translate(d.cond, self.consts, lambda t, call, d=d: self.var_of(t, call, d))
                if self.via:   # 대입을 거친 값: 조건 숫자 ± 대입 식 숫자도 경계 후보로 (v = a + 5; v > 100 → a 95)
                    cn = translate(d.cond, self.consts, lambda t, call: _fixed(0))[2]
                    nums = list(dict.fromkeys(nums))
                    nums = nums + [n + k * m for n in cn for m in nums for k in (1, -1)] + \
                        [_cdiv(n, m) for n in cn for m in nums if m and n % m == 0]   # twice(a) > 10 → 5 (정수는 정수로)
                if self.inv:   # 수학 함수: 조건 숫자를 역함수로 옮긴 값 (sqrt(x) > 3 → x 9)
                    cn = translate(d.cond, self.consts, lambda t, call: _fixed(0))[2]
                    for g in self.inv:
                        for n in cn:
                            try:
                                y = g(n)
                            except (ValueError, OverflowError):
                                continue
                            if isinstance(y, (int, float)) and abs(y) < 1e9:
                                nums.append(round(y, 6))
                if d.kind == 'switch':
                    self.labels[d] = {lab: const_eval(lab[5:], self.consts) for lab, _ in d.labels if lab != 'default'}
                    nums += list(self.labels[d].values())
            except Manual as e:
                self.manual[d] = str(e)
                continue
            self.fn[d], self.vars[d], self.dnums[d] = f, vs, nums
            self.parts[d] = self.split(d)
            # 조건 값에 직접 들어가는 변수만 (앞 분기 결과를 정하는 변수는 빼서 경계 검사를 가볍게)
            self.own[d] = [v for v in vs if v in self.leaf]
            flt = any(isinstance(n, float) for n in nums)
            for v in vs:
                if v not in self.cols:
                    self.cols.append(v)
                self.isf[v] = self.isf.get(v, False) or self.declared(v, FLOAT_T, text) or (
                    (flt or v in self.float_hint) and not self.declared(v, INT_T, text))   # 형을 모르면 소수점 숫자·stub 형으로
                self.uns[v] = self.uns.get(v, False) or self.declared(v, UNSIGNED_T, text)
                self.cands.setdefault(v, set()).update(nums)
        for v, ns in self.cands.items():
            self.cands[v] = self.expand(v, ns)

    def split(self, d):
        """조건을 맨 바깥 || (없으면 &&)로 나눈 조각들. 조각 하나씩 맞춰 가는 탐색(climb)에 쓴다."""
        c = bc.norm(d.cond)
        for op in ('||', '&&'):
            pieces, dep, last, i = [], 0, 0, 0
            while i < len(c):
                dep += (c[i] == '(') - (c[i] == ')')
                if dep == 0 and c.startswith(op, i):
                    pieces.append(c[last:i])
                    last = i = i + 2
                    continue
                i += 1
            if pieces:
                try:
                    fs = [translate(x, self.consts, lambda t, call: self.var_of(t, call, d))[0]
                          for x in pieces + [c[last:]]]
                except Manual:
                    return None
                return op, fs
        return None

    def expand(self, v, ns):
        """숫자마다 경계 양쪽 (정수 ±1, float ±0.1), 0 이상 작은 값부터."""
        s = self.step(v)
        vals = {0, 1} | {round(n + k * s, 6) for n in ns for k in (-1, 0, 1)}
        if self.uns[v]:
            vals = {x for x in vals if x >= 0}
        if not self.isf[v]:
            vals = {int(x) for x in vals}
        return sorted(vals, key=lambda x: (x < 0, abs(x)))

    def cands_for(self, v, req):
        """req 경로의 조건에 나온 숫자로만 만든 v의 후보 (다른 분기 숫자는 섞지 않는다)."""
        ds = frozenset(d for d in req if v in self.vars.get(d, ()))
        if (v, ds) not in self.ccache:
            self.ccache[(v, ds)] = self.expand(v, {n for d in ds for n in self.dnums[d]}) if ds else self.cands[v]
        return self.ccache[(v, ds)]

    def var_of(self, t, call, d, pos=None, depth=0):
        if not call:
            return self.value(t, d.pos if pos is None else pos, d)
        if depth > 8:
            raise Manual('stub 호출이 너무 깊음')
        here = d.pos if pos is None else pos

        def args():   # 호출 자리의 인자 값들
            out = [translate(a, self.consts, lambda n, c: self.var_of(n, c, d, here, depth + 1)) for a in call]
            return [a[0] for a in out], [v for a in out for v in a[1]], [n for a in out for n in a[2]]

        if t in self.stubs.pfns:   # 인자를 쓰는 stub 코드: 반환식에 인자 값을 넣는다
            ps, ret = self.stubs.pfns[t]
            fs, vs, ns = args()
            if len(fs) != len(ps):
                raise Manual(f'{t}: 인자 개수가 stub 코드와 다름')
            bind = dict(zip(ps, zip(fs, [vs] * len(fs), [ns] * len(fs))))
            self.via = True   # 조건 숫자를 인자 쪽으로 옮긴 후보도 쓴다 (twice(a) > 10 → a 5)
            return translate(ret, self.consts, lambda n, c: (
                bind[n] if not c and n in bind else
                _var(n) if not c else self.var_of(n, c, d, here, depth + 1)))
        # 수학 함수: stub 코드가 반환식 하나로 따로 정하지 않았으면 (실제 구현을 넣은 것도) 실제 값으로
        if t in MATH and t not in self.stubs.fns:
            fn, inv = MATH[t]
            fs, vs, ns = args()
            if inv:
                self.inv.append(inv)
            return (lambda V, R: fn(*(f(V, R) for f in fs))), vs, ns
        r = self.stubs.ref(t)
        self.leaf.update(r[1])
        if not r[1]:
            self.fixed.setdefault(d, set()).add(t)
        if re.search(rf'\b{FLOAT_T}', self.stubs.rtype.get(t, '')):
            self.float_hint.update(r[1])   # float stub이 돌려주는 변수는 형을 모르면 float로
        return r

    def value(self, t, pos, d):
        """pos 바로 앞에서 t의 값 → (값 함수, 열 이름들, 숫자들).
        마지막 대입부터 거슬러 간다. 조건 안의 대입은 '그 결과로 왔으면 이 값, 아니면 그 앞 값'이 된다.
        R(i)는 결정문 i의 결과 (outcome이 같은 입력으로 계산)."""
        a = self.info['body'].get(d.func, (0, 0))[0]
        hits = sorted((h, p) for p, hs in self.info['set'].items()
                      if t == p or t.startswith(p + '.') or t.startswith(p + '->')
                      for h in hs if a < h[0] < pos)
        base = re.match(r'\w+', t).group()
        if not hits:
            if base in self.info['locals'].get(d.func, ()) and base not in self.info['params'].get(d.func, ()):
                return _fixed(0)   # ponytail: 대입 전 지역 변수는 0으로 본다 (모든 갈래에서 대입하면 안 쓰임)
            if base not in self.info['params'].get(d.func, ()) and not self.declared_var(base):
                if base in self.constlike:   # 읽은 enum·#define에 있는데 값을 못 구함 → 입력으로 만들면 경로가 틀어진다
                    raise Manual(f'상수 값 모름 ({base})')
                self.guessed.add(base)   # 어디에도 정의가 없으면 다른 모듈 전역 변수로 보고 입력으로 둔다
            self.leaf.add(t)
            return _var(t)
        self.via = True
        h, p = hits[-1]
        key = (t, h[0], d)   # 경로에서 이미 정해진 조건을 빼므로 결정문마다 따로
        if key in self.vmemo:
            return self.vmemo[key]
        where = f'{t}: L{h[1]}'
        if p != t:
            raise Manual(f'{where} 구조체 통째로 대입')
        if not h[4].strip():
            raise Manual(f'{where} 못 다루는 대입')
        ctx = self.stmt_at(h[0])
        if ctx is None:
            raise Manual(f'{where} 식 안에서 대입')
        parent, guards, loop, sws = ctx
        if loop and not loop < d.pos < self.info['loops'].get(loop, 0):
            raise Manual(f'{where} 반복문 안에서 대입 (반복 횟수에 따라 다름)')
        known = self.known.setdefault(d, closure(d.reqs()))
        conds, dep = [], []
        for pd, po in sorted(closure(([parent] if parent else []) + list(guards) + list(sws)) - known,
                             key=lambda c: c[0].pos):
            if pd not in self.fn:
                raise Manual(f'{where} 대입이 L{pd.line} 결과에 따라 달라짐')
            i = self.decs.index(pd)
            conds.append((i, po if isinstance(po, tuple) else (po,)))
            dep += self.vars[pd]
        f, vs, ns = translate(h[4], self.consts, lambda t2, call: self.var_of(t2, call, d, h[0]))
        if h[3] != '=' or conds:
            pf, pvs, pns = self.value(t, h[0], d)
            if h[3] != '=':   # v += e → v_앞 + e
                f = (lambda a, b, o: lambda V, R: o(a(V, R), b(V, R)))(pf, f, BIN[h[3]])
                vs, ns = pvs + vs, pns + ns
            if conds:   # 그 조건으로 왔으면 이 대입 값, 아니면 앞 값
                f = (lambda a, b, cs: lambda V, R: a(V, R) if all(R(i) in os for i, os in cs) else b(V, R))(
                    f, pf, conds)
                vs, ns = vs + dep + pvs, ns + pns
        out = f, list(dict.fromkeys(vs)), list(dict.fromkeys(ns))   # 앞 값의 숫자가 겹쳐 쌓이지 않게
        self.vmemo[key] = out
        return out

    def stmt_at(self, k):
        """토큰 k가 든 단순 문장의 (부모, 가드, 반복문, switch 묶음). 조건식 안이면 None."""
        i = bisect.bisect_right(self.starts, k) - 1
        st = self.info['stmts']
        return st[i][2] if i >= 0 and k < st[i][1] else None

    def diagnose(self, t):
        """못 채운 분기 t를 숫자로만 설명한다 (화면용, 이름 없음)."""
        path = bc.need(*t)
        allv = {v for pd, _ in path for v in self.vars.get(pd, ())}
        own = {v for pd, _ in path for v in self.own.get(pd, ())}
        fixed = {n for pd, _ in path for n in self.fixed.get(pd, ())}
        word = {None: 'x', 'limit': '상', 'time': '시', 'ok': 'o'}   # 범례는 branch_cov 화면에
        tried = ''.join(word.get(r, '?') for _, _, r in self.trace.get(t, [])[-8:])
        cand = max((len(self.cands_for(v, dict(path))) for v in allv), default=0)
        return f'경로 {len(path):>2} · 변수 {len(allv):>3}/빠른 {len(own):>2} · 후보 {cand:>3} · 고정stub {len(fixed)} · 탐색 {tried}'

    def why_not(self, t, stop=None):
        names = sorted({n for pd, _ in bc.need(*t) for n in self.fixed.get(pd, ())})
        hint = (f" — 고정 stub {', '.join(n + '()' for n in names)} 때문일 수 있음, stub 코드를 추가해 볼 것"
                if names else '')
        if stop == 'time':
            return f'계산 시간 한도({self.budget:g}초)를 넘김' + hint
        if stop == 'limit':
            return '값 조합이 많아 탐색 상한에 걸림' + hint
        return '갈 수 없는 경로이거나 후보 밖 값이 필요함 (경로 조건을 모두 만족하는 값이 없음)' + hint

    def declared_var(self, name):
        """읽은 코드·헤더 어딘가에 `형 [*]name;`·`= …`·`[`·`,`·`)` 꼴로 변수로 선언됐는가."""
        if name not in self.isvar:
            n = re.escape(name)
            self.isvar[name] = name in self.declnames or name in self.setnames or bool(re.search(
                # 상수에는 없는 쓰임: f(&이름), 이름.x, 이름->x, 이름[i]
                rf'[(,=]\s*&\s*{n}\b|\b{n}\s*(?:\.|->|\[)', self.text)) or bool(re.search(
                rf'\b(?!(?:return|case|goto|else|sizeof|if|while|switch)\b)[A-Za-z_]\w*[\s*]+{n}'
                rf'\s*(?:[;=,)]|\[)', self.text) or re.search(
                # 매크로 선언 VAR(uint8, AUTOMATIC) name; P2VAR(…) name — 쉼표가 있어 캐스트 (uint8)name 과 구별
                rf'\b[A-Z][A-Z0-9_]*\s*\([^()]*,[^()]*\)[\s*]+{n}\s*(?:[;=,)]|\[)', self.text))
        return self.isvar[name]

    @staticmethod
    def declared(v, pat, text):
        name = re.findall(r'\w+', v.split(' ')[0])[-1]
        return bool(re.search(rf'\b{pat}(?:[ \t]+\w+)*[ \t]*\**[ \t]*{re.escape(name)}\s*[;,)=\[(]', text))

    def step(self, v):
        return 0.1 if self.isf[v] else 1

    def outcome(self, d, V, memo=None):
        """memo: 같은 V로 이미 계산한 결정문 결과 (대입 값이 앞 결정문 결과에 따라 달라질 때 씀)"""
        if d not in self.fn:
            return None
        memo = {} if memo is None else memo
        if d not in memo:
            memo[d] = None
            try:
                x = self.fn[d](V, lambda i: self.outcome(self.decs[i], V, memo))
            except Exception:
                return None
            if d.kind != 'switch':
                memo[d] = 'T' if x else 'F'
            else:
                memo[d] = next((lab for lab, val in self.labels[d].items() if x == val),
                               'default' if any(lab == 'default' for lab, _ in d.labels) else None)
        return memo[d]

    def boundary(self, d, o, V):
        """한 변수를 경계 한 칸 움직이면 결과가 바뀌는가 (= 경계 바로 옆 값인가)."""
        if d.kind == 'switch' or not self.own[d]:   # 움직일 값이 없으면(고정 stub 등) 그대로 인정
            return True
        for v in self.own[d]:
            for x in (V[v] + self.step(v), V[v] - self.step(v)):
                if self.uns[v] and x < 0:
                    continue
                if self.outcome(d, Defaults({**V, v: round(x, 6)})) not in (o, None):
                    return True
        return False

    def solve(self, req, bnd, limit=LIMIT, focus=False):
        """req {결정문: 결과}를 모두 만족하고 bnd 분기는 경계에 있는 값. 못 찾으면 None.
        focus: 경로 조건에 직접 쓰인 변수만 바꾸고 나머지(앞 분기에서 값을 바꾸는 쪽)는 0으로 둔다.
        못 찾은 까닭은 self.stop: 'limit'(탐색 상한) / 'time'(시간 한도) / None(후보를 다 봄)."""
        V, nodes, self.stop = Defaults(), [0], None
        if time.monotonic() > self.deadline:   # 시간이 다 됐으면 시작도 안 한다
            self.stop = 'time'
            return None
        keep = {v for d in req for v in self.own[d]} if focus else None
        need = {d: [x for x in self.vars[d] if keep is None or x in keep] for d in req}
        for d, o in req.items():   # 바꿀 변수가 없는 결정문은 지금 값(0)으로 바로 판정
            if not need[d] and (self.outcome(d, V) != o or ((d, o) in bnd and not self.boundary(d, o, V))):
                return None
        order = list(dict.fromkeys(v for d in sorted(req, key=lambda d: d.pos) for v in need[d]))
        cl = {v: self.cands_for(v, req) for v in order}
        checks = [(d, o) for d, o in req.items() if need[d]]

        def ok(v):
            memo = {}   # 같은 V에서 앞 분기 결과를 한 번만 계산
            for d, o in checks:
                if v in need[d] and all(x in V for x in need[d]):
                    if self.outcome(d, V, memo) != o or ((d, o) in bnd and not self.boundary(d, o, V)):
                        return False
            return True

        def go(i):
            if i == len(order):
                return True
            v = order[i]
            for x in cl[v]:
                nodes[0] += 1
                if nodes[0] > limit:
                    self.stop = 'limit'
                    break
                if nodes[0] % 64 == 0 and time.monotonic() > self.deadline:
                    self.stop = 'time'
                    break
                V[v] = x
                if ok(v) and go(i + 1):
                    return True
            V.pop(v, None)
            return False

        return dict(V) if go(0) else None

    def sparse(self, req, bnd, limit=LIMIT):
        """나머지 변수는 0으로 두고 1개, 그다음 2개만 바꿔 본다 (그 분기만 보고 값을 정하듯).
        경로 끝 결정문의 변수부터 본다. 못 찾으면 None, 까닭은 self.stop."""
        self.stop = None
        if time.monotonic() > self.deadline:
            self.stop = 'time'
            return None
        ds = sorted(req, key=lambda d: d.pos)
        vs = list(dict.fromkeys(v for d in reversed(ds) for v in self.vars[d]))
        cl = {v: self.cands_for(v, req) for v in vs}
        nodes = [0]

        def ok(V):
            memo = {}
            return all(self.outcome(d, V, memo) == req[d] and ((d, req[d]) not in bnd or self.boundary(d, req[d], V))
                       for d in ds)

        def tries():
            yield {}
            for v in vs:
                for x in cl[v]:
                    yield {v: x}
            for i, v in enumerate(vs):
                for w in vs[i + 1:]:
                    for x in cl[v]:
                        for y in cl[w]:
                            yield {v: x, w: y}

        for V in tries():
            nodes[0] += 1
            if nodes[0] > limit:
                self.stop = 'limit'
                return None
            if nodes[0] % 64 == 0 and time.monotonic() > self.deadline:
                self.stop = 'time'
                return None
            if ok(Defaults(V)):
                return V
        return None

    def score(self, V, req):
        """req를 얼마나 맞췄나: 맞춘 결정문마다 100, 못 맞춘 && 참·|| 거짓은 맞춘 조각 수만큼."""
        memo, s = {}, 0
        R = lambda i: self.outcome(self.decs[i], V, memo)
        for d, o in req.items():
            if self.outcome(d, V, memo) == o:
                s += 100
            elif self.parts.get(d) and (self.parts[d][0], o) in (('&&', 'T'), ('||', 'F')):
                for f in self.parts[d][1]:
                    try:
                        s += bool(f(V, R)) == (o == 'T')
                    except Exception:
                        pass
        return s

    def climb(self, req, bnd, limit=LIMIT, start=None):
        """나머지는 0으로 두고(start가 있으면 그 값에서), 변수 하나를 바꿔 맞는 조각이 늘면 그 값을 두고
        다음으로 (사람이 하나씩 맞추듯)."""
        self.stop = None
        ds = sorted(req, key=lambda d: d.pos)
        vs = list(dict.fromkeys(v for d in reversed(ds) for v in self.vars[d]))
        cl = {v: self.cands_for(v, req) for v in vs}
        V = dict(start or {})
        best, full, nodes = self.score(Defaults(V), req), 100 * len(req), 0
        while best < full:
            better = None
            for v in vs:
                for x in cl[v]:
                    if x == V.get(v, 0):
                        continue
                    nodes += 1
                    if nodes > limit:
                        self.stop = 'limit'
                        return None
                    if nodes % 64 == 0 and time.monotonic() > self.deadline:
                        self.stop = 'time'
                        return None
                    s = self.score(Defaults({**V, v: x}), req)
                    if s > best:
                        better, best = {**V, v: x}, s
                        break
                if better:
                    break
            if better is None:
                return None   # 더 나아지지 않음
            V = better
        W = Defaults(V)
        return V if all(self.boundary(d, o, W) for d, o in bnd) else None

    def solve_alone(self, t, limit=LIMIT):
        """분기 t 하나만 채우는 값. 앞 분기는 안 타는 쪽(0)으로 두고 경로 조건 변수만 바꾸는 빠른 탐색부터,
        경계 바로 옆 값 → 경계 아닌 값 순서로. 못 찾으면 None, 까닭은 self.stop."""
        req, stops = dict(bc.need(*t)), set()
        log = self.trace.setdefault(t, [])   # 진단용: (경계?, 빠른?, 결과)
        for bnd in ([] if t in self.plain else [{t}]) + [set()]:
            for focus in ('sparse', 'climb', True, False):   # 1~2개만 → 조각씩 → 경로 조건 변수만 → 전체
                V = (self.sparse(req, bnd, limit) if focus == 'sparse' else
                     self.climb(req, bnd, limit) if focus == 'climb' else
                     self.solve(req, bnd, limit, focus))
                log.append((bool(bnd), focus, 'ok' if V is not None else self.stop))
                if V is not None:
                    if not bnd:
                        self.plain.add(t)   # 갈 수는 있는데 경계 바로 옆 값은 못 찾음 → 일반 값으로 채운다
                    return V
                if self.stop == 'time':
                    return None
                if focus is False:
                    stops.add(self.stop)   # 전체 탐색이 아닌 건 못 찾아도 못 가는 증거가 아니다
        self.stop = 'limit' if 'limit' in stops else None
        return None

    def run(self, V):
        """값 V로 지나는 결정문과 결과. 결과를 모르면 None (그 아래는 안 따라감)."""
        V, res, memo = Defaults(V), {}, {}
        for d in self.decs:
            if all(res.get(pd) == po for pd, po in d.reqs()):
                res[d] = self.outcome(d, V, memo)
        return res

    def generate(self):
        """→ (TC 목록 [(값, 지나는 결과, 경계로 채운 분기)], 수동 {분기: 이유})
        1) 분기마다 혼자 채우는 값을 먼저 찾아 둔다 (못 가는 분기는 여기서 거른다).
        2) 되도록 여러 분기를 한 TC에 묶는다. 묶음 순서에 따라 TC 수가 달라서 코드 순서와 경로가 긴 것부터
           두 가지로 해 보고 적은 쪽을 쓴다. 못 묶은 분기도 1)의 값으로 TC를 만든다.
        3) 상한에 걸린 분기는 남은 시간에 상한을 크게 해서 다시 찾는다."""
        targets = [(d, o) for d in self.decs for o in d.outcomes()]
        manual = {}
        for t in targets:
            bad = next((pd for pd, _ in bc.need(*t) if pd in self.manual), None)
            if bad:
                manual[t] = f'L{bad.line} {self.manual[bad]}'
        todo = [t for t in targets if t not in manual]
        # 시간은 단계별로 나눈다: 하나씩 풀기 40% → 묶기 ~80% → 남은 분기 다시 찾기 ~100%.
        # (하나씩 풀기가 시간을 다 쓰면 묶지 못해 분기마다 TC가 하나씩 생긴다)
        t0, runs, self.plain, self.alone = time.monotonic(), [], set(), {}
        self.deadline = t0 + self.budget * 0.4
        for t in todo:
            V = self.solve_alone(t)
            if V is not None:
                self.alone[t] = V
            elif self.stop is None:   # 후보를 다 봐도 없음
                manual[t] = self.why_not(t)
            # 상한·시간에 걸린 건 묶음과 3)에서 다시 해 본다
        todo = [t for t in todo if t not in manual]
        deep = sorted(todo, key=lambda t: -len(bc.need(*t)))
        for order, end in ((todo, 0.6), (deep, 0.8)):   # 두 번째 순서는 첫 번째가 남긴 시간까지
            self.deadline = t0 + self.budget * end
            runs.append(self.greedy(order, dict(manual)))
        tcs, manual = min(runs, key=lambda r: (len(r[1]), len(r[0])))   # 수동이 적은 쪽, 그다음 TC가 적은 쪽
        self.deadline = t0 + self.budget
        for t in sorted(manual, key=lambda t: t[0].pos):
            if t in manual and ('탐색 상한' in manual[t] or '시간 한도' in manual[t]) and                     not self.retry(t, tcs, manual):
                if self.stop == 'time':
                    break
        if time.monotonic() > self.deadline:   # 시간이 모자라 다시 찾지 못한 것은 시간 초과로 알린다
            for t, why in manual.items():
                manual[t] = why.replace('값 조합이 많아 탐색 상한에 걸림',  # 고정 stub 힌트는 그대로
                                        f'값 조합이 많고 계산 시간 한도({self.budget:g}초) 안에 못 찾음')
        self.deadline = float('inf')
        return tcs, manual

    def passes(self, V, t):
        """값 V로 분기 t를 (경계 조건까지) 이미 지나가는가."""
        W, memo = Defaults(V), {}
        return all(self.outcome(d, W, memo) == o for d, o in bc.need(*t)) and             (t in self.plain or self.boundary(*t, W))

    def cover(self, V, todo):
        """값 V로 todo 중 실제로 채우는 분기 → (전체 값, 지나는 결과, 채운 분기)."""
        full = {v: V.get(v, 0) for v in self.cols}
        res = self.run(full)
        got = {u for u in todo if res.get(u[0]) == u[1] and
               (u in self.plain or self.boundary(*u, Defaults(full)))}
        got |= {(d, 'F') for d, o in got if o == 'T' and d.kind in LOOPS and (d, 'F') in todo}
        return full, res, got

    def retry(self, t, tcs, manual):
        """t 하나만 큰 상한으로 다시 푼다. 찾으면 TC를 더하고 그 TC가 채우는 수동 분기를 뺀다."""
        V = self.solve_alone(t, LIMIT * 100)
        if V is None:
            return False
        full, res, got = self.cover(V, manual)
        if t not in got:
            return False
        tcs.append((full, res, got))
        for u in got:
            del manual[u]
        return True

    def greedy(self, todo, manual):
        tcs = []
        while todo:
            req, bnd, V, picked = {}, set(), None, set()
            for t in todo:   # 앞 분기와 같이 갈 수 있으면 같은 TC에 묶는다
                r2 = dict(req)
                if any(r2.setdefault(pd, po) != po for pd, po in bc.need(*t)):
                    continue
                b2 = bnd if t in self.plain else bnd | {t}
                if V is None and t in self.alone:   # 첫 분기는 혼자 찾아 둔 값 그대로
                    V2 = self.alone[t]
                elif V is None:
                    V2 = self.solve(r2, b2, LIMIT)
                elif self.passes(V, t):   # 지금 값으로 이미 지나가면 그대로 묶는다
                    V2 = V
                elif time.monotonic() <= self.deadline:
                    # 묶을 때는 지금 TC 값에서 몇 개만 바꿔 맞춰 본다 → 안 되면 경로 조건 변수만 짧게 찾는다
                    V2 = self.climb(r2, b2, LIMIT // 10, start=V)
                    if V2 is None and self.stop != 'time':
                        V2 = self.solve(r2, b2, LIMIT // 10, focus=True)
                else:
                    continue   # 시간이 다 되면 더 묶지 않는다
                if V2 is not None:
                    req, bnd, V = r2, b2, V2
                    picked.add(t)
            if V is None:
                stop = 'time' if time.monotonic() > self.deadline else self.stop
                manual.update((t, self.why_not(t, stop)) for t in todo)
                break
            full, res, got = self.cover(V, todo)
            manual.update((t, '값은 찾았지만 실제 경로가 달라짐 (확인 필요)') for t in picked - got)
            tcs.append((full, res, got))
            todo = [t for t in todo if t not in got and t not in picked]
        return tcs, manual

    @staticmethod
    def fmt(x):
        return f'{x:g}' if isinstance(x, float) else str(x)

    def tc_text(self, tcs, start=1):
        blocks = []
        for n, (V, res, _) in enumerate(tcs, start):
            lines = [f"경계 TC{n}  " + ', '.join(f'{k}={self.fmt(v)}' for k, v in V.items())]
            i = 0
            for d in self.decs:
                if d not in res:
                    continue
                if res[d] is None:
                    hint = ' — 들어가면 [T/F], 안 들어가면 [F]' if d.kind in LOOPS else ''
                    lines.append(f'# L{d.line} {d.kind}: 직접 확인 ({self.manual.get(d, "결과 모름")}){hint}')
                    continue
                i += 1
                line = bc.tc_line(i, d, res[d])
                if res[d] == 'T' and d.kind in LOOPS:   # 반복문에 들어가면 끝날 때 F도 탄다
                    line = line.replace('[T]', '[T/F]', 1)
                lines.append(line)
            blocks.append('\n'.join(lines))
        return '\n\n'.join(blocks) + '\n'

    def write_csv(self, path, tcs, start=1):
        with open(path, 'w', newline='', encoding='utf-8-sig') as f:   # 엑셀에서 한글이 안 깨지게 BOM
            w = csv.writer(f)
            w.writerow(['TC'] + self.cols + ['경계로 채운 분기'])
            for n, (V, _, got) in enumerate(tcs, start):
                hit = sorted((d.line_of(o), o, (d, o) in self.plain) for d, o in got)
                w.writerow([f'TC{n}'] + [self.fmt(V[c]) for c in self.cols] +
                           ['; '.join(f"L{ln} {o}{'(경계 아님)' if p else ''}" for ln, o, p in hit)])
