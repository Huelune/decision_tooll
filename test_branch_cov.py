"""실행: python test_branch_cov.py  (secret 이 든 이름이 화면에 안 나오는지도 본다)"""
import contextlib
import csv
import os
import sys
import tempfile
import io

import branch_cov as bc

C_SRC = r'''
#include <stdio.h>
#define SECRET_MAX 10
/* if (in_comment) { */
int secret_func(int a, int c, int x, int secret_mode)
{
    const char *s = "if (in_string) {";
    if (a == 1) {
        a = 2;
    }
    if ((c == x) && (c > 10)) {
        c = 0;
    } else if (c < 10) {
        c = 1;
    } else {
        c = 2;
    }
    switch (secret_mode) {
    case 1:
        x = 1;
        break;
    case 2:
        if (x > 3) x = 0;
        break;
    default:
        break;
    }
    for (int i = 0; i < SECRET_MAX; i++) { if (i == a) break; }
    return c;
}
int other(int q) { if (q) return 1; return 0; }
'''

TC = '''secret_tc_name_1
D1 [T] if(a==1)
D2 [T] if((c==x) && (c > 10))
D3 [T] case 1:

D1 [T] if(a==1)
D2 [T] elseif(c < 10)
D3 [T] case 2:
D4 [T] if(secret_flag)

D1 [F] if(a==1)
D2 [F] if((c==x) && (c > 10))
D3 [T] default:
'''


def test():
    r = bc.analyze(C_SRC, TC, func='secret_func')
    kinds = [d.kind for d in r['decs']]
    assert kinds == ['if', 'if', 'elseif', 'switch', 'if', 'for', 'if'], kinds  # 주석·문자열 속 if 제외
    # 2+2+2+3+2+2+2 = 15, 채움: a T/F, 사슬 T/F, elseif T/F, case 3개 = 9
    total = sum(len(d.outcomes()) for d in r['decs'])
    assert (total, total - len(r['missing'])) == (15, 9), (total, len(r['missing']))
    chain_head = r['decs'][1]
    assert r['cov'][(chain_head, 'F')] == ['TC2*', 'TC3'], r['cov'][(chain_head, 'F')]  # elseif에서 추론
    assert r['cov'][(r['decs'][2], 'F')] == ['TC3']   # [F] if(첫 조건) = 사슬 전체 F (else)
    msgs = [(n, w) for n, _, w, _ in r['warns']]
    assert any(n == 2 and '못 찾음' in w for n, w in msgs), msgs
    assert not any('else if' in w for n, w in msgs), msgs
    # 만들 때도 사슬은 D 하나: else if 갈래는 [T] elseif(…) 한 줄, 모두 F면 [F] if(첫 조건)
    head, eif = r['decs'][1], r['decs'][2]
    assert [bc.tc_line(i, d, o) for i, (d, o) in enumerate(bc.chain(bc.need(eif, 'T')), 1)] == [
        'D1 [T] elseif(c < 10)'], bc.chain(bc.need(eif, 'T'))
    assert bc.chain([(head, 'F'), (eif, 'F')]) == [(head, 'F')]
    assert bc.chain([(head, 'T')]) == [(head, 'T')]
    # case 2 아래의 if 는 case 2 를, for 안의 if 는 for T 를 부모로 갖는다
    assert r['decs'][4].parent == (r['decs'][3], 'case 2')
    assert r['decs'][6].in_loop and not r['decs'][4].in_loop

    screen = bc.render_screen(r)
    assert 'secret' not in screen.lower(), screen
    assert len(screen.splitlines()) <= 40 and max(map(len, screen.splitlines())) <= 120
    assert 'secret_flag' in bc.render_report(r)  # 상세 파일에는 원문이 있다

    # 함수 지정 없이: 다른 함수까지 센다
    assert len(bc.analyze(C_SRC, TC)['funcs']) == 2

    # 오류 화면에도 경로·이름이 안 나온다
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert bc.main(['C:/secret_dir/secret.c', 'secret_tc.txt']) == 1
    assert 'secret' not in buf.getvalue().lower(), buf.getvalue()
    # 예시 파일: 15개 중 13개, 못 채운 것은 L23 if T 와 L27 case 3, 경고 없음 ([F] if 는 사슬 전체 F)
    ex = bc.analyze(open('example.c', encoding='utf-8').read(),
                    open('example_tc.txt', encoding='utf-8').read(), 'example')
    assert [(ln, o) for ln, _, o in ex['missing']] == [(23, 'T'), (27, 'case 3')], ex['missing']
    assert [(n, w[:3]) for n, _, w, _ in ex['warns']] == [], ex['warns']
    # 화면에서 넣어 주는 줄은 다시 읽으면 같은 분기로 맞아야 한다
    for d in ex['decs']:
        for o in d.outcomes():
            hits = bc.analyze('', f"D1 [{'T' if d.kind == 'switch' else o}] {bc.template(d, o)}",
                              parsed=bc.parse(bc.strip_code(open('example.c', encoding='utf-8').read())))
            assert any(k[1] == o and k[0].line == d.line for k in hits['cov']), (d.line, o)

    # [T/F]: 반복문처럼 한 TC에서 둘 다 탐. 반복문이 아닌 if에 쓰면 경고
    p = bc.parse(bc.strip_code(open('example.c', encoding='utf-8').read()), 'example')
    r = bc.analyze('', 'D1 [T / f] while(r > 100)\nD2 [T/F] if(a==1)', parsed=p)
    w = {x.line: x for x in p[0]}
    assert {(d.line, o) for d, o in r['cov']} == {(35, 'T'), (35, 'F'), (6, 'T'), (6, 'F')}, r['cov']
    assert [m for _, _, m, _ in r['warns']] == ['L6 결정문에 서로 다른 결과가 함께 적힘'], r['warns']
    assert r['cov'][(w[35], 'F')] == ['TC1']

    test_howto()
    test_bva()
    test_expr()
    test_follow()
    test_gui()
    test_utf8_output()
    print('OK')
    print(screen)


G_SRC = r'''
int g(int p, int q, struct s_t *st)
{
    int v = get_val(&q);
    if (p == 0) return -1;
    if (stub_ok() && v > q) { st->n = 1; }
    else if (st->mode == 2 && g_flag) return 2;
    if (st->n > 0) { v++; }
    return 0;
}
'''


def test_howto():
    p = bc.parse(bc.strip_code(G_SRC))
    d = {x.line: x for x in p[0]}
    # 조기 반환: 뒤 분기들은 p==0 F 를 요구하고, else if 가 return 하는 사슬 뒤에는 요구가 생기지 않는다
    assert d[7].reqs() == [(d[6], 'F'), (d[5], 'F')] and d[8].reqs() == [(d[5], 'F')], d[8].reqs()
    # 뒤 분기를 적으면 조기 반환을 피한 결과(p==0 F)도 채워진다
    r = bc.analyze('', 'D1 [T] if(st->n > 0)', parsed=p)
    assert r['cov'][(d[5], 'F')] == ['TC1*'], r['cov']
    assert [(x.line, o) for x, o in bc.need(d[7], 'T')] == [(5, 'F'), (6, 'F'), (7, 'T')]
    vals = bc.values(bc.need(d[7], 'T'), p[3])
    assert vals == {'p': '입력 인자', 'stub_ok': 'stub 반환값', 'v': 'get_val stub 반환값 (L4)',
                    'q': '입력 인자', 'st->mode': '입력 인자 st가 가리키는 값',   # &q 로 넘겨도 안 바뀜
                    'g_flag': '전역/외부 값'}, vals
    assert bc.values(bc.need(d[8], 'T'), p[3])['st->n'] == '대입을 따라 계산 (L6)'
    # 채우는 법의 줄들을 그대로 TC로 쓰면 그 분기가 채워진다
    tc = '\n'.join(bc.tc_line(i, x, o) for i, (x, o) in enumerate(bc.need(d[7], 'T'), 1))
    assert (d[7], 'T') in bc.analyze('', tc, parsed=p)['cov'], tc


H_SRC = r'''
#define LIMIT 10
typedef enum { ST_IDLE, ST_RUN = 5, ST_STOP } st_e;
float get_temp(void);
int h(float t, unsigned char n, st_e s)
{
    int ret = Stub_Read();
    if (t >= 25.5f && n < LIMIT) { return 1; }
    if (s == ST_RUN || ret != 0) { n++; }
    if (get_temp() > 30) { return 2; }
    return 0;
}
'''


def test_bva():
    import csv
    import os
    import tempfile
    import bva
    src = open('example.c', encoding='utf-8').read()
    p = bc.parse(bc.strip_code(src), 'example')
    m = bva.Model(p, [src])
    tcs, manual = m.generate()
    # case 5개 + x>3 T/F 가 둘 다 case 2 → 최소 6개. while(r>100)의 r은 대입을 경로대로 따라가 계산
    assert len(tcs) == 6 and not manual, (len(tcs), manual)
    # 모든 분기를 경계로 채운다: T와 F가 경계 한 칸 차이
    got = set().union(*(g for _, _, g in tcs))
    assert len(got) == 15
    by = {(d.line, o): V for V, _, g in tcs for d, o in g}
    assert by[(23, 'T')]['x'] == 4 and by[(23, 'F')]['x'] == 3
    assert by[(12, 'T')]['c'] == 9 and by[(12, 'F')]['c'] == 10
    assert by[(6, 'T')]['a'] == 1 and by[(6, 'F')]['a'] in (0, 2)
    # 만든 TC 표기를 다시 읽으면 다 채워진다
    r = bc.analyze('', m.tc_text(tcs), parsed=p)
    assert not r['missing'] and not r['warns'], r
    # CSV: 머리줄 + TC 6줄
    path = os.path.join(tempfile.mkdtemp(), 'bva.csv')
    m.write_csv(path, tcs)
    rows = list(csv.reader(open(path, encoding='utf-8-sig')))
    assert rows[0] == ['TC', 'a', 'c', 'x', 'mode', '경계로 채운 분기'] and len(rows) == 7, rows

    # float은 0.1, unsigned는 음수 없음, #define·enum 값, stub 반환값을 받은 지역 변수
    p = bc.parse(bc.strip_code(H_SRC))
    m = bva.Model(p, [H_SRC], stub_zero=False)   # stub 반환값을 자유 입력으로
    assert {v: (m.isf[v], m.uns[v]) for v in m.cols} == {
        't': (True, False), 'n': (False, True), 's': (False, False),
        'Stub_Read()': (False, False), 'get_temp()': (True, False)}
    tcs, manual = m.generate()
    assert not manual and len(tcs) == 3, (len(tcs), manual)
    vals = {(d.line, o): V for V, _, g in tcs for d, o in g}
    assert vals[(8, 'T')]['t'] == 25.5 and vals[(8, 'F')]['t'] == 25.4, vals
    assert vals[(10, 'T')]['get_temp()'] == 30.1 and vals[(10, 'F')]['get_temp()'] == 30
    assert all(V['n'] >= 0 for V, _, _ in tcs)

    # stub: 정의 안 하면 0 고정 → 못 가는 분기는 그 stub 이름을 알려 준다
    m = bva.Model(p, [H_SRC])
    tcs, manual = m.generate()
    assert m.cols == ['t', 'n', 's'] and len(tcs) == 3, m.cols
    assert [(d.line, o) for d, o in manual] == [(10, 'T')] and 'get_temp()' in list(manual.values())[0], manual
    # 뼈대: 원형이 있으면 그 반환형, 없으면 int
    names = bva.needed_stubs(p)
    assert names == ['Stub_Read', 'get_temp'], names
    assert bva.skeleton(names, [H_SRC]) == ['int Stub_Read(void) { return 0; }',
                                           'float get_temp(void) { return 0; }']
    assert bva.skeleton(names, [H_SRC], 'int Stub_Read(void) { return 0; }') == ['float get_temp(void) { return 0; }']
    # stub 코드: 전역을 돌려주면 그 전역이 CSV 열, float stub이면 0.1 단위
    stub = 'int Stub_Read(void) { return g_ret; }\nfloat get_temp(void) { return g_temp; }'
    m = bva.Model(p, [H_SRC], stub)
    tcs, manual = m.generate()
    assert not manual and m.cols == ['t', 'n', 's', 'g_ret', 'g_temp'] and m.isf['g_temp'], m.cols
    vals = {(d.line, o): V for V, _, g in tcs for d, o in g}
    assert vals[(10, 'T')]['g_temp'] == 30.1 and vals[(10, 'F')]['g_temp'] == 30, vals
    s = bva.Stubs('int a(int x){return x;} int b(void){return 3;} int c(void){ if(g) return 1; return 0; }')
    assert s.ref('b')[0]({}, None) == 3 and s.ref('b')[1:] == ([], [3])
    assert s.ref('a')[1] == ['a()'] and s.ref('c')[1] == ['c()']
    assert s.ref('zz')[0]({}, None) == 0 and s.ref('zz')[1:] == ([], [0])
    assert bva.Stubs('', zero=False).ref('zz')[1] == ['zz()']
    assert s.desc('b') == 'stub 코드: 3 고정' and s.desc('a') == 'stub 코드: 인자로 계산' and '0 고정' in s.desc('zz')
    assert '자유 입력' in s.desc('c') and '수학 함수' in s.desc('sqrt')

    # 표준 수학 함수는 실제 값으로, 인자를 쓰는 stub 코드는 호출 자리 인자로 계산한다
    q_src = 'int q(int x, float y, int a) { if (sqrt(x) > 3) { return 1; } if (fabsf(y) < 2.5f) { return 2; }'             ' if (twice(a + 1) > 10) { return 3; } return 0; }'
    pq = bc.parse(bc.strip_code(q_src))
    mq = bva.Model(pq, [q_src], 'int twice(int v) { return v * 2; }')
    tcs, manual = mq.generate()
    assert not manual and bva.needed_stubs(pq) == ['twice'], (manual, bva.needed_stubs(pq))
    d1, d2, d3 = sorted(pq[0], key=lambda d: d.pos)
    vals = {(d.pos, o): V for V, _, g in tcs for d, o in g}
    assert vals[(d1.pos, 'T')]['x'] == 10 and vals[(d1.pos, 'F')]['x'] == 9, vals   # sqrt 경계 9 / 10
    assert abs(vals[(d2.pos, 'T')]['y']) in (2.4,) and abs(vals[(d2.pos, 'F')]['y']) == 2.5, vals
    assert vals[(d3.pos, 'T')]['a'] == 5 and vals[(d3.pos, 'F')]['a'] == 4, vals      # (a+1)*2 > 10
    # 수학 함수 이름으로 실제 구현(반환문 여럿)을 넣어도 실제 값으로 계산하고, 화면에는 개수만
    impl = ('float sqrt(float v) { float r = v; int i; for (i = 0; i < 20; i++) {'
            ' if (r == 0) return 0; r = (r + v / r) / 2; } return r; }\n')
    mq = bva.Model(pq, [q_src], impl + 'int twice(int v) { return v * 2; }')
    tcs, manual = mq.generate()
    assert not manual and mq.stubs.counts() == (2, 1, 0, 1), mq.stubs.counts()
    d = tempfile.mkdtemp()
    c_path, s_path = os.path.join(d, 'secret_q.c'), os.path.join(d, 'secret_stub.c')
    open(c_path, 'w', encoding='utf-8').write(q_src.replace('twice', 'secret_twice'))
    open(s_path, 'w', encoding='utf-8').write(impl + 'int secret_twice(int v) { return v * 2; }')
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert bc.main([c_path, '--stub', s_path]) == 0
    out = buf.getvalue()
    assert 'secret' not in out.lower() and 'stub 코드 함수 2개' in out and '4/6 채움' not in out, out
    assert bc.values(bc.need(p[0][2], 'T'), p[3], s)['get_temp'] == 'stub 정의 없음 → 0 고정'

    # 입력으로 정해지는 반복문: 들어가는 TC는 [T/F]로 적고 F도 채운 것으로 본다
    w_src = 'int w(int n) { while (n > 0) { n--; } return n; }'
    p = bc.parse(bc.strip_code(w_src))
    m = bva.Model(p, [w_src])
    tcs, manual = m.generate()
    assert not manual and len(tcs) == 1 and '[T/F] while(n > 0)' in m.tc_text(tcs), m.tc_text(tcs)
    assert len(bc.analyze('', m.tc_text(tcs), parsed=p)['missing']) == 0

    # 명령줄: CSV 저장, 화면에 이름이 안 나온다
    d = tempfile.mkdtemp()
    c_path = os.path.join(d, 'secret_mod.c')
    open(c_path, 'w', encoding='utf-8').write(C_SRC)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert bc.main([c_path, '--func', 'secret_func', '--csv', os.path.join(d, 'secret.csv')]) == 0
    assert 'secret' not in buf.getvalue().lower() and '경계값 TC' in buf.getvalue(), buf.getvalue()
    assert os.path.exists(os.path.join(d, 'secret.csv'))


F_SRC = r'''
int f(int a, int n)
{
    int ret = 0;
    int v = a + 5;
    int sum = 0;
    int i, w;
    ret = Stub_A();
    if (ret == 0) { ret = Stub_B(); }
    if (ret == 0) { return 1; }
    if (v > 100) { return 2; }
    for (i = 0; i < n; i++) { sum += a; w = Stub_C(); if (w == 1) { n = 0; } }
    if (sum > 3) { return 3; }
    return 0;
}
'''


def test_expr():
    """조건식 해석: eval 없이 C 규칙대로 계산한다."""
    import bva

    def ev(c, **V):
        f, _, _ = bva.translate(c, {'K': 3}, lambda t, call: bva._var(t))
        return f(V, None)
    assert ev('-7 / 2') == -3 and ev('-7 % 2') == -1 and ev('7 / 2') == 3   # 0 쪽으로 버림
    assert ev('010') == 8 and ev('0x10') == 16 and ev('1.5f') == 1.5          # 8진수, float 접미사
    assert ev('1 + 2 * 3 == 7 && !0') == 1 and ev('(1 + 2) * 3') == 9
    assert ev('a & 4 || b', a=4, b=0) == 1 and ev('1 << K') == 8 and ev('~0') == -1
    assert ev('(uint8)a > -1', a=0) == 1                                      # 형 변환은 벗긴다
    assert ev('a != 0 && 10 / a > 2', a=0) == 0                               # 단락 평가: 0으로 안 나눔
    for bad in ('a ? 1 : 2', 'sizeof(a)', 'UNKNOWN_K > 1', '(a > 1'):
        try:
            ev(bad, a=1)
            raise AssertionError(bad)
        except bva.Manual:
            pass
    try:
        ev('1 << a', a=1000)   # 큰 시프트는 계산하지 않는다 (outcome에서 결과 모름)
        raise AssertionError('shift')
    except ValueError:
        pass


def test_follow():
    """함수 안에서 대입한 값: 경로대로 따라가 stub 반환값·입력으로 계산한다."""
    import bva
    p = bc.parse(bc.strip_code(F_SRC))
    d = {x.line: x for x in p[0]}
    m = bva.Model(p, [F_SRC], stub_zero=False)
    tcs, manual = m.generate()
    # 반복문 안에서 쌓은 값만 수동 (반복 횟수에 따라 다름)
    assert sorted((x.line, o) for x, o in manual) == [(13, 'F'), (13, 'T')], manual
    assert all('반복문' in why for why in manual.values()) and d[12] not in m.manual, m.manual
    assert set(m.cols) == {'Stub_A()', 'Stub_B()', 'Stub_C()', 'a', 'n'}, m.cols
    by = {(x.line, o): V for V, _, g in tcs for x, o in g}
    # ret = A(); if (ret == 0) ret = B(); 뒤의 ret == 0 은 A·B 둘 다 0일 때만 참
    assert by[(10, 'T')]['Stub_A()'] == 0 and by[(10, 'T')]['Stub_B()'] == 0, by[(10, 'T')]
    assert m.run(by[(10, 'T')])[d[10]] == 'T' and m.run({**by[(10, 'T')], 'Stub_A()': 1})[d[10]] == 'F'
    # v = a + 5; v > 100 → a 경계 95 / 96
    assert by[(11, 'T')]['a'] == 96 and by[(11, 'F')]['a'] == 95, (by[(11, 'T')], by[(11, 'F')])
    # 반복문 안에서 같은 회차에 받은 stub 값은 그대로 쓴다
    assert (d[12], 'T') in {t for _, _, g in tcs for t in g}, tcs
    assert bva.needed_stubs(p) == ['Stub_A', 'Stub_B', 'Stub_C'], bva.needed_stubs(p)
    # 정의 안 한 stub은 0 → ret == 0 이 늘 참이라 뒤로 못 간다. 그 stub 이름을 알려 준다
    tcs, manual = bva.Model(p, [F_SRC]).generate()
    assert 'Stub_A()' in manual[(d[11], 'T')] and 'Stub_B()' in manual[(d[11], 'T')], manual

    # 경계를 못 맞추면 일반 값으로 채우고, 갈 수 없는 경로는 그 이유로 남긴다
    nb = 'int nb(int a) { if (a > 10) { if (a > 5) { return 1; } } return 0; }'
    p2 = bc.parse(bc.strip_code(nb))
    inner = p2[0][1]
    m2 = bva.Model(p2, [nb])
    tcs, manual = m2.generate()
    assert (inner, 'T') in m2.plain and (inner, 'T') in set().union(*(g for _, _, g in tcs)), m2.plain
    assert list(manual) == [(inner, 'F')] and bva.reason_kind(manual[(inner, 'F')]) == '갈 수 없는 경로/후보 밖'
    assert m2.diagnose((inner, 'F')).endswith('탐색 xxxxxxxx') and '고정stub 0' in m2.diagnose((inner, 'F'))
    path = os.path.join(tempfile.mkdtemp(), 'nb.csv')
    m2.write_csv(path, tcs)
    assert '(경계 아님)' in open(path, encoding='utf-8-sig').read()

    # 짧은 경로인데 변수가 많고 조건 셋을 같이 맞춰야 하는 분기: 조각씩 맞춰 간다 (전에는 탐색 상한)
    n = 24
    body = ['int z = 0, y = 0;'] + [f'if (a{i} > {37 * i + 11}) {{ y += {i}; }} else if (a{i} == {13 * i + 7}) {{ y -= 1; }}'
                                  for i in range(n)] + [f'z += a{i};' for i in range(4, n)]
    body.append('if (k == 3) { if (a0 > 5 && a1 == 7 && z > 30) { return 1; } if (a2 < -3 && a3 > 9) { return 2; } }')
    sh = 'int sh(' + ', '.join([f'int a{i}' for i in range(n)] + ['int k']) + ') {\n' + '\n'.join(body) + '\nreturn y; }'
    ps = bc.parse(bc.strip_code(sh))
    tcs, manual = bva.Model(ps, [sh]).generate()
    assert not manual and len(set().union(*(g for _, _, g in tcs))) == 102, manual

    # 대소문자 섞인 enum 값을 입력으로 착각하지 않는다: 값을 모르면 상수 값 모름, --inc 로 헤더 폴더를 주면 푼다
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, 'src'))
    os.makedirs(os.path.join(root, 'inc', 'sub'))
    e_src = ('int g_secretLevel;\n'
             'int e(int mode) { if (mode == eSecretRun) { return 1; } if (g_secretLevel > 3) { return 2; } return 0; }\n')
    c_path = os.path.join(root, 'src', 'secret_e.c')
    open(c_path, 'w', encoding='utf-8').write(e_src)
    open(os.path.join(root, 'inc', 'sub', 'secret.h'), 'w', encoding='utf-8').write(
        'typedef enum { eSecretIdle, eSecretRun = 4 } secret_mode_t;\n')
    pe = bc.parse(bc.strip_code(e_src))
    me = bva.Model(pe, [e_src])
    assert {'eSecretRun', 'g_secretLevel'} <= set(me.cols) and me.guessed == {'eSecretRun'}, me.cols
    # 읽은 enum에 있는데 값을 못 구하면 상수 값 모름 (입력으로 만들지 않음)
    pk = bc.parse(bc.strip_code(e_src))
    mk = bva.Model(pk, [e_src, 'enum { eSecretRun = SECRET_UNDEF };\n'])
    assert 'eSecretRun' not in mk.cols and '상수 값 모름' in mk.manual[pk[0][0]], mk.manual
    # 결과를 모르는 결정문도 D 번호를 차지한다: 뒤 결정문은 D2
    q_src = 'int g_secLv;\nint q(int mode) { int r = 0; if (mode == eSecretRun) { r = 1; } if (g_secLv > 3) { r = 2; } return r; }\n'
    mq = bva.Model(bc.parse(bc.strip_code(q_src)), [q_src, 'enum { eSecretRun = SECRET_UNDEF };\n'])
    tk = mq.tc_text(mq.generate()[0])
    assert '# D1 [?] if(mode == eSecretRun)' in tk and '\nD2 [' in tk and '\nD1 [' not in tk, tk
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert bc.main([c_path]) == 0
    out = buf.getvalue()
    assert 'secret' not in out.lower() and '입력 변수로 본 이름 1개' in out and '--inc' in out, out
    rep = open(os.path.join(tempfile.gettempdir(), bc.REPORT), encoding='utf-8').read()
    assert 'eSecretRun' in rep.split('# 선언 없이 입력 변수로 본 이름')[1], rep
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert bc.main([c_path, '--inc', os.path.join(root, 'inc')]) == 0
    assert '4/4 채움' in buf.getvalue() and '입력 변수로 본' not in buf.getvalue(), buf.getvalue()
    # 매크로 선언 VAR(uint8, AUTOMATIC) 이름 은 변수 (캐스트와 구별), 모르는 이름이 어디 나오는지 개수
    a_src = ('static VAR(uint8, VAR_NOINIT) s_secretSt;\n'
             'FUNC(void, CODE) af(VAR(uint8, AUTOMATIC) secretIn) {\n'
             '  VAR(uint8, AUTOMATIC) secretLoc;\n'
             '  if (s_secretSt == (uint8)secretK) { secretLoc = 1; }\n  if (secretIn > 3) { return; }\n}\n')
    pa = bc.parse(bc.strip_code(a_src))
    ma = bva.Model(pa, [a_src])
    assert ma.declared_var('s_secretSt') and not ma.declared_var('secretK') and 'secretIn' in ma.cols
    assert ma.guessed == {'secretK'} and not ma.manual, ma.manual
    assert 'secretLoc' in pa[3]['locals']['af']
    # 쉼표로 여러 개 선언, 두 단어 형, 매크로 선언 여러 개 / 호출 인자·함수 원형·대입은 선언 아님
    dn = bva.decl_names(bc.strip_code(
        'static uint8 g_a = 0u, g_secB = 1u, g_arr[3];\nunsigned char g_c;\n'
        'static VAR(uint8, VAR_NOINIT) s_x, s_y;\nint proto(int pa, int pb);\n'
        'void f(void) { foo(x, SECRET_K2); y = z * w; return v; if (q) r = 1; }\n'))
    assert {'g_a', 'g_secB', 'g_arr', 'g_c', 's_x', 's_y'} <= dn, dn
    assert not dn & {'SECRET_K2', 'x', 'z', 'w', 'v', 'y', 'r', 'proto'}, dn
    a_path = os.path.join(root, 'secret_a.c')
    open(a_path, 'w', encoding='utf-8').write(a_src)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert bc.main([a_path]) == 0
    assert '입력 변수로 본 이름 1개' in buf.getvalue() and 'secret' not in buf.getvalue().lower(), buf.getvalue()
    # 선언은 못 찾아도 대입·&넘김·멤버 접근이 있으면 변수 (다른 모듈 전역), 근거 없으면 상수 값 모름
    x_src = ('void init(void) { g_secDone = TRUE; Stub_Get(&g_secVal); }\n'
             'int xf(void) { if (g_secDone == TRUE) { return 1; } if (g_secVal > 3) { return 2; }'
             ' if (g_secSt.mode == 2) { return 3; } if (eSecMode == 1) { return 4; } return 0; }\n')
    px = bc.parse(bc.strip_code(x_src))
    mx = bva.Model(px, [x_src])
    assert {'g_secDone', 'g_secVal', 'g_secSt.mode'} <= set(mx.cols), mx.cols
    assert not mx.manual and mx.guessed == {'eSecMode'}, mx.manual
    # const 변수는 입력이 아니라 상수 (포인터 const·volatile 은 아님)
    k_src = ('static const uint8 c_secA = 5u;\nuint16 const c_secB = (c_secA + 1u);\n'
             'CONST(uint8, APPL_CONST) c_secC = 7u;\nconst uint8 *p_sec = 0;\n'
             'int kf(int v) { if (v > c_secA) { return 1; } if (v == c_secB) { return 2; } return c_secC; }\n')
    kc = bva.constants([k_src])
    assert (kc['c_secA'], kc['c_secB'], kc['c_secC']) == (5, 6, 7) and 'p_sec' not in kc, kc
    mk2 = bva.Model(bc.parse(bc.strip_code(k_src)), [k_src])
    assert mk2.cols == ['v'] and mk2.cstats['defines'] == 0, (mk2.cols, mk2.cstats)
    # --inc 에 파일을 직접 줘도 (확장자 상관없이) 읽고, 읽은 개수를 화면에
    txt = os.path.join(root, 'secret_defs.txt')
    open(txt, 'w', encoding='utf-8').write('#define eSecretRun (4u)\n#define secret_bad (NOPE + 1)\n')
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert bc.main([c_path, '--inc', txt]) == 0
    out = buf.getvalue()
    assert '4/4 채움' in out and '헤더 파일 1개' in out and '값을 못 구한 #define 1개' in out, out
    assert 'secret' not in out.lower()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert bc.main([c_path, '--inc', os.path.join(root, 'no_such')]) == 1
    assert '--inc' in buf.getvalue()
    # Windows 줄 끝(CRLF) 헤더의 #ifndef 안 #define (전에는 줄 끝 \r 때문에 못 읽었다)
    crlf = os.path.join(root, 'inc', 'crlf.h')
    open(crlf, 'wb').write(b'#ifndef LIM_X\r\n#define LIM_X        (7u)\r\n#endif\r\n')
    assert bva.constants([bc._read(crlf)])['LIM_X'] == 7 and '\r' not in bc._read(crlf)
    assert bva.constants(['#define LIM_Y   (8u)\r\n'])['LIM_Y'] == 8
    # 헤더끼리 서로 참조하는 상수, 식으로 된 #define, 함수 꼴 매크로는 빼기
    c = bva.constants(['enum { eX = BASE + 1, eY, eZ = LIM2 };\n',
                       '#define LIM2 (BASE * 2)  /* x */\n#define BIT3 (1U << 3)\n#define SQ(x) ((x) * (x))\n'
                       '#define BASE ((uint8)10)\n#define NAME "str"\n'])
    assert (c['BASE'], c['LIM2'], c['BIT3'], c['eX'], c['eY'], c['eZ']) == (10, 20, 8, 11, 12, 20), c
    assert 'SQ' not in c and 'NAME' not in c
    # --path: TC 하나의 예상 경로를 줄 번호·결과로만 (switch는 case 줄 번호)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert bc.main(['example.c', '--func', 'example', '--path', '1']) == 0
    out = buf.getvalue()
    assert 'TC1 예상 경로' in out and 'L6' in out and ('case' in out or 'default' in out), out
    assert len(out.splitlines()) <= 40

    # TC 없이 명령줄: 결정문 종류와 경계값 요약을 이유 종류로만 찍는다 (이름이 화면에 안 나온다)
    src = (F_SRC.replace('sum', 'secret_sum').replace('Stub_', 'Secret_stub_')
           .replace('(v > 100)', '(v > SECRET_LIMIT)'))
    c_path = os.path.join(tempfile.mkdtemp(), 'secret_mod.c')
    open(c_path, 'w', encoding='utf-8').write(src)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert bc.main([c_path]) == 0
    out = buf.getvalue()
    assert 'secret' not in out.lower() and '결정문 종류' in out and '못 채운 분기' not in out, out
    assert '상수 값 모름' in out and '고정 stub이 막음' in out, out   # 상수를 모르는 L11이 return이라 뒤도 막힘
    assert len(out.splitlines()) <= 40


def test_gui():
    import tkinter as tk
    import bva as bva_mod
    import branch_gui as g
    root = tk.Tk()
    root.withdraw()
    app = g.App(root, 'example.c', 'example')
    app.text.insert('1.0', open('example_tc.txt', encoding='utf-8').read())
    app.refresh()
    assert len(app.result['missing']) == 2 and '13/15' in app.status['text'], app.status['text']
    # 끝 줄(TC4)에 커서를 두고 L23 if T 를 더블클릭한 것처럼 넣는다
    iid = next(i for i, (d, o) in app.rows.items() if d.line == 23 and o == 'T')
    app.tree.focus(iid)
    app.text.mark_set('insert', 'end-1c')
    app.insert_line(None)
    assert app.tc_text().rstrip().endswith('D5 [T] if(x > 3)'), app.tc_text()[-40:]
    app.refresh()
    assert '14/15' in app.status['text'], app.status['text']
    app.only_miss.set(True)
    app.refresh()
    assert len(app.tree.get_children()) == 1  # 남은 건 switch 하나
    # case 3 을 고르면 채우는 법이 나오고, 새 TC로 넣으면 100%
    iid = next(i for i, (d, o) in app.rows.items() if o == 'case 3')
    app.tree.selection_set(iid)
    app.show_how()
    assert 'D1 [T] case 3:' in app.how.get('1.0', 'end'), app.how.get('1.0', 'end')
    app.add_tc()
    app.refresh()
    assert '15/15' in app.status['text'] and len(app.result['tcs']) == 5, app.status['text']
    # 경계값 TC: 빈 칸에서 만들면 다 채워지고 수동 경고가 없다
    import os
    import tempfile
    app.text.delete('1.0', 'end')
    path = os.path.join(tempfile.mkdtemp(), 'b.csv')
    app.budget.set('30')   # 시간 한도 칸 (숫자가 아니면 기본값으로 되돌린다)
    app.make_bva(path)
    assert os.path.exists(path) and len(app.result['tcs']) == 6, len(app.result['tcs'])
    app.budget.set('abc')
    app.make_bva(os.path.join(tempfile.mkdtemp(), 'b0.csv'))
    assert app.budget.get() == str(bva_mod.BUDGET)
    app.text.delete('1.0', 'end')
    app.make_bva(path)
    assert '15/15' in app.status['text'], app.status['text']
    assert not any('경계값 수동' in x for x in app.warn.get(0, 'end'))
    # stub 탭: 예시는 stub이 없어 뼈대가 안 붙고, 직접 쓴 stub 코드는 채우는 법에 나온다
    import bva
    assert bva.needed_stubs(app.parsed) == []   # 예시 함수에는 분기에 값이 들어가는 stub이 없다
    hdir = tempfile.mkdtemp()
    open(os.path.join(hdir, 'x.h'), 'w', encoding='utf-8').write('enum { eA = 2 };')
    g.messagebox.showinfo = lambda *a, **k: None
    app.add_inc(hdir)   # 헤더 폴더: 다음 경계값 계산부터 쓰고 C 파일을 바꿔도 유지
    assert len(app.inc) == 1 and app.inc[0] in app.headers
    app.stub.insert('1.0', 'int x_stub(void) { return 7; }')
    assert app.stubs().desc('x_stub') == 'stub 코드: 7 고정'

    # 저장 안 한 내용 지키기 (대화 상자는 가짜로 바꿔 답한다)
    asked = []
    g.messagebox.showerror = g.messagebox.showinfo = lambda *a, **k: asked.append(a[0])
    g.messagebox.askyesnocancel = lambda *a, **k: asked.append(a[0])   # None = 취소
    g.filedialog.asksaveasfilename = lambda *a, **k: ''                  # 저장 위치 고르기 취소
    before, n = app.tc_text(), len(app.result['tcs'])
    app.make_bva()                                    # CSV 취소 → TC 표기도 안 붙인다
    assert app.tc_text() == before
    app.make_bva(os.path.join(tempfile.mkdtemp(), 'no_dir', 'b.csv'))   # 못 쓰면 안 붙인다
    assert app.tc_text() == before and 'CSV' in asked
    path = os.path.join(tempfile.mkdtemp(), 'b2.csv')
    app.make_bva(path)                                # 붙일 때 CSV TC 번호는 왼쪽 순번에 이어서
    rows = list(csv.reader(open(path, encoding='utf-8-sig')))
    assert rows[1][0] == f'TC{n + 1}' and f'경계 TC{n + 1}' in app.tc_text(), rows[1]
    bad = os.path.join(tempfile.mkdtemp(), 'bad.c')
    open(bad, 'w', encoding='utf-8').write('int f(int a) { if (a) { return 1; }')   # 괄호 짝 틀림
    app.load_c(bad)                                   # 해석 실패 → 보던 파일 그대로
    assert app.parsed and app.src.startswith('/*') and app.func.get() == 'example'
    app.func.set('없는함수')
    app.set_func()                                    # 실패하면 함수 선택도 되돌린다
    assert app.func.get() == 'example'
    app.close()                                       # 저장 안 한 TC → 물어보고, 취소하면 안 닫힌다
    assert asked[-1] == 'TC 저장' and root.winfo_exists()
    app.text.delete('1.0', 'end')                     # 다 지운 것도 바뀐 것
    app.saved = 'x'
    app.close()
    assert asked[-1] == 'TC 저장' and root.winfo_exists()
    root.destroy()


def test_utf8_output():
    """영문 Windows처럼 출력이 cp1252여도 한글 화면이 죽지 않는다."""
    import subprocess
    env = dict(os.environ, PYTHONIOENCODING='cp1252')
    for args in (['branch_cov.py', 'example.c', '--func', 'example'], ['branch_cov.py', '--help']):
        r = subprocess.run([sys.executable, *args], capture_output=True, env=env)
        assert r.returncode == 0 and '분기'.encode() in r.stdout, r.stderr[-300:]


if __name__ == '__main__':
    test()
