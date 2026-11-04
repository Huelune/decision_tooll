"""C structure parsing and annotation-based coverage estimation."""

import bisect
import re

TERMINAL = {"break", "continue", "return", "goto"}
KEYKIND = {"if": "if", "elseif": "if", "while": "while", "dowhile": "while", "for": "for"}
TC_LINE = re.compile(
    r"^\s*(?:D\s*(\d+)\s*)?\[\s*([TFtf](?:\s*/\s*[TFtf])?)\s*\]\s*(.+?)\s*$"
)  # [T] [F] [T/F]
LITERAL = r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\''


def norm(s):
    """공백을 없애고 전체를 감싼 괄호를 벗긴다."""
    s = re.sub(LITERAL + r"|\s+", lambda m: m.group() if m.group()[0] in "\"'" else "", s)
    while s.startswith("(") and _match_end(s) == len(s) - 1:
        s = s[1:-1]
    return s


def _match_end(s):
    d = 0
    for m in re.finditer(LITERAL + r"|[\s\S]", s):
        i, c = m.start(), m.group()
        d += c == "("
        d -= c == ")"
        if d == 0:
            return i
    return -1


def strip_code(src):
    """주석·전처리 줄 제거. 리터럴은 원문과 토큰 경계를 보존한다."""
    pat = LITERAL + r"|//[^\n]*|/\*.*?\*/|^[ \t]*#(?:\\\n|[^\n])*"
    return re.sub(
        pat,
        lambda m: m.group() if m.group()[0] in "\"'" else re.sub(r"[^\n]", " ", m.group()),
        src,
        flags=re.S | re.M,
    )


class Dec:
    def __init__(self, kind, cond, line, parent, in_loop, pos):
        self.kind, self.cond, self.line = kind, cond, line
        self.parent = parent  # (Dec, 결과): 이 결정문이 평가되려면 필요한 바깥 결과
        self.in_loop, self.pos = in_loop, pos
        self.next = None  # else if 사슬의 다음 결정문
        self.labels = []  # switch: [(결과 이름, 줄)]
        self.guards = ()  # 앞쪽 조기 반환을 피한 결과들 [(Dec, 결과)]
        self.func = None
        self.uncertain = ""  # 단일 경로로 표현할 수 없는 제어 흐름

    def reqs(self):
        """이 결정문이 평가되려면 필요한 결과들."""
        return ([self.parent] if self.parent else []) + list(self.guards)

    def outcomes(self):
        return [lab for lab, _ in self.labels] if self.kind == "switch" else ["T", "F"]

    def line_of(self, o):
        return dict(self.labels).get(o, self.line)


def parse(clean, func=None):
    """결정문 목록과 TC 매칭용 색인 {(종류, 조건): [(위치, Dec, 결과)]}을 만든다."""
    toks = [(m.group(), m.start()) for m in re.finditer(LITERAL + r"|\w+|\S", clean)]
    nls = [m.start() for m in re.finditer("\n", clean)]
    decs, keys, funcs = [], {}, []
    guards = []  # 지금 위치에서 유효한 조기 반환 회피 조건 (블록을 나가면 줄어든다)
    cur = {"func": None}
    info = {
        "params": {},
        "body": {},
        "set": {},
    }  # set: 이름 -> [(토큰 위치, 줄, 받은 stub 이름 또는 None)]

    def T(k):
        return toks[k][0] if 0 <= k < len(toks) else ""

    def line(k):
        return bisect.bisect_right(nls, toks[k][1]) + 1

    def close(k):
        d = 0
        for j in range(k, len(toks)):
            c = toks[j][0]
            if c in ("(", "[", "{"):
                d += 1
            elif c in (")", "]", "}"):
                d -= 1
                if d == 0:
                    return j
        raise SyntaxError("bracket")

    def paren(k):
        if T(k) != "(":
            raise SyntaxError("paren")
        j = close(k)
        return clean[toks[k][1] + 1 : toks[j][1]], j + 1

    def index(key, k, d, o):
        keys.setdefault(key, []).append((k, d, o))

    def add(kind, cond, at, parent, loop):
        d = Dec(kind, cond, line(at), parent, loop, at)
        d.guards, d.func = tuple(guards), cur["func"]
        decs.append(d)
        if kind in KEYKIND:
            index((KEYKIND[kind], norm(cond)), at, d, None)
        return d

    def params(a, b):
        """함수 머리 ( … ) 안의 인자 이름들."""
        names, last, depth = set(), None, 0
        for k in range(a + 1, b + 1):
            t = T(k)
            if t in ("[", "("):
                depth += 1
            elif t in ("]", ")") and depth:
                depth -= 1
            elif depth == 0 and t in (",", ")"):
                if last and last != "void":
                    names.add(last)
                last = None
            elif depth == 0 and _is_ident(t):
                last = t
        return names

    def skip_to_semi(k):
        while T(k) not in (";", "}"):
            if T(k) == "":
                raise SyntaxError("eof")
            k = close(k) + 1 if T(k) in ("(", "[", "{") else k + 1
        return k + (T(k) == ";")

    def body(k, parent, loop):
        n = len(guards)
        r = stmt(k, parent, loop)
        del guards[n:]
        return r

    def stmt(k, parent, loop):
        t = T(k)
        if t == "{":
            k, last, n = k + 1, None, len(guards)
            while T(k) != "}":
                if T(k) == "":
                    raise SyntaxError("eof")
                k, last = stmt(k, parent, loop)
            del guards[n:]
            return k + 1, last
        if t == "if":
            cond, k2 = paren(k + 1)
            d = add("if", cond, k, parent, loop)
            k, last = body(k2, (d, "T"), loop)
            branches = [((d, "T"), last in TERMINAL)]  # (갈래, return 등으로 끝나나)
            while T(k) == "else":
                if T(k + 1) == "if":
                    cond, k2 = paren(k + 2)
                    e = add("elseif", cond, k + 1, (d, "F"), loop)
                    d.next, d = e, e
                    k, last = body(k2, (d, "T"), loop)
                    branches.append(((d, "T"), last in TERMINAL))
                else:
                    index(("else", ""), k, d, "F")
                    k, last = body(k + 1, (d, "F"), loop)
                    branches.append(((d, "F"), last in TERMINAL))
                    break
            else:
                branches.append(((d, "F"), False))
            go_on = [b for b, ends in branches if not ends]
            if (
                len(go_on) == 1 and len(branches) > 1
            ):  # 빠져나가는 갈래가 하나뿐이면 뒤 문장은 그 결과를 요구
                guards.append(go_on[0])
            return k, (None if go_on else "return")
        if t == "switch":
            at = k
            cond, k = paren(k + 1)
            d = add("switch", cond, at, parent, loop)
            if T(k) != "{":
                return stmt(k, parent, loop)
            k, last, group, cur, fresh, n = k + 1, "start", [], parent, True, len(guards)
            ambiguous = False
            while T(k) != "}":
                if T(k) in ("case", "default"):
                    del guards[n:]
                    j = k + 1
                    while T(j) != ":":
                        if T(j) == "":
                            raise SyntaxError("eof")
                        j += 1
                    if T(k) == "default":
                        lab, key = "default", ("default", "")
                    else:
                        v = norm(clean[toks[k + 1][1] : toks[j][1]])
                        lab, key = "case " + v, ("case", v)
                    d.labels.append((lab, line(k)))
                    index(key, k, d, lab)
                    if not group:  # 앞 case에서 흘러들어오면(fall-through) 어느 case인지 모른다
                        fresh = last in TERMINAL or last == "start"
                    group.append(lab)
                    k = j + 1
                    continue
                if group:
                    cur = (d, group[0]) if len(group) == 1 and fresh else parent
                    ambiguous = len(group) != 1 or not fresh
                    group = []
                first = len(decs)
                k, last = stmt(k, cur, loop)
                if ambiguous:
                    for child in decs[first:]:
                        child.uncertain = "공유 case 또는 fall-through 경로 확인 필요"
            del guards[n:]
            return k + 1, None
        if t == "while":
            cond, k2 = paren(k + 1)
            d = add("while", cond, k, parent, loop)
            return body(k2, (d, "T"), True)[0], None
        if t == "for":
            j = close(k + 1)
            parts = clean[toks[k + 1][1] + 1 : toks[j][1]].split(";")
            mid = parts[1] if len(parts) == 3 else ""
            d = add("for", mid, k, parent, loop) if mid.strip() else None
            return body(j + 1, (d, "T") if d else parent, True)[0], None
        if t == "do":
            k = body(k + 1, parent, True)[0]
            if T(k) != "while":
                raise SyntaxError("do")
            cond, k2 = paren(k + 1)
            add("dowhile", cond, k, parent, loop)
            return k2 + (T(k2) == ";"), None
        if t in TERMINAL:
            return skip_to_semi(k), t
        if t == ";":
            return k + 1, None
        if re.fullmatch(r"[A-Za-z_]\w*", t) and T(k + 1) == ":":  # goto 레이블
            return stmt(k + 2, parent, loop)
        return skip_to_semi(k), None

    k = 0
    while k < len(toks):
        if T(k) != "{":
            k += 1
            continue
        j = close(k)
        if T(k - 1) == ")":  # 함수 본문
            d, m = 0, k - 1
            for m in range(k - 1, -1, -1):
                d += (T(m) == ")") - (T(m) == "(")
                if d == 0:
                    break
            name = T(m - 1)
            if func is None or name == func:
                funcs.append(name)
                cur["func"], guards[:] = name, []
                info["body"][name] = (k, j)
                info["params"][name] = params(m, k - 1)
                stmt(k, None, False)
        k = j + 1
    if func is not None and not funcs:
        raise LookupError("func")
    info["set"] = assignments(toks, T, line)
    return decs, keys, funcs, info


def _is_ident(t):
    return bool(re.fullmatch(r"[A-Za-z_]\w*", t))


def assignments(toks, T, line):
    """값이 바뀌는 곳: 'a' / 'p->b' / 's.x' -> [(토큰 위치, 줄, v = f(); 면 f)]. a[i] 는 a 로 센다."""

    def op(k, s):  # k부터 붙어 있는 문자 토큰이 s인가
        return all(T(k + i) == c for i, c in enumerate(s)) and all(
            toks[k + i][1] == toks[k][1] + i for i in range(len(s)) if k + i < len(toks)
        )

    out = {}
    for k in range(len(toks)):
        if not _is_ident(T(k)) or T(k + 1) == "(":
            continue
        b = k  # 멤버 접근이면 맨 앞 이름까지 거슬러 올라간다
        while True:
            if T(b - 1) == "." and _is_ident(T(b - 2)):
                b -= 2
            elif op(b - 2, "->") and _is_ident(T(b - 3)):
                b -= 3
            else:
                break
        e = k + 1
        while T(e) == "[":  # a[i] = ...
            d = 0
            for e in range(e, len(toks)):
                d += (T(e) == "[") - (T(e) == "]")
                if d == 0:
                    break
            e += 1
        if T(e) == "." or op(e, "->"):
            continue  # 더 긴 멤버 접근의 앞부분
        # f(&x) 로 넘긴 값은 stub이 바꾸지 않는 것으로 본다 (대입으로 세지 않음)
        changed = (
            (T(e) == "=" and T(e + 1) != "=")
            or (T(e) != "" and T(e) in "+-*/%&|^" and op(e + 1, "="))
            or op(e, "<<=")
            or op(e, ">>=")
            or op(e, "++")
            or op(e, "--")
            or op(b - 2, "++")
            or op(b - 2, "--")
        )
        call = None  # v = f(...); 처럼 stub 반환값을 그대로 받는가
        if T(e) == "=" and _is_ident(T(e + 1)) and T(e + 2) == "(":
            d, j = 0, e + 2
            for j in range(e + 2, len(toks)):
                d += (T(j) == "(") - (T(j) == ")")
                if d == 0:
                    break
            call = T(e + 1) if T(j + 1) == ";" else None
        if changed:
            out.setdefault("".join(T(i) for i in range(b, k + 1)), []).append((k, line(k), call))
    return out


def tc_key(text):
    s = text.strip().rstrip("{").strip().rstrip(";").strip()
    m = re.fullmatch(r"(?:else\s*if|elseif|if)\s*(\(.*\))", s)
    if m:
        return "if", norm(m.group(1))
    m = re.fullmatch(r"while\s*(\(.*\))", s)
    if m:
        return "while", norm(m.group(1))
    m = re.fullmatch(r"for\s*\((.*)\)", s)
    if m:
        parts = m.group(1).split(";")
        return "for", norm(parts[1] if len(parts) == 3 else m.group(1))
    m = re.fullmatch(r"case\s+(.+?)\s*:?", s)
    if m:
        return "case", norm(m.group(1))
    if re.fullmatch(r"default\s*:?", s):
        return "default", ""
    if s == "else":
        return "else", ""
    return None


def read_tcs(text):
    tcs = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [
            x for x in block.splitlines() if x.strip() and not x.lstrip().startswith(("#", "//"))
        ]  # 주석 줄
        tc = {"name": "", "items": [], "bad": []}
        for i, x in enumerate(lines):
            m = TC_LINE.match(x)
            if m:
                tc["items"].append(
                    (m.group(1) or "?", re.sub(r"[\s/]", "", m.group(2).upper()), m.group(3))
                )
            elif i == 0:
                tc["name"] = x.strip()
            else:
                tc["bad"].append(x.strip())
        if tc["items"] or tc["bad"]:
            tcs.append(tc)
    return tcs


def template(d, o):
    """못 채운 분기 (d, o)를 TC 표기 줄의 분기 부분으로 만든다. 결과 [T]/[F]는 따로 붙인다."""
    if d.kind == "switch":
        return o + ":"
    c = re.sub(
        LITERAL + r"|\s+", lambda m: m.group() if m.group()[0] in "\"'" else " ", d.cond
    ).strip()
    if d.kind == "for":
        return f"for(;{c};)"
    return f"{'while' if d.kind == 'dowhile' else d.kind}({c})"


def analyze(c_src, tc_text, func=None, parsed=None):
    decs, keys, funcs, info = parsed or parse(strip_code(c_src), func)
    tcs = read_tcs(tc_text)
    cov, warns, matched = {}, [], 0  # warns: (TC 순번, D번호, 화면용 문구, 상세용 원문)
    for n, tc in enumerate(tcs, 1):
        got = {}

        def imply(dd):
            for pd, po in dd.reqs():
                if po not in got.get(pd, {}):
                    got.setdefault(pd, {})[po] = False
                    imply(pd)

        for bad in tc["bad"]:
            warns.append((n, "?", "줄 형식을 모름 (D번호 [T/F] 조건문)", bad))
        for dno, tf, text in tc["items"]:
            key = tc_key(text)
            if key is None:
                warns.append((n, dno, "분기 종류를 모름 (if/else/case/default/while/for)", text))
                continue
            cands = keys.get(key, [])
            if not cands:
                warns.append((n, dno, "코드에서 못 찾음 (조건문 글자 확인)", text))
                continue
            if len(cands) > 1:
                warns.append(
                    (n, dno, "같은 조건이 여러 곳: 함수/조건을 구분하기 전까지 집계하지 않음", text)
                )
                continue
            _, d, o = cands[0]
            matched += 1
            # tf: 'T' / 'F' / 'TF'(반복문처럼 둘 다 탐). case·else 는 T일 때만 그 결과, [F]는 평가만 됨
            for outcome in dict.fromkeys(tf) if o is None else [o] if "T" in tf else []:
                got.setdefault(d, {})[outcome] = True
            imply(d)

        for d, outs in got.items():
            for o, explicit in outs.items():
                cov.setdefault((d, o), []).append(f"TC{n}" + ("" if explicit else "*"))
            if d.in_loop or d.kind in (
                "while",
                "for",
                "dowhile",
            ):  # 반복하면 T·F 둘 다 나올 수 있다
                continue
            if len(outs) > 1:
                warns.append((n, "-", f"L{d.line} 결정문에 서로 다른 결과가 함께 적힘", ""))
            if "F" in outs and d.next and d.next not in got:
                warns.append((n, "-", f"L{d.next.line} else if 결과가 빠짐 (앞이 F면 평가됨)", ""))

    missing = sorted(
        ((d.line_of(o), d, o) for d in decs for o in d.outcomes() if (d, o) not in cov),
        key=lambda m: m[0],
    )
    return {
        "decs": decs,
        "cov": cov,
        "warns": warns,
        "missing": missing,
        "tcs": tcs,
        "info": info,
        "funcs": funcs,
        "matched": matched,
        "lines": sum(len(t["items"]) for t in tcs),
    }


def _kind(d, o):
    return o.split()[0] if d.kind == "switch" else d.kind


C_WORDS = {
    "sizeof",
    "int",
    "char",
    "short",
    "long",
    "unsigned",
    "signed",
    "float",
    "double",
    "void",
    "const",
    "volatile",
    "struct",
    "union",
    "enum",
    "true",
    "false",
}


def need(d, o):
    """(d, o)에 가려면 꼭 거쳐야 하는 (결정문, 결과)들. 코드 순서대로."""
    seen, stack = {d: o}, [d]
    while stack:
        for pd, po in stack.pop().reqs():
            if pd not in seen:
                seen[pd] = po
                stack.append(pd)
    return sorted(seen.items(), key=lambda t: t[0].pos)


def values(path, info):
    """경로의 조건에 쓰인 이름마다 값이 어디서 오는지. 다른 함수는 stub으로 본다."""
    out = {}
    for d, _ in path:
        c = re.sub(r"\s+", "", d.cond)
        for m in re.finditer(r"[A-Za-z_]\w*(?:(?:\.|->)[A-Za-z_]\w*)*", c):
            t, base = m.group(), re.match(r"\w+", m.group()).group()
            if (
                t in out
                or base in C_WORDS
                or base.isupper()
                or base.endswith("_t")
                or c[max(m.start() - 1, 0) : m.start()] == "."
                or c[max(m.start() - 2, 0) : m.start()] == "->"
            ):
                continue
            out[t] = "stub 반환값" if c[m.end() : m.end() + 1] == "(" else source_of(t, d, info)[0]
    return out


def source_of(t, d, info):
    """결정문 d의 조건에 쓰인 이름 t(멤버 경로 포함)의 값 출처.
    (설명, 경계값 TC의 열 이름 — 테스트에서 직접 넣을 수 없으면 None)"""
    a, _b = info["body"].get(d.func, (0, 0))
    base = re.match(r"\w+", t).group()
    hits = sorted(
        h
        for p, hs in info["set"].items()
        if t == p or t.startswith(p + ".") or t.startswith(p + "->")
        for h in hs
        if a < h[0] < d.pos
    )
    lines = ", ".join(f"L{h[1]}" for h in hits[-3:])
    if len(hits) == 1 and hits[0][2]:
        return f"{hits[0][2]} stub 반환값 ({lines})", hits[0][2] + "()"
    if hits:
        return f"함수 안에서 계산 ({lines})", None
    if base in info["params"].get(d.func, set()):
        return ("입력 인자" if t == base else f"입력 인자 {base}가 가리키는 값"), t
    return "전역/외부 값", t


def tc_line(i, d, o):
    return f"D{i} [{'T' if d.kind == 'switch' else o}] {template(d, o)}"


def howto(d, o, info):
    """(d, o)를 채우는 TC를 쓰는 데 필요한 것: 거쳐야 할 분기와 조건에 쓰인 값의 출처."""
    path = need(d, o)
    out = [
        f"L{d.line_of(o)} {_kind(d, o)} {'' if d.kind == 'switch' else o} 채우기",
        "  꼭 거쳐야 하는 분기 (여기 없는 분기는 어떤 결과든 됨, D 번호는 실제 순서로 고칠 것)",
    ]
    out += ["    " + tc_line(i, pd, po) for i, (pd, po) in enumerate(path, 1)]
    vals = values(path, info)
    if vals:
        w = max(map(len, vals))
        out.append("  조건에 쓰인 값")
        out += [f"    {n:<{w}}  {v}" for n, v in vals.items()]
    if any(pd.in_loop or pd.kind in ("while", "for", "dowhile") for pd, _ in path):
        out.append("  반복문이 끼어 있어 반복 횟수에 따라 결과가 달라질 수 있음")
    return out
