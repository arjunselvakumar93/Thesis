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
