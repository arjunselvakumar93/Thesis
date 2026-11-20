from contextlib import redirect_stdout
import io
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import apply_annotations
import review_workflow
from apply_annotations import validate_decisions
from expression_document import parse_papers
from notation_catalog import build_expression_config
from notation_catalog import build_catalog, search_catalog


class TestIntegralReviewWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = json.loads((ROOT / "integral-config.example.json").read_text(encoding="utf-8"))
        cls.config = build_expression_config(raw)
        cls.paper = ROOT / "tests" / "fixtures" / "integral_review_cases.tex"

    def test_combined_catalog_search_preserves_configured_provenance(self):
        catalogue = build_catalog([], self.config)
        for query in (r"\int", "fooIntegral", "integral"):
            with self.subTest(query=query):
                matches = search_catalog(catalogue, query)
                integral = next(item for item in matches if item.get("macro") == "fooIntegral")
                self.assertEqual(integral["source"], "user configuration")
                self.assertTrue(integral["classification"]["annotation_ready"])

    def test_review_finds_accepts_saves_and_validates_integral_decision(self):
        candidates = review_workflow.find_expression_candidates("integral", self.config, [])
        self.assertEqual(len(candidates), 1)
        regions = parse_papers(self.paper, self.config)
        captured = []
        with redirect_stdout(io.StringIO()), patch.object(review_workflow, "choose_candidate", return_value=candidates[0]), \
             patch.object(review_workflow, "ask_for_decision", side_effect=["accepted", "quit"]), \
             patch.object(review_workflow, "save_decisions",
                          side_effect=lambda decisions: captured.extend(decisions) or len(decisions)):
            reviewed = review_workflow.run_generic_review(
                "integral", candidates, self.paper, self.config, regions)
        self.assertEqual(reviewed, 1)
        self.assertEqual(captured[0]["semantic_macro"], "fooIntegral")
        self.assertEqual(captured[0]["arguments"], ["0", "1", "f(x)", "x"])
        self.assertEqual(captured[0]["argument_roles"], ["lower", "upper", "body", "variable"])
        self.assertEqual(captured[0]["configuration_source"], "user configuration")
        self.assertIn("source_hash", captured[0])
        report = validate_decisions(captured, [], self.paper)
        self.assertEqual(len(report["valid"]), 1)
        self.assertFalse(report["stale"])
        self.assertFalse(report["unverifiable"])

    def test_nested_application_output_is_safe_and_idempotent(self):
        source = "\\documentclass{article}\n$\\int_0^1 \\frac{x}{2}\\,dx$\n"
        expression = r"\int_0^1 \frac{x}{2}\,dx"
        parent_start = source.index(expression)
        child_text = r"\frac{x}{2}"
        child_start = source.index(child_text)
        common = {"file": "paper.tex", "line": 2, "decision": "accepted"}
        child = dict(common, column=child_start + 1, start=child_start,
                     end=child_start + len(child_text), source_hash=hashlib.sha256(child_text.encode("utf-8")).hexdigest(),
                     matched_expression=child_text, semantic_macro="fooDivide",
                     semantic_suggestion=r"\fooDivide{x}{2}", semantic_module="foo-arithmetic",
                     semantic_repository="user")
        parent = dict(common, column=parent_start + 1, start=parent_start,
                      end=parent_start + len(expression), source_hash=hashlib.sha256(expression.encode("utf-8")).hexdigest(),
                      matched_expression=expression, semantic_macro="fooIntegral",
                      semantic_suggestion=r"\fooIntegral{0}{1}{\frac{x}{2}}{x}",
                      semantic_module="synthetic-integrals", semantic_repository="user")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            papers, output = root / "papers", root / "output"
            papers.mkdir(); output.mkdir()
            (papers / "paper.tex").write_text(source, encoding="utf-8")
            (output / "paper.tex").write_text(source, encoding="utf-8")
            with patch.object(apply_annotations, "OUTPUT_DIR", output):
                report = apply_annotations.apply_annotations([child, parent], return_report=True)
                first = (output / "paper.tex").read_bytes()
                apply_annotations.apply_annotations([child, parent], return_report=True)
                second = (output / "paper.tex").read_bytes()
            annotated = first.decode("utf-8")
            self.assertIn(r"\fooIntegral{0}{1}{\fooDivide{x}{2}}{x}", annotated)
            self.assertEqual(report["successfully_rendered_decisions"], 2)
            self.assertEqual(first, second)
            self.assertEqual((papers / "paper.tex").read_text(encoding="utf-8"), source)


if __name__ == "__main__":
    unittest.main()
