import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from notation_scanner import find_notation_occurrences
from smglom_notation_extractor import parse_notation, parse_symdef


class TestNotationScanner(unittest.TestCase):
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
