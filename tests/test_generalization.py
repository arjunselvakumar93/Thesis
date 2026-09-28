import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evaluate_generalization import DEFAULT_FIXTURE, evaluate
from expression_document import find_ast_occurrences, parse_math_regions
from expression_metadata import build_expression_config
from expression_parser import ExpressionParser
from expression_registry import NotationRegistry
from review_workflow import find_expression_candidates
from review_scope import build_review_scopes, render_review_scope


class TestGeneralizationStress(unittest.TestCase):
    def test_fixture_is_isolated_and_every_region_is_reported_separately(self):
        result = evaluate(DEFAULT_FIXTURE)
        self.assertTrue(result["separate_from_gold_corpus"])
        self.assertEqual(result["total_expressions"], 52)
        self.assertEqual(len(result["expressions"]), 52)
        required = {"structural_parse_success", "exact_span_preserved",
                    "review_scope_available", "semantic_mapping_available",
                    "annotation_ready"}
        self.assertTrue(all(required <= item.keys() for item in result["expressions"]))

    def test_after_state_records_only_honest_remaining_failures(self):
        result = evaluate(DEFAULT_FIXTURE)
        failures = [item for item in result["expressions"]
                    if item["structural_status"] == "UNSUPPORTED"]
        self.assertEqual(failures, [])
        self.assertEqual(result["structurally_partial"], 0)

    def test_literal_relation_class_uses_registry_precedence_without_semantics(self):
        raw = json.loads((ROOT / "expression-config.example.json").read_text(
            encoding="utf-8-sig"))
        parser = ExpressionParser(registry=NotationRegistry.from_config(raw))
        for source, left, right in (
                ("x<y", "x", "y"),
                ("x>y", "x", "y"),
                ("x<y+1", "x", "y+1"),
                ("a+b<c*d", "a+b", "c*d"),
                ("f(x)<g(y)", "f(x)", "g(y)")):
            with self.subTest(source=source):
                tree = parser.parse(source)
                self.assertEqual(tree.kind, "BinaryOperator")
                self.assertEqual([child.text for child in tree.children], [left, right])
                self.assertFalse(tree.metadata.semantic_macro)

    def test_generic_environment_rows_cells_and_nested_expressions(self):
        raw = json.loads((ROOT / "expression-config.example.json").read_text(
            encoding="utf-8-sig"))
        parser = ExpressionParser(registry=NotationRegistry.from_config(raw))
        for name in ("pmatrix", "matrix", "bmatrix", "fooenv"):
            source = rf"\begin{{{name}}}a & b \\ c & d\end{{{name}}}"
            with self.subTest(name=name):
                tree = parser.parse(source)
                self.assertEqual((tree.kind, tree.value, tree.text),
                                 ("EnvironmentExpression", name, source))
                self.assertEqual([[cell.children[0].text for cell in row.children]
                                  for row in tree.children], [["a", "b"], ["c", "d"]])
                self.assertFalse(tree.metadata["semantic_mapping_available"])
        source = (r"\begin{pmatrix}a+b & \frac{x}{y} \\ "
                  r"f(z) & \omega_i\end{pmatrix}")
        tree = parser.parse(source)
        self.assertEqual([[cell.children[0].text for cell in row.children]
                          for row in tree.children],
                         [["a+b", r"\frac{x}{y}"], ["f(z)", r"\omega_i"]])

    def test_environment_with_unsafe_cell_is_preserved_opaquely(self):
        raw = json.loads((ROOT / "expression-config.example.json").read_text(
            encoding="utf-8-sig"))
        parser = ExpressionParser(registry=NotationRegistry.from_config(raw))
        source = r"\begin{fooenv}a & x@ y\end{fooenv}"
        tree = parser.parse(source)
        self.assertEqual((tree.kind, tree.text), ("EnvironmentExpression", source))
        self.assertTrue(tree.metadata["opaque"])
        self.assertEqual(tree.children[0].kind, "OpaqueEnvironmentBody")

    def test_inner_cell_expression_can_be_reviewed_without_environment_semantics(self):
        raw = json.loads((ROOT / "expression-config.example.json").read_text(
            encoding="utf-8-sig"))
        config = build_expression_config(raw, [])
        source = r"$\begin{fooenv}x+y & z\end{fooenv}$"
        regions = parse_math_regions(source, NotationRegistry.from_config(config))
        candidate = {"macro": "demoPlus", "name": "addition", "module": "test",
                     "repository": None, "args": "a",
                     "classification": {"pattern_type": "sequence", "separator": "+"}}
        matches = find_ast_occurrences(regions, candidate["classification"], config, candidate)
        scopes = build_review_scopes(matches, (candidate,))
        self.assertEqual(len(scopes), 1)
        self.assertEqual(scopes[0].matched_source, "x+y")
        self.assertIn("[x+y]", scopes[0].highlighted)
        self.assertNotIn("Internal AST", render_review_scope(scopes[0]))

    def test_layout_command_at_recursive_binder_body_entry_is_generic(self):
        raw = json.loads((ROOT / "expression-config.example.json").read_text(
            encoding="utf-8-sig"))
        parser = ExpressionParser(registry=NotationRegistry.from_config(raw))
        for source in (r"\forall x\, P(x)", r"\exists x\, Q(x)"):
            with self.subTest(source=source):
                tree = parser.parse(source)
                self.assertEqual(tree.kind, "BinderApplication")
                self.assertEqual(tree.text, source)

    def test_three_constructs_activate_through_metadata_only(self):
        raw = json.loads((ROOT / "expression-config.example.json").read_text(
            encoding="utf-8-sig"))
        before_parser = ExpressionParser(registry=NotationRegistry.from_config(raw))
        for token in (r"\cos", r"\log", r"\exp"):
            before = before_parser.parse(token + "(x)")
            self.assertNotEqual(before.kind, "FunctionApplication")
            raw["commands"][token] = {
                "arity": 0, "callable": True,
                "semantic_macro": token[1:] + "Semantic",
                "source": "test metadata"}
        configured = build_expression_config(raw, [])
        after_parser = ExpressionParser(registry=NotationRegistry.from_config(configured))
        for token in (r"\cos", r"\log", r"\exp"):
            with self.subTest(token=token):
                after = after_parser.parse(token + "(x)")
                self.assertEqual(after.kind, "FunctionApplication")
                self.assertTrue(find_expression_candidates(token, configured, []))


if __name__ == "__main__":
    unittest.main()
