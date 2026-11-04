"""Regression cases kept independent of generated-TC round trips."""

import unittest
from unittest.mock import Mock, patch

from decision_tool import analysis as bc
from decision_tool import boundary as bva
from decision_tool import gui
from decision_tool.expressions import Expression, Manual


def model(source):
    return bva.Model(bc.parse(bc.strip_code(source)), [source])


class Expressions(unittest.TestCase):
    def test_c_semantics(self):
        for expression, expected in [
            ("3 / 2", 1),
            ("-3 / 2", -1),
            ("-3 % 2", -1),
            ("!0 == 2", 0),
            ("3 < 2 < 1", 1),
            ("010 == 8", 1),
            ("0xFF == 255", 1),
            ("(2 && 3) == 1", 1),
            ("0 && (1/0)", 0),
            ("1 || (1/0)", 1),
            ("25.5f > 25", 1),
        ]:
            with self.subTest(expression=expression):
                self.assertEqual(Expression(expression, {})({}), expected)

    def test_unsupported_or_expensive_expressions_are_manual(self):
        for expression in [
            "1 << 10000000000",
            "(unsigned char)256",
            "1U",
            "08",
            "1e999",
            "(" * 100 + "1" + ")" * 100,
            "1+" * 300 + "1",
            "sizeof(int)",
            "__import__(1)",
        ]:
            with self.subTest(expression=expression), self.assertRaises(Manual):
                Expression(expression, {})({})

    def test_bitwise_condition_is_not_evaluated_with_python_precedence(self):
        m = model("int f(int a){if(a & 1 == 0)return 1;return 0;}")
        tcs, manual = m.generate()
        self.assertFalse(tcs)
        self.assertEqual(len(manual), 2)

    def test_signed_unsigned_mixing_is_manual(self):
        m = model("int f(unsigned n){if(n > -1)return 1;return 0;}")
        self.assertEqual(len(m.generate()[1]), 2)


class Coverage(unittest.TestCase):
    def test_loops_do_not_infer_false_from_true(self):
        for body in ["return 1;", "break;", "n--;", ";"]:
            with self.subTest(body=body):
                m = model(f"int f(int n){{while(n>0){{{body}}}return 0;}}")
                tcs, _ = m.generate()
                self.assertNotIn("[T/F]", m.tc_text(tcs))
                for values, _, got in tcs:
                    expected = "T" if values["n"] > 0 else "F"
                    self.assertEqual({outcome for _, outcome in got}, {expected})

    def test_do_while_and_loop_body_need_execution_confirmation(self):
        for source in [
            "int f(int n){do{return 1;}while(n>0);}",
            "int f(int n){while(n>0){if(n>1)return 1;}return 0;}",
        ]:
            m = model(source)
            _, manual = m.generate()
            self.assertTrue(manual)

    def test_shared_case_is_manual_instead_of_reached_from_default(self):
        source = "int f(int m,int x){switch(m){case 1:case 2:if(x>0)return 1;break;default:break;}return 0;}"
        m = model(source)
        tcs, manual = m.generate()
        self.assertEqual({d.kind for d, _ in manual}, {"if"})
        self.assertFalse(any(d.kind == "if" for _, _, got in tcs for d, _ in got))

    def test_unsigned_char_inputs_stay_representable(self):
        m = model("int f(unsigned char x){if(x>255)return 1;return 0;}")
        tcs, manual = m.generate()
        self.assertTrue(all(0 <= values["x"] <= 255 for values, _, _ in tcs))
        self.assertIn("T", {o for _, o in manual})

    def test_literal_tokens_and_spaces_survive_matching(self):
        for literal in ["'A'", "' '", "')'", '"a  b"']:
            with self.subTest(literal=literal):
                condition = f"c == {literal}"
                parsed = bc.parse(
                    bc.strip_code(f"int f(char c){{if({condition})return 1;return 0;}}")
                )
                text = bc.tc_line(1, parsed[0][0], "T")
                result = bc.analyze("", text, parsed=parsed)
                self.assertEqual(result["matched"], 1)
                self.assertIn(literal, text)

    def test_duplicate_conditions_are_not_silently_attributed(self):
        result = bc.analyze(
            "int f(int a){if(a)return 1;} int g(int a){if(a)return 2;}", "D1 [T] if(a)"
        )
        self.assertFalse(result["cov"])
        self.assertEqual(len(result["warns"]), 1)


class GuiState(unittest.TestCase):
    def app(self, text="edited", saved="saved"):
        app = gui.App.__new__(gui.App)
        app.tc_text = Mock(return_value=text)
        app.text, app.root = Mock(), Mock()
        app.saved, app.tc_path = saved, None
        app.parsed = bc.parse(bc.strip_code("int f(int x){if(x)return 1;}"))
        app.src, app.headers, app.manual = "old", ["old"], {}
        app.refresh, app.parse_failed = Mock(), Mock()
        return app

    def test_failed_load_keeps_entire_previous_snapshot(self):
        app = self.app()
        old = app.parsed
        with (
            patch.object(gui, "read_text", return_value="int broken(){"),
            patch.object(gui, "headers_near", return_value=["new"]),
        ):
            app.load_c("broken.c")
        self.assertIs(app.parsed, old)
        self.assertEqual((app.src, app.headers), ("old", ["old"]))

    def test_cancel_tc_open_preserves_editor(self):
        app = self.app()
        with (
            patch.object(gui.filedialog, "askopenfilename", return_value="new.txt"),
            patch.object(gui, "read_text", return_value="new"),
            patch.object(gui.messagebox, "askyesnocancel", return_value=None),
        ):
            app.open_tc()
        app.text.delete.assert_not_called()

    def test_deleting_everything_still_prompts_on_close(self):
        app = self.app(text="")
        with patch.object(gui.messagebox, "askyesnocancel", return_value=None) as prompt:
            app.close()
        prompt.assert_called_once()
        app.root.destroy.assert_not_called()

    def test_save_error_returns_false(self):
        app = self.app()
        app.tc_path = "existing.txt"
        with (
            patch("builtins.open", side_effect=PermissionError),
            patch.object(gui.messagebox, "showerror"),
        ):
            self.assertFalse(app.save_tc())
        self.assertEqual(app.saved, "saved")

    def test_cancel_or_failed_csv_does_not_append_tcs(self):
        for selected in ["", "cannot-write.csv"]:
            app = self.app()
            with (
                patch.object(gui.filedialog, "asksaveasfilename", return_value=selected),
                patch.object(bva.Model, "write_csv", side_effect=PermissionError),
                patch.object(gui.messagebox, "showerror"),
            ):
                app.make_bva()
            app.text.insert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
