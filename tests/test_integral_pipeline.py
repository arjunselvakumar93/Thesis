import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from apply_annotations import build_application_plan, render_application_plan
from expression_annotation import match_ast
from expression_metadata import build_expression_config, configured_annotation_candidates
from expression_parser import ExpressionParser, ParseError
from expression_registry import NotationRegistry
from expression_document import parse_math_regions
from review_workflow import find_expression_candidates, generate_classified_suggestion


class IntegralTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = json.loads((ROOT / "integral-config.example.json").read_text(encoding="utf-8"))
        cls.config = build_expression_config(raw)
        cls.registry = NotationRegistry.from_config(cls.config)
        cls.parser = ExpressionParser(registry=cls.registry)


class TestIntegralStructure(IntegralTestCase):
    def test_integral_fixture_regions_all_parse(self):
        fixture = ROOT / "tests" / "fixtures" / "integral_expressions.tex"
        regions = parse_math_regions(fixture.read_text(encoding="utf-8"), self.registry, fixture)
        self.assertEqual(len(regions), 8)
        self.assertTrue(all(not region.error for region in regions))

    def test_definite_indefinite_and_one_sided_bounds(self):
        cases = ((r"\int_0^1 f(x)\,dx", ("lower", "upper", "body", "variable")),
                 (r"\int f(x)\,dx", ("body", "variable")),
                 (r"\int_0 f(x)\,dx", ("lower", "body", "variable")),
                 (r"\int^1 f(x)\,dx", ("upper", "body", "variable")))
        for source, roles in cases:
            with self.subTest(source=source):
                tree = self.parser.parse(source)
                self.assertEqual((tree.kind, tree.value), ("BoundExpression", r"\int"))
                self.assertEqual(tree.roles, roles)

    def test_grouped_and_complex_integrands_preserve_roles(self):
        sources = (r"\int_0^1 (a+b*x)\,dx",
                   r"\int_0^1 {a+b*x}\,dx",
                   r"\int_0^1 \left(a+b*x\right)\,dx",
                   r"\int_0^1 \frac{x^2+1}{\sqrt{1-x}}\,dx",
                   r"\int_{-1}^{1} x^2\,dx",
                   r"\int_0^\pi \sin(x)\,dx")
        for source in sources:
            with self.subTest(source=source):
                tree = self.parser.parse(source)
                role_map = dict(zip(tree.roles, tree.children))
                self.assertEqual(role_map["variable"].text, "x")
                self.assertTrue(role_map["body"].text)

    def test_integral_is_one_subexpression_and_nesting_is_structural(self):
        addition = self.parser.parse(r"a+\int_0^1 b*x\,dx")
        self.assertEqual(addition.value, "+")
        self.assertEqual(addition.children[1].kind, "BoundExpression")
        fraction = self.parser.parse(r"\frac{\int_0^1 f(x)\,dx}{2}")
        self.assertEqual(fraction.children[0].children[0].kind, "BoundExpression")
        outer = self.parser.parse(r"\int_0^1 \left(\int_0^x f(t)\,dt\right)\,dx")
        bound_nodes = [node for node in outer.walk() if node.kind == "BoundExpression"]
        self.assertEqual(len(bound_nodes), 2)
        self.assertEqual([dict(zip(node.roles, node.children))["variable"].text
                          for node in bound_nodes], ["x", "t"])

    def test_config_only_synthetic_and_multiple_integral_heads(self):
        synthetic = self.parser.parse(r"\fooIntegral_0^1 x^2\,dx")
        self.assertEqual((synthetic.kind, synthetic.value), ("BoundExpression", r"\fooIntegral"))
        double = self.parser.parse(r"\iint_D f(x)\,dx\,dy")
        self.assertEqual(double.roles, ("lower", "body", "variable1", "variable2"))
        self.assertEqual([child.text for child in double.children[-2:]], ["x", "y"])
        triple = self.parser.parse(r"\iiint_V f(x)\,dx\,dy\,dz")
        self.assertEqual([child.text for child in triple.children[-3:]], ["x", "y", "z"])


class TestIntegralDifferentials(IntegralTestCase):
    def test_configured_differential_spellings(self):
        sources = (r"\int_0^1 f(x)dx", r"\int_0^1 f(x)\,dx",
                   r"\int_0^1 f(x)\, dx", r"\int_0^1 f(x)\mathrm{d}x",
                   r"\int_0^1 f(x)\mathrm{d} x",
                   r"\int_0^1 f(x)\operatorname{d}x")
        for source in sources:
            with self.subTest(source=source):
                tree = self.parser.parse(source)
                self.assertEqual(dict(zip(tree.roles, tree.children))["variable"].text, "x")

    def test_differential_detection_does_not_consume_arbitrary_d_expressions(self):
        for source in (r"\int_0^1 dx + y", r"\int_0^1 d*x", r"\int_0^1 distance(x)"):
            with self.subTest(source=source), self.assertRaises(ParseError):
                self.parser.parse(source)


class TestIntegralSemanticMatching(IntegralTestCase):
    def test_configured_candidate_matches_ast_roles_in_declared_order(self):
        candidate = configured_annotation_candidates(r"\int", self.config)[0]
        tree = self.parser.parse(r"\int_0^1 \frac{x}{2}\,dx")
        matches = match_ast(tree, candidate["classification"], self.registry)
        self.assertEqual(matches[0]["arguments"], ["0", "1", r"\frac{x}{2}", "x"])
        self.assertEqual(matches[0]["matched_expression"], tree.text)

    def test_required_bound_roles_control_annotation_readiness(self):
        candidate = configured_annotation_candidates("integral", self.config)[0]
        self.assertTrue(candidate["classification"]["annotation_ready"])
        indefinite = self.parser.parse(r"\int f(x)\,dx")
        self.assertEqual(match_ast(indefinite, candidate["classification"], self.registry), [])
        self.assertEqual(candidate["semantic_source"], "user configuration")

    def test_review_search_and_suggestion_use_configured_metadata(self):
        candidates = find_expression_candidates("integral", self.config, [])
        self.assertEqual([candidate["macro"] for candidate in candidates], ["fooIntegral"])
        candidate = candidates[0]
        tree = self.parser.parse(r"\int_0^1 f(x)\,dx")
        match = match_ast(tree, candidate["classification"], self.registry)[0]
        self.assertEqual(generate_classified_suggestion(match, candidate),
                         r"\fooIntegral{0}{1}{f(x)}{x}")


class TestIntegralApplication(IntegralTestCase):
    def test_integral_and_fraction_compose_through_generic_span_tree(self):
        source = r"\int_0^1 \frac{x}{2}\,dx"
        child_start = source.index(r"\frac")
        child_text = r"\frac{x}{2}"
        child = {"file": "paper.tex", "line": 1, "column": child_start + 1,
                 "source_start": child_start, "source_end": child_start + len(child_text),
                 "source_expression": child_text, "matched_expression": child_text,
                 "semantic_macro": "fooDivide", "semantic_suggestion": r"\fooDivide{x}{2}"}
        parent = {"file": "paper.tex", "line": 1, "column": 1,
                  "source_start": 0, "source_end": len(source),
                  "source_expression": source, "matched_expression": source,
                  "semantic_macro": "fooIntegral",
                  "semantic_suggestion": r"\fooIntegral{0}{1}{\frac{x}{2}}{x}"}
        plan = build_application_plan(source, [child, parent], "paper.tex")
        result, _, failures, count = render_application_plan(source, plan)
        self.assertEqual(result, r"\fooIntegral{0}{1}{\fooDivide{x}{2}}{x}")
        self.assertFalse(failures)
        self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
