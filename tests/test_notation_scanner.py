import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from notation_scanner import find_notation_occurrences
from notation_classifier import classify_notation
from smglom_notation_extractor import parse_notation, parse_symdef


class TestNotationScanner(unittest.TestCase):
    def test_classified_command_rule_preserves_expression_and_location(self):
        rule = classify_notation(parse_notation(r"\notation{adivide}{\frac{#1}{#2}}"))
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "section" / "paper.tex"
            paper.parent.mkdir()
            paper.write_text(r"x = \frac a b" + "\n", encoding="utf-8")
            matches = find_notation_occurrences(rule, directory)
        self.assertEqual(matches, [{
            "file": paper, "line": 1, "column": 5,
            "text": r"x = \frac a b",
            "matched_expression": r"\frac a b",
            "arguments": ["a", "b"], "pattern_type": "command",
        }])

    def test_classified_binary_rules_preserve_operand_case(self):
        greater = classify_notation(parse_symdef(
            r"\symdef{natmorethan}[args=ai]{#1\mathrel{\maincomp{>}}#2}"
        ))
        less = classify_notation(parse_symdef(
            r"\symdef{natlessthan}[args=ai]{#1\mathrel{\maincomp{<}}#2}"
        ))
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "paper.tex"
            paper.write_text("A>B\nx<Y\n", encoding="utf-8")
            greater_matches = find_notation_occurrences(greater, directory)
            less_matches = find_notation_occurrences(less, directory)
        self.assertEqual(greater_matches[0]["matched_expression"], "A>B")
        self.assertEqual(greater_matches[0]["arguments"], ["A", "B"])
        self.assertEqual(greater_matches[0]["column"], 1)
        self.assertEqual(less_matches[0]["matched_expression"], "x<Y")
        self.assertEqual(less_matches[0]["arguments"], ["x", "Y"])
        self.assertEqual(less_matches[0]["line"], 2)

    def test_classified_sequence_multiple_matches_comments_and_incomplete(self):
        rule = classify_notation(parse_symdef(
            r"\symdef{natplus}[args=a]{\argsep{#1}{\mathbin{\maincomp+}}}"
        ))
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "paper.tex"
            paper.write_text(
                "A+b+C\na+b and X+Y\na+b % X+Y\na+\n% Z+W\n",
                encoding="utf-8",
            )
            matches = find_notation_occurrences(rule, directory)
        self.assertEqual(
            [(match["line"], match["column"], match["matched_expression"], match["arguments"])
             for match in matches],
            [(1, 1, "A+b+C", ["A", "b", "C"]),
             (2, 1, "a+b", ["a", "b"]),
             (2, 9, "X+Y", ["X", "Y"]),
             (3, 1, "a+b", ["a", "b"])],
        )
        self.assertTrue(all(match["pattern_type"] == "sequence" for match in matches))

    def test_escaped_percent_does_not_start_a_comment(self):
        rule = classify_notation(parse_symdef(
            r"\symdef{natplus}[args=a]{\argsep{#1}{\mathbin{\maincomp+}}}"
        ))
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "paper.tex"
            paper.write_text(r"a+b \% X+Y % Z+W" + "\n", encoding="utf-8")
            matches = find_notation_occurrences(rule, directory)
        self.assertEqual([match["matched_expression"] for match in matches], ["a+b", "X+Y"])
        self.assertEqual([match["column"] for match in matches], [1, 8])

    def test_extracted_command_rule_scans_nested_papers_and_ignores_comments(self):
        record = parse_notation(r"\notation{adivide}{\frac{#1}{#2}}")
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "nested" / "paper.tex"
            paper.parent.mkdir()
            paper.write_text(r"x = \frac a b % \frac c d" + "\n" + r"% \frac e f", encoding="utf-8")
            matches = find_notation_occurrences(record, directory)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["file"], paper)
        self.assertEqual(matches[0]["column"], 5)
        self.assertEqual(matches[0]["arguments"], ["a", "b"])
        self.assertEqual(matches[0]["pattern_type"], "command")

    def test_extracted_infix_and_sequence_rules(self):
        infix = parse_symdef(r"\symdef{natmorethan}[args=ai]{#1\mathrel{\maincomp{>}}#2}")
        sequence = parse_symdef(r"\symdef{natplus}[args=a]{\argsep{#1}{\mathbin{\maincomp+}}}")
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "paper.tex"
            paper.write_text("a > b\na+b+c+d\n", encoding="utf-8")
            greater = find_notation_occurrences(infix, directory)
            additions = find_notation_occurrences(sequence, directory)
        self.assertEqual(greater[0]["arguments"], ["a", "b"])
        self.assertEqual(additions[0]["arguments"], ["a", "b", "c", "d"])
        self.assertEqual(additions[0]["line"], 2)

    def test_unsupported_template_is_skipped(self):
        record = parse_symdef(r"\symdef{complexplus}{#1\comp{+}#2\comp{i}}")
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "paper.tex").write_text("a+bi", encoding="utf-8")
            self.assertEqual(find_notation_occurrences(record, directory), [])


if __name__ == "__main__":
    unittest.main()
