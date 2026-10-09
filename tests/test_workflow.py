import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_DIR / "src"

sys.path.insert(0, str(SRC_DIR))


import review_workflow





from apply_annotations import (
    build_module_declaration,
    insert_required_modules,
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
            "column": 5, "start": 100, "end": 110,
            "decision": "accepted"
        }

        second_decision = {
            "visible_symbol": r"\frac",
            "semantic_macro": "adivide",
            "file": "paper.tex",
            "line": 20,
            "column": 8, "start": 200, "end": 210,
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
            "column": 8, "start": 200, "end": 210,
            "decision": "rejected"
        }

        new_decision = {
            "visible_symbol": r"\frac",
            "semantic_macro": "adivide",
            "file": "paper.tex",
            "line": 20,
            "column": 8, "start": 200, "end": 210,
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
