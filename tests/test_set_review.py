import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import review_workflow
from apply_annotations import build_application_plan, render_application_plan
from decision_validator import materialize_valid_decisions, validate_decisions
from expression_document import find_ast_occurrences, parse_math_regions, parse_papers
from expression_metadata import build_expression_config
from expression_registry import NotationRegistry
from review_scope import build_review_scopes, render_review_scope
from smglom_notation_extractor import scan_available_smglom


class TestSetReview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paper = ROOT / "tests" / "fixtures" / "review_demo_sets.tex"
        cls.demo_path = ROOT / "tests" / "fixtures" / "demo-sets-config.json"
        cls.demo_raw = json.loads(cls.demo_path.read_text(encoding="utf-8"))
        cls.demo = build_expression_config(cls.demo_raw)
        cls.production_raw = json.loads(
            (ROOT / "expression-config.example.json").read_text(encoding="utf-8-sig"))
        cls.production = build_expression_config(cls.production_raw)
        cls.records = scan_available_smglom(quiet=True)

    def structural_scopes(self, source, symbol):
        rule = review_workflow.structural_review_classification(symbol, self.production)
        regions = parse_math_regions("$" + source + "$", NotationRegistry.from_config(self.production))
        matches = find_ast_occurrences(regions, rule, self.production)
        for match in matches:
            match["classification"] = dict(rule)
        return build_review_scopes(matches, (), include_structural=True)

    def demo_candidate(self, symbol, macro):
        return next(item for item in review_workflow.find_expression_candidates(
            symbol, self.demo, self.records) if item["macro"] == macro)

    def test_generic_structural_ast_for_set_operators_and_nested_function(self):
        regions = parse_math_regions(
            "$A \\cup B$ $A \\cap B$ $A \\subseteq B$ $x \\in A$ $P(A \\cup B)$",
            NotationRegistry.from_config(self.production))
        for region, token in zip(regions[:4], (r"\cup", r"\cap", r"\subseteq", r"\in")):
            self.assertIn(region.tree.kind, {"BinaryOperator", "NaryOperator"})
            self.assertEqual(region.tree.value, token)
        nested = regions[4].tree
        self.assertEqual(nested.kind, "FunctionApplication")
        argument = nested.children[1]
        self.assertEqual(argument.kind, "GroupedExpression")
        self.assertEqual((argument.children[0].kind, argument.children[0].value),
                         ("NaryOperator", r"\cup"))

    def test_nested_union_scope_and_ellipsis_sequence_are_complete(self):
        scope = self.structural_scopes(r"P(A \cup B)", r"\cup")[0]
        self.assertEqual(scope.matched_source, r"A \cup B")
        self.assertEqual(scope.highlighted, r"P([A \cup B])")
        ellipsis = self.structural_scopes(r"P(A_1 \cup \dots \cup A_k)", r"\cup")[0]
        self.assertEqual(ellipsis.matched_source, r"A_1 \cup \dots \cup A_k")
        self.assertEqual(ellipsis.highlighted, r"P([A_1 \cup \dots \cup A_k])")
        self.assertEqual(ellipsis.arguments, (r"A_1", r"\dots", r"A_k"))
        self.assertEqual(ellipsis.simple_structure, "union sequence")

    def test_structural_only_scope_is_visible_and_not_review_ready(self):
        scope = self.structural_scopes(r"A \cup B", r"\cup")[0]
        self.assertTrue(scope.structurally_ready)
        self.assertFalse(scope.semantic_mapping_available)
        self.assertFalse(scope.review_ready)
        rendered = render_review_scope(scope)
        self.assertIn("Semantic status:\nreal set-theory semantic metadata unavailable locally",
                      rendered)
        self.assertNotIn("Semantic candidates:", rendered)

    def test_exact_spacing_and_highlights_are_preserved(self):
        for source in (r"A\cup B", r"A \cup B", r"A\cup B", r"A \cup  B"):
            with self.subTest(source=source):
                scope = self.structural_scopes(source, r"\cup")[0]
                self.assertEqual(scope.matched_source, source)
                self.assertEqual(scope.highlighted, "[" + source + "]")

    def test_relation_labels_and_membership_roles_come_from_metadata(self):
        subset = self.structural_scopes(r"A \subseteq B", r"\subseteq")[0]
        member = self.structural_scopes(r"x \in A", r"\in")[0]
        self.assertEqual((subset.simple_structure, subset.argument_roles),
                         ("subset relation", ("left", "right")))
        self.assertIn("element: x", render_review_scope(member))
        self.assertIn("set: A", render_review_scope(member))

    def test_production_has_no_set_candidate_and_p_is_only_structural(self):
        self.assertEqual(review_workflow.find_generic_candidates(r"\cup", self.records), [])
        self.assertFalse(any(item.get("semantic_macro") for item in
                             self.production.get("operators", ()) if item["token"] == r"\cup"))
        self.assertNotIn(r"\P", self.production.get("commands", {}))

    def test_demo_config_is_explicit_and_candidates_keep_provenance(self):
        self.assertTrue(self.demo_raw["demo_only"])
        self.assertIn("not real SMGloM", self.demo_raw["description"])
        candidate = self.demo_candidate(r"\cup", "fooUnion")
        self.assertEqual(candidate["semantic_source"],
                         "test/demo configuration; not real SMGloM")

    def test_demo_decision_round_trip_validates_and_plans_application(self):
        candidate = self.demo_candidate(r"\cup", "fooUnion")
        matches = find_ast_occurrences(parse_papers(self.paper, self.demo),
                                       candidate["classification"], self.demo, candidate)
        scope = next(item for item in build_review_scopes(matches, (candidate,))
                     if item.matched_source == r"A \cup B")
        classification = dict(candidate["classification"], expression_config=self.demo_raw)
        decision = review_workflow.build_review_decision(
            scope.match, candidate, classification, self.paper)
        self.assertEqual(decision["semantic_suggestion"], r"\fooUnion{A,B}")
        validation = validate_decisions([decision], self.records, self.paper)
        self.assertEqual(len(validation["valid"]), 1,
                         (validation["stale"], validation["unverifiable"]))
        source = self.paper.read_text(encoding="utf-8")
        plan = build_application_plan(source, materialize_valid_decisions(validation), self.paper.name)
        rendered, _, failures, count = render_application_plan(source, plan)
        self.assertEqual((count, failures), (1, []))
        self.assertIn(r"\fooUnion{A,B}", rendered)
        self.assertEqual(self.paper.read_text(encoding="utf-8"), source)

    def test_production_preview_shows_structure_without_demo_fallback(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["review_workflow.py", "--paper", str(self.paper),
                                        "--preview"]), \
             patch("builtins.input", return_value=r"\cup"), redirect_stdout(output):
            review_workflow.main()
        text = output.getvalue()
        self.assertIn("Semantic status:\nreal set-theory semantic metadata unavailable locally", text)
        self.assertNotIn(r"\fooUnion", text)


if __name__ == "__main__":
    unittest.main()
