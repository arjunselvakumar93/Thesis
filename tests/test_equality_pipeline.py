from contextlib import redirect_stdout
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import apply_annotations
import review_workflow
from ast_test_helpers import match_rule as match_notation
from ast_test_helpers import scan_rule as find_notation_occurrences
from smglom_notation_extractor import SMGLOM_REPOSITORIES, scan_repository


class TestEqualityPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = scan_repository("mv", SMGLOM_REPOSITORIES["mv"])
        cls.eq_record = next(record for record in cls.records if record.get("macro") == "eq")
        cls.notequal_record = next(
            record for record in cls.records if record.get("macro") == "notequal"
        )
        cls.eq_candidate = next(
            candidate for candidate in review_workflow.find_generic_candidates("=", cls.records)
            if candidate["macro"] == "eq"
        )
        cls.notequal_candidate = next(
            candidate for candidate in review_workflow.find_generic_candidates(r"\ne", cls.records)
            if candidate["macro"] == "notequal"
        )

    def test_real_smglom_records_determine_the_rules(self):
        self.assertEqual(
            {key: self.eq_record.get(key) for key in
             ("repository", "module", "macro", "name", "args", "notation", "placeholders")},
            {
                "repository": "mv", "module": "equal", "macro": "eq",
                "name": "equal", "args": "a",
                "notation": r"\argsep{#1}{\mathrel{\maincomp=}}",
                "placeholders": [1],
            },
        )
        self.assertEqual(
            self.eq_candidate["classification"],
            {
                "pattern_type": "sequence", "separator": "=",
                "separator_fragment": r"\mathrel{\maincomp=}",
                "variable_arguments": True,
                "operator_role": "relation", "precedence": 700,
                "grammar_supported": True, "fixity": "infix",
                "arity": "variable",
            },
        )
        self.assertEqual(
            {key: self.notequal_record.get(key) for key in
             ("repository", "module", "macro", "name", "args", "notation", "placeholders")},
            {
                "repository": "mv", "module": "equal", "macro": "notequal",
                "name": "not equal", "args": "ai",
                "notation": r"#1\mathrel{\maincomp\ne}#2",
                "placeholders": [1, 2],
            },
        )
        self.assertEqual(
            self.notequal_candidate["classification"],
            {
                "pattern_type": "binary_infix",
                "operator_fragment": r"\mathrel{\maincomp\ne}",
                "operator": r"\ne", "argument_count": 2,
                "operator_role": "relation", "precedence": 800,
                "grammar_supported": True, "fixity": "infix", "arity": 2,
            },
        )

    def test_relation_sequence_uses_complete_expression_operands(self):
        equality = self.eq_candidate["classification"]
        for expression, expected in (
            ("a+b = c", ["a+b", "c"]),
            ("a+b = c-d", ["a+b", "c-d"]),
            ("x^2 + Bx + C = 0", ["x^2 + Bx + C", "0"]),
            (
                "x^2 + Bx + C = (x-R)(x-S)",
                ["x^2 + Bx + C", "(x-R)(x-S)"],
            ),
            (
                "(u+v)(u-v) = u^2 - v^2",
                ["(u+v)(u-v)", "u^2 - v^2"],
            ),
            (
                "(u + v)^2 = u^2 + 2uv + v^2",
                ["(u + v)^2", "u^2 + 2uv + v^2"],
            ),
            (r"R = S = -\frac{B}{2}", ["R", "S", r"-\frac{B}{2}"]),
            ("a+b = c-d = x^2", ["a+b", "c-d", "x^2"]),
            ("A=B", ["A", "B"]),
            ("x^2=K", ["x^2", "K"]),
            (r"x=\frac{a}{b}", ["x", r"\frac{a}{b}"]),
            (
                "x^2 + Bx + C = (x-R)(x-S),",
                ["x^2 + Bx + C", "(x-R)(x-S)"],
            ),
        ):
            with self.subTest(expression=expression):
                matches = match_notation(expression, equality)
                self.assertEqual(len(matches), 1)
                self.assertEqual(
                    matches[0]["matched_expression"], expression.rstrip(",")
                )
                self.assertEqual(matches[0]["arguments"], expected)

    def test_multiline_relation_preserves_complete_sides_and_source_location(self):
        source = (
            "Before prose.\n"
            "\\begin{equation}\n"
            "x^2 + Bx\n"
            "+ C = (x-R)\n"
            "(x-S)\n"
            "\\end{equation}\n"
            "\\begin{equation}\n"
            "(u+v)^2 = u^2 + 2uv +\n"
            "v^2\n"
            "\\end{equation}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "paper.tex"
            paper.write_text(source, encoding="utf-8")
            matches = find_notation_occurrences(
                self.eq_candidate["classification"], directory
            )

        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0]["line"], 3)
        self.assertEqual(matches[0]["column"], 1)
        self.assertEqual(
            matches[0]["arguments"],
            ["x^2 + Bx + C", "(x-R) (x-S)"],
        )
        self.assertEqual(
            matches[0]["matched_expression"],
            "x^2 + Bx + C = (x-R) (x-S)",
        )
        self.assertEqual(matches[1]["line"], 8)
        self.assertEqual(matches[1]["column"], 1)
        self.assertEqual(
            matches[1]["arguments"],
            ["(u+v)^2", "u^2 + 2uv + v^2"],
        )
        self.assertEqual(
            matches[1]["matched_expression"],
            "(u+v)^2 = u^2 + 2uv + v^2",
        )

    def test_real_rules_match_case_whitespace_and_complex_operands(self):
        equality = self.eq_candidate["classification"]
        for expression, expected in (
            ("a=b", ["a", "b"]), ("a = b", ["a", "b"]),
            ("a= b", ["a", "b"]), ("a =b", ["a", "b"]),
            ("A=b", ["A", "b"]), ("a=B", ["a", "B"]),
            ("A=B", ["A", "B"]), ("X = y", ["X", "y"]),
            ("x^2 = y^2", ["x^2", "y^2"]),
            ("x_i = y_j", ["x_i", "y_j"]),
            (r"\frac{a}{b} = c", [r"\frac{a}{b}", "c"]),
            (r"\sqrt{x} = y", [r"\sqrt{x}", "y"]),
            ("(A+B) = C", ["(A+B)", "C"]),
        ):
            with self.subTest(expression=expression):
                matches = match_notation(expression, equality)
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0]["arguments"], expected)

        notequal = self.notequal_candidate["classification"]
        for expression, expected in (
            (r"A\ne b", ["A", "b"]),
            (r"A \ne B", ["A", "B"]),
            (r"x \ne Y", ["x", "Y"]),
        ):
            with self.subTest(expression=expression):
                matches = match_notation(expression, notequal)
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0]["arguments"], expected)

        for expression, rule in (
            ("a =", equality), ("= b", equality),
            (r"a \ne", notequal), (r"\ne b", notequal),
        ):
            with self.subTest(incomplete=expression):
                self.assertEqual(match_notation(expression, rule), [])

    def test_paper_scanner_uses_math_context_and_original_locations(self):
        source = (
            "Prose A=B and A ne B must be ignored.\n"
            "$A=b$ and $x^2 = y^2$.\n"
            r"$\frac{a}{b} = c$ and $x \ne Y$." + "\n"
            r"$a =$ and $\ne b$" + "\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "paper.tex"
            paper.write_text(source, encoding="utf-8")
            equal_matches = find_notation_occurrences(
                self.eq_candidate["classification"], directory
            )
            notequal_matches = find_notation_occurrences(
                self.notequal_candidate["classification"], directory
            )
        self.assertEqual(
            [(item["line"], item["matched_expression"], item["arguments"])
             for item in equal_matches],
            [
                (2, "A=b", ["A", "b"]),
                (2, "x^2 = y^2", ["x^2", "y^2"]),
                (3, r"\frac{a}{b} = c", [r"\frac{a}{b}", "c"]),
            ],
        )
        self.assertEqual(
            [(item["line"], item["matched_expression"], item["arguments"])
             for item in notequal_matches],
            [(3, r"x \ne Y", ["x", "Y"])],
        )
        self.assertEqual(equal_matches[0]["column"], source.splitlines()[1].index("A=b") + 1)

    def test_review_persistence_and_apply_modify_only_copied_output(self):
        original = (
            "\\documentclass{article}\n"
            "$A=b$\n"
            "$x \\ne Y$\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            papers = root / "papers"
            output = root / "output" / "annotated_papers"
            decisions_file = root / "decisions" / "review_results.json"
            papers.mkdir()
            paper = papers / "paper.tex"
            paper.write_text(original, encoding="utf-8")

            with redirect_stdout(io.StringIO()), patch.object(review_workflow, "DECISIONS_DIR", decisions_file.parent), \
                 patch.object(review_workflow, "DECISION_FILE", decisions_file), \
                 patch.object(review_workflow, "choose_candidate", return_value=self.eq_candidate), \
                 patch.object(review_workflow, "ask_for_decision", return_value="accepted"):
                self.assertEqual(
                    review_workflow.run_generic_review(
                        "=", [self.eq_candidate], papers
                    ),
                    1,
                )
            with redirect_stdout(io.StringIO()), patch.object(review_workflow, "DECISIONS_DIR", decisions_file.parent), \
                 patch.object(review_workflow, "DECISION_FILE", decisions_file), \
                 patch.object(
                     review_workflow, "choose_candidate",
                     return_value=self.notequal_candidate,
                 ), \
                 patch.object(review_workflow, "ask_for_decision", return_value="accepted"):
                self.assertEqual(
                    review_workflow.run_generic_review(
                        r"\ne", [self.notequal_candidate], papers
                    ),
                    1,
                )

            decisions = json.loads(decisions_file.read_text(encoding="utf-8"))
            self.assertEqual(
                [item["semantic_suggestion"] for item in decisions],
                [r"\eq{A,b}", r"\notequal{x}{Y}"],
            )
            self.assertTrue(all(item["semantic_repository"] == "mv" for item in decisions))

            with patch.object(apply_annotations, "PAPERS_DIR", papers), \
                 patch.object(apply_annotations, "OUTPUT_DIR", output):
                apply_annotations.prepare_output_folder()
                self.assertEqual(apply_annotations.apply_annotations(decisions), 2)

            annotated = (output / "paper.tex").read_text(encoding="utf-8")
            self.assertEqual(paper.read_text(encoding="utf-8"), original)
            self.assertIn(r"$\eq{A,b}$", annotated)
            self.assertIn(r"$\notequal{x}{Y}$", annotated)
            self.assertIn(r"\usemodule[smglom/mv]{mod?equal}", annotated)


if __name__ == "__main__":
    unittest.main()
