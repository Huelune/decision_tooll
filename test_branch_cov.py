"""실행: python test_branch_cov.py  (secret 이 든 이름이 화면에 안 나오는지도 본다)"""
import contextlib
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
    # 2+2+2+3+2+2+2 = 15, 채움: a T/F, 사슬 T/F, elseif T, case 3개 = 8
    total = sum(len(d.outcomes()) for d in r['decs'])
    assert (total, total - len(r['missing'])) == (15, 8), (total, len(r['missing']))
    chain_head = r['decs'][1]
    assert r['cov'][(chain_head, 'F')] == ['TC2*', 'TC3'], r['cov'][(chain_head, 'F')]  # elseif에서 추론
    assert (r['decs'][2], 'F') not in r['cov']
    msgs = [(n, w) for n, _, w, _ in r['warns']]
    assert any(n == 2 and '못 찾음' in w for n, w in msgs), msgs
    assert any(n == 3 and 'else if 결과가 빠짐' in w for n, w in msgs), msgs
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
    # 예시 파일: 15개 중 13개, 못 채운 것은 L23 if T 와 L27 case 3, 경고는 TC4 else if 누락 하나
    ex = bc.analyze(open('example.c', encoding='utf-8').read(),
                    open('example_tc.txt', encoding='utf-8').read(), 'example')
    assert [(ln, o) for ln, _, o in ex['missing']] == [(23, 'T'), (27, 'case 3')], ex['missing']
    assert [(n, w[:3]) for n, _, w, _ in ex['warns']] == [(4, 'L12')], ex['warns']
    # 화면에서 넣어 주는 줄은 다시 읽으면 같은 분기로 맞아야 한다
    for d in ex['decs']:
        for o in d.outcomes():
            hits = bc.analyze('', f"D1 [{'T' if d.kind == 'switch' else o}] {bc.template(d, o)}",
                              parsed=bc.parse(bc.strip_code(open('example.c', encoding='utf-8').read())))
            assert any(k[1] == o and k[0].line == d.line for k in hits['cov']), (d.line, o)

    test_gui()
    print('OK')
    print(screen)


def test_gui():
    import tkinter as tk
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
    root.destroy()


if __name__ == '__main__':
    test()
