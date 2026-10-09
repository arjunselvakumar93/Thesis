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
from expression_document import find_structural_occurrences, parse_papers
from notation_catalog import build_expression_config
from review_scope import build_review_scopes, render_review_scope


class TestStructuralReviewFallback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paper = ROOT / "tests" / "fixtures" / "structural_fallback_cases.tex"
        cls.raw = json.loads((ROOT / "expression-config.example.json").read_text(
            encoding="utf-8-sig"))
        cls.config = build_expression_config(cls.raw, [])
        cls.regions = parse_papers(cls.paper, cls.config)

    def scopes(self, query):
        matches = find_structural_occurrences(self.regions, query, self.config)
        return build_review_scopes(matches, (), include_structural=True)

    def test_symbol_and_scripted_symbol_are_structural_only(self):
        symbol = next(scope for scope in self.scopes(r"\omega")
                      if scope.full_math_region.strip() == r"\omega")
        self.assertEqual((symbol.matched_source, symbol.simple_structure),
                         (r"\omega", "symbol"))
        self.assertEqual(symbol.highlighted, r"[\omega]")
        self.assertTrue(symbol.structurally_ready)
        self.assertFalse(symbol.semantic_mapping_available)
        self.assertFalse(symbol.review_ready)
        script = next(scope for scope in self.scopes(r"\omega_i")
                      if scope.full_math_region.strip() == r"\omega_i")
        self.assertEqual(script.simple_structure, "scripted expression")
        self.assertEqual(script.arguments, (r"\omega", "i"))
        rendered = render_review_scope(script)
        self.assertIn("base: \\omega", rendered)
        self.assertIn("subscript: i", rendered)

    def test_relation_function_sum_opaque_and_raw_product_are_discoverable(self):
        cases = {
            r"\omega_i\in A": "membership relation",
            r"P(\omega_i)": "function application",
            r"\sum": "summation",
            r"\foo{x}": "opaque command application",
            "*": "multiplication",
        }
        for query, structure in cases.items():
            with self.subTest(query=query):
                scopes = self.scopes(query)
                self.assertTrue(scopes)
                self.assertIn(structure, {scope.simple_structure for scope in scopes})
                self.assertTrue(all(not scope.semantic_candidates for scope in scopes))

    def test_opaque_arguments_and_exact_highlights_are_preserved(self):
        scope = self.scopes(r"\foo{x}")[0]
        self.assertEqual(scope.arguments, ("x",))
        self.assertEqual(scope.argument_roles, ("arg1",))
        self.assertIn(r"[\foo{x}]+y", scope.highlighted)
        rendered = render_review_scope(scope)
        self.assertIn("Annotation status:\nNot annotation-ready.", rendered)

    def test_cli_omega_uses_structural_fallback_instead_of_unmatched_message(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["review_workflow.py", "--paper", str(self.paper),
                                        "--preview"]), \
             patch("builtins.input", return_value=r"\omega"), redirect_stdout(output):
            review_workflow.main()
        text = output.getvalue()
        self.assertIn("Found ", text)
        self.assertIn("structural review scope", text)
        self.assertIn("Suggested scope:\n\\omega", text)
        self.assertNotIn("No matching notation was found", text)
        self.assertNotIn("Semantic candidates:", text)

    def test_raw_star_is_structural_without_config_and_semantic_with_test_config(self):
        output = io.StringIO()
        with redirect_stdout(output):
            count = review_workflow.run_structural_preview(
                "*", self.paper, self.raw, self.regions)
        self.assertEqual(count, 1)
        self.assertIn("Structure:\nmultiplication", output.getvalue())
        self.assertIn("Suggested scope:\np(x) * q(x)", output.getvalue())
        self.assertNotIn(r"\fooTimes", output.getvalue())
        test_config_path = ROOT / "tests" / "fixtures" / "configs/test_raw_star_semantics.json"
        test_config = build_expression_config(json.loads(test_config_path.read_text(encoding="utf-8")), [])
        candidates = review_workflow.find_expression_candidates("*", test_config, [])
        self.assertEqual({candidate["macro"] for candidate in candidates}, {"fooTimes"})

    def test_existing_semantic_review_keeps_priority(self):
        candidate = {"macro": "realCandidate", "classification": {
            "pattern_type": "sequence", "separator": "+"}}
        with patch.object(sys, "argv", ["review_workflow.py", "--preview"]), \
             patch("builtins.input", return_value="+"), \
             patch.object(review_workflow, "find_generic_candidates", return_value=[candidate]), \
             patch.object(review_workflow, "run_generic_review") as semantic, \
             patch.object(review_workflow, "run_structural_preview") as structural:
            review_workflow.main()
        semantic.assert_called_once()
        structural.assert_not_called()


if __name__ == "__main__":
    unittest.main()
