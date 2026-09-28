import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from expression_document import parse_math_regions
from expression_metadata import build_expression_config
from expression_registry import NotationRegistry


class TestProfessorDemo(unittest.TestCase):
    def test_every_demo_region_parses_and_preserves_source(self):
        raw = json.loads((ROOT / "expression-config.example.json").read_text(encoding="utf-8"))
        registry = NotationRegistry.from_config(build_expression_config(raw, []))
        path = ROOT / "tests/fixtures/professor_demo.tex"
        source = path.read_text(encoding="utf-8")
        regions = parse_math_regions(source, registry, path)
        self.assertEqual(len(regions), 9)
        self.assertTrue(all(not region.error for region in regions))
        self.assertTrue(all(region.tree.text == source[region.start + region.tree.start:
                                                       region.start + region.tree.end]
                            for region in regions))

    def test_integral_demo_distinguishes_latex_and_unicode_source(self):
        raw = json.loads((ROOT / "expression-config.example.json").read_text(encoding="utf-8"))
        registry = NotationRegistry.from_config(build_expression_config(raw, []))
        path = ROOT / "tests/fixtures/professor_integral_demo.tex"
        source = path.read_text(encoding="utf-8")
        regions = parse_math_regions(source, registry, path)
        self.assertEqual([region.tree.kind for region in regions],
                         ["BoundExpression", "BoundExpression"])
        self.assertEqual([region.tree.text for region in regions],
                         [r"\int_0^1 f(x)\,dx", "∫₀¹ f(x) dx"])


if __name__ == "__main__":
    unittest.main()
