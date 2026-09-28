import json
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from expression_document import parse_math_regions, find_structural_occurrences
from expression_metadata import build_expression_config
from expression_parser import ExpressionParser
from expression_registry import NotationRegistry


class TestUnicodeMath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = json.loads((ROOT / "expression-config.example.json").read_text(encoding="utf-8"))
        cls.config = build_expression_config(raw, [])
        cls.registry = NotationRegistry.from_config(cls.config)
        cls.parser = ExpressionParser(registry=cls.registry)

    def test_greek_letters_are_generic_atoms(self):
        for source in "ωαβθλπΩΓΣ":
            with self.subTest(source=source):
                tree = self.parser.parse(source)
                self.assertEqual((tree.kind, tree.text), ("Atom", source))

    def test_unicode_scripts_preserve_raw_source(self):
        for source, roles in (("ωᵢ", ("base", "_")), ("x²", ("base", "^")),
                              ("x₁", ("base", "_"))):
            tree = self.parser.parse(source)
            self.assertEqual((tree.kind, tree.roles, tree.text),
                             ("ScriptedExpression", roles, source))

    def test_unicode_relations_operators_and_root(self):
        for source, value in (("ωᵢ∈A", "∈"), ("x≤y", "≤"), ("A⊆B", "⊆"),
                              ("x≠0", "≠"), ("A∪B", "∪"), ("p∧q", "∧"),
                              ("x→∞", "→"), ("α+β", "+")):
            tree = self.parser.parse(source)
            self.assertEqual((tree.value, tree.text), (value, source))
        root = self.parser.parse("√(x²+y²)")
        self.assertEqual((root.kind, root.value, root.text), ("UnaryOperator", "√", "√(x²+y²)"))

    def test_sigma_distinction_and_unicode_sum(self):
        self.assertEqual(self.parser.parse("Σ").kind, "Atom")
        source = "∑_{ωᵢ∈A} P(ωᵢ)"
        tree = self.parser.parse(source)
        self.assertEqual((tree.kind, tree.value, tree.text),
                         ("BigOperatorApplication", "∑", source))
        self.assertEqual(tree.roles, ("lower", "body"))

    def test_professor_example_is_an_honest_fragment_sequence(self):
        source = "P(ωi) ω∈A"
        tree = self.parser.parse(source)
        self.assertEqual((tree.kind, tree.text), ("MathSequence", source))
        self.assertEqual([child.text for child in tree.children], ["P(ωi)", "ω∈A"])

    def test_mixed_unicode_and_tex(self):
        sources = (r"\sum_{ω_i\in A} P(ω_i)",
                   r"∑_{\omega_i∈A} P(\omega_i)",
                   r"\frac{α+β}{x²+y²}")
        for source in sources:
            self.assertEqual(self.parser.parse(source).text, source)

    def test_unknown_unicode_math_symbol_is_preserved(self):
        tree = self.parser.parse("∞")
        self.assertEqual((tree.kind, tree.text), ("OpaqueMathToken", "∞"))
        self.assertFalse(tree.metadata["semantic_mapping_available"])

    def test_unicode_review_scopes_preserve_exact_spans(self):
        path = ROOT / "tests/fixtures/review_unicode_math.tex"
        regions = parse_math_regions(path.read_text(encoding="utf-8"), self.registry, path)
        matches = find_structural_occurrences(regions, "ωᵢ∈A", self.config)
        self.assertTrue(matches)
        self.assertEqual(matches[0]["matched_expression"], "ωᵢ∈A")
        self.assertEqual(matches[0]["text"][matches[0]["column"] - 1:
                                             matches[0]["column"] - 1 + 4], "ωᵢ∈A")

    def test_deterministic_generated_unicode_expressions_do_not_crash(self):
        random.seed(20260928)
        atoms = list("xyzABCαβγω")
        operators = ["+", "−", "×", "∪", "∩", "∧", "∨"]
        relations = ["=", "<", ">", "≤", "≥", "≠", "∈", "⊆"]
        sources = []
        for _ in range(120):
            left, right = random.choice(atoms), random.choice(atoms)
            source = left + random.choice(operators + relations) + right
            if random.randrange(3) == 0:
                source = "(" + source + ")"
            sources.append(source)
        for source in sources:
            tree = self.parser.parse(source)
            self.assertEqual(tree.text, source)
            for node in tree.walk():
                self.assertLessEqual(0, node.start)
                self.assertLessEqual(node.start, node.end)
                self.assertLessEqual(node.end, len(source))
                self.assertEqual(node.text, source[node.start:node.end])


if __name__ == "__main__":
    unittest.main()
