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
from expression_document import (find_ast_occurrences, find_structural_occurrences,
                                 parse_papers)
from notation_catalog import build_expression_config
from expression_parser import ExpressionParser
from expression_parser import NotationRegistry
from review_scope import (build_review_scope, build_review_scopes, render_review_scope,
                          scope_alternatives)
from apply_annotations import build_application_plan, render_application_plan


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

    def test_color_is_applied_only_when_rendering_to_a_terminal(self):
        source = "  [x+y] + x+y  "
        start = source.rindex("x+y")
        match = {"full_math_region": source, "math_region_start": 100,
                 "source_start": 100 + start, "source_end": 100 + start + 3,
                 "source_expression": "x+y", "arguments": ["x", "y"],
                 "classification": self.plus["classification"]}
        with patch.object(sys.stdout, "isatty", return_value=True):
            scope = build_review_scope(match, (self.plus,))
            self.assertEqual(scope.highlighted, "[x+y] + [x+y]")
            self.assertIn("Highlighted:\n[x+y] + [\033[96mx+y\033[0m]",
                          render_review_scope(scope))
        output = io.StringIO()
        with redirect_stdout(output):
            print(render_review_scope(scope))
        self.assertNotIn("\033", output.getvalue())
        self.assertIn("Highlighted:\n[x+y] + [x+y]", output.getvalue())
        self.assertEqual(scope.highlighted, "[x+y] + [x+y]")
        self.assertEqual(scope.matched_source, "x+y")
        self.assertEqual((scope.matched_start, scope.matched_end),
                         (100 + start, 100 + start + 3))

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
        source = r"P(A_1)+\dots+P(A_k)=1"
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory, "paper.tex")
            paper.write_text("$" + source + "$", encoding="utf-8")
            matches = self.matches(paper, (self.plus,))
        scope = next(scope for scope in build_review_scopes(matches, (self.plus,))
                     if scope.matched_source == r"P(A_1)+\dots+P(A_k)")
        self.assertEqual(scope.arguments, (r"P(A_1)", r"\dots", r"P(A_k)"))
        self.assertEqual(scope.highlighted,
                         r"[P(A_1)+\dots+P(A_k)]=1")

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
            self.assertIn("Suggested scope:\np(x+y) * p(x-y)", text)
            self.assertNotIn("select AST", text.lower())
            self.assertNotIn("choose tree", text.lower())
            self.assertIn("Preview mode: no decisions were written.", text)

    def test_normal_review_still_uses_existing_decision_builder(self):
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory, "paper.tex")
            paper.write_text("$x+y$", encoding="utf-8")
            regions = parse_papers(paper, self.config)
            with redirect_stdout(io.StringIO()), patch.object(review_workflow, "scan_available_smglom", return_value=[]), \
                 patch.object(review_workflow, "choose_candidate", return_value=self.plus), \
                 patch.object(review_workflow, "ask_for_decision", return_value="accepted"), \
                 patch.object(review_workflow, "save_decisions", return_value=1) as save:
                self.assertEqual(review_workflow.run_generic_review(
                    "+", [self.plus], paper, self.config, regions), 1)
            decision = save.call_args.args[0][0]
            self.assertEqual(decision["semantic_macro"], "testPlus")
            self.assertEqual(decision["semantic_suggestion"], r"\testPlus{x,y}")
            self.assertEqual(decision["end"] - decision["start"], len("x+y"))

    def test_cli_forwards_preview_ast_and_scope_order_without_tree_selection(self):
        with redirect_stdout(io.StringIO()), patch.object(sys, "argv", ["review_workflow.py", "--preview", "--show-ast",
                                        "--scope-order", "outermost"]), \
             patch("builtins.input", return_value="+"), \
             patch.object(review_workflow, "find_generic_candidates", return_value=[self.plus]), \
             patch.object(review_workflow, "run_generic_review") as run:
            review_workflow.main()
        run.assert_called_once_with("+", [self.plus], preview=True, show_ast=True,
                                    scope_order="outermost")

    def test_changed_range_recomputes_target_candidates_and_application_offsets(self):
        source = r"A \Leftrightarrow B \land C"
        relation = candidate("biimpl", "iff", "binary_infix", r"\Leftrightarrow",
                             ("left", "right"))
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory, "paper.tex")
            paper.write_text("$" + source + "$", encoding="utf-8")
            regions = parse_papers(paper, self.config)
            matches = find_ast_occurrences(
                regions, relation["classification"], self.config, relation)
            scope = build_review_scopes(matches, (relation,))[0]
            alternatives = scope_alternatives(scope, self.config)
            self.assertEqual(alternatives[0]['node'].text, r"A \Leftrightarrow B")
            self.assertEqual(scope.precedence_confidence,
                             "configuration-dependent")
            self.assertEqual(len(alternatives), len({(item['start'], item['end']) for item in alternatives}))
            self.assertLessEqual(len(alternatives), 7)
            self.assertNotIn("A", [item['node'].text for item in alternatives])
            self.assertNotIn("B", [item['node'].text for item in alternatives])
            self.assertNotIn("C", [item['node'].text for item in alternatives])
            selected_index = next(index for index, item in enumerate(alternatives, 1)
                                  if item['node'].text == r"A \Leftrightarrow B")
            answers = iter(("r", str(selected_index)))
            output = io.StringIO()
            with redirect_stdout(output):
                changed, action = review_workflow.choose_review_range(
                    scope, self.config, regions, [], input_fn=lambda _: next(answers))
            self.assertIn("Alternative ranges:", output.getvalue())
            self.assertEqual(action, "changed")
            self.assertEqual((changed.matched_start, changed.matched_end),
                             (scope.matched_start, scope.matched_start + len(r"A \Leftrightarrow B")))
            self.assertEqual(changed.matched_source, r"A \Leftrightarrow B")
            self.assertEqual(changed.arguments, ("A", "B"))
            self.assertEqual(changed.highlighted, r"[A \Leftrightarrow B] \land C")
            self.assertEqual(changed.semantic_candidates[0]["macro"], "biimpl")
            self.assertEqual(changed.review_state, "SEMANTIC_UNCERTAIN")
            self.assertIn("rank", changed.semantic_candidates[0])
            self.assertTrue(any(item["signal"] == "exact_visible_notation"
                                for item in changed.semantic_candidates[0]["evidence"]))
            decision = review_workflow.build_review_decision(
                changed.match, changed.semantic_candidates[0], changed.classification, paper)
            self.assertEqual(decision["review_target_selection"], "user_corrected")
            self.assertEqual((decision["start"], decision["end"]),
                             (changed.matched_start, changed.matched_end))
            rendered, _, failures, count = render_application_plan(
                "$" + source + "$",
                build_application_plan("$" + source + "$", [decision], "paper.tex"))
        self.assertFalse(failures)
        self.assertEqual(count, 1)
        self.assertIn(r"\biimpl{A}{B} \land C", rendered)

    def test_signature_operator_prefers_exact_subexpression_and_offers_parent(self):
        source = r"f: A \to B"
        arrow = candidate("testArrow", "arrow", "binary_infix", r"\to",
                          ("domain", "codomain"))
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory, "paper.tex")
            paper.write_text("$" + source + "$", encoding="utf-8")
            regions = parse_papers(paper, self.config)
            matches = find_ast_occurrences(
                regions, arrow["classification"], self.config, arrow)
            scope = build_review_scopes(matches, (arrow,))[0]
            alternatives = scope_alternatives(scope, self.config)
        self.assertEqual(scope.matched_source, r"A \to B")
        self.assertEqual(scope.highlighted, r"f: [A \to B]")
        self.assertIn(source, [item['node'].text for item in alternatives])
        self.assertTrue(all(item['node'].text not in {"f", "A", "B"}
                            for item in alternatives))

    def test_review_state_separates_structure_semantics_and_uncertainty(self):
        structural_match = {
            "file": Path("paper.tex"), "line": 1, "column": 1,
            "source_start": 0, "source_end": 6, "math_region_start": 0,
            "source_expression": r"\omega", "full_math_region": r"\omega",
            "arguments": [], "pattern_type": "structural", "structural_node": True,
            "classification": {"pattern_type": "structural"},
            "proposal_eligibility": {"eligible": True},
        }
        structural = build_review_scope(structural_match, ())
        self.assertEqual(structural.review_state, "STRUCTURAL_ONLY")
        self.assertTrue(structural.structurally_ready)
        self.assertFalse(structural.semantic_candidates)
        self.assertEqual(structural.matched_source, r"\omega")
        self.assertFalse(structural.review_ready)
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory, "paper.tex")
            paper.write_text(r"$x+y$", encoding="utf-8")
            ready = build_review_scopes(self.matches(paper, (self.plus,)), (self.plus,))[0]
        self.assertEqual(ready.review_state, "ANNOTATION_READY")
        self.assertEqual(ready.semantic_candidates[0]["macro"], "testPlus")

    def test_range_alternatives_are_typed_targets_with_exact_spans(self):
        source = r"f: A \to B"
        arrow = candidate("testArrow", "arrow", "binary_infix", r"\to",
                          ("domain", "codomain"))
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory, "paper.tex")
            document = "$" + source + "$"
            paper.write_text(document, encoding="utf-8")
            regions = parse_papers(paper, self.config)
            scope = build_review_scopes(find_ast_occurrences(
                regions, arrow["classification"], self.config, arrow), (arrow,))[0]
            alternatives = scope_alternatives(scope, self.config)
        parent = next(item for item in alternatives if item['node'].text == source)
        self.assertEqual(document[parent['start']:parent['end']], parent['node'].text)

    def test_precedence_sensitive_automatic_ranges_are_complete(self):
        cases = (
            ("a+b*c", self.plus, "a+b*c", ("a", "b*c")),
            ("a+b*c", self.times, "b*c", ("b", "c")),
            ("(a+b)*c", self.plus, "a+b", ("a", "b")),
            ("(a+b)*c", self.times, "(a+b)*c", ("(a+b)", "c")),
        )
        for source, semantic, expected, arguments in cases:
            with self.subTest(source=source, macro=semantic["macro"]), \
                 tempfile.TemporaryDirectory() as directory:
                paper = Path(directory, "paper.tex")
                document = "$" + source + "$"
                paper.write_text(document, encoding="utf-8")
                scope = build_review_scopes(
                    self.matches(paper, (semantic,)), (semantic,))[0]
                self.assertEqual(scope.matched_source, expected)
                self.assertEqual(scope.arguments, arguments)
                self.assertEqual(document[scope.matched_start:scope.matched_end], expected)

    def test_reparsed_alternatives_do_not_cut_composite_operands(self):
        source = "p(x+y) * p(x-y)"
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory, "paper.tex")
            paper.write_text("$" + source + "$", encoding="utf-8")
            scope = build_review_scopes(
                self.matches(paper, (self.times,)), (self.times,))[0]
            alternatives = scope_alternatives(scope, self.config)
        texts = [item['node'].text for item in alternatives]
        self.assertEqual(texts, ["p(x+y)", "p(x-y)"])
        self.assertEqual(len(texts), len(set(texts)))
        self.assertNotIn("(x+y) * p(x-y)", texts)
        self.assertNotIn("p(x+y) * p", texts)

    def test_sequence_and_relation_ranges_follow_configured_precedence(self):
        cdot = candidate("testCdot", "multiplication", "sequence", r"\cdot")
        cases = ((r"a\cdot b\cdot c", cdot, r"a\cdot b\cdot c"),
                 (r"a+b\cdot c", cdot, r"b\cdot c"),
                 (r"a+b\cdot c", self.plus, r"a+b\cdot c"))
        for source, semantic, expected in cases:
            with self.subTest(source=source, query=semantic["macro"]), \
                 tempfile.TemporaryDirectory() as directory:
                paper = Path(directory, "paper.tex")
                paper.write_text("$" + source + "$", encoding="utf-8")
                scope = build_review_scopes(
                    self.matches(paper, (semantic,)), (semantic,))[0]
                self.assertEqual(scope.matched_source, expected)

    def test_symbol_scope_has_complete_script_and_relation_alternatives(self):
        source = r"\omega_i\in\Omega"
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory, "paper.tex")
            paper.write_text("$" + source + "$", encoding="utf-8")
            regions = parse_papers(paper, self.config)
            matches = find_structural_occurrences(regions, r"\omega", self.config)
            scope = next(item for item in build_review_scopes(
                matches, (), include_structural=True) if item.matched_source == r"\omega")
            alternatives = scope_alternatives(scope, self.config)
        self.assertEqual(scope.matched_source, r"\omega")
        self.assertEqual([item['node'].text for item in alternatives],
                         [r"\omega_i", source])
        self.assertFalse(scope.semantic_mapping_available)

    def test_corrected_parent_drops_stale_child_candidate(self):
        source = r"f: A \to B"
        arrow = candidate("testArrow", "arrow", "binary_infix", r"\to",
                          ("domain", "codomain"))
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory, "paper.tex")
            paper.write_text("$" + source + "$", encoding="utf-8")
            regions = parse_papers(paper, self.config)
            scope = build_review_scopes(find_ast_occurrences(
                regions, arrow["classification"], self.config, arrow), (arrow,))[0]
            parent = next(item for item in scope_alternatives(scope, self.config)
                          if item['node'].text == source)
            changed = review_workflow.recompute_review_scope(
                scope, parent, self.config, regions, records=[])
        self.assertEqual(changed.matched_source, source)
        self.assertEqual(changed.match["review_target_selection"], "user_corrected")
        self.assertFalse(any(item["macro"] == "testArrow"
                             for item in changed.semantic_candidates))
        self.assertEqual(changed.review_state, "STRUCTURAL_ONLY")

    def test_function_signature_arrow_scopes_exclude_declaration_context(self):
        arrow = candidate("testArrow", "arrow", "binary_infix", r"\to",
                          ("domain", "codomain"))
        cases = ((r"f: A \to B", r"A \to B"),
                 (r"f: A \to B \to C", r"A \to B \to C"),
                 (r"F: X \times Y \to Z", r"X \times Y \to Z"))
        for source, expected in cases:
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                paper = Path(directory, "paper.tex")
                paper.write_text("$" + source + "$", encoding="utf-8")
                scopes = build_review_scopes(
                    self.matches(paper, (arrow,)), (arrow,), "outermost")
                self.assertEqual(scopes[0].matched_source, expected)
                self.assertIn(source, [item['node'].text
                                       for item in scope_alternatives(scopes[0], self.config)])

    def test_script_alternatives_never_expose_markers_or_partial_groups(self):
        cases = (("x^2", ()), (r"x^{n+1}", ()), ("x_i^2", ()),
                 (r"P(\omega_i)^2", (r"P(\omega_i)",)))
        for source, expected in cases:
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                paper = Path(directory, "paper.tex")
                paper.write_text("$" + source + "$", encoding="utf-8")
                regions = parse_papers(paper, self.config)
                matches = find_structural_occurrences(regions, source, self.config)
                scope = build_review_scopes(matches, (), include_structural=True)[0]
                alternatives = scope_alternatives(scope, self.config)
                texts = tuple(item['node'].text for item in alternatives)
                self.assertEqual(texts, expected)
                self.assertTrue(all(text not in {"^", "_", "{n+1", "n+1}"}
                                    for text in texts))


if __name__ == "__main__":
    unittest.main()
