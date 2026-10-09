import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from apply_annotations import build_application_plan, render_application_plan
from apply_annotations import materialize_valid_decisions, validate_decisions
from expression_document import find_ast_occurrences, parse_papers
from notation_catalog import build_expression_config
from notation_catalog import build_catalog, search_catalog
from review_scope import build_review_scopes, render_review_scope
from review_workflow import build_review_decision, find_expression_candidates
from smglom_notation_extractor import scan_available_smglom


class TestSummationReview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paper = ROOT / "tests" / "fixtures" / "review_sum_cases.tex"
        cls.variants = ROOT / "tests" / "fixtures" / "review_sum_cases.tex"
        cls.membership_path = ROOT / "tests" / "fixtures" / "configs/test_sum_membership_semantics.json"
        cls.bounded_path = ROOT / "tests" / "fixtures" / "configs/test_bounded_sum_semantics.json"
        cls.membership_raw = json.loads(cls.membership_path.read_text(encoding="utf-8"))
        cls.bounded_raw = json.loads(cls.bounded_path.read_text(encoding="utf-8"))
        cls.membership = build_expression_config(cls.membership_raw)
        cls.bounded = build_expression_config(cls.bounded_raw)
        cls.records = scan_available_smglom(quiet=True)

    def candidate(self, query, config, macro):
        return next(item for item in find_expression_candidates(query, config, self.records)
                    if item["macro"] == macro)

    def scopes(self, paper, config, candidate):
        matches = find_ast_occurrences(parse_papers(paper, config),
                                       candidate["classification"], config, candidate)
        return build_review_scopes(matches, (candidate,))

    def test_target_ast_preserves_membership_script_and_function_body(self):
        expression = r"\sum_{\omega_i\in A} P(\omega_i)"
        region = next(item for item in parse_papers(self.paper, self.membership)
                      if item.tree.text == expression)
        tree = region.tree
        self.assertEqual((tree.kind, tree.value), ("BigOperatorApplication", r"\sum"))
        self.assertEqual(tree.roles, ("lower", "body"))
        lower, body = tree.children
        relation = lower.children[0]
        self.assertEqual((relation.kind, relation.value), ("BinaryOperator", r"\in"))
        scripted = relation.children[0]
        self.assertEqual(scripted.kind, "ScriptedExpression")
        self.assertEqual(scripted.text, r"\omega_i")
        self.assertEqual(scripted.roles, ("base", "_"))
        self.assertEqual([child.text for child in scripted.children], [r"\omega", "i"])
        self.assertEqual(body.kind, "FunctionApplication")
        self.assertEqual(body.text, r"P(\omega_i)")

    def test_membership_sum_review_scope_and_exact_highlight(self):
        candidate = self.candidate(r"\sum", self.membership, "fooSum")
        expression = r"\sum_{\omega_i\in A} P(\omega_i)"
        scope = next(item for item in self.scopes(self.paper, self.membership, candidate)
                     if item.matched_source == expression)
        self.assertEqual(scope.matched_source, expression)
        self.assertEqual(scope.highlighted, "[" + expression + "]")
        self.assertEqual(scope.simple_structure, "summation")
        self.assertEqual(scope.argument_roles, ("lower", "body"))
        self.assertEqual(scope.arguments, (r"\omega_i\in A", r"P(\omega_i)"))
        source = self.paper.read_text(encoding="utf-8")
        self.assertEqual(source[scope.matched_start:scope.matched_end], expression)
        rendered = render_review_scope(scope)
        self.assertIn("index/domain: \\omega_i\\in A", rendered)
        self.assertIn("source: synthetic test configuration; not real SMGloM", rendered)
        self.assertNotIn("Internal AST", rendered)
        self.assertIn("BigOperatorApplication", render_review_scope(scope, show_ast=True))

    def test_bounded_sum_variants_keep_roles_and_complete_bodies(self):
        candidate = self.candidate(r"\sum", self.bounded, "fooBoundedSum")
        scopes = self.scopes(self.variants, self.bounded, candidate)
        by_source = {scope.matched_source: scope for scope in scopes}
        for expression, body in (
                (r"\sum_{i=1}^{n} a_i", "a_i"),
                (r"\sum_{i=1}^{n} f(i)", "f(i)"),
                (r"\sum_{i=1}^{n} (a_i+b_i)", "(a_i+b_i)"),
                (r"\sum_{i=1}^{n} (a_i+\dots+b_i)", r"(a_i+\dots+b_i)")):
            with self.subTest(expression=expression):
                scope = by_source[expression]
                self.assertEqual(scope.argument_roles, ("lower", "upper", "body"))
                self.assertEqual(scope.arguments, ("i=1", "n", body))
        self.assertEqual(by_source[r"\sum_{i=1}^{n} f(i)"].match["ast_node"]["kind"],
                         "BigOperatorApplication")

    def test_sum_inside_addition_highlights_only_sum(self):
        candidate = self.candidate(r"\sum", self.bounded, "fooBoundedSum")
        scope = next(scope for scope in self.scopes(self.variants, self.bounded, candidate)
                     if scope.matched_source == r"\sum_{i=1}^{n} a_i"
                     and scope.highlighted.startswith("x+"))
        self.assertEqual(scope.highlighted, r"x+[\sum_{i=1}^{n} a_i]")

    def test_membership_whitespace_variants_preserve_raw_source(self):
        candidate = self.candidate(r"\sum", self.membership, "fooSum")
        scopes = self.scopes(self.variants, self.membership, candidate)
        expected = {
            r"\sum_{\omega\in A} P(\omega)",
            r"\sum_{\omega_i\in A}P(\omega_i)",
            r"\sum_{\omega_i \in A} P(\omega_i)",
        }
        actual = {scope.matched_source for scope in scopes}
        self.assertTrue(expected <= actual)
        for scope in scopes:
            if scope.matched_source in expected:
                self.assertIn("[" + scope.matched_source + "]", scope.highlighted)

    def test_real_sum_records_expose_hidden_argument_blockers(self):
        entries = search_catalog(build_catalog(self.records), r"\sum")
        sums = [item for item in entries if item.get("module") == "sumprod"]
        self.assertEqual({item["macro"] for item in sums}, {
            "Sumfromto", "SumOver", "SumfromtoProp", "SumInColl", "SumProp"})
        self.assertTrue(all(item["source"] == "SMGloM" for item in sums))
        self.assertTrue(all(not item["classification"]["annotation_ready"] for item in sums))
        self.assertTrue(all(item["classification"]["annotation_blocker"]
                            == "missing_argument_mapping" for item in sums))
        self.assertTrue(all(len(item["record"]["placeholders"])
                            < len(item["record"]["args"]) for item in sums))

    def test_queries_find_only_explicit_synthetic_candidate(self):
        for query in (r"\sum", "sum", "fooSum"):
            with self.subTest(query=query):
                candidates = find_expression_candidates(query, self.membership, self.records)
                self.assertEqual({item["macro"] for item in candidates}, {"fooSum"})
                self.assertEqual(candidates[0]["semantic_source"],
                                 "synthetic test configuration; not real SMGloM")

    def test_synthetic_decision_serializes_validates_and_renders(self):
        candidate = self.candidate(r"\sum", self.membership, "fooSum")
        expression = r"\sum_{\omega_i\in A} P(\omega_i)"
        scope = next(item for item in self.scopes(self.paper, self.membership, candidate)
                     if item.matched_source == expression)
        classification = dict(candidate["classification"],
                              expression_config=self.membership_raw)
        decision = build_review_decision(scope.match, candidate, classification, self.paper)
        self.assertEqual(decision["semantic_suggestion"],
                         r"\fooSum{\omega_i\in A}{P(\omega_i)}")
        loaded = json.loads(json.dumps([decision]))
        validation = validate_decisions(loaded, self.records, self.paper)
        self.assertEqual(len(validation["valid"]), 1,
                         (validation["stale"], validation["unverifiable"]))
        source = self.paper.read_text(encoding="utf-8")
        current = materialize_valid_decisions(validation)
        plan = build_application_plan(source, current, self.paper.name)
        rendered, _, failures, count = render_application_plan(source, plan)
        self.assertEqual(count, 1)
        self.assertFalse(failures)
        self.assertIn(r"\fooSum{\omega_i\in A}{P(\omega_i)}", rendered)
        self.assertEqual(self.paper.read_text(encoding="utf-8"), source)


if __name__ == "__main__":
    unittest.main()
