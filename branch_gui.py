"""TC 표기 작성 도우미 (화면). 사용: python branch_gui.py [C 파일] [--func 함수이름]

왼쪽에 TC 표기를 쓰면 오른쪽에 분기마다 채운 TC와 아직 못 채운 분기가 바로 나온다.
오른쪽에서 빨간 줄(못 채운 분기)을 더블클릭하면 커서가 있는 TC에 그 줄을 넣는다.
분기를 고르면 아래에 채우는 법(꼭 거쳐야 할 분기, 조건에 쓰인 값의 출처)이 나오고,
[새 TC로 추가]는 그 경로를 새 TC 블록으로 붙인다. 다른 함수 호출은 stub으로 본다.
[경계값 TC 만들기]는 경계 바로 양쪽 값으로 되도록 적은 TC를 만들어 입력값은 CSV로 저장하고
TC 표기는 왼쪽 끝에 붙인다. 같은 폴더의 .h에서 #define·enum 값과 형을 찾는다.
왼쪽 [stub 코드] 탭에 stub을 직접 쓰면 그 반환식을 쓴다(정의 안 한 stub은 0 반환이 기본).
[뼈대 추가]는 분기에 값이 들어가는 stub을 `return 0;` 모양으로 붙여 준다.
이 창에는 조건문 원문이 보이므로 캡처해서 밖으로 보내지 않는다.
"""
import argparse
import os
import tempfile
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

import branch_cov as bc
import bva

ALL = '(전체)'


class App:
    def __init__(self, root, c_path=None, func=None):
        self.root, self.src, self.parsed, self.tc_path, self.saved = root, '', None, None, ''
        self.active_func = ALL
        self.job, self.rows, self.result, self.headers, self.manual = None, {}, None, [], {}
        root.title('분기 커버리지 도우미')
        root.geometry('1100x700')

        top = ttk.Frame(root, padding=4)
        top.pack(fill='x')
        ttk.Button(top, text='C 파일 열기', command=self.open_c).pack(side='left')
        ttk.Label(top, text='  함수').pack(side='left')
        self.func = ttk.Combobox(top, state='readonly', width=28)
        self.func.pack(side='left')
        self.func.bind('<<ComboboxSelected>>', lambda e: self.set_func())
        ttk.Button(top, text='TC 열기', command=self.open_tc).pack(side='left', padx=(12, 0))
        ttk.Button(top, text='TC 저장', command=self.save_tc).pack(side='left')
        ttk.Button(top, text='경계값 TC 만들기', command=self.make_bva).pack(side='left', padx=(12, 0))
        self.only_miss = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text='못 채운 것만', variable=self.only_miss,
                        command=self.refresh).pack(side='left', padx=12)
        self.status = ttk.Label(top, text='C 파일을 여세요', font=('맑은 고딕', 11, 'bold'))
        self.status.pack(side='left', padx=8)

        pw = ttk.PanedWindow(root, orient='horizontal')
        pw.pack(fill='both', expand=True)
        tabs = ttk.Notebook(pw)
        pw.add(tabs, weight=1)
        self.text = tk.Text(tabs, width=50, undo=True, font=('Consolas', 11))
        tabs.add(self.text, text='TC 표기')
        sbox = ttk.Frame(tabs)
        tabs.add(sbox, text='stub 코드')
        sbar = ttk.Frame(sbox)
        sbar.pack(fill='x')
        ttk.Button(sbar, text='열기', command=self.open_stub).pack(side='left')
        ttk.Button(sbar, text='저장', command=self.save_stub).pack(side='left')
        ttk.Button(sbar, text='뼈대 추가', command=self.add_skeleton).pack(side='left', padx=(8, 0))
        self.stub_zero = tk.BooleanVar(value=True)
        ttk.Checkbutton(sbar, text='정의 안 한 stub은 0 반환', variable=self.stub_zero).pack(side='left', padx=8)
        self.stub = tk.Text(sbox, undo=True, font=('Consolas', 11))
        self.stub.pack(fill='both', expand=True)
        self.stub_path, self.stub_saved = None, ''
        right = ttk.PanedWindow(pw, orient='vertical')
        pw.add(right, weight=1)
        self.tree = ttk.Treeview(right, columns=('tc',), show='tree headings')
        self.tree.heading('#0', text='분기 (빨간 줄 더블클릭 → 왼쪽에 추가)')
        self.tree.heading('tc', text='채운 TC (* = 추론)')
        self.tree.column('#0', width=380)
        self.tree.column('tc', width=180)
        self.tree.tag_configure('miss', foreground='red')
        self.tree.tag_configure('done', foreground='gray50')
        right.add(self.tree, weight=3)
        box = ttk.Frame(right)
        right.add(box, weight=2)
        bar = ttk.Frame(box)
        bar.pack(fill='x')
        ttk.Label(bar, text='채우는 법 (분기를 고르면 나옴)').pack(side='left')
        ttk.Button(bar, text='새 TC로 추가', command=self.add_tc).pack(side='right')
        self.how = tk.Text(box, height=10, font=('Consolas', 10), state='disabled', bg='#f7f7f7')
        self.how.pack(fill='both', expand=True)
        self.warn = tk.Listbox(root, height=6, fg='darkorange3')
        self.warn.pack(fill='x')

        self.text.bind('<<Modified>>', self.on_edit)
        self.tree.bind('<Double-1>', self.insert_line)
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.show_how())
        root.protocol('WM_DELETE_WINDOW', self.close)
        if c_path:
            self.load_c(c_path, func)

    # --- 파일 ---
    def open_c(self):
        path = filedialog.askopenfilename(filetypes=[('C', '*.c *.h'), ('모든 파일', '*.*')])
        if path:
            self.load_c(path)

    def load_c(self, path, func=None):
        # 다 읽고 해석에 성공한 뒤에만 바꾼다 (실패하면 보던 파일 그대로)
        try:
            src, headers = bc._read(path), bc.headers_near(path)
            funcs = bc.parse(bc.strip_code(src))[2]
            f = func if func in funcs else (funcs[0] if len(funcs) == 1 else ALL)
            parsed = bc.parse(bc.strip_code(src), None if f == ALL else f)
        except OSError:
            messagebox.showerror('C 파일', '파일을 열 수 없음 — 경로를 확인하세요')
            return
        except Exception:
            self.parse_failed()
            return
        self.src, self.headers, self.parsed, self.active_func, self.manual = src, headers, parsed, f, {}
        self.func['values'] = [ALL] + funcs
        self.func.set(f)
        self.refresh()

    def set_func(self):
        f = self.func.get()
        try:
            parsed = bc.parse(bc.strip_code(self.src), None if f == ALL else f)
        except Exception:
            self.func.set(self.active_func)
            self.parse_failed()
            return
        self.parsed, self.active_func, self.manual = parsed, f, {}
        self.refresh()

    def make_bva(self, csv_path=None):
        """경계값 TC를 만들어 입력값은 CSV로 저장하고, TC 표기는 왼쪽 끝에 붙인다."""
        if not self.parsed:
            return
        try:
            m = bva.Model(self.parsed, [self.src] + self.headers, self.stub_text(), self.stub_zero.get())
            tcs, manual = m.generate()
        except Exception:
            with open(os.path.join(tempfile.gettempdir(), bc.REPORT), 'w', encoding='utf-8') as f:
                f.write(traceback.format_exc())
            messagebox.showerror('경계값 TC', f'만들다 실패함 — 상세: %TEMP%\\{bc.REPORT}')
            return
        path = csv_path or filedialog.asksaveasfilename(title='경계값 TC 입력값 저장', defaultextension='.csv',
                                                        filetypes=[('CSV', '*.csv')], initialfile='boundary_tc.csv')
        if not path:   # 취소하면 TC 표기도 붙이지 않는다 (CSV와 표기가 어긋나지 않게)
            return
        body = self.tc_text().rstrip()
        start = len(bc.read_tcs(body)) + 1   # CSV의 TC 번호를 왼쪽 TC 순번과 맞춘다
        try:
            m.write_csv(path, tcs, start)
        except OSError:
            messagebox.showerror('CSV', '파일을 쓸 수 없음 — 엑셀에서 열려 있지 않은지 확인하세요')
            return
        self.manual = manual
        self.text.insert('end', ('\n\n' if body else '') + m.tc_text(tcs, start))
        self.text.see('end')
        self.refresh()
        if not csv_path:
            messagebox.showinfo('경계값 TC', f'TC {len(tcs)}개를 만들었습니다.\n'
                                         f'자동으로 못 만든 분기 {len(self.manual)}개는 아래 목록에 이유가 있습니다.')

    def parse_failed(self):
        with open(os.path.join(tempfile.gettempdir(), bc.REPORT), 'w', encoding='utf-8') as f:
            f.write(traceback.format_exc())
        messagebox.showerror('C 파일', '코드 구조 해석 실패 — 괄호 짝, #ifdef 안의 중괄호를 확인하세요\n'
                                       f'상세: %TEMP%\\{bc.REPORT}')

    def open_tc(self):
        path = filedialog.askopenfilename(filetypes=[('텍스트', '*.txt'), ('모든 파일', '*.*')])
        if not path:
            return
        try:
            body = bc._read(path)
        except OSError:
            messagebox.showerror('TC', '파일을 열 수 없음 — 경로를 확인하세요')
            return
        if not self.confirm('TC 저장', 'TC 표기', self.tc_text(), self.saved, self.save_tc):
            return
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', body)
        self.tc_path, self.saved = path, self.tc_text()

    def save_tc(self):
        path = self.tc_path or filedialog.asksaveasfilename(defaultextension='.txt',
                                                            filetypes=[('텍스트', '*.txt')])
        if not path:
            return False
        if not self.write(path, self.tc_text(), 'TC 저장'):
            return False
        self.tc_path, self.saved = path, self.tc_text()
        return True

    @staticmethod
    def write(path, text, title):
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(text)
            return True
        except OSError:
            messagebox.showerror(title, '파일을 쓸 수 없음 — 경로와 파일 권한을 확인하세요')
            return False

    @staticmethod
    def confirm(title, what, now, saved, save):
        """저장 안 한 내용이 있으면 물어본다 (다 지운 것도 바뀐 것). 계속해도 되면 True."""
        if now == saved:
            return True
        ans = messagebox.askyesnocancel(title, f'저장하지 않은 {what}이(가) 있습니다. 저장할까요?')
        return ans is not None and (not ans or save())

    def close(self):
        if self.confirm('TC 저장', 'TC 표기', self.tc_text(), self.saved, self.save_tc) and \
                self.confirm('stub 저장', 'stub 코드', self.stub_text(), self.stub_saved, self.save_stub):
            self.root.destroy()

    # --- stub 코드 ---
    def stub_text(self):
        return self.stub.get('1.0', 'end-1c')

    def stubs(self):
        return bva.Stubs(self.stub_text(), bva.constants([self.src] + self.headers + [self.stub_text()]),
                         self.stub_zero.get())

    def open_stub(self):
        path = filedialog.askopenfilename(filetypes=[('C', '*.c *.h'), ('모든 파일', '*.*')])
        if not path:
            return
        try:
            body = bc._read(path)
        except OSError:
            messagebox.showerror('stub', '파일을 열 수 없음 — 경로를 확인하세요')
            return
        if not self.confirm('stub 저장', 'stub 코드', self.stub_text(), self.stub_saved, self.save_stub):
            return
        self.stub.delete('1.0', 'end')
        self.stub.insert('1.0', body)
        self.stub_path, self.stub_saved = path, self.stub_text()

    def save_stub(self):
        path = self.stub_path or filedialog.asksaveasfilename(defaultextension='.c', filetypes=[('C', '*.c')])
        if not path:
            return False
        if not self.write(path, self.stub_text(), 'stub 저장'):
            return False
        self.stub_path, self.stub_saved = path, self.stub_text()
        return True

    def add_skeleton(self):
        """분기에 값이 들어가는 stub 중 아직 없는 것을 `return 0;` 뼈대로 붙인다."""
        if not self.parsed:
            return
        lines = bva.skeleton(bva.needed_stubs(self.parsed), [self.src] + self.headers, self.stub_text())
        if not lines:
            messagebox.showinfo('stub', '더 추가할 stub이 없습니다.')
            return
        body = self.stub_text().rstrip()
        self.stub.insert('end', ('\n' if body else '') + '\n'.join(lines) + '\n')
        self.stub.see('end')

    def tc_text(self):
        return self.text.get('1.0', 'end-1c')

    # --- 다시 계산 ---
    def on_edit(self, _):
        if not self.text.edit_modified():
            return
        self.text.edit_modified(False)
        if self.job:
            self.root.after_cancel(self.job)
        self.job = self.root.after(300, self.refresh)

    def refresh(self):
        self.job = None
        if not self.parsed:
            return
        r = self.result = bc.analyze('', self.tc_text(), parsed=self.parsed)
        y = self.tree.yview()[0]
        self.tree.delete(*self.tree.get_children())
        self.rows = {}
        for d in r['decs']:
            outs = d.outcomes()
            hit = sum((d, o) in r['cov'] for o in outs)
            if self.only_miss.get() and hit == len(outs):
                continue
            pid = self.tree.insert('', 'end', open=True, values=(f'{hit}/{len(outs)}',),
                                   text=f"L{d.line}  {d.kind} ({' '.join(d.cond.split())})",
                                   tags=('done',) if hit == len(outs) else ())
            for o in outs:
                tcs = r['cov'].get((d, o))
                iid = self.tree.insert(pid, 'end', text=f'    {o}',
                                       values=(', '.join(tcs) if tcs else '— 아직 없음',),
                                       tags=() if tcs else ('miss',))
                self.rows[iid] = (d, o)
        self.tree.yview_moveto(y)

        total = sum(len(d.outcomes()) for d in r['decs'])
        done = total - len(r['missing'])
        pct = 100.0 * done / total if total else 0.0
        self.status['text'] = (f'{done}/{total} ({pct:.1f}%)  남은 분기 {len(r["missing"])}개'
                               f'  경고 {len(r["warns"])}개' + ('  ✔ 100%' if total and done == total else ''))
        self.warn.delete(0, 'end')
        for n, dno, msg, detail in r['warns']:
            self.warn.insert('end', f'TC{n} D{dno}: {msg}' + (f'   | {detail}' if detail else ''))
        for (d, o), why in self.manual.items():
            self.warn.insert('end', f'경계값 수동  L{d.line_of(o)} {d.kind} {o}: {why}')

    # --- 채우는 법 ---
    def picked(self):
        sel = self.tree.selection()
        return self.rows.get(sel[0] if sel else self.tree.focus(), (None, None))

    def show_how(self):
        d, o = self.picked()
        self.how.configure(state='normal')
        self.how.delete('1.0', 'end')
        if d is not None:
            self.how.insert('1.0', '\n'.join(bc.howto(d, o, self.result['info'], self.stubs())))
        self.how.configure(state='disabled')

    def add_tc(self):
        """고른 분기까지 꼭 거쳐야 하는 줄들을 새 TC 블록으로 맨 끝에 붙인다."""
        d, o = self.picked()
        if d is None:
            return
        name = f"새 TC  L{d.line_of(o)} {bc._kind(d, o)} {'' if d.kind == 'switch' else o}".rstrip()
        lines = [name] + [bc.tc_line(i, pd, po) for i, (pd, po) in enumerate(bc.need(d, o), 1)]
        body = self.tc_text().rstrip()
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', (body + '\n\n' if body else '') + '\n'.join(lines) + '\n')
        self.text.mark_set('insert', 'end-1c')
        self.text.see('end')

    # --- 못 채운 분기를 왼쪽에 넣기 ---
    def insert_line(self, _):
        d, o = self.rows.get(self.tree.focus(), (None, None))
        if d is None:
            return
        ln = int(self.text.index('insert').split('.')[0])
        line = f"D{self.next_d(ln)} [{'T' if d.kind == 'switch' else o}] {bc.template(d, o)}"
        if self.text.get(f'{ln}.0', f'{ln}.end').strip():
            self.text.insert(f'{ln}.end', '\n' + line)
            ln += 1
        else:
            self.text.insert(f'{ln}.0', line)
        self.text.mark_set('insert', f'{ln}.end')
        self.text.see('insert')
        self.text.focus_set()

    def next_d(self, ln):
        """커서가 있는 TC 블록에서 커서 줄까지 적힌 D 줄 수 + 1."""
        lines = self.tc_text().split('\n')[:ln]
        if lines and not lines[-1].strip():  # 빈 줄에 넣으면 바로 위 블록에 붙는다
            lines.pop()
        n = 0
        for x in reversed(lines):
            if not x.strip():
                break
            n += bool(bc.TC_LINE.match(x))
        return n + 1


def main():
    ap = argparse.ArgumentParser(description='TC 표기 작성 도우미')
    ap.add_argument('c_file', nargs='?')
    ap.add_argument('--func')
    a = ap.parse_args()
    root = tk.Tk()
    App(root, a.c_file, a.func)
    root.mainloop()


if __name__ == '__main__':
    bc.utf8_output()
    main()
