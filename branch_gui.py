"""TC 표기 작성 도우미 (화면). 사용: python branch_gui.py [C 파일] [--func 함수이름]

왼쪽에 TC 표기를 쓰면 오른쪽에 분기마다 채운 TC와 아직 못 채운 분기가 바로 나온다.
오른쪽에서 빨간 줄(못 채운 분기)을 더블클릭하면 커서가 있는 TC에 그 줄을 넣는다.
이 창에는 조건문 원문이 보이므로 캡처해서 밖으로 보내지 않는다.
"""
import argparse
import os
import tempfile
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

import branch_cov as bc

ALL = '(전체)'


class App:
    def __init__(self, root, c_path=None, func=None):
        self.root, self.src, self.parsed, self.tc_path, self.saved = root, '', None, None, ''
        self.job, self.rows, self.result = None, {}, None
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
        self.only_miss = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text='못 채운 것만', variable=self.only_miss,
                        command=self.refresh).pack(side='left', padx=12)
        self.status = ttk.Label(top, text='C 파일을 여세요', font=('맑은 고딕', 11, 'bold'))
        self.status.pack(side='left', padx=8)

        pw = ttk.PanedWindow(root, orient='horizontal')
        pw.pack(fill='both', expand=True)
        self.text = tk.Text(pw, width=50, undo=True, font=('Consolas', 11))
        pw.add(self.text, weight=1)
        self.tree = ttk.Treeview(pw, columns=('tc',), show='tree headings')
        self.tree.heading('#0', text='분기 (빨간 줄 더블클릭 → 왼쪽에 추가)')
        self.tree.heading('tc', text='채운 TC (* = 추론)')
        self.tree.column('#0', width=380)
        self.tree.column('tc', width=180)
        self.tree.tag_configure('miss', foreground='red')
        self.tree.tag_configure('done', foreground='gray50')
        pw.add(self.tree, weight=1)
        self.warn = tk.Listbox(root, height=6, fg='darkorange3')
        self.warn.pack(fill='x')

        self.text.bind('<<Modified>>', self.on_edit)
        self.tree.bind('<Double-1>', self.insert_line)
        root.protocol('WM_DELETE_WINDOW', self.close)
        if c_path:
            self.load_c(c_path, func)

    # --- 파일 ---
    def open_c(self):
        path = filedialog.askopenfilename(filetypes=[('C', '*.c *.h'), ('모든 파일', '*.*')])
        if path:
            self.load_c(path)

    def load_c(self, path, func=None):
        try:
            self.src = bc._read(path)
            funcs = bc.parse(bc.strip_code(self.src))[2]
        except OSError:
            messagebox.showerror('C 파일', '파일을 열 수 없음 — 경로를 확인하세요')
            return
        except Exception:
            self.parse_failed()
            return
        self.func['values'] = [ALL] + funcs
        self.func.set(func if func in funcs else (funcs[0] if len(funcs) == 1 else ALL))
        self.set_func()

    def set_func(self):
        f = self.func.get()
        try:
            self.parsed = bc.parse(bc.strip_code(self.src), None if f == ALL else f)
        except Exception:
            self.parse_failed()
            return
        self.refresh()

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
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', body)
        self.tc_path, self.saved = path, self.tc_text()

    def save_tc(self):
        path = self.tc_path or filedialog.asksaveasfilename(defaultextension='.txt',
                                                            filetypes=[('텍스트', '*.txt')])
        if not path:
            return False
        with open(path, 'w', encoding='utf-8') as f:
            f.write(self.tc_text())
        self.tc_path, self.saved = path, self.tc_text()
        return True

    def close(self):
        if self.tc_text().strip() and self.tc_text() != self.saved:
            ans = messagebox.askyesnocancel('TC 저장', '저장하지 않은 TC 표기가 있습니다. 저장할까요?')
            if ans is None or (ans and not self.save_tc()):
                return
        self.root.destroy()

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
    main()
