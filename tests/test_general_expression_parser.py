import io
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from expression_annotation import match_ast
from expression_document import parse_math_regions, parse_papers, find_ast_occurrences
from expression_metadata import build_expression_config
from expression_parser import ExpressionParser, ParseError
from expression_registry import NotationRegistry, Operator
from expression_tokenizer import tokenize
from inspect_expression import main as inspect_main
from parser_diagnostics import coverage_report
from notation_catalog import build_catalog, catalog_statistics, registry_from_catalog, search_catalog
from smglom_notation_extractor import scan_repository, scan_available_smglom, SMGLOM_REPOSITORIES
from review_workflow import find_generic_candidates, generate_classified_suggestion
import review_workflow
import apply_annotations
from decision_validator import validate_decisions


def configuration():
    return json.loads((ROOT / "expression-config.example.json").read_text(encoding="utf-8-sig"))


class TestGeneralExpressionParser(unittest.TestCase):
    def setUp(self):
        self.config = configuration()
        self.registry = NotationRegistry.from_config(self.config)
        self.parser = ExpressionParser(registry=self.registry)

    def test_precedence_changes_through_metadata_only(self):
        for power, root, nested in ((100, "@", "$"), (300, "$", "@")):
            parser = ExpressionParser([Operator("@", power, "left"), Operator("$", 200, "left")])
            tree = parser.parse("a@b$c")
            self.assertEqual(tree.value, root)
            self.assertEqual(tree.children[1 if power == 100 else 0].value, nested)

    def test_new_control_sequence_at_runtime(self):
        parser = ExpressionParser([Operator(r"\fooop", 20, "left")])
        tree = parser.parse(r"a\fooop b")
        self.assertEqual(tree.value, r"\fooop")
        self.assertEqual([child.text for child in tree.children], ["a", "b"])

    def test_new_control_sequence_loaded_from_json_without_parser_changes(self):
        data = json.loads((ROOT / "tests/fixtures/custom_operator.json").read_text(
            encoding="utf-8"
        ))
        registry = NotationRegistry.from_config(build_expression_config(data, []))
        tree = ExpressionParser(registry=registry).parse(r"a\customop b")
        self.assertEqual((tree.kind, tree.value), ("BinaryOperator", r"\customop"))
        self.assertEqual(tree.metadata.semantic_macro, "customOperation")

    def test_logic_and_set_metadata_use_the_same_parser(self):
        for source, outer, inner in ((r"A\wedge(B\vee C)", r"\wedge", r"\vee"),
                                      (r"A\cup(B\cap C)", r"\cup", r"\cap")):
            tree = self.parser.parse(source)
            self.assertEqual(tree.value, outer)
            self.assertEqual(tree.children[1].children[0].value, inner)

    def test_variants_preserve_original_and_canonical_notation(self):
        tree = self.parser.parse(r"A\land B")
        self.assertEqual((tree.value, tree.notation), (r"\wedge", r"\land"))
        self.assertEqual(tree.metadata.variants, (r"\land",))
        tree = self.parser.parse(r"a\neq b")
        self.assertEqual((tree.value, tree.notation), (r"\ne", r"\neq"))

    def test_scripts_preserve_roles_and_order(self):
        for expression in ("x^2", "x^{n+1}", "x_i", "x_{i+1}", "x_i^2", "x^2_i"):
            tree = self.parser.parse(expression)
            self.assertEqual(tree.kind, "ScriptedExpression")
            self.assertEqual(tree.children[0].text, "x")
            self.assertEqual(tree.text, expression)
        self.assertEqual(self.parser.parse("x_i^2").roles, ("base", "_", "^"))
        self.assertEqual(self.parser.parse("x^2_i").roles, ("base", "^", "_"))

    def test_unbraced_script_takes_one_tex_token(self):
        tree = self.parser.parse("x^23")
        self.assertEqual(tree.value, "juxtaposition")
        self.assertEqual(tree.children[0].children[1].text, "2")
        self.assertEqual(tree.children[1].text, "3")

    def test_mixed_expression_hierarchy(self):
        tree = self.parser.parse("a+b*c-d/e")
        self.assertEqual(tree.value, "-")
        self.assertEqual(tree.children[0].value, "+")
        self.assertEqual(tree.children[0].children[1].value, "*")
        self.assertEqual(tree.children[1].value, "/")

    def test_long_nested_commands_and_equality(self):
        tree = self.parser.parse(r"\frac{x_i^2+a*b}{\sqrt{c-d}} = y^{n+1}")
        self.assertEqual(tree.kind, "NaryOperator")
        numerator, denominator = tree.children[0].children
        self.assertEqual(numerator.children[0].value, "+")
        self.assertEqual(numerator.children[0].children[0].kind, "ScriptedExpression")
        self.assertEqual(denominator.children[0].value, r"\sqrt")
        self.assertEqual(denominator.children[0].children[0].children[0].value, "-")
        self.assertEqual(tree.children[1].children[1].children[0].value, "+")

    def test_semantic_arguments_are_structured_only_with_metadata(self):
        tree = self.parser.parse(r"\aplus{a,b*c}-\frac{x}{y}")
        semantic = tree.children[0]
        self.assertEqual(semantic.kind, "SemanticCommand")
        self.assertEqual([child.value for child in semantic.children[0].children], ["a", "*"])
        tree = self.parser.parse(r"a+\aplus{b,\aplus{c,d}}")
        self.assertEqual(tree.children[1].children[0].children[1].kind, "SemanticCommand")

    def test_semantic_expressions_remain_distinct(self):
        first = self.parser.parse(r"\aplus{a,b-c}")
        second = self.parser.parse(r"\aplus{a,b}-c")
        self.assertEqual(first.kind, "SemanticCommand")
        self.assertEqual(second.kind, "BinaryOperator")
        self.assertNotEqual(self.parser.parse("a+(b-c)"), self.parser.parse("(a+b)-c"))

    def test_function_arguments_and_style_identity(self):
        tree = self.parser.parse("g(a,b)")
        self.assertEqual(tree.kind, "FunctionApplication")
        self.assertEqual([child.text for child in tree.children[1].children], ["a", "b"])
        styles = [self.parser.parse(source) for source in (r"\mathcal{P}(X)", r"\mathfrak{P}(X)")]
        self.assertTrue(all(node.kind == "FunctionApplication" for node in styles))
        self.assertNotEqual(styles[0].children[0].value, styles[1].children[0].value)
        self.assertEqual(self.parser.parse("f(x^2+a*b)+g(y)").children[0].kind, "FunctionApplication")

    def test_implicit_product_is_explicit_policy(self):
        tree = self.parser.parse("(x-R)(x-S)")
        self.assertEqual(tree.value, "juxtaposition")
        self.assertEqual(tree.operator_start, -1)
        cfg = deepcopy(self.config)
        cfg["syntax"].pop("implicit_operator")
        with self.assertRaises(ParseError):
            ExpressionParser(registry=NotationRegistry.from_config(cfg)).parse("(x-R)(x-S)")

    def test_sized_and_regular_groups(self):
        for source in ("{a+b}", "[a+b]", "(a+b)", r"\{R,S\}",
                       r"\left(a+b\right)", r"\left[\left(a+b\right)\right]"):
            tree = self.parser.parse(source)
            self.assertEqual(tree.kind, "GroupedExpression")
            self.assertEqual(tree.text, source)
        self.assertEqual([child.text for child in self.parser.parse(r"\{R,S\}").children],
                         ["R", "S"])
        with self.assertRaises(ParseError):
            self.parser.parse(r"\left(a+b\right]")

    def test_layout_commands_are_configured_and_do_not_become_operands(self):
        tree = self.parser.parse("\\label{eq:test}\n a+b")
        self.assertEqual((tree.value, tree.text), ("+", "a+b"))
        tree = self.parser.parse(r"a\,b")
        self.assertEqual(tree.value, "juxtaposition")
        self.assertEqual(self.parser.parse("a+b,").text, "a+b")

    def test_nary_is_explicit_and_does_not_cross_groups(self):
        tree = self.parser.parse("R=S=T")
        self.assertEqual(tree.kind, "NaryOperator")
        self.assertEqual([child.text for child in tree.children], ["R", "S", "T"])
        self.assertEqual(len(self.parser.parse("(R=S)=T").children), 2)
        cfg = deepcopy(self.config)
        cfg["operators"][1]["associativity"] = "sequence"
        tree = ExpressionParser(registry=NotationRegistry.from_config(cfg)).parse("a-b-c")
        self.assertEqual(tree.kind, "NaryOperator")
        self.assertEqual(self.parser.parse("a-b-c").kind, "BinaryOperator")

    def test_nonassociative_mixed_chains_rejected(self):
        for source in (r"a\ne b\ne c", r"a=b\ne c", r"a\ne b=c"):
            with self.subTest(source=source), self.assertRaises(ParseError):
                self.parser.parse(source)

    def test_comments_escaped_specials_and_tex_parameters(self):
        tree = self.parser.parse("a+% ignored operator /\nb*c")
        self.assertEqual(tree.children[1].value, "*")
        self.assertIn("% ignored", tree.text)
        self.assertEqual(self.parser.parse(r"50\%").children[1].value, r"\%")
        self.assertEqual(self.parser.parse(r"\#").value, r"\#")
        with self.assertRaises(ParseError):
            self.parser.parse("#1")
        for symbol in ("%", "#", "^", "_"):
            with self.assertRaises(ValueError):
                NotationRegistry([Operator(symbol, 10, "left")])

    def test_tokens_preserve_spans_and_control_word_boundaries(self):
        source = r"12.5\neq x_i+\%"
        tokens = tokenize(source, self.registry)
        self.assertEqual(tokens[0].kind, "number")
        self.assertEqual(tokens[1].text, r"\neq")
        self.assertTrue(all(source[token.start:token.end] == token.text for token in tokens))
        with self.assertRaises(ParseError):
            self.parser.parse(r"a\neqUnknown b")

    def test_nodes_keep_source_spans_and_metadata(self):
        source = r"\frac{x_i^2+a*b}{\sqrt{c-d}}"
        root = self.parser.parse(source)
        for node in root.walk():
            self.assertEqual(node.text, source[node.start:node.end])
            self.assertIs(node.source, source)
            for child in node.children:
                self.assertLessEqual(node.start, child.start)
                self.assertLessEqual(child.end, node.end)
        self.assertEqual(root.roles, ("numerator", "denominator"))

    def test_unsupported_regions_retain_source_and_do_not_block_others(self):
        source = r"$a+b$ $\unknown{x}$ $a&b$ $a*b$"
        regions = parse_math_regions(source, self.registry)
        self.assertEqual([bool(region.error) for region in regions], [False, True, True, False])
        self.assertEqual(source[regions[1].start:regions[1].end], r"\unknown{x}")
        with self.assertRaises(ParseError):
            self.parser.parse(r"a\\b")
        with self.assertRaises(ParseError):
            self.parser.parse("x\u00b2")

    def test_synthetic_corpus_reports_explicit_advanced_grammar_gaps(self):
        source = (ROOT / "tests/fixtures/general_math_expressions.tex").read_text(encoding="utf-8")
        regions = parse_math_regions(source, self.registry)
        self.assertGreaterEqual(len(regions), 60)
        unsupported = [region for region in regions if region.error]
        self.assertEqual(unsupported, [])
        self.assertTrue(all(region.tree.text == source[region.start:region.end].strip()
                            for region in regions if region.tree))

        report = coverage_report(regions)
        self.assertEqual(report["structural_parser"]["unsupported"], 0)
        self.assertIn("notation_semantics", report)
        self.assertIn("annotation", report)

    def test_long_flat_expression_and_deep_failure(self):
        root = self.parser.parse("+".join(["a"] * 1200))
        self.assertEqual(sum(1 for _ in root.walk()), 2399)
        with self.assertRaises(ParseError):
            self.parser.parse("(" * 1200 + "a" + ")" * 1200)

    def test_alignment_environment_is_a_source_preserving_row_container(self):
        source = "before\n\\begin{align}\n1-z^2 &= 4 \\\\\nz^2 &= -3.\n\\end{align}\nafter"
        region = parse_math_regions(source, self.registry)[0]
        self.assertFalse(region.error)
        self.assertEqual(region.tree.kind, "Alignment")
        self.assertEqual([row.kind for row in region.tree.children], ["Row", "Row"])
        equations = [row.children[0] for row in region.tree.children]
        self.assertEqual([node.value for node in equations], ["=", "="])
        self.assertEqual([[child.text.strip() for child in node.children] for node in equations],
                         [["1-z^2", "4"], ["z^2", "-3"]])
        for node in region.tree.walk():
            self.assertEqual(node.text, region.tree.source[node.start:node.end])
        self.assertEqual(source[region.start + equations[1].start:region.start + equations[1].end],
                         "z^2 &= -3")

    def test_big_operators_share_one_metadata_driven_abstraction(self):
        for source, operator in ((r"\sum_{i=1}^{n} a_i", r"\sum"),
                                 (r"\prod_{i=1}^{n} a_i", r"\prod")):
            tree = self.parser.parse(source)
            self.assertEqual((tree.kind, tree.value), ("BigOperatorApplication", operator))
            self.assertEqual(tree.roles, ("lower", "upper", "body"))
            self.assertEqual([child.text for child in tree.children], ["{i=1}", "{n}", "a_i"])
        nested = self.parser.parse(r"\sum_{i=1}^{n} (a_i+b_i*c_i)")
        self.assertEqual(nested.children[-1].children[0].value, "+")

    def test_integrals_use_bound_expression_metadata(self):
        for source in (r"\int_0^1 f(x),dx",
                       r"\int_0^1 \frac{x_i^2+a*x}{\sqrt{1-x}},dx"):
            tree = self.parser.parse(source)
            self.assertEqual((tree.kind, tree.value), ("BoundExpression", r"\int"))
            self.assertEqual(tree.roles, ("lower", "upper", "body", "variable"))
            self.assertEqual(tree.children[-1].text, "x")
        self.assertEqual(self.parser.parse(r"\int_0^1 f(x)\,dx").children[-1].text, "x")

    def test_quantifiers_share_generic_binder_metadata(self):
        for source, operator in ((r"\forall x P(x)", r"\forall"),
                                 (r"\exists x P(x)", r"\exists")):
            tree = self.parser.parse(source)
            self.assertEqual((tree.kind, tree.value), ("BinderApplication", operator))
            self.assertEqual(tree.roles, ("variable", "body"))
            self.assertEqual([child.text for child in tree.children], ["x", "P(x)"])
        limit = self.parser.parse(r"\lim_{x=0} f(x)")
        self.assertEqual((limit.kind, limit.roles),
                         ("BigOperatorApplication", ("lower", "body")))

    def test_ast_matching_reuses_one_tree_for_different_notations(self):
        tree = self.parser.parse("a+b*c")
        with patch.object(ExpressionParser, "parse", side_effect=AssertionError("must not reparse")):
            plus = match_ast(tree, {"pattern_type": "sequence", "separator": "+"}, self.registry)
            times = match_ast(tree, {"pattern_type": "binary_infix", "operator": "*"}, self.registry)
        self.assertEqual(plus[0]["arguments"], ["a", "b*c"])
        self.assertEqual(times[0]["matched_expression"], "b*c")

    def test_command_matching_extracts_nested_arguments(self):
        tree = self.parser.parse(r"\frac{a+b*c}{d-e}+f(x)")
        matches = match_ast(tree, {"pattern_type": "command", "command": r"\frac", "argument_count": 2}, self.registry)
        self.assertEqual(matches[0]["arguments"], ["a+b*c", "d-e"])
        root = self.parser.parse(r"\sqrt[3]{x}")
        self.assertEqual(root.roles, ("optional", "radicand"))
        self.assertEqual(match_ast(root, {"pattern_type": "command", "command": r"\sqrt", "argument_count": 1}, self.registry), [])

    def test_inspection_cli(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(inspect_main(["a+b*c"]), 0)
        self.assertIn("BinaryOperator(*)", output.getvalue())
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(inspect_main([r"\unknown{x}"]), 1)
        self.assertIn("UnsupportedExpression", output.getvalue())


class TestRegistryIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = scan_repository("arithmetics", SMGLOM_REPOSITORIES["arithmetics"])
        cls.records += scan_repository("mv", SMGLOM_REPOSITORIES["mv"])

    def test_real_smglom_precedence_and_command_imports(self):
        cfg = build_expression_config({"smglom_precedence_direction": "lower", "smglom": [
            {"macro": "aplus", "module": "arithmetics", "visible": "+", "associativity": "left"},
            {"macro": "atimes", "module": "arithmetics", "visible": r"\cdot", "associativity": "left"},
            {"macro": "adivide", "module": "arithmetics", "visible": r"\frac"},
        ]}, self.records)
        parser = ExpressionParser(registry=NotationRegistry.from_config(cfg))
        tree = parser.parse(r"a+b\cdot c")
        self.assertEqual(tree.metadata.precedence, -500)
        self.assertEqual(tree.children[1].metadata.precedence, -400)
        self.assertIn("SMGloM", tree.metadata.source)
        self.assertEqual(parser.parse(r"\frac{a+b}{c}").kind, "Command")
        self.assertEqual(parser.parse(r"\aplus{a,b\cdot c}").kind, "SemanticCommand")

    def test_catalogue_reports_structural_reasons_and_round_trips_bound_metadata(self):
        base_entries = build_catalog(self.records)
        statistics = catalog_statistics(base_entries)
        self.assertEqual(sum(statistics["unsupported_by_reason"].values()),
                         statistics["grammar_unsupported"])
        self.assertEqual(statistics["supported_by_adapter"].get("BinderAdapter"), 33)

        configured = build_catalog([], self.config if hasattr(self, "config") else configuration())
        compiled, diagnostics = registry_from_catalog(configured)
        self.assertFalse([item for item in diagnostics if item.get("reason") == "unsupported notation structure"
                          and item.get("macro") in {"sum", "prod", "int"}])
        registry = NotationRegistry.from_config(compiled)
        self.assertIn(r"\sum", registry.bound_constructs)

    def test_catalogue_search_uses_classified_visible_notation_before_raw_text(self):
        entries = build_catalog(scan_available_smglom(quiet=True))
        self.assertEqual(search_catalog(entries, "*"), [])

        equality = search_catalog(entries, "=")
        self.assertTrue(equality)
        self.assertTrue(all(item["visible"] == "=" for item in equality))
        self.assertTrue(all(item["match_type"] == "exact visible notation" for item in equality))

        for notation in (r"\times", r"\cdot"):
            matches = search_catalog(entries, notation)
            self.assertTrue(matches)
            self.assertTrue(all(item["visible"] == notation for item in matches))
            self.assertTrue(all(item["classification"]["pattern_type"] == "sequence"
                                for item in matches))

        sums = search_catalog(entries, r"\sum")
        self.assertTrue(sums)
        self.assertTrue(all(item["visible"] == r"\sum" for item in sums))
        self.assertTrue(all(item["classification"]["pattern_type"] == "binder" for item in sums))

        not_equal = search_catalog(entries, "not equal")
        self.assertTrue(not_equal)
        self.assertTrue(all(item["match_type"] in {"exact meaning/name", "meaning/name search"}
                            for item in not_equal))

    def test_missing_associativity_and_ambiguous_import_fail(self):
        for selector in ({"macro": "atimes", "module": "arithmetics", "associativity": "left"},
                         {"macro": "aplus", "module": "arithmetics", "visible": "+"}):
            with self.assertRaises(ValueError):
                build_expression_config({"smglom_precedence_direction": "lower", "smglom": [selector]}, self.records)

    def test_parse_before_selection_and_reuse_in_review(self):
        candidate = next(item for item in find_generic_candidates("+", self.records) if item["macro"] == "aplus")
        cfg = configuration()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / "paper.tex"
            paper.write_text("$a+b*c$", encoding="utf-8")
            regions = parse_papers(root, cfg)
            def choose(_):
                self.assertIsNotNone(regions[0].tree)
                return candidate
            with patch.object(review_workflow, "choose_candidate", side_effect=choose), \
                 patch.object(review_workflow, "ask_for_decision", return_value="accepted"), \
                 patch.object(review_workflow, "save_decisions", return_value=1) as save, \
                 patch.object(ExpressionParser, "parse", side_effect=AssertionError("AST should be reused")):
                review_workflow.run_generic_review("+", [candidate], root, cfg, regions)
            self.assertEqual(save.call_args.args[0][0]["semantic_suggestion"], r"\aplus{a,b*c}")
            self.assertIn("expression_config", save.call_args.args[0][0])

    def test_user_operator_semantic_mapping_survives_validation(self):
        cfg = build_expression_config({"operators": [{"token": r"\fooop", "precedence": 10,
            "associativity": "left", "semantic_macro": "customOperation"}]})
        candidates = review_workflow.find_expression_candidates(r"\fooop", cfg, [])
        self.assertEqual(len(candidates), 1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "paper.tex").write_text(r"$a\fooop b$", encoding="utf-8")
            with patch.object(review_workflow, "choose_candidate", return_value=candidates[0]), \
                 patch.object(review_workflow, "ask_for_decision", return_value="accepted"), \
                 patch.object(review_workflow, "save_decisions", return_value=1) as save:
                review_workflow.run_generic_review(r"\fooop", candidates, root, cfg)
            decisions = save.call_args.args[0]
            self.assertEqual(decisions[0]["semantic_suggestion"], r"\customOperation{a}{b}")
            self.assertEqual(len(validate_decisions(decisions, [], root)["valid"]), 1)
            (root / "paper.tex").write_text(r"$a\fooop c$", encoding="utf-8")
            self.assertEqual(len(validate_decisions(decisions, [], root)["stale"]), 1)

    def test_multiline_application_uses_original_spans(self):
        cfg = configuration()
        rule = {"pattern_type": "sequence", "separator": "+"}
        candidate = next(item for item in find_generic_candidates("+", self.records) if item["macro"] == "aplus")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            papers, output = root / "papers", root / "output"
            papers.mkdir()
            original = "$a+% comment\n b*c$\n"
            (papers / "paper.tex").write_text(original, encoding="utf-8")
            matches = find_ast_occurrences(parse_papers(papers, cfg), rule, cfg)
            match = matches[0]
            decision = dict(match, file="paper.tex", semantic_suggestion=generate_classified_suggestion(match, candidate))
            self.assertNotIn("%", decision["semantic_suggestion"])
            with patch.object(apply_annotations, "PAPERS_DIR", papers), patch.object(apply_annotations, "OUTPUT_DIR", output):
                apply_annotations.prepare_output_folder()
                self.assertEqual(apply_annotations.apply_annotations([decision]), 1)
            self.assertIn(r"\aplus{a,b*c}", (output / "paper.tex").read_text(encoding="utf-8"))
            self.assertEqual((papers / "paper.tex").read_text(encoding="utf-8"), original)

    def test_overlapping_and_stale_ast_scopes_are_not_applied(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / "paper.tex"
            original = "$a-b-c$"
            paper.write_text(original, encoding="utf-8")
            decisions = [dict(file="paper.tex", source_start=1, source_end=end,
                              source_expression=expression, matched_expression=expression,
                              semantic_suggestion=r"\changed{x}")
                         for end, expression in ((4, "a-b"), (6, "a-b-c"))]
            with patch.object(apply_annotations, "OUTPUT_DIR", root):
                self.assertEqual(apply_annotations.apply_annotations(decisions), 0)
                decisions[0]["source_expression"] = "a-z"
                self.assertEqual(apply_annotations.apply_annotations(decisions[:1]), 0)
            self.assertEqual(paper.read_text(encoding="utf-8"), original)

    def test_main_parses_before_requesting_annotation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "paper.tex").write_text("$a+b*c$", encoding="utf-8")
            completed = []
            def parse_first(*args):
                result = parse_papers(*args)
                completed.extend(result)
                return result
            def prompt(_):
                self.assertTrue(completed)
                return ""
            with patch.object(review_workflow, "PAPERS_DIR", root), \
                 patch("expression_document.parse_papers", side_effect=parse_first), \
                 patch("builtins.input", side_effect=prompt), \
                 patch.object(sys, "argv", ["review_workflow.py", "--expression-config", str(ROOT / "expression-config.example.json")]):
                review_workflow.main()

    def test_tex_structure_does_not_trigger_semantic_lookup(self):
        with patch("builtins.input", return_value="^"), \
             patch.object(sys, "argv", ["review_workflow.py"]), \
             patch.object(review_workflow, "find_generic_candidates") as lookup:
            review_workflow.main()
        lookup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
