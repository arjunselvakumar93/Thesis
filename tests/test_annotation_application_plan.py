import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import apply_annotations
from apply_annotations import build_application_plan, render_application_plan


def decision(source, original, replacement, macro, start=None, module="synthetic", occurrence=0):
    if start is None:
        cursor = -1
        for _ in range(occurrence + 1):
            cursor = source.index(original, cursor + 1)
        start = cursor
    return {"file": "paper.tex", "line": 1, "column": start + 1,
            "start": start, "end": start + len(original),
            "source_hash": hashlib.sha256(original.encode("utf-8")).hexdigest(), "matched_expression": original,
            "semantic_macro": macro, "semantic_suggestion": replacement,
            "semantic_module": module, "semantic_repository": "test",
            "decision": "accepted"}


def render(source, decisions):
    plan = build_application_plan(source, decisions, "paper.tex")
    result, _, failures, count = render_application_plan(source, plan)
    return result, plan, failures, count


class TestApplicationPlan(unittest.TestCase):
    def test_two_disjoint_replacements(self):
        source = "a+b and c-d"
        decisions = [decision(source, "a+b", r"\fooPlus{a}{b}", "fooPlus"),
                     decision(source, "c-d", r"\fooMinus{c}{d}", "fooMinus")]
        result, plan, failures, count = render(source, decisions)
        self.assertEqual(result, r"\fooPlus{a}{b} and \fooMinus{c}{d}")
        self.assertEqual(plan["independent_replacements"], 2)
        self.assertFalse(failures)
        self.assertEqual(count, 2)

    def test_nested_fraction_and_subtraction(self):
        source = r"\frac{a}{b} - c"
        decisions = [decision(source, r"\frac{a}{b}", r"\fooDivide{a}{b}", "fooDivide"),
                     decision(source, source, r"\fooMinus{\frac{a}{b}}{c}", "fooMinus", 0)]
        result, plan, failures, count = render(source, decisions)
        self.assertEqual(result, r"\fooMinus{\fooDivide{a}{b}}{c}")
        self.assertEqual(plan["nested_relationships"], 1)
        self.assertFalse(failures)
        self.assertEqual(count, 2)

    def test_three_level_fraction_subtraction_equality(self):
        source = r"x = \frac{a}{b} - c"
        fraction = decision(source, r"\frac{a}{b}", r"\fooDivide{a}{b}", "fooDivide")
        subtraction = decision(source, r"\frac{a}{b} - c",
                               r"\fooMinus{\frac{a}{b}}{c}", "fooMinus")
        equality = decision(source, source, r"\fooEq{x}{\frac{a}{b} - c}", "fooEq", 0)
        result, plan, failures, count = render(source, [fraction, subtraction, equality])
        self.assertEqual(result, r"\fooEq{x}{\fooMinus{\fooDivide{a}{b}}{c}}")
        self.assertEqual(plan["nested_relationships"], 2)
        self.assertFalse(failures)
        self.assertEqual(count, 3)

    def test_same_span_different_semantics_is_conflict(self):
        source = "ab"
        left = decision(source, source, r"\natTimes{a}{b}", "natTimes", 0)
        right = decision(source, source, r"\realTimes{a}{b}", "realTimes", 0)
        result, plan, _, count = render(source, [left, right])
        self.assertEqual(result, source)
        self.assertEqual(len(plan["same_span_conflicts"]), 1)
        self.assertEqual(plan["nodes_with_competing_semantic_decisions"], 1)
        self.assertEqual(count, 0)

    def test_partial_overlap_is_conflict(self):
        source = "abcdef"
        left = decision(source, "abcd", r"\left{abcd}", "left", 0)
        right = decision(source, "cdef", r"\right{cdef}", "right", 2)
        result, plan, _, count = render(source, [left, right])
        self.assertEqual(result, source)
        self.assertEqual(len(plan["partial_overlap_conflicts"]), 1)
        self.assertEqual(count, 0)

    def test_stale_decision_is_not_planned(self):
        stale = decision("a+b", "a+b", r"\fooPlus{a}{b}", "fooPlus", 0)
        result, plan, _, count = render("a-b", [stale])
        self.assertEqual(result, "a-b")
        self.assertEqual(len(plan["stale"]), 1)
        self.assertEqual(count, 0)


    def test_modules_are_deduplicated_and_second_run_is_idempotent(self):
        source = "\\documentclass{article}\n$a+b$\n"
        item = decision(source, "a+b", r"\fooPlus{a}{b}", "fooPlus")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            paper = output / "paper.tex"
            paper.write_text(source, encoding="utf-8")
            with patch.object(apply_annotations, "OUTPUT_DIR", output):
                first = apply_annotations.apply_annotations([item], return_report=True)
                first_text = paper.read_text(encoding="utf-8")
                second = apply_annotations.apply_annotations([item], return_report=True)
                second_text = paper.read_text(encoding="utf-8")
            self.assertEqual(first_text, second_text)
            self.assertEqual(first_text.count(r"\usemodule[smglom/test]{mod?synthetic}"), 1)
            self.assertEqual(first["successfully_rendered_decisions"], 1)
            self.assertEqual(second["successfully_rendered_decisions"], 0)

    def test_original_source_tree_is_unchanged(self):
        source = "$a+b$"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            papers = root / "papers"
            output = root / "output"
            papers.mkdir()
            (papers / "paper.tex").write_text(source, encoding="utf-8")
            output.mkdir()
            (output / "paper.tex").write_text(source, encoding="utf-8")
            item = decision(source, "a+b", r"\fooPlus{a}{b}", "fooPlus")
            with patch.object(apply_annotations, "OUTPUT_DIR", output):
                apply_annotations.apply_annotations([item])
            self.assertEqual((papers / "paper.tex").read_text(encoding="utf-8"), source)
            self.assertIn(r"\fooPlus{a}{b}", (output / "paper.tex").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
