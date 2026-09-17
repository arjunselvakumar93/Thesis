import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_DIR / "src"

sys.path.insert(0, str(SRC_DIR))


import review_workflow

from symbol_scanner import (
    remove_latex_comment,
    extract_braced_expression,
    extract_command_with_arguments,
)

from review_workflow import (
    generate_semantic_suggestion,
)

from apply_annotations import (
    build_module_declaration,
    insert_required_modules,
)

from smglom_lookup import (
    find_symbol_definitions,
)


class TestSymbolScanner(unittest.TestCase):

    def test_remove_comment(self):

        line = r"x = \sqrt{4} % comment"

        result = remove_latex_comment(
            line
        )

        self.assertEqual(
            result,
            r"x = \sqrt{4} "
        )

    def test_simple_sqrt_expression(self):

        line = r"x = \sqrt{3}"

        position = line.find(
            r"\sqrt"
        )

        result = extract_braced_expression(
            line,
            position,
            r"\sqrt"
        )

        self.assertEqual(
            result,
            r"\sqrt{3}"
        )

    def test_nested_sqrt_expression(self):

        line = (
            r"\sqrt{\frac{B^2}{4} - C}"
        )

        position = line.find(
            r"\sqrt"
        )

        result = extract_braced_expression(
            line,
            position,
            r"\sqrt"
        )

        self.assertEqual(
            result,
            r"\sqrt{\frac{B^2}{4} - C}"
        )

    def test_two_sqrt_expressions(self):

        line = (
            r"\sqrt{3} + \sqrt{2}"
        )

        first_position = line.find(
            r"\sqrt"
        )

        second_position = line.find(
            r"\sqrt",
            first_position + 1
        )

        first = extract_braced_expression(
            line,
            first_position,
            r"\sqrt"
        )

        second = extract_braced_expression(
            line,
            second_position,
            r"\sqrt"
        )

        self.assertEqual(
            first,
            r"\sqrt{3}"
        )

        self.assertEqual(
            second,
            r"\sqrt{2}"
        )

    def test_simple_fraction_expression(self):

        line = r"\frac{B}{2}"

        position = line.find(
            r"\frac"
        )

        (
            expression,
            arguments
        ) = extract_command_with_arguments(
            line,
            position,
            r"\frac",
            2
        )

        self.assertEqual(
            expression,
            r"\frac{B}{2}"
        )

        self.assertEqual(
            arguments,
            [
                "B",
                "2"
            ]
        )

    def test_nested_fraction_expression(self):

        line = (
            r"\frac{1}{\sqrt{3} - \sqrt{2}}"
        )

        position = line.find(
            r"\frac"
        )

        (
            expression,
            arguments
        ) = extract_command_with_arguments(
            line,
            position,
            r"\frac",
            2
        )

        self.assertEqual(
            expression,
            r"\frac{1}{\sqrt{3} - \sqrt{2}}"
        )

        self.assertEqual(
            arguments,
            [
                "1",
                r"\sqrt{3} - \sqrt{2}"
            ]
        )


class TestSemanticSuggestion(unittest.TestCase):

    def test_asqrt_suggestion(self):

        result = generate_semantic_suggestion(
            r"\sqrt",
            r"\sqrt{3}",
            "asqrt"
        )

        self.assertEqual(
            result,
            r"\asqrt{3}"
        )

    def test_realsqrt_suggestion(self):

        result = generate_semantic_suggestion(
            r"\sqrt",
            r"\sqrt{x}",
            "realsqrt"
        )

        self.assertEqual(
            result,
            r"\realsqrt{x}"
        )

    def test_adivide_suggestion(self):

        result = generate_semantic_suggestion(
            r"\frac",
            r"\frac{B}{2}",
            "adivide"
        )

        self.assertEqual(
            result,
            r"\adivide{B}{2}"
        )


class TestModuleHandling(unittest.TestCase):

    def test_build_module_declaration(self):

        result = build_module_declaration(
            "arithmetics"
        )

        self.assertEqual(
            result,
            r"\usemodule[smglom/arithmetics]{mod?arithmetics}"
        )

    def test_build_module_declaration_uses_record_repository(self):
        result = build_module_declaration("equal", "mv")
        self.assertEqual(result, r"\usemodule[smglom/mv]{mod?equal}")

    def test_insert_module_only_once(self):

        original = (
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            "\\end{document}\n"
        )

        result = insert_required_modules(
            original,
            [
                "arithmetics",
                "arithmetics"
            ]
        )

        declaration = (
            r"\usemodule[smglom/arithmetics]{mod?arithmetics}"
        )

        self.assertEqual(
            result.count(
                declaration
            ),
            1
        )


class TestSMGloMLookup(unittest.TestCase):

    def test_find_one_argument_sqrt_candidates(self):

        results = find_symbol_definitions(
            r"\sqrt",
            required_args=1
        )

        macros = {
            result["macro"]
            for result in results
        }

        self.assertIn(
            "asqrt",
            macros
        )

        self.assertIn(
            "realsqrt",
            macros
        )

    def test_two_argument_root_is_filtered_out(self):

        results = find_symbol_definitions(
            r"\sqrt",
            required_args=1
        )

        macros = {
            result["macro"]
            for result in results
        }

        self.assertNotIn(
            "aroot",
            macros
        )

        self.assertNotIn(
            "realroot",
            macros
        )

    def test_find_two_argument_fraction_candidates(self):

        results = find_symbol_definitions(
            r"\frac",
            required_args=2
        )

        macros = {
            result["macro"]
            for result in results
        }

        self.assertIn(
            "adivide",
            macros
        )

        self.assertIn(
            "fraction",
            macros
        )

        self.assertIn(
            "realdivide",
            macros
        )


class TestDecisionPersistence(unittest.TestCase):

    def setUp(self):

        self.original_decisions_dir = (
            review_workflow.DECISIONS_DIR
        )

        self.original_decision_file = (
            review_workflow.DECISION_FILE
        )

        self.temp_directory = (
            tempfile.TemporaryDirectory()
        )

        temporary_path = Path(
            self.temp_directory.name
        )

        review_workflow.DECISIONS_DIR = (
            temporary_path
        )

        review_workflow.DECISION_FILE = (
            temporary_path
            / "review_results.json"
        )

    def tearDown(self):

        review_workflow.DECISIONS_DIR = (
            self.original_decisions_dir
        )

        review_workflow.DECISION_FILE = (
            self.original_decision_file
        )

        self.temp_directory.cleanup()

    def test_previous_decisions_are_preserved(self):

        first_decision = {
            "visible_symbol": r"\sqrt",
            "semantic_macro": "asqrt",
            "file": "paper.tex",
            "line": 10,
            "column": 5,
            "decision": "accepted"
        }

        second_decision = {
            "visible_symbol": r"\frac",
            "semantic_macro": "adivide",
            "file": "paper.tex",
            "line": 20,
            "column": 8,
            "decision": "accepted"
        }

        review_workflow.save_decisions(
            [
                first_decision
            ]
        )

        total = (
            review_workflow.save_decisions(
                [
                    second_decision
                ]
            )
        )

        stored = json.loads(
            review_workflow
            .DECISION_FILE
            .read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            total,
            2
        )

        self.assertEqual(
            len(stored),
            2
        )

        symbols = {
            decision[
                "visible_symbol"
            ]
            for decision in stored
        }

        self.assertEqual(
            symbols,
            {
                r"\sqrt",
                r"\frac"
            }
        )

    def test_same_occurrence_is_updated(self):

        old_decision = {
            "visible_symbol": r"\frac",
            "semantic_macro": "adivide",
            "file": "paper.tex",
            "line": 20,
            "column": 8,
            "decision": "rejected"
        }

        new_decision = {
            "visible_symbol": r"\frac",
            "semantic_macro": "adivide",
            "file": "paper.tex",
            "line": 20,
            "column": 8,
            "decision": "accepted"
        }

        review_workflow.save_decisions(
            [
                old_decision
            ]
        )

        total = (
            review_workflow.save_decisions(
                [
                    new_decision
                ]
            )
        )

        stored = json.loads(
            review_workflow
            .DECISION_FILE
            .read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            total,
            1
        )

        self.assertEqual(
            len(stored),
            1
        )

        self.assertEqual(
            stored[0]["decision"],
            "accepted"
        )


if __name__ == "__main__":
    unittest.main()
