"""Display regressions: syntax labels, original slices and CLI compatibility."""

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from expression_parser import ExpressionParser, NotationRegistry, ParseError
from inspect_expression import format_readable, format_tree, main


class TestExpressionDisplay(unittest.TestCase):
    def setUp(self):
        config = json.loads((ROOT / "expression-config.example.json").read_text(encoding="utf-8-sig"))
        self.parser = ExpressionParser(registry=NotationRegistry.from_config(config))

    def display(self, source, view="summary"):
        return format_readable(self.parser.parse(source), view)

    def test_simple_summary_and_exact_source(self):
        output = self.display("  a + b  ")
        self.assertIn('Expression: "  a + b  "', output)
        self.assertIn("Top-level structure: Addition", output)
        self.assertIn('Symbol: "a"', output)
        self.assertIn('Symbol: "b"', output)
        self.assertIn("Unknown/fallback constructs: 0", output)
        self.assertIn("no semantic annotation selected", output)

    def test_nested_tree_preserves_structure_and_source(self):
        source = "a+(b-c)^2"
        tree = self.parser.parse(source)
        before = format_tree(tree)
        output = format_readable(tree, "tree")
        for label in ("Addition", "Subtraction", "Group", "Exponentiation"):
            self.assertIn(label, output)
        for node in tree.walk():
            self.assertIn(json.dumps(node.text, ensure_ascii=False), output)
        self.assertNotIn("BinaryOperator", output)
        self.assertEqual(format_tree(tree), before)

    def test_integral_fraction_and_roles(self):
        output = self.display(r"\int_0^1 \frac{x}{2}\,dx")
        for label in ("Top-level structure: Integral", "body: Fraction", "lower:", "upper:", "variable:"):
            self.assertIn(label, output)
        self.assertIn(json.dumps(r"\frac{x}{2}"), output)

    def test_unknown_constructs_are_not_interpreted(self):
        for source, label in ((r"\nabla", "Unknown Symbol"),
                              (r"\partial", "Unknown Symbol"),
                              (r"\mystery{x}", "Unknown Command (fallback)")):
            with self.subTest(source=source):
                output = self.display(source)
                self.assertIn(label, output)
                self.assertIn("Unknown/fallback constructs: 1", output)
                for invented in ("Gradient", "Divergence", "Partial Derivative", "Function Application"):
                    self.assertNotIn(invented, output)

    def test_function_application_requires_ast_support(self):
        source = r"\sin(x)"
        self.assertEqual(self.parser.parse(source).kind, "FunctionApplication")
        self.assertIn("Function Application", self.display(source))
        parser = ExpressionParser(syntax={"implicit_operator": {
            "token": "juxtaposition", "precedence": 20, "associativity": "left"}})
        self.assertNotIn("Function Application", format_readable(parser.parse("f(x)"), "tree"))

    def test_long_summary_is_bounded_but_keeps_original(self):
        source = "+".join(str(i) for i in range(80))
        output = self.display(source)
        self.assertIn(json.dumps(source), output)
        self.assertIn("more components; use --view tree", output)
        self.assertLess(len(output.splitlines()), 20)

    def test_summary_skips_implicit_intermediates_without_changing_complete_views(self):
        source = " ".join(["u"] * 20) + r" \frac{v}{w}+\int_0^1 z\,dz"
        tree = self.parser.parse(source)
        detailed_before = format_tree(tree)
        tree_before = format_readable(tree, "tree")
        output = format_readable(tree, "summary")
        self.assertIn("Fraction:", output)
        self.assertIn("Integral:", output)
        self.assertIn("intermediate implicit-operation nodes omitted", output)
        self.assertNotIn("Operator juxtaposition:", output)
        self.assertNotIn("more components;", output)
        self.assertEqual(format_tree(tree), detailed_before)
        self.assertEqual(format_readable(tree, "tree"), tree_before)
        self.assertIn("Operator juxtaposition:", tree_before)
        self.assertEqual(len(tree_before.splitlines()), len(list(tree.walk())) + 2)
        for node in tree.walk():
            self.assertEqual(node.text, source[node.start:node.end])

    def test_summary_keeps_explicit_operators_even_with_same_configured_token(self):
        parser = ExpressionParser(registry=NotationRegistry.from_config({
            "operators": [{"token": "@", "precedence": 20, "associativity": "left"}],
            "syntax": {"implicit_operator": {
                "token": "@", "precedence": 20, "associativity": "left"}}}))
        output = format_readable(parser.parse("a@b c d"), "summary")
        self.assertIn('Operator @: "a@b"', output)
        self.assertIn("1 intermediate implicit-operation nodes omitted", output)

    def test_unconfigured_symbols_are_preserved_not_parse_failures(self):
        for source in (r"\Psi", r"\lambda", r"\tau", r"\unlistedSymbol"):
            with self.subTest(source=source):
                tree = self.parser.parse(source)
                self.assertEqual(tree.kind, "SymbolAtom")
                output = format_readable(tree, "summary")
                self.assertIn("preserved syntax, not parse failures", output)
                self.assertIn(json.dumps(source), output)
                self.assertNotIn("UnsupportedExpression", output)
        with self.assertRaises(ParseError):
            self.parser.parse(r"\unlistedSymbol{")

    def test_detailed_default_remains_byte_identical(self):
        expected = "BinaryOperator(+) [0:3] notation=+ source=user configuration\n  Atom(a) [0:1]\n  Atom(b) [2:3]\n"
        for args in (["a+b"], ["--view", "detailed", "a+b"]):
            with patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(main(args), 0)
            self.assertEqual(output.getvalue(), expected)

    def test_file_views_keep_region_sources_and_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.tex"
            path.write_text("Before $ a+b $ after $x+$", encoding="utf-8")
            for view in ("summary", "tree", "detailed"):
                with self.subTest(view=view), patch("sys.stdout", new_callable=io.StringIO) as output:
                    self.assertEqual(main(["--file", str(path), "--view", view]), 1)
                    self.assertIn("Math region [", output.getvalue())
                    self.assertIn("UnsupportedExpression:", output.getvalue())
                    if view != "detailed":
                        self.assertIn('Expression: " a+b "', output.getvalue())


if __name__ == "__main__":
    unittest.main()
