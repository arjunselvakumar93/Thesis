import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evaluate_workflow import GOLD_FILE, _output_sanity, evaluate


class TestWorkflowEvaluation(unittest.TestCase):
    def test_manual_gold_set_is_real_and_not_parser_generated(self):
        gold = json.loads(GOLD_FILE.read_text(encoding="utf-8"))
        self.assertEqual(len(gold), 20)
        self.assertTrue(all(item.get("expected_root") and "semantic_status" in item for item in gold))
        self.assertTrue(any(item["semantic_status"] == "ambiguous" for item in gold))
        self.assertTrue(all((ROOT / "papers" / item["file"]).exists() for item in gold))

    def test_real_evaluation_keeps_layers_separate(self):
        summary, occurrences, gold = evaluate()
        self.assertEqual(summary["math_region_extraction"]["total_extracted"], 70)
        self.assertEqual(summary["structural_parser"]["parsed"], 70)
        self.assertEqual(summary["gold"]["size"], 20)
        self.assertEqual(len(occurrences), 70)
        self.assertEqual(len(gold), 20)
        self.assertIn("semantic_candidate_coverage", summary)
        self.assertIn("annotation_ready_coverage", summary)
        self.assertIn("application", summary)
        self.assertEqual(len(summary["examples"]["successful"]), 5)
        self.assertEqual(len(summary["examples"]["ambiguous"]), 5)

    def test_output_sanity_detects_balanced_and_broken_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / "paper.tex"
            paper.write_text("$a+b$\n\\usemodule[smglom/arithmetics]{mod?arithmetics}\n",
                             encoding="utf-8")
            self.assertTrue(_output_sanity(root)["passed"])
            paper.write_text("$a+b\n{", encoding="utf-8")
            report = _output_sanity(root)
            self.assertFalse(report["passed"])
            self.assertTrue(report["unbalanced_brace_files"])
            self.assertTrue(report["unbalanced_math_delimiter_files"])


if __name__ == "__main__":
    unittest.main()
