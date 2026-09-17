import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_DIR / "src"

sys.path.insert(0, str(SRC_DIR))


from notation_matcher import (
    match_notation,
)
from notation_classifier import classify_notation
from smglom_notation_extractor import parse_symdef


class TestNotationMatcher(unittest.TestCase):

    def test_sequence_operand_case_and_whitespace(self):
        rule = classify_notation(parse_symdef(
            r"\symdef{natplus}[args=a]{\argsep{#1}{\mathbin{\maincomp+}}}"
        ))
        for text, expected in (
            ("a+b", ["a", "b"]),
            ("a + b", ["a", "b"]),
            ("a+ b", ["a", "b"]),
            ("a +b", ["a", "b"]),
            ("a+B", ["a", "B"]),
            ("A+b", ["A", "b"]),
            ("A+B", ["A", "B"]),
            ("a+b+c", ["a", "b", "c"]),
            ("A+b+C", ["A", "b", "C"]),
        ):
            with self.subTest(text=text):
                matches = match_notation(text, rule)
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0]["arguments"], expected)

    def test_relations_operand_case_and_whitespace(self):
        for operator in ("<", ">"):
            rule = classify_notation(parse_symdef(
                rf"\symdef{{relation}}[args=ai]{{#1\mathrel{{\maincomp{{{operator}}}}}#2}}"
            ))
            for text in (f"a{operator}b", f"A{operator}b", f"a{operator}B",
                         f"A {operator} B", f"A{operator} B", f"A {operator}B"):
                with self.subTest(text=text):
                    matches = match_notation(text, rule)
                    self.assertEqual(len(matches), 1)
                    self.assertEqual(matches[0]["arguments"], ["A" if text[0] == "A" else "a",
                                                               "B" if text.rstrip()[-1] == "B" else "b"])

    def test_real_subtraction_is_classified_as_sequence(self):
        rule = classify_notation(parse_symdef(
            r"\symdef{natminus}[args=a]{\argsep{#1}{\mathbin{\maincomp-}}}"
        ))
        self.assertEqual(rule["pattern_type"], "sequence")
        for text in ("a-b", "A-B", "A - b"):
            with self.subTest(text=text):
                matches = match_notation(text, rule)
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0]["arguments"], [text[0], text.rstrip()[-1]])

    def test_complete_tex_operands_fix_real_subtraction_failures(self):
        rule = classify_notation(parse_symdef(
            r"\symdef{natminus}[args=a]{\argsep{#1}{\mathbin{\maincomp-}}}"
        ))
        fraction = r"\frac{B^2}{4}"
        for text, expected in (
            (fraction + " - C", [fraction, "C"]),
            ("u^2 - v^2", ["u^2", "v^2"]),
            (fraction + " - " + fraction, [fraction, fraction]),
        ):
            with self.subTest(text=text):
                matches = match_notation(text, rule)
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0]["matched_expression"], text)
                self.assertEqual(matches[0]["arguments"], expected)

    def test_complete_tex_operand_forms(self):
        rule = classify_notation(parse_symdef(
            r"\symdef{natminus}[args=a]{\argsep{#1}{\mathbin{\maincomp-}}}"
        ))
        cases = (
            ("x_i^2 - x^2_i", ["x_i^2", "x^2_i"]),
            (r"a^{n+1} - \sqrt{x}", [r"a^{n+1}", r"\sqrt{x}"]),
            ("{a+b} - (c+d)", ["a+b", "(c+d)"]),
            (r"\left(a+b\right) - \alpha", [r"\left(a+b\right)", r"\alpha"]),
            (r"\frac{1}{\sqrt{3}-\sqrt{2}} - C",
             [r"\frac{1}{\sqrt{3}-\sqrt{2}}", "C"]),
            ("12 - A", ["12", "A"]),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                matches = match_notation(text, rule)
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0]["arguments"], expected)

    def test_binary_rule_uses_the_same_complete_operand_reader(self):
        rule = classify_notation(parse_symdef(
            r"\symdef{relation}[args=ai]{#1\mathrel{\maincomp{>}}#2}"
        ))
        text = r"\frac{B^2}{4} > v^2"
        matches = match_notation(text, rule)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["matched_expression"], text)
        self.assertEqual(matches[0]["arguments"], [r"\frac{B^2}{4}", "v^2"])

    def test_multiline_normalization_is_generic_and_preserves_raw_offsets(self):
        rule = classify_notation(parse_symdef(
            r"\symdef{relation}[args=ai]{#1\mathrel{\maincomp{>}}#2}"
        ))
        text = "A + B\n  > C -\nD"
        matches = match_notation(text, rule)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["matched_expression"], "A + B > C - D")
        self.assertEqual(matches[0]["arguments"], ["A + B", "C - D"])
        self.assertEqual((matches[0]["start"], matches[0]["end"]), (0, len(text)))

    def test_structural_commands_and_unary_minus_are_not_operands(self):
        rule = classify_notation(parse_symdef(
            r"\symdef{natminus}[args=a]{\argsep{#1}{\mathbin{\maincomp-}}}"
        ))
        for text in (
            r"-\frac{B}{2}",
            "\\label{eq:formula-monic}\n-\\frac{B}{2}",
            r"\label{eq:formula-monic} - \frac{B}{2}",
            r"\begin{equation} - A",
            r"\usepackage{math} - A",
        ):
            with self.subTest(text=text):
                self.assertEqual(match_notation(text, rule), [])

    def test_sequence_matching_inside_parenthesized_products(self):
        rule = classify_notation(parse_symdef(
            r"\symdef{natminus}[args=a]{\argsep{#1}{\mathbin{\maincomp-}}}"
        ))
        self.assertEqual(
            [match["matched_expression"] for match in match_notation("(a-b)", rule)],
            ["a-b"],
        )
        matches = match_notation("(a-b)(c-d)", rule)
        self.assertEqual(
            [(match["matched_expression"], match["arguments"]) for match in matches],
            [("a-b", ["a", "b"]), ("c-d", ["c", "d"])],
        )
        self.assertEqual(
            [match["matched_expression"]
             for match in match_notation(r"\left(a-b\right)", rule)],
            ["a-b"],
        )

    def test_signed_operands_participate_in_sequences(self):
        rule = classify_notation(parse_symdef(
            r"\symdef{natminus}[args=a]{\argsep{#1}{\mathbin{\maincomp-}}}"
        ))
        self.assertEqual(match_notation("-a", rule), [])
        for text, expected in (
            ("-a - b", ["-a", "b"]),
            (r"-\frac{B}{2} - z", [r"-\frac{B}{2}", "z"]),
        ):
            with self.subTest(text=text):
                matches = match_notation(text, rule)
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0]["matched_expression"], text)
                self.assertEqual(matches[0]["arguments"], expected)

    def test_other_rules_match_inside_parentheses(self):
        plus = classify_notation(parse_symdef(
            r"\symdef{natplus}[args=a]{\argsep{#1}{\mathbin{\maincomp+}}}"
        ))
        greater = classify_notation(parse_symdef(
            r"\symdef{natmorethan}[args=ai]{#1\mathrel{\maincomp{>}}#2}"
        ))
        equal = classify_notation(parse_symdef(
            r"\symdef{eq}[args=a]{\argsep{#1}{\mathrel{\maincomp=}}}"
        ))
        self.assertEqual(match_notation("(a + b)", plus)[0]["arguments"], ["a", "b"])
        self.assertEqual(match_notation("(A>B)", greater)[0]["arguments"], ["A", "B"])
        self.assertEqual(match_notation("(A=B)", equal)[0]["arguments"], ["A", "B"])

    def test_generic_control_word_relation(self):
        rule = classify_notation(parse_symdef(
            r"\symdef{relation}[args=ai]{#1\mathrel{\maincomp{\leq}}#2}"
        ))
        self.assertEqual(match_notation(r"A\leq B", rule)[0]["arguments"], ["A", "B"])
        self.assertEqual(match_notation(r"A\leqq B", rule), [])

    def test_generic_relation_structure_for_other_operator_tokens(self):
        # The local arithmetic source has no standalone equality or \neq
        # declaration. These synthetic bodies check the reusable structure,
        # without assigning either token a semantic SMGloM meaning.
        for operator, text, expected in (
            ("=", "A=B", ["A", "B"]),
            (r"\neq", r"\alpha\neq B", [r"\alpha", "B"]),
        ):
            with self.subTest(operator=operator):
                rule = classify_notation(parse_symdef(
                    rf"\symdef{{relation}}[args=ai]{{#1\mathrel{{\maincomp{{{operator}}}}}#2}}"
                ))
                self.assertEqual(match_notation(text, rule)[0]["arguments"], expected)

    def test_real_equality_division_and_multiplication_structures(self):
        cases = (
            (r"\symdef{eq}[args=a]{\argsep{#1}{\mathrel{\maincomp=}}}",
             "A=b=C", ["A", "b", "C"]),
            (r"\symdef{notequal}[args=ai]{#1\mathrel{\maincomp\ne}#2}",
             r"A\ne b", ["A", "b"]),
            (r"\symdef{adivide}[args=2]{#1\maincomp{/}#2}",
             "A/b", ["A", "b"]),
            (r"\symdef{nattimes}[args=a]{\argsep{#1}{\mathbin{\maincomp\times}}}",
             r"A\times b\times C", ["A", "b", "C"]),
        )
        for declaration, expression, expected in cases:
            with self.subTest(expression=expression):
                rule = classify_notation(parse_symdef(declaration))
                matches = match_notation(expression, rule)
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0]["arguments"], expected)

    def test_incomplete_extended_arithmetic_does_not_match(self):
        declarations = (
            (r"\symdef{eq}[args=a]{\argsep{#1}{\mathrel{\maincomp=}}}", ("a=", "=b")),
            (r"\symdef{notequal}[args=ai]{#1\mathrel{\maincomp\ne}#2}",
             (r"a\ne", r"\ne b")),
            (r"\symdef{adivide}[args=2]{#1\maincomp{/}#2}", ("a/", "/b")),
            (r"\symdef{nattimes}[args=a]{\argsep{#1}{\mathbin{\maincomp\times}}}",
             (r"a\times", r"\times b")),
        )
        for declaration, expressions in declarations:
            rule = classify_notation(parse_symdef(declaration))
            for expression in expressions:
                with self.subTest(expression=expression):
                    self.assertEqual(match_notation(expression, rule), [])

    def test_incomplete_expressions_do_not_match(self):
        plus = classify_notation(parse_symdef(
            r"\symdef{natplus}[args=a]{\argsep{#1}{\mathbin{\maincomp+}}}"
        ))
        minus = classify_notation(parse_symdef(
            r"\symdef{natminus}[args=a]{\argsep{#1}{\mathbin{\maincomp-}}}"
        ))
        greater = classify_notation(parse_symdef(
            r"\symdef{natmorethan}[args=ai]{#1\mathrel{\maincomp{>}}#2}"
        ))
        fraction = classify_notation({"notation": r"\frac{#1}{#2}", "placeholders": [1, 2]})
        for text, rule in (("a +", plus), ("+ b", plus), ("a ++ b", plus),
                           ("A+b+", plus), ("- b", minus), ("A-B-", minus),
                           ("a >", greater), ("> b", greater),
                           (r"\frac{a}", fraction), (r"\frac", fraction)):
            with self.subTest(text=text):
                self.assertEqual(match_notation(text, rule), [])

    def test_command_unbraced_arguments(self):

        text = r"\frac a b"

        classification = {
            "pattern_type": "command",
            "command": r"\frac",
            "argument_count": 2
        }

        matches = match_notation(
            text,
            classification
        )

        self.assertEqual(
            len(matches),
            1
        )

        self.assertEqual(
            matches[0]["arguments"],
            ["a", "b"]
        )


    def test_command_mixed_arguments(self):

        text = r"\frac {a+b} c"

        classification = {
            "pattern_type": "command",
            "command": r"\frac",
            "argument_count": 2
        }

        matches = match_notation(
            text,
            classification
        )

        self.assertEqual(
            matches[0]["arguments"],
            ["a+b", "c"]
        )

    def test_command_grouped_arguments(self):
        matches = match_notation(
            r"\frac{a+b}{c+d}",
            {"pattern_type": "command", "command": r"\frac", "argument_count": 2},
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["arguments"], ["a+b", "c+d"])


    def test_binary_infix(self):

        text = "a > b"

        classification = {
            "pattern_type": "binary_infix",
            "operator": ">"
        }

        matches = match_notation(
            text,
            classification
        )

        self.assertEqual(
            len(matches),
            1
        )

        self.assertEqual(
            matches[0]["arguments"],
            ["a", "b"]
        )


    def test_binary_infix_grouped_arguments(self):

        text = "{a+b} > {c+d}"

        classification = {
            "pattern_type": "binary_infix",
            "operator": ">"
        }

        matches = match_notation(
            text,
            classification
        )

        self.assertEqual(
            matches[0]["arguments"],
            [
                "a+b",
                "c+d"
            ]
        )


    def test_sequence_three_arguments(self):

        text = "a + b + c"

        classification = {
            "pattern_type": "sequence",
            "separator": "+",
            "variable_arguments": True
        }

        matches = match_notation(
            text,
            classification
        )

        self.assertEqual(
            len(matches),
            1
        )

        self.assertEqual(
            matches[0]["arguments"],
            [
                "a",
                "b",
                "c"
            ]
        )


    def test_sequence_grouped_argument(self):

        text = "{a+b} + c + d"

        classification = {
            "pattern_type": "sequence",
            "separator": "+",
            "variable_arguments": True
        }

        matches = match_notation(
            text,
            classification
        )

        self.assertEqual(
            matches[0]["arguments"],
            [
                "a+b",
                "c",
                "d"
            ]
        )


    def test_template_not_matched_yet(self):

        text = "a + bi"

        classification = {
            "pattern_type": "template"
        }

        matches = match_notation(
            text,
            classification
        )

        self.assertEqual(
            matches,
            []
        )


if __name__ == "__main__":
    unittest.main()
