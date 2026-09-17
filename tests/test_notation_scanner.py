import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from notation_classifier import classify_notation
from notation_scanner import extract_math_regions, find_notation_occurrences
from smglom_notation_extractor import parse_notation, parse_symdef


def sequence_rule(separator, macro="operation"):
    return classify_notation(parse_symdef(
        rf"\symdef{{{macro}}}[args=a]{{\argsep{{#1}}{{\mathbin{{\maincomp{separator}}}}}}}"
    ))


def binary_rule(operator, macro="relation"):
    return classify_notation(parse_symdef(
        rf"\symdef{{{macro}}}[args=ai]{{#1\mathrel{{\maincomp{{{operator}}}}}#2}}"
    ))


class TestNotationScanner(unittest.TestCase):
    def scan(self, source, rule):
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "nested" / "paper.tex"
            paper.parent.mkdir()
            paper.write_text(source, encoding="utf-8")
            return find_notation_occurrences(rule, directory), paper

    def test_prose_hyphens_are_ignored(self):
        matches, _ = self.scan(
            "guess-and-check method\nfirst-time Algebra learners\nstate-of-the-art\n",
            sequence_rule("-", "natminus"),
        )
        self.assertEqual(matches, [])

    def test_inline_math_subtraction_and_original_location(self):
        source = "The guess-and-check method gives $A-b-C$.\n"
        matches, paper = self.scan(source, sequence_rule("-", "natminus"))
        self.assertEqual(matches, [{
            "file": paper,
            "line": 1,
            "column": source.index("A-b-C") + 1,
            "text": source.rstrip("\n"),
            "matched_expression": "A-b-C",
            "matched_visible_notation": "-",
            "arguments": ["A", "b", "C"],
            "pattern_type": "sequence",
        }])

    def test_plus_outside_math_is_ignored_and_math_sequence_matches(self):
        matches, _ = self.scan(
            "C++-style text and ordinary A+B prose. Math: $A+b+C$.\n",
            sequence_rule("+", "natplus"),
        )
        self.assertEqual(
            [(match["matched_expression"], match["arguments"]) for match in matches],
            [("A+b+C", ["A", "b", "C"])],
        )

    def test_relations_only_match_inside_math(self):
        rules = (
            binary_rule(">", "natmorethan"),
            binary_rule("<", "natlessthan"),
            sequence_rule("=", "eq"),
        )
        source = "$A>B$ and \\(x<Y\\) and $A=B$; prose C>D and E=F.\n"
        groups = [self.scan(source, rule)[0] for rule in rules]
        self.assertEqual(groups[0][0]["arguments"], ["A", "B"])
        self.assertEqual(groups[1][0]["arguments"], ["x", "Y"])
        self.assertEqual(groups[2][0]["arguments"], ["A", "B"])
        self.assertEqual([len(group) for group in groups], [1, 1, 1])

    def test_command_inside_math_preserves_case_expression_and_location(self):
        rule = classify_notation(parse_notation(r"\notation{adivide}{\frac{#1}{#2}}"))
        source = r"Text $\frac{A}{b}$ and prose \frac C D." + "\n"
        matches, _ = self.scan(source, rule)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["matched_expression"], r"\frac{A}{b}")
        self.assertEqual(matches[0]["arguments"], ["A", "b"])
        self.assertEqual(matches[0]["column"], source.index(r"\frac{A}{b}") + 1)

    def test_display_delimiters_and_supported_environments(self):
        source = (
            "\\[\nA-b\n\\]\n"
            "$$C-d$$\n"
            "\\begin{equation}\nE-f=0\n\\end{equation}\n"
            "\\begin{equation*}G-h\\end{equation*}\n"
            "\\begin{align}I-j\\end{align}\n"
            "\\begin{align*}K-l\\end{align*}\n"
            "\\begin{gather}M-n\\end{gather}\n"
            "\\begin{multline*}O-p\\end{multline*}\n"
        )
        matches, _ = self.scan(source, sequence_rule("-", "natminus"))
        self.assertEqual(
            [match["matched_expression"] for match in matches],
            ["A-b", "C-d", "E-f", "G-h", "I-j", "K-l", "M-n", "O-p"],
        )
        self.assertEqual(matches[0]["line"], 2)
        self.assertEqual(matches[0]["column"], 1)
        self.assertEqual(matches[2]["text"], "E-f=0")

    def test_comments_and_escaped_percent_preserve_active_math(self):
        source = "$a-b$ % $X-Y$\n$a-c$ \\% $D-e$ % $F-g$\n% $H-i$\n"
        matches, _ = self.scan(source, sequence_rule("-", "natminus"))
        self.assertEqual(
            [(match["line"], match["matched_expression"]) for match in matches],
            [(1, "a-b"), (2, "a-c"), (2, "D-e")],
        )

    def test_incomplete_math_expressions_do_not_match(self):
        for separator, source in (
            ("-", "$a-$ $-b$"),
            ("=", "$a=$ $=b$"),
        ):
            with self.subTest(separator=separator):
                matches, _ = self.scan(source, sequence_rule(separator))
                self.assertEqual(matches, [])

    def test_complex_operands_and_structural_lines_in_real_math_context(self):
        source = (
            "\\begin{equation}\n"
            "\\label{eq:formula-monic}\n"
            "-\\frac{B}{2}\n"
            "\\frac{B^2}{4} - C\n"
            "u^2 - v^2\n"
            "\\frac{B^2}{4} - \\frac{B^2}{4}\n"
            "\\end{equation}\n"
        )
        matches, _ = self.scan(source, sequence_rule("-", "natminus"))
        fraction = r"\frac{B^2}{4}"
        self.assertEqual(
            [(match["line"], match["matched_expression"], match["arguments"])
             for match in matches],
            [
                (4, fraction + " - C", [fraction, "C"]),
                (5, "u^2 - v^2", ["u^2", "v^2"]),
                (6, fraction + " - " + fraction, [fraction, fraction]),
            ],
        )

    def test_parenthesized_and_signed_subtractions_preserve_locations(self):
        source = (
            "Products: $(a-b)(c-d)$.\n"
            "Signed: $-a - b$ and $-\\frac{B}{2} - z$.\n"
        )
        matches, _ = self.scan(source, sequence_rule("-", "natminus"))
        self.assertEqual(
            [(match["line"], match["column"], match["matched_expression"],
              match["arguments"]) for match in matches],
            [
                (1, source.splitlines()[0].index("a-b") + 1, "a-b", ["a", "b"]),
                (1, source.splitlines()[0].index("c-d") + 1, "c-d", ["c", "d"]),
                (2, source.splitlines()[1].index("-a - b") + 1,
                 "-a - b", ["-a", "b"]),
                (2, source.splitlines()[1].index(r"-\frac{B}{2} - z") + 1,
                 r"-\frac{B}{2} - z", [r"-\frac{B}{2}", "z"]),
            ],
        )

    def test_multiple_inline_math_regions_on_one_line(self):
        matches, _ = self.scan(
            "$a+b$ prose $X+Y$ % $Z+W$\n",
            sequence_rule("+", "natplus"),
        )
        self.assertEqual(
            [(match["column"], match["matched_expression"]) for match in matches],
            [(2, "a+b"), (14, "X+Y")],
        )

    def test_math_region_extractor_ignores_unclosed_and_commented_regions(self):
        source = "prose $unclosed\n% $A-b$\n\\(C-d\\)\n"
        regions = extract_math_regions(source)
        self.assertEqual([region["text"] for region in regions], ["C-d"])

    def test_unsupported_template_is_skipped(self):
        record = parse_symdef(r"\symdef{complexplus}{#1\comp{+}#2\comp{i}}")
        matches, _ = self.scan("$a+bi$", record)
        self.assertEqual(matches, [])


if __name__ == "__main__":
    unittest.main()
