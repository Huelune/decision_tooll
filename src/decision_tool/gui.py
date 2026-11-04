"""TC 표기 작성 도우미 (화면). 사용: python branch_gui.py [C 파일] [--func 함수이름]

왼쪽에 TC 표기를 쓰면 오른쪽에 분기마다 채운 TC와 아직 못 채운 분기가 바로 나온다.
오른쪽에서 빨간 줄(못 채운 분기)을 더블클릭하면 커서가 있는 TC에 그 줄을 넣는다.
분기를 고르면 아래에 채우는 법(꼭 거쳐야 할 분기, 조건에 쓰인 값의 출처)이 나오고,
[새 TC로 추가]는 그 경로를 새 TC 블록으로 붙인다. 다른 함수 호출은 stub으로 본다.
[경계값 TC 만들기]는 경계 바로 양쪽 값으로 되도록 적은 TC를 만들어 입력값은 CSV로 저장하고
TC 표기는 왼쪽 끝에 붙인다. 같은 폴더의 .h에서 #define·enum 값과 형을 찾는다.
이 창에는 조건문 원문이 보이므로 캡처해서 밖으로 보내지 않는다.
"""

import argparse
import os
import tempfile
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

from . import analysis as bc
from . import boundary as bva
from .console import configure_output
from .storage import REPORT, headers_near, read_text

ALL = "(전체)"


class App:
    def __init__(self, root, c_path=None, func=None):
        self.root, self.src, self.parsed, self.tc_path, self.saved = root, "", None, None, ""
        self.job, self.rows, self.result, self.headers, self.manual = None, {}, None, [], {}
        root.title("분기 커버리지 도우미")
        root.geometry("1100x700")

        top = ttk.Frame(root, padding=4)
        top.pack(fill="x")
        ttk.Button(top, text="C 파일 열기", command=self.open_c).pack(side="left")
        ttk.Label(top, text="  함수").pack(side="left")
        self.func = ttk.Combobox(top, state="readonly", width=28)
        self.func.pack(side="left")
        self.func.bind("<<ComboboxSelected>>", lambda e: self.set_func())
        ttk.Button(top, text="TC 열기", command=self.open_tc).pack(side="left", padx=(12, 0))
        ttk.Button(top, text="TC 저장", command=self.save_tc).pack(side="left")
        ttk.Button(top, text="경계값 TC 만들기", command=self.make_bva).pack(
            side="left", padx=(12, 0)
        )
        self.only_miss = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            top, text="못 채운 것만", variable=self.only_miss, command=self.refresh
        ).pack(side="left", padx=12)
        self.status = ttk.Label(top, text="C 파일을 여세요", font=("맑은 고딕", 11, "bold"))
        self.status.pack(side="left", padx=8)

        pw = ttk.PanedWindow(root, orient="horizontal")
        pw.pack(fill="both", expand=True)
        self.text = tk.Text(pw, width=50, undo=True, font=("Consolas", 11))
        pw.add(self.text, weight=1)
        right = ttk.PanedWindow(pw, orient="vertical")
        pw.add(right, weight=1)
        self.tree = ttk.Treeview(right, columns=("tc",), show="tree headings")
        self.tree.heading("#0", text="분기 (빨간 줄 더블클릭 → 왼쪽에 추가)")
        self.tree.heading("tc", text="채운 TC (* = 추론)")
        self.tree.column("#0", width=380)
        self.tree.column("tc", width=180)
        self.tree.tag_configure("miss", foreground="red")
        self.tree.tag_configure("done", foreground="gray50")
        right.add(self.tree, weight=3)
        box = ttk.Frame(right)
        right.add(box, weight=2)
        bar = ttk.Frame(box)
        bar.pack(fill="x")
        ttk.Label(bar, text="채우는 법 (분기를 고르면 나옴)").pack(side="left")
        ttk.Button(bar, text="새 TC로 추가", command=self.add_tc).pack(side="right")
        self.how = tk.Text(box, height=10, font=("Consolas", 10), state="disabled", bg="#f7f7f7")
        self.how.pack(fill="both", expand=True)
        self.warn = tk.Listbox(root, height=6, fg="darkorange3")
        self.warn.pack(fill="x")

        self.text.bind("<<Modified>>", self.on_edit)
        self.tree.bind("<Double-1>", self.insert_line)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_how())
        root.protocol("WM_DELETE_WINDOW", self.close)
        if c_path:
            self.load_c(c_path, func)

    # --- 파일 ---
    def open_c(self):
        path = filedialog.askopenfilename(filetypes=[("C", "*.c *.h"), ("모든 파일", "*.*")])
        if path:
            self.load_c(path)

    def load_c(self, path, func=None):
        try:
            src = read_text(path)
            headers = headers_near(path)
            parsed = bc.parse(bc.strip_code(src))
            funcs = parsed[2]
            selected = func if func in funcs else (funcs[0] if len(funcs) == 1 else ALL)
            if selected != ALL and len(funcs) != 1:
                parsed = bc.parse(bc.strip_code(src), selected)
        except OSError:
            messagebox.showerror("C 파일", "파일을 열 수 없음 — 경로를 확인하세요")
            return
        except Exception:
            self.parse_failed()
            return
        self.src, self.headers, self.parsed = src, headers, parsed
        self.func["values"] = [ALL] + funcs
        self.func.set(selected)
        self.active_func = selected
        self.manual = {}
        self.refresh()

    def set_func(self):
        f = self.func.get()
        try:
            parsed = bc.parse(bc.strip_code(self.src), None if f == ALL else f)
        except Exception:
            self.func.set(self.active_func)
            self.parse_failed()
            return
        self.parsed, self.active_func = parsed, f
        self.manual = {}
        self.refresh()

    def make_bva(self, csv_path=None):
        """경계값 TC를 만들어 입력값은 CSV로 저장하고, TC 표기는 왼쪽 끝에 붙인다."""
        if not self.parsed:
            return
        try:
            m = bva.Model(self.parsed, [self.src] + self.headers)
            tcs, manual = m.generate()
        except Exception:
            with open(os.path.join(tempfile.gettempdir(), REPORT), "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
            messagebox.showerror("경계값 TC", f"만들다 실패함 — 상세: %TEMP%\\{REPORT}")
            return
        path = csv_path or filedialog.asksaveasfilename(
            title="경계값 TC 입력값 저장",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile="boundary_tc.csv",
        )
        if not path:
            return
        body = self.tc_text().rstrip()
        start = len(bc.read_tcs(body)) + 1
        try:
            m.write_csv(path, tcs, start=start)
        except OSError:
            messagebox.showerror("CSV", "파일을 쓸 수 없음 — 엑셀에서 열려 있지 않은지 확인하세요")
            return
        self.manual = manual
        self.text.insert("end", ("\n\n" if body else "") + m.tc_text(tcs, start=start))
        self.text.see("end")
        self.refresh()
        if not csv_path:
            messagebox.showinfo(
                "경계값 TC",
                f"TC {len(tcs)}개를 만들었습니다.\n"
                f"자동으로 못 만든 분기 {len(self.manual)}개는 아래 목록에 이유가 있습니다.",
            )

    def parse_failed(self):
        with open(os.path.join(tempfile.gettempdir(), REPORT), "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        messagebox.showerror(
            "C 파일",
            "코드 구조 해석 실패 — 괄호 짝, #ifdef 안의 중괄호를 확인하세요\n"
            f"상세: %TEMP%\\{REPORT}",
        )

    def open_tc(self):
        path = filedialog.askopenfilename(filetypes=[("텍스트", "*.txt"), ("모든 파일", "*.*")])
        if not path:
            return
        try:
            body = read_text(path)
        except OSError:
            messagebox.showerror("TC", "파일을 열 수 없음 — 경로를 확인하세요")
            return
        if not self.confirm_tc_change():
            return
        self.text.delete("1.0", "end")
        self.text.insert("1.0", body)
        self.tc_path, self.saved = path, self.tc_text()

    def save_tc(self):
        path = self.tc_path or filedialog.asksaveasfilename(
            defaultextension=".txt", filetypes=[("텍스트", "*.txt")]
        )
        if not path:
            return False
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.tc_text())
        except OSError:
            messagebox.showerror("TC 저장", "파일을 쓸 수 없음 — 경로와 파일 권한을 확인하세요")
            return False
        self.tc_path, self.saved = path, self.tc_text()
        return True

    def confirm_tc_change(self):
        if self.tc_text() != self.saved:
            ans = messagebox.askyesnocancel(
                "TC 저장", "저장하지 않은 TC 표기가 있습니다. 저장할까요?"
            )
            if ans is None or (ans and not self.save_tc()):
                return False
        return True

    def close(self):
        if self.confirm_tc_change():
            self.root.destroy()

    def tc_text(self):
        return self.text.get("1.0", "end-1c")

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
        r = self.result = bc.analyze("", self.tc_text(), parsed=self.parsed)
        y = self.tree.yview()[0]
        self.tree.delete(*self.tree.get_children())
        self.rows = {}
        for d in r["decs"]:
            outs = d.outcomes()
            hit = sum((d, o) in r["cov"] for o in outs)
            if self.only_miss.get() and hit == len(outs):
                continue
            pid = self.tree.insert(
                "",
                "end",
                open=True,
                values=(f"{hit}/{len(outs)}",),
                text=f"L{d.line}  {d.kind} ({' '.join(d.cond.split())})",
                tags=("done",) if hit == len(outs) else (),
            )
            for o in outs:
                tcs = r["cov"].get((d, o))
                iid = self.tree.insert(
                    pid,
                    "end",
                    text=f"    {o}",
                    values=(", ".join(tcs) if tcs else "— 아직 없음",),
                    tags=() if tcs else ("miss",),
                )
                self.rows[iid] = (d, o)
        self.tree.yview_moveto(y)

        total = sum(len(d.outcomes()) for d in r["decs"])
        done = total - len(r["missing"])
        pct = 100.0 * done / total if total else 0.0
        self.status["text"] = (
            f"{done}/{total} ({pct:.1f}%)  남은 분기 {len(r['missing'])}개"
            f"  경고 {len(r['warns'])}개" + ("  ✔ 100%" if total and done == total else "")
        )
        self.warn.delete(0, "end")
        for n, dno, msg, detail in r["warns"]:
            self.warn.insert("end", f"TC{n} D{dno}: {msg}" + (f"   | {detail}" if detail else ""))
        for (d, o), why in self.manual.items():
            self.warn.insert("end", f"경계값 수동  L{d.line_of(o)} {d.kind} {o}: {why}")

    # --- 채우는 법 ---
    def picked(self):
        sel = self.tree.selection()
        return self.rows.get(sel[0] if sel else self.tree.focus(), (None, None))

    def show_how(self):
        d, o = self.picked()
        self.how.configure(state="normal")
        self.how.delete("1.0", "end")
        if d is not None:
            self.how.insert("1.0", "\n".join(bc.howto(d, o, self.result["info"])))
        self.how.configure(state="disabled")

    def add_tc(self):
        """고른 분기까지 꼭 거쳐야 하는 줄들을 새 TC 블록으로 맨 끝에 붙인다."""
        d, o = self.picked()
        if d is None:
            return
        name = f"새 TC  L{d.line_of(o)} {bc._kind(d, o)} {'' if d.kind == 'switch' else o}".rstrip()
        lines = [name] + [bc.tc_line(i, pd, po) for i, (pd, po) in enumerate(bc.need(d, o), 1)]
        body = self.tc_text().rstrip()
        self.text.delete("1.0", "end")
        self.text.insert("1.0", (body + "\n\n" if body else "") + "\n".join(lines) + "\n")
        self.text.mark_set("insert", "end-1c")
        self.text.see("end")

    # --- 못 채운 분기를 왼쪽에 넣기 ---
    def insert_line(self, _):
        d, o = self.rows.get(self.tree.focus(), (None, None))
        if d is None:
            return
        ln = int(self.text.index("insert").split(".")[0])
        line = f"D{self.next_d(ln)} [{'T' if d.kind == 'switch' else o}] {bc.template(d, o)}"
        if self.text.get(f"{ln}.0", f"{ln}.end").strip():
            self.text.insert(f"{ln}.end", "\n" + line)
            ln += 1
        else:
            self.text.insert(f"{ln}.0", line)
        self.text.mark_set("insert", f"{ln}.end")
        self.text.see("insert")
        self.text.focus_set()

    def next_d(self, ln):
        """커서가 있는 TC 블록에서 커서 줄까지 적힌 D 줄 수 + 1."""
        lines = self.tc_text().split("\n")[:ln]
        if lines and not lines[-1].strip():  # 빈 줄에 넣으면 바로 위 블록에 붙는다
            lines.pop()
        n = 0
        for x in reversed(lines):
            if not x.strip():
                break
            n += bool(bc.TC_LINE.match(x))
        return n + 1


def main():
    configure_output()
    ap = argparse.ArgumentParser(description="TC 표기 작성 도우미")
    ap.add_argument("c_file", nargs="?")
    ap.add_argument("--func")
    a = ap.parse_args()
    root = tk.Tk()
    App(root, a.c_file, a.func)
    root.mainloop()


if __name__ == "__main__":
    main()
