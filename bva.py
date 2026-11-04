"""경계값 TC 만들기.

분기 결과(T/F, case)마다 조건이 참·거짓으로 바뀌는 경계 바로 양쪽 값으로 채우고,
그런 분기를 되도록 많이 묶어 TC 수를 줄인다. 정수는 ±1, float은 ±0.1이 경계 한 칸이다.

값을 테스트에서 직접 넣을 수 있는 것(입력 인자, stub 반환값, 전역)만 다룬다.
stub에 &로 넘긴 값은 stub이 바꾸지 않는 것으로 본다.
함수 안에서 계산되는 값이 끼거나 상수 값을 모르는 분기는 '수동'으로 남기고 이유를 적는다.
"""
import csv
import re

import branch_cov as bc
from c_expr import Expression, Manual, NUM, num

FLOAT_T = r'(?:float|double|\w*[fF](?:32|64)\b|\w*(?:float|real)\w*)'
UNSIGNED_T = r'(?:unsigned|uint\w*|u(?:8|16|32|64)\b|\w*_u(?:8|16|32|64)\w*|U(?:8|16|32|64)\w*|UINT\w*|bool\w*)'
INT_T = r'(?:char|short|int|long|signed|unsigned|u?int\w*|[us](?:8|16|32|64)\b|[US](?:8|16|32|64)\w*|U?INT\w*|bool\w*)'
LOOPS = ('while', 'for', 'dowhile')
LIMIT = 20000  # ponytail: 값 조합 탐색 상한. 넘으면 그 묶음은 못 푼 것으로 보고 다른 TC로 넘긴다


class Defaults(dict):
    def __missing__(self, key):
        return 0


def const_eval(expr, consts):
    return Expression(expr, consts)({})


def constants(srcs):
    """#define 숫자와 enum 값."""
    out = {'NULL': 0, 'TRUE': 1, 'FALSE': 0, 'true': 1, 'false': 0}
    for src in srcs:
        for m in re.finditer(rf'^[ \t]*#[ \t]*define[ \t]+(\w+)[ \t]+\(?[ \t]*(-?)[ \t]*({NUM})[ \t]*\)?'
                             r'[ \t]*(?://.*|/\*.*)?$', src, re.M):
            try:
                out[m.group(1)] = (-1 if m.group(2) else 1) * num(m.group(3))
            except Manual:
                out.pop(m.group(1), None)
        for m in re.finditer(r'\benum\b[^{;]*\{([^}]*)\}', bc.strip_code(src)):
            v = -1
            for item in m.group(1).split(','):
                name, _, expr = item.partition('=')
                if not name.strip():
                    continue
                try:
                    v = const_eval(expr, out) if expr.strip() else v + 1
                except Manual:
                    break  # 값을 모르면 뒤 항목도 모른다
                out[name.strip()] = v
    return out


class Model:
    def __init__(self, parsed, srcs):
        self.decs = sorted(parsed[0], key=lambda d: d.pos)
        self.info = parsed[3]
        self.consts = constants(srcs)
        text = '\n'.join(bc.strip_code(s) for s in srcs)
        self.fn, self.vars, self.manual, self.labels = {}, {}, {}, {}
        self.cands, self.isf, self.uns, self.cols = {}, {}, {}, []
        self.upper = {}
        for d in self.decs:
            try:
                if d.uncertain:
                    raise Manual(d.uncertain)
                if d.in_loop or d.kind == 'dowhile' or any(
                        p.func == d.func and p.pos < d.pos and p.kind in LOOPS for p in self.decs):
                    raise Manual('반복 실행 또는 선행 반복문의 경로 확인 필요')
                f = Expression(d.cond, self.consts, lambda t, call, d=d: self.var_of(t, call, d))
                vs, nums = f.variables, list(f.numbers)
                if any(self.declared(v, UNSIGNED_T, text) and not f.unsigned_safe(v) for v in vs):
                    raise Manual('signed/unsigned 변환 규칙 확인 필요')
                if d.kind == 'switch':
                    self.labels[d] = {lab: const_eval(lab[5:], self.consts) for lab, _ in d.labels if lab != 'default'}
                    nums += list(self.labels[d].values())
            except Manual as e:
                self.manual[d] = str(e)
                continue
            except SyntaxError:
                self.manual[d] = '못 다루는 식'
                continue
            self.fn[d], self.vars[d] = f, vs
            flt = any(isinstance(n, float) for n in nums)
            for v in vs:
                if v not in self.cols:
                    self.cols.append(v)
                self.isf[v] = self.isf.get(v, False) or self.declared(v, FLOAT_T, text) or (
                    flt and not self.declared(v, INT_T, text))   # 형을 모르면 식의 소수점 숫자로 판단
                self.uns[v] = self.uns.get(v, False) or self.declared(v, UNSIGNED_T, text)
                # Keep generated inputs representable without guessing wider target widths.
                if self.declared(v, r'(?:unsigned[ \t]+char|uint8_t|uint8|u8|U8|UINT8)', text):
                    self.upper[v] = 255
                elif self.declared(v, r'(?:_Bool|bool)', text):
                    self.upper[v] = 1
                self.cands.setdefault(v, set()).update(nums)
        for v, ns in self.cands.items():   # 숫자마다 경계 양쪽
            s = self.step(v)
            vals = {0, 1} | {round(n + k * s, 6) for n in ns for k in (-1, 0, 1)}
            if self.uns[v]:
                vals = {x for x in vals if x >= 0}
            if v in self.upper:
                vals = {x for x in vals if x <= self.upper[v]}
            if not self.isf[v]:
                vals = {int(x) for x in vals}
            self.cands[v] = sorted(vals, key=lambda x: (x < 0, abs(x)))   # 0 이상 작은 값부터

    def var_of(self, t, call, d):
        if call:
            return t + '()'
        why, col = bc.source_of(t, d, self.info)
        if col is None:
            raise Manual(f'{t}: {why}')
        return col

    @staticmethod
    def declared(v, pat, text):
        name = re.findall(r'\w+', v.split(' ')[0])[-1]
        return bool(re.search(rf'\b{pat}(?:[ \t]+\w+)*[ \t]*\**[ \t]*{re.escape(name)}\s*[;,)=\[(]', text))

    def step(self, v):
        return 0.1 if self.isf[v] else 1

    def outcome(self, d, V):
        if d not in self.fn:
            return None
        try:
            x = self.fn[d](V)
        except Exception:
            return None
        if d.kind != 'switch':
            return 'T' if x else 'F'
        for lab, val in self.labels[d].items():
            if x == val:
                return lab
        return 'default' if any(lab == 'default' for lab, _ in d.labels) else None

    def boundary(self, d, o, V):
        """한 변수를 경계 한 칸 움직이면 결과가 바뀌는가 (= 경계 바로 옆 값인가)."""
        if d.kind == 'switch':
            return True
        for v in self.vars[d]:
            for x in (V[v] + self.step(v), V[v] - self.step(v)):
                if (self.uns[v] and x < 0) or (v in self.upper and x > self.upper[v]):
                    continue
                if self.outcome(d, Defaults({**V, v: round(x, 6)})) not in (o, None):
                    return True
        return False

    def solve(self, req, bnd):
        """req {결정문: 결과}를 모두 만족하고 bnd 분기는 경계에 있는 값. 못 찾으면 None."""
        V, nodes = Defaults(), [0]
        if any(not self.vars[d] and self.outcome(d, V) != o for d, o in req.items()):
            return None
        order = [v for d in sorted(req, key=lambda d: d.pos) for v in self.vars[d]]
        order = list(dict.fromkeys(order))
        checks = [(d, o) for d, o in req.items() if self.vars[d]]

        def ok(v):
            for d, o in checks:
                if v in self.vars[d] and all(x in V for x in self.vars[d]):
                    if self.outcome(d, V) != o or ((d, o) in bnd and not self.boundary(d, o, V)):
                        return False
            return True

        def go(i):
            if i == len(order):
                return True
            v = order[i]
            for x in self.cands[v]:
                nodes[0] += 1
                if nodes[0] > LIMIT:
                    break
                V[v] = x
                if ok(v) and go(i + 1):
                    return True
            V.pop(v, None)
            return False

        return dict(V) if go(0) else None

    def run(self, V):
        """값 V로 지나는 결정문과 결과. 결과를 모르면 None (그 아래는 안 따라감)."""
        V, res = Defaults(V), {}
        for d in self.decs:
            if all(res.get(pd) == po for pd, po in d.reqs()):
                res[d] = self.outcome(d, V)
        return res

    def generate(self):
        """→ (TC 목록 [(값, 지나는 결과, 경계로 채운 분기)], 수동 {분기: 이유})
        묶는 순서에 따라 TC 수가 달라서, 코드 순서와 경로가 긴 것부터 두 가지로 해 보고 적은 쪽을 쓴다."""
        targets = [(d, o) for d in self.decs for o in d.outcomes()]
        manual = {}
        for t in targets:
            bad = next((pd for pd, _ in bc.need(*t) if pd in self.manual), None)
            if bad:
                manual[t] = f'L{bad.line} {self.manual[bad]}'
        todo = [t for t in targets if t not in manual]
        deep = sorted(todo, key=lambda t: -len(bc.need(*t)))
        runs = [self.greedy(order, dict(manual)) for order in (todo, deep)]
        return min(runs, key=lambda r: (len(r[1]), len(r[0])))   # 수동이 적은 쪽, 그다음 TC가 적은 쪽

    def greedy(self, todo, manual):
        tcs = []
        while todo:
            req, bnd, V = {}, set(), None
            for t in todo:   # 앞 분기와 같이 갈 수 있으면 같은 TC에 묶는다
                r2 = dict(req)
                if any(r2.setdefault(pd, po) != po for pd, po in bc.need(*t)):
                    continue
                V2 = self.solve(r2, bnd | {t})
                if V2 is not None:
                    req, bnd, V = r2, bnd | {t}, V2
            if V is None:
                manual.update((t, '경로 조건을 모두 만족하는 경계값을 못 찾음') for t in todo)
                break
            full = {v: V.get(v, 0) for v in self.cols}
            res = self.run(full)
            got = {t for t in todo if res.get(t[0]) == t[1] and self.boundary(*t, Defaults(full))}
            manual.update((t, '값은 찾았지만 실제 경로가 달라짐 (확인 필요)') for t in bnd - got)
            tcs.append((full, res, got))
            todo = [t for t in todo if t not in got and t not in bnd]
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
                    hint = ' — 반복 실행 결과는 직접 확인' if d.kind in LOOPS else ''
                    lines.append(f'# L{d.line} {d.kind}: 직접 확인 ({self.manual.get(d, "결과 모름")}){hint}')
                    continue
                i += 1
                line = bc.tc_line(i, d, res[d])
                lines.append(line)
            blocks.append('\n'.join(lines))
        return '\n\n'.join(blocks) + '\n'

    def write_csv(self, path, tcs, start=1):
        with open(path, 'w', newline='', encoding='utf-8-sig') as f:   # 엑셀에서 한글이 안 깨지게 BOM
            w = csv.writer(f)
            w.writerow(['TC'] + self.cols + ['경계로 채운 분기'])
            for n, (V, _, got) in enumerate(tcs, start):
                hit = sorted((d.line_of(o), o) for d, o in got)
                w.writerow([f'TC{n}'] + [self.fmt(V[c]) for c in self.cols] +
                           ['; '.join(f'L{ln} {o}' for ln, o in hit)])
