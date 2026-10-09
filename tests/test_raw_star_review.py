import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import review_workflow
from expression_document import find_ast_occurrences, parse_papers
from notation_catalog import build_expression_config
from review_scope import build_review_scopes
from smglom_notation_extractor import scan_available_smglom


class TestRawStarReview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config_path = ROOT / "tests" / "fixtures" / "configs/test_raw_star_semantics.json"
        cls.paper = ROOT / "tests" / "fixtures" / "review_scope_cases.tex"
        cls.raw_config = json.loads(cls.config_path.read_text(encoding="utf-8"))
        cls.config = build_expression_config(cls.raw_config)
        cls.records = scan_available_smglom(quiet=True)

    def test_production_catalogue_does_not_map_raw_star(self):
        self.assertEqual(review_workflow.find_generic_candidates("*", self.records), [])

    def test_synthetic_config_has_no_times_alias(self):
        self.assertTrue(self.raw_config["test_only"])
        self.assertIn("not real SMGloM", self.raw_config["description"])
        star = next(item for item in self.raw_config["operators"] if item["token"] == "*")
        self.assertEqual(star["semantic_macro"], "fooTimes")
        self.assertEqual(star["source"], "synthetic test configuration; not real SMGloM")
        self.assertNotIn("variants", star)
        self.assertNotIn("notation_aliases", self.raw_config)
        self.assertNotIn(r"\times", json.dumps(star))

    def test_synthetic_config_reviews_exact_outer_scope(self):
        candidates = review_workflow.find_expression_candidates("*", self.config, self.records)
        self.assertEqual({item["macro"] for item in candidates}, {"fooTimes"})
        candidate = candidates[0]
        matches = find_ast_occurrences(parse_papers(self.paper, self.config),
                                       candidate["classification"], self.config, candidate)
        scopes = build_review_scopes(matches, candidates)
        self.assertEqual(len(scopes), 1)
        scope = scopes[0]
        expression = "p(x+y) * p(x - y)"
        self.assertEqual(scope.matched_source, expression)
        self.assertEqual(scope.highlighted, "[" + expression + "]")
        self.assertEqual(scope.arguments, ("p(x+y)", "p(x - y)"))
        self.assertEqual(scope.argument_roles, ("left", "right"))
        self.assertEqual(scope.simple_structure, "multiplication")
        source = self.paper.read_text(encoding="utf-8")
        self.assertEqual(source[scope.matched_start:scope.matched_end], expression)

    def test_inner_scopes_remain_structurally_available(self):
        plus = {"macro": "testPlus", "name": "addition", "module": "test",
                "classification": {"pattern_type": "sequence", "separator": "+"}}
        minus = {"macro": "testMinus", "name": "subtraction", "module": "test",
                 "classification": {"pattern_type": "sequence", "separator": "-"}}
        regions = parse_papers(self.paper, self.config)
        matches = []
        for candidate in (plus, minus):
            matches.extend(find_ast_occurrences(regions, candidate["classification"],
                                                self.config, candidate))
        scopes = build_review_scopes(matches, (plus, minus))
        self.assertEqual([scope.matched_source for scope in scopes], ["x+y", "x - y"])

    def test_preview_uses_synthetic_candidate_without_writing(self):
        candidates = review_workflow.find_expression_candidates("*", self.config, self.records)
        regions = parse_papers(self.paper, self.config)
        output = io.StringIO()
        with patch.object(review_workflow, "save_decisions") as save, redirect_stdout(output):
            reviewed = review_workflow.run_generic_review(
                "*", candidates, self.paper, self.config, regions, preview=True)
        self.assertEqual(reviewed, 1)
        save.assert_not_called()
        text = output.getvalue()
        self.assertIn("Original formula:\np(x+y) * p(x - y)", text)
        self.assertIn("Suggested scope:\np(x+y) * p(x - y)", text)
        self.assertIn("Highlighted:\n[p(x+y) * p(x - y)]", text)
        self.assertIn("Structure:\nmultiplication", text)
        self.assertIn("left: p(x+y)", text)
        self.assertIn("right: p(x - y)", text)
        self.assertIn(r"[1] \fooTimes", text)

    def test_main_without_synthetic_config_reports_structural_scope(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["review_workflow.py", "--paper", str(self.paper),
                                        "--preview"]), \
             patch("builtins.input", return_value="*"), \
             redirect_stdout(output):
            review_workflow.main()
        text = output.getvalue()
        self.assertIn("Structure:\nmultiplication", text)
        self.assertIn("Annotation status:\nNot annotation-ready.", text)
        self.assertNotIn(r"\fooTimes", text)


if __name__ == "__main__":
    unittest.main()
