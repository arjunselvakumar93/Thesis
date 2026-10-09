import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import apply_annotations as decision_validator
import validate_decisions


class TestDecisionValidator(unittest.TestCase):
    def test_requested_command_entry_point_uses_safe_validator(self):
        self.assertIs(validate_decisions.main, decision_validator.validate_main)

    def test_current_and_stale_decisions_are_partitioned_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            papers = Path(directory)
            paper = papers / "paper.tex"
            decisions = [
                {
                    "visible_symbol": "-", "semantic_macro": "aminus",
                    "semantic_module": "arithmetics", "file": "paper.tex",
                    "line": 1, "column": 2, "matched_expression": "a-b",
                    "start": 1, "end": 4, "source_hash": hashlib.sha256(b"a-b").hexdigest(),
                },
                {
                    "visible_symbol": "-", "semantic_macro": "aminus",
                    "semantic_module": "arithmetics", "file": "paper.tex",
                    "line": 2, "column": 1, "start": 6, "end": 21,
                    "matched_expression": "guess-and-check",
                },
            ]
            candidate = {
                "macro": "aminus", "module": "arithmetics",
                "repository": "arithmetics", "classification": {"pattern_type": "sequence"},
            }
            current_match = {
                "file": paper, "line": 1, "column": 2,
                "matched_expression": "a-b", "source_expression": "a-b",
                "source_start": 1, "source_end": 4,
            }
            with patch.object(
                decision_validator, "find_generic_candidates", return_value=[candidate]
            ), patch.object(
                decision_validator, "find_ast_occurrences", return_value=[current_match]
            ):
                report = decision_validator.validate_decisions(
                    decisions, records=[], papers_dir=papers
                )
            self.assertEqual([index for index, _ in report["valid"]], [0])
            self.assertEqual([index for index, _ in report["stale"]], [1])
            self.assertEqual(report["unverifiable"], [])

    def test_removal_requires_exact_confirmation_and_creates_backup(self):
        decisions = [{"id": 1}, {"id": 2}, {"id": 3}]
        with tempfile.TemporaryDirectory() as directory:
            decision_file = Path(directory) / "review_results.json"
            original = json.dumps(decisions)
            decision_file.write_text(original, encoding="utf-8")

            changed = decision_validator.remove_stale_decisions(
                decisions, [1], decision_file, "yes"
            )
            self.assertFalse(changed)
            self.assertEqual(decision_file.read_text(encoding="utf-8"), original)
            self.assertFalse(Path(str(decision_file) + ".bak").exists())

            changed = decision_validator.remove_stale_decisions(
                decisions, [1], decision_file, "REMOVE"
            )
            self.assertTrue(changed)
            self.assertEqual(
                json.loads(decision_file.read_text(encoding="utf-8")),
                [{"id": 1}, {"id": 3}],
            )
            self.assertEqual(
                json.loads(Path(str(decision_file) + ".bak").read_text(encoding="utf-8")),
                decisions,
            )


if __name__ == "__main__":
    unittest.main()
