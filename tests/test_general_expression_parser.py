import hashlib
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
from notation_catalog import build_expression_config
from expression_parser import ExpressionParser, ParseError
from expression_parser import NotationRegistry, Operator
from expression_parser import tokenize
from inspect_expression import main as inspect_main
from parser_diagnostics import coverage_report
from notation_catalog import build_catalog, catalog_statistics, registry_from_catalog, search_catalog
from smglom_notation_extractor import scan_repository, scan_available_smglom, SMGLOM_REPOSITORIES
from review_workflow import (find_expression_candidates, find_generic_candidates,
                             generate_classified_suggestion)
import review_workflow
import apply_annotations
from apply_annotations import validate_decisions


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
        data = json.loads((ROOT / "tests/fixtures/configs/test_custom_operator.json").read_text(
            encoding="utf-8"
        ))
        registry = NotationRegistry.from_config(build_expression_config(data, []))
        tree = ExpressionParser(registry=registry).parse(r"a\customop b")
        self.assertEqual((tree.kind, tree.value), ("BinaryOperator", r"\customop"))
        self.assertEqual(tree.metadata.semantic_macro, "customOperation")

    def test_unconfigured_bare_commands_are_semantics_free_symbol_atoms(self):
        for source in (r"\omega", r"\beta", r"\gamma", r"\theta", r"\lambda",
                       r"\mu", r"\phi", r"\unknownsymbol"):
            with self.subTest(source=source):
                tree = self.parser.parse(source)
                self.assertEqual((tree.kind, tree.value, tree.text),
                                 ("SymbolAtom", source, source))
                self.assertFalse(tree.metadata["semantic_mapping_available"])

    def test_symbol_commands_compose_with_scripts_relations_and_arithmetic(self):
        for source in (r"\omega_i", r"\omega^2", r"\omega_i^2"):
            with self.subTest(source=source):
                tree = self.parser.parse(source)
                self.assertEqual(tree.kind, "ScriptedExpression")
                self.assertEqual(tree.children[0].kind, "SymbolAtom")
                self.assertEqual(tree.text, source)
        relation = self.parser.parse(r"\omega_i\in A")
        self.assertEqual((relation.kind, relation.value, relation.text),
                         ("BinaryOperator", r"\in", r"\omega_i\in A"))
        self.assertEqual(relation.children[0].text, r"\omega_i")
        for source in (r"\alpha+\beta", r"\theta-\lambda",
                       r"\alpha\beta", r"\alpha+\beta*\gamma"):
            self.assertEqual(self.parser.parse(source).text, source)

    def test_unknown_command_groups_use_an_explicit_opaque_fallback(self):
        cases = {
            r"\foo{x}": ("arg1",),
            r"\foo{x}{y}": ("arg1", "arg2"),
            r"\foo[x]": ("optional1",),
            r"\foo[x]{y}": ("optional1", "arg1"),
        }
        for source, roles in cases.items():
            with self.subTest(source=source):
                tree = self.parser.parse(source)
                self.assertEqual((tree.kind, tree.value, tree.text),
                                 ("OpaqueCommandApplication", r"\foo", source))
                self.assertEqual(tree.roles, roles)
                self.assertTrue(tree.metadata["opaque"])
                self.assertFalse(tree.metadata["semantic_mapping_available"])
                self.assertFalse(tree.metadata["argument_roles_known"])

    def test_opaque_command_does_not_overconsume_and_composes_as_an_operand(self):
        addition = self.parser.parse(r"\foo{x}+y")
        self.assertEqual((addition.kind, addition.value), ("BinaryOperator", "+"))
        self.assertEqual([child.text for child in addition.children], [r"\foo{x}", "y"])
        product = self.parser.parse(r"a*\foo{x}")
        self.assertEqual([child.text for child in product.children], ["a", r"\foo{x}"])
        for source in (r"\foo {x}", r"\foo{x}", r"\foo  {x}"):
            self.assertEqual(self.parser.parse(source).text, source)

    def test_nested_opaque_command_groups_remain_structured(self):
        for source in (r"\foo{x+y}", r"\foo{\frac{a}{b}}",
                       r"\foo{\sum_{i=1}^{n} a_i}"):
            with self.subTest(source=source):
                tree = self.parser.parse(source)
                self.assertEqual(tree.kind, "OpaqueCommandApplication")
                self.assertEqual(tree.text, source)
                self.assertTrue(tree.children[0].children)
        self.assertEqual(self.parser.parse(r"\foo{x}+g(y)").children[0].text, r"\foo{x}")
        function = self.parser.parse(r"f(\foo{x})")
        self.assertEqual(function.kind, "FunctionApplication")
        self.assertTrue(any(node.kind == "OpaqueCommandApplication" for node in function.walk()))

    def test_config_metadata_replaces_opaque_fallback_without_parser_changes(self):
        before = self.parser.parse(r"\foo{x}")
        self.assertEqual(before.kind, "OpaqueCommandApplication")
        raw = configuration()
        raw["commands"][r"\foo"] = {
            "arity": 1, "semantic_macro": "fooSemantic",
            "argument_types": ["expression"], "source": "test metadata"}
        configured = build_expression_config(raw, [])
        after = ExpressionParser(registry=NotationRegistry.from_config(configured)).parse(r"\foo{x}")
        self.assertEqual(after.kind, "Command")
        self.assertTrue(find_expression_candidates(r"\foo", configured, []))

    def test_complex_opaque_command_composes_with_known_grammar(self):
        source = (r"\frac{\foo{x+y}+\sum_{\omega_i\in A}P(\omega_i)}"
                  r"{\sqrt{\alpha^2+\beta^2}}")
        tree = self.parser.parse(source)
        self.assertEqual((tree.kind, tree.value, tree.text), ("Command", r"\frac", source))
        kinds = {node.kind for node in tree.walk()}
        self.assertTrue({"OpaqueCommandApplication", "BigOperatorApplication",
                         "FunctionApplication", "ScriptedExpression"} <= kinds)

    def test_symbol_commands_work_in_functions_scripts_sums_and_integrals(self):
        for source in (r"\pi r^2", r"f(\theta)", r"A_{\lambda}",
                       r"\sum_{i=1}^{n} \alpha_i",
                       r"\int_0^1 \omega x\,dx"):
            with self.subTest(source=source):
                self.assertEqual(self.parser.parse(source).text, source)

    def test_unconfigured_symbol_in_sum_preserves_complete_generic_structure(self):
        source = r"\sum_{\omega_i\in A} P(\omega_i)"
        tree = self.parser.parse(source)
        self.assertEqual((tree.kind, tree.value, tree.text),
                         ("BigOperatorApplication", r"\sum", source))
        self.assertEqual(tree.roles, ("lower", "body"))
        lower, body = tree.children
        self.assertEqual((lower.kind, lower.value, lower.text),
                         ("GroupedExpression", "{", r"{\omega_i\in A}"))
        relation = lower.children[0]
        self.assertEqual((relation.kind, relation.value, relation.text),
                         ("BinaryOperator", r"\in", r"\omega_i\in A"))
        self.assertEqual(relation.children[0].text, r"\omega_i")
        self.assertEqual(body.kind, "FunctionApplication")
        self.assertEqual(body.text, r"P(\omega_i)")

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
        for expression in ("x^2", "x^{n+1}", "x_i", "x_{i+1}", "x_i^2", "x^2_i",
                           "f(x)^2", "(a+b)^n", r"P(\omega_i)^2"):
            tree = self.parser.parse(expression)
            self.assertEqual(tree.kind, "ScriptedExpression")
            self.assertEqual(tree.text, expression)
            self.assertEqual(tree.text, expression[tree.start:tree.end])
        self.assertEqual(self.parser.parse("x_i^2").roles, ("base", "_", "^"))
        self.assertEqual(self.parser.parse("x^2_i").roles, ("base", "^", "_"))
        self.assertEqual(self.parser.parse("x^{n+1}").children[1].children[0].value, "+")
        semantic = self.parser.parse(r"\aplus{a,b}^2")
        self.assertEqual((semantic.kind, semantic.children[0].kind),
                         ("ScriptedExpression", "SemanticCommand"))

    def test_malformed_scripts_fail_without_partial_ast(self):
        for expression in ("x^", "x_", "x^{", "x_{i", "x^^2", "x__i"):
            with self.subTest(expression=expression), self.assertRaises(ParseError):
                self.parser.parse(expression)

    def test_configured_variable_arity_operator_and_mixed_precedence(self):
        sequence = self.parser.parse(r"a\cdot b\cdot c")
        self.assertEqual((sequence.kind, sequence.value), ("NaryOperator", r"\cdot"))
        self.assertEqual([child.text for child in sequence.children], ["a", "b", "c"])
        self.assertEqual(sequence.metadata.arity, "variable")
        self.assertEqual(sequence.metadata.precedence_source, "structural configuration")
        self.assertEqual(sequence.text, r"a\cdot b\cdot c")
        mixed = self.parser.parse(r"a+b\cdot c")
        self.assertEqual((mixed.value, mixed.children[1].value), ("+", r"\cdot"))
        reverse = self.parser.parse(r"a\cdot b+c")
        self.assertEqual((reverse.value, reverse.children[0].value), ("+", r"\cdot"))
        self.assertEqual(self.parser.parse(r"a\cdot(b+c)").children[1].children[0].value, "+")

    def test_configured_biconditional_precedence_and_uncertain_chain(self):
        tree = self.parser.parse(r"A \Leftrightarrow B \land C")
        self.assertEqual((tree.value, tree.children[1].value),
                         (r"\Leftrightarrow", r"\wedge"))
        reverse = self.parser.parse(r"A \land B \Leftrightarrow C")
        self.assertEqual((reverse.value, reverse.children[0].value),
                         (r"\Leftrightarrow", r"\wedge"))
        chain = self.parser.parse(r"A \Leftrightarrow B \Leftrightarrow C")
        self.assertEqual((chain.kind, chain.text),
                         ("UncertainOperatorChain", r"A \Leftrightarrow B \Leftrightarrow C"))
        self.assertEqual(tree.metadata.precedence_source,
                         "structural policy; semantically unverified")
        self.assertTrue(tree.metadata.uncertain_chain)
        self.assertEqual(self.parser.parse(r"A \to B \Leftrightarrow C").children[0].value,
                         r"\to")
        self.assertEqual(self.parser.parse(r"A \Leftrightarrow B \to C").children[1].value,
                         r"\to")

    def test_configured_function_signature_is_structural(self):
        for source in (r"f: A \to B", r"f:A\to B", r"f : A \to B", r"g: X \to Y",
                       r"T: V \to W", r"F: X \times Y \to Z"):
            with self.subTest(source=source):
                tree = self.parser.parse(source)
                self.assertEqual((tree.value, tree.children[1].value, tree.text),
                                 (":", r"\to", source))
        chain = self.parser.parse(r"f: A \to B \to C")
        self.assertEqual(chain.children[1].children[1].value, r"\to")
        product_signature = self.parser.parse(r"F: X \times Y \to Z")
        product = product_signature.children[1].children[0]
        self.assertEqual((product.value, product.notation, product.text),
                         ("*", r"\times", r"X \times Y"))

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

    def test_configured_symbolic_calls_preserve_complete_spans(self):
        sources = (
            "f(x)", "g(a+b)", "P(A_1)", "F(x_i)", r"h(\frac{a}{b})",
            "f(g(x))", "f(g(x)+h(y))", r"f(\frac{x}{2})", "f(x_i^2)",
            "f(a+b*c)", r"P\left(A_1\cup A_2\right)",
        )
        self.assertTrue(set("fghPF") <= self.registry.functions)
        for source in sources:
            with self.subTest(source=source):
                tree = self.parser.parse(source)
                self.assertEqual(tree.kind, "FunctionApplication")
                self.assertEqual(tree.text, source)
                self.assertEqual((tree.start, tree.end), (0, len(source)))

    def test_addition_consumes_complete_function_application_children(self):
        rule = {"pattern_type": "sequence", "separator": "+", "fixity": "infix",
                "arity": "variable"}
        cases = {
            "f(x)+g(y)": ["f(x)", "g(y)"],
            "f(x)+g(y)+h(z)": ["f(x)", "g(y)", "h(z)"],
            "f(x*y)+g(a+b)": ["f(x*y)", "g(a+b)"],
            "F(x_1)+\\dots+F(x_n)": ["F(x_1)", r"\dots", "F(x_n)"],
            "a+f(x)": ["a", "f(x)"],
            "f(x)+a": ["f(x)", "a"],
            "(a+b)+f(x)": ["(a+b)", "f(x)"],
            "f(x)+(a+b)": ["f(x)", "(a+b)"],
            "P(A_1)+\\dots+P(A_k)": ["P(A_1)", r"\dots", "P(A_k)"],
        }
        for source, arguments in cases.items():
            with self.subTest(source=source):
                tree = self.parser.parse(source)
                matches = match_ast(tree, rule, self.registry)
                root = next(match for match in matches
                            if (match["start"], match["end"]) == (0, len(source)))
                self.assertEqual(root["matched_expression"], source)
                self.assertEqual(root["arguments"], arguments)

    def test_real_probability_shape_and_sized_calls_keep_exact_scopes(self):
        source = (r"P\left(A_1\cup\dots\cup A_k\right)="
                  r"P(A_1)+\dots+P(A_k).")
        tree = self.parser.parse(source)
        left, right = tree.children
        self.assertEqual((left.kind, left.text),
                         ("FunctionApplication", r"P\left(A_1\cup\dots\cup A_k\right)"))
        union = next(node for node in left.walk() if node.value == r"\cup")
        self.assertEqual(union.text, r"A_1\cup\dots\cup A_k")
        rule = {"pattern_type": "sequence", "separator": "+"}
        matches = match_ast(tree, rule, self.registry)
        complete = next(match for match in matches
                        if match["matched_expression"] == r"P(A_1)+\dots+P(A_k)")
        self.assertEqual(complete["arguments"],
                         [r"P(A_1)", r"\dots", r"P(A_k)"])

    def test_gather_continuation_preserves_complete_sized_call_operands(self):
        source = ("\\begin{gather*}\n"
                  r"P\left(\text{all pieces}\right)=\\" + "\n"
                  r"P\left(\text{pieces in }A_1\right)+\dots+"
                  r"P\left(\text{pieces in }A_k\right)." + "\n\\end{gather*}")
        regions = parse_math_regions(source, self.registry)
        self.assertEqual(len(regions), 1)
        self.assertFalse(regions[0].error)
        matches = match_ast(regions[0].tree,
                            {"pattern_type": "sequence", "separator": "+"}, self.registry)
        self.assertEqual(matches[-1]["arguments"],
                         [r"P\left(\text{pieces in }A_1\right)", r"\dots",
                          r"P\left(\text{pieces in }A_k\right)"])

    def test_symbolic_calls_do_not_replace_implicit_products(self):
        for source in ("2(x+1)", "(a+b)(c+d)", "2x"):
            with self.subTest(source=source):
                tree = self.parser.parse(source)
                self.assertNotEqual(tree.kind, "FunctionApplication")
                self.assertEqual(tree.value, "juxtaposition")

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
        tree = self.parser.parse(r"a\neqUnknown b")
        unknown = next(node for node in tree.walk() if node.kind == "SymbolAtom")
        self.assertEqual((unknown.value, unknown.text), (r"\neqUnknown", r"\neqUnknown"))

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

    def test_source_spans_and_named_children_are_exact_and_ordered(self):
        source = r"f(x_i^2)+y"
        tree = self.parser.parse(source)
        self.assertEqual((tree.start, tree.end), (0, len(source)))
        self.assertEqual([role for role, _ in tree.named_children()],
                         ["argument_1", "argument_2"])
        for node in tree.walk():
            self.assertEqual(node.text, source[node.start:node.end])
            self.assertTrue(tree.start <= node.start <= node.end <= tree.end)
            starts = [child.start for child in node.children]
            self.assertEqual(starts, sorted(starts))


    def test_unsupported_regions_retain_source_and_do_not_block_others(self):
        source = r"$a+b$ $\unknown{x}$ $a&b$ $a*b$"
        regions = parse_math_regions(source, self.registry)
        self.assertEqual([bool(region.error) for region in regions], [False, False, True, False])
        self.assertEqual(source[regions[1].start:regions[1].end], r"\unknown{x}")
        self.assertEqual(regions[1].tree.kind, "OpaqueCommandApplication")
        with self.assertRaises(ParseError):
            self.parser.parse(r"a\\b")
        unicode_script = self.parser.parse("x\u00b2")
        self.assertEqual((unicode_script.kind, unicode_script.roles, unicode_script.text),
                         ("ScriptedExpression", ("base", "^"), "x\u00b2"))

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
            self.assertEqual(inspect_main([r"\unknown{x}"]), 0)
        self.assertIn("OpaqueCommandApplication", output.getvalue())


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

    def test_real_plus_and_cdot_use_the_same_sequence_classification_path(self):
        config = configuration()
        plus = [item for item in find_expression_candidates("+", config, self.records)
                if item.get("macro") == "realplus" and item.get("record")]
        cdot = [item for item in find_expression_candidates(r"\cdot", config, self.records)
                if item.get("macro") == "realtimes" and item.get("record")]
        self.assertTrue(plus)
        self.assertTrue(cdot)
        for candidates, separator in ((plus, "+"), (cdot, r"\cdot")):
            self.assertTrue(all(item["classification"]["pattern_type"] == "sequence"
                                for item in candidates))
            self.assertTrue(all(item["classification"]["separator"] == separator
                                for item in candidates))
            self.assertTrue(all(item["source"] == "SMGloM" for item in candidates))

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
            decision = review_workflow.build_review_decision(match, candidate, rule, papers)
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
            decisions = [dict(file="paper.tex", start=1, end=end,
                              source_hash=hashlib.sha256(expression.encode("utf-8")).hexdigest(), matched_expression=expression,
                              semantic_suggestion=r"\changed{x}")
                         for end, expression in ((4, "a-b"), (6, "a-b-c"))]
            with patch.object(apply_annotations, "OUTPUT_DIR", root):
                self.assertEqual(apply_annotations.apply_annotations(decisions), 0)
                decisions[0]["source_hash"] = hashlib.sha256(b"a-z").hexdigest()
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
