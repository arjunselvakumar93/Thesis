import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import review_workflow
from expression_document import find_ast_occurrences, parse_papers
from expression_metadata import build_expression_config
from expression_parser import ExpressionParser
from expression_registry import NotationRegistry
from review_scope import build_review_scope, build_review_scopes, render_review_scope


ROOT = Path(__file__).resolve().parents[1]


def candidate(macro, name, pattern, visible, roles=()):
    key = {"sequence": "separator", "binary_infix": "operator",
           "command": "command"}[pattern]
    rule = {"pattern_type": pattern, key: visible, "argument_roles": list(roles)}
    if pattern in {"command", "binary_infix"}:
        rule["argument_count"] = len(roles)
    return {"macro": macro, "name": name, "module": "test-review",
            "repository": "test", "args": "a" if pattern == "sequence" else len(roles),
            "classification": rule, "evidence": [{"signal": "test_metadata"}]}


class TestReviewScope(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = json.loads((ROOT / "expression-config.example.json").read_text(encoding="utf-8-sig"))
        # The fixture explicitly declares p as a structural function symbol;
        # this assigns no semantic meaning to p.
        raw["syntax"]["functions"].append("p")
        cls.config = build_expression_config(raw)
        cls.plus = candidate("testPlus", "addition", "sequence", "+")
        cls.minus = candidate("testMinus", "subtraction", "sequence", "-")
        cls.times = candidate("testTimes", "multiplication", "binary_infix", "*",
                              ("left", "right"))

    def matches(self, paper, candidates):
        regions = parse_papers(paper, self.config)
        return [match for item in candidates for match in find_ast_occurrences(
            regions, item["classification"], self.config, item)]

    def test_nested_function_expression_has_internal_ast_and_review_scopes(self):
        source = "p(x+y) * p(x-y)"
        tree = ExpressionParser(registry=NotationRegistry.from_config(self.config)).parse(source)
        self.assertEqual((tree.kind, tree.value), ("BinaryOperator", "*"))
        self.assertEqual([child.kind for child in tree.children],
                         ["FunctionApplication", "FunctionApplication"])
        self.assertEqual([child.children[1].children[0].value for child in tree.children],
                         ["+", "-"])
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory, "paper.tex")
            paper.write_text("$" + source + "$", encoding="utf-8")
            matches = self.matches(paper, (self.plus, self.minus, self.times))
        scopes = build_review_scopes(matches, (self.plus, self.minus, self.times))
        self.assertEqual([scope.matched_source for scope in scopes],
                         ["x+y", "x-y", source])
        self.assertEqual([scope.highlighted for scope in scopes], [
            "p([x+y]) * p(x-y)", "p(x+y) * p([x-y])", "[p(x+y) * p(x-y)]"])
        self.assertEqual([scope.simple_structure for scope in scopes],
                         ["addition", "subtraction", "multiplication"])
        self.assertEqual(scopes[-1].argument_roles, ("left", "right"))
        self.assertEqual(scopes[-1].arguments, ("p(x+y)", "p(x-y)"))

    def test_outermost_order_is_optional_and_source_order_breaks_sibling_ties(self):
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory, "paper.tex")
            paper.write_text("$p(x+y) * p(x-y)$", encoding="utf-8")
            matches = self.matches(paper, (self.plus, self.minus, self.times))
        scopes = build_review_scopes(matches, (self.plus, self.minus, self.times), "outermost")
        self.assertEqual(scopes[0].matched_source, "p(x+y) * p(x-y)")
        self.assertEqual([scope.matched_source for scope in scopes[1:]], ["x+y", "x-y"])

    def test_ellipsis_scope_preserves_left_right_source_and_complete_arguments(self):
        source = r"P\left(A_1\cup\dots\cup A_k\right)=P(A_1)+\dots+P(A_k)"
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory, "paper.tex")
            paper.write_text("$" + source + "$", encoding="utf-8")
            matches = self.matches(paper, (self.plus,))
        scope = next(scope for scope in build_review_scopes(matches, (self.plus,))
                     if scope.matched_source == r"P(A_1)+\dots+P(A_k)")
        self.assertEqual(scope.arguments, (r"P(A_1)", r"\dots", r"P(A_k)"))
        self.assertEqual(scope.highlighted,
                         r"P\left(A_1\cup\dots\cup A_k\right)=[P(A_1)+\dots+P(A_k)]")

    def test_presentation_wrapper_projects_only_nested_semantic_scope(self):
        full = r"\boldsymbol{x^2}"
        start = full.index("x^2")
        match = {"file": Path("paper.tex"), "line": 1, "column": start + 1,
                 "math_region_start": 100, "source_start": 100 + start,
                 "source_end": 100 + start + 3, "full_math_region": full,
                 "source_expression": "x^2", "matched_expression": "x^2",
                 "pattern_type": "scripted_template", "arguments": ["x", "2"],
                 "argument_roles": ["base", "exponent"], "ast_depth": 2,
                 "ast_debug": "ScriptedExpression(scripts)",
                 "classification": {"pattern_type": "scripted_template",
                                    "grammar_adapter": "ScriptedTemplateAdapter"},
                 "proposal_eligibility": {"eligible": True}}
        power = {"macro": "testPower", "name": "power", "module": "test-review",
                 "classification": dict(match["classification"]),
                 "evidence": [{"signal": "test_metadata"}]}
        scope = build_review_scope(match, (power,))
        self.assertEqual(scope.matched_source, "x^2")
        self.assertEqual(scope.highlighted, r"\boldsymbol{[x^2]}")
        self.assertNotIn("AST", render_review_scope(scope))
        self.assertIn("Internal AST (debug):", render_review_scope(scope, show_ast=True))

    def test_preview_prints_cards_without_ast_prompt_or_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory, "paper.tex")
            paper.write_text("$p(x+y) * p(x-y)$", encoding="utf-8")
            regions = parse_papers(paper, self.config)
            output = io.StringIO()
            with patch.object(review_workflow, "scan_available_smglom", return_value=[]), \
                 patch.object(review_workflow, "save_decisions") as save, \
                 redirect_stdout(output):
                count = review_workflow.run_generic_review(
                    "*", [self.times], paper, self.config, regions, preview=True)
            self.assertEqual(count, 1)
            save.assert_not_called()
            text = output.getvalue()
            self.assertIn("Original formula:\np(x+y) * p(x-y)", text)
            self.assertIn("Matched scope:\np(x+y) * p(x-y)", text)
            self.assertNotIn("select AST", text.lower())
            self.assertNotIn("choose tree", text.lower())
            self.assertIn("Preview mode: no decisions were written.", text)

    def test_normal_review_still_uses_existing_decision_builder(self):
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory, "paper.tex")
            paper.write_text("$x+y$", encoding="utf-8")
            regions = parse_papers(paper, self.config)
            with patch.object(review_workflow, "scan_available_smglom", return_value=[]), \
                 patch.object(review_workflow, "choose_candidate", return_value=self.plus), \
                 patch.object(review_workflow, "ask_for_decision", return_value="accepted"), \
                 patch.object(review_workflow, "save_decisions", return_value=1) as save:
                self.assertEqual(review_workflow.run_generic_review(
                    "+", [self.plus], paper, self.config, regions), 1)
            decision = save.call_args.args[0][0]
            self.assertEqual(decision["semantic_macro"], "testPlus")
            self.assertEqual(decision["semantic_suggestion"], r"\testPlus{x,y}")
            self.assertEqual(decision["source_expression"], "x+y")

    def test_cli_forwards_preview_ast_and_scope_order_without_tree_selection(self):
        with patch.object(sys, "argv", ["review_workflow.py", "--preview", "--show-ast",
                                        "--scope-order", "outermost"]), \
             patch("builtins.input", return_value="+"), \
             patch.object(review_workflow, "find_generic_candidates", return_value=[self.plus]), \
             patch.object(review_workflow, "run_generic_review") as run:
            review_workflow.main()
        run.assert_called_once_with("+", [self.plus], preview=True, show_ast=True,
                                    scope_order="outermost")


if __name__ == "__main__":
    unittest.main()
