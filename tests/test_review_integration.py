import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import review_workflow
from smglom_notation_extractor import SMGLOM_REPOSITORIES, scan_repository


class TestGenericReviewIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = scan_repository("arithmetics", SMGLOM_REPOSITORIES["arithmetics"])

    def candidate(self, symbol, macro):
        return next(candidate for candidate in
                    review_workflow.find_generic_candidates(symbol, self.records)
                    if candidate["macro"] == macro)

    def test_real_records_supply_rules_and_semantic_candidates(self):
        for symbol, macro, pattern_type, visible in (
            (r"\frac", "adivide", "command", r"\frac"),
            ("+", "natplus", "sequence", "+"),
            ("-", "natminus", "sequence", "-"),
            (">", "natmorethan", "binary_infix", ">"),
            ("<", "natlessthan", "binary_infix", "<"),
        ):
            with self.subTest(symbol=symbol, macro=macro):
                candidate = self.candidate(symbol, macro)
                rule = candidate["classification"]
                self.assertEqual(rule["pattern_type"], pattern_type)
                self.assertEqual(rule.get({"command": "command", "sequence": "separator",
                                           "binary_infix": "operator"}[pattern_type]), visible)
                self.assertEqual(candidate["module"], "arithmetics" if macro == "adivide" else "natarith")

        fraction_macros = {candidate["macro"] for candidate in
                           review_workflow.find_generic_candidates(r"\frac", self.records)}
        self.assertEqual(fraction_macros,
                         {"adivide", "compdivide", "ratdivide", "fraction", "realdivide"})
        plus_candidates = review_workflow.find_generic_candidates("+", self.records)
        self.assertTrue(plus_candidates)
        self.assertTrue(all(candidate["classification"]["pattern_type"] == "sequence"
                            for candidate in plus_candidates))

    def test_suggestions_use_match_arguments_and_preserve_case(self):
        for symbol, macro, expression, arguments, expected in (
            (r"\frac", "adivide", r"\frac a b", ["a", "b"], r"\adivide{a}{b}"),
            (">", "natmorethan", "A>B", ["A", "B"], r"\natmorethan{A}{B}"),
            ("+", "natplus", "A+b+C", ["A", "b", "C"], r"\natplus{A,b,C}"),
            ("+", "natplus", "A+b", ["A", "b"], r"\natplus{A,b}"),
            ("-", "natminus", "A-b-C", ["A", "b", "C"], r"\natminus{A,b,C}"),
        ):
            with self.subTest(expression=expression):
                candidate = self.candidate(symbol, macro)
                match = {"pattern_type": candidate["classification"]["pattern_type"],
                         "matched_expression": expression, "arguments": arguments}
                self.assertEqual(review_workflow.generate_classified_suggestion(match, candidate), expected)

    def test_incomplete_match_cannot_generate_a_suggestion(self):
        candidate = self.candidate("+", "natplus")
        self.assertIsNone(review_workflow.generate_classified_suggestion(
            {"pattern_type": "sequence", "arguments": []}, candidate))
        self.assertIsNone(review_workflow.generate_classified_suggestion(
            {"pattern_type": "sequence", "arguments": ["a"]}, candidate))

    def test_human_review_persists_decision_and_leaves_paper_unchanged(self):
        candidate = self.candidate(">", "natmorethan")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            papers = root / "papers"
            papers.mkdir()
            paper = papers / "paper.tex"
            original = "A>B\nx<Y\n"
            paper.write_text(original, encoding="utf-8")
            decisions = root / "decisions" / "review_results.json"
            with patch.object(review_workflow, "DECISIONS_DIR", decisions.parent), \
                 patch.object(review_workflow, "DECISION_FILE", decisions), \
                 patch.object(review_workflow, "choose_candidate", return_value=candidate), \
                 patch.object(review_workflow, "ask_for_decision", return_value="accepted"):
                self.assertEqual(review_workflow.run_generic_review(">", [candidate], papers), 1)
            stored = json.loads(decisions.read_text(encoding="utf-8"))
            self.assertEqual(stored[0]["semantic_suggestion"], r"\natmorethan{A}{B}")
            self.assertEqual(stored[0]["file"], "paper.tex")
            self.assertEqual(stored[0]["line"], 1)
            self.assertEqual(stored[0]["column"], 1)
            self.assertEqual(paper.read_text(encoding="utf-8"), original)

    def test_command_and_sequence_review_use_generic_scanner(self):
        for symbol, macro, original, expected in (
            (r"\frac", "adivide", r"\frac a b", r"\adivide{a}{b}"),
            ("+", "natplus", "A+b+C", r"\natplus{A,b,C}"),
        ):
            with self.subTest(symbol=symbol):
                candidate = self.candidate(symbol, macro)
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    papers = root / "papers"
                    papers.mkdir()
                    paper = papers / "paper.tex"
                    paper.write_text(original + "\n", encoding="utf-8")
                    decisions = root / "decisions" / "review_results.json"
                    with patch.object(review_workflow, "DECISIONS_DIR", decisions.parent), \
                         patch.object(review_workflow, "DECISION_FILE", decisions), \
                         patch.object(review_workflow, "choose_candidate", return_value=candidate), \
                         patch.object(review_workflow, "ask_for_decision", return_value="accepted"):
                        self.assertEqual(review_workflow.run_generic_review(symbol, [candidate], papers), 1)
                    stored = json.loads(decisions.read_text(encoding="utf-8"))
                    self.assertEqual(stored[0]["semantic_suggestion"], expected)
                    self.assertEqual(paper.read_text(encoding="utf-8"), original + "\n")

    def test_incomplete_paper_expression_is_not_reviewed(self):
        candidate = self.candidate("+", "natplus")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / "paper.tex"
            paper.write_text("a+\n", encoding="utf-8")
            with patch.object(review_workflow, "choose_candidate", return_value=candidate), \
                 patch.object(review_workflow, "ask_for_decision") as ask:
                self.assertEqual(review_workflow.run_generic_review("+", [candidate], root), 0)
            ask.assert_not_called()

    def test_main_routes_supported_rule_to_generic_review(self):
        candidate = self.candidate(">", "natmorethan")
        with patch.object(sys, "argv", ["review_workflow.py"]), \
             patch("builtins.input", return_value=">"), \
             patch.object(review_workflow, "find_generic_candidates", return_value=[candidate]), \
             patch.object(review_workflow, "run_generic_review") as generic, \
             patch.object(review_workflow, "find_symbol_definitions") as legacy:
            review_workflow.main()
        generic.assert_called_once_with(">", [candidate])
        legacy.assert_not_called()

    def test_main_keeps_explicit_legacy_command_path(self):
        with patch.object(sys, "argv", ["review_workflow.py", "--legacy"]), \
             patch("builtins.input", return_value=r"\frac"), \
             patch.object(review_workflow, "find_generic_candidates") as generic, \
             patch.object(review_workflow, "find_symbol_definitions", return_value=[]) as legacy:
            review_workflow.main()
        generic.assert_not_called()
        legacy.assert_called_once_with(r"\frac")


if __name__ == "__main__":
    unittest.main()
